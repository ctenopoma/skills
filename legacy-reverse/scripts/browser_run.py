#!/usr/bin/env python3
"""browser_run.py — ①〜⑥の実行をブラウザから1関数だけトリガーする。

チャットで `/legacy-1-spec F-xxxx` 等と打つのと同じ処理（headless Claude を
1回起動して成果物を作る）を、WBSのページに出るボタンから起動できるようにする。
**チャット駆動の既存フローを置き換えるものではない**——同じ pipeline.py の
実行ロジック（run_one）を共有する、もう一つの入口。

排他制御は pipeline.py の実行スロットロック（.legacy-reverse/run.lock）を共有する。
バッチ（pipeline.py）とブラウザ単発が同じロックを取り合うので、どちらの方向からも
二重起動しない。ロックには PID が入っており、クラッシュの残骸は自動解除される。

serve_site.py から呼ばれる:
  - trigger_widget_html(root, fid): ①〜⑤未着手の仕様書ページに埋め込むボタン
  - start(root, fid, kind): POST /run-phase の実処理（バックグラウンドスレッドで起動して即返す）
  - cancel(root):           POST /run-cancel（実行中の単発を中止）
  - run_check(root):        POST /run-check（⑥完了検証。同期）
  - shutdown():             serve_site 終了時の後片付け（孤児プロセス・ロック残留の防止）
"""
import json
import os
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline  # noqa: E402  run_one / RunStatus / ロック / find_claude / verify_* を共用する
import review_actions  # noqa: E402  完了後の WBS・サイト更新を共用する
import review_checks  # noqa: E402  再実行可否（機械レビューNGが残っているか）の判定に使う
from ledger import Project, parse_frontmatter  # noqa: E402

# バッチと同じ既定値（pipeline.RUN_ARG_DEFAULTS を参照して乖離を防ぐ）。
# ただしレート待機だけは短くする——単発は人がボタンを押して待っている対話的な
# 操作なので、バッチの6時間ではなく15分で諦めて「今は混んでいる」ことを返す
# （待機中でも中止ボタンで打ち切れる）。
RUN_DEFAULTS = types.SimpleNamespace(
    **{**pipeline.RUN_ARG_DEFAULTS, "rate_wait_total": 900},
    model=None, skip_permissions=False, claude_args=[],
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

# 実行中のブラウザ単発（このプロセス内で高々1つ。プロセス間の排他は run.lock が担う）
_ACTIVE_MU = threading.Lock()
_ACTIVE: dict = {}       # {"fid", "kind", "cancel": Event, "thread": Thread}


# --- Project のロードキャッシュ -------------------------------------------
#
# レンダ時は全仕様書ページからウィジェット判定が呼ばれる。ページごとに
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
           _file_sig(root / "data" / "ledger.json"))
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


def _decide_kind(root: Path, fid: str, include_rerun: bool = False) -> str | None:
    """この関数の①仕様書ページに出す実行ボタンの種別を決める。①〜⑤のうち
    次に着手すべきものを1つ返す（無ければ None＝ボタンを出さない）。

    ①draft中・②generated中は承認ウィジェット（review_actions）側が担当するので
    通常は対象外（承認待ちの間に実行ボタンを並べて二重導線にしない）。
    include_rerun=True のとき（start() のサーバ側検証と連続実行バッチ）は、
    修正依頼(pending)・機械レビューNGが残る draft/generated の再実行も許可する
    （承認ウィジェット内の「再実行」ボタンとバッチの自動修復がこれに乗る）。
    """
    sp = root / "docs" / "specs" / f"{fid}.md"
    if not sp.exists():
        return None
    sfm = parse_frontmatter(sp.read_text(encoding="utf-8-sig"))
    status = sfm.get("status")
    if status == "skeleton":
        return "spec"
    if status == "draft":
        if include_rerun and _rerun_wanted(root, fid, "spec"):
            return "spec"
        return None                          # 承認待ちは承認ウィジェットの担当
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
        return None                          # generated中（②承認待ち）も承認ウィジェットの担当
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
        return None                          # ⑤裁定待ち。再実行は空実行になるので出さない
    if not s["test_ok"]:
        return "test"
    return None


# --- ウィジェットの共有 JS ------------------------------------------------
#
# 3種のウィジェット（実行・⑥・承認）の fetch/ポーリング処理を1ファイルに集約する。
# render_site.py がレンダ後に docs/_site/lr-widgets.js として書き出し、各ウィジェットの
# HTML は <script src="/lr-widgets.js"> で参照する（ページごとの複製をやめ、
# 修正が1箇所で済むようにする）。サイトルート配信（serve_site / EXE / http.server）
# のどれでも絶対パス /lr-widgets.js で解決できる。

WIDGETS_JS = """\
(function(){
  if(window.lrWidgets) return;          // 同一ページに複数ウィジェットがあっても1回だけ
  window.lrWidgets = true;

  function jsonPost(url, body){
    return fetch(url, {method:"POST", headers:{"Content-Type":"application/json"},
                       body: JSON.stringify(body || {})})
      .then(r => r.json());
  }

  // ---- ①〜⑤ 単発実行 ----
  window.lrRun = {
    start(fid, kind){
      const msg = document.getElementById("run-msg-" + fid);
      const cancelBtn = document.getElementById("run-cancel-" + fid);
      msg.style.color = ""; msg.textContent = "開始しています…";
      const t0 = Date.now();
      jsonPost("/run-phase", {kind: kind, func_id: fid})
        .then(d => {
          if(!d.ok){ msg.textContent = "開始できません: " + d.message;
                     msg.style.color = "#991b1b"; return; }
          if(cancelBtn) cancelBtn.style.display = "";
          poll(fid, msg, cancelBtn, t0, 0, 0);
        })
        .catch(e => { msg.textContent = "通信エラー: " + e; msg.style.color = "#991b1b"; });
    },
    cancel(fid){
      const msg = document.getElementById("run-msg-" + fid);
      jsonPost("/run-cancel", {})
        .then(d => { msg.textContent = d.message || (d.ok ? "中止を要求しました…" : "中止できません"); })
        .catch(()=>{});
    }
  };

  function poll(fid, msg, cancelBtn, t0, quiet, errs){
    fetch("/pipeline-status.json", {cache:"no-store"}).then(r=>r.json()).then(d=>{
      if(d.state === "waiting_rate"){
        msg.textContent = "レート待機中…（詳細は /pipeline.html。中止もできます）";
        return setTimeout(() => poll(fid, msg, cancelBtn, t0, 0, 0), 3000);
      }
      if(d.state === "running"){
        // current が未設定でも running なら実行準備中。「完了」と誤認しない
        if(d.current && d.current.func_id === fid){
          const el = Math.max(0, Math.round((Date.now() - new Date(d.current.started_at))/1000));
          msg.textContent = "実行中…（" + Math.floor(el/60) + "分" + el%60 + "秒経過。"
            + "詳細は /pipeline.html）";
        } else {
          msg.textContent = "起動中…";
        }
        return setTimeout(() => poll(fid, msg, cancelBtn, t0, 0, 0), 3000);
      }
      // 実行が終わった。「この実行」の結果は fid ＋ 開始時刻で照合する
      //（以前の実行が recent に残していた同じ fid のエントリを結果と誤認しないため）
      const last = (d.recent || []).find(r => r.func_id === fid
                                             && Date.parse(r.at) >= t0 - 15000);
      if(last){
        if(cancelBtn) cancelBtn.style.display = "none";
        msg.textContent = last.ok ? "完了：検証 OK。再読み込みします…"
                                  : "失敗：" + (last.why || "");
        msg.style.color = last.ok ? "#166534" : "#991b1b";
        if(last.ok) setTimeout(() => location.reload(), 1200);
        return;
      }
      if(quiet < 5)     // 起動直後（スレッドが状態を書く前）の空白は待って再確認
        return setTimeout(() => poll(fid, msg, cancelBtn, t0, quiet + 1, 0), 1500);
      if(cancelBtn) cancelBtn.style.display = "none";
      msg.textContent = "実行状態を確認できませんでした（/pipeline.html を参照）";
      msg.style.color = "#991b1b";
    }).catch(() => {
      if(errs < 5)      // 一時的な通信エラーで進捗表示を止めない
        return setTimeout(() => poll(fid, msg, cancelBtn, t0, quiet, errs + 1), 3000);
      msg.textContent = "状況の取得に失敗（通信エラーが続いています）";
      msg.style.color = "#991b1b";
    });
  }

  // ---- ⑥ 完了検証 ----
  window.lrCheck = {
    start(){
      const msg = document.getElementById("run-check-msg");
      msg.style.color = ""; msg.textContent = "実行中…（数十秒かかることがあります）";
      jsonPost("/run-check", {})
        .then(d => {
          msg.textContent = d.message || (d.ok ? "完了" : "失敗");
          msg.style.color = d.ok ? "#166534" : "#991b1b";
          if(d.ok) setTimeout(() => location.reload(), 800);
        })
        .catch(e => { msg.textContent = "通信エラー: " + e; msg.style.color = "#991b1b"; });
    }
  };

  // ---- ①② 承認・修正依頼 ----
  function approver(){
    let n = localStorage.getItem("lr_approver");
    if(!n){ n = prompt("承認者名を入力してください（次回から省略されます）") || "unknown";
            localStorage.setItem("lr_approver", n); }
    return n;
  }
  function reviewPost(body, msgEl){
    msgEl.textContent = "処理中…";
    jsonPost("/review-action", body)
      .then(d => {
        msgEl.textContent = d.message || (d.ok ? "完了" : "失敗");
        msgEl.style.color = d.ok ? "#166534" : "#991b1b";
        if(d.ok) setTimeout(() => location.reload(), 700);
      })
      .catch(e => { msgEl.textContent = "通信エラー: " + e
                    + "（serve_site.py で配信していますか？）"; msgEl.style.color = "#991b1b"; });
  }
  window.lrReview = {
    approve(fid, kind){
      reviewPost({action:"approve", kind: kind, func_id: fid, approver: approver()},
                 document.getElementById("review-msg-" + fid));
    },
    toggleFeedback(fid){
      const box = document.getElementById("review-fb-" + fid);
      box.style.display = box.style.display === "none" ? "block" : "none";
    },
    requestChanges(fid, kind){
      const msg = document.getElementById("review-msg-" + fid);
      const text = document.getElementById("review-fb-text-" + fid).value.trim();
      if(!text){ msg.textContent = "内容を入力してください"; msg.style.color = "#991b1b"; return; }
      reviewPost({action:"request_changes", kind: kind, func_id: fid,
                  approver: approver(), comment: text}, msg);
    }
  };

  // ---- ⑤裁定（ISSUE回答 → unblock） ----
  window.lrAdj = {
    submit(fid, issueId){
      const msg = document.getElementById("adj-msg-" + fid);
      const text = document.getElementById("adj-text-" + fid).value.trim();
      if(!text){ msg.textContent = "回答を入力してください"; msg.style.color = "#991b1b"; return; }
      reviewPost({action:"adjudicate", func_id: fid, issue_id: issueId,
                  approver: approver(), comment: text}, msg);
    }
  };

  // ---- ⑦分析（定量評価 → 施策候補の提案。適用はしない） ----
  window.lrAnalyze = {
    start(){
      const msg = document.getElementById("run-msg-analyze");
      const cancelBtn = document.getElementById("run-cancel-analyze");
      msg.style.color = ""; msg.textContent = "開始しています…（計測 → 提案生成）";
      const t0 = Date.now();
      jsonPost("/run-analyze", {})
        .then(d => {
          if(!d.ok){ msg.textContent = "開始できません: " + d.message;
                     msg.style.color = "#991b1b"; return; }
          if(cancelBtn) cancelBtn.style.display = "";
          poll("analyze", msg, cancelBtn, t0, 0, 0);
        })
        .catch(e => { msg.textContent = "通信エラー: " + e; msg.style.color = "#991b1b"; });
    },
    cancel(){ window.lrRun.cancel("analyze"); }
  };
})();
"""


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
  <button id="run-cancel-{fid}" onclick="lrRun.cancel('{fid}')"
          style="display:none;background:#fff;border:1px solid #991b1b;color:#991b1b;
                 border-radius:6px;padding:8px 16px;margin-left:8px;cursor:pointer">中止</button>
  <span id="run-msg-{fid}" style="margin-left:10px;font-size:.9rem"></span>
</div>
<script src="/lr-widgets.js"></script>
```"""


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


def _run_background(root: Path, fid: str, kind: str, claude_cmd: list,
                    cancel_event: threading.Event) -> None:
    try:
        _run_background_inner(root, fid, kind, claude_cmd, cancel_event)
    finally:
        with _ACTIVE_MU:
            _ACTIVE.clear()
        pipeline.release_run_lock(root)   # 実行中ずっと保持し、完了/異常終了のどちらでも必ず外す


def _run_background_inner(root: Path, fid: str, kind: str, claude_cmd: list,
                          cancel_event: threading.Event) -> None:
    cfg = KINDS[kind]
    status = pipeline.RunStatus(root, f"{cfg['label']}（ブラウザ単発）", 1, RUN_DEFAULTS)
    try:
        ok, why, r, cost, _ = pipeline.run_one(
            fid, claude_cmd, [], root, cfg["prompt_template"], RUN_DEFAULTS.max_turns,
            RUN_DEFAULTS.timeout, cfg.get("retries", RUN_DEFAULTS.retries), RUN_DEFAULTS.backoff_base,
            RUN_DEFAULTS.backoff_max, RUN_DEFAULTS.rate_wait_total, status, 0.0, 0,
            verify_fn=cfg["verify_fn"], cancel_event=cancel_event)
        status.counts(1 if ok else 0, 0 if ok else 1)
    except KeyboardInterrupt:
        status.finish("stopped", "レート待機の累計上限に到達")
        return
    except Exception as e:                        # noqa: BLE001 — バックグラウンドで例外を握り潰さない
        status.result(fid, False, f"内部エラー: {e}", "内部エラー", 0, 0.0, {}, 1)
        status.finish("stopped", f"内部エラー: {e}")
        return

    # state を finished にする前にサイト側を実際に更新し切る。先に finished にすると、
    # ページ側のポーリング（WIDGETS_JS）が「終わった」と判断して
    # まだ古いままのページを reload してしまう（承認ウィジェットへの切替が反映されない）。
    refreshed = review_actions.refresh_site(str(root), "spec")
    # ②が新規に作られた場合、一斉レビュー表の対象は①のみなので report は上で足りる
    if kind == "impl" and ok:
        _build_sphinx_if_needed(root)   # ④完了 → 新コード詳細(API)を作り直す
    if why == "中止された":
        status.finish("stopped", "中止された")
        return
    reason = "" if ok else f"検証NG: {why}"
    if not refreshed:
        reason = (reason + "／" if reason else "") + "サイト更新に失敗（render_site.py を手動実行）"
    status.finish("finished", reason)


def start(root: str, fid: str, kind: str = "spec") -> dict:
    if kind not in KINDS:
        return {"ok": False, "message": f"不明な種別: {kind}"}
    cfg = KINDS[kind]
    rootp = Path(root).resolve()
    running = pipeline.current_run_state(rootp)
    if running:
        cur = (running.get("current") or {}).get("func_id")
        return {"ok": False,
                "message": f"既に実行中です（{running.get('mode')}"
                           + (f"・{cur}" if cur else "")
                           + "）。完了を待つか /pipeline.html を確認してください"}
    # ここから実際にスレッドを起動するまでの間に別のリクエストやバッチが割り込む
    # 隙間があるため、上のチェックだけでなく原子的なロックでも縛る（本命はこちら）
    if not pipeline.acquire_run_lock(rootp, f"browser-{kind}"):
        return {"ok": False,
                "message": "別の実行（バッチまたはブラウザ単発）が進行中です。"
                           "/pipeline.html で状況を確認してください"}
    started = False
    try:
        p = _project(rootp)
        try:
            p.func(fid)
        except SystemExit:
            return {"ok": False, "message": f"{fid} が functions.json に存在しない"}
        # 直前のクリック競合等に備え、サーバ側でも実行条件を再確認する
        # （include_rerun=True: 承認ウィジェット内の「再実行」ボタン経由も受け付ける）
        if _decide_kind(rootp, fid, include_rerun=True) != kind:
            return {"ok": False,
                    "message": f"{cfg['label']}を実行できる状態ではありません（既に着手済みか、他で更新されています）"}
        try:
            # preflight はここで1回だけ（スレッド側では繰り返さない。起動直後の
            # 「state=running だが current 未設定」の空白時間を短くする意味もある）
            claude_cmd = pipeline.find_claude(None)
            pipeline.preflight_claude(claude_cmd)
        except SystemExit as e:
            return {"ok": False, "message": str(e)}
        cancel_event = threading.Event()
        th = threading.Thread(target=_run_background,
                              args=(rootp, fid, kind, claude_cmd, cancel_event),
                              daemon=True)
        with _ACTIVE_MU:
            _ACTIVE.clear()
            _ACTIVE.update({"fid": fid, "kind": kind, "cancel": cancel_event, "thread": th})
        th.start()
        started = True
        return {"ok": True, "message": f"{fid} の{cfg['label']}実行を開始しました"}
    finally:
        # 起動に成功した場合はロックの解放をバックグラウンドスレッド側（_run_background）に委ねる。
        # 早期returnした場合（検証NG・func未存在等）はここで必ず解放する
        if not started:
            pipeline.release_run_lock(rootp)


# --- 連続実行（ブラウザから①〜⑤の機械実行をまとめて回す） ------------------
#
# CLI の pipeline.py（①全件バッチ）に相当する無人実行を、serve_site のプロセス内で
# 行うもう一つの入口。全関数を走査し「次に着手すべき工程」（_decide_kind。
# 修正依頼・機械NGの再実行も含む）を1件ずつ実行しては再走査する——③が終われば
# 同じ関数の④、①が draft になれば別の関数の①、と自動で進む。
# 承認待ち（draft/generated）・裁定待ち（blocked）・完了は対象外なので、
# 人はサイト上で承認・裁定だけしていけばよい。停止は /run-cancel（中止ボタン）。

def _scan_targets(root: Path, skip: set) -> list:
    p = _project(root)
    out = []
    for f in p.funcs():
        fid = f["func_id"]
        kind = _decide_kind(root, fid, include_rerun=True)
        if kind and (fid, kind) not in skip:
            out.append((fid, kind))
    return out


def batch_start(root: str, max_funcs: int = 0, budget_usd: float = 0) -> dict:
    """POST /run-batch の実処理。単発の start() と同じ枠（ロック・_ACTIVE）を使う。"""
    rootp = Path(root).resolve()
    running = pipeline.current_run_state(rootp)
    if running:
        return {"ok": False,
                "message": f"既に実行中です（{running.get('mode')}）。"
                           "完了を待つか停止してから開始してください"}
    if not pipeline.acquire_run_lock(rootp, "browser-batch"):
        return {"ok": False,
                "message": "別の実行（バッチまたはブラウザ単発）が進行中です。"
                           "/pipeline.html で状況を確認してください"}
    started = False
    try:
        if not _project(rootp).funcs():
            return {"ok": False, "message": "functions.json に対象関数がない（⓪が未実行）"}
        try:
            claude_cmd = pipeline.find_claude(None)
            pipeline.preflight_claude(claude_cmd)
        except SystemExit as e:
            return {"ok": False, "message": str(e)}
        cancel_event = threading.Event()
        th = threading.Thread(
            target=_batch_background,
            args=(rootp, claude_cmd, cancel_event, int(max_funcs or 0), float(budget_usd or 0)),
            daemon=True)
        with _ACTIVE_MU:
            _ACTIVE.clear()
            _ACTIVE.update({"fid": "連続実行", "kind": "batch",
                            "cancel": cancel_event, "thread": th})
        th.start()
        started = True
        return {"ok": True, "message": "連続実行を開始しました（承認・裁定待ちはスキップして進みます）"}
    finally:
        if not started:
            pipeline.release_run_lock(rootp)


def _batch_background(root: Path, claude_cmd: list, cancel_event: threading.Event,
                      max_funcs: int, budget_usd: float) -> None:
    try:
        _batch_background_inner(root, claude_cmd, cancel_event, max_funcs, budget_usd)
    finally:
        with _ACTIVE_MU:
            _ACTIVE.clear()
        pipeline.release_run_lock(root)


def _batch_background_inner(root: Path, claude_cmd: list, cancel_event: threading.Event,
                            max_funcs: int, budget_usd: float) -> None:
    ns = types.SimpleNamespace(**vars(RUN_DEFAULTS))
    ns.rate_wait_total = pipeline.RUN_ARG_DEFAULTS["rate_wait_total"]  # 無人前提なので6時間
    ns.budget_usd, ns.max_funcs = budget_usd, max_funcs
    skip: set = set()
    targets = _scan_targets(root, skip)
    status = pipeline.RunStatus(root, "連続実行（ブラウザ）", len(targets), ns)
    done = failed = consecutive_fail = 0
    cost_total, rate_waited = 0.0, 0
    stop_reason = ""
    try:
        while targets and not cancel_event.is_set():
            if max_funcs and done + failed >= max_funcs:
                stop_reason = f"上限 {max_funcs} 件に到達"
                break
            if budget_usd and cost_total >= budget_usd:
                stop_reason = f"予算 ${budget_usd} に到達（累計 ${cost_total:.2f}）"
                break
            fid, kind = targets[0]
            cfg = KINDS[kind]
            ok, why, r, cost_total, rate_waited = pipeline.run_one(
                fid, claude_cmd, [], root, cfg["prompt_template"], ns.max_turns,
                ns.timeout, cfg.get("retries", ns.retries), ns.backoff_base,
                ns.backoff_max, ns.rate_wait_total, status, cost_total, rate_waited,
                verify_fn=cfg["verify_fn"], cancel_event=cancel_event)
            if why == "中止された" or cancel_event.is_set():
                stop_reason = "中止された"
                break
            if ok:
                done += 1
                consecutive_fail = 0
                if kind == "impl":
                    _build_sphinx_if_needed(root)
            else:
                failed += 1
                consecutive_fail += 1
                skip.add((fid, kind))            # 同じ (関数, 工程) は再試行しない
                if consecutive_fail >= 3:
                    stop_reason = f"連続 {consecutive_fail} 件失敗（環境異常の疑い）"
                    break
            if (done + failed) % 5 == 0:
                # 承認待ちが溜まっていくので、実行中も定期的にサイトを更新して
                # 人がブラウザで承認・裁定を並行して進められるようにする
                review_actions.refresh_site(str(root), "spec")
            targets = _scan_targets(root, skip)  # 承認・裁定で新たに可能になった工程も拾う
            status.counts(done, failed, total=done + failed + len(targets))
    except KeyboardInterrupt:
        stop_reason = "レート待機の累計上限に到達"
    except Exception as e:                        # noqa: BLE001 — バックグラウンドで例外を握り潰さない
        stop_reason = f"内部エラー: {e}"

    refreshed = review_actions.refresh_site(str(root), "spec")
    status.counts(done, failed)
    if stop_reason:
        reason = stop_reason
    else:
        reason = (f"実行できる工程が無くなった（済 {done}・失敗 {failed}。"
                  "残りは承認・裁定待ちか完了）")
    if not refreshed:
        reason += "／サイト更新に失敗（render_site.py を手動実行）"
    status.finish("stopped" if stop_reason else "finished", reason)


def cancel(root: str) -> dict:
    """POST /run-cancel の実処理。実行中のブラウザ単発に中止を要求する。

    実際の停止（claude プロセスツリーの kill・ロック解放・state 更新）は
    バックグラウンドスレッド側が行う。バッチ（別プロセス）はここでは止められない。
    """
    with _ACTIVE_MU:
        act = dict(_ACTIVE)
    if not act:
        return {"ok": False,
                "message": "このサーバから開始した実行はありません（バッチは Ctrl+C で停止してください）"}
    act["cancel"].set()
    return {"ok": True, "message": f"{act['fid']} に中止を要求しました（停止まで数秒かかります）"}


def shutdown(timeout: float = 20.0) -> None:
    """serve_site.py の終了時に呼ぶ。実行中のブラウザ単発があれば中止して片付けを待つ。

    デーモンスレッドのままプロセスを落とすと finally が走らず、claude が孤児として
    走り続け・ロックも残留する。ここで cancel を立てて join することで、スレッド側の
    後片付け（プロセスツリー kill・state 書き込み・ロック解放）を完了させる。
    """
    with _ACTIVE_MU:
        act = dict(_ACTIVE)
    if not act:
        return
    print(f"\n実行中のブラウザ単発（{act['fid']}）を中止しています…")
    act["cancel"].set()
    act["thread"].join(timeout)
    if act["thread"].is_alive():
        print("warning: 実行スレッドの停止を確認できなかった（ロックが残った場合は自動解除される）")


# ---------- ⑦分析（定量評価 → LLMが施策候補を提案。適用はしない） ----------
#
# 「計測せずに提案しない」が⑦の大原則なので、2段構えにする:
#   1. quant_analyze.py（決定的・LLM不要）: cProfile＋radon/ruff/bandit/pip-audit を
#      機械実行し、.legacy-reverse/quant.json と docs/quant.md に実測データを残す
#   2. headless Claude 1回: 実測データを読ませ、docs/analysis.md に施策候補
#      （OPT-/REF-/SEC-、期待効果・リスク・優先順位）を記入させる。
#      **施策の適用・コード変更はさせない**（改善イタレーションは人の承認後、別途）
# 検証は pipeline.verify_analysis（候補が実測ベースで記入されたか）。

ANALYZE_PROMPT = (
    "/legacy-7-analyze 分析のみを実施してください。計測と静的解析は実施済みで、"
    "実測データが .legacy-reverse/quant.json・docs/quant.md・docs/perf.md にあります。"
    "再計測はせず、この実測データに基づいて docs/analysis.md に施策候補"
    "（OPT-/REF-/SEC-。期待効果・リスク・優先順位つき。数値は実測値を引用）を記入してください。"
    "quant.md にスモーク計測（bench.py 無し）とある場合は、計測サマリに"
    "「テスト負荷によるスモーク計測に基づく暫定分析」と明記してください。"
    "施策の適用・コード変更・施策票の起票はしないでください。候補が無い場合は"
    "「候補なし」と根拠つきで明記してください。")


def _run_cancellable(cmd: list, cwd: Path, timeout: int,
                     cancel_event: threading.Event) -> dict:
    """LLM以外のサブプロセス（quant_analyze.py 等）をキャンセル可能に実行する。"""
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")
    deadline = time.monotonic() + timeout
    while True:
        try:
            out, err = proc.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if cancel_event.is_set() or time.monotonic() >= deadline:
                pipeline._kill_tree(proc)
                try:
                    out, err = proc.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    out, err = "", ""
                return {"canceled": cancel_event.is_set(), "exit_code": proc.returncode,
                        "stdout": out or "", "stderr": err or ""}
    return {"canceled": False, "exit_code": proc.returncode,
            "stdout": out or "", "stderr": err or ""}


def analyze_widget_html(root: str) -> str | None:
    """WBSトップページに出す⑦分析ウィジェット（⑥の下）。functions.json が無ければ None。"""
    rootp = Path(root).resolve()
    if not (rootp / "data" / "functions.json").exists():
        return None
    cc = rootp / "docs" / "completion-check.md"
    ok6 = (cc.exists() and
           parse_frontmatter(cc.read_text(encoding="utf-8-sig")).get("status") == "pass")
    lines = []
    an = rootp / "docs" / "analysis.md"
    if an.exists():
        lines.append('<p>前回の分析: <a href="analysis.html">analysis</a>'
                     '（定量データ: <a href="quant.html">quant</a>・<a href="perf.html">perf</a>）</p>')
    else:
        lines.append("<p>まだ実行されていません。</p>")
    if (rootp / "bench.py").exists():
        lines.append('<p style="color:#166534">計測: bench.py（代表ワークロード）を使用 ✅</p>')
    else:
        lines.append('<p style="color:#854d0e">⚠ bench.py（代表ワークロード）が無いため'
                     "テスト負荷のスモーク計測になります。ルートに bench.py を置くと本命計測</p>")
    if ok6:
        btn = ('<button onclick="lrAnalyze.start()" '
               'style="background:#7c3aed;color:#fff;border:0;border-radius:6px;'
               'padding:8px 16px;font-weight:600;cursor:pointer">⑦分析を実行する</button>')
    else:
        lines.append('<p style="color:#991b1b">前提: ⑥完了検証が pass していること（上の⑥から実行）</p>')
        btn = ('<button disabled title="⑥がpassすると実行できます" '
               'style="background:#cbd5e1;color:#64748b;border:0;border-radius:6px;'
               'padding:8px 16px;font-weight:600;cursor:not-allowed">⑦分析を実行する</button>')
    return f"""```{{=html}}
<div id="run-analyze" class="lr-run-widget"
     style="border:2px solid #7c3aed;border-radius:10px;padding:14px 18px;margin:14px 0 22px;
            background:#f5f3ff;font-family:system-ui,sans-serif">
  <div style="font-weight:700;margin-bottom:6px">⑦分析（定量評価 → 施策候補の提案）</div>
  {''.join(lines)}
  <p style="margin:4px 0 10px;color:#5b21b6;font-size:.9rem">
    cProfile＋静的解析（radon/ruff/bandit/pip-audit）で実測し、その数値に基づいて
    AI が施策候補（OPT-/REF-/SEC-）を analysis.md に提案します。<b>適用はしません</b>。</p>
  {btn}
  <button id="run-cancel-analyze" onclick="lrAnalyze.cancel()"
          style="display:none;background:#fff;border:1px solid #991b1b;color:#991b1b;
                 border-radius:6px;padding:8px 16px;margin-left:8px;cursor:pointer">中止</button>
  <span id="run-msg-analyze" style="margin-left:10px;font-size:.9rem"></span>
</div>
<script src="/lr-widgets.js"></script>
```"""


def analyze_start(root: str) -> dict:
    """POST /run-analyze の実処理。単発実行と同じ枠（ロック・_ACTIVE・中止）を使う。"""
    rootp = Path(root).resolve()
    cc = rootp / "docs" / "completion-check.md"
    if not (cc.exists() and
            parse_frontmatter(cc.read_text(encoding="utf-8-sig")).get("status") == "pass"):
        return {"ok": False,
                "message": "⑦の前提は⑥完了検証が pass していることです。先に⑥を実行してください"}
    running = pipeline.current_run_state(rootp)
    if running:
        return {"ok": False,
                "message": f"既に実行中です（{running.get('mode')}）。完了を待つか停止してください"}
    if not pipeline.acquire_run_lock(rootp, "browser-analyze"):
        return {"ok": False,
                "message": "別の実行が進行中です。/pipeline.html で状況を確認してください"}
    started = False
    try:
        try:
            claude_cmd = pipeline.find_claude(None)
            pipeline.preflight_claude(claude_cmd)
        except SystemExit as e:
            return {"ok": False, "message": str(e)}
        cancel_event = threading.Event()
        th = threading.Thread(target=_analyze_background,
                              args=(rootp, claude_cmd, cancel_event), daemon=True)
        with _ACTIVE_MU:
            _ACTIVE.clear()
            _ACTIVE.update({"fid": "analyze", "kind": "analyze",
                            "cancel": cancel_event, "thread": th})
        th.start()
        started = True
        return {"ok": True, "message": "⑦分析を開始しました（定量評価 → 提案生成）"}
    finally:
        if not started:
            pipeline.release_run_lock(rootp)


def _analyze_background(root: Path, claude_cmd: list,
                        cancel_event: threading.Event) -> None:
    try:
        _analyze_background_inner(root, claude_cmd, cancel_event)
    finally:
        with _ACTIVE_MU:
            _ACTIVE.clear()
        pipeline.release_run_lock(root)


def _analyze_background_inner(root: Path, claude_cmd: list,
                              cancel_event: threading.Event) -> None:
    status = pipeline.RunStatus(root, "⑦分析（ブラウザ）", 1, RUN_DEFAULTS)
    status.current("analyze", 1)               # 計測中も経過時間が見えるように
    scripts = Path(__file__).resolve().parent
    r = _run_cancellable([sys.executable, str(scripts / "quant_analyze.py"),
                          "--root", str(root)], root, 900, cancel_event)
    if r["canceled"]:
        status.result("analyze", False, "中止された", "中止", 0, 0.0, {}, 1)
        status.finish("stopped", "中止された")
        return
    if r["exit_code"] != 0:
        why = f"定量評価に失敗: {(r['stderr'] or r['stdout']).strip()[-200:]}"
        status.result("analyze", False, why, "定量評価失敗", 0, 0.0,
                      {"tail": (r["stderr"] or r["stdout"])[-400:]}, 1)
        status.finish("stopped", why)
        return
    try:
        ok, why, r2, cost, _ = pipeline.run_one(
            "analyze", claude_cmd, [], root, ANALYZE_PROMPT, RUN_DEFAULTS.max_turns,
            RUN_DEFAULTS.timeout, RUN_DEFAULTS.retries, RUN_DEFAULTS.backoff_base,
            RUN_DEFAULTS.backoff_max, RUN_DEFAULTS.rate_wait_total, status, 0.0, 0,
            verify_fn=pipeline.verify_analysis, cancel_event=cancel_event)
        status.counts(1 if ok else 0, 0 if ok else 1)
    except KeyboardInterrupt:
        status.finish("stopped", "レート待機の累計上限に到達")
        return
    except Exception as e:                        # noqa: BLE001
        status.result("analyze", False, f"内部エラー: {e}", "内部エラー", 0, 0.0, {}, 1)
        status.finish("stopped", f"内部エラー: {e}")
        return
    refreshed = review_actions.refresh_site(str(root), "analyze")
    if why == "中止された":
        status.finish("stopped", "中止された")
        return
    reason = "" if ok else f"検証NG: {why}"
    if not refreshed:
        reason = (reason + "／" if reason else "") + "サイト更新に失敗（render_site.py を手動実行）"
    status.finish("finished", reason)


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
<script src="/lr-widgets.js"></script>
```"""


def run_check(root: str) -> dict:
    """⑥完了検証（ledger check）を実行し、結果に応じてサイトを更新する。同期処理。"""
    rootp = Path(root).resolve()
    if pipeline.current_run_state(rootp):
        return {"ok": False, "message": "①〜⑤が実行中です。完了を待ってから実行してください"}
    if not pipeline.acquire_run_lock(rootp, "browser-check"):
        return {"ok": False, "message": "別の実行が進行中です。/pipeline.html で状況を確認してください"}
    try:
        scripts = Path(__file__).resolve().parent
        r = subprocess.run([sys.executable, str(scripts / "ledger.py"), "--root", str(rootp), "check"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode not in (0, 1):       # 0=pass / 1=fail。それ以外はチェック自体の異常
            return {"ok": False,
                    "message": f"⑥の実行に失敗（exit={r.returncode}）: "
                               f"{(r.stderr or r.stdout or '').strip()[-200:]}"}
        passed = r.returncode == 0
        rr = subprocess.run([sys.executable, str(scripts / "render_site.py"), "--root", str(rootp)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        note = ("" if rr.returncode == 0
                else "（注意: サイト再生成に失敗。render_site.py を手動実行してください）")
        return {"ok": True, "passed": passed,
                "message": ("⑥完了検証: ✅ pass 🎉" if passed
                            else "⑥完了検証: ❌ fail（不備一覧は completion-check を参照）") + note}
    finally:
        pipeline.release_run_lock(rootp)
