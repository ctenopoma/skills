#!/usr/bin/env python3
"""legacy-reverse 台帳スクリプト。

対象プロジェクトのルートで実行する（--root で変更可）。
機械可読メタデータ（functions.json / ledger.json / 各mdフロントマター）から
WBS・骨子・完了検証を生成し、ハッシュ連鎖とブロック状態を管理する。

サブコマンド:
  wbs                        docs/index.qmd を再生成
  skeletons [--force]        functions.json から docs/specs/ の骨子を生成
  hash <path>                sha256 先頭8桁を表示
  verify <func-id>           ハッシュ連鎖（①→②、③）を検証
  status [<func-id>]         フェーズ状況を表示（機械可読 JSON も可: --json）
  next                       次に着手すべき関数を提案（トポロジカル順）
  next-issue                 次の ISSUE 番号を表示
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
        return json.loads(path.read_text(encoding="utf-8-sig"))
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

    # ---------- 状態判定（schema.md の表が正） ----------
    def status_of(self, f: dict) -> dict:
        fid = f["func_id"]
        led = self.ledger.get(fid, {})
        s: dict = {"func_id": fid, "blocked_by": led.get("blocked_by")}

        spec_p = self.docs / "specs" / f"{fid}.md"
        spec_fm = self.fm(f"docs/specs/{fid}.md")
        s["spec"] = spec_fm.get("status", "-") if spec_p.exists() else "-"
        s["spec_ok"] = s["spec"] == "reviewed"

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
    c1 = _mark(s["spec_ok"], f"{pre}specs/{fid}.md",
               "" if s["spec_ok"] or s["spec"] == "-" else s["spec"])
    c2 = _mark(s["test_spec_ok"], f"{pre}test-specs/{fid}.md",
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


def cmd_wbs(p: Project, args) -> None:
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
    if cycles:
        lines += ["::: {.callout-warning}", "コールグラフに循環があります: "
                  + " / ".join("→".join(c) for c in cycles), ":::", ""]

    # ---- 要対応（人が最初に見るべきもの。大規模でもここだけ見れば良い） ----
    blocked = [(fid, stats[fid]["blocked_by"]) for fid in order if stats[fid]["blocked_by"]]
    stale = [fid for fid in order if stats[fid]["test_spec_stale"]]
    tampered = [fid for fid in order if stats[fid]["test_code_tampered"]]
    failing = [fid for fid in order
               if stats[fid]["test"] == "fail" and not stats[fid]["blocked_by"]]
    drafts = [fid for fid in order if stats[fid]["spec"] == "draft"]
    if blocked or stale or tampered or failing or drafts:
        lines += ["# 要対応", "", "| 種別 | 関数 | 詳細 |", "|------|------|------|"]
        if drafts:
            ref = ("[一斉レビュー表](spec-review.md)"
                   if (p.docs / "spec-review.md").exists() else "関数一覧の ▲draft を参照")
            lines.append(f"| ▲ ①レビュー待ち | {len(drafts)} 件 | {ref} |")
        for fid, iss in blocked:
            lines.append(f"| ⛔ 裁定待ち | {_spec_ref(fid, stats[fid])} | [{iss}](issues/{iss}.md) |")
        for fid in stale:
            lines.append(f"| ⚠ ②stale | {_spec_ref(fid, stats[fid])} | ①改訂済み → ②要再確認 |")
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
                       or s["test_code_tampered"] or s["test"] == "fail")
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


def cmd_skeletons(p: Project, args) -> None:
    tdir = p.docs / "specs"
    tdir.mkdir(parents=True, exist_ok=True)
    made = 0
    for f in p.funcs():
        fid = f["func_id"]
        out = tdir / f"{fid}.md"
        if out.exists() and not args.force:
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
            "legacy:",
            f'  file: "{f["legacy"]["file"]}"',
            f'  name: "{f["legacy"]["name"]}"',
            f'  lines: "{f["legacy"].get("lines", "")}"',
            f'  hash: "{lhash}"',
            "new:",
            f'  module: "{f["new"]["module"]}"',
            f'  signature: "{f["new"].get("signature", "")}"',
            "---",
            "", "# 概要", "", "<!-- ①で充填 -->", "",
            "# 処理フロー", "",
            "<!-- 分岐が3本以上ある場合のみ①が ```mermaid の flowchart を書く"
            "（```{mermaid} は render を落とす）。不要なら節ごと削除 -->", "",
            "# インタフェース", "", "## 入力", "",
            "| # | 名前 | レガシー型 | 新型 | 説明 | Confidence |",
            "|---|------|-----------|------|------|:---:|",
        ]
        body += [f"| {i+1} | {it.get('name','')} | {it.get('legacy_type','')} | "
                 f"{it.get('new_type','')} | {it.get('desc','')} | 🟢 |"
                 for i, it in enumerate(f.get("inputs", []))] or ["| | | | | | |"]
        body += ["", "## 出力", "",
                 "| 名前 | レガシー型 | 新型 | 説明 | Confidence |",
                 "|------|-----------|------|------|:---:|"]
        body += rows(f.get("outputs", []), ["name", "legacy_type", "new_type", "desc"])
        body += ["", "## グローバル状態", "",
                 "| 名前 | 読み/書き | 説明 | Confidence |", "|------|:---:|------|:---:|"]
        body += rows(f.get("globals", []), ["name", "access", "desc"])
        body += ["", "## 参照外部ファイル", "",
                 "| ファイル | 読み/書き | 用途 | Confidence |", "|---------|:---:|------|:---:|"]
        body += rows(f.get("external_files", []), ["path", "access", "desc"])
        body += ["", "## 呼び出しサブルーチン", "", "| 名前 | func-id | 用途 |", "|------|---------|------|"]
        for c in f.get("calls", []):
            body.append(f"| | {c} | |")
        if not f.get("calls"):
            body.append("| （なし） | | |")
        body += ["", "# 機能詳細", "",
                 f"<!-- ①で充填。見出しIDは SPEC-{num}-01 形式、各項目に Confidence と根拠(file:lines)必須 -->",
                 "", "# 副作用・例外", "", "<!-- ①で充填。なければ「なし」と明記 -->", "",
                 "# 未確定事項", "", "| ISSUE | 内容 | 状態 |", "|-------|------|------|", ""]
        out.write_text("\n".join(body), encoding="utf-8")
        made += 1
    print(f"skeletons: {made} 件生成（既存はスキップ、--force で上書き）")


def cmd_hash(p: Project, args) -> None:
    print(sha8(p.root / args.path))


def cmd_verify(p: Project, args) -> None:
    f = p.func(args.func_id)
    s = p.status_of(f)
    ok = True
    if s["test_spec_stale"]:
        ok = False
        print(f"NG: ②の spec-hash が①の現物と不一致（①が改訂済み）→ ②要再確認")
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


def actionable(p: Project, phase: str = None, skip_wait: bool = False) -> list:
    """着手可能な (func_id, 次フェーズ) をトポロジカル順で返す。

    skip_wait=True は人のレビュー/承認待ち（①draft・②generated）を除外し
    「AIの作業が残っているもの」だけにする——バッチ全件モードの再開時、
    draft を二重に書き直さないための機械的な区別。pipeline.py も共用する。
    """
    order, _ = p.topo_order()
    fmap = {f["func_id"]: f for f in p.funcs()}
    want = PHASE_LABEL.get(phase or "", phase)
    todo = []
    for fid in order:
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
        todo.append((fid, ph))
    return todo


def cmd_next(p: Project, args) -> None:
    todo = actionable(p, getattr(args, "phase", None), getattr(args, "skip_draft", False))
    if not getattr(args, "all", False):
        todo = todo[:1]
    if not todo:
        if getattr(args, "phase", None) or getattr(args, "skip_draft", False):
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
    s = sub.add_parser("hash"); s.add_argument("path")
    s = sub.add_parser("verify"); s.add_argument("func_id")
    s = sub.add_parser("status"); s.add_argument("func_id", nargs="?"); s.add_argument("--json", action="store_true"); s.add_argument("--summary", action="store_true")
    s = sub.add_parser("next"); s.add_argument("--all", action="store_true"); s.add_argument("--limit", type=int, default=20); s.add_argument("--phase", help="1〜5 でフェーズ絞り込み（バッチ実行の対象選定用）"); s.add_argument("--skip-draft", action="store_true", help="人のレビュー/承認待ち（①draft・②generated）を除外（バッチ再開用）")
    sub.add_parser("next-issue")
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
    {"wbs": cmd_wbs, "skeletons": cmd_skeletons, "hash": cmd_hash, "verify": cmd_verify,
     "status": cmd_status, "next": cmd_next, "next-issue": cmd_next_issue,
     "add": cmd_add, "exclude": cmd_exclude, "include": cmd_include,
     "freeze-tests": cmd_freeze, "block": cmd_block, "unblock": cmd_unblock,
     "phase-start": cmd_phase_start, "phase-end": cmd_phase_end, "check": cmd_check,
     "sphinx-index": cmd_sphinx_index}[args.cmd](p, args)


if __name__ == "__main__":
    main()
