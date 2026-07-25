#!/usr/bin/env python3
"""desktop2web 台帳。legacy-reverse の ledger を土台に、画面レーンを重ねる。

サブコマンド:
  wbs               docs/index.qmd（画面＋機能の統合WBS）を再生成
  crud              docs/crud.md（画面×機能×テーブルのCRUDマトリクス）を再生成
  screen-skeletons  screens.json の screens から docs/screens/S-xxx.md の骨子を生成
  next              次に着手すべき画面と、その前提で未完了の機能を提案
機能レーンの操作（verify/freeze/block等）は legacy-reverse の ledger.py をそのまま使う。
"""
import argparse
import json
import sys
from pathlib import Path

LR_SCRIPTS = Path(__file__).resolve().parents[2] / "legacy-reverse" / "scripts"
sys.path.insert(0, str(LR_SCRIPTS))
from ledger import Project, load_json, parse_frontmatter  # noqa: E402

MOCK_STATES = ["draft", "mock-review", "design-fixed", "implemented", "e2e-pass"]


class D2W:
    def __init__(self, root: Path):
        self.root = root
        self.docs = root / "docs"
        self.p = Project(root)  # 機能レーン
        self.screens_data = load_json(root / "data" / "screens.json", {})
        self.schema = load_json(root / "data" / "schema.json", {})

    def screens(self) -> list:
        return self.screens_data.get("screens", [])

    def screen_fm(self, sid: str) -> dict:
        f = self.docs / "screens" / f"{sid}.md"
        return parse_frontmatter(f.read_text(encoding="utf-8-sig")) if f.exists() else {}

    def func_done(self, fid: str) -> bool:
        try:
            return self.p.status_of(self.p.func(fid))["test_ok"]
        except SystemExit:
            return False


def cmd_screen_skeletons(d: D2W, args) -> None:
    tdir = d.docs / "screens"
    tdir.mkdir(parents=True, exist_ok=True)
    made = 0
    for s in d.screens():
        out = tdir / f"{s['screen_id']}.md"
        if out.exists() and not args.force:
            continue
        legacy = json.dumps(s.get("legacy", []), ensure_ascii=False)
        uses = json.dumps(s.get("uses", []), ensure_ascii=False)
        body = f"""---
title: "{s.get('title', s['screen_id'])}"
screen-id: "{s['screen_id']}"
legacy: {legacy}
uses: {uses}
route: "{s.get('route', '')}"
status: draft
mock-version: 0
e2e: null
e2e-frozen: null
approved-by: null
approved-date: null
---

# 旧画面の分析

| 旧画面 | 役割 | UX問題点 |
|--------|------|---------|

# 新画面の狙い（Plan）

# 受け入れ基準（design-fixed 時に凍結）

| # | Given | When | Then |
|---|-------|------|------|

# モック履歴

| v | ファイル | レビュー指摘（要約） | 対応 |
|---|---------|--------------------|------|

# 確定デザイン

# 実装・E2E記録
"""
        out.write_text(body, encoding="utf-8")
        made += 1
    print(f"screen-skeletons: {made} 件生成")


def screen_rows(d: D2W) -> list:
    rows = []
    for s in d.screens():
        sid = s["screen_id"]
        fm = d.screen_fm(sid)
        uses = s.get("uses", [])
        done = sum(1 for f in uses if d.func_done(f))
        rows.append({
            "sid": sid, "title": fm.get("title", s.get("title", "")),
            "legacy": ", ".join(s.get("legacy", [])),
            "uses": f"{done}/{len(uses)}",
            "uses_list": uses,
            "status": fm.get("status", "-"),
            "mock_v": fm.get("mock-version", "0"),
            "e2e": fm.get("e2e"),
            "ready": done == len(uses),
        })
    return rows


def cmd_wbs(d: D2W, args) -> None:
    rows = screen_rows(d)
    pol = parse_frontmatter((d.docs / "policy.md").read_text(encoding="utf-8-sig")) \
        if (d.docs / "policy.md").exists() else {}
    funcs = d.p.funcs()
    fstat = {f["func_id"]: d.p.status_of(f) for f in funcs}

    n_scr = len(rows)
    lines = [
        "---",
        f'title: "{d.p.functions.get("project", {}).get("name", "project")} desktop2web WBS"',
        "date: last-modified", "---", "",
        "<!-- d2w_ledger.py wbs による自動生成。手編集禁止 -->", "",
        f"**Ⓐ 方針書**: [{pol.get('status', '未作成')}](policy.md)　"
        f"**CRUD**: [マトリクス](crud.md)", "",
        "# 進捗サマリ", "",
        "| 画面 e2e-pass | 機能 ⑤pass |", "|:---:|:---:|",
        f"| {sum(1 for r in rows if r['status'] == 'e2e-pass')}/{n_scr} "
        f"| {sum(1 for s in fstat.values() if s['test_ok'])}/{len(funcs)} |", "",
    ]
    # Open ISSUES（legacy-reverse と同運用）
    issues = []
    for ip in sorted((d.docs / "issues").glob("ISSUE-*.md")):
        ifm = parse_frontmatter(ip.read_text(encoding="utf-8-sig"))
        if ifm.get("status") == "open":
            issues.append(f"| [{ip.stem}](issues/{ip.name}) | {ifm.get('kind', '')} "
                          f"| {ifm.get('title', '')} |")
    lines += ["# Open ISSUES（要人間判断）", ""]
    lines += (["| ID | 種別 | 内容 |", "|----|------|------|"] + issues) if issues else ["なし 🎉"]

    lines += ["", "# 画面イタレーション", "",
              "<!-- uses = その画面が使う機能の ⑤pass 数。全部揃うまで画面実装には入らない -->", "",
              "| ID | 画面 | 旧画面 | uses(⑤済) | モック | 状態 | E2E |",
              "|----|------|--------|:---:|:---:|------|:---:|"]
    for r in rows:
        e2e = {"pass": "✅", "fail": "❌"}.get(r["e2e"], "—")
        note = "" if r["ready"] else " ⏳機能待ち"
        lines.append(f"| [{r['sid']}](screens/{r['sid']}.md) | {r['title']} | {r['legacy']} "
                     f"| {r['uses']}{note} | v{r['mock_v']} | {r['status']} | {e2e} |")

    lines += ["", "# 機能一覧（鏡移しレーン）", "",
              "| 関数 | ①spec | ②test-spec | ③test-code | ④impl | ⑤test |",
              "|------|:---:|:---:|:---:|:---:|:---:|"]
    for fid, s in fstat.items():
        m = lambda ok: "✅" if ok else "☐"  # noqa: E731
        lines.append(f"| [{fid}](specs/{fid}.md) | {m(s['spec_ok'])} | {m(s['test_spec_ok'])} "
                     f"| {m(s['test_code_ok'])} | {m(s['impl_ok'])} | {m(s['test_ok'])} |")

    out = d.docs / "index.qmd"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}")


def cmd_crud(d: D2W, args) -> None:
    funcs = {f["func_id"]: f for f in d.p.funcs()}
    tables = [t["name"] for t in d.schema.get("tables", [])]
    lines = ["---", 'title: "CRUDマトリクス"', "---", "",
             "<!-- d2w_ledger.py crud による自動生成。手編集禁止 -->", "",
             "# 機能 × テーブル", "",
             "| 機能 | " + " | ".join(tables) + " |",
             "|------|" + "|".join([":---:"] * len(tables)) + "|"]
    touched = {t: "" for t in tables}
    for fid, f in funcs.items():
        crud = f.get("crud", {})
        cells = []
        for t in tables:
            op = crud.get(t, "")
            cells.append(op or "")
            touched[t] += op
        lines.append(f"| {fid} {f['new'].get('name', '')} | " + " | ".join(cells) + " |")
    lines += ["", "# 画面 × 機能", "", "| 画面 | 機能 |", "|------|------|"]
    for s in d.screens():
        lines.append(f"| {s['screen_id']} {s.get('title', '')} | " + ", ".join(s.get("uses", [])) + " |")
    orphan = [t for t, ops in touched.items() if not ops]
    lines += ["", "# 検出事項", ""]
    lines.append(("- ⚠ どの機能からも触られていないテーブル: " + ", ".join(orphan))
                 if orphan else "- 全テーブルがいずれかの機能から参照されている")
    (d.docs / "crud.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {d.docs / 'crud.md'}" + (f"  ⚠ orphan: {len(orphan)}" if orphan else ""))


def cmd_next(d: D2W, args) -> None:
    rows = [r for r in screen_rows(d) if r["status"] != "e2e-pass"]
    if not rows:
        print("全画面完了。d2w-6-check を実行せよ")
        return
    # 未完了機能が少ない画面から
    rows.sort(key=lambda r: (len([f for f in r["uses_list"] if not d.func_done(f)]),
                             r["sid"]))
    r = rows[0]
    pend = [f for f in r["uses_list"] if not d.func_done(f)]
    print(f"{r['sid']} {r['title']}（状態: {r['status']}）")
    print("  未完了の機能: " + (", ".join(pend) if pend else "なし（画面実装に入れる）"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("wbs")
    sub.add_parser("crud")
    s = sub.add_parser("screen-skeletons"); s.add_argument("--force", action="store_true")
    sub.add_parser("next")
    args = ap.parse_args()
    d = D2W(Path(args.root).resolve())
    {"wbs": cmd_wbs, "crud": cmd_crud, "screen-skeletons": cmd_screen_skeletons,
     "next": cmd_next}[args.cmd](d, args)


if __name__ == "__main__":
    main()
