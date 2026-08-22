#!/usr/bin/env python3
"""pipeline.py — 無人バッチ実行ドライバ（2000関数規模・トークン制約なし）。

サブコマンド:
  spec / testspec / testcode / impl / test
            **その工程だけ**を全件回す（①②③④⑤に1つずつ）。
            「②だけ全件流して、溜まったら人がまとめて承認する」という運転の入口。
            ①(spec)は2000関数規模向けの専用ドライバ（書きかけの修復を優先し、
            対象の再走査をチャンク境界に間引く）。②〜⑤は run エンジンを
            その工程に限定して呼ぶだけで、`run --only <工程>` と等価
  run       ①〜⑤を工程横断で回す（連続実行。承認済みの関数から
            ②③④⑤と自動で進み、承認・裁定待ちはスキップ。⭐優先も反映される）。
            --only は複数工程に限定したいときに使う（1工程なら同名サブコマンドが早い）
  priority  ⭐優先の ON/OFF・一覧。実行中の run/spec に即座に効き、
            バッチ実行中でも割り込み順を変えられる

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
  python <LR>/scripts/pipeline.py spec     --root . --max-funcs 200   # ①だけ全件
  python <LR>/scripts/pipeline.py testspec --root . --max-funcs 100   # ②だけ全件
  python <LR>/scripts/pipeline.py impl     --root .                   # ④だけ全件
  python <LR>/scripts/pipeline.py run      --root . --max-funcs 100   # ①〜⑤を工程横断で
  python <LR>/scripts/pipeline.py run  --root . --dry-run         # 対象と実行順の確認のみ
  python <LR>/scripts/pipeline.py priority F-0012 --root .        # F-0012 を⭐優先（次に割り込む）
  python <LR>/scripts/pipeline.py priority --root .               # ⭐優先の一覧

前提:
  - 対象プロジェクトに legacy-reverse の skill 一式が配置済み（.claude/skills/）
  - headless では許可プロンプトに答えられず、未許可のツール呼び出しは黙って拒否
    される（＝応答はあるのにファイルが更新されない）。そのため無人バッチは
    **既定で --dangerously-skip-permissions を付けて claude を起動する**。
    許可で止めたい環境は --no-skip-permissions を付け、対象プロジェクトの
    .claude/settings.json に <LR>/hooks/settings-example.json の
    permissions.allow をマージしておくこと
  - どちらで回す場合も hooks（guard_tests.py / guard_json.py）は必ず登録する。
    ④⑤中の tests/ 保護と JSON 破損検出はこちらが担っている

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
from ledger import Project, actionable, parse_frontmatter, resolve_flow_fids  # noqa: E402
import check_stubs  # noqa: E402
import review_checks  # noqa: E402
import review_actions  # noqa: E402  修正依頼(pending)の検出・チャンク境界のサイト更新に使う

# ①〜⑤の1関数実行に共通する既定値（argparse の既定はこの辞書を参照する）
RUN_ARG_DEFAULTS = {"max_turns": 50, "timeout": 1800, "retries": 1,
                    "backoff_base": 60, "backoff_max": 900, "rate_wait_total": 21600}

# 実行中の生存表示の間隔（秒）。headless の1プロセスは数分〜30分かかることがあり、
# その間コンソールに1行も出ないと「固まった／応答がない」としか見えない。
HEARTBEAT_SEC = 60


# --- 実行スロットの排他（複数のバッチが同じロックを取り合う） ------------------
#
# pipeline-status.json の state チェックだけでは「チェックしてから RunStatus を
# 作るまでの隙間」で二重起動できるため、O_CREAT|O_EXCL のファイル作成（OS レベルで
# 原子的）でチェックと予約を1操作にする。ロックには保持プロセスの PID を書き、
# 取得失敗時に保持プロセスの死活を確認してクラッシュの残骸は自動解除する
# （Ctrl+C や強制終了で finally が走らなくても、次の実行が自力で復旧できる）。

def run_lock_path(root: Path) -> Path:
    return root / ".legacy-reverse" / "run.lock"


def _pid_alive(pid: int) -> bool:
    """その PID のプロセスが生存しているか。Windows の os.kill(pid, 0) は
    シグナル 0 でも TerminateProcess になってしまうので OpenProcess で確認する。"""
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        SYNCHRONIZE = 0x00100000
        h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_run_lock(root: Path, mode: str) -> bool:
    """実行スロットのロックを取る。取れたら True。

    保持プロセスが既に存在しないロック（クラッシュの残骸）は解除して取り直す。
    （残骸解除→再取得の間に別プロセスが同じ判断をする狭い競合窓は残るが、
    復旧経路は人が介在する頻度なので許容する。）
    """
    lock = run_lock_path(root)
    lock.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "mode": mode,
                           "at": datetime.datetime.now().isoformat(timespec="seconds")}, f)
            return True
        except FileExistsError:
            try:
                holder = json.loads(lock.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                holder = {}
            if _pid_alive(holder.get("pid")):
                return False
            print(f"note: 残骸ロックを解除する（{lock.name}: "
                  f"pid={holder.get('pid')} は存在しない）")
            lock.unlink(missing_ok=True)
    return False


def release_run_lock(root: Path) -> None:
    run_lock_path(root).unlink(missing_ok=True)


def current_run_state(root: Path) -> dict | None:
    """実行中のバッチの枠が埋まっていればその内容を返す。

    書いたプロセス（pid）が既に存在しない場合はクラッシュの残骸として None を返す
    （state: running のまま止まったファイルが次のバッチを永久に塞がないように。
    pid の無い旧形式ファイルも残骸として扱う）。
    """
    p = root / ".legacy-reverse" / "pipeline-status.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if d.get("state") not in ("running", "waiting_rate"):
        return None
    if not _pid_alive(d.get("pid")):
        return None
    return d


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


def _kill_tree(proc: subprocess.Popen) -> None:
    """claude のプロセスツリーを止める。Windows の claude.cmd 起動は cmd.exe の
    子として node が動くため、proc.kill()（TerminateProcess）だけでは本体が残る。"""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
    else:
        proc.kill()


def run_claude(claude_cmd: list, prompt: str, root: Path, max_turns: int,
               extra: list, timeout: int, heartbeat: int = 0, label: str = "") -> dict:
    """headless Claude を1プロセス起動し、JSON 結果を返す。

    heartbeat（秒）を渡すと、待っている間その間隔で経過を1行ずつ出す
    （無音のまま数十分待たせない。0 なら何も出さない）。
    中断は Ctrl-C（＝実行の入口は CLI だけ。外部から止める口は持たない）。
    """
    cmd = claude_cmd + ["-p", prompt, "--output-format", "json",
                        "--max-turns", str(max_turns)] + extra
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(cmd, cwd=str(root), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", env=env)
    started = time.monotonic()
    deadline = started + timeout
    next_beat = (started + heartbeat) if heartbeat else None
    stop = None                                    # None=完走 / "timeout"
    while True:
        try:
            stdout, stderr = proc.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            now = time.monotonic()
            if now >= deadline:
                stop = "timeout"
            else:
                if next_beat is not None and now >= next_beat:
                    el = int(now - started)
                    print(f"    … {label} 実行中 {el // 60}分{el % 60:02d}秒"
                          f"（上限 {timeout}s）", flush=True)
                    next_beat = now + heartbeat
                continue
            _kill_tree(proc)
            try:
                stdout, stderr = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
            break
    if stop == "timeout":
        return {"ok": False, "timeout": True, "cost_usd": 0.0,
                "tail": f"timeout {timeout}s", "err": "",
                "stdout": stdout or "", "stderr": stderr or ""}
    out = {}
    for line in reversed((stdout or "").strip().splitlines() or [""]):
        try:
            out = json.loads(line)
            break
        except ValueError:
            continue
    res = {"ok": proc.returncode == 0 and not out.get("is_error", False),
           "exit_code": proc.returncode,
           "cost_usd": out.get("total_cost_usd") or out.get("cost_usd") or 0.0,
           "num_turns": out.get("num_turns"),
           "duration_ms": out.get("duration_ms"),
           "tail": (out.get("result") or stdout or "")[-500:],
           "err": (stderr or "")[-500:],
           "stdout": stdout or "", "stderr": stderr or ""}
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


def verify_test(root: str, fid: str) -> tuple:
    """契約検証: ⑤が pass しているか。

    legacy-5-test/SKILL.md の設計上、(a)実装バグは1回の headless 実行の中で
    AI 自身が「④相当の修正→⑤再実行」をループし、pass か attempt上限（blocked）
    のどちらかで自然に止まる。**blocked（人の裁定待ち）は orchestrator から見て
    「異常な失敗」ではなく「設計どおりの正常な停止」**——ここでリトライしても
    `ledger verify` が即座に断るだけで空実行になる（SKILL.md で明示的に禁止されている）
    ので、ok=False だが理由を区別して返す（呼び出し側は retries=0 にして
    無駄な再試行をしない設計にする）。
    """
    rootp = Path(root).resolve()
    p = Project(rootp)
    try:
        f = p.func(fid)
    except SystemExit:
        return False, f"{fid} が functions.json に存在しない", []
    s = p.status_of(f)
    if s["test_ok"]:
        return True, "", []
    if s["blocked_by"]:
        return False, f"裁定待ち（{s['blocked_by']}）", []
    return False, f"⑤ 未pass（現在: {s.get('test', '-')}）", []


def verify_analysis(root: str, fid: str) -> tuple:
    """契約検証: ⑦分析で docs/analysis.md に施策候補が記入されたか。

    fid は "analyze" 固定（run_one のインタフェース互換のために受け取るだけ）。
    候補ゼロは「候補なし」と明記されていれば合格（無理に施策をひねり出させない）。
    """
    p = Path(root) / "docs" / "analysis.md"
    if not p.exists():
        return False, "analysis.md が存在しない", []
    text = p.read_text(encoding="utf-8-sig")
    problems = []
    if "{{" in text:
        problems.append("テンプレのプレースホルダ（{{…}}）が残っている＝記入の省略")
    if not re.search(r"\b(OPT|REF|SEC)-\d+\b", text) and "候補なし" not in text:
        problems.append("施策候補（OPT-/REF-/SEC-）が1件も無い（無い場合は「候補なし」と明記する）")
    if not (Path(root) / "docs" / "quant.md").exists():
        problems.append("quant.md（定量評価）が無い＝実測ベースの分析になっていない")
    if problems:
        return False, f"分析の記入不備（{len(problems)}件）", problems
    return True, "", []


# --- 工程設定と対象選定（run / priority が使う。旧 browser_run.py から移設） --------
#
# kind ごとの設定。任意キー:
#   retries … その工程だけリトライ回数を変える
#
# モデルは**選ばない**。全工程 claude -p の既定モデルで回す（--model は付けない）。
# 工程ごとにモデルを変えられる作りだと、その指定が通らない環境で「その工程だけ
# 応答が返らない」という切り分けの難しい失敗になる。どうしても変えたいときだけ
# --claude-args で明示する（自己責任の抜け道）。

KINDS = {
    "spec": {"label": "①", "noun": "仕様書", "prompt_template": "/legacy-1-spec {fid}",
            "verify_fn": verify_spec},
    "testspec": {"label": "②", "noun": "テスト仕様書", "prompt_template": "/legacy-2-testspec {fid}",
                "verify_fn": verify_testspec},
    "testcode": {"label": "③", "noun": "テストコード", "prompt_template": "/legacy-3-testcode {fid}",
                "verify_fn": verify_testcode},
    "impl": {"label": "④", "noun": "実装", "prompt_template": "/legacy-4-impl {fid}",
            "verify_fn": verify_impl},
    "test": {"label": "⑤", "noun": "テスト", "prompt_template": "/legacy-5-test {fid}",
             "verify_fn": verify_test, "retries": 0},
    # ⑤は1回のheadless実行の中でAI自身が「修正→再実行」をattempt上限までループする設計
    # （legacy-5-test/SKILL.md）。orchestrator側で追加リトライすると、blocked中は
    # ledger verify に断られるだけの空実行になる（SKILL.mdで明示的に禁止）ので retries=0
}


# --- Project のロードキャッシュ -------------------------------------------
#
# レンダ時は全仕様書ページから案内パネルの判定が呼ばれる。ページごとに
# functions.json / ledger.json（2000関数級では数MB）を再パースすると、
# 差分レンダで稼いだ時間をここで食い潰すため、stat が変わるまで使い回す。

_PROJECT_CACHE: dict = {}


def _file_sig(p: Path):
    try:
        st = p.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _project(root: Path) -> Project:
    key = str(root)
    sig = (_file_sig(root / "data" / "functions.json"),
           _file_sig(root / "data" / "ledger.json"),
           _file_sig(root / "data" / "variables.json"))   # dict-gate の判定材料
    hit = _PROJECT_CACHE.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    p = Project(root)
    _PROJECT_CACHE[key] = (sig, p)
    return p


def _rerun_wanted(root: Path, fid: str, kind: str) -> bool:
    """承認待ち（draft/generated）のまま止まっている①②を、AIにもう一度修正させて
    よいか。人の修正依頼(pending)か機械レビューNGが残っていれば True。
    どちらも無い（承認を待つだけの）成果物は勝手に作り直さない。"""
    if kind in review_actions.pending_feedback_kinds(str(root), fid):
        return True
    check = review_checks.check_spec if kind == "spec" else review_checks.check_testspec
    return not check(str(root), fid)["ok"]


def _decide_kind(root: Path, fid: str, include_rerun: bool = False,
                 dict_gate: bool = True) -> str | None:
    """この関数について①〜⑤のうち次に着手すべき工程を1つ返す（無ければ None）。

    ①draft中・②generated中は人の承認待ちなので通常は対象外。
    include_rerun=True のとき（連続実行バッチ）は、修正依頼(pending)・
    機械レビューNGが残る draft/generated の再実行も許可する（自動修復）。
    dict_gate=False は変数辞書のゲートを解除する（`--no-dict-gate`。
    辞書の整理を後回しにして①を先に進める運用。CLI の `ledger next` と同じ意味）。
    """
    sp = root / "docs" / "specs" / f"{fid}.md"
    if not sp.exists():
        return None
    sfm = parse_frontmatter(sp.read_text(encoding="utf-8-sig"))
    status = sfm.get("status")
    if status == "skeleton":
        # dict-gate（設計 P2）: 変数の語義が未承認のまま①を書かせない。
        # 判定は ledger.Project.dict_gate_blockers が唯一の実装（CLI の
        # `ledger next` と共有）。辞書が無いプロジェクトでは常に空＝従来どおり
        if dict_gate and _project(root).dict_gate_blockers(fid, spec_status=status):
            return None
        return "spec"
    if status == "draft":
        if include_rerun and _rerun_wanted(root, fid, "spec"):
            return "spec"
        return None                          # 承認待ち（人の担当）
    if status != "reviewed":
        return None
    tsp = root / "docs" / "test-specs" / f"{fid}.md"
    if not tsp.exists():
        return "testspec"
    tsfm = parse_frontmatter(tsp.read_text(encoding="utf-8-sig"))
    if tsfm.get("status") != "approved":
        if (tsfm.get("status") == "generated" and include_rerun
                and _rerun_wanted(root, fid, "testspec")):
            return "testspec"
        return None                          # generated中（②承認待ち）も人の担当
    p = _project(root)
    try:
        f = p.func(fid)
    except SystemExit:
        return None
    s = p.status_of(f)
    if not s["test_code_ok"]:
        return "testcode"
    if not s["impl_ok"]:
        return "impl"
    if s["blocked_by"]:
        return None                          # ⑤裁定待ち。再実行は空実行になるので対象外
    if not s["test_ok"]:
        return "test"
    return None


def _build_sphinx_if_needed(root: Path) -> None:
    """④完了後、docs-sphinx があれば「新コード詳細(API)」を作り直す。

    review_actions.refresh_site は Quarto（WBS・仕様書サイト）しか更新しないので、
    ④の成果物であるdocstringから作るAPI詳細はここで別途面倒を見る
    （MCPの render_site ツールの with_sphinx と同じ2段構え: index.rst 再生成 → build）。
    docs-sphinx が無いプロジェクト（未導入）では何もしない。
    """
    if not (root / "docs-sphinx").exists():
        return
    scripts = Path(__file__).resolve().parent
    for name, cmd in (
            ("sphinx-index", [sys.executable, str(scripts / "ledger.py"),
                              "--root", str(root), "sphinx-index"]),
            ("sphinx-build", [sys.executable, "-m", "sphinx", "-b", "html",
                              "docs-sphinx", "docs/_site/api", "-q"])):
        r = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:                # 無音で失敗させない（古いAPIページが残る）
            print(f"note: {name} が失敗（exit={r.returncode}）: "
                  f"{(r.stderr or r.stdout or '').strip()[-200:]}")


# --- ⭐優先（run / spec の実行順への割り込み） ------------------------------

def _priority_path(root: Path) -> Path:
    return root / ".legacy-reverse" / "batch-priority.json"


def _load_priority(root: Path) -> dict:
    """優先キュー: {"order": [優先fid…（⭐順）], "retry": [失敗スキップを解除するfid…]}。

    order は実行順の割り込み（_scan_targets が先頭に並べ替える）。retry は
    「⭐優先して再実行」用のワンショット——バッチの失敗スキップ集合から次の走査で
    取り除かれ、消費される（毎走査解除だと失敗し続ける関数が無限ループするため）。
    """
    try:
        d = json.loads(_priority_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return {"order": list(d.get("order") or []), "retry": list(d.get("retry") or [])}


def _save_priority(root: Path, d: dict) -> None:
    p = _priority_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def prioritize(root: str, fid: str, on: bool = True) -> dict:
    """⭐優先のON/OFF（バッチ実行中でなくても設定できる）。"""
    rootp = Path(root).resolve()
    try:
        _project(rootp).func(fid)
    except SystemExit:
        return {"ok": False, "message": f"{fid} が functions.json に存在しない"}
    d = _load_priority(rootp)
    if on:
        if fid not in d["order"]:
            d["order"].append(fid)
        if fid not in d["retry"]:
            d["retry"].append(fid)     # 失敗スキップ済みでも次の走査で拾い直す
        msg = f"{fid} を優先しました（いまの1件が終わり次第、先頭に割り込みます）"
    else:
        d["order"] = [x for x in d["order"] if x != fid]
        d["retry"] = [x for x in d["retry"] if x != fid]
        msg = f"{fid} の優先を解除しました"
    _save_priority(rootp, d)
    return {"ok": True, "message": msg}


def _scan_targets(root: Path, skip: set, flow_fids=None, dict_gate: bool = True) -> list:
    """次に着手できる (func_id, kind) の一覧（承認待ち等を除いた自動実行対象）。

    flow_fids（set|None）を渡すと、その集合に無い func_id を除く
    （`run --flow` がこの引数で対象を絞る）。
    dict_gate=False で変数辞書のゲートを解除する（`run --no-dict-gate`）。
    """
    # ⭐優先の retry 分は失敗スキップを解除してから走査する（skip を直接削る。
    # ワンショット消費なので保存し直す）
    prio = _load_priority(root)
    if prio["retry"]:
        for fid in prio["retry"]:
            for entry in [s for s in skip if s[0] == fid]:
                skip.discard(entry)
        _save_priority(root, {"order": prio["order"], "retry": []})
    p = _project(root)
    out = []
    for f in p.funcs():
        fid = f["func_id"]
        if flow_fids is not None and fid not in flow_fids:
            continue
        kind = _decide_kind(root, fid, include_rerun=True, dict_gate=dict_gate)
        if kind and (fid, kind) not in skip:
            out.append((fid, kind))
    # ⭐優先を先頭へ（⭐順）。それ以外は functions.json 順のまま（安定ソート）
    rank = {fid: i for i, fid in enumerate(prio["order"])}
    out.sort(key=lambda t: (0, rank[t[0]]) if t[0] in rank else (1, 0))
    return out


def _queue_entry(root: Path, p: Project, f: dict) -> dict | None:
    """1関数の残タスク（/batch-queue 表示の1行）。

    自動実行待ちは kind=spec〜test、人待ちは approve-spec / approve-testspec /
    adjudicate。どちらでもない（完了・⓪未了）は None。
    """
    fid = f["func_id"]
    kind = _decide_kind(root, fid, include_rerun=True)
    if kind:
        return {"func_id": fid, "kind": kind, "auto": True}
    sp = root / "docs" / "specs" / f"{fid}.md"
    if not sp.exists():
        return None
    st = parse_frontmatter(sp.read_text(encoding="utf-8-sig")).get("status")
    if st == "draft":
        return {"func_id": fid, "kind": "approve-spec", "auto": False}
    if st != "reviewed":
        return None
    tsp = root / "docs" / "test-specs" / f"{fid}.md"
    if (tsp.exists()
            and parse_frontmatter(tsp.read_text(encoding="utf-8-sig")).get("status") == "generated"):
        return {"func_id": fid, "kind": "approve-testspec", "auto": False}
    blocked = (p.ledger.get(fid) or {}).get("blocked_by")
    if blocked:
        return {"func_id": fid, "kind": "adjudicate", "auto": False, "issue": blocked}
    return None


def batch_queue(root: str, q: str = "", chip: str = "") -> dict:
    """残タスク一覧（実行順）＋件数集計＋検索。serve_site の GET /batch-queue が
    **表示専用**で使う（操作は CLI・チャット・ファイル記入で行う）。

    2000関数規模では全 frontmatter を読む（数秒かかりうる）ので、フロントは
    ポーリングに載せず、表示時・検索時にだけ呼ぶこと。応答は50件で打ち切り、
    検索はサーバ側で行う（全件をブラウザに送らない）。
    """
    rootp = Path(root).resolve()
    p = _project(rootp)
    rank = {fid: i for i, fid in enumerate(_load_priority(rootp)["order"])}
    entries = []
    for f in p.funcs():
        e = _queue_entry(rootp, p, f)
        if not e:
            continue
        leg, new = f.get("legacy") or {}, f.get("new") or {}
        e["name"] = new.get("name") or leg.get("name") or ""
        e["file"] = leg.get("file") or new.get("module") or ""
        e["starred"] = e["func_id"] in rank
        entries.append(e)
    entries.sort(key=lambda e: (0, rank[e["func_id"]]) if e["func_id"] in rank else (1, 0))
    # 実行中の1件（あれば）を除いた「次N」の通し番号を振る
    cur = None
    try:
        sd = json.loads((rootp / ".legacy-reverse" / "pipeline-status.json")
                        .read_text(encoding="utf-8"))
        if sd.get("state") in ("running", "waiting_rate"):
            cur = (sd.get("current") or {}).get("func_id")
    except (OSError, ValueError):
        pass
    pos = 0
    for e in entries:                      # 自動実行待ちのみ番号を振る（人待ちはスキップされる）
        if e["auto"] and e["func_id"] == cur:
            e["now"] = True
        elif e["auto"]:
            pos += 1
            e["pos"] = pos
    counts: dict = {}
    for e in entries:
        k = e["kind"] if e["auto"] else "human"
        counts[k] = counts.get(k, 0) + 1
    lst = entries
    if chip == "human":
        lst = [e for e in lst if not e["auto"]]
    elif chip:
        lst = [e for e in lst if e["auto"] and e["kind"] == chip]
    if q:
        ql = q.lower()
        lst = [e for e in lst
               if ql in f"{e['func_id']} {e['name']} {e['file']}".lower()]
    return {"ok": True, "total": len(entries), "counts": counts,
            "hits": len(lst), "items": lst[:50]}


def refresh_outputs(root: Path) -> dict:
    """WBS と一斉レビュー表（①②）を再生成する（人が途中経過を常に見られる状態を保つ）。

    戻り値は①の表の集計（連続実行の対象選定が machine_ng_funcs を使う）。
    """
    scripts = Path(__file__).resolve().parent
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    subprocess.run([sys.executable, str(scripts / "ledger.py"), "--root", str(root), "wbs"],
                   capture_output=True, env=env)
    return review_checks.make_reports(str(root))["spec"]


def classify_ng(why: str, r: dict) -> str:
    """失敗理由をライブ表示・集計用に分類する。"""
    if "裁定待ち" in why:
        return "⛔人の裁定待ち（正常）"     # ⑤の設計どおりの停止。異常ではない
    if "タイムアウト" in why:
        return "タイムアウト"
    if not r.get("ok"):
        return "claude異常終了"
    if "存在しない" in why:
        return "ファイル未作成（書き込み権限の疑い）"
    if "status が" in why:
        return "draft未到達（status未更新）"
    if "スタブ検出" in why:
        return "スタブ検出"
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
        self.d = {"state": "running", "mode": mode, "pid": os.getpid(),
                  "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
                  "total_targets": total, "done": 0, "failed": 0,
                  "cost_usd": 0.0, "budget_usd": args.budget_usd or None,
                  "max_funcs": args.max_funcs or None,
                  "flow": getattr(args, "flow", None),
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
               cost_total: float, r: dict, attempt: int, problems: list = None,
               phase: str = None) -> None:
        if ok:
            self.ok_secs.append(sec)
        else:
            self.d["ng_kinds"][kind] = self.d["ng_kinds"].get(kind, 0) + 1
        self.d["cost_usd"] = round(cost_total, 4)
        self.d["recent"].insert(0, {
            "func_id": fid, "ok": ok, "why": why, "kind": None if ok else kind,
            "phase": phase,                      # 何の工程だったか（spec/testspec/…。表示用）
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


def agent_log_name(fid: str) -> str:
    """識別子 → 応答ログのファイル名。

    F-xxxx はそのまま。辞書バッチの識別子（"dict:V-0001〜V-0040"）のように
    ファイル名に使えない文字を含むものだけ潰す（Windows の ':' で落ちないように）。
    **serve_site.py の /agent-logs/ ルートが同じ規則で引き当てる**ので、
    片側だけ変えないこと（画面のリンクが 404 になる）。
    """
    return re.sub(r"[^\w.\-]", "_", fid) + ".txt"


def save_agent_log(root: Path, fid: str, attempt: int, r: dict) -> Path:
    """エージェント（headless claude）の応答全文を関数ごとのファイルに残す。

    失敗原因の一次情報はここにしか無い（why はファイル状態の検証結果でしかない）。
    """
    d = root / ".legacy-reverse" / "agent-logs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / agent_log_name(fid)
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


def permission_args(args) -> list:
    """許可まわりの起動引数。**既定で --dangerously-skip-permissions を付ける**。

    headless（claude -p）は許可プロンプトに答えられず、未許可のツール呼び出しは
    黙って拒否される。無人バッチはその場に人がいないので、既定が「聞く」だと
    「応答はあるのにファイルが更新されない」失敗を工程ごとに繰り返すことになる
    （しかも why はファイル状態の検証結果なので、原因が許可だと分からない）。
    安全装置は許可プロンプトではなく hooks 側（guard_tests.py / guard_json.py）が
    担う設計なので、既定を「聞かない」に倒す。--no-skip-permissions で外せる。
    """
    if args.skip_permissions:
        return ["--dangerously-skip-permissions"]
    print("permissions: --no-skip-permissions 指定。.claude/settings.json の "
          "permissions.allow に不足があると、応答はあってもファイルが更新されない"
          "（<LR>/hooks/settings-example.json を参照）")
    return []


def run_one(fid: str, claude_cmd: list, extra: list, root: Path,
           prompt_template: str, max_turns: int, timeout: int, retries: int,
           backoff_base: int, backoff_max: int, rate_wait_total: int,
           status: "RunStatus", cost_total: float, rate_waited: int,
           verify_fn=verify_spec, phase: str = None) -> tuple:
    """1関数分の実行ループ（レート待機・リトライ・検証込み）。フェーズ非依存。

    cmd_spec（①無人バッチ）と cmd_run（①〜⑤の連続実行）が共用する。
    verify_fn(root_str, fid) -> (ok, why, problems) で「完了とみなす条件」を差し替える
    （① verify_spec / ② verify_testspec）。プロンプトテンプレートも差し替え可能なので
    ①以外のフェーズも同じループに乗る。
    戻り値: (ok, why, r, cost_total, rate_waited) — 呼び出し側の累計をこれで更新する。
    レート待機の累計上限超過は KeyboardInterrupt で呼び出し側に伝える（バッチの
    安全停止と同じ扱い）。モデルは指定しない（claude の既定モデルで回す）。
    """
    prompt = prompt_template.format(fid=fid)
    ok, why, r = False, "", {}
    attempt, backoff = 0, backoff_base
    while attempt <= retries:
        status.current(fid, attempt + 1)
        t0 = time.time()
        # 開始を必ず1行出す。完了時だけ出す作りだと、1プロセスが長い工程では
        # 「無反応」にしか見えない
        print(f"  ▶ {fid} 実行中"
              + (f"（試行 {attempt + 1}）" if attempt else "") + "…", flush=True)
        r = run_claude(claude_cmd, prompt, root, max_turns, extra, timeout,
                       heartbeat=HEARTBEAT_SEC, label=fid)
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
                      round(time.time() - t0), cost_total, r, attempt, problems=problems,
                      phase=phase)
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
    flow_fids = None
    if args.flow:
        flow_fids = resolve_flow_fids(p, args.flow)
        print(f"フロー {args.flow}: {len(flow_fids)} 関数に限定")
    claude_cmd = None if args.dry_run else find_claude(args.claude_cmd)
    if claude_cmd:
        preflight_claude(claude_cmd)
    extra = permission_args(args)
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
        repair = [f for f in rep["machine_ng_funcs"]
                 if f not in skip and (flow_fids is None or f in flow_fids)]
        gated: list = []
        fresh = [fid for fid, _ in actionable(Project(root), phase="1", skip_wait=True,
                                              flow_fids=flow_fids,
                                              dict_gate=getattr(args, "dict_gate", True),
                                              gated=gated)
                 if fid not in skip]
        if gated:
            # dict-gate（設計 P2）。変数辞書が無いプロジェクトでは常に空＝従来どおり
            print(f"dict-gate: 未承認の語義が残る {len(gated)} 関数を①の対象から外した"
                  "（チャットで `/legacy-0-dict` → 辞書ページで承認 → 再実行。"
                  "辞書を後回しにして①を先に進めるなら --no-dict-gate）")
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
    import serve_site                        # ポート規則は serve_site.default_port が正
    pname = p.functions.get("project", {}).get("name") or root.name
    live_port = serve_site.default_port(pname)
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
                    verify_fn=verify_spec, phase="spec")
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
    rep = review_checks.make_report(str(args.root), "spec")
    print(f"レビュー待ち {rep['drafts']} 件 → docs/spec-review.md を人がレビューし、"
          f"OK分を reviewed 化してください")


def cmd_run(args) -> None:
    """①〜⑤を工程横断で無人実行する（連続実行）。

    対象選定（次に着手できる工程）は `_scan_targets`、工程別の設定
    （プロンプト・検証関数・リトライ数）は `KINDS`。1件終わるごとに全関数を
    再走査するので、実行中に人が承認・裁定した分も次の走査で自動的に拾われる。
    """
    root = Path(args.root).resolve()

    flow_fids = None
    if args.flow:
        flow_fids = resolve_flow_fids(Project(root), args.flow)
        print(f"フロー {args.flow}: {len(flow_fids)} 関数に限定")

    def label(kind: str) -> str:
        cfg = KINDS[kind]
        return cfg["label"] + cfg["noun"]

    only = None
    if getattr(args, "only", None):
        only = {k.strip() for k in args.only.split(",") if k.strip()}
        bad = sorted(only - set(KINDS))
        if bad:
            sys.exit(f"error: --only に不明な工程 {bad}"
                     f"（有効: {', '.join(KINDS)}）")
        print("工程を限定: " + "・".join(label(k) for k in KINDS if k in only))

    def scan(skip: set) -> list:
        t = _scan_targets(root, skip, flow_fids=flow_fids,
                          dict_gate=getattr(args, "dict_gate", True))
        return [x for x in t if x[1] in only] if only else t

    targets = scan(set())
    if not targets:
        print("実行できる工程がない（残りは承認・裁定待ちか完了）。"
              "WBS か /pipeline.html の「人待ち」を確認してください")
        return
    print(f"対象 {len(targets)} 工程から開始"
          + (f"（このセッションは最大 {args.max_funcs} 件）" if args.max_funcs else ""))
    if args.dry_run:
        for fid, kind in targets[:args.max_funcs or None]:
            cfg = KINDS[kind]
            print(f"  {label(kind):<10} claude -p \"{cfg['prompt_template'].format(fid=fid)}\"")
        print("※ 実際は1件ごとに再走査するため、実行中の承認・⭐優先で対象と順番は変わる")
        return

    claude_cmd = find_claude(args.claude_cmd)
    preflight_claude(claude_cmd)
    extra = permission_args(args)
    extra += args.claude_args

    # 1工程だけに限定しているときは、進捗ページにその工程名を出す
    # （「連続実行」と出ると②だけ回しているのか工程横断なのか区別が付かないため）
    mode = (label(next(iter(only))) + "（全件・CLI）") if only and len(only) == 1 \
        else "連続実行（CLI）"
    status = RunStatus(root, mode, len(targets), args)
    import serve_site                        # ポート規則は serve_site.default_port が正
    pname = Project(root).functions.get("project", {}).get("name") or root.name
    print(f"ライブ進捗: http://127.0.0.1:{serve_site.default_port(pname)}/pipeline.html"
          f"（serve_site.py を起動していれば。2〜3秒ごとに自動更新）")

    skip: set = set()
    done = failed = consecutive_fail = 0
    cost_total, rate_waited = 0.0, 0
    stop_reason = ""
    try:
        while targets:
            if args.max_funcs and done + failed >= args.max_funcs:
                stop_reason = f"セッション上限 {args.max_funcs} 件に到達"
                print(f"{stop_reason}。同じコマンドで再開できる")
                break
            if args.budget_usd and cost_total >= args.budget_usd:
                stop_reason = f"予算 ${args.budget_usd} に到達（累計 ${cost_total:.2f}）"
                print(f"{stop_reason}。停止")
                break
            fid, kind = targets[0]
            cfg = KINDS[kind]
            try:
                ok, why, r, cost_total, rate_waited = run_one(
                    fid, claude_cmd, extra, root, cfg["prompt_template"], args.max_turns,
                    args.timeout, cfg.get("retries", args.retries), args.backoff_base,
                    args.backoff_max, args.rate_wait_total, status, cost_total, rate_waited,
                    verify_fn=cfg["verify_fn"], phase=kind)
            except KeyboardInterrupt:
                print(f"レート待機の累計が上限 {args.rate_wait_total}s に到達。"
                      f"停止する（再開は同じコマンド）")
                raise

            if ok:
                done += 1
                consecutive_fail = 0
                status.counts(done, failed)
                print(f"[{done}] {fid} {label(kind)} OK（累計 ${cost_total:.2f}）")
                if kind == "impl":
                    _build_sphinx_if_needed(root)
            else:
                failed += 1
                consecutive_fail += 1
                skip.add((fid, kind))        # 同じ (関数, 工程) はこのセッション中は再試行しない
                status.counts(done, failed)
                print(f"  {fid} {label(kind)}: 失敗として記録しスキップ（{why}）")
                if consecutive_fail >= args.max_consecutive_fail:
                    stop_reason = f"連続 {consecutive_fail} 件失敗（環境異常の疑い）"
                    print(f"連続 {consecutive_fail} 件失敗 → 環境異常の疑い（許可設定・"
                          f"skill配置・claude CLI を確認）。停止する")
                    break

            if args.pause:
                time.sleep(args.pause)       # 予防的ペーシング（レートリミット回避）
            if (done + failed) % args.chunk == 0:
                # 承認待ちが溜まるので定期的にサイトを更新し、人が閲覧サイトを
                # 見ながら承認・裁定（チャット/CLI/ファイル記入）を並行できるようにする
                review_actions.refresh_site(str(root), "all")     # ①②の表とも
                print(f"  -- WBS・一斉レビュー表を更新（済 {done} / 失敗 {failed}）")
            targets = scan(skip)             # 承認・⭐で増減した対象を拾う（--only 指定分に限定）
            status.counts(done, failed, total=done + failed + len(targets))
    except KeyboardInterrupt:
        stop_reason = "中断（Ctrl-C）"
        print("\n中断した。進捗はファイルに保存済み。同じコマンドで続きから再開できる")
    status.finish("stopped" if stop_reason else "finished",
                  stop_reason or f"実行できる工程が無くなった（済 {done}・失敗 {failed}。"
                                 "残りは承認・裁定待ちか完了）")

    if not args.no_render:
        review_actions.refresh_site(str(root), "all")    # ①②の一斉レビュー表・WBS・サイトを最終更新
    print(f"\n完了: 成功 {done} 件 / 失敗 {failed} 件 / 累計コスト ${cost_total:.2f}")
    if failed:
        print(f"失敗した (関数, 工程): {sorted(skip)}")
        print("  原因調査: .legacy-reverse/agent-logs/<fid>.txt にエージェント応答の全文、"
              ".legacy-reverse/pipeline-log.jsonl に検証結果と応答末尾がある")
        print("  再試行: `pipeline.py priority F-xxxx`（⭐優先）を付けて同じコマンドを再実行"
              "（⭐は失敗スキップも解除する）")


def cmd_priority(args) -> None:
    """⭐優先の ON/OFF・一覧。

    設定は .legacy-reverse/batch-priority.json に書かれ、実行中の run / spec が
    1件終わるごとの再走査で拾う——つまり実行中に叩けば「いまの1件が終わり次第、
    その関数に割り込む」。失敗スキップ済みの関数も⭐を付ければ次の走査で拾い直される。
    """
    root = Path(args.root).resolve()
    if args.clear:
        d = _load_priority(root)
        for fid in list(d["order"]):
            prioritize(str(root), fid, on=False)
        print(f"⭐優先をすべて解除した（{len(d['order'])} 件）")
        return
    if not args.fids:
        d = _load_priority(root)
        if not d["order"]:
            print("⭐優先は未設定。`pipeline.py priority F-0012` で設定する"
                  "（実行中のバッチにも次の1件から効く）")
            return
        print("⭐優先（この順に割り込む）:")
        for i, fid in enumerate(d["order"], 1):
            print(f"  {i}. {fid}")
        return
    ng = 0
    for fid in args.fids:
        r = prioritize(str(root), fid, on=not args.off)
        print(("OK " if r["ok"] else "NG ") + r["message"])
        ng += 0 if r["ok"] else 1
    if ng:
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    """CLI の引数定義。main() から分離してあるのは、既定値の契約
    （--skip-permissions が既定 ON であること等）をセルフテストから検証するため。
    """
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_driver_args(s, chunk_default: int) -> None:
        """spec / run で共通のドライバ引数。"""
        s.add_argument("--root", default=".")
        s.add_argument("--chunk", type=int, default=chunk_default,
                       help=f"WBS/レビュー表を更新する間隔（既定{chunk_default}件）")
        s.add_argument("--max-funcs", type=int, default=0, help="このセッションで処理する上限（0=無制限）")
        s.add_argument("--budget-usd", type=float, default=0, help="累計コスト上限 USD（0=無制限）")
        s.add_argument("--retries", type=int, default=RUN_ARG_DEFAULTS["retries"],
                       help="検証NG時のリトライ回数（既定1）")
        s.add_argument("--max-consecutive-fail", type=int, default=3,
                       help="連続失敗でドライバを停止する閾値（既定3）")
        s.add_argument("--max-turns", type=int, default=RUN_ARG_DEFAULTS["max_turns"],
                       help="1関数あたりのターン上限")
        s.add_argument("--timeout", type=int, default=RUN_ARG_DEFAULTS["timeout"],
                       help="1関数あたりの秒数上限（既定30分）")
        s.add_argument("--backoff-base", type=int, default=RUN_ARG_DEFAULTS["backoff_base"],
                       help="レートリミット検知時の初期待機秒（以後2倍ずつ。既定60）")
        s.add_argument("--backoff-max", type=int, default=RUN_ARG_DEFAULTS["backoff_max"],
                       help="1回の待機の上限秒（既定900=15分）")
        s.add_argument("--rate-wait-total", type=int, default=RUN_ARG_DEFAULTS["rate_wait_total"],
                       help="レート待機の累計上限秒。超えたら停止（既定21600=6時間）")
        s.add_argument("--pause", type=float, default=0,
                       help="関数間の予防的な待機秒（レートリミットに当たりやすい環境用）")
        s.add_argument("--skip-permissions", dest="skip_permissions", action="store_true",
                       default=True,
                       help="--dangerously-skip-permissions を claude に渡す（既定。"
                            "無人バッチは許可プロンプトに答えられる人がいないため）")
        s.add_argument("--no-skip-permissions", dest="skip_permissions", action="store_false",
                       help="許可をスキップしない。.claude/settings.json の permissions.allow "
                            "だけで足りる環境向け（不足していると黙って拒否される）")
        s.add_argument("--claude-cmd", default=None, help="claude 実行ファイルのパス（既定: PATH から検索）")
        s.add_argument("--claude-args", nargs="*", default=[], help="claude へ追加で渡す引数")
        s.add_argument("--dry-run", action="store_true", help="実行せず対象とコマンドを表示")
        s.add_argument("--no-render", action="store_true", help="終了時の HTML 再生成を省略")
        s.add_argument("--flow", default=None,
                       help="フロー名 or flow_id で対象をそのフロー到達集合に限定"
                            "（ledger flow add で定義したもの。人が「このフローだけ作業」と指定できる）")
        s.add_argument("--no-dict-gate", dest="dict_gate", action="store_false", default=True,
                       help="変数辞書のゲートを解除（既定は ON: 未承認の語義が残る関数の①を除外）。"
                            "辞書の整理を後回しにして①を先に進めるとき。"
                            "`ledger next --no-dict-gate` と同じ意味")

    s = sub.add_parser("spec", help="①仕様書を全件 draft まで無人実行（→ 人が一斉レビュー）")
    add_driver_args(s, chunk_default=10)
    s.add_argument("--prompt-template", default="/legacy-1-spec {fid}",
                   help="1関数あたりのプロンプト（{fid} が関数IDに置換される）")

    r = sub.add_parser("run", help="①〜⑤を工程横断で無人実行（連続実行。⭐優先も反映）")
    add_driver_args(r, chunk_default=5)
    r.add_argument("--only", default=None,
                   help="工程を限定（spec,testspec,testcode,impl,test をカンマ区切り。"
                        "複数工程だけを回したいときに使う。1工程なら同名のサブコマンドが早い）")

    # 工程ごとの専用サブコマンド（②〜⑤）。①の `spec` と同じ粒度で「②だけ全件」を
    # 打てるようにする入口で、実体は run エンジンをその工程に限定して呼ぶだけ
    # （`run --only <工程>` と等価。挙動を二重に実装はしない）。
    # 名前はリテラルで書く——check_skill.py の静的走査（文書に書いたサブコマンドの
    # 実在確認）が拾えるようにするため。
    for s in (sub.add_parser("testspec", help="②テスト仕様書だけを全件 無人実行（run --only testspec と同じ）"),
              sub.add_parser("testcode", help="③テストコードだけを全件 無人実行（run --only testcode と同じ）"),
              sub.add_parser("impl", help="④実装だけを全件 無人実行（run --only impl と同じ）"),
              sub.add_parser("test", help="⑤テストだけを全件 無人実行（run --only test と同じ）")):
        add_driver_args(s, chunk_default=5)

    p = sub.add_parser("priority", help="⭐優先の ON/OFF・一覧（実行中でも使える）")
    p.add_argument("fids", nargs="*", help="関数ID（F-0012 …）。省略すると現在の⭐一覧を表示")
    p.add_argument("--off", action="store_true", help="指定した関数の⭐優先を解除する")
    p.add_argument("--clear", action="store_true", help="⭐優先をすべて解除する")
    p.add_argument("--root", default=".")

    return ap


def main() -> None:
    # claude の応答やサブプロセスの stderr（絵文字・置換文字を含み得る）を print するため、
    # cp932 コンソールでも UnicodeEncodeError で落ちないようにしておく
    import serve_site
    serve_site.use_utf8_console()
    args = build_parser().parse_args()
    if args.cmd == "priority":
        # 設定ファイルを書くだけで実行スロットは使わない。バッチ実行中に
        # 割り込み順を変える用途がむしろ本命なので、ロックは取らない
        cmd_priority(args)
        return
    if args.cmd in KINDS and args.cmd != "spec":
        args.only = args.cmd            # 工程別サブコマンド = run をその工程に限定
        fn = cmd_run
    else:
        fn = {"spec": cmd_spec, "run": cmd_run}[args.cmd]
    root = Path(args.root).resolve()
    if args.dry_run:
        fn(args)
        return
    # 複数のバッチ（別端末の spec/run/dict 等）は同じ実行スロットを取り合う
    running = current_run_state(root)
    if running:
        cur = (running.get("current") or {}).get("func_id")
        sys.exit(f"error: 別の実行が進行中（{running.get('mode')}"
                 + (f"・{cur}" if cur else "")
                 + "）。/pipeline.html で確認するか、完了を待ってから実行する")
    if not acquire_run_lock(root, f"cli-{args.cmd}"):
        sys.exit("error: 別の実行がロックを保持中（.legacy-reverse/run.lock）。"
                 "実行中のバッチが本当に無いか確認する")
    try:
        fn(args)
    finally:
        release_run_lock(root)


if __name__ == "__main__":
    main()
