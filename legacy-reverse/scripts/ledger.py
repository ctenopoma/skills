#!/usr/bin/env python3
"""legacy-reverse 台帳スクリプト。

対象プロジェクトのルートで実行する（--root で変更可）。
機械可読メタデータ（functions.json / ledger.json / 各mdフロントマター）から
WBS・骨子・完了検証を生成し、ハッシュ連鎖とブロック状態を管理する。

サブコマンド:
  wbs                        docs/index.qmd を再生成
  skeletons [--force]        functions.json から docs/specs/ の骨子を生成
                             （項目立ては docs/templates/spec.md が正。無ければ同梱シード）
  init-templates             テンプレのシードを docs/templates/ にコピー（以後は人が編集）
  hash <path>                sha256 先頭8桁を表示
  verify <func-id>           ハッシュ連鎖（①→②、③）を検証
  status [<func-id>]         フェーズ状況を表示（機械可読 JSON も可: --json）
  next [--flow <name|id>] [--no-dict-gate]
                             次に着手すべき関数を提案（トポロジカル順。--flow でフロー到達集合に限定。
                             既定では変数辞書に未承認の語義が残る関数の①を除外＝dict-gate）
  next-issue                 次の ISSUE 番号を表示
  flow add <name> --entry F-xxxx[,F-yyyy...] [--desc ...]
                             フロー（作業スコープ）を追加。flow rm <name|id> / flow list
  add <name> [--file ...]    関数を後追い追加（人の指示。manual フラグ付きで採番）
  exclude <func-id> [--reason ...] / include <func-id>
                             移植対象から外す／復帰させる（物理削除はしない）
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
    if blocked or stale or dict_stale or tampered or failing or drafts:
        lines += ["# 要対応", "", "| 種別 | 関数 | 詳細 |", "|------|------|------|"]
        if drafts:
            ref = ("[一斉レビュー表](spec-review.md)"
                   if (p.docs / "spec-review.md").exists() else "関数一覧の ▲draft を参照")
            lines.append(f"| ▲ ①レビュー待ち | {len(drafts)} 件 | {ref} |")
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


def _sync_dict_hash(p: Project, path: Path, fid: str) -> int:
    """既存の骨子（status: skeleton のまま＝①未着手）の dict-hash を現在値に合わせる。

    辞書の承認は⓪の骨子生成より後に進む（承認 → propagate → skeletons の順で
    review_actions が呼ぶ）。骨子を上書きしない既定のままだと、①が読む骨子の
    dict-hash が「承認ゼロ時点の値」で固定され、①直後にいきなり stale になってしまう。
    **①未着手の骨子だけ**を対象に、その場で dict-hash 行を差し替える
    （draft / reviewed＝AI・人が書いた成果物には一切触れない）。戻り値は更新件数。
    """
    text = path.read_text(encoding="utf-8-sig")
    if parse_frontmatter(text).get("status") != "skeleton":
        return 0
    line = f'dict-hash: "{p.dict_hash(fid)}"'
    lines = text.splitlines()
    fences = [i for i, l in enumerate(lines) if l.strip() == "---"][:2]
    if len(fences) < 2:
        return 0
    start, end = fences
    for i in range(start + 1, end):
        if lines[i].startswith("dict-hash:"):
            if lines[i] == line:
                return 0
            lines[i] = line
            break
    else:
        anchor = next((i for i in range(start + 1, end) if lines[i].startswith("status:")), start)
        lines.insert(anchor + 1, line)
    path.write_text("\n".join(lines) + ("\n" if text.endswith("\n") else ""),
                    encoding="utf-8")
    return 1


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


def spec_template_problems(body: str) -> list:
    """①テンプレが固定契約を満たしているかの検証（不足の一覧を返す）。"""
    problems = [f"置換マーカーがない: {m}" for m in SPEC_TEMPLATE_MARKERS if m not in body]
    problems += [f"契約見出しがない: {h}" for h in SPEC_CONTRACT_HEADS
                 if not re.search(rf"(?m)^{re.escape(h)}\s*$", body)]
    return problems


def cmd_init_templates(p: Project, args) -> None:
    """シードを docs/templates/ にコピーする（既存は上書きしない。以後は人が編集）。"""
    tdir = p.docs / "templates"
    tdir.mkdir(parents=True, exist_ok=True)
    for name in TEMPLATE_NAMES.values():
        dst = tdir / name
        if dst.exists():
            print(f"skip: {dst}（既存。編集して使う）")
            continue
        shutil.copy2(ASSETS_TEMPLATES / name, dst)
        print(f"copied: {dst}（項目立て・書き方ガイドはこのファイルを人が編集する）")


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
    made = synced = 0

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
            if p.has_dict():
                synced += _sync_dict_hash(p, out, fid)
            continue
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

        haz_lines = ["| hazard | 種別 | 箇所 | 適用EP | 仕様記述 |",
                     "|--------|------|------|--------|----------|"]
        haz_lines += [f"| {h['hz_id']} | {h['kind']} | {f['legacy'].get('file', '')}:{h.get('line', '')} | | |"
                      for h in f.get("hazards", [])] or ["| 該当なし | | | | |"]

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
        out.write_text("\n".join(body) + "\n" + text.rstrip("\n") + "\n", encoding="utf-8")
        made += 1
    print(f"skeletons: {made} 件生成（既存はスキップ、--force で上書き）"
          + (f" / dict-hash 更新 {synced} 件（①未着手の骨子のみ）" if synced else ""))


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
    """
    f = p.func(args.func_id)
    if f.get("excluded"):
        sys.exit(f"error: {args.func_id} は既に対象外")
    f["excluded"] = True
    if args.reason:
        f["excluded_reason"] = args.reason
    save_json(p.data / "functions.json", p.functions)
    fid = args.func_id
    print(f"excluded: {fid} {f['legacy'].get('name', '')}（理由: {args.reason or '未記載'}）")
    arts = [rp for rp in (f"docs/specs/{fid}.md", f"docs/test-specs/{fid}.md",
                          f.get("test_file") or "", f["new"].get("module", ""))
            if rp and (p.root / rp).exists()]
    if arts:
        print("note: 既存の成果物は残るが①〜⑥・WBSの対象から外れる: " + ", ".join(arts))
    callers = [g["func_id"] for g in p.funcs() if fid in g.get("calls", [])]
    if callers:
        print("warn: 対象内の関数がこの関数を呼んでいる: " + ", ".join(callers)
              + " → 呼び出し側の仕様・実装の扱いを確認（判断が要るなら ISSUE 起票）")
    print("next: ledger wbs で反映（「対象外の関数」の表に載る）")


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
    sub.add_parser("init-templates", help="仕様書テンプレのシードを docs/templates/ にコピー（以後は人が編集）")
    s = sub.add_parser("hash"); s.add_argument("path")
    s = sub.add_parser("verify"); s.add_argument("func_id")
    s = sub.add_parser("status"); s.add_argument("func_id", nargs="?"); s.add_argument("--json", action="store_true"); s.add_argument("--summary", action="store_true")
    s = sub.add_parser("next"); s.add_argument("--all", action="store_true"); s.add_argument("--limit", type=int, default=20); s.add_argument("--phase", help="1〜5 でフェーズ絞り込み（バッチ実行の対象選定用）"); s.add_argument("--skip-draft", action="store_true", help="人のレビュー/承認待ち（①draft・②generated）を除外（バッチ再開用）"); s.add_argument("--flow", default=None, help="フロー名 or flow_id で対象をそのフロー到達集合に限定"); s.add_argument("--no-dict-gate", dest="dict_gate", action="store_false", default=True, help="変数辞書のゲートを解除（既定は ON: 未承認の語義が残る関数の①を除外）")
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
    s = sub.add_parser("exclude", help="関数を移植対象から外す（物理削除はしない）")
    s.add_argument("func_id"); s.add_argument("--reason", default="", help="対象外の理由（WBSに載る）")
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
    {"wbs": cmd_wbs, "skeletons": cmd_skeletons, "init-templates": cmd_init_templates,
     "hash": cmd_hash, "verify": cmd_verify,
     "status": cmd_status, "next": cmd_next, "next-issue": cmd_next_issue,
     "add": cmd_add, "exclude": cmd_exclude, "include": cmd_include,
     "freeze-tests": cmd_freeze, "block": cmd_block, "unblock": cmd_unblock,
     "phase-start": cmd_phase_start, "phase-end": cmd_phase_end, "check": cmd_check,
     "sphinx-index": cmd_sphinx_index}[args.cmd](p, args)


if __name__ == "__main__":
    main()
