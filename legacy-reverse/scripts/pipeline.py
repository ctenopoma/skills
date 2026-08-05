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
  .legacy-reverse/pipeline-status.json ライブ進捗（serve_site.py の /pipeline.html が表示）
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
import check_stubs  # noqa: E402
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
    """契約検証: ①が draft 以上になり、機械レビュー NG ゼロか。

    戻り値は (ok, why, problems)。problems は機械レビューの理由の全文リスト
    （NGの原因がステータス未達・ファイル欠落など機械レビュー以外の場合は空）。
    why は一覧・ログ用の1行要約、problems は /pipeline.html 等で箇条書き表示する用。
    """
    p = Path(root) / "docs" / "specs" / f"{fid}.md"
    if not p.exists():
        return False, "仕様書ファイルが存在しない", []
    status = parse_frontmatter(p.read_text(encoding="utf-8-sig")).get("status")
    if status not in ("draft", "reviewed"):
        return False, f"status が {status} のまま（draft になっていない）", []
    r = review_checks.check_spec(root, fid)
    if not r["ok"]:
        return False, f"機械レビューNG（{len(r['problems'])}件）", r["problems"]
    return True, "", []


def verify_testspec(root: str, fid: str) -> tuple:
    """契約検証: ②が generated 以上になり、機械レビュー NG ゼロか。戻り値は verify_spec と同形。"""
    p = Path(root) / "docs" / "test-specs" / f"{fid}.md"
    if not p.exists():
        return False, "テスト仕様書ファイルが存在しない", []
    status = parse_frontmatter(p.read_text(encoding="utf-8-sig")).get("status")
    if status not in ("generated", "approved"):
        return False, f"status が {status} のまま（generated になっていない）", []
    r = review_checks.check_testspec(root, fid)
    if not r["ok"]:
        return False, f"機械レビューNG（{len(r['problems'])}件）", r["problems"]
    return True, "", []


def verify_testcode(root: str, fid: str) -> tuple:
    """契約検証: ③がfreeze済み（テストファイルが凍結時のハッシュと一致）か。

    ③自体には①②のような「引用の実在検証」に相当する静的レビューは無い
    （legacy-3-testcode/SKILL.md の設計）。ケースID とテスト関数の過不足は
    freeze 前に `pytest --collect-only` ＋ marker突合（collect_results.py）で
    人ではなく機械が検知する。ここでは「その手順を経て freeze された状態か」を見る。
    """
    rootp = Path(root).resolve()
    p = Project(rootp)
    try:
        f = p.func(fid)
    except SystemExit:
        return False, f"{fid} が functions.json に存在しない", []
    tf = f.get("test_file")
    if not tf:
        return False, "functions.json に test_file が未設定", []
    tf_p = rootp / tf
    if not tf_p.exists():
        return False, f"テストファイルが存在しない（{tf}）", []
    s = p.status_of(f)
    if s["test_code_tampered"]:
        return False, "freeze後にテストコードが改変されている（ledger freeze-tests をやり直す）", []
    if not s["test_code_ok"]:
        return False, "freeze されていない（ledger freeze-tests が未実行、または収集エラーが残っている）", []
    return True, "", []


def verify_impl(root: str, fid: str) -> tuple:
    """契約検証: ④の実装ファイルが存在し、スタブ検出ゼロか。

    problems は check_stubs.py と同じ検出（空実装・NotImplementedError・TODO/FIXME）。
    """
    rootp = Path(root).resolve()
    p = Project(rootp)
    try:
        f = p.func(fid)
    except SystemExit:
        return False, f"{fid} が functions.json に存在しない", []
    mod_p = rootp / f["new"]["module"]
    if not mod_p.exists():
        return False, "実装ファイルが存在しない", []
    problems = [f"{lineno}: {msg}" for lineno, msg in check_stubs.check_file(mod_p)]
    if problems:
        return False, f"スタブ検出（{len(problems)}件）", problems
    return True, "", []


def refresh_outputs(root: Path) -> dict:
    """WBS と一斉レビュー表を再生成する（人が途中経過を常に見られる状態を保つ）。"""
    scripts = Path(__file__).resolve().parent
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    subprocess.run([sys.executable, str(scripts / "ledger.py"), "--root", str(root), "wbs"],
                   capture_output=True, env=env)
    return review_checks.make_report(str(root))


def classify_ng(why: str, r: dict) -> str:
    """失敗理由をライブ表示・集計用に分類する。"""
    if "タイムアウト" in why:
        return "タイムアウト"
    if not r.get("ok"):
        return "claude異常終了"
    if "存在しない" in why:
        return "ファイル未作成（書き込み権限の疑い）"
    if "status が" in why:
        return "draft未到達（status未更新）"
    if "機械レビューNG" in why:
        return "機械レビューNG"
    return "その他"


class RunStatus:
    """ライブ進捗の状態ファイル（.legacy-reverse/pipeline-status.json）。

    serve_site.py の /pipeline.html がこれをポーリングして表示する。
    Quarto を通さないのでリアルタイム、書き込みはアトミック（tmp→replace）。
    バッチの正は従来どおり成果物ファイル側にあり、これは表示専用の使い捨て。
    """

    def __init__(self, root: Path, mode: str, total: int, args):
        self.path = root / ".legacy-reverse" / "pipeline-status.json"
        self.ok_secs: list = []
        self.d = {"state": "running", "mode": mode,
                  "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
                  "total_targets": total, "done": 0, "failed": 0,
                  "cost_usd": 0.0, "budget_usd": args.budget_usd or None,
                  "max_funcs": args.max_funcs or None,
                  "rate_waited_sec": 0, "wait_until": None,
                  "current": None, "ng_kinds": {}, "recent": [], "metrics": {}}
        self.save()

    def save(self) -> None:
        self.d["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.d, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def current(self, fid: str, attempt: int) -> None:
        self.d["state"] = "running"
        self.d["wait_until"] = None
        self.d["current"] = {"func_id": fid, "attempt": attempt,
                             "started_at": datetime.datetime.now().isoformat(timespec="seconds")}
        self.save()

    def waiting(self, wait_sec: int, total_waited: int, tail: str) -> None:
        self.d["state"] = "waiting_rate"
        self.d["rate_waited_sec"] = total_waited
        self.d["wait_until"] = (datetime.datetime.now()
                                + datetime.timedelta(seconds=wait_sec)).isoformat(timespec="seconds")
        self.d["last_rate_msg"] = tail[:200]
        self.save()

    def result(self, fid: str, ok: bool, why: str, kind: str, sec: int,
               cost_total: float, r: dict, attempt: int, problems: list = None) -> None:
        if ok:
            self.ok_secs.append(sec)
        else:
            self.d["ng_kinds"][kind] = self.d["ng_kinds"].get(kind, 0) + 1
        self.d["cost_usd"] = round(cost_total, 4)
        self.d["recent"].insert(0, {
            "func_id": fid, "ok": ok, "why": why, "kind": None if ok else kind,
            "problems": (problems or [])[:20],   # 機械レビューの理由の全文（表示側で箇条書きにする）
            "sec": sec, "cost_usd": r.get("cost_usd"), "attempt": attempt,
            "num_turns": r.get("num_turns"),
            "tail": None if ok else (r.get("tail") or r.get("err") or "")[-400:],
            "at": datetime.datetime.now().isoformat(timespec="seconds")})
        del self.d["recent"][50:]
        secs = sorted(self.ok_secs)
        med = secs[len(secs) // 2] if secs else None
        n_ok, n_ng = self.d["done"], self.d["failed"]
        remaining = max(self.d["total_targets"] - n_ok - n_ng, 0)
        self.d["metrics"] = {
            "median_sec": med,
            "success_rate": round(n_ok / (n_ok + n_ng), 3) if (n_ok + n_ng) else None,
            "eta_sec": med * remaining if med else None,
            "avg_cost_usd": round(cost_total / (n_ok + n_ng), 4) if (n_ok + n_ng) else None}
        self.save()

    def counts(self, done: int, failed: int, total: int = None) -> None:
        self.d["done"], self.d["failed"] = done, failed
        if total is not None:
            self.d["total_targets"] = total
        self.save()

    def finish(self, state: str, reason: str = "") -> None:
        self.d["state"] = state          # finished / stopped
        self.d["current"] = None
        self.d["reason"] = reason
        self.save()


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


def run_one(fid: str, claude_cmd: list, extra: list, root: Path,
           prompt_template: str, max_turns: int, timeout: int, retries: int,
           backoff_base: int, backoff_max: int, rate_wait_total: int,
           status: "RunStatus", cost_total: float, rate_waited: int,
           verify_fn=verify_spec) -> tuple:
    """1関数分の実行ループ（レート待機・リトライ・検証込み）。フェーズ非依存。

    cmd_spec（①無人バッチ）と browser_run.py（ブラウザからの単発実行。①②対応）が共用する。
    verify_fn(root_str, fid) -> (ok, why, problems) で「完了とみなす条件」を差し替える
    （① verify_spec / ② verify_testspec）。プロンプトテンプレートも差し替え可能なので
    ①以外のフェーズも同じループに乗る。
    戻り値: (ok, why, r, cost_total, rate_waited) — 呼び出し側の累計をこれで更新する。
    レート待機の累計上限超過は KeyboardInterrupt で呼び出し側に伝える（バッチの
    安全停止と同じ扱い。単発実行側もこれを捕まえて「停止」として扱えばよい）。
    """
    prompt = prompt_template.format(fid=fid)
    ok, why, r = False, "", {}
    attempt, backoff = 0, backoff_base
    while attempt <= retries:
        status.current(fid, attempt + 1)
        t0 = time.time()
        r = run_claude(claude_cmd, prompt, root, max_turns, extra, timeout)
        cost_total += r.get("cost_usd") or 0.0

        if r.get("rate_limited"):
            if rate_waited >= rate_wait_total:
                raise KeyboardInterrupt
            wait = min(backoff, backoff_max)
            print(f"  {fid}: レートリミット/利用枠を検知 → {wait}s 待機"
                  f"（累計 {rate_waited}s）: {r.get('tail', '')[:80]}")
            log_line(root, {"func_id": fid, "rate_limited": True, "wait_sec": wait,
                            "tail": r.get("tail", "")[:200]})
            status.waiting(wait, rate_waited + wait, r.get("tail", ""))
            time.sleep(wait)
            rate_waited += wait
            backoff *= 2
            continue                          # attempt は消費しない

        attempt += 1
        backoff = backoff_base
        save_agent_log(root, fid, attempt, r)
        # root は解決済み絶対パスなので str() でも cwd 非依存（相対の args.root より頑健）
        ok, why, problems = verify_fn(str(root), fid)
        if r.get("timeout") and not ok:
            why = f"タイムアウト（{timeout}s）: " + why
        entry = {"func_id": fid, "attempt": attempt, "ok": ok,
                 "why": why, "problems": problems, "cost_usd": r.get("cost_usd"),
                 "claude_ok": r.get("ok"), "num_turns": r.get("num_turns"),
                 "sec": round(time.time() - t0)}
        if not ok:
            entry.update({"exit_code": r.get("exit_code"),
                          "claude_tail": (r.get("tail") or "")[-300:],
                          "claude_err": (r.get("err") or "")[-200:]})
        log_line(root, entry)
        status.result(fid, ok, why, classify_ng(why, r),
                      round(time.time() - t0), cost_total, r, attempt, problems=problems)
        if ok:
            break
        print(f"  {fid}: 検証NG（{why}）"
              + (f" → リトライ {attempt}/{retries}" if attempt <= retries else ""))
        if not r.get("ok"):
            print(f"    claude: exit={r.get('exit_code')} "
                  f"{((r.get('err') or r.get('tail') or '').strip())[:160]}")
    return ok, why, r, cost_total, rate_waited


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

    status = RunStatus(root, "spec", len(todo), args)
    import zlib
    pname = p.functions.get("project", {}).get("name") or root.name
    live_port = 8100 + zlib.crc32(pname.encode("utf-8")) % 800   # serve_site.py と同じ規則
    print(f"ライブ進捗: http://127.0.0.1:{live_port}/pipeline.html"
          f"（serve_site.py を起動していれば。2〜3秒ごとに自動更新）")

    stop_reason = ""
    try:
        while todo:
            fid = todo.pop(0)
            if args.max_funcs and done + failed >= args.max_funcs:
                stop_reason = f"セッション上限 {args.max_funcs} 件に到達"
                print(f"{stop_reason}。同じコマンドで再開できる")
                break
            if args.budget_usd and cost_total >= args.budget_usd:
                stop_reason = f"予算 ${args.budget_usd} に到達（累計 ${cost_total:.2f}）"
                print(f"{stop_reason}。停止")
                break

            try:
                ok, why, r, cost_total, rate_waited = run_one(
                    fid, claude_cmd, extra, root, args.prompt_template, args.max_turns,
                    args.timeout, args.retries, args.backoff_base, args.backoff_max,
                    args.rate_wait_total, status, cost_total, rate_waited,
                    verify_fn=verify_spec)
            except KeyboardInterrupt:
                print(f"レート待機の累計が上限 {args.rate_wait_total}s に到達。"
                      f"停止する（再開は同じコマンド）")
                raise

            if ok:
                done += 1
                consecutive_fail = 0
                status.counts(done, failed)
                print(f"[{done}] {fid} draft化 OK（累計 ${cost_total:.2f}）")
            else:
                failed += 1
                consecutive_fail += 1
                skip.add(fid)
                status.counts(done, failed)
                print(f"  {fid}: 失敗として記録しスキップ（{why}）")
                if consecutive_fail >= args.max_consecutive_fail:
                    stop_reason = f"連続 {consecutive_fail} 件失敗（環境異常の疑い）"
                    print(f"連続 {consecutive_fail} 件失敗 → 環境異常の疑い（許可設定・"
                          f"skill配置・claude CLI を確認）。停止する")
                    break

            if args.pause and todo:
                time.sleep(args.pause)       # 予防的ペーシング（レートリミット回避）
            if (done + failed) % args.chunk == 0:
                rep = refresh_outputs(root)
                todo = targets(rep)          # チャンク境界でのみ再計算（性能・自己修復の両立）
                status.counts(done, failed, total=done + failed + len(todo))
                print(f"  -- WBS・一斉レビュー表を更新（済 {done} / 失敗 {failed}）")
    except KeyboardInterrupt:
        stop_reason = "中断（Ctrl-C）"
        print("\n中断した。進捗はファイルに保存済み。同じコマンドで続きから再開できる")
    status.finish("stopped" if stop_reason else "finished", stop_reason)

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
