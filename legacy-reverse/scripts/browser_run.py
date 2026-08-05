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

# kind ごとの設定。① は骨子(skeleton)ページに、② は reviewed 済みだが
# テスト仕様書がまだ無い仕様書ページにボタンを出す
KINDS = {
    "spec": {"label": "①", "prompt_template": "/legacy-1-spec {fid}",
            "verify_fn": pipeline.verify_spec, "doc_dir": "specs"},
    "testspec": {"label": "②", "prompt_template": "/legacy-2-testspec {fid}",
                "verify_fn": pipeline.verify_testspec, "doc_dir": "test-specs"},
}


def _status_path(root: Path) -> Path:
    return root / ".legacy-reverse" / "pipeline-status.json"


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
    """このページに出す実行ボタンの種別を決める。①未着手 or ②未着手、どちらでもなければ None。

    ②のトリガーは①ページ（specs/<fid>.md）に出す——②の成果物自体は②が動いた
    時点で初めて作られるため、着手前に埋め込める既存ページが①側しかない。
    """
    sp = root / "docs" / "specs" / f"{fid}.md"
    if not sp.exists():
        return None
    sfm = parse_frontmatter(sp.read_text(encoding="utf-8-sig"))
    if sfm.get("status") == "skeleton":
        return "spec"
    if sfm.get("status") == "reviewed" and not (root / "docs" / "test-specs" / f"{fid}.md").exists():
        return "testspec"
    return None


def trigger_widget_html(root: str, fid: str) -> str | None:
    """①未着手 or ②未着手のときだけ実行ボタンを返す。それ以外（着手済み等）は None。"""
    rootp = Path(root).resolve()
    kind = _decide_kind(rootp, fid)
    if not kind:
        return None
    cfg = KINDS[kind]
    label = cfg["label"]
    noun = "仕様書" if kind == "spec" else "テスト仕様書"
    return f"""```{{=html}}
<div id="run-{fid}" class="lr-run-widget"
     style="border:2px solid #0891b2;border-radius:10px;padding:14px 18px;margin:0 0 22px;
            background:#ecfeff;font-family:system-ui,sans-serif">
  <div style="font-weight:700;margin-bottom:6px">{label}{noun} — 未着手（{fid}）</div>
  <p style="margin:4px 0 10px;color:#155e75">
    headless Claude を1回起動して{noun}を作成します（数分かかります）。
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


def _run_background(root: Path, fid: str, kind: str) -> None:
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
            RUN_DEFAULTS.timeout, RUN_DEFAULTS.retries, RUN_DEFAULTS.backoff_base,
            RUN_DEFAULTS.backoff_max, RUN_DEFAULTS.rate_wait_total, status, 0.0, 0,
            verify_fn=cfg["verify_fn"])
        status.counts(1 if ok else 0, 0 if ok else 1)
        status.finish("finished", "" if ok else f"検証NG: {why}")
    except KeyboardInterrupt:
        status.finish("stopped", "レート待機の累計上限に到達")
    except Exception as e:                        # noqa: BLE001 — バックグラウンドで例外を握り潰さない
        status.result(fid, False, f"内部エラー: {e}", "内部エラー", 0, 0.0, {}, 1)
        status.finish("stopped", f"内部エラー: {e}")
    finally:
        review_actions._refresh(str(root), "spec")   # WBS・一斉レビュー表・サイトを更新
        # ②が新規に作られた場合、一斉レビュー表の対象は①のみなので report は上で足りる


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
    return {"ok": True, "message": f"{fid} の{cfg['label']}実行を開始しました"}
