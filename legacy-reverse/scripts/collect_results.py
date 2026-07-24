#!/usr/bin/env python3
"""⑤ テスト結果報告書の自動生成。

②のケース一覧と .legacy-reverse/last-run.json（tc_report_plugin の出力）を突合し、
docs/test-results/<func-id>_<YYYYMMDD-HHMM>.md を1から生成する。

  - 実装率 = ②のケースIDのうちマーカーが存在するものの割合
  - result: pass は「実装率100% かつ 失敗0」のときだけ
  - attempt = ledger の attempt_reset_at 以降の結果ファイル数 + 1
  - attempt >= 3 かつ fail → triage ISSUE の骨子を生成し、ledger を block（人の裁定待ち）
  - ②に無いIDのマーカー / マーカー無しテスト → エラー終了（野良テスト・幽霊ケースの防止）

使い方: python collect_results.py <func-id> [--root .] [--max-attempt 3]
exit code: 0=pass / 1=fail / 2=blocked(要裁定) / 3=突合エラー
"""
import argparse
import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ledger import Project, parse_frontmatter, save_json  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("func_id")
    ap.add_argument("--root", default=".")
    ap.add_argument("--max-attempt", type=int, default=3)
    args = ap.parse_args()
    p = Project(Path(args.root).resolve())
    f = p.func(args.func_id)
    fid = args.func_id
    now = datetime.datetime.now()

    # --- ②のケース一覧 ---
    ts_path = p.docs / "test-specs" / f"{fid}.md"
    if not ts_path.exists():
        sys.exit(f"error: ②が存在しない: {ts_path}")
    ts_text = ts_path.read_text(encoding="utf-8-sig")
    spec_cases = re.findall(r"^##\s+(\S+-TC-\d+)", ts_text, flags=re.M)
    if not spec_cases:
        sys.exit("error: ②からケースIDを抽出できない（## <ID>: 見出し形式か確認）")

    # --- 実行結果 ---
    run_path = p.state_dir / "last-run.json"
    if not run_path.exists():
        sys.exit("error: last-run.json がない。pytest を -p tc_report_plugin 付きで実行せよ")
    runs = json.loads(run_path.read_text(encoding="utf-8-sig"))

    # --- 突合 ---
    errors = [f"マーカー無しのテスト: {r['nodeid']}" for r in runs if not r["tc"]]
    by_tc = {}
    for r in runs:
        if r["tc"]:
            if r["tc"] not in spec_cases:
                errors.append(f"②に存在しないID: {r['tc']} ({r['nodeid']})")
            by_tc.setdefault(r["tc"], r)  # 同一IDが複数あれば先勝ち＋エラー
    dup = [tc for tc in by_tc if sum(1 for r in runs if r["tc"] == tc) > 1]
    errors += [f"同一IDのテストが複数: {tc}" for tc in dup]
    if errors:
        print("\n".join(errors))
        sys.exit(3)

    implemented = [tc for tc in spec_cases if tc in by_tc]
    failed = [tc for tc in implemented if by_tc[tc]["outcome"] == "failed"]
    skipped = [tc for tc in implemented if by_tc[tc]["outcome"] == "skipped"]
    rate = f"{round(100 * len(implemented) / len(spec_cases))}%"
    result = "pass" if len(implemented) == len(spec_cases) and not failed else "fail"

    # --- attempt ---
    led = p.ledger.setdefault(fid, {})
    reset = led.get("attempt_reset_at", "")
    prev = sorted((p.docs / "test-results").glob(f"{fid}_*.md"))
    if reset:
        # "2026-07-25T10:00:30" → "20260725-100030"（ファイル名スタンプと同形式・秒精度）
        stamp = reset.replace("-", "").replace(":", "").replace("T", "-")
        prev = [x for x in prev if x.stem.split("_", 1)[1] > stamp]
    attempt = len(prev) + 1

    blocked_by = None
    if result == "fail" and attempt >= args.max_attempt:
        # triage ISSUE 骨子を生成して block
        nums = [int(m.group(1)) for x in (p.docs / "issues").glob("ISSUE-*.md")
                if (m := re.match(r"ISSUE-(\d+)", x.name))]
        blocked_by = f"ISSUE-{(max(nums) + 1 if nums else 1):03d}"
        issue = [
            "---",
            f'title: "④⑤ループ上限到達: {fid} の裁定"',
            f'issue-id: "{blocked_by}"', "status: open", "kind: triage",
            f'func-id: "{fid}"', 'raised-in: "phase-5"',
            f'raised-date: "{now.date()}"', "---", "",
            "# 事象", "",
            f"{fid} が {attempt} 回連続で fail。失敗ケース: {', '.join(failed) or '(実装率不足)'}", "",
            "# 質問（人への問い）", "",
            "**仮説**: <!-- ⑤skill が失敗分析を記入する -->", "",
            "**問い**: トリアージ分類 (a)実装続行 / (b)テストコード修正 / (c)仕様修正 のどれで進めますか？", "",
            "# 回答（人が記入）", "", "- 回答:", "- 回答者 / 日付:", "",
            "# 反映", "", "| 反映先 | 内容 |", "|--------|------|", "| | |", ""]
        ip = p.docs / "issues" / f"{blocked_by}.md"
        ip.parent.mkdir(parents=True, exist_ok=True)
        ip.write_text("\n".join(issue), encoding="utf-8")
        led["blocked_by"] = blocked_by
        save_json(p.ledger_path, p.ledger)

    # --- 試行履歴（過去ファイル走査） ---
    history = []
    for x in sorted((p.docs / "test-results").glob(f"{fid}_*.md")):
        fm = parse_frontmatter(x.read_text(encoding="utf-8-sig"))
        history.append((fm.get("attempt", "?"), fm.get("run", {}).get("date", x.stem),
                        fm.get("tc-coverage", {}).get("rate", "?"), fm.get("result", "?")))

    # --- 本文生成 ---
    name = f["new"].get("name", fid)
    tf = f.get("test_file", "")
    frozen = led.get("test_code_hash", "")
    dur = sum(r["duration"] for r in by_tc.values())
    lines = [
        "---", f'title: "テスト結果: {name}"', f'func-id: "{fid}"',
        f'test-spec-ref: "test-specs/{fid}.md"', f'test-code-hash: "{frozen}"',
        "run:", f'  date: "{now.isoformat(timespec="minutes")}"', f'  env: "python {sys.version.split()[0]}"',
        f"result: {result}", f"attempt: {attempt}",
        f"blocked-by: {blocked_by or 'null'}",
        "tc-coverage:", f"  spec-cases: {len(spec_cases)}",
        f"  implemented: {len(implemented)}", f'  rate: "{rate}"',
        "---", "", "<!-- collect_results.py による自動生成。手編集禁止 -->", "",
        "# サマリ", "",
        "| 仕様ケース数 | 実装済み | **実装率** | 成功 | 失敗 | スキップ | 実行時間 |",
        "|:---:|:---:|:---:|:---:|:---:|:---:|:---:|",
        f"| {len(spec_cases)} | {len(implemented)} | **{rate}** "
        f"| {len(implemented) - len(failed) - len(skipped)} | {len(failed)} | {len(skipped)} | {dur:.2f}s |",
        "", "# ケース別結果", "",
        "| ケースID | 実装 | 結果 | 時間 | 詳細 |", "|---------|:---:|:---:|-----:|------|"]
    for tc in spec_cases:
        if tc not in by_tc:
            lines.append(f"| {tc} | － | 未実装 | — | |")
        else:
            r = by_tc[tc]
            sym = {"passed": "✅", "failed": "❌", "skipped": "⏭"}[r["outcome"]]
            anchor = f"[詳細](#fail-{tc.lower()})" if r["outcome"] == "failed" else ""
            lines.append(f"| {tc} | ✅ | {sym} | {r['duration']}s | {anchor} |")
    lines += ["", "# 失敗詳細", ""]
    if failed:
        for tc in failed:
            r = by_tc[tc]
            lines += [f"## {tc} {{#fail-{tc.lower()}}}", "", "```", r["longrepr"], "```", "",
                      "- トリアージ分類（暫定）: <!-- ⑤skill が (a)/(b)/(c) と根拠を記入 -->", ""]
    else:
        lines.append("なし")
    lines += ["", "# 試行履歴", "",
              "| 試行 | 日時 | 実装率 | 結果 |", "|:---:|------|:---:|:---:|"]
    lines += [f"| {a} | {d} | {r} | {res} |" for a, d, r, res in history]
    lines += [f"| {attempt} | {now.isoformat(timespec='minutes')} | {rate} | {result} |", ""]

    out = p.docs / "test-results" / f"{fid}_{now.strftime('%Y%m%d-%H%M%S')}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}  result={result} rate={rate} attempt={attempt}"
          + (f"  ⛔ blocked by {blocked_by}（人の裁定待ち）" if blocked_by else ""))
    sys.exit(2 if blocked_by else (0 if result == "pass" else 1))


if __name__ == "__main__":
    main()
