#!/usr/bin/env python3
"""⑦ 定量評価の機械収集。プロファイル計測＋静的解析を一括で走らせ、結果を集約する。

LLM（/legacy-7-analyze）に施策候補を提案させる前の「実測データづくり」を担う。
計測せずに「たぶん速くなる」で提案するのは⑦の禁止事項なので、
提案のベースになる数字はすべてここで決定的に採る（LLMには読ませるだけ）。

集めるもの:
  - cProfile      profile_run.py に委譲（bench.py があれば本命計測、無ければテスト負荷のスモーク）
  - radon cc/mi   循環的複雑度（C以上）・保守性指標（B以下）
  - ruff          リント検出（ルール別集計）
  - bandit        セキュリティ静的解析（severity別集計）
  - pip-audit     依存パッケージの既知脆弱性

出力:
  .legacy-reverse/quant.json  機械可読の集約（LLMと施策のbefore/after比較が読む）
  docs/quant.md               人が読むサマリページ（サイトに載る）

未導入のツールはエラーにせず「未実施（未導入）」として記録する（部分的な
データでも分析の足しになる。何が欠けたかは quant.md に明記される）。

使い方:
  python quant_analyze.py --root .
"""
import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def run_tool(cmd: list, cwd: Path, timeout: int = 180) -> dict:
    """1ツールを実行して {ok, stdout, stderr, exit_code} を返す。未導入・失敗も値として返す。"""
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except FileNotFoundError as e:
        return {"ok": False, "missing": True, "error": str(e)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout {timeout}s"}
    out = {"ok": r.returncode == 0, "exit_code": r.returncode,
           "stdout": r.stdout or "", "stderr": (r.stderr or "")[-500:]}
    # 「モジュールが無い」は exit≠0 ＋ No module named で来る（python -m 起動のため）
    if r.returncode != 0 and "No module named" in (r.stderr or ""):
        out["missing"] = True
    return out


def parse_json(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return None


def collect(root: Path) -> dict:
    """全ツールを実行して集約 dict を返す。"""
    q: dict = {"date": datetime.datetime.now().isoformat(timespec="minutes"),
               "missing_tools": [], "errors": []}

    # --- cProfile（profile_run.py に委譲。bench.py があれば本命） ---
    bench = root / "bench.py"
    cmd = [sys.executable, str(SCRIPTS / "profile_run.py"), "--root", str(root)]
    if bench.exists():
        cmd += ["--script", "bench.py"]
    q["workload"] = "bench.py（代表ワークロード）" if bench.exists() else "pytest tests（スモーク）"
    q["workload_is_smoke"] = not bench.exists()
    r = run_tool(cmd, root, timeout=600)
    if not r["ok"]:
        # 計測は⑦の土台なのでハード失敗（テストfail中の計測拒否などもここに出る）
        raise SystemExit(f"error: プロファイル計測に失敗: "
                         f"{(r.get('stderr') or r.get('error') or r.get('stdout') or '').strip()[-300:]}")
    perf = parse_json((root / ".legacy-reverse" / "perf.json").read_text(encoding="utf-8"))
    q["perf"] = perf

    # --- radon cc（循環的複雑度。C以上を列挙） ---
    r = run_tool([sys.executable, "-m", "radon", "cc", "src", "-j"], root)
    if r.get("missing"):
        q["missing_tools"].append("radon")
    elif (data := parse_json(r.get("stdout", ""))) is not None:
        bad = []
        for f, items in data.items():
            for it in items or []:
                if it.get("rank", "A") >= "C":
                    bad.append({"file": f.replace("\\", "/"), "name": it.get("name"),
                                "line": it.get("lineno"), "rank": it["rank"],
                                "complexity": it.get("complexity")})
        bad.sort(key=lambda x: -(x["complexity"] or 0))
        q["radon_cc"] = bad
    else:
        q["errors"].append(f"radon cc: {r.get('stderr') or r.get('error')}")

    # --- radon mi（保守性指標。B以下を列挙） ---
    if "radon" not in q["missing_tools"]:
        r = run_tool([sys.executable, "-m", "radon", "mi", "src", "-j"], root)
        if (data := parse_json(r.get("stdout", ""))) is not None:
            q["radon_mi"] = sorted(
                [{"file": f.replace("\\", "/"), "mi": round(v.get("mi", 0), 1),
                  "rank": v.get("rank")}
                 for f, v in data.items() if isinstance(v, dict) and v.get("rank", "A") >= "B"],
                key=lambda x: x["mi"])
        else:
            q["errors"].append(f"radon mi: {r.get('stderr') or r.get('error')}")

    # --- ruff（ルール別に集計） ---
    r = run_tool([sys.executable, "-m", "ruff", "check", "src", "--output-format", "json",
                  "--exit-zero"], root)
    if r.get("missing"):
        q["missing_tools"].append("ruff")
    elif (data := parse_json(r.get("stdout", ""))) is not None:
        by_rule: dict = {}
        for d in data:
            key = f"{d.get('code')}: {d.get('message', '')[:60]}"
            by_rule[key] = by_rule.get(key, 0) + 1
        q["ruff"] = {"total": len(data),
                     "by_rule": dict(sorted(by_rule.items(), key=lambda kv: -kv[1])[:20])}
    else:
        q["errors"].append(f"ruff: {r.get('stderr') or r.get('error')}")

    # --- bandit（severity別に集計＋検出一覧） ---
    r = run_tool([sys.executable, "-m", "bandit", "-r", "src", "-f", "json", "-q"], root)
    if r.get("missing"):
        q["missing_tools"].append("bandit")
    elif (data := parse_json(r.get("stdout", ""))) is not None:
        results = data.get("results", [])
        sev: dict = {}
        for it in results:
            sev[it.get("issue_severity", "?")] = sev.get(it.get("issue_severity", "?"), 0) + 1
        q["bandit"] = {"total": len(results), "by_severity": sev,
                       "top": [{"file": it.get("filename", "").replace("\\", "/"),
                                "line": it.get("line_number"),
                                "severity": it.get("issue_severity"),
                                "test": it.get("test_id"),
                                "text": (it.get("issue_text") or "")[:100]}
                               for it in results[:20]]}
    else:
        q["errors"].append(f"bandit: {r.get('stderr') or r.get('error')}")

    # --- pip-audit（依存の既知脆弱性。ネットワーク必要なので失敗しても続行） ---
    r = run_tool([sys.executable, "-m", "pip_audit", "-f", "json"], root, timeout=300)
    if r.get("missing"):
        q["missing_tools"].append("pip-audit")
    elif (data := parse_json(r.get("stdout", ""))) is not None:
        deps = data.get("dependencies", data) if isinstance(data, dict) else data
        vulns = [{"name": d.get("name"), "version": d.get("version"),
                  "vulns": [v.get("id") for v in d.get("vulns", [])]}
                 for d in (deps or []) if d.get("vulns")]
        q["pip_audit"] = {"vulnerable": vulns}
    else:
        q["errors"].append(f"pip-audit: {r.get('stderr') or r.get('error')}（オフラインの可能性）")

    return q


def render_md(q: dict) -> str:
    """docs/quant.md（人が読むサマリ。サイトに載る）を組み立てる。"""
    L = ["---", 'title: "⑦ 定量評価（自動収集）"', "---", "",
         "<!-- quant_analyze.py による自動生成。手編集禁止。提案は analysis.md 側に書く -->", "",
         f"- 収集日時: {q['date']} / ワークロード: {q['workload']}"]
    if q.get("workload_is_smoke"):
        L.append("- ⚠ **スモーク計測**（テスト負荷）。ルートに `bench.py`（代表ワークロード）を"
                 "置いて再実行すると本命の計測になる")
    L += ["- 詳細プロファイル: [perf.md](perf.md)", ""]

    perf = q.get("perf") or {}
    rows = (perf.get("rows") or [])[:8]
    total = perf.get("total_tt") or 0
    L += ["# ホットスポット（tottime 上位）", ""]
    if rows:
        L += ["| 関数 | 場所 | tottime(s) | 全体比 |", "|---|---|--:|--:|"]
        for r in sorted(rows, key=lambda x: -x["tottime"])[:8]:
            pct = f"{100 * r['tottime'] / total:.1f}%" if total else "-"
            L.append(f"| {r['func']} | {r['file']} | {r['tottime']} | {pct} |")
    else:
        L.append("src/ 配下で計測に掛かった関数なし。")

    L += ["", "# 複雑度（radon cc: C以上）", ""]
    cc = q.get("radon_cc")
    if cc is None:
        L.append("未実施（radon 未導入）。`python -m pip install radon`")
    elif not cc:
        L.append("C以上の関数なし ✅")
    else:
        L += ["| 関数 | 場所 | ランク | CC |", "|---|---|:-:|--:|"]
        L += [f"| {c['name']} | {c['file']}:{c['line']} | {c['rank']} | {c['complexity']} |"
              for c in cc[:15]]

    L += ["", "# 保守性指標（radon mi: B以下）", ""]
    mi = q.get("radon_mi")
    if mi is None:
        L.append("未実施（radon 未導入）。")
    elif not mi:
        L.append("B以下のモジュールなし ✅")
    else:
        L += ["| モジュール | MI | ランク |", "|---|--:|:-:|"]
        L += [f"| {m['file']} | {m['mi']} | {m['rank']} |" for m in mi]

    L += ["", "# ruff（リント）", ""]
    ruff = q.get("ruff")
    if ruff is None:
        L.append("未実施（ruff 未導入）。`python -m pip install ruff`")
    elif not ruff["total"]:
        L.append("検出なし ✅")
    else:
        L.append(f"検出 {ruff['total']} 件。上位ルール:")
        L += ["", "| ルール | 件数 |", "|---|--:|"]
        L += [f"| {k} | {v} |" for k, v in ruff["by_rule"].items()]

    L += ["", "# bandit（セキュリティ静的解析）", ""]
    b = q.get("bandit")
    if b is None:
        L.append("未実施（bandit 未導入）。`python -m pip install bandit`")
    elif not b["total"]:
        L.append("検出なし ✅")
    else:
        L.append(f"検出 {b['total']} 件（{', '.join(f'{k}: {v}' for k, v in b['by_severity'].items())}）")
        L += ["", "| 場所 | severity | ルール | 内容 |", "|---|:-:|---|---|"]
        L += [f"| {t['file']}:{t['line']} | {t['severity']} | {t['test']} | {t['text']} |"
              for t in b["top"]]

    L += ["", "# pip-audit（依存の既知脆弱性）", ""]
    pa = q.get("pip_audit")
    if pa is None:
        L.append("未実施（pip-audit 未導入、またはオフライン）。`python -m pip install pip-audit`")
    elif not pa["vulnerable"]:
        L.append("既知の脆弱性なし ✅")
    else:
        L += ["| パッケージ | バージョン | 脆弱性 |", "|---|---|---|"]
        L += [f"| {v['name']} | {v['version']} | {', '.join(v['vulns'])} |"
              for v in pa["vulnerable"]]

    if q.get("missing_tools") or q.get("errors"):
        L += ["", "# 収集できなかったもの", ""]
        L += [f"- 未導入: {t}" for t in q.get("missing_tools", [])]
        L += [f"- エラー: {e}" for e in q.get("errors", [])]
    return "\n".join(L) + "\n"


def main() -> None:
    import serve_site
    serve_site.use_utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    if not (root / "src").is_dir():
        sys.exit(f"error: {root / 'src'} がない（④の実装がまだ無いプロジェクトでは⑦は実行できない）")

    q = collect(root)
    out = root / ".legacy-reverse" / "quant.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")
    (root / "docs" / "quant.md").write_text(render_md(q), encoding="utf-8", newline="\n")
    print(f"wrote {root / 'docs' / 'quant.md'} / {out}")
    if q["missing_tools"]:
        print(f"note: 未導入ツール: {', '.join(q['missing_tools'])}"
              "（pip install radon ruff bandit pip-audit で全部入る）")


if __name__ == "__main__":
    sys.path.insert(0, str(SCRIPTS))
    main()
