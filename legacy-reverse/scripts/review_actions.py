#!/usr/bin/env python3
"""review_actions.py — ブラウザから①②の承認・修正依頼を行う（serve_site.py が呼ぶ）。

WBS サイトを開いたまま「承認」「修正依頼」を完結させるための実処理。
承認ボタンを押した時点のクライアント側の表示を信用せず、**サーバ側で機械レビューを
再検証してから**承認する（NG付きの成果物を承認しない、という原則をUIでも強制する）。

呼び出し元は2つ:
  - render_site.py: 仕様書ページに埋め込む承認ウィジェット（widget_html）を作る
  - serve_site.py:   POST /review-action を受けて approve / request_changes を実行
"""
import datetime
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import review_checks  # noqa: E402
from ledger import parse_frontmatter  # noqa: E402

# kind ごとの設定（対象フェーズ・承認前後の status・機械レビュー関数）
KINDS = {
    "spec": {
        "dir": "specs", "pending_status": "draft", "approved_status": "reviewed",
        "by_field": "reviewed-by", "date_field": "reviewed-date",
        "check": review_checks.check_spec,
    },
    "testspec": {
        "dir": "test-specs", "pending_status": "generated", "approved_status": "approved",
        "by_field": "approved-by", "date_field": "approved-date",
        "check": review_checks.check_testspec,
    },
}


def _doc_path(root: str, kind: str, fid: str) -> Path:
    cfg = KINDS[kind]
    return Path(root).resolve() / "docs" / cfg["dir"] / f"{fid}.md"


def reviewable_status(kind: str) -> str:
    """このステータスの間だけ、仕様書ページに承認ウィジェットが出る。"""
    return KINDS[kind]["pending_status"]


def _set_field(text: str, key: str, value: str) -> str:
    """フロントマターのトップレベル1キーを更新（無ければ末尾に追加）。1段ネストは非対応。"""
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == "---")
        end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return text
    pat = re.compile(rf"^{re.escape(key)}\s*:.*$")
    for i in range(start + 1, end):
        if pat.match(lines[i]):
            lines[i] = f"{key}: {value}"
            return "\n".join(lines)
    lines.insert(end, f"{key}: {value}")
    return "\n".join(lines)


def widget_html(root: str, kind: str, fid: str) -> str | None:
    """仕様書ページに埋め込む承認ウィジェット。承認待ちでなければ None（埋め込まない）。"""
    cfg = KINDS[kind]
    p = _doc_path(root, kind, fid)
    if not p.exists():
        return None
    fm = parse_frontmatter(p.read_text(encoding="utf-8-sig"))
    if fm.get("status") != cfg["pending_status"]:
        return None
    r = cfg["check"](root, fid)
    label = "①仕様書" if kind == "spec" else "②テスト仕様書"

    approve_btn_style = (
        "background:#4f46e5;color:#fff;border:0;border-radius:6px;padding:8px 16px;"
        "font-weight:600;cursor:pointer")
    if r["ok"]:
        review_box = '<p style="color:#166534">✅ 機械レビュー: 問題なし。内容を確認して承認してください。</p>'
        approve_disabled = ""
    else:
        items = "".join(f"<li>{_esc(p_)}</li>" for p_ in r["problems"])
        review_box = (
            '<div style="background:#fee2e2;border-radius:8px;padding:10px 14px;color:#991b1b">'
            f"<b>❌ 機械レビューNG（{len(r['problems'])}件）— AI が自己修正するまで承認できません</b>"
            f"<ul style='margin:6px 0 0'>{items}</ul></div>")
        approve_disabled = "disabled title='機械レビューNGが解消されるまで承認できません'"
        # disabled 属性だけだとブラウザの見た目がボタン色に埋もれて分かりにくいので、
        # グレーアウト＋カーソル変更で「押せない」ことを一目で分かるようにする
        approve_btn_style = (
            "background:#cbd5e1;color:#64748b;border:0;border-radius:6px;padding:8px 16px;"
            "font-weight:600;cursor:not-allowed")

    warn_box = ""
    if r.get("warnings"):
        warn_box = ("<p style='color:#854d0e'>⚠ " + "<br>".join(_esc(w) for w in r["warnings"])
                    + "</p>")

    return f"""```{{=html}}
<div id="review-{fid}" class="lr-review-widget"
     style="border:2px solid #4f46e5;border-radius:10px;padding:14px 18px;margin:0 0 22px;
            background:#f5f5ff;font-family:system-ui,sans-serif">
  <div style="font-weight:700;margin-bottom:8px">{label}レビュー — {fid}（現在: {fm.get("status")}）</div>
  {review_box}
  {warn_box}
  <div style="margin-top:10px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
    <button {approve_disabled} onclick="lrReview.approve('{fid}','{kind}',this)"
            style="{approve_btn_style}">承認する</button>
    <button onclick="lrReview.toggleFeedback('{fid}')"
            style="background:#fff;border:1px solid #4f46e5;color:#4f46e5;border-radius:6px;
                   padding:8px 16px;cursor:pointer">修正依頼…</button>
    <span id="review-msg-{fid}" style="font-size:.9rem"></span>
  </div>
  <div id="review-fb-{fid}" style="display:none;margin-top:10px">
    <textarea id="review-fb-text-{fid}" rows="3" style="width:100%;box-sizing:border-box"
              placeholder="修正してほしい内容を書く（例: 端数処理の丸め規則が本文に無い）"></textarea>
    <button onclick="lrReview.requestChanges('{fid}','{kind}',this)"
            style="margin-top:6px;background:#dc2626;color:#fff;border:0;border-radius:6px;
                   padding:6px 14px;cursor:pointer">この内容で修正依頼を送る</button>
  </div>
</div>
<script>
window.lrReview = window.lrReview || (function(){{
  function approver(){{
    let n = localStorage.getItem("lr_approver");
    if(!n){{ n = prompt("承認者名を入力してください（次回から省略されます）") || "unknown";
             localStorage.setItem("lr_approver", n); }}
    return n;
  }}
  function post(body, msgEl){{
    msgEl.textContent = "処理中…";
    return fetch("/review-action", {{method:"POST",
      headers:{{"Content-Type":"application/json"}}, body: JSON.stringify(body)}})
      .then(r => r.json().then(d => ({{status:r.status, d}})))
      .then(({{status, d}}) => {{
        msgEl.textContent = d.message || (d.ok ? "完了" : "失敗");
        msgEl.style.color = d.ok ? "#166534" : "#991b1b";
        if(d.ok) setTimeout(() => location.reload(), 700);
      }})
      .catch(e => {{ msgEl.textContent = "通信エラー: " + e
                     + "（serve_site.py で配信していますか？）"; msgEl.style.color = "#991b1b"; }});
  }}
  return {{
    approve(fid, kind, btn){{
      const msg = document.getElementById("review-msg-" + fid);
      post({{action:"approve", kind, func_id: fid, approver: approver()}}, msg);
    }},
    toggleFeedback(fid){{
      const box = document.getElementById("review-fb-" + fid);
      box.style.display = box.style.display === "none" ? "block" : "none";
    }},
    requestChanges(fid, kind, btn){{
      const msg = document.getElementById("review-msg-" + fid);
      const text = document.getElementById("review-fb-text-" + fid).value.trim();
      if(!text){{ msg.textContent = "内容を入力してください"; msg.style.color = "#991b1b"; return; }}
      post({{action:"request_changes", kind, func_id: fid, approver: approver(),
             comment: text}}, msg);
    }}
  }};
}})();
</script>
```"""


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _refresh(root: str, kind: str) -> None:
    """WBS・（①なら）一斉レビュー表・サイトを更新する（差分レンダなので数秒）。"""
    scripts = Path(__file__).resolve().parent
    root_p = str(Path(root).resolve())
    if kind == "spec":
        review_checks.make_report(root_p)
    subprocess.run([sys.executable, str(scripts / "ledger.py"), "--root", root_p, "wbs"],
                   capture_output=True)
    subprocess.run([sys.executable, str(scripts / "render_site.py"), "--root", root_p],
                   capture_output=True)


def approve(root: str, kind: str, fid: str, approver: str) -> dict:
    if kind not in KINDS:
        return {"ok": False, "message": f"不明な種別: {kind}"}
    cfg = KINDS[kind]
    p = _doc_path(root, kind, fid)
    if not p.exists():
        return {"ok": False, "message": f"{p} が見つからない"}
    text = p.read_text(encoding="utf-8-sig")
    fm = parse_frontmatter(text)
    if fm.get("status") != cfg["pending_status"]:
        return {"ok": False,
                "message": f"status が {cfg['pending_status']} でない（現在: {fm.get('status')}）"
                           "。既に承認済みか、他で更新されています"}
    # クライアント表示を信用せず、サーバ側で機械レビューを再検証する
    r = cfg["check"](root, fid)
    if not r["ok"]:
        return {"ok": False,
                "message": "機械レビューNGのため承認できません: " + " / ".join(r["problems"][:3])}

    today = datetime.date.today().isoformat()
    text = _set_field(text, "status", cfg["approved_status"])
    text = _set_field(text, cfg["by_field"], f'"{approver}"')
    text = _set_field(text, cfg["date_field"], f'"{today}"')
    p.write_text(text, encoding="utf-8")
    _refresh(root, kind)
    return {"ok": True, "message": f"{fid} を {cfg['approved_status']} にしました"}


def request_changes(root: str, kind: str, fid: str, approver: str, comment: str) -> dict:
    if kind not in KINDS:
        return {"ok": False, "message": f"不明な種別: {kind}"}
    if not comment.strip():
        return {"ok": False, "message": "修正内容が空です"}
    p = _doc_path(root, kind, fid)
    if not p.exists():
        return {"ok": False, "message": f"{p} が見つからない"}
    fb = Path(root).resolve() / "docs" / "review-feedback.md"
    fb.parent.mkdir(parents=True, exist_ok=True)
    if not fb.exists():
        fb.write_text(
            "# 修正依頼（ブラウザからの人の指摘）\n\n"
            "<!-- serve_site.py の /review-action が追記する。各フェーズ skill は起動時に\n"
            "     状態: pending の項目を確認・反映し、状態: applied に書き換える -->\n",
            encoding="utf-8")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with fb.open("a", encoding="utf-8") as f:
        f.write(f"\n## {fid}（{kind}）\n"
                f"- 起票: {now} / {approver}\n"
                "- 状態: pending\n"
                f"- 内容: {comment.strip()}\n")
    return {"ok": True, "message": f"{fid} への修正依頼を送りました（次回の① AI 実行時に反映されます）"}
