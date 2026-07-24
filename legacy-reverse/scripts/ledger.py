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
        return self.functions.get("functions", [])

    def func(self, func_id: str) -> dict:
        for f in self.funcs():
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
        "---",
        "",
        "<!-- ledger.py wbs による自動生成。手編集禁止 -->",
        "",
        "# 進捗サマリ",
        "",
        "| ①spec | ②test-spec | ③test-code | ④impl | ⑤test |",
        "|:---:|:---:|:---:|:---:|:---:|",
        f"| {count('spec_ok')}/{n} | {count('test_spec_ok')}/{n} | {count('test_code_ok')}/{n}"
        f" | {count('impl_ok')}/{n} | {count('test_ok')}/{n} |",
        "",
    ]
    if cycles:
        lines += ["::: {.callout-warning}", "コールグラフに循環があります: "
                  + " / ".join("→".join(c) for c in cycles), ":::", ""]

    lines += ["# Open ISSUES（要人間判断）", ""]
    if issues:
        lines += ["| ID | 種別 | 関数 | 内容 |", "|----|------|------|------|"]
        for name, ifm in issues:
            lines.append(
                f"| [{name}](issues/{name}.md) | {ifm.get('kind','')} "
                f"| {ifm.get('func-id','')} | {ifm.get('title','')} |")
    else:
        lines.append("なし 🎉")
    lines += ["", "# 関数一覧（推奨着手順）", "",
              "| # | 関数 | 依存 | ①spec | ②test-spec | ③test-code | ④impl | ⑤test |",
              "|:-:|------|------|:---:|:---:|:---:|:---:|:---:|"]

    def mark(ok, link=None, note=""):
        sym = "✅" if ok else ("▲" if note else "☐")
        body = f"{sym}{(' ' + note) if note else ''}"
        return f"[{body}]({link})" if link and (ok or note) else body

    for i, fid in enumerate(order, 1):
        f, s = fmap[fid], stats[fid]
        name = f["new"].get("name", fid)
        deps = ", ".join(f.get("calls", [])) or "なし"
        spec_link = f"specs/{fid}.md"
        c1 = mark(s["spec_ok"], spec_link, "" if s["spec_ok"] or s["spec"] == "-" else s["spec"])
        c2 = mark(s["test_spec_ok"], f"test-specs/{fid}.md",
                  "stale⚠" if s["test_spec_stale"] else ("" if s["test_spec_ok"] or s["test_spec"] == "-" else s["test_spec"]))
        c3 = "⚠改変" if s["test_code_tampered"] else ("✅" if s["test_code_ok"] else "☐")
        c4 = "✅" if s["impl_ok"] else "☐"
        if s["blocked_by"]:
            c5 = f"[⛔ {s['blocked_by']}](issues/{s['blocked_by']}.md)"
        elif s["test"] == "pass":
            c5 = f"[✅]({s['result_file']})"
        elif s["test"] == "fail":
            c5 = f"[❌ {s['attempt']}回目]({s['result_file']})"
        else:
            c5 = "☐"
        lines.append(f"| {i} | [{name}](specs/{fid}.md) | {deps} | {c1} | {c2} | {c3} | {c4} | {c5} |")

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
        lhash = sha8(legacy_p) if legacy_p.exists() else ""
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


def cmd_next(p: Project, args) -> None:
    order, _ = p.topo_order()
    fmap = {f["func_id"]: f for f in p.funcs()}
    for fid in order:
        s = p.status_of(fmap[fid])
        if s["blocked_by"]:
            continue
        if not all([s["spec_ok"], s["test_spec_ok"], s["test_code_ok"], s["impl_ok"], s["test_ok"]]):
            phase = ("①spec" if not s["spec_ok"] else "②test-spec" if not s["test_spec_ok"]
                     else "③test-code" if not s["test_code_ok"] else "④impl" if not s["impl_ok"] else "⑤test")
            print(f"{fid} → 次フェーズ: {phase}")
            return
    print("全関数完了。⑥ check を実行せよ")


def cmd_next_issue(p: Project, args) -> None:
    nums = [int(m.group(1)) for f in (p.docs / "issues").glob("ISSUE-*.md")
            if (m := re.match(r"ISSUE-(\d+)", f.name))]
    print(f"ISSUE-{(max(nums) + 1 if nums else 1):03d}")


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
    s = sub.add_parser("status"); s.add_argument("func_id", nargs="?"); s.add_argument("--json", action="store_true")
    sub.add_parser("next")
    sub.add_parser("next-issue")
    s = sub.add_parser("freeze-tests"); s.add_argument("func_id")
    s = sub.add_parser("block"); s.add_argument("func_id"); s.add_argument("issue_id")
    s = sub.add_parser("unblock"); s.add_argument("func_id")
    s = sub.add_parser("phase-start"); s.add_argument("phase"); s.add_argument("func_id")
    sub.add_parser("phase-end")
    sub.add_parser("check")
    args = ap.parse_args()
    p = Project(Path(args.root).resolve())
    {"wbs": cmd_wbs, "skeletons": cmd_skeletons, "hash": cmd_hash, "verify": cmd_verify,
     "status": cmd_status, "next": cmd_next, "next-issue": cmd_next_issue,
     "freeze-tests": cmd_freeze, "block": cmd_block, "unblock": cmd_unblock,
     "phase-start": cmd_phase_start, "phase-end": cmd_phase_end, "check": cmd_check}[args.cmd](p, args)


if __name__ == "__main__":
    main()
