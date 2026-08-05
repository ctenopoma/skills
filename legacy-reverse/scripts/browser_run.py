#!/usr/bin/env python3
"""browser_run.py — ①②の作成をブラウザから1関数だけトリガーする（試作）。

チャットで `/legacy-1-spec F-xxxx` / `/legacy-2-testspec F-xxxx` と打つのと同じ
処理（headless Claude を1回起動して成果物を作る）を、WBSのページに出るボタンから
起動できるようにする。**チャット駆動の既存フローを置き換えるものではない**——
同じ pipeline.py の実行ロジック（run_one）を共有する、もう一つの入口。

排他制御は .legacy-reverse/pipeline-status.json を共有することで実現する
（pipeline.py の無人バッチと同じ「実行スロット」を取り合う形。RunStatus を
そのまま流用するので、ブラウザ発の単発実行も /pipeline.html にそのまま出る）。
2つ目の安全策として実行中はページ側の再クリックも弾く（サーバ側で二重チェック）。

serve_site.py から呼ばれる:
  - trigger_widget_html(root, fid): ①/②未着手の仕様書ページに埋め込むボタン
  - start(root, fid, kind): POST /run-phase の実処理（バックグラウンドスレッドで起動して即返す）
"""
import json
import os
import subprocess
import sys
import threading
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline  # noqa: E402  run_one / RunStatus / find_claude / verify_* 等を共用する
import review_actions  # noqa: E402  完了後の WBS・サイト更新を共用する
from ledger import Project, parse_frontmatter  # noqa: E402

# バッチと同じデフォルト値（pipeline.py の argparse 既定と揃える。単発実行なので
# リトライ・タイムアウトは軽め、レート待機はバッチ同様に長時間許容する）
RUN_DEFAULTS = types.SimpleNamespace(
    max_turns=50, timeout=1800, retries=1, backoff_base=60, backoff_max=900,
    rate_wait_total=21600, model=None, skip_permissions=False, claude_args=[],
    budget_usd=0, max_funcs=1)

# kind ごとの設定。ボタンはすべて①仕様書ページに出す（関数の「ホーム」として
# 常に存在し続けるページのため。②③④の成果物ページは工程途中では無かったり
# docs/ 配下にすら無かったりするので、置き場所を統一している）
KINDS = {
    "spec": {"label": "①", "noun": "仕様書", "prompt_template": "/legacy-1-spec {fid}",
            "verify_fn": pipeline.verify_spec},
    "testspec": {"label": "②", "noun": "テスト仕様書", "prompt_template": "/legacy-2-testspec {fid}",
                "verify_fn": pipeline.verify_testspec},
    "testcode": {"label": "③", "noun": "テストコード", "prompt_template": "/legacy-3-testcode {fid}",
                "verify_fn": pipeline.verify_testcode},
    "impl": {"label": "④", "noun": "実装", "prompt_template": "/legacy-4-impl {fid}",
            "verify_fn": pipeline.verify_impl},
    "test": {"label": "⑤", "noun": "テスト", "prompt_template": "/legacy-5-test {fid}",
             "verify_fn": pipeline.verify_test, "retries": 0},
    # ⑤は1回のheadless実行の中でAI自身が「修正→再実行」をattempt上限までループする設計
    # （legacy-5-test/SKILL.md）。orchestrator側で追加リトライすると、blocked中は
    # ledger verify に断られるだけの空実行になる（SKILL.mdで明示的に禁止）ので retries=0
}


def _status_path(root: Path) -> Path:
    return root / ".legacy-reverse" / "pipeline-status.json"


def _lock_path(root: Path) -> Path:
    return root / ".legacy-reverse" / "browser-run.lock"


def _acquire_lock(root: Path) -> bool:
    """ブラウザ発の実行を1つに絞る排他ロック。取れたら True。

    pipeline-status.json の state チェックだけでは「チェックしてから実際に
    予約する（RunStatus を作る）までの隙間」で二重起動できてしまう——
    start() は即座に返り、状態ファイルへの書き込みはバックグラウンドスレッド側で
    後から起きるため、ほぼ同時に届いた2つの POST（ダブルクリック・複数タブ）が
    両方ともチェックを通り抜ける余地がある。O_CREAT|O_EXCL のファイル作成は
    OS レベルで原子的なので、ここでチェックと予約を1操作にまとめて隙間を無くす。
    """
    lock = _lock_path(root)
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        return False


def _release_lock(root: Path) -> None:
    _lock_path(root).unlink(missing_ok=True)


def _current_run_state(root: Path) -> dict | None:
    """実行中の枠（バッチ or 別のブラウザ実行）が既に埋まっていれば、その内容を返す。"""
    p = _status_path(root)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if d.get("state") in ("running", "waiting_rate") else None


def _decide_kind(root: Path, fid: str) -> str | None:
    """この関数の①仕様書ページに出す実行ボタンの種別を決める。①〜⑤のうち
    次に着手すべきものを1つ返す（無ければ None＝ボタンを出さない）。

    ①draft中・②generated中は承認ウィジェット（review_actions）側が担当するので
    ここでは対象外（承認待ちの間に実行ボタンを並べて二重導線にしない）。
    """
    sp = root / "docs" / "specs" / f"{fid}.md"
    if not sp.exists():
        return None
    sfm = parse_frontmatter(sp.read_text(encoding="utf-8-sig"))
    status = sfm.get("status")
    if status == "skeleton":
        return "spec"
    if status != "reviewed":
        return None                          # draft中は承認ウィジェットの担当
    tsp = root / "docs" / "test-specs" / f"{fid}.md"
    if not tsp.exists():
        return "testspec"
    tsfm = parse_frontmatter(tsp.read_text(encoding="utf-8-sig"))
    if tsfm.get("status") != "approved":
        return None                          # generated中（②承認待ち）も承認ウィジェットの担当
    p = Project(root)
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
        return None                          # ⑤裁定待ち。再実行は空実行になるので出さない
    if not s["test_ok"]:
        return "test"
    return None


def trigger_widget_html(root: str, fid: str) -> str | None:
    """①〜⑤のうち次に着手すべきものがあるときだけ実行ボタンを返す。無ければ None。"""
    rootp = Path(root).resolve()
    kind = _decide_kind(rootp, fid)
    if not kind:
        return None
    cfg = KINDS[kind]
    label, noun = cfg["label"], cfg["noun"]
    verb = "実行" if kind == "test" else "作成"
    return f"""```{{=html}}
<div id="run-{fid}" class="lr-run-widget"
     style="border:2px solid #0891b2;border-radius:10px;padding:14px 18px;margin:0 0 22px;
            background:#ecfeff;font-family:system-ui,sans-serif">
  <div style="font-weight:700;margin-bottom:6px">{label}{noun} — 未着手（{fid}）</div>
  <p style="margin:4px 0 10px;color:#155e75">
    headless Claude を1回起動して{noun}を{verb}します（数分かかります）。
  </p>
  <button onclick="lrRun.start('{fid}','{kind}')"
          style="background:#0891b2;color:#fff;border:0;border-radius:6px;padding:8px 16px;
                 font-weight:600;cursor:pointer">{label}を実行する</button>
  <span id="run-msg-{fid}" style="margin-left:10px;font-size:.9rem"></span>
</div>
<script>
window.lrRun = window.lrRun || (function(){{
  function poll(fid, msgEl, tries){{
    fetch("/pipeline-status.json", {{cache:"no-store"}}).then(r=>r.json()).then(d=>{{
      if(d.state === "running" && d.current && d.current.func_id === fid){{
        const t0 = new Date(d.current.started_at);
        const el = Math.max(0, Math.round((Date.now()-t0)/1000));
        msgEl.textContent = "実行中…（" + Math.floor(el/60) + "分" + el%60 + "秒経過。"
          + "詳細は /pipeline.html）";
        setTimeout(() => poll(fid, msgEl, tries+1), 3000);
      }} else if(d.state === "waiting_rate"){{
        msgEl.textContent = "レート待機中…（/pipeline.html を参照）";
        setTimeout(() => poll(fid, msgEl, tries+1), 3000);
      }} else if(tries === 0){{
        setTimeout(() => poll(fid, msgEl, tries+1), 1500);  // 起動直後の反映待ち
      }} else {{
        const last = (d.recent || [])[0];
        if(last && last.func_id === fid){{
          msgEl.textContent = last.ok ? "完了：draft化 OK。再読み込みします…"
                                       : "失敗：" + (last.why || "");
          msgEl.style.color = last.ok ? "#166534" : "#991b1b";
          if(last.ok) setTimeout(() => location.reload(), 1200);
        }} else {{
          msgEl.textContent = "完了。再読み込みします…";
          setTimeout(() => location.reload(), 1200);
        }}
      }}
    }}).catch(()=>{{ msgEl.textContent = "状況の取得に失敗（通信エラー）"; }});
  }}
  return {{
    start(fid, kind){{
      const msg = document.getElementById("run-msg-" + fid);
      msg.textContent = "開始しています…";
      fetch("/run-phase", {{method:"POST", headers:{{"Content-Type":"application/json"}},
        body: JSON.stringify({{kind: kind, func_id: fid}})}})
        .then(r => r.json().then(d => ({{status:r.status, d}})))
        .then(({{status, d}}) => {{
          if(!d.ok){{ msg.textContent = "開始できません: " + d.message; msg.style.color = "#991b1b"; return; }}
          poll(fid, msg, 0);
        }})
        .catch(e => {{ msg.textContent = "通信エラー: " + e; msg.style.color = "#991b1b"; }});
    }}
  }};
}})();
</script>
```"""


def _build_sphinx_if_needed(root: Path) -> None:
    """④完了後、docs-sphinx があれば「新コード詳細(API)」を作り直す。

    review_actions._refresh は Quarto（WBS・仕様書サイト）しか更新しないので、
    ④の成果物であるdocstringから作るAPI詳細はここで別途面倒を見る
    （MCPの render_site ツールの with_sphinx と同じ2段構え: index.rst 再生成 → build）。
    docs-sphinx が無いプロジェクト（未導入）では何もしない。
    """
    if not (root / "docs-sphinx").exists():
        return
    scripts = Path(__file__).resolve().parent
    subprocess.run([sys.executable, str(scripts / "ledger.py"), "--root", str(root), "sphinx-index"],
                   capture_output=True)
    subprocess.run([sys.executable, "-m", "sphinx", "-b", "html", "docs-sphinx",
                    "docs/_site/api", "-q"], cwd=str(root), capture_output=True)


def _run_background(root: Path, fid: str, kind: str) -> None:
    try:
        _run_background_inner(root, fid, kind)
    finally:
        _release_lock(root)   # 実行中ずっと保持し、完了/異常終了のどちらでも必ず外す


def _run_background_inner(root: Path, fid: str, kind: str) -> None:
    cfg = KINDS[kind]
    status = pipeline.RunStatus(root, f"{cfg['label']}（ブラウザ単発）", 1, RUN_DEFAULTS)
    try:
        claude_cmd = pipeline.find_claude(None)
        pipeline.preflight_claude(claude_cmd)
    except SystemExit as e:
        status.result(fid, False, str(e), "claude起動不可", 0, 0.0, {}, 1)
        status.finish("stopped", "claude 起動に失敗")
        return
    try:
        ok, why, r, cost, _ = pipeline.run_one(
            fid, claude_cmd, [], root, cfg["prompt_template"], RUN_DEFAULTS.max_turns,
            RUN_DEFAULTS.timeout, cfg.get("retries", RUN_DEFAULTS.retries), RUN_DEFAULTS.backoff_base,
            RUN_DEFAULTS.backoff_max, RUN_DEFAULTS.rate_wait_total, status, 0.0, 0,
            verify_fn=cfg["verify_fn"])
        status.counts(1 if ok else 0, 0 if ok else 1)
    except KeyboardInterrupt:
        status.finish("stopped", "レート待機の累計上限に到達")
        return
    except Exception as e:                        # noqa: BLE001 — バックグラウンドで例外を握り潰さない
        status.result(fid, False, f"内部エラー: {e}", "内部エラー", 0, 0.0, {}, 1)
        status.finish("stopped", f"内部エラー: {e}")
        return

    # state を finished にする前にサイト側を実際に更新し切る。先に finished にすると、
    # ページ側のポーリング（trigger_widget_html の JS）が「終わった」と判断して
    # まだ古いままのページを reload してしまう（承認ウィジェットへの切替が反映されない）。
    review_actions._refresh(str(root), "spec")
    # ②が新規に作られた場合、一斉レビュー表の対象は①のみなので report は上で足りる
    if kind == "impl" and ok:
        _build_sphinx_if_needed(root)   # ④完了 → 新コード詳細(API)を作り直す
    status.finish("finished", "" if ok else f"検証NG: {why}")


def start(root: str, fid: str, kind: str = "spec") -> dict:
    if kind not in KINDS:
        return {"ok": False, "message": f"不明な種別: {kind}"}
    cfg = KINDS[kind]
    rootp = Path(root).resolve()
    running = _current_run_state(rootp)
    if running:
        cur = (running.get("current") or {}).get("func_id")
        return {"ok": False,
                "message": f"既に実行中です（{cur or '不明'}）。完了を待つか /pipeline.html を確認してください"}
    # ここから実際にスレッドを起動するまでの間に別のリクエストが割り込む隙間があるため、
    # 上のチェックだけでなく原子的なロックでも縛る（二重起動の実害を防ぐ本命はこちら）
    if not _acquire_lock(rootp):
        return {"ok": False,
                "message": "他の実行が開始処理中です。数秒待ってから再試行してください"}
    started = False
    try:
        p = Project(rootp)
        try:
            p.func(fid)
        except SystemExit:
            return {"ok": False, "message": f"{fid} が functions.json に存在しない"}
        # 直前のクリック競合等に備え、サーバ側でも実行条件（trigger_widget_html と同じ判定）を再確認する
        if _decide_kind(rootp, fid) != kind:
            return {"ok": False,
                    "message": f"{cfg['label']}を実行できる状態ではありません（既に着手済みか、他で更新されています）"}
        try:
            pipeline.preflight_claude(pipeline.find_claude(None))
        except SystemExit as e:
            return {"ok": False, "message": str(e)}
        threading.Thread(target=_run_background, args=(rootp, fid, kind), daemon=True).start()
        started = True
        return {"ok": True, "message": f"{fid} の{cfg['label']}実行を開始しました"}
    finally:
        # 起動に成功した場合はロックの解放をバックグラウンドスレッド側（_run_background）に委ねる。
        # 早期returnした場合（検証NG・func未存在等）はここで必ず解放する
        if not started:
            _release_lock(rootp)


# ---------- ⑥完了検証（LLM不要・全関数横断なので①〜⑤とは別の仕組み） ----------
#
# ⑥は headless Claude を呼ばない純粋な機械チェック（ledger check）で、
# 数秒〜数十秒で終わる。①〜⑤のような「バックグラウンドスレッド起動＋
# ポーリングで進捗表示」は不要——POSTをブロックしたまま同期的に実行して結果を返す。
# ただし①〜⑤の実行中に割り込んで整合性の低い状態を検証しても意味が薄いので、
# 排他ロックだけは共有する。

def check_widget_html(root: str) -> str | None:
    """WBSトップページ（docs/index.qmd）に出す⑥実行ボタン。常に表示する
    （未完了でも「今どれだけ足りないか」の一覧を見る用途で実行してよい設計のため）。
    functions.json が無い等の異常時は None。
    """
    rootp = Path(root).resolve()
    if not (rootp / "data" / "functions.json").exists():
        return None
    cc = rootp / "docs" / "completion-check.md"
    status_line = "<p>まだ実行されていません。</p>"
    if cc.exists():
        fm = parse_frontmatter(cc.read_text(encoding="utf-8-sig"))
        st = fm.get("status")
        if st == "pass":
            status_line = '<p style="color:#166534">前回の結果: ✅ pass</p>'
        elif st == "fail":
            status_line = ('<p style="color:#991b1b">前回の結果: ❌ fail'
                           '（<a href="completion-check.html">不備一覧</a>）</p>')
    return f"""```{{=html}}
<div id="run-check" class="lr-run-widget"
     style="border:2px solid #0891b2;border-radius:10px;padding:14px 18px;margin:14px 0 22px;
            background:#ecfeff;font-family:system-ui,sans-serif">
  <div style="font-weight:700;margin-bottom:6px">⑥完了検証</div>
  {status_line}
  <button onclick="lrCheck.start()"
          style="background:#0891b2;color:#fff;border:0;border-radius:6px;padding:8px 16px;
                 font-weight:600;cursor:pointer">⑥を実行する</button>
  <span id="run-check-msg" style="margin-left:10px;font-size:.9rem"></span>
</div>
<script>
window.lrCheck = window.lrCheck || {{
  start(){{
    const msg = document.getElementById("run-check-msg");
    msg.textContent = "実行中…（数十秒かかることがあります）";
    fetch("/run-check", {{method:"POST"}})
      .then(r => r.json().then(d => ({{status:r.status, d}})))
      .then(({{status, d}}) => {{
        msg.textContent = d.message || (d.ok ? "完了" : "失敗");
        msg.style.color = d.ok ? "#166534" : "#991b1b";
        if(d.ok) setTimeout(() => location.reload(), 800);
      }})
      .catch(e => {{ msg.textContent = "通信エラー: " + e; msg.style.color = "#991b1b"; }});
  }}
}};
</script>
```"""


def run_check(root: str) -> dict:
    """⑥完了検証（ledger check）を実行し、結果に応じてサイトを更新する。同期処理。"""
    rootp = Path(root).resolve()
    if _current_run_state(rootp):
        return {"ok": False, "message": "①〜⑤が実行中です。完了を待ってから実行してください"}
    if not _acquire_lock(rootp):
        return {"ok": False, "message": "他の実行が開始処理中です。数秒待ってから再試行してください"}
    try:
        scripts = Path(__file__).resolve().parent
        r = subprocess.run([sys.executable, str(scripts / "ledger.py"), "--root", str(rootp), "check"],
                           capture_output=True, text=True)
        passed = r.returncode == 0
        subprocess.run([sys.executable, str(scripts / "render_site.py"), "--root", str(rootp)],
                       capture_output=True)
        return {"ok": True, "passed": passed,
                "message": "⑥完了検証: ✅ pass 🎉" if passed
                           else "⑥完了検証: ❌ fail（不備一覧は completion-check を参照）"}
    finally:
        _release_lock(rootp)
