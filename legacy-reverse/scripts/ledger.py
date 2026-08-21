#!/usr/bin/env python3
"""legacy-reverse 台帳スクリプト。

対象プロジェクトのルートで実行する（--root で変更可）。
機械可読メタデータ（functions.json / ledger.json / 各mdフロントマター）から
WBS・骨子・完了検証を生成し、ハッシュ連鎖とブロック状態を管理する。

サブコマンド:
  wbs                        docs/index.qmd を再生成
  skeletons [--force]        functions.json から docs/specs/ の骨子を生成
                             （項目立ては docs/templates/spec.md が正。無ければ同梱シード）
  migrate-specs [--dry-run]  既存の仕様書の「例外・数値特異点」節を機械が維持する。
                             節の追加（旧世代の救済）・⓪の再抽出で増えた hazard 行の追加・
                             空欄の適用EPの転記。**本文と記入済みの欄は触らない**
  init-templates             人が書くファイル一式の雛形を配置（規約・業務知識・例外ポリシー・
                             仕様書テンプレ・工程別プロンプト。既存は上書きしない）
  authored [--json]          人が書くファイルの記入状況を一覧
  hash <path>                sha256 先頭8桁を表示
  verify <func-id>           ハッシュ連鎖（①→②、③）を検証
  status [<func-id>]         フェーズ状況を表示（機械可読 JSON も可: --json）
  next [--flow <name|id>] [--no-dict-gate]
                             次に着手すべき関数を提案（トポロジカル順。--flow でフロー到達集合に限定。
                             既定では変数辞書に未承認の語義が残る関数の①を除外＝dict-gate）
  audit [--flow <name>] [--json]
                             WBS の関数数と①バッチの対象件数が合わない原因を内訳で出す
                             （骨子なし・dict-gate・blocked・draft 待ちの件数と func_id）
  next-issue                 次の ISSUE 番号を表示
  flow add <name> --entry F-xxxx[,F-yyyy...] [--desc ...]
                             フロー（作業スコープ）を追加。flow rm <name|id> / flow list
  add <name> [--file ...]    関数を後追い追加（人の指示。manual フラグ付きで採番）
  exclude <func-id>... [--dead] [--reason ...] / include <func-id>
                             移植対象から外す／復帰させる（物理削除はしない。複数指定可。
                             --dead でエントリから到達不能な関数を一括除外）
  freeze-tests <func-id>     テストコードのハッシュを ledger.json に記録（③完了時）
  block <func-id> <issue-id> / unblock <func-id>
  phase-start <n> <func-id> / phase-end
  check                      ⑥完了検証 → docs/completion-check.md（不備ありなら exit 1）
"""
import argparse
import datetime
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

import graph  # noqa: E402 — scripts/graph.py（同ディレクトリ）。フロー到達集合の計算に使う

# ---------- 基盤 ----------

def sha8(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def parse_frontmatter(text: str) -> dict:
    """YAMLサブセット（スカラーと1段ネストの辞書のみ）を読む。外部依存なし。"""
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == "---")
        end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return {}
    fm: dict = {}
    parent = None
    for raw in lines[start + 1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = re.match(r"^(\s*)([\w-]+):\s*(.*)$", raw)
        if not m:
            continue
        indent, key, val = len(m.group(1)), m.group(2), m.group(3)
        val = re.sub(r"\s+#.*$", "", val).strip().strip('"').strip("'")
        if val.lower() == "null":
            val = None
        if indent == 0:
            if val == "" or val is None and raw.rstrip().endswith(":"):
                fm[key] = {}
                parent = key
            else:
                fm[key] = val
                parent = None
        elif parent is not None:
            fm[parent][key] = val
    return fm


def load_json(path: Path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as e:
            # AI の編集ミス（末尾カンマ等）で正データが壊れると全スクリプトがここを通る。
            # 生の traceback ではなく、場所と直し方が分かる1メッセージで止める
            sys.exit(f"error: {path} が JSON として読めない（{e.lineno}行{e.colno}列: {e.msg}）。\n"
                     "  AIの編集で壊れた可能性が高い（よくある原因: 末尾の余分なカンマ）。\n"
                     "  `git diff` で直前の変更を確認して修復するか、`git checkout -- <file>` で戻すこと")
    return default if default is not None else {}


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class Project:
    def __init__(self, root: Path):
        self.root = root
        self.docs = root / "docs"
        self.data = root / "data"
        self.state_dir = root / ".legacy-reverse"
        self.functions = load_json(self.data / "functions.json", {})
        self.ledger_path = self.data / "ledger.json"
        self.ledger = load_json(self.ledger_path, {})
        self._vars_cache = None      # (辞書あり?, variables 配列) の遅延ロード
        self._vars_by_func = None    # func_id -> [variable, ...]（重複除去済み）

    def funcs(self) -> list:
        """移植対象の関数のみ（excluded を除く）。①〜⑥・WBS・next は常にこれを見る。"""
        return [f for f in self.all_funcs() if not f.get("excluded")]

    def all_funcs(self) -> list:
        return self.functions.get("functions", [])

    def func(self, func_id: str) -> dict:
        for f in self.all_funcs():
            if f["func_id"] == func_id:
                return f
        sys.exit(f"error: {func_id} は functions.json に存在しない")

    def fm(self, relpath: str) -> dict:
        p = self.root / relpath
        return parse_frontmatter(p.read_text(encoding="utf-8-sig")) if p.exists() else {}

    def latest_result(self, func_id: str):
        files = sorted((self.docs / "test-results").glob(f"{func_id}_*.md"))
        return files[-1] if files else None

    # ---------- 変数辞書との連動（設計 references/graph-dict-design.md P2） ----------
    #
    # 辞書エンジンそのもの（クラスタリング・検証・伝搬）は variables.py の所有物。
    # ここは台帳側が必要とする2つの読み取り機能だけを持つ:
    #   dict-gate  … 未承認の語義が残る関数の①着手を止める（既定 ON）
    #   dict-hash  … ①生成時の語義集合のハッシュを骨子に刻み、後の改訂を検知する
    # **data/variables.json が無いプロジェクトでは両方とも完全に無効**（後方互換）。

    def _load_variables(self) -> tuple:
        if self._vars_cache is None:
            path = self.data / "variables.json"
            store = load_json(path, None) if path.exists() else None
            self._vars_cache = (store is not None, (store or {}).get("variables") or [])
        return self._vars_cache

    def has_dict(self) -> bool:
        """変数辞書（data/variables.json）を持つプロジェクトか。"""
        return self._load_variables()[0]

    def vars_of(self, func_id: str) -> list:
        """その関数に出現する変数（variables.json のエントリ）。出現順・重複なし。"""
        if self._vars_by_func is None:
            idx: dict = {}
            for v in self._load_variables()[1]:
                for o in v.get("occurrences") or []:
                    seen = idx.setdefault(o.get("func_id"), {})
                    seen.setdefault(v.get("var_id"), v)
            self._vars_by_func = {fid: list(m.values()) for fid, m in idx.items()}
        return self._vars_by_func.get(func_id, [])

    def dict_gate_blockers(self, func_id: str, spec_status: str = None) -> list:
        """dict-gate: その関数の①着手を止めている「未承認の変数」の var_id 一覧。

        空リスト＝ゲートに掛からない。設計 P2「伝搬とゲート」の判定はここが唯一の実装で、
        `ledger next` と pipeline._decide_kind（連続実行の対象選定）が共有する。

        免除:
          - 変数辞書が無いプロジェクト（従来どおりの挙動）
          - spec が既に draft / reviewed の関数（仕様化済みを今更止めても意味がない）
        spec_status を渡すと frontmatter の再読み込みを省略できる（status_of の結果を流用）。
        """
        if not self.has_dict():
            return []
        if spec_status is None:
            spec_p = self.docs / "specs" / f"{func_id}.md"
            spec_status = (self.fm(f"docs/specs/{func_id}.md").get("status")
                           if spec_p.exists() else "-")
        if spec_status in ("draft", "reviewed"):
            return []
        return [v["var_id"] for v in self.vars_of(func_id) if v.get("status") != "approved"]

    def dict_hash(self, func_id: str):
        """その関数の approved 変数の (var_id, desc) 集合の正規化 sha256 先頭8桁。

        辞書が無いプロジェクトでは None（骨子にも記録しない＝従来どおりの出力）。
        approved が0件なら ""（「辞書は見たが確定語義は無かった」と
        「そもそも辞書連鎖が無い（旧骨子）」を区別するため、空文字で記録する）。
        """
        if not self.has_dict():
            return None
        pairs = sorted([v["var_id"], v.get("desc") or ""]
                       for v in self.vars_of(func_id) if v.get("status") == "approved")
        if not pairs:
            return ""
        payload = json.dumps(pairs, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]

    # ---------- 状態判定（schema.md の表が正） ----------
    def status_of(self, f: dict) -> dict:
        fid = f["func_id"]
        led = self.ledger.get(fid, {})
        s: dict = {"func_id": fid, "blocked_by": led.get("blocked_by")}

        spec_p = self.docs / "specs" / f"{fid}.md"
        spec_fm = self.fm(f"docs/specs/{fid}.md")
        s["spec"] = spec_fm.get("status", "-") if spec_p.exists() else "-"
        s["spec_ok"] = s["spec"] == "reviewed"
        # dict-hash 連鎖: ①生成時に刻んだ語義集合のハッシュと現在値のずれ＝辞書の改訂。
        # フロントマターに dict-hash が無い旧骨子・辞書なしプロジェクトは常に False
        # （後方互換。WBS の出力もバイト単位で従来と一致する）
        # 判定対象は「①が実際に書かれた仕様書」（draft / reviewed）だけ。骨子のままの
        # ものは次の `ledger skeletons` が dict-hash を現在値へ同期するので警告しない
        s["dict_stale"] = False
        if spec_p.exists() and s["spec"] in ("draft", "reviewed") and "dict-hash" in spec_fm:
            cur = self.dict_hash(fid)
            if cur is not None:
                rec = spec_fm.get("dict-hash")
                # parse_frontmatter は空値 `dict-hash: ""` を「ネストの親」として {} で返す
                rec = "" if isinstance(rec, dict) or rec is None else str(rec)
                s["dict_stale"] = rec != cur

        ts_p = self.docs / "test-specs" / f"{fid}.md"
        ts_fm = self.fm(f"docs/test-specs/{fid}.md")
        s["test_spec"] = ts_fm.get("status", "-") if ts_p.exists() else "-"
        s["test_spec_stale"] = bool(
            ts_p.exists() and spec_p.exists() and ts_fm.get("spec-hash") != sha8(spec_p)
        )
        s["test_spec_ok"] = s["test_spec"] == "approved" and not s["test_spec_stale"]

        tf = f.get("test_file")
        frozen = led.get("test_code_hash")
        tf_p = self.root / tf if tf else None
        s["test_code_ok"] = bool(
            tf_p and tf_p.exists() and frozen and sha8(tf_p) == frozen
        )
        s["test_code_tampered"] = bool(
            tf_p and tf_p.exists() and frozen and sha8(tf_p) != frozen
        )

        impl_p = self.root / f["new"]["module"]
        s["impl_ok"] = impl_p.exists()

        latest = self.latest_result(fid)
        if latest:
            rfm = parse_frontmatter(latest.read_text(encoding="utf-8-sig"))
            s["test"] = rfm.get("result", "-")
            s["attempt"] = rfm.get("attempt", "?")
            s["result_file"] = str(latest.relative_to(self.docs)).replace("\\", "/")
        else:
            s["test"], s["attempt"], s["result_file"] = "-", 0, None
        s["test_ok"] = s["test"] == "pass"
        return s

    # ---------- 着手順（コールグラフのトポロジカルソート、葉から） ----------
    def topo_order(self) -> tuple:
        ids = [f["func_id"] for f in self.funcs()]
        calls = {f["func_id"]: [c for c in f.get("calls", []) if c in ids] for f in self.funcs()}
        order, seen, in_stack, cycles = [], set(), set(), []

        def visit(n, path):
            if n in seen:
                return
            if n in in_stack:
                cycles.append(path[path.index(n):] + [n])
                return
            in_stack.add(n)
            for c in calls[n]:
                visit(c, path + [n])
            in_stack.discard(n)
            seen.add(n)
            order.append(n)

        for n in ids:
            visit(n, [])
        return order, cycles


def resolve_flow_fids(p: Project, key: str) -> set:
    """--flow 引数（フロー名 or flow_id）からフロー到達集合を計算する。

    ledger.py（next / wbs / skeletons）と pipeline.py が共用する（P3節: 「人が
    このフローだけ作業と指定できる」対象選定の共通実装）。到達集合は毎回
    graph.py がその場で計算する導出値のため、再抽出後の functions.json にも自動追随する。
    """
    g = graph.graph_from_data(p.functions)
    fl = graph.get_flow(p.functions, key)
    entries = fl.get("entries", [])
    if not entries:
        sys.exit(f"error: フロー '{key}' に entries が無い")
    fmap = {f["func_id"]: f for f in p.all_funcs()}
    missing = [fid for fid in entries if fid not in fmap]
    if missing:
        sys.exit(f"error: フロー '{key}' の entries に functions.json 未定義の func_id: "
                 + ", ".join(missing))
    return graph.reachable(g, entries)


# ---------- サブコマンド ----------

def call_graph(order: list, fmap: dict, stats: dict) -> list:
    """コールグラフを Mermaid で描く（呼ぶ側 → 呼ばれる側）。

    GitHub 流の ```mermaid で書く。HTML サイトは render_site.py が影コピーで
    ```{mermaid} に変換し、PDF は qtpdf.py が同じ変換をする。
    """
    if not order:
        return []
    if len(order) > 60:
        return ["# コールグラフ", "",
                f"関数 {len(order)} 件のため図は省略（依存は下表の「依存」列を参照）。", ""]

    def nid(fid):
        return fid.replace("-", "")

    def cls(s):
        if s["blocked_by"]:
            return "blocked"
        if s["test_ok"]:
            return "done"
        return "wip" if s["spec_ok"] else "todo"

    body = ["::: {.column-screen-inset}", "```mermaid", "graph LR"]
    for fid in order:
        f, s = fmap[fid], stats[fid]
        label = f["new"].get("name", fid)
        body.append(f'  {nid(fid)}["{label}"]:::{cls(s)}')
        if s["spec"] != "-":                    # 仕様書（骨子含む）がある時だけリンク
            body.append(f'  click {nid(fid)} "specs/{fid}.html"')
    for fid in order:
        for callee in fmap[fid].get("calls", []):
            if callee in fmap:
                body.append(f"  {nid(fid)} --> {nid(callee)}")
    body += [
        "  classDef done fill:#d4edda,stroke:#28a745,color:#155724",
        "  classDef wip fill:#fff3cd,stroke:#ffc107,color:#856404",
        "  classDef todo fill:#f1f3f5,stroke:#adb5bd,color:#495057",
        "  classDef blocked fill:#f8d7da,stroke:#dc3545,color:#721c24",
        "```",
        ":::",
    ]
    return ["# コールグラフ", "",
            "<!-- 緑=⑤pass / 黄=着手中 / 灰=未着手 / 赤=⛔blocked。ノードをクリックで仕様書へ -->",
            ""] + body + [""]


WBS_SPLIT = 200   # 関数一覧を1ページに載せる上限。超えるとレガシーファイル別のサブページへ分割


def _mark(ok, link=None, note=""):
    sym = "✅" if ok else ("▲" if note else "☐")
    body = f"{sym}{(' ' + note) if note else ''}"
    return f"[{body}]({link})" if link and (ok or note) else body


FUNC_TABLE_HEAD = [
    "| # | 関数 | 依存 | ①spec | ②test-spec | ③test-code | ④impl | ⑤test |",
    "|:-:|------|------|:---:|:---:|:---:|:---:|:---:|"]


def _spec_ref(label: str, s: dict, pre: str = "") -> str:
    """仕様書（骨子含む）が存在する場合のみリンクにする（⓪途中のリンク切れ防止）。"""
    if s["spec"] != "-":
        return f"[{label}]({pre}specs/{s['func_id']}.md)"
    return label


def _func_row(i: int, f: dict, s: dict, pre: str = "") -> str:
    fid = s["func_id"]
    name = f["new"].get("name", fid)
    deps = ", ".join(f.get("calls", [])) or "なし"
    # draft / generated（＝承認待ち）は render_site.py が仕様書ページに埋め込む
    # 案内パネル（機械レビュー結果と返答方法）へ直接ジャンプさせる（#review-<fid>）
    spec_href = f"{pre}specs/{fid}.md" + ("#review-" + fid if s["spec"] == "draft" else "")
    ts_href = (f"{pre}test-specs/{fid}.md"
              + ("#review-" + fid if s["test_spec"] == "generated" else ""))
    c1 = _mark(s["spec_ok"], spec_href,
               "辞書⚠" if s["dict_stale"]
               else ("" if s["spec_ok"] or s["spec"] == "-" else s["spec"]))
    c2 = _mark(s["test_spec_ok"], ts_href,
               "stale⚠" if s["test_spec_stale"]
               else ("" if s["test_spec_ok"] or s["test_spec"] == "-" else s["test_spec"]))
    c3 = "⚠改変" if s["test_code_tampered"] else ("✅" if s["test_code_ok"] else "☐")
    c4 = "✅" if s["impl_ok"] else "☐"
    if s["blocked_by"]:
        c5 = f"[⛔ {s['blocked_by']}]({pre}issues/{s['blocked_by']}.md)"
    elif s["test"] == "pass":
        c5 = f"[✅]({pre}{s['result_file']})"
    elif s["test"] == "fail":
        c5 = f"[❌ {s['attempt']}回目]({pre}{s['result_file']})"
    else:
        c5 = "☐"
    return f"| {i} | {_spec_ref(name, s, pre)} | {deps} | {c1} | {c2} | {c3} | {c4} | {c5} |"


def _next_phase(s: dict):
    for key, label in (("spec_ok", "①spec"), ("test_spec_ok", "②test-spec"),
                       ("test_code_ok", "③test-code"), ("impl_ok", "④impl"),
                       ("test_ok", "⑤test")):
        if not s[key]:
            return label
    return None


# ナビバー（_quarto.yml）が参照するページのうち、後続フェーズが生成する/人が書くもの。
# ⓪直後に無いままだと quarto render がリンク切れエラーで落ちるため、
# wbs 再生成のたびに「無ければスタブを置く」。⑥⑦や人の記入で自然に上書きされる。
NAV_STUBS = {
    "domain-knowledge.md": ("ドメイン知識",
                            "人の裁定・業務知識がここに蓄積されます（ISSUE の回答が転記されます）。"),
    "conventions.md": ("プロジェクト規約",
                       "⓪（/legacy-0-analyze）で人と確定します"
                       "（テンプレ: assets/templates/conventions.md）。"),
    "completion-check.md": ("⑥ 完了検証",
                            "全関数の①〜⑤完了後に /legacy-6-check を実行すると生成されます。"),
    "analysis.md": ("⑦ 分析", "/legacy-7-analyze で生成されます。"),
}


def ensure_nav_stubs(p: Project) -> int:
    p.docs.mkdir(parents=True, exist_ok=True)
    made = 0
    for name, (title, note) in NAV_STUBS.items():
        f = p.docs / name
        if not f.exists():
            f.write_text(f'---\ntitle: "{title}"\n---\n\n'
                         "::: {.callout-note}\n"
                         f"このページはまだ作成されていません。{note}\n"
                         ":::\n", encoding="utf-8")
            made += 1
    return made


def cmd_wbs(p: Project, args) -> None:
    made = ensure_nav_stubs(p)
    if made:
        print(f"nav stubs: {made} 件作成（未生成ページのリンク切れ防止）")
    order, cycles = p.topo_order()
    fmap = {f["func_id"]: f for f in p.funcs()}
    stats = {fid: p.status_of(fmap[fid]) for fid in order}
    n = len(order)

    def count(key):
        return sum(1 for s in stats.values() if s[key])

    issues = []
    for ip in sorted((p.docs / "issues").glob("ISSUE-*.md")):
        ifm = parse_frontmatter(ip.read_text(encoding="utf-8-sig"))
        if ifm.get("status") == "open":
            issues.append((ip.name[:-3], ifm))

    lines = [
        "---",
        f'title: "{p.functions.get("project", {}).get("name", "project")} リバースエンジニアリング WBS"',
        "date: last-modified",
        "page-layout: full",   # 関数一覧は列が多い。既定の800px幅だと関数名が何行にも折れる
        "---",
        "",
        "<!-- ledger.py wbs による自動生成。手編集禁止 -->",
        "",
        "# 進捗サマリ",
        "",
        "| 関数数 | ①spec | ②test-spec | ③test-code | ④impl | ⑤test |",
        "|:---:|:---:|:---:|:---:|:---:|:---:|",
        f"| {n} | {count('spec_ok')}/{n} | {count('test_spec_ok')}/{n} | {count('test_code_ok')}/{n}"
        f" | {count('impl_ok')}/{n} | {count('test_ok')}/{n} |",
        "",
    ]

    # ---- フロー別進捗（flows 未定義なら何も出さない＝従来出力と完全一致） ----
    flows = p.functions.get("flows") or []
    if flows:
        fg = graph.graph_from_data(p.functions)
        lines += ["# フロー別進捗", "",
                  "| フロー | エントリ | 到達関数数 | ①spec | ②test-spec | ③test-code | ④impl | ⑤test |",
                  "|------|------|:---:|:---:|:---:|:---:|:---:|:---:|"]
        for fl in flows:
            entries = fl.get("entries", [])
            reached = graph.reachable(fg, entries) if entries else set()
            in_scope = [fid for fid in reached if fid in stats]   # excluded・対象外は数えない
            m = len(in_scope)

            def fcount(key):
                return sum(1 for fid in in_scope if stats[fid][key])

            entries_str = ", ".join(entries) or "(未設定)"
            lines.append(
                f"| {fl.get('name', '')} | {entries_str} | {m} "
                f"| {fcount('spec_ok')}/{m} | {fcount('test_spec_ok')}/{m} "
                f"| {fcount('test_code_ok')}/{m} | {fcount('impl_ok')}/{m} "
                f"| {fcount('test_ok')}/{m} |")
        lines.append("")

    if cycles:
        lines += ["::: {.callout-warning}", "コールグラフに循環があります: "
                  + " / ".join("→".join(c) for c in cycles), ":::", ""]

    # ---- 要対応（人が最初に見るべきもの。大規模でもここだけ見れば良い） ----
    blocked = [(fid, stats[fid]["blocked_by"]) for fid in order if stats[fid]["blocked_by"]]
    stale = [fid for fid in order if stats[fid]["test_spec_stale"]]
    dict_stale = [fid for fid in order if stats[fid]["dict_stale"]]
    tampered = [fid for fid in order if stats[fid]["test_code_tampered"]]
    failing = [fid for fid in order
               if stats[fid]["test"] == "fail" and not stats[fid]["blocked_by"]]
    drafts = [fid for fid in order if stats[fid]["spec"] == "draft"]
    # ②も①と同じく「まとめて承認」の対象（review_checks.py report が表を作る）
    generated = [fid for fid in order
                 if stats[fid]["test_spec"] == "generated" and not stats[fid]["test_spec_stale"]]
    if blocked or stale or dict_stale or tampered or failing or drafts or generated:
        lines += ["# 要対応", "", "| 種別 | 関数 | 詳細 |", "|------|------|------|"]
        if drafts:
            ref = ("[一斉レビュー表](spec-review.md)"
                   if (p.docs / "spec-review.md").exists() else "関数一覧の ▲draft を参照")
            lines.append(f"| ▲ ①レビュー待ち | {len(drafts)} 件 | {ref} |")
        if generated:
            ref = ("[一斉レビュー表](testspec-review.md)"
                   if (p.docs / "testspec-review.md").exists()
                   else "関数一覧の ▲generated を参照")
            lines.append(f"| ▲ ②レビュー待ち | {len(generated)} 件 | {ref} |")
        for fid, iss in blocked:
            lines.append(f"| ⛔ 裁定待ち | {_spec_ref(fid, stats[fid])} | [{iss}](issues/{iss}.md) |")
        for fid in stale:
            lines.append(f"| ⚠ ②stale | {_spec_ref(fid, stats[fid])} | ①改訂済み → ②要再確認 |")
        for fid in dict_stale:
            lines.append(f"| ⚠ 辞書stale | {_spec_ref(fid, stats[fid])} "
                         f"| 辞書の語義が①生成後に改訂 → ①要再確認 |")
        for fid in tampered:
            lines.append(f"| ⚠ ③改変 | {_spec_ref(fid, stats[fid])} | freeze後にテストが変更されている |")
        for fid in failing:
            s = stats[fid]
            lines.append(f"| ❌ ⑤fail | {_spec_ref(fid, s)} "
                         f"| [{s['attempt']}回目]({s['result_file']}) |")
        lines.append("")

    lines += call_graph(order, fmap, stats)

    lines += ["# Open ISSUES（要人間判断）", ""]
    if issues:
        lines += ["| ID | 種別 | 関数 | 内容 |", "|----|------|------|------|"]
        for name, ifm in issues:
            lines.append(
                f"| [{name}](issues/{name}.md) | {ifm.get('kind','')} "
                f"| {ifm.get('func-id','')} | {ifm.get('title','')} |")
    else:
        lines.append("なし 🎉")

    # ---- 次の一手（トポロジカル順で着手可能なもの） ----
    todo = [(fid, _next_phase(stats[fid])) for fid in order
            if not stats[fid]["blocked_by"] and _next_phase(stats[fid])]
    lines += ["", "# 次の一手（推奨着手順 上位10）", ""]
    if todo:
        lines += ["| # | 関数 | 次フェーズ |", "|:-:|------|------|"]
        for i, (fid, ph) in enumerate(todo[:10], 1):
            name = fmap[fid]["new"].get("name", fid)
            lines.append(f"| {i} | {_spec_ref(name, stats[fid])} | {ph} |")
        if len(todo) > 10:
            lines.append(f": 残り {len(todo) - 10} 件は関数一覧を参照")
    else:
        lines.append("全関数完了。⑥ check へ 🎉")

    # ---- 関数一覧（小規模: 1ページ / 大規模: レガシーファイル別サブページ） ----
    # column-screen-inset = 画面幅いっぱいに広げる Quarto の仕組み（本文の800px枠から出す）。
    # .wbs-funcs は wbs.css で列幅を制御する（関数名は折り返さず、依存列に幅を譲らせる）
    wbs_dir = p.docs / "wbs"
    if n <= WBS_SPLIT:
        if wbs_dir.exists():
            shutil.rmtree(wbs_dir)
        lines += ["", "# 関数一覧（推奨着手順）", "",
                  "::: {.wbs-funcs .column-screen-inset}"] + FUNC_TABLE_HEAD
        for i, fid in enumerate(order, 1):
            lines.append(_func_row(i, fmap[fid], stats[fid]))
        lines.append(":::")
    else:
        # ファイル別に分割（トポロジカル順の通し番号は保つ）
        topo_idx = {fid: i for i, fid in enumerate(order, 1)}
        groups: dict = {}
        for fid in order:
            # ledger add した関数は legacy.file が空のことがある → 専用グループへ
            groups.setdefault(fmap[fid]["legacy"].get("file") or "（file未設定・手動追加）",
                              []).append(fid)
        if wbs_dir.exists():
            shutil.rmtree(wbs_dir)
        wbs_dir.mkdir(parents=True)
        slugs: dict = {}
        lines += ["", f"# 関数一覧（{n} 件・レガシーファイル別）", "",
                  "| レガシーファイル | 関数数 | ①spec | ②test-spec | ③test-code | ④impl | ⑤test | 要対応 |",
                  "|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|"]
        for lf, fids in groups.items():
            slug = re.sub(r"\W+", "-", lf).strip("-").lower() or "no-file"
            while slug in slugs.values():
                slug += "-2"
            slugs[lf] = slug
            g = [stats[fid] for fid in fids]
            m = len(fids)

            def gcount(key):
                return sum(1 for s in g if s[key])

            warn = sum(1 for s in g if s["blocked_by"] or s["test_spec_stale"]
                       or s["dict_stale"] or s["test_code_tampered"] or s["test"] == "fail")
            lines.append(
                f"| [{lf}](wbs/{slug}.qmd) | {m} | {gcount('spec_ok')}/{m} "
                f"| {gcount('test_spec_ok')}/{m} | {gcount('test_code_ok')}/{m} "
                f"| {gcount('impl_ok')}/{m} | {gcount('test_ok')}/{m} "
                f"| {'⚠ ' + str(warn) if warn else '—'} |")
            page = ["---", f'title: "WBS: {lf}"', "date: last-modified",
                    "page-layout: full", "---", "",
                    "<!-- ledger.py wbs による自動生成。手編集禁止 -->", "",
                    "[← WBS トップ](../index.qmd)", "",
                    f"# {lf}（{m} 関数・#はプロジェクト全体の推奨着手順）", "",
                    "::: {.wbs-funcs .column-screen-inset}"] + FUNC_TABLE_HEAD
            for fid in fids:
                page.append(_func_row(topo_idx[fid], fmap[fid], stats[fid], pre="../"))
            page.append(":::")
            (wbs_dir / f"{slug}.qmd").write_text("\n".join(page) + "\n", encoding="utf-8")

    # ---- 対象外（人が exclude した関数。黙って消さず、理由ごと見えるようにする） ----
    excluded = [f for f in p.all_funcs() if f.get("excluded")]
    if excluded:
        lines += ["", f"# 対象外の関数（{len(excluded)} 件・移植しない）", "",
                  "<!-- ledger exclude で対象外化したもの。ledger include F-xxxx で復帰 -->", "",
                  "| func-id | レガシー | 理由 |", "|------|------|------|"]
        for f in excluded:
            leg = f"{f['legacy'].get('file', '')}: {f['legacy'].get('name', '')}"
            lines.append(f"| {f['func_id']} | {leg} | {f.get('excluded_reason', '')} |")

    imps = sorted((p.docs / "improvements").glob("*.md"))
    if imps:
        lines += ["", "# ⑦ 改善イタレーション", "",
                  "<!-- 1施策=1票=1イタレーション。達成: ✅=基準達成 / ❌=未達 / —=検証前 -->", "",
                  "| ID | 分類 | 目的 | 対象 | 状態 | 達成 |",
                  "|----|------|------|------|:---:|:---:|"]
        for ip in imps:
            ifm = parse_frontmatter(ip.read_text(encoding="utf-8-sig"))
            ach = str(ifm.get("achieved", "")).lower()
            mark = "✅" if ach == "true" else ("❌" if ach == "false" else "—")
            lines.append(f"| [{ip.stem}](improvements/{ip.name}) | {ifm.get('kind', '')} "
                         f"| {ifm.get('title', '')} | {ifm.get('func-id', '')} "
                         f"| {ifm.get('status', '')} | {mark} |")

    out = p.docs / "index.qmd"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}")


# ---------- 仕様書テンプレート（固変分離） ----------
#
# **項目立て（節構成）と書き方ガイドはプロジェクトが所有する**（人が著者）:
#   docs/templates/spec.md      … ①仕様書の骨子テンプレ（cmd_skeletons が使う）
#   docs/templates/test-spec.md … ②テスト仕様書の書式（skill と review_checks が使う）
# 無いプロジェクトは skill 同梱のシード（assets/templates/）にフォールバックする。
# `ledger init-templates` がシードを docs/templates/ へコピーする（人がそこを編集する）。
#
# ワークフロー側の**固定契約**（機械が生成・検証するアンカー）だけは変えられない:
#   - 置換マーカー: LR:IO-TABLES（IO表）・LR:CALLS-TABLE（呼出表）・LR:HAZARD-TABLE（hazard表）
#   - 契約見出し: 機能詳細（SPEC-ID＋Confidence＋根拠）・副作用・例外／例外・数値特異点・未確定事項
# それ以外の節は追加・改名・削除が自由（review_checks の必須節はテンプレの見出しから導出される）。

ASSETS_TEMPLATES = Path(__file__).resolve().parent.parent / "assets" / "templates"
TEMPLATE_NAMES = {"spec": "spec.md", "testspec": "test-spec.md"}

# ---------- 工程別のプロンプト調整（固変分離の「可変」その2） ----------
#
# docs/prompts/<phase>.md … ①〜④の skill が起動のたびに読む、プロジェクト個別の
# 上乗せ指示（人が著者）。項目立て（docs/templates/）や規約（conventions.md）に
# 収まらない「書き方の癖・重点・繰り返したくない指摘」を置く場所。
#
# 同梱シードへのフォールバックは**しない**——シードは案内コメントだけの雛形なので、
# プロジェクトに無いときは「個別指示なし」が正しい（雛形の文言を指示として
# 読ませない）。`ledger init-templates` がシードを docs/prompts/ へ配置する。
PROMPT_PHASES = ("1-spec", "2-testspec", "3-testcode", "4-impl")

# ---------- 人が書くファイル一式 ----------
#
# 「人だけが書く」ファイルは、⓪の最初に `ledger init-templates` で
# **空欄＋記入ガイド付きの雛形**として置く。人が白紙から書き始めずに済ませるのが目的で、
# 中身を AI が書くわけではない（作成者区分は workflow.md が正）。既存は上書きしない。
AUTHORED_DOCS = ("conventions.md", "domain-knowledge.md", "exception-policy.md")
SPEC_TEMPLATE_MARKERS = ("<!-- LR:IO-TABLES -->", "<!-- LR:CALLS-TABLE -->",
                         "<!-- LR:HAZARD-TABLE -->")
SPEC_CONTRACT_HEADS = ("# 機能詳細", "# 副作用・例外", "## 例外・数値特異点", "# 未確定事項")
TESTSPEC_CONTRACT_HEADS = ("# トレーサビリティマトリクス",)


def template_path(root, kind: str) -> Path:
    """プロジェクト所有のテンプレ（docs/templates/）。無ければ同梱シード。"""
    name = TEMPLATE_NAMES[kind]
    proj = Path(root) / "docs" / "templates" / name
    return proj if proj.exists() else ASSETS_TEMPLATES / name


def template_body(tpath: Path) -> str:
    """テンプレ本文（フロントマターを除く）。フロントマターは機械が生成する契約部分
    なので、テンプレ側のものは人向けの参考表示としてだけ残し、骨子には写さない。"""
    text = tpath.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == "---")
        end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "---")
        return "\n".join(lines[end + 1:]).lstrip("\n")
    except StopIteration:
        return text


def load_hazard_map(root) -> dict:
    """data/hazard-map.json（hazards.py match の突合結果）。無ければ空。"""
    path = Path(root) / "data" / "hazard-map.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return {}


def hazard_table_lines(f: dict, hz_map: dict = None) -> list:
    """LR:HAZARD-TABLE に差し込む hazard 表（⓪が検知した数値特異点の一覧）。

    骨子生成（cmd_skeletons）と、後から契約見出しを足す移行（cmd_migrate_specs）で
    同じ表を作る。**適用EP は機械が決めた突合結果（hazard-map.json）をそのまま入れる**
    ——人が exception-policy.md で決めた EP-ID の転記なので、①に書かせる意味がない
    （未決定なら空欄。決めてから `hazards.py match` を回し直せば埋まる）。
    仕様記述の欄は空のまま——「その EP をこの関数でどう適用するか」は①の仕事。
    """
    hz_map = hz_map or {}
    rows = [f"| {h['hz_id']} | {h['kind']} | {f['legacy'].get('file', '')}:"
            f"{h.get('line', '')} | {(hz_map.get(h['hz_id']) or {}).get('ep', '') or ''} | |"
            for h in f.get("hazards", [])]
    return (["| hazard | 種別 | 箇所 | 適用EP | 仕様記述 |",
             "|--------|------|------|--------|----------|"]
            + (rows or ["| 該当なし | | | | |"]))


def spec_template_problems(body: str) -> list:
    """①テンプレが固定契約を満たしているかの検証（不足の一覧を返す）。"""
    problems = [f"置換マーカーがない: {m}" for m in SPEC_TEMPLATE_MARKERS if m not in body]
    problems += [f"契約見出しがない: {h}" for h in SPEC_CONTRACT_HEADS
                 if not re.search(rf"(?m)^{re.escape(h)}\s*$", body)]
    return problems


def prompt_path(root, phase: str):
    """工程別プロンプト調整の実体（無ければ None。シードへのフォールバックはしない）。"""
    if phase not in PROMPT_PHASES:
        raise KeyError(f"未知の工程: {phase}（{'/'.join(PROMPT_PHASES)}）")
    p = Path(root) / "docs" / "prompts" / f"{phase}.md"
    return p if p.exists() else None


def prompt_is_seed(path: Path) -> bool:
    """シードのまま（案内コメントだけで、人が中身を書いていない）かどうか。

    見出し・コメント・空行だけなら「未記入」と判定する。`ledger prompts` の表示と、
    skill 側の「個別指示なし」の判定に使う（雛形の例文を指示として扱わないため）。
    """
    body = re.sub(r"(?s)<!--.*?-->", "", path.read_text(encoding="utf-8-sig"))
    return not [l for l in body.splitlines()
                if l.strip() and not l.lstrip().startswith("#")]


def authored_files(root) -> list:
    """人が書くファイルの (区分, 出力先, シード, 説明) 一覧。

    `init-templates`（配置）と `authored`（記入状況）が同じ定義を共有する
    ——一方だけが知っているファイル、という取りこぼしを作らないため。
    """
    root = Path(root)
    docs = root / "docs"
    out = [
        ("規約", docs / "conventions.md", ASSETS_TEMPLATES / "conventions.md",
         "型対応・丸め・単位・日付・命名・docstring（①〜④が読む）"),
        ("業務知識", docs / "domain-knowledge.md", ASSETS_TEMPLATES / "domain-knowledge.md",
         "語彙・略語・区分値・ISSUE 回答の蓄積（⓪①②が読む）"),
        ("例外ポリシー", docs / "exception-policy.md", ASSETS_TEMPLATES / "exception-policy.md",
         "0割等の扱いの決定 EP-xxx（hazards.py add-policy が追記）"),
    ]
    out += [("仕様書テンプレ", docs / "templates" / name, ASSETS_TEMPLATES / name,
             f"{'①' if kind == 'spec' else '②'}の項目立て（節構成＋記入ガイド）")
            for kind, name in TEMPLATE_NAMES.items()]
    out += [("工程別プロンプト", docs / "prompts" / f"{ph}.md",
             ASSETS_TEMPLATES / "prompts" / f"{ph}.md",
             f"{ph} への個別指示（重点・繰り返さない指摘・手本）")
            for ph in PROMPT_PHASES]
    return out


def authored_state(dst: Path, seed: Path, fill=None) -> str:
    """未作成 / 未記入 / 記入途中 / 記入あり のいずれかを返す。

    「雛形のまま」を機械的に見分けるための判定。シードと同一・見出しとコメントだけ、を
    未記入とし、人が書き始めたあとにプレースホルダ（{{…}}）が残っていれば記入途中とする。
    `fill` には配置時と同じプレースホルダ補完を渡す——補完済みで配置したファイルを
    「もう人が触った」と誤判定しないため。
    """
    if not dst.exists():
        return "未作成"
    text = dst.read_text(encoding="utf-8-sig")
    if seed.exists():
        seed_text = seed.read_text(encoding="utf-8-sig")
        if text in (seed_text, fill(seed_text) if fill else seed_text):
            return "未記入"
    if "{{" in text:
        return "記入途中"
    body = re.sub(r"(?s)<!--.*?-->", "", text)
    if not [l for l in body.splitlines() if l.strip() and not l.lstrip().startswith("#")]:
        return "未記入"
    return "記入あり"


def _fill_project_placeholders(text: str, p: Project) -> str:
    """分かっている値だけプレースホルダを埋める（不明なものは残して人に見せる）。"""
    proj = p.functions.get("project") or {}
    known = {"legacy_lang": proj.get("legacy_lang", ""),
             "new_lang": proj.get("new_lang", ""),
             "package": proj.get("package", ""),
             "test_framework": "pytest"}
    for key, value in known.items():
        if value:
            text = text.replace("{{" + key + "}}", str(value))
    return text


def cmd_init_templates(p: Project, args) -> None:
    """人が書くファイルの雛形を一式そろえる（既存は上書きしない）。

    ⓪の最初に実行する。人はここで作られた「空欄＋記入ガイド」を埋めるだけでよく、
    白紙から書き始めたり、置き場所を調べたりしなくて済む。
    """
    made = kept = 0
    for label, dst, seed, _desc in authored_files(p.root):
        rel = dst.relative_to(p.root).as_posix()
        if dst.exists():
            print(f"skip   : {rel}（既存。そのまま編集して使う）")
            kept += 1
            continue
        if not seed.exists():
            print(f"warn   : シードが無い {seed}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(_fill_project_placeholders(
            seed.read_text(encoding="utf-8-sig"), p), encoding="utf-8")
        print(f"created: {rel}  ← {label}")
        made += 1
    print(f"\n{made} 件を作成、{kept} 件は既存のまま。**中身を書くのは人**"
          "（AI は読むだけ・提案まで）。\n"
          "各ファイルの冒頭と各節に「何を書くか」のガイドがコメントで入っている。\n"
          "記入状況は `ledger authored` で確認できる。")


def cmd_authored(p: Project, args) -> None:
    """人が書くファイルの記入状況を一覧する。"""
    rows = []
    fill = lambda text: _fill_project_placeholders(text, p)   # noqa: E731
    for label, dst, seed, desc in authored_files(p.root):
        rows.append({"kind": label, "path": dst.relative_to(p.root).as_posix(),
                     "state": authored_state(dst, seed, fill), "desc": desc})
    if args.json:
        print(json.dumps({"authored": rows}, ensure_ascii=False, indent=1))
        return
    marks = {"未作成": "✗", "未記入": "・", "記入途中": "▲", "記入あり": "✏"}
    print("人が書くファイル（AI は読むだけ・書き込まない）:")
    for r in rows:
        print(f"  {marks.get(r['state'], '?')} {r['state']:<5} {r['path']:<34} {r['desc']}")
    todo = [r for r in rows if r["state"] == "未作成"]
    half = [r for r in rows if r["state"] == "記入途中"]
    if todo:
        print(f"\n未作成が {len(todo)} 件 → `ledger init-templates` で雛形を配置する")
    if half:
        print(f"記入途中（{{{{…}}}} が残っている）が {len(half)} 件: "
              + ", ".join(r["path"] for r in half))
    print("\n注: 「未記入」は雛形のままという意味で、異常ではない"
          "（prompts は個別指示なし、規約は既定のままの扱い）。")


def cmd_skeletons(p: Project, args) -> None:
    tdir = p.docs / "specs"
    tdir.mkdir(parents=True, exist_ok=True)
    tpath = template_path(p.root, "spec")
    tmpl = template_body(tpath)
    bad = spec_template_problems(tmpl)
    if bad:
        sys.exit(f"error: 仕様書テンプレ（{tpath}）が固定契約を満たしていない:\n  - "
                 + "\n  - ".join(bad)
                 + "\n  マーカーと契約見出しの説明は references/workflow.md「固定契約」を参照")
    made = 0
    regenerated: list = []
    hz_map = load_hazard_map(p.root)          # 適用EP は機械の突合結果をそのまま載せる

    # フロー所属（func_id -> [フロー名, ...]）。骨子の新規生成時のみフロントマターに載せる
    # （文脈付与のみ・機械判定には使わない。既存 spec ファイルは触らない）
    flows = p.functions.get("flows") or []
    flow_membership: dict = {}
    if flows:
        fg = graph.graph_from_data(p.functions)
        for fl in flows:
            entries = fl.get("entries", [])
            reached = graph.reachable(fg, entries) if entries else set()
            for fid in reached:
                flow_membership.setdefault(fid, []).append(fl.get("name", ""))

    for f in p.funcs():
        fid = f["func_id"]
        out = tdir / f"{fid}.md"
        if out.exists() and not args.force:
            # status: skeleton＝①未着手（人もAIも中身を書いていない）。機械生成ブロックは
            # 辞書の承認・改訂や⓪の再抽出で変わるので、その場で作り直す。
            # draft / reviewed（＝書かれた成果物）には一切触れない
            if parse_frontmatter(out.read_text(encoding="utf-8-sig")).get("status") \
                    != "skeleton":
                continue
            regenerated.append(fid)
        legacy_p = p.root / f["legacy"]["file"]
        lhash = sha8(legacy_p) if legacy_p.is_file() else ""   # 手動追加は file 未設定があり得る
        num = fid.replace("-", "")

        def rows(items, cols):
            if not items:
                return ["| （なし） " + "| " * (len(cols) - 1) + "|"]
            return ["| " + " | ".join(str(it.get(c, "")) for c in cols) + " | 🟢 |" for it in items]

        body = [
            "---",
            f'title: "関数仕様書: {f["new"].get("name", fid)}"',
            f'func-id: "{fid}"',
            "status: skeleton",
        ]
        # dict-hash（設計 P2「dict-hash 連鎖」）。辞書が無いプロジェクトでは行ごと出さない
        dhash = p.dict_hash(fid)
        if dhash is not None:
            body.append(f'dict-hash: "{dhash}"')
        body += [
            "reviewed-by: null",
            "reviewed-date: null",
            "legacy:",
            f'  file: "{f["legacy"]["file"]}"',
            f'  name: "{f["legacy"]["name"]}"',
            f'  lines: "{f["legacy"].get("lines", "")}"',
            f'  hash: "{lhash}"',
            "new:",
            f'  module: "{f["new"]["module"]}"',
            f'  signature: "{f["new"].get("signature", "")}"',
        ]
        fl_names = flow_membership.get(fid)
        if fl_names:
            # json.dumps で YAML フローシーケンスとしても妥当な形（引用符・エスケープ込み）にする
            body.append("flows: " + json.dumps(fl_names, ensure_ascii=False))
        body += ["---", ""]

        # --- 機械生成ブロック（テンプレの LR: マーカーを置換する） ---
        io_lines = ["## 入力", "",
                    "| # | 名前 | レガシー型 | 新型 | 説明 | Confidence |",
                    "|---|------|-----------|------|------|:---:|"]
        io_lines += [f"| {i+1} | {it.get('name','')} | {it.get('legacy_type','')} | "
                     f"{it.get('new_type','')} | {it.get('desc','')} | 🟢 |"
                     for i, it in enumerate(f.get("inputs", []))] or ["| | | | | | |"]
        io_lines += ["", "## 出力", "",
                     "| 名前 | レガシー型 | 新型 | 説明 | Confidence |",
                     "|------|-----------|------|------|:---:|"]
        io_lines += rows(f.get("outputs", []), ["name", "legacy_type", "new_type", "desc"])
        io_lines += ["", "## グローバル状態", "",
                     "| 名前 | 読み/書き | 説明 | Confidence |", "|------|:---:|------|:---:|"]
        io_lines += rows(f.get("globals", []), ["name", "access", "desc"])
        io_lines += ["", "## 参照外部ファイル", "",
                     "| ファイル | 読み/書き | 用途 | Confidence |", "|---------|:---:|------|:---:|"]
        io_lines += rows(f.get("external_files", []), ["path", "access", "desc"])

        calls_lines = ["## 呼び出しサブルーチン", "",
                       "| 名前 | func-id | 用途 |", "|------|---------|------|"]
        calls_lines += [f"| | {c} | |" for c in f.get("calls", [])] or ["| （なし） | | |"]

        haz_lines = hazard_table_lines(f, hz_map)

        # テンプレ本文（プロジェクト所有）にプレースホルダと機械ブロックを差し込む。
        # LR:TEMPLATE-NOTE コメント（テンプレ自身の説明書き）は骨子には写さない
        text = re.sub(r"(?s)<!--\s*LR:TEMPLATE-NOTE.*?-->\s*", "", tmpl)
        for key, val in (("{{func_id}}", fid), ("{{func_num}}", num),
                         ("{{func_num_lower}}", num.lower()),
                         ("{{new_name}}", f["new"].get("name", fid)),
                         ("{{legacy_file}}", f["legacy"].get("file", ""))):
            text = text.replace(key, val)
        text = text.replace("<!-- LR:IO-TABLES -->", "\n".join(io_lines))
        text = text.replace("<!-- LR:CALLS-TABLE -->", "\n".join(calls_lines))
        text = text.replace("<!-- LR:HAZARD-TABLE -->", "\n".join(haz_lines))
        new_text = "\n".join(body) + "\n" + text.rstrip("\n") + "\n"
        if fid in regenerated and out.read_text(encoding="utf-8-sig") == new_text:
            regenerated.remove(fid)                  # 中身が同じなら書かない（mtime を汚さない）
            continue
        out.write_text(new_text, encoding="utf-8")
        if fid not in regenerated:
            made += 1
    print(f"skeletons: {made} 件生成（①着手済み＝draft/reviewed はスキップ、--force で上書き）"
          + (f" / 未着手の骨子を作り直し {len(regenerated)} 件"
             "（辞書の改訂・⓪の再抽出を反映）" if regenerated else ""))


# ---------- 契約見出しの後追い（既存の仕様書の移行） ----------
#
# SPEC_CONTRACT_HEADS は skill の版が上がると増えることがある（実例:「## 例外・数値特異点」は
# hazard 機構と一緒に後から入った。それ以前の「# 副作用・例外」は改名ではなく別の節）。
# 骨子生成はテンプレの契約違反を弾くが、**既に書かれた仕様書は誰も直さない**ため、
# 新しい版の機械レビューが全関数で「節がない」を出し続ける。--force での再生成は
# 書いた本文を捨てるので使えない。本文に一切触れず、足りない節だけを挿し込む。

HAZ_HEAD = "## 例外・数値特異点"
HAZ_NOTE = ("<!-- ⓪が検知した hazard を1件ずつ書く。適用EP は docs/exception-policy.md に"
            "実在する EP-ID だけを引用する（未決定なら docs/exception-queue.md で決めてから）。"
            "この節は後から入った契約見出しで、ledger migrate-specs が枠だけ置いた -->")


def _insert_haz_section(text: str, f: dict, hz_map: dict = None) -> str:
    """「## 例外・数値特異点」節を、本文を壊さずに挿し込んだ全文を返す。

    置き場所は上から順に: `# 副作用・例外` 節の末尾 → `# 未確定事項` の直前 → 文末。
    （`##` なので `# 副作用・例外` の配下に入るのが正。テンプレの並びと同じにする）
    """
    block = "\n".join([HAZ_HEAD, "", HAZ_NOTE, ""] + hazard_table_lines(f, hz_map)) + "\n"
    lines = text.splitlines(keepends=True)

    def head_index(pat: str):
        for i, l in enumerate(lines):
            if re.match(pat, l):
                return i
        return None

    at = head_index(r"^#\s*副作用・例外\s*$")
    if at is not None:                       # その節の末尾（次の `# ` 見出しの直前）
        pos = len(lines)
        for i in range(at + 1, len(lines)):
            if re.match(r"^#\s+\S", lines[i]):
                pos = i
                break
    else:
        pos = head_index(r"^#\s*未確定事項\s*$")
        if pos is None:
            pos = len(lines)
    body = "".join(lines[:pos]).rstrip("\n")
    rest = "".join(lines[pos:])
    return body + "\n\n" + block + ("\n" + rest if rest.strip() else "")


RE_HAZ_ROW = re.compile(r"^\|\s*(H-\d+-\d+)\s*\|")
RE_HAZ_HEAD_LINE = re.compile(r"(?m)^#{1,4}\s*例外[・･]数値特異点\s*$")


def _sync_haz_table(text: str, f: dict, hz_map: dict) -> tuple:
    """既にある「例外・数値特異点」節の表を、機械が決まる範囲だけ最新化する。

    この節の中身は**全部が機械由来**——hazard は⓪の検出結果、適用EP は人が
    exception-policy.md で決めた内容を hazards.py match が突合した結果。人（①）が
    書くのは「仕様記述」欄だけで、そこは機械レビューの対象外。したがって節の維持を
    人にやらせる理由がなく、次の2つを機械が面倒を見る:

      - **足りない hazard 行を足す**（⓪の再抽出で hazard が増えると、既存の仕様書は
        「hz_id が節に無い＝検討漏れ」で NG になる。人が手で足す作業だった）
      - **空欄の適用EPを埋める**（例外ポリシーは節を足したあとに決まることが多い）

    行の削除・書かれた値の上書きはしない（人と①の記入を壊さないため）。
    戻り値: (新しい全文, EPを埋めた hz_id, 行を足した hz_id)
    """
    m = RE_HAZ_HEAD_LINE.search(text)
    if not m:
        return text, [], []
    level = len(text[m.start():m.end()].split()[0])
    rest = text[m.end():]
    nxt = re.search(r"(?m)^#{1,%d}\s+\S" % level, rest)
    sec_end = m.end() + (nxt.start() if nxt else len(rest))
    head, body, tail = text[:m.end()], text[m.end():sec_end], text[sec_end:]

    lines = body.splitlines(keepends=True)
    filled, present, last_row = [], set(), None
    for i, line in enumerate(lines):
        hm = RE_HAZ_ROW.match(line)
        if not hm:
            continue
        present.add(hm.group(1))
        last_row = i
        # "| a | b | c | d | e |" → ['', ' a ', ' b ', ' c ', ' d ', ' e ', '']
        cells = line.rstrip("\n").split("|")
        if len(cells) >= 6 and not cells[4].strip():
            ep = (hz_map.get(hm.group(1)) or {}).get("ep")
            if ep:
                cells[4] = f" {ep} "
                lines[i] = "|".join(cells) + "\n"
                filled.append(hm.group(1))

    missing = [h for h in f.get("hazards") or [] if h["hz_id"] not in present]
    if missing:
        rows = hazard_table_lines({**f, "hazards": missing}, hz_map)[2:]   # ヘッダ2行を除く
        block = "".join(r + "\n" for r in rows)
        if last_row is not None:
            lines.insert(last_row + 1, block)
        else:
            # 表が無い（「該当なし」も無い）節 → 表ごと置く
            lines.append("\n" + "\n".join(hazard_table_lines(f, hz_map)) + "\n")
        # 「該当なし」のプレースホルダ行は実データが入ったら消す
        lines = [l for l in lines if not re.match(r"^\|\s*該当なし\s*\|", l)]
    return head + "".join(lines) + tail, filled, [h["hz_id"] for h in missing]


def cmd_migrate_specs(p: Project, args) -> None:
    hz_map = load_hazard_map(p.root)
    added, already, missing, reviewed_touched, ep_filled, row_added = [], [], [], [], [], []
    undecided: set = set()
    for f in p.funcs():
        fid = f["func_id"]
        sp = p.docs / "specs" / f"{fid}.md"
        if not sp.exists():
            missing.append(fid)
            continue
        text = sp.read_text(encoding="utf-8-sig")
        if re.search(r"(?m)^#{1,4}\s*例外[・･]数値特異点\s*$", text):
            already.append(fid)
            new_text, filled, added_rows = _sync_haz_table(text, f, hz_map)
            if filled:
                ep_filled.append((fid, len(filled)))
            if added_rows:
                row_added.append((fid, len(added_rows)))
            if new_text != text and not args.dry_run:
                sp.write_text(new_text, encoding="utf-8")
            continue
        added.append(fid)
        undecided |= {h["hz_id"] for h in f.get("hazards") or []
                      if not (hz_map.get(h["hz_id"]) or {}).get("ep")}
        if parse_frontmatter(text).get("status") == "reviewed":
            reviewed_touched.append(fid)
        if not args.dry_run:
            sp.write_text(_insert_haz_section(text, f, hz_map), encoding="utf-8")

    verb = "追加できる" if args.dry_run else "追加した"
    print(f"migrate-specs: {len(added)} 件に「例外・数値特異点」節を{verb}"
          f"（既にある {len(already)} 件 / 仕様書なし {len(missing)} 件）")
    if added:
        print("  " + ", ".join(added[:20]) + (" ほか" if len(added) > 20 else ""))
    if row_added:
        n = sum(k for _, k in row_added)
        print(f"  既存の節に hazard 行 {n} 件を{'足せる' if args.dry_run else '足した'}"
              f"（{len(row_added)} 関数。⓪の再抽出で増えた分）")
    if ep_filled:
        n = sum(k for _, k in ep_filled)
        print(f"  既存の節の「適用EP」空欄 {n} 件を{'埋められる' if args.dry_run else '埋めた'}"
              f"（{len(ep_filled)} 関数。決定済みの EP-ID を突合結果から転記）")
    if not hz_map:
        print("warn: data/hazard-map.json が無いので適用EPの欄は空になる"
              "（`hazards.py match --root .` を先に回すと機械が埋める）")
    elif undecided:
        print(f"warn: 例外ポリシーが未決定の hazard が {len(undecided)} 件あり、適用EPが空になる"
              "（docs/exception-queue.md で決定 → hazards.py add-policy → hazards.py match → "
              "この migrate-specs を回し直すと空欄だけ埋まる）: "
              + ", ".join(sorted(undecided)[:10]))
    if reviewed_touched:
        print(f"warn: 承認済み（reviewed）の仕様書 {len(reviewed_touched)} 件を書き換える"
              "＝②の spec-hash が古くなる（WBS に「⚠ ②stale」が出る）: "
              + ", ".join(reviewed_touched[:10]))
    if args.dry_run:
        print("  （--dry-run。実行するにはこの指定を外す）")
        return
    if added:
        print("next: 置いたのは枠と機械が決まる欄（hazard・適用EP）だけ。"
              "「仕様記述」は①で埋める（/legacy-1-spec <func-id> か pipeline.py spec）")
    if added or ep_filled or row_added:
        print("      反映: ledger wbs → render_site.py")


def cmd_hash(p: Project, args) -> None:
    print(sha8(p.root / args.path))


def cmd_verify(p: Project, args) -> None:
    f = p.func(args.func_id)
    s = p.status_of(f)
    ok = True
    if s["test_spec_stale"]:
        ok = False
        print(f"NG: ②の spec-hash が①の現物と不一致（①が改訂済み）→ ②要再確認")
    if s["dict_stale"]:
        ok = False
        approved = [v["var_id"] for v in p.vars_of(args.func_id)
                    if v.get("status") == "approved"]
        print("NG: 辞書の語義が①生成後に改訂された → ①要再確認"
              f"（この関数の承認済み変数 {len(approved)}件。"
              "docs/variables.qmd と docs/dict-conflicts.md を確認）")
    if s["test_code_tampered"]:
        ok = False
        print(f"NG: テストコードが freeze 後に改変されている → 実行前に停止せよ")
    if s["blocked_by"]:
        ok = False
        print(f"NG: {s['blocked_by']} の裁定待ち（blocked）")
    print("verify: OK" if ok else "verify: NG")
    sys.exit(0 if ok else 1)


def cmd_status(p: Project, args) -> None:
    targets = [p.func(args.func_id)] if args.func_id else p.funcs()
    out = [p.status_of(f) for f in targets]
    if args.summary:
        n = len(out)
        summary = {"n": n}
        for key in ("spec_ok", "test_spec_ok", "test_code_ok", "impl_ok", "test_ok"):
            summary[key] = sum(1 for s in out if s[key])
        summary["blocked"] = [{"func_id": s["func_id"], "issue": s["blocked_by"]}
                              for s in out if s["blocked_by"]]
        summary["stale"] = [s["func_id"] for s in out if s["test_spec_stale"]]
        summary["tampered"] = [s["func_id"] for s in out if s["test_code_tampered"]]
        summary["failing"] = [s["func_id"] for s in out
                              if s["test"] == "fail" and not s["blocked_by"]]
        summary["excluded"] = sum(1 for f in p.all_funcs() if f.get("excluded"))
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        return
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        for s in out:
            flags = "".join([
                "①" if s["spec_ok"] else "-", "②" if s["test_spec_ok"] else "-",
                "③" if s["test_code_ok"] else "-", "④" if s["impl_ok"] else "-",
                "⑤" if s["test_ok"] else "-"])
            extra = f" ⛔{s['blocked_by']}" if s["blocked_by"] else ""
            print(f"{s['func_id']}: {flags}{extra}")


PHASE_LABEL = {"1": "①spec", "2": "②test-spec", "3": "③test-code",
               "4": "④impl", "5": "⑤test"}


def actionable(p: Project, phase: str = None, skip_wait: bool = False, flow_fids=None,
               dict_gate: bool = True, gated: list = None) -> list:
    """着手可能な (func_id, 次フェーズ) をトポロジカル順で返す。

    skip_wait=True は人のレビュー/承認待ち（①draft・②generated）を除外し
    「AIの作業が残っているもの」だけにする——バッチ全件モードの再開時、
    draft を二重に書き直さないための機械的な区別。pipeline.py も共用する。
    flow_fids（set|None）を渡すと、その集合に無い func_id を対象から除く
    （`ledger next --flow` / `pipeline.py --flow` のフロー絞り込み。resolve_flow_fids が計算する）。
    dict_gate=True（既定）は「変数の語義が未承認の関数は①に進ませない」ゲート
    （設計 P2。判定は Project.dict_gate_blockers）。除外したものは gated に
    (func_id, [var_id...]) で積む——理由を人間可読出力にだけ出すため。
    変数辞書が無いプロジェクトでは何も起きない（後方互換）。
    """
    order, _ = p.topo_order()
    fmap = {f["func_id"]: f for f in p.funcs()}
    want = PHASE_LABEL.get(phase or "", phase)
    todo = []
    for fid in order:
        if flow_fids is not None and fid not in flow_fids:
            continue
        s = p.status_of(fmap[fid])
        if s["blocked_by"]:
            continue
        ph = _next_phase(s)
        if not ph:
            continue
        if skip_wait and ((ph == "①spec" and s["spec"] == "draft")
                          or (ph == "②test-spec" and s["test_spec"] == "generated"
                              and not s["test_spec_stale"])):
            continue
        if want and ph != want:
            continue
        if dict_gate and ph == "①spec":
            blockers = p.dict_gate_blockers(fid, spec_status=s["spec"])
            if blockers:
                if gated is not None:
                    gated.append((fid, blockers))
                continue
        todo.append((fid, ph))
    return todo


def cmd_next(p: Project, args) -> None:
    flow_fids = None
    if getattr(args, "flow", None):
        flow_fids = resolve_flow_fids(p, args.flow)
        # --all の出力（fid<TAB>phase）は他スクリプトが機械的に読む可能性があるため、
        # 案内メッセージは stderr に出す（stdout を汚さない）
        print(f"フロー {args.flow}: 到達 {len(flow_fids)} 関数に限定", file=sys.stderr)
    gated: list = []
    todo = actionable(p, getattr(args, "phase", None), getattr(args, "skip_draft", False),
                      flow_fids, dict_gate=getattr(args, "dict_gate", True), gated=gated)
    # dict-gate の除外理由は人間向け。--all の機械可読出力（fid<TAB>phase）を汚さないよう
    # stderr に出す（絞り込み結果そのものは stdout のまま）
    if gated:
        sink = sys.stderr if getattr(args, "all", False) else sys.stdout
        for fid, blockers in gated[:10]:
            ids = ", ".join(blockers[:5]) + ("…" if len(blockers) > 5 else "")
            print(f"dict-gate: {fid} は未承認の変数 {len(blockers)} 件（{ids}）", file=sink)
        if len(gated) > 10:
            print(f"dict-gate: ほか {len(gated) - 10} 関数も同様に除外", file=sink)
        print("  → docs/variables.qmd で承認するか、--no-dict-gate で解除する", file=sink)
    if not getattr(args, "all", False):
        todo = todo[:1]
    if not todo:
        if gated:
            print(f"対象なし（dict-gate で {len(gated)} 関数を除外。"
                  "変数の承認を先に進めるか --no-dict-gate）")
        elif getattr(args, "phase", None) or getattr(args, "skip_draft", False) or flow_fids is not None:
            print("対象なし（フィルタ該当ゼロ。レビュー/承認待ちは status --summary や WBS を参照）")
        else:
            print("全関数完了。⑥ check を実行せよ")
        return
    if getattr(args, "all", False):
        for fid, phase in todo[:args.limit]:      # ドライバ/並列バッチ用: fid<TAB>phase
            print(f"{fid}\t{phase}")
        if len(todo) > args.limit:
            print(f"...（残り {len(todo) - args.limit} 件）")
    else:
        print(f"{todo[0][0]} → 次フェーズ: {todo[0][1]}")


AUDIT_TARGET = "① 未着手（バッチの対象）"


def audit_buckets(p: Project, flow_fids=None) -> tuple:
    """全関数を「なぜ①の対象に入らないか」で分類する。

    WBS の関数数（＝ excluded を除いた数）と、バッチが見ている対象件数が合わない
    ときの内訳。分類の順序は actionable() が対象から外す順序と同じにしてあり、
    「バッチはこう判断している」をそのまま数え直したものになる（別の判定を
    書き起こすと必ずずれるので、判定は Project の同じメソッドを呼ぶ）。

    戻り値は (fid -> 分類名, 分類名 -> [fid...])。
    """
    st = {f["func_id"]: p.status_of(f) for f in p.funcs()}

    def bucket(fid: str) -> str:
        s = st[fid]
        if flow_fids is not None and fid not in flow_fids:
            return "flow の到達集合の外"
        if s["blocked_by"]:
            return f"blocked（{s['blocked_by']} の裁定待ち）"
        if s["spec"] == "reviewed":
            return "① 完了（reviewed）"
        if s["spec"] == "draft":
            return "① draft（人のレビュー待ち。バッチは触らない）"
        if p.dict_gate_blockers(fid, spec_status=s["spec"]):
            return "dict-gate（語義が未承認）"
        if s["spec"] == "-":
            return "骨子なし（docs/specs/<fid>.md が無い）"
        return AUDIT_TARGET

    by_fid = {fid: bucket(fid) for fid in st}
    groups: dict = {}
    for fid, name in by_fid.items():
        groups.setdefault(name, []).append(fid)
    return by_fid, groups


def cmd_audit(p: Project, args) -> None:
    flow_fids = resolve_flow_fids(p, args.flow) if getattr(args, "flow", None) else None
    funcs = p.funcs()
    excluded = [f["func_id"] for f in p.all_funcs() if f.get("excluded")]
    _, groups = audit_buckets(p, flow_fids)

    gated: list = []
    todo = actionable(p, phase="1", skip_wait=True, flow_fids=flow_fids, gated=gated)

    on_disk = sorted(q.stem for q in (p.docs / "specs").glob("F-*.md"))
    known = {f["func_id"] for f in funcs}
    orphan = [fid for fid in on_disk if fid not in known]
    missing = sorted(groups.get("骨子なし（docs/specs/<fid>.md が無い）", []))

    if getattr(args, "json", False):
        print(json.dumps({
            "all": len(p.all_funcs()), "excluded": excluded, "wbs_n": len(funcs),
            "buckets": {k: sorted(v) for k, v in groups.items()},
            "actionable_phase1": [fid for fid, _ in todo],
            "dict_gated": [fid for fid, _ in gated],
            "spec_files_on_disk": len(on_disk), "orphan_spec_files": orphan,
            "missing_skeletons": missing,
        }, ensure_ascii=False, indent=1))
        return

    print(f"functions.json の全関数   : {len(p.all_funcs())}")
    print(f"  対象外（excluded）      : {len(excluded)}")
    print(f"  WBS の「関数数」        : {len(funcs)}  ← excluded は既に引かれている")
    for name in sorted(groups, key=lambda k: -len(groups[k])):
        print(f"    {len(groups[name]):6d}  {name}")
    print(f"\n①バッチの対象（actionable）: {len(todo)}")
    print(f"  dict-gate で外れた関数   : {len(gated)}")
    print(f"docs/specs/ の .md         : {len(on_disk)}")

    if orphan:
        in_excl = [fid for fid in orphan if fid in set(excluded)]
        print(f"\n! functions.json に無い/対象外の仕様書が {len(orphan)} 件"
              f"（うち excluded {len(in_excl)}）: {', '.join(orphan[:20])}"
              + ("…" if len(orphan) > 20 else ""))
        print("  「作った件数」にこれが混ざると WBS の母数と合わなくなる")
    if missing:
        print(f"\n! 骨子が無い関数が {len(missing)} 件: {', '.join(missing[:20])}"
              + ("…" if len(missing) > 20 else ""))
        print("  → `ledger skeletons` で作られる（⓪の再抽出で関数が増えた後に"
              "流し直していないとこうなる）")
        print("  → この状態の関数は `pipeline.py run` の対象にならない"
              "（spec ファイルが無いと次工程を決められないため）。"
              "`pipeline.py spec` は対象にする")
    if gated:
        print(f"\n! dict-gate: {', '.join(fid for fid, _ in gated[:10])}"
              + ("…" if len(gated) > 10 else ""))
        print("  → `/legacy-0-dict` で語義を承認するか `ledger next --no-dict-gate`")


def cmd_next_issue(p: Project, args) -> None:
    nums = [int(m.group(1)) for f in (p.docs / "issues").glob("ISSUE-*.md")
            if (m := re.match(r"ISSUE-(\d+)", f.name))]
    print(f"ISSUE-{(max(nums) + 1 if nums else 1):03d}")


def cmd_add(p: Project, args) -> None:
    """人の指示による関数の後追い追加（⓪の抽出漏れ・関数分割など）。

    manual フラグを付けて採番する——extract_fortran の再実行が
    「ソースに無い関数」として警告し続けないようにするため。
    物理的な手書き追記と違い、func_id の重複や既存キーとの衝突を防げる。
    """
    if not p.functions:
        sys.exit("error: data/functions.json がない（先に⓪の抽出を実行する）")
    funcs = p.functions.setdefault("functions", [])
    name = args.name.upper()
    file = (args.file or "").replace("\\", "/")
    for f in funcs:
        if f["legacy"].get("name", "").upper() == name and f["legacy"].get("file", "") == file:
            sys.exit(f"error: 同じ legacy file/name のエントリが既にある: {f['func_id']}"
                     "（対象外からの復帰なら include を使う）")
    calls = [c for c in (args.calls or "").split(",") if c]
    known = {f["func_id"] for f in funcs}
    unknown = [c for c in calls if c not in known]
    if unknown:
        sys.exit(f"error: --calls に未知の func-id: {', '.join(unknown)}")
    snake = re.sub(r"\W", "_", (args.new_name or args.name).lower())
    package = p.functions.get("project", {}).get("package")
    stem = re.sub(r"\W", "_", Path(file).stem.lower()) if file else snake
    module = args.module or (f"src/{package}/{stem}.py" if package else f"src/{stem}.py")
    nums = [int(m.group(1)) for f in funcs if (m := re.match(r"F-(\d+)", f["func_id"]))]
    fid = f"F-{(max(nums) + 1 if nums else 1):04d}"
    funcs.append({
        "func_id": fid, "manual": True,
        "legacy": {"file": file, "name": name, "lines": args.lines or "", "kind": args.kind},
        "new": {"module": module, "name": snake, "signature": ""},
        "inputs": [], "outputs": [], "globals": [], "external_files": [],
        "calls": calls,
    })
    save_json(p.data / "functions.json", p.functions)
    print(f"added: {fid} {name}（manual・module={module}）")
    print("next: functions.json の inputs/outputs/desc/signature を充填 → "
          "ledger skeletons → ledger wbs（以後は他の関数と同じく①〜⑤を回す）")


def cmd_flow_add(p: Project, args) -> None:
    """フロー（作業スコープ）を追加する（人の指示。references/graph-dict-design.md P3節）。

    functions.json トップレベルの "flows" 配列に保存する。エントリの実在は検証するが、
    到達集合そのものは保存しない（graph.py がその場で計算する導出値。再抽出に自動追随）。
    """
    if not p.functions:
        sys.exit("error: data/functions.json がない（先に⓪の抽出を実行する）")
    flows = p.functions.setdefault("flows", [])
    name = args.name
    for fl in flows:
        if fl.get("name") == name:
            sys.exit(f"error: 同名のフローが既にある: {fl.get('flow_id')}({name})"
                     "（先に `ledger flow rm` するか別名にする）")
    entries = [e for e in (args.entry or "").split(",") if e]
    if not entries:
        sys.exit("error: --entry で func-id を1件以上指定する（カンマ区切りで複数可）")
    fmap = {f["func_id"]: f for f in p.all_funcs()}
    missing = [e for e in entries if e not in fmap]
    if missing:
        sys.exit(f"error: --entry に functions.json 未定義の func-id: {', '.join(missing)}")
    excluded_entries = [e for e in entries if fmap[e].get("excluded")]
    nums = [int(m.group(1)) for fl in flows if (m := re.match(r"FL-(\d+)", fl.get("flow_id", "")))]
    flow_id = f"FL-{(max(nums) + 1 if nums else 1):02d}"
    flows.append({"flow_id": flow_id, "name": name, "entries": entries, "desc": args.desc or ""})
    save_json(p.data / "functions.json", p.functions)
    print(f"added: {flow_id} {name}（entries: {', '.join(entries)}）")
    if excluded_entries:
        print(f"warn: entry に対象外(excluded)の関数が含まれる: {', '.join(excluded_entries)}"
              "（到達集合の計算には使えるが、①〜⑤の対象からは外れたまま）")
    print("next: ledger wbs / ledger skeletons / ledger next --flow で反映")


def cmd_flow_rm(p: Project, args) -> None:
    """フローを削除する（name or flow_id を指定）。"""
    flows = p.functions.get("flows") or []
    for i, fl in enumerate(flows):
        if fl.get("flow_id") == args.key or fl.get("name") == args.key:
            removed = flows.pop(i)
            if not flows:
                p.functions.pop("flows", None)
            save_json(p.data / "functions.json", p.functions)
            print(f"removed: {removed.get('flow_id')} {removed.get('name')}")
            return
    available = ", ".join(f"{fl.get('flow_id')}({fl.get('name')})" for fl in flows)
    sys.exit(f"error: フロー '{args.key}' が見つからない。定義済み: {available or '(flows は未定義)'}")


def cmd_flow_list(p: Project, args) -> None:
    """フロー一覧（flow_id・名前・entries・到達関数数・説明）。"""
    flows = p.functions.get("flows") or []
    if not flows:
        print("flows は未定義（`ledger flow add <name> --entry F-xxxx` で追加）")
        return
    g = graph.graph_from_data(p.functions)
    for fl in flows:
        entries = fl.get("entries", [])
        reached = graph.reachable(g, entries) if entries else set()
        print(f"{fl.get('flow_id')}\t{fl.get('name')}\tentries={', '.join(entries)}"
              f"\t到達{len(reached)}件\t{fl.get('desc', '')}")


def cmd_exclude(p: Project, args) -> None:
    """関数を移植対象から外す（デッドコード・重複・移植不要の判断）。

    functions.json から物理削除はしない——extract の再実行で別 func_id として
    復活し、成果物との紐付けが切れるため。フラグで対象外にする。

    複数指定と `--dead`（エントリから到達不能な関数を一括除外）に対応する。
    `graph.py dead` はあくまで列挙のみ——除外は人がこのコマンドを実行することで
    確定する（--dead も人の明示操作。除外前に対象の一覧を表示する）。
    """
    fids = list(dict.fromkeys(args.func_ids))          # 重複指定を除いて順序維持
    reason = args.reason
    if args.dead:
        g = graph.graph_from_data(p.functions)
        entries, desc = graph.default_entries(p.functions, g)
        reached = graph.reachable(g, entries)
        by_id = {f["func_id"]: f for f in p.functions.get("functions", [])}
        dead = sorted(fid for fid in g
                      if fid not in reached and not by_id.get(fid, {}).get("excluded"))
        if not dead and not fids:
            print(f"dead はありません（エントリ: {', '.join(entries)}〈{desc}〉。"
                  "全関数が到達可能か、既に対象外）")
            return
        print(f"dead {len(dead)} 件（エントリ: {', '.join(entries)}〈{desc}〉から到達不能）:")
        for fid in dead:
            print(f"  {fid} {by_id[fid]['legacy'].get('name', '')}")
        fids += [fid for fid in dead if fid not in fids]
        if not reason:
            reason = f"エントリ（{', '.join(entries)}）から到達不能（dead）"
    if not fids:
        sys.exit("error: func-id を指定する（または --dead）")

    done = []
    for fid in fids:
        f = p.func(fid)
        if f.get("excluded"):
            print(f"skip: {fid} は既に対象外")
            continue
        f["excluded"] = True
        if reason:
            f["excluded_reason"] = reason
        done.append(fid)
        print(f"excluded: {fid} {f['legacy'].get('name', '')}（理由: {reason or '未記載'}）")
        arts = [rp for rp in (f"docs/specs/{fid}.md", f"docs/test-specs/{fid}.md",
                              f.get("test_file") or "", f["new"].get("module", ""))
                if rp and (p.root / rp).exists()]
        if arts:
            print("  note: 既存の成果物は残るが①〜⑥・WBSの対象から外れる: " + ", ".join(arts))
    if not done:
        return
    save_json(p.data / "functions.json", p.functions)
    # 呼び出し元の警告は、今回の除外をすべて反映した後の「対象内」だけで判定する
    # （dead 同士の呼び合いを警告しないため）
    excluded_now = set(done)
    for fid in done:
        callers = [g["func_id"] for g in p.funcs()
                   if fid in g.get("calls", []) and g["func_id"] not in excluded_now]
        if callers:
            print(f"warn: 対象内の関数が {fid} を呼んでいる: " + ", ".join(callers)
                  + " → 呼び出し側の仕様・実装の扱いを確認（判断が要るなら ISSUE 起票）")
    print(f"next: ledger wbs で反映（{len(done)} 件が「対象外の関数」の表に載る）")


def cmd_include(p: Project, args) -> None:
    """対象外にした関数を①〜⑤の対象へ復帰させる。"""
    f = p.func(args.func_id)
    if not f.get("excluded"):
        sys.exit(f"error: {args.func_id} は対象外になっていない")
    f.pop("excluded", None)
    f.pop("excluded_reason", None)
    save_json(p.data / "functions.json", p.functions)
    print(f"included: {args.func_id}（対象に復帰。ledger wbs で反映）")


def cmd_freeze(p: Project, args) -> None:
    f = p.func(args.func_id)
    tf = f.get("test_file")
    if not tf or not (p.root / tf).exists():
        sys.exit(f"error: test_file が未設定か存在しない: {tf}")
    p.ledger.setdefault(args.func_id, {})["test_code_hash"] = sha8(p.root / tf)
    save_json(p.ledger_path, p.ledger)
    print(f"frozen: {tf} = {p.ledger[args.func_id]['test_code_hash']}")


def cmd_block(p: Project, args) -> None:
    p.ledger.setdefault(args.func_id, {})["blocked_by"] = args.issue_id
    save_json(p.ledger_path, p.ledger)
    print(f"blocked: {args.func_id} by {args.issue_id}")


def cmd_unblock(p: Project, args) -> None:
    e = p.ledger.setdefault(args.func_id, {})
    e["blocked_by"] = None
    e["attempt_reset_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    save_json(p.ledger_path, p.ledger)
    print(f"unblocked: {args.func_id}（attempt リセット）")


def cmd_phase_start(p: Project, args) -> None:
    save_json(p.state_dir / "state.json", {"phase": args.phase, "func_id": args.func_id})
    print(f"phase {args.phase} 開始: {args.func_id}（hook 有効）")


def cmd_phase_end(p: Project, args) -> None:
    sp = p.state_dir / "state.json"
    if sp.exists():
        sp.unlink()
    print("phase 終了（hook 解除）")


def cmd_sphinx_index(p: Project, args) -> None:
    """functions.json から docs-sphinx/index.rst を生成（ホーム=関数一覧→個別ページ）。"""
    order, _ = p.topo_order()
    fmap = {f["func_id"]: f for f in p.funcs()}
    rows, dotted_list = [], []
    for fid in order:
        f = fmap[fid]
        mod = re.sub(r"\.py$", "", re.sub(r"^src/", "", f["new"]["module"].replace("\\", "/")))
        dotted = f"{mod.replace('/', '.')}.{f['new']['name']}"
        dotted_list.append(dotted)
        rows.append((fid, f["new"]["name"], dotted, f["legacy"]["name"]))
    name = p.functions.get("project", {}).get("name", "project")
    title = f"{name} 新コード詳細仕様"
    lines = [
        title, "=" * (len(title) * 2), "",
        "④実装の docstring から自動生成。``Spec:`` 行で関数仕様書へトレースできる。", "",
        "関数一覧", "-" * 16, "",
        ".. autosummary::", "   :toctree: functions", "   :nosignatures:", "",
    ]
    lines += [f"   {d}" for d in dotted_list]
    lines += [
        "", "トレース対応表", "-" * 16, "",
        ".. list-table::", "   :header-rows: 1", "   :widths: 12 30 22 36", "",
        "   * - func-id", "     - 新関数", "     - レガシー", "     - 関数仕様書",
    ]
    for fid, new_name, dotted, legacy_name in rows:
        lines += [f"   * - {fid}", f"     - :py:func:`{dotted}`", f"     - {legacy_name}",
                  f"     - `仕様書 <../specs/{fid}.html>`__"]
    out = p.root / "docs-sphinx" / "index.rst"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}")


def cmd_check(p: Project, args) -> None:
    order, cycles = p.topo_order()
    fmap = {f["func_id"]: f for f in p.funcs()}
    rows, counts = [], [0] * 8
    for fid in order:
        s = p.status_of(fmap[fid])
        checks = [
            (0, s["spec_ok"], "①spec が reviewed でない"),
            (1, s["test_spec_ok"], "②test-spec が approved でない/stale"),
            (2, s["test_code_ok"], "③test-code 不在または hash 不一致"),
            (3, s["impl_ok"], "④impl が存在しない"),
            (4, s["test_ok"], "⑤test が pass でない"),
            (5, not s["test_spec_stale"] and not s["test_code_tampered"], "ハッシュ連鎖の不整合"),
        ]
        for idx, ok, msg in checks:
            if not ok:
                counts[idx] += 1
                rows.append((fid, msg))
    open_issues = [f.name[:-3] for f in (p.docs / "issues").glob("ISSUE-*.md")
                   if parse_frontmatter(f.read_text(encoding="utf-8-sig")).get("status") == "open"]
    counts[6] = len(open_issues)
    rows += [("-", f"open ISSUE: {i}") for i in open_issues]
    # 実装率100%チェック（最新⑤の coverage.rate）
    for fid in order:
        latest = p.latest_result(fid)
        rate = (parse_frontmatter(latest.read_text(encoding="utf-8-sig"))
                .get("tc-coverage", {}).get("rate", "") if latest else "")
        if rate != "100%":
            counts[7] += 1
            rows.append((fid, f"⑤実装率が100%でない ({rate or '未実行'})"))

    ok = not rows and not cycles
    names = ["全関数に ①spec (reviewed) がある", "全関数に ②test-spec (approved) がある",
             "全関数に ③test-code があり hash 一致", "全関数に ④impl がある",
             "全関数の ⑤test が pass", "ハッシュ連鎖が全関数で整合",
             "open の ISSUE がゼロ", "全関数の⑤実装率が100%"]
    lines = ["---", 'title: "⑥ 完了検証レポート"', f"status: {'pass' if ok else 'fail'}",
             "run:", f'  date: "{datetime.date.today()}"', "---", "",
             "<!-- ledger.py check による自動生成。手編集禁止 -->", "",
             "# 判定サマリ", "", "| チェック | 結果 | 不備件数 |", "|---------|:---:|:---:|"]
    for i, nm in enumerate(names):
        lines.append(f"| {nm} | {'✅' if counts[i] == 0 else '❌'} | {counts[i]} |")
    n_excl = sum(1 for f in p.all_funcs() if f.get("excluded"))
    lines += ["", f"検証対象: {len(order)} 関数"
              + (f"（対象外 {n_excl} 件は検証しない。WBS の「対象外の関数」を参照）"
                 if n_excl else "")]
    lines += ["", "# 不備一覧", ""]
    if rows:
        lines += ["| 関数 | 不備 |", "|------|------|"] + [f"| {a} | {b} |" for a, b in rows]
    else:
        lines.append("なし。⑥ 完了 🎉")
    (p.docs / "completion-check.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"check: {'pass' if ok else f'fail（不備 {len(rows)} 件）'} → docs/completion-check.md")
    sys.exit(0 if ok else 1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".", help="対象プロジェクトのルート")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("wbs")
    s = sub.add_parser("skeletons"); s.add_argument("--force", action="store_true")
    s = sub.add_parser("migrate-specs",
                       help="既存の仕様書の「例外・数値特異点」節を機械が維持する"
                            "（節の追加・増えた hazard 行の追加・空欄の適用EPの転記。本文は触らない）")
    s.add_argument("--dry-run", action="store_true", help="書き換えず対象だけ表示")
    sub.add_parser("init-templates", help="人が書くファイル（規約・業務知識・例外ポリシー・仕様書テンプレ・工程別プロンプト）の雛形を一式配置（既存は上書きしない）")
    s = sub.add_parser("authored", help="人が書くファイルの記入状況を一覧（未作成/未記入/記入途中/記入あり）")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("hash"); s.add_argument("path")
    s = sub.add_parser("verify"); s.add_argument("func_id")
    s = sub.add_parser("status"); s.add_argument("func_id", nargs="?"); s.add_argument("--json", action="store_true"); s.add_argument("--summary", action="store_true")
    s = sub.add_parser("next"); s.add_argument("--all", action="store_true"); s.add_argument("--limit", type=int, default=20); s.add_argument("--phase", help="1〜5 でフェーズ絞り込み（バッチ実行の対象選定用）"); s.add_argument("--skip-draft", action="store_true", help="人のレビュー/承認待ち（①draft・②generated）を除外（バッチ再開用）"); s.add_argument("--flow", default=None, help="フロー名 or flow_id で対象をそのフロー到達集合に限定"); s.add_argument("--no-dict-gate", dest="dict_gate", action="store_false", default=True, help="変数辞書のゲートを解除（既定は ON: 未承認の語義が残る関数の①を除外）")
    s = sub.add_parser("audit", help="WBS の関数数と①バッチの対象件数が合わない原因を内訳で出す")
    s.add_argument("--flow", default=None, help="バッチに --flow を付けているなら同じ値を指定")
    s.add_argument("--json", action="store_true")
    sub.add_parser("next-issue")
    s = sub.add_parser("flow", help="フロー（作業スコープ）の管理")
    flow_sub = s.add_subparsers(dest="flow_cmd", required=True)
    fa = flow_sub.add_parser("add", help="フローを追加（functions.json の flows に保存）")
    fa.add_argument("name", help="フロー名（一意）")
    fa.add_argument("--entry", required=True,
                    help="エントリの func-id（カンマ区切りで複数可。例: F-0000,F-0006）")
    fa.add_argument("--desc", default="", help="説明")
    fr = flow_sub.add_parser("rm", help="フローを削除")
    fr.add_argument("key", help="フロー名 or flow_id")
    flow_sub.add_parser("list", help="フロー一覧（entries・到達関数数つき）")
    s = sub.add_parser("add", help="関数を後追い追加（人の指示。manual フラグ付きで採番）")
    s.add_argument("name", help="レガシー側の関数名")
    s.add_argument("--file", default="", help="レガシーファイル（例: legacy/tax.f。無いなら省略可）")
    s.add_argument("--lines", default="", help="行範囲（例: 120-240）")
    s.add_argument("--kind", default="subroutine")
    s.add_argument("--module", help="新実装の配置先（既定: src/<package>/<file名>.py）")
    s.add_argument("--new-name", dest="new_name", help="新関数名（既定: name の snake_case）")
    s.add_argument("--calls", help="呼び出す func-id をカンマ区切り（例: F-0001,F-0002）")
    s = sub.add_parser("exclude", help="関数を移植対象から外す（物理削除はしない。複数指定・--dead 可）")
    s.add_argument("func_ids", nargs="*", metavar="func_id",
                   help="関数ID（複数可。--dead と併用もできる）")
    s.add_argument("--dead", action="store_true",
                   help="エントリから到達不能な関数（graph.py dead と同じ集合）を一括で対象外にする")
    s.add_argument("--reason", default="", help="対象外の理由（WBSに載る。--dead の既定は「到達不能」）")
    s = sub.add_parser("include", help="対象外にした関数を復帰させる")
    s.add_argument("func_id")
    s = sub.add_parser("freeze-tests"); s.add_argument("func_id")
    s = sub.add_parser("block"); s.add_argument("func_id"); s.add_argument("issue_id")
    s = sub.add_parser("unblock"); s.add_argument("func_id")
    s = sub.add_parser("phase-start"); s.add_argument("phase"); s.add_argument("func_id")
    sub.add_parser("phase-end")
    sub.add_parser("check")
    sub.add_parser("sphinx-index")
    args = ap.parse_args()
    p = Project(Path(args.root).resolve())
    if args.cmd == "flow":
        {"add": cmd_flow_add, "rm": cmd_flow_rm, "list": cmd_flow_list}[args.flow_cmd](p, args)
        return
    {"wbs": cmd_wbs, "skeletons": cmd_skeletons, "migrate-specs": cmd_migrate_specs,
     "init-templates": cmd_init_templates,
     "authored": cmd_authored,
     "hash": cmd_hash, "verify": cmd_verify,
     "status": cmd_status, "next": cmd_next, "audit": cmd_audit,
     "next-issue": cmd_next_issue,
     "add": cmd_add, "exclude": cmd_exclude, "include": cmd_include,
     "freeze-tests": cmd_freeze, "block": cmd_block, "unblock": cmd_unblock,
     "phase-start": cmd_phase_start, "phase-end": cmd_phase_end, "check": cmd_check,
     "sphinx-index": cmd_sphinx_index}[args.cmd](p, args)


if __name__ == "__main__":
    main()
