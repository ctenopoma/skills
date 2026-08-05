#!/usr/bin/env python3
"""pipeline.py — ①仕様書の無人バッチ実行ドライバ（2000関数規模・トークン制約なし）。

エージェントの会話内ループと違い、**1関数 = 1つの新しい headless Claude プロセス**
（`claude -p "/legacy-1-spec F-xxxx"`）で回すため、コンテキストが積み上がらない。
1関数あたりのトークンは常に一定で、コンパクションも指示劣化も起きない。
ドライバ自身は Python なのでトークンを消費しない。

各関数の実行後は LLM の申告を信用せず、ファイル状態で契約検証する:
  - docs/specs/<fid>.md の status が draft（または reviewed）になったか
  - review_checks.check_spec が NG ゼロか
検証 NG はリトライし、それでも駄目なら記録してスキップ（このセッション中は再選定しない）。
連続失敗が閾値を超えたら環境異常とみなして停止する。

タイムアウト・レートリミット耐性:
  - 1関数ごとに --timeout（既定30分）で子プロセスを打ち切る（ハング対策）。
    タイムアウトは通常のリトライ経路に乗る
  - レートリミット/過負荷/利用枠上限（429・529・usage limit 等の応答）は「失敗」に
    数えず、指数バックオフ（--backoff-base 60s → 2倍ずつ → --backoff-max 15分）で
    待って同じ関数をやり直す。利用枠の時間リセットを人手なしで跨げる。
    待機の累計が --rate-wait-total（既定6時間）を超えたら安全に停止する
  - 予防的に間隔を空けたい環境では --pause N 秒

進捗の正は従来どおりファイル側にあるので、Ctrl-C・電源断のどこで止めても
同じコマンドで続きから再開できる（処理済み draft は --skip-draft 相当で除外、
書きかけ draft は機械レビュー NG として検出し先に修復される）。

使い方（対象プロジェクトのルートで）:
  python <LR>/scripts/pipeline.py spec --root . --max-funcs 200
  python <LR>/scripts/pipeline.py spec --root . --dry-run          # 対象と手順の確認のみ

前提:
  - 対象プロジェクトに legacy-reverse の skill 一式が配置済み（.claude/skills/）
  - headless では許可プロンプトに答えられないため、必要ツールを
    .claude/settings.json で allow 済みにするか --skip-permissions を明示する

実行ログ:
  .legacy-reverse/pipeline-log.jsonl   1行1試行（結果・所要・コスト。失敗時は応答末尾も）
  .legacy-reverse/agent-logs/<fid>.txt 各関数のエージェント応答**全文**（失敗原因の一次情報）
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ledger import Project, actionable, parse_frontmatter  # noqa: E402
import review_checks  # noqa: E402


def find_claude(explicit: str = None) -> list:
    """claude の起動コマンド（リスト形式）を解決する。

    PowerShell では動くのに Python の subprocess からは動かない事故が多い
    （PowerShell プロファイルのエイリアス/PATH追記は subprocess に届かない、
    実体が .ps1 で CreateProcess から直接起動できない等）。
    ここで実体を探し、.ps1 は powershell 経由に包む。
    """
    cands = []
    if explicit:
        cands.append(explicit)
    else:
        for name in ("claude", "claude.exe", "claude.cmd", "claude.ps1"):
            w = shutil.which(name)
            if w:
                cands.append(w)
        home = Path.home()
        for p in (home / ".local" / "bin" / "claude.exe",
                  home / ".local" / "bin" / "claude",
                  Path(os.environ.get("APPDATA", "/nonexistent")) / "npm" / "claude.cmd",
                  Path(os.environ.get("APPDATA", "/nonexistent")) / "npm" / "claude.ps1",
                  Path(os.environ.get("LOCALAPPDATA", "/nonexistent"))
                  / "Programs" / "claude" / "claude.exe"):
            if p.exists():
                cands.append(str(p))
    for cand in cands:
        if cand.lower().endswith(".ps1"):
            return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", cand]
        return [cand]
    sys.exit(
        "error: claude CLI が見つからない。\n"
        "  PowerShell で動くのにここで失敗する場合、claude が PowerShell の\n"
        "  プロファイル（エイリアス/PATH追記）でしか解決できていない可能性が高い。\n"
        "  PowerShell で実体パスを調べて --claude-cmd に渡す:\n"
        "    (Get-Command claude).Source\n"
        "    python pipeline.py spec --claude-cmd \"<そのパス>\" ...")


def preflight_claude(claude_cmd: list, timeout: int = 120) -> None:
    """ループに入る前に claude が本当に起動できるか実測する。

    ここで確認しないと、起動不能でも全関数が「検証NG」という遠い症状で
    失敗し続け、原因（PATH・許可・実体の種類）が見えない。
    """
    try:
        r = subprocess.run(claude_cmd + ["--version"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        sys.exit(f"error: claude を起動できない（{' '.join(claude_cmd)}）: {e}\n"
                 "  PowerShell で (Get-Command claude).Source を調べて --claude-cmd に渡す")
    if r.returncode != 0:
        sys.exit(f"error: claude --version が失敗（exit={r.returncode}）\n"
                 f"  {(r.stderr or r.stdout).strip()[:300]}\n"
                 "  実体パスを --claude-cmd で明示するか、PATH を確認する")
    print(f"claude: {' '.join(claude_cmd)}（{(r.stdout or '').strip().splitlines()[0] if r.stdout.strip() else 'version不明'}）")


# レートリミット・過負荷・利用枠上限の検知（これらは「失敗」でなく「待って再試行」）
RATE_LIMIT_PAT = re.compile(
    r"rate.?limit|too many requests|\b429\b|overloaded|\b529\b|usage limit"
    r"|quota|capacity|resets at|out of (credits|tokens)|limit reached", re.I)


def run_claude(claude_cmd: list, prompt: str, root: Path, max_turns: int,
               extra: list, timeout: int) -> dict:
    """headless Claude を1プロセス起動し、JSON 結果を返す。"""
    cmd = claude_cmd + ["-p", prompt, "--output-format", "json",
                        "--max-turns", str(max_turns)] + extra
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        r = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired as e:
        return {"ok": False, "timeout": True, "cost_usd": 0.0,
                "tail": f"timeout {timeout}s", "err": "",
                "stdout": str(e.stdout or ""), "stderr": str(e.stderr or "")}
    out = {}
    for line in reversed(r.stdout.strip().splitlines() or [""]):
        try:
            out = json.loads(line)
            break
        except ValueError:
            continue
    res = {"ok": r.returncode == 0 and not out.get("is_error", False),
           "exit_code": r.returncode,
           "cost_usd": out.get("total_cost_usd") or out.get("cost_usd") or 0.0,
           "num_turns": out.get("num_turns"),
           "duration_ms": out.get("duration_ms"),
           "tail": (out.get("result") or r.stdout or "")[-500:],
           "err": (r.stderr or "")[-500:],
           "stdout": r.stdout or "", "stderr": r.stderr or ""}
    res["rate_limited"] = bool(not res["ok"]
                               and RATE_LIMIT_PAT.search(res["tail"] + " " + res["err"]))
    return res


def verify_spec(root: str, fid: str) -> tuple:
    """契約検証: ①が draft 以上になり、機械レビュー NG ゼロか。"""
    p = Path(root) / "docs" / "specs" / f"{fid}.md"
    if not p.exists():
        return False, "仕様書ファイルが存在しない"
    status = parse_frontmatter(p.read_text(encoding="utf-8-sig")).get("status")
    if status not in ("draft", "reviewed"):
        return False, f"status が {status} のまま（draft になっていない）"
    r = review_checks.check_spec(root, fid)
    if not r["ok"]:
        return False, "機械レビューNG: " + " / ".join(r["problems"][:3])
    return True, ""


def refresh_outputs(root: Path) -> dict:
    """WBS と一斉レビュー表を再生成する（人が途中経過を常に見られる状態を保つ）。"""
    scripts = Path(__file__).resolve().parent
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    subprocess.run([sys.executable, str(scripts / "ledger.py"), "--root", str(root), "wbs"],
                   capture_output=True, env=env)
    return review_checks.make_report(str(root))


def save_agent_log(root: Path, fid: str, attempt: int, r: dict) -> Path:
    """エージェント（headless claude）の応答全文を関数ごとのファイルに残す。

    失敗原因の一次情報はここにしか無い（why はファイル状態の検証結果でしかない）。
    """
    d = root / ".legacy-reverse" / "agent-logs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{fid}.txt"
    with p.open("a", encoding="utf-8") as f:
        f.write(f"\n===== attempt {attempt} "
                f"{datetime.datetime.now().isoformat(timespec='seconds')} "
                f"exit={r.get('exit_code')} timeout={bool(r.get('timeout'))} =====\n")
        f.write((r.get("stdout") or "(stdoutなし)") + "\n")
        if r.get("stderr"):
            f.write("--- stderr ---\n" + r["stderr"] + "\n")
    return p


def log_line(root: Path, entry: dict) -> None:
    logp = root / ".legacy-reverse" / "pipeline-log.jsonl"
    logp.parent.mkdir(parents=True, exist_ok=True)
    entry["at"] = datetime.datetime.now().isoformat(timespec="seconds")
    with logp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def cmd_spec(args) -> None:
    root = Path(args.root).resolve()
    p = Project(root)
    claude_cmd = None if args.dry_run else find_claude(args.claude_cmd)
    if claude_cmd:
        preflight_claude(claude_cmd)
    extra = []
    if args.model:
        extra += ["--model", args.model]
    if args.skip_permissions:
        extra += ["--dangerously-skip-permissions"]
    extra += args.claude_args

    done = failed = 0
    consecutive_fail = 0
    cost_total = 0.0
    rate_waited = 0
    skip: set = set()

    def targets(rep: dict = None) -> list:
        # 書きかけ（機械NGの draft）を最優先で修復し、その後に未着手を進める。
        # 状態を変えるのはこのドライバだけなので、再計算はチャンク境界のみでよい
        # （毎イテレーション make_report を回すと2000件規模で O(n²) になる）
        rep = rep or review_checks.make_report(str(args.root))
        repair = [f for f in rep["machine_ng_funcs"] if f not in skip]
        fresh = [fid for fid, _ in actionable(Project(root), phase="1", skip_wait=True)
                 if fid not in skip]
        return repair + fresh

    todo = targets()
    if not todo:
        print("①の対象なし（未着手ゼロ・書きかけゼロ）。レビュー待ちは spec-review.md を参照")
        return
    print(f"対象 {len(todo)} 件から開始"
          + (f"（このセッションは最大 {args.max_funcs} 件）" if args.max_funcs else ""))
    if args.dry_run:
        for fid in todo[:args.max_funcs or None]:
            print(f"  claude -p \"{args.prompt_template.format(fid=fid)}\" "
                  f"--max-turns {args.max_turns} {' '.join(extra)}")
        return

    try:
        while todo:
            fid = todo.pop(0)
            if args.max_funcs and done + failed >= args.max_funcs:
                print(f"セッション上限 {args.max_funcs} 件に到達。同じコマンドで再開できる")
                break
            if args.budget_usd and cost_total >= args.budget_usd:
                print(f"予算 ${args.budget_usd} に到達（累計 ${cost_total:.2f}）。停止")
                break

            prompt = args.prompt_template.format(fid=fid)
            ok, why = False, ""
            attempt, backoff = 0, args.backoff_base
            while attempt <= args.retries:
                t0 = time.time()
                r = run_claude(claude_cmd, prompt, root, args.max_turns, extra, args.timeout)
                cost_total += r.get("cost_usd") or 0.0

                # レートリミット/過負荷/利用枠上限: 失敗に数えず、待って同じ関数をやり直す
                # （利用枠は時間で回復する。夜間無人実行が人手なしで再開するための要）
                if r.get("rate_limited"):
                    if rate_waited >= args.rate_wait_total:
                        print(f"レート待機の累計が上限 {args.rate_wait_total}s に到達。"
                              f"停止する（再開は同じコマンド）")
                        raise KeyboardInterrupt
                    wait = min(backoff, args.backoff_max)
                    print(f"  {fid}: レートリミット/利用枠を検知 → {wait}s 待機"
                          f"（累計 {rate_waited}s）: {r.get('tail', '')[:80]}")
                    log_line(root, {"func_id": fid, "rate_limited": True, "wait_sec": wait,
                                    "tail": r.get("tail", "")[:200]})
                    time.sleep(wait)
                    rate_waited += wait
                    backoff *= 2
                    continue                      # attempt は消費しない

                attempt += 1
                backoff = args.backoff_base       # 正常応答が返ったらバックオフをリセット
                save_agent_log(root, fid, attempt, r)
                ok, why = verify_spec(str(args.root), fid)
                if r.get("timeout") and not ok:
                    why = f"タイムアウト（{args.timeout}s）: " + why
                entry = {"func_id": fid, "attempt": attempt, "ok": ok,
                         "why": why, "cost_usd": r.get("cost_usd"),
                         "claude_ok": r.get("ok"), "num_turns": r.get("num_turns"),
                         "sec": round(time.time() - t0)}
                if not ok:                        # 失敗時は claude の応答末尾も台帳に残す
                    entry.update({"exit_code": r.get("exit_code"),
                                  "claude_tail": (r.get("tail") or "")[-300:],
                                  "claude_err": (r.get("err") or "")[-200:]})
                log_line(root, entry)
                if ok:
                    break
                print(f"  {fid}: 検証NG（{why}）"
                      + (f" → リトライ {attempt}/{args.retries}" if attempt <= args.retries else ""))
                if not r.get("ok"):
                    print(f"    claude: exit={r.get('exit_code')} "
                          f"{((r.get('err') or r.get('tail') or '').strip())[:160]}")

            if ok:
                done += 1
                consecutive_fail = 0
                print(f"[{done}] {fid} draft化 OK（累計 ${cost_total:.2f}）")
            else:
                failed += 1
                consecutive_fail += 1
                skip.add(fid)
                print(f"  {fid}: 失敗として記録しスキップ（{why}）")
                if consecutive_fail >= args.max_consecutive_fail:
                    print(f"連続 {consecutive_fail} 件失敗 → 環境異常の疑い（許可設定・"
                          f"skill配置・claude CLI を確認）。停止する")
                    break

            if args.pause and todo:
                time.sleep(args.pause)       # 予防的ペーシング（レートリミット回避）
            if (done + failed) % args.chunk == 0:
                rep = refresh_outputs(root)
                todo = targets(rep)          # チャンク境界でのみ再計算（性能・自己修復の両立）
                print(f"  -- WBS・一斉レビュー表を更新（済 {done} / 失敗 {failed}）")
    except KeyboardInterrupt:
        print("\n中断した。進捗はファイルに保存済み。同じコマンドで続きから再開できる")

    refresh_outputs(root)
    if not args.no_render:
        subprocess.run([sys.executable,
                        str(Path(__file__).resolve().parent / "render_site.py"),
                        "--root", str(root)], capture_output=True)
    print(f"\n完了: draft化 {done} 件 / 失敗 {failed} 件 / 累計コスト ${cost_total:.2f}")
    if failed:
        print(f"失敗した関数: {sorted(skip)}")
        print("  原因調査: .legacy-reverse/agent-logs/<fid>.txt にエージェント応答の全文、"
              ".legacy-reverse/pipeline-log.jsonl に検証結果と応答末尾がある")
    rep = review_checks.make_report(str(args.root))
    print(f"レビュー待ち {rep['drafts']} 件 → docs/spec-review.md を人がレビューし、"
          f"OK分を reviewed 化してください")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("spec", help="①仕様書を全件 draft まで無人実行（→ 人が一斉レビュー）")
    s.add_argument("--root", default=".")
    s.add_argument("--chunk", type=int, default=10, help="WBS/レビュー表を更新する間隔（既定10件）")
    s.add_argument("--max-funcs", type=int, default=0, help="このセッションで処理する上限（0=無制限）")
    s.add_argument("--budget-usd", type=float, default=0, help="累計コスト上限 USD（0=無制限）")
    s.add_argument("--retries", type=int, default=1, help="検証NG時のリトライ回数（既定1）")
    s.add_argument("--max-consecutive-fail", type=int, default=3,
                   help="連続失敗でドライバを停止する閾値（既定3）")
    s.add_argument("--max-turns", type=int, default=50, help="1関数あたりのターン上限")
    s.add_argument("--timeout", type=int, default=1800, help="1関数あたりの秒数上限（既定30分）")
    s.add_argument("--backoff-base", type=int, default=60,
                   help="レートリミット検知時の初期待機秒（以後2倍ずつ。既定60）")
    s.add_argument("--backoff-max", type=int, default=900,
                   help="1回の待機の上限秒（既定900=15分）")
    s.add_argument("--rate-wait-total", type=int, default=21600,
                   help="レート待機の累計上限秒。超えたら停止（既定21600=6時間）")
    s.add_argument("--pause", type=float, default=0,
                   help="関数間の予防的な待機秒（レートリミットに当たりやすい環境用）")
    s.add_argument("--model", default=None, help="claude に渡すモデル指定")
    s.add_argument("--skip-permissions", action="store_true",
                   help="--dangerously-skip-permissions を claude に渡す（信頼できる環境のみ）")
    s.add_argument("--claude-cmd", default=None, help="claude 実行ファイルのパス（既定: PATH から検索）")
    s.add_argument("--claude-args", nargs="*", default=[], help="claude へ追加で渡す引数")
    s.add_argument("--prompt-template", default="/legacy-1-spec {fid}",
                   help="1関数あたりのプロンプト（{fid} が関数IDに置換される）")
    s.add_argument("--dry-run", action="store_true", help="実行せず対象とコマンドを表示")
    s.add_argument("--no-render", action="store_true", help="終了時の HTML 再生成を省略")
    args = ap.parse_args()
    if args.cmd == "spec":
        cmd_spec(args)


if __name__ == "__main__":
    main()
