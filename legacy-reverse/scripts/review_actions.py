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
import threading
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


def pending_feedback_kinds(root: str, fid: str) -> set:
    """review-feedback.md に「状態: pending」で残っている修正依頼の kind 集合。

    ブラウザの「修正依頼を反映して再実行」ボタンの表示判定と、
    連続実行（browser_run のバッチ）が修正依頼済みの draft/generated を
    再実行対象に拾うために使う。
    """
    fb = Path(root).resolve() / "docs" / "review-feedback.md"
    if not fb.exists():
        return set()
    kinds: set = set()
    cur = None
    for line in fb.read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"##\s+(\S+)（(\w+)）", line)
        if m:
            cur = (m.group(1), m.group(2))
            continue
        if cur and cur[0] == fid and re.match(r"-\s*状態:\s*pending\b", line.strip()):
            kinds.add(cur[1])
    return kinds


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

    # 機械レビューNG・修正依頼(pending)が残っている間は、チャットに戻らなくても
    # このページから「AIに修正させて再実行」できる（headless 1回の単発実行。
    # 実行の可否はサーバ側でも browser_run._decide_kind(include_rerun=True) で再検証される）
    has_feedback = kind in pending_feedback_kinds(root, fid)
    rerun_box = ""
    if has_feedback or not r["ok"]:
        rerun_label = ("修正依頼を反映して再実行" if has_feedback
                       else "AIに自己修正させて再実行")
        rerun_box = f"""
  <div style="margin-top:10px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
    <button onclick="lrRun.start('{fid}','{kind}')"
            style="background:#0891b2;color:#fff;border:0;border-radius:6px;padding:8px 16px;
                   font-weight:600;cursor:pointer">{rerun_label}</button>
    <button id="run-cancel-{fid}" onclick="lrRun.cancel('{fid}')"
            style="display:none;background:#fff;border:1px solid #991b1b;color:#991b1b;
                   border-radius:6px;padding:8px 16px;cursor:pointer">中止</button>
    <span id="run-msg-{fid}" style="font-size:.9rem"></span>
  </div>"""

    return f"""```{{=html}}
<div id="review-{fid}" class="lr-review-widget"
     style="border:2px solid #4f46e5;border-radius:10px;padding:14px 18px;margin:0 0 22px;
            background:#f5f5ff;font-family:system-ui,sans-serif">
  <div style="font-weight:700;margin-bottom:8px">{label}レビュー — {fid}（現在: {fm.get("status")}）</div>
  {review_box}
  {warn_box}
  <div style="margin-top:10px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
    <button {approve_disabled} onclick="lrReview.approve('{fid}','{kind}')"
            style="{approve_btn_style}">承認する</button>
    <button onclick="lrReview.toggleFeedback('{fid}')"
            style="background:#fff;border:1px solid #4f46e5;color:#4f46e5;border-radius:6px;
                   padding:8px 16px;cursor:pointer">修正依頼…</button>
    <span id="review-msg-{fid}" style="font-size:.9rem"></span>
  </div>
  <div id="review-fb-{fid}" style="display:none;margin-top:10px">
    <textarea id="review-fb-text-{fid}" rows="3" style="width:100%;box-sizing:border-box"
              placeholder="修正してほしい内容を書く（例: 端数処理の丸め規則が本文に無い）"></textarea>
    <button onclick="lrReview.requestChanges('{fid}','{kind}')"
            style="margin-top:6px;background:#dc2626;color:#fff;border:0;border-radius:6px;
                   padding:6px 14px;cursor:pointer">この内容で修正依頼を送る</button>
  </div>{rerun_box}
</div>
<script src="/lr-widgets.js"></script>
```"""


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --- ⑤裁定（ISSUE回答 → unblock）をブラウザで完結させる ---------------------

def _blocked_issue(root: str, fid: str) -> str | None:
    import browser_run   # 遅延import（相互参照回避）。Project のロードキャッシュを共有する
    p = browser_run._project(Path(root).resolve())
    return (p.ledger.get(fid) or {}).get("blocked_by")


def adjudicate_widget_html(root: str, fid: str) -> str | None:
    """⑤で blocked（裁定待ち）の関数の①仕様書ページに出す裁定ウィジェット。

    ISSUE の「質問（人への問い）」をその場に表示し、回答を書いて
    「裁定を完了」を押すと ISSUE への回答記入（status: answered）→ unblock まで
    済む。チャットに戻る必要はない。blocked でなければ None。
    """
    iss = _blocked_issue(root, fid)
    if not iss:
        return None
    rootp = Path(root).resolve()
    ip = rootp / "docs" / "issues" / f"{iss}.md"
    title, question, answered = "", "", False
    if ip.exists():
        text = ip.read_text(encoding="utf-8-sig")
        ifm = parse_frontmatter(text)
        title = ifm.get("title") or ""
        answered = ifm.get("status") == "answered"
        m = re.search(r"(?ms)^#\s*質問（人への問い）\s*$(.*?)(?=^# |\Z)", text)
        if m:
            question = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S).strip()
    q_box = (f'<pre style="white-space:pre-wrap;background:#78350f14;padding:10px 14px;'
             f'border-radius:8px;font-size:.9rem;margin:8px 0">{_esc(question)}</pre>'
             if question else "")
    note = ('<p style="color:#166534">この ISSUE は回答済み（answered）です。'
            '内容を確認して裁定を完了してください。</p>' if answered else "")
    return f"""```{{=html}}
<div id="adj-{fid}" class="lr-adj-widget"
     style="border:2px solid #b45309;border-radius:10px;padding:14px 18px;margin:0 0 22px;
            background:#fffbeb;font-family:system-ui,sans-serif">
  <div style="font-weight:700;margin-bottom:6px">⛔ ⑤裁定待ち — {fid}
    （<a href="../issues/{iss}.html">{iss}</a>{'：' + _esc(title) if title else ''}）</div>
  {note}
  {q_box}
  <textarea id="adj-text-{fid}" rows="3" style="width:100%;box-sizing:border-box"
            placeholder="回答・裁定を書く（例: 仮説の通りでよい／丸めは銀行丸めが正、テスト側を直す）"></textarea>
  <div style="margin-top:8px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
    <button onclick="lrAdj.submit('{fid}','{iss}')"
            style="background:#b45309;color:#fff;border:0;border-radius:6px;padding:8px 16px;
                   font-weight:600;cursor:pointer">回答して裁定を完了（unblock）</button>
    <span id="adj-msg-{fid}" style="font-size:.9rem"></span>
  </div>
  <p style="margin:8px 0 0;color:#854d0e;font-size:.85rem">
    回答は {iss} に記録され、unblock 後に出る「⑤を実行する」ボタンで
    AI が回答を反映して再テストします。</p>
</div>
<script src="/lr-widgets.js"></script>
```"""


def adjudicate(root: str, fid: str, issue_id: str, approver: str, comment: str) -> dict:
    """POST /review-action (action=adjudicate) の実処理。

    ISSUE に回答を記入（status: answered）し、`ledger unblock` で裁定待ちを解除する。
    """
    if not comment.strip():
        return {"ok": False, "message": "回答が空です"}
    rootp = Path(root).resolve()
    current = _blocked_issue(root, fid)
    if current != issue_id:
        return {"ok": False,
                "message": f"{fid} は {issue_id} でブロックされていません"
                           f"（現在: {current or 'ブロックなし'}）。他で更新されています"}
    ip = rootp / "docs" / "issues" / f"{issue_id}.md"
    if ip.exists():
        text = ip.read_text(encoding="utf-8-sig")
        today = datetime.date.today().isoformat()
        one_line = " ／ ".join(l.strip() for l in comment.strip().splitlines() if l.strip())
        text, n = re.subn(r"(?m)^-\s*回答:\s*$", f"- 回答: {one_line}", text, count=1)
        if n == 0:                       # テンプレの回答行が無い/既に埋まっている → 追記
            text += f"\n- 回答（ブラウザから）: {one_line}\n"
        text = re.sub(r"(?m)^-\s*回答者\s*/\s*日付:\s*$",
                      f"- 回答者 / 日付: {approver} / {today}", text, count=1)
        text = _set_field(text, "status", "answered")
        ip.write_text(text, encoding="utf-8")
    r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "ledger.py"),
                        "--root", str(rootp), "unblock", fid],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return {"ok": False, "message": f"unblock に失敗（exit={r.returncode}）: "
                                        f"{(r.stderr or r.stdout or '').strip()[-200:]}"}
    refreshed = refresh_site(root, "adjudicate")     # WBS の⛔行と本ページを更新
    msg = f"{fid} の裁定を完了しました（{issue_id}: answered・unblock済み）"
    if not refreshed:
        msg += "（注意: サイト更新に失敗。render_site.py を手動実行してください）"
    return {"ok": True, "message": msg}


_REFRESH_MU = threading.Lock()


def refresh_site(root: str, kind: str) -> bool:
    """WBS・（①なら）一斉レビュー表・サイトを更新する（差分レンダなので数秒）。

    戻り値は全工程の成否。失敗を無音にしない——ここが黙って失敗すると、ブラウザ側は
    リロードしても古いページのままで手がかりが一切残らない。
    同一プロセス内（serve_site の承認POSTとブラウザバッチのチャンク更新など）で
    同時に走ると render_site 同士が _sitework を取り合うため、ロックで直列化する。
    """
    scripts = Path(__file__).resolve().parent
    root_p = str(Path(root).resolve())
    with _REFRESH_MU:
        if kind == "spec":
            review_checks.make_report(root_p)
        ok = True
        for name in ("ledger.py", "render_site.py"):
            cmd = [sys.executable, str(scripts / name), "--root", root_p]
            if name == "ledger.py":
                cmd.append("wbs")
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if r.returncode != 0:
                print(f"note: {name} が失敗（exit={r.returncode}）: "
                      f"{(r.stderr or r.stdout or '').strip()[-300:]}")
                ok = False
        return ok


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
    refreshed = refresh_site(root, kind)
    msg = f"{fid} を {cfg['approved_status']} にしました"
    if not refreshed:
        msg += "（注意: サイト更新に失敗。render_site.py を手動実行してください）"
    return {"ok": True, "message": msg}


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
    # 再レンダリングして、このページに「修正依頼を反映して再実行」ボタンを出す
    # （ウィジェットはレンダ時に焼き込まれるため、更新しないとリロードしても現れない）
    refreshed = refresh_site(root, kind)
    msg = (f"{fid} への修正依頼を送りました。リロード後に出る"
           "「修正依頼を反映して再実行」ボタンか、連続実行が反映します")
    if not refreshed:
        msg += "（注意: サイト更新に失敗。render_site.py を手動実行してください）"
    return {"ok": True, "message": msg}
