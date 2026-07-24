#!/usr/bin/env python3
"""⑦ 性能計測。ワークロードを cProfile で実行し、src/ 配下の関数別に集計する。

使い方:
  python profile_run.py --root .                     # 既定: pytest tests を負荷に使う（スモーク計測）
  python profile_run.py --root . --script bench.py   # 代表ワークロードスクリプトで計測（本命）

出力:
  docs/perf.md               人が読む計測レポート（cumtime降順）
  .legacy-reverse/perf.json  機械可読（施策の before/after 比較用）

注意: 単体テストは入力が小さく呼び出し回数も少ないため、あくまでスモーク。
ホットスポット判断は代表ワークロード（実データ規模）で行うこと。
"""
import argparse
import cProfile
import datetime
import json
import pstats
import runpy
import sys
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--script", default=None, help="代表ワークロードの .py（無ければ pytest tests）")
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args()
    root = Path(args.root).resolve()
    src_prefix = str(root / "src")
    sys.path.insert(0, src_prefix)

    prof = cProfile.Profile()
    label = args.script or "pytest tests"
    if args.script:
        prof.enable()
        runpy.run_path(str(root / args.script), run_name="__main__")
        prof.disable()
    else:
        import pytest
        prof.enable()
        rc = pytest.main(["-q", str(root / "tests")])
        prof.disable()
        if rc != 0:
            sys.exit(f"error: テストがfailしている状態で計測しない（pytest exit={rc}）")

    stats = pstats.Stats(prof)
    total = stats.total_tt
    rows = []
    for (fn, line, name), (cc, nc, tt, ct, _callers) in stats.stats.items():
        if str(fn).startswith(src_prefix):
            rel = str(Path(fn).relative_to(root)).replace("\\", "/")
            rows.append({"func": name, "file": f"{rel}:{line}", "ncalls": nc,
                         "tottime": round(tt, 4), "cumtime": round(ct, 4)})
    rows.sort(key=lambda r: -r["cumtime"])
    rows = rows[:args.top]

    now = datetime.datetime.now().isoformat(timespec="minutes")
    out_json = root / ".legacy-reverse" / "perf.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps({"date": now, "workload": label, "total_tt": round(total, 4),
                                    "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = [
        "---", 'title: "⑦ 性能計測レポート"', "---", "",
        "<!-- profile_run.py による自動生成。手編集禁止 -->", "",
        f"- 計測日時: {now} / ワークロード: `{label}` / 全体実行時間: {total:.3f}s",
        "- 単体テスト負荷はスモーク計測。施策判断は代表ワークロードで行うこと", "",
        "# 関数別プロファイル（cumtime 降順）", "",
        "| 関数 | 場所 | 呼出回数 | tottime(s) | cumtime(s) | 全体比 |",
        "|------|------|-------:|--------:|--------:|-----:|",
    ]
    for r in rows:
        pct = f"{100 * r['tottime'] / total:.1f}%" if total > 0 else "-"
        lines.append(f"| {r['func']} | {r['file']} | {r['ncalls']} "
                     f"| {r['tottime']} | {r['cumtime']} | {pct} |")
    perf_md = root / "docs" / "perf.md"
    perf_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {perf_md} / {out_json}")


if __name__ == "__main__":
    main()
