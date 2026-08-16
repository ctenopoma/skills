#!/usr/bin/env python3
"""review_actions.py — ブラウザから①②の承認・修正依頼を行う（serve_site.py が呼ぶ）。

WBS サイトを開いたまま「承認」「修正依頼」を完結させるための実処理。
承認ボタンを押した時点のクライアント側の表示を信用せず、**サーバ側で機械レビューを
再検証してから**承認する（NG付きの成果物を承認しない、という原則をUIでも強制する）。

呼び出し元は2つ:
  - render_site.py: 仕様書ページに埋め込む承認ウィジェット（widget_html）を作る
  - serve_site.py:   POST /review-action を受けて approve / request_changes を実行

変数辞書（P2）の承認も同じ流儀でここに同居する:
  - render_site.py: docs/variables.qmd に埋め込む辞書ウィジェット（dict_widget_html）
  - serve_site.py:   POST /dict-action を受けて approve / approve_all_ab / revise を実行
"""
import contextlib
import datetime
import io
import json
import re
import subprocess
import sys
import threading
import types
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


def _attr(s: str) -> str:
    """HTML 属性値に埋める用（_esc に加えて引用符も潰す）。"""
    return _esc(s).replace('"', "&quot;").replace("'", "&#39;")


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


# --- 変数辞書（P2）の承認をブラウザで完結させる -----------------------------
#
# 語義の確定は人の仕事（設計 P2「人の承認導線」）。チャットで
# `variables.py approve/revise` を打つのと**同じライブラリ関数**をここから呼ぶので、
# ブラウザ承認とチャット承認は同格・同結果になる（片方だけの抜け道を作らない）。
# 承認後は propagate → skeletons → 辞書ページ再生成 → サイト差分レンダまで一気に行う
# （①②承認の「フロントマター更新 → サイト再生成」と同じ設計）。

DICT_PANEL_LIMIT = 200      # 操作パネルに並べる未承認変数の上限（数千変数のプロジェクト対策）


def _load_variables(root: str):
    """data/variables.json の variables 配列。無い・壊れている場合は None。

    variables.load_json は壊れた JSON で sys.exit するが、ここは render 中にも
    呼ばれる（ウィジェット生成）ので、読めなければ黙ってウィジェットを出さない。
    """
    p = Path(root).resolve() / "data" / "variables.json"
    if not p.exists():
        return None
    try:
        store = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return (store or {}).get("variables") or []


def _dict_approvable(v: dict) -> bool:
    """そのまま承認してよいか。rank D / desc 未確定は「修正して承認」が必要。

    判定は variables.py cmd_approve の拒否条件（desc が空 or「不明」）に
    rank D を足したもの。UI ではボタンを落とし、サーバでも同じ条件で弾く。
    """
    desc = (v.get("desc") or "").strip()
    return bool(desc) and desc != "不明" and v.get("rank") != "D"


def _dict_pending(variables_):
    """未承認変数を辞書ページと同じ並び（確認待ち → 一括承認候補）で返す。"""
    import variables as V
    pending = [v for v in variables_ if v.get("status") != "approved"]
    queue = [v for v in pending if V.RANK_ORDER.get(v.get("rank"), 0) <= 2]   # 未判定・C・D
    batch = [v for v in pending if V.RANK_ORDER.get(v.get("rank"), 0) >= 3]   # A・B
    queue.sort(key=V._sort_key)
    batch.sort(key=V._sort_key)
    return queue, batch


_DICT_BTN = ("background:#4f46e5;color:#fff;border:0;border-radius:6px;padding:4px 12px;"
             "font-weight:600;cursor:pointer;font-size:.85rem")
_DICT_BTN_OFF = ("background:#cbd5e1;color:#64748b;border:0;border-radius:6px;padding:4px 12px;"
                 "font-weight:600;cursor:not-allowed;font-size:.85rem")
_DICT_BTN_SUB = ("background:#fff;border:1px solid #4f46e5;color:#4f46e5;border-radius:6px;"
                 "padding:4px 12px;cursor:pointer;font-size:.85rem")


def _dict_row_html(v: dict) -> str:
    """1変数ぶんの操作ブロック（承認 / 修正して承認…）。表の行アンカーへ相互リンクする。"""
    vid = v["var_id"]
    canonical = v.get("canonical_name") or ""
    others = [a for a in (v.get("aliases") or []) if a != canonical]
    name = _esc(canonical) + (f"（{_esc(', '.join(others))}）" if others else "")
    nfunc = len({o["func_id"] for o in v.get("occurrences", [])})
    desc = (v.get("desc") or "").strip()
    unit = v.get("unit") or ""
    meta = (f"rank {_esc(v.get('rank') or '—')}・{_esc(v.get('status') or '')}"
            f"・{nfunc}関数に出現")
    flags = ", ".join(v.get("flags") or [])
    if flags:
        meta += f"・{_esc(flags)}"
    if _dict_approvable(v):
        approve_btn = (f'<button onclick="lrDict.approve(\'{vid}\')" '
                       f'style="{_DICT_BTN}">承認</button>')
    else:
        approve_btn = (f'<button disabled title="desc が未確定です（rank D・空・「不明」）。'
                       f'「修正して承認…」で意味を書いてから承認します" '
                       f'style="{_DICT_BTN_OFF}">承認</button>')
    return f"""
  <div id="dict-op-{vid}" style="border-top:1px solid #c7d2fe;padding:8px 0">
    <div style="display:flex;gap:10px;align-items:baseline;flex-wrap:wrap">
      <a href="#dict-{vid}" style="font-weight:700">{vid}</a>
      <b>{name}</b>
      <span style="font-size:.85rem;color:#4338ca">{meta}</span>
      <span style="flex:1;min-width:120px">{_esc(desc) or '<i>（意味が未記入）</i>'}</span>
      {approve_btn}
      <button onclick="lrDict.toggleRevise('{vid}')" style="{_DICT_BTN_SUB}">修正して承認…</button>
      <span id="dict-msg-{vid}" style="font-size:.85rem"></span>
    </div>
    <div id="dict-rev-{vid}" style="display:none;margin-top:6px">
      <input id="dict-desc-{vid}" type="text" value="{_attr(desc)}"
             placeholder="意味（例: 年間税率）" style="width:60%;box-sizing:border-box;padding:4px 8px">
      <input id="dict-unit-{vid}" type="text" value="{_attr(unit)}"
             placeholder="単位（例: 円 / 無次元(比率)。無ければ空）"
             style="width:28%;box-sizing:border-box;padding:4px 8px">
      <button onclick="lrDict.revise('{vid}')" style="{_DICT_BTN}">この内容で承認</button>
    </div>
  </div>"""


def dict_widget_html(root: str) -> str | None:
    """docs/variables.qmd に埋め込む承認ウィジェット。未承認が無ければ None。"""
    variables_ = _load_variables(root)
    if not variables_:
        return None
    queue, batch = _dict_pending(variables_)
    if not queue and not batch:
        return None
    ab = [v for v in batch if v.get("status") == "interpreted" and _dict_approvable(v)]

    bulk = ""
    if ab:
        bulk = f"""
  <div style="background:#eef2ff;border-radius:8px;padding:10px 14px;margin-bottom:8px;
              display:flex;gap:12px;align-items:center;flex-wrap:wrap">
    <b>一括承認</b>
    <span style="font-size:.9rem">rank A/B（明示的な根拠あり）で未承認の
      <b>{len(ab)}</b> 件をまとめて承認します</span>
    <button onclick="lrDict.approveAllAB()"
            style="background:#4f46e5;color:#fff;border:0;border-radius:6px;padding:8px 16px;
                   font-weight:600;cursor:pointer">A/B の {len(ab)} 件を一括承認</button>
    <span id="dict-msg-all" style="font-size:.9rem"></span>
  </div>"""

    rows = queue + batch
    shown = rows[:DICT_PANEL_LIMIT]
    more = ""
    if len(rows) > len(shown):
        more = (f'<p style="margin:8px 0 0;color:#4338ca;font-size:.85rem">'
                f'※ 未承認 {len(rows)} 件のうち先頭 {len(shown)} 件を表示しています。'
                f'残りは一括承認、またはチャット（<code>variables.py approve / revise</code>）'
                f'で処理してください。</p>')
    return f"""```{{=html}}
<div id="lr-dict" class="lr-dict-widget"
     style="border:2px solid #4f46e5;border-radius:10px;padding:14px 18px;margin:0 0 22px;
            background:#f5f5ff;font-family:system-ui,sans-serif">
  <div style="font-weight:700;margin-bottom:8px">変数辞書の承認 —
    未承認 {len(rows)} 件（確認待ち {len(queue)} / 一括承認候補 {len(batch)}）</div>
  <p style="margin:0 0 8px;font-size:.9rem;color:#3730a3">
    承認1回でクラスタ全出現（別名・COMMON・呼び出し結線）に効きます。承認すると
    functions.json への転記（propagate）・骨子・サイトまで自動で更新されます。
    rank D や意味が未記入のものは「修正して承認…」で意味を書いてから承認してください。</p>
  {bulk}
  <div style="max-height:520px;overflow:auto">{"".join(_dict_row_html(v) for v in shown)}
  </div>
  {more}
</div>
<script src="/lr-widgets.js"></script>
```"""


def _run_vars_cmd(root: Path, fn, **kw):
    """variables.py のサブコマンド関数を **import して** 直接呼ぶ（subprocess にしない）。

    CLI 用に書かれているので、引数は Namespace で渡し、print は捕まえ、
    拒否（sys.exit）はメッセージに変換して呼び出し元へそのまま返す。
    戻り値は (成否, メッセージ)。
    """
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            fn(root, types.SimpleNamespace(**kw))
    except SystemExit as e:
        detail = str(e) if str(e) and str(e) != "None" else buf.getvalue().strip()
        return False, re.sub(r"^error:\s*", "", detail.strip()) or "変数辞書の更新に失敗しました"
    return True, buf.getvalue().strip()


def _dict_after_approve(root: str, headline: str) -> dict:
    """承認後の伝搬。propagate → skeletons → 辞書ページ再生成 → サイト差分レンダ。

    どれかが失敗しても承認自体は成立している（variables.json は保存済み）ので、
    ok は True のまま「注意」として理由を返す——黙って古い表示のままにしない。
    """
    import variables as V
    rootp = Path(root).resolve()
    notes = []
    ok, out = _run_vars_cmd(rootp, V.cmd_propagate)
    if not ok:
        notes.append(f"propagate 失敗（{out}）")
    # 骨子の再生成。既存の spec ファイルは ledger.py skeletons が上書きしない仕様なので、
    # 人が書いた・AI が書いた成果物を壊さない
    r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "ledger.py"),
                        "--root", str(rootp), "skeletons"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        notes.append(f"skeletons 失敗（exit={r.returncode}: "
                     f"{(r.stderr or r.stdout or '').strip()[-150:]}）")
    ok, out = _run_vars_cmd(rootp, V.cmd_page)
    if not ok:
        notes.append(f"辞書ページ再生成に失敗（{out}）")
    if not refresh_site(root, "dict"):
        notes.append("サイト更新に失敗（render_site.py を手動実行してください）")
    msg = (headline or "").strip().splitlines()[0] if (headline or "").strip() else "承認しました"
    if notes:
        msg += "（注意: " + " / ".join(notes) + "）"
    return {"ok": True, "message": msg}


def dict_action(root: str, action: str, approver: str, var_ids=None,
                var_id: str = "", desc: str = "", unit=None) -> dict:
    """POST /dict-action の実処理。

    action:
      approve         var_ids の変数を承認（desc 確定済みのものだけ）
      approve_all_ab  status=interpreted かつ rank A/B を一括承認
      revise          var_id の desc/unit を確定して同時に承認（rank D もここから）
    """
    import variables as V
    rootp = Path(root).resolve()
    variables_ = _load_variables(root)
    if variables_ is None:
        return {"ok": False,
                "message": "data/variables.json が読めません（先に `variables.py build` を実行）"}
    by_id = {v["var_id"]: v for v in variables_}
    approver = (approver or "").strip() or "unknown"

    if action == "revise":
        vid = (var_id or "").strip().upper()
        if vid not in by_id:
            return {"ok": False, "message": f"未定義の var_id: {var_id or '(空)'}"}
        d = (desc or "").strip()
        if not d or d == "不明":
            return {"ok": False, "message": "意味（desc）を具体的に入力してください（「不明」は不可）"}
        ok, msg = _run_vars_cmd(rootp, V.cmd_revise, var_id=vid, desc=d, by=approver,
                                unit=(unit.strip() if isinstance(unit, str) else None))
        return _dict_after_approve(root, msg) if ok else {"ok": False, "message": msg}

    if action == "approve_all_ab":
        _, batch = _dict_pending(variables_)
        ids = [v["var_id"] for v in batch
               if v.get("status") == "interpreted" and _dict_approvable(v)]
        if not ids:
            return {"ok": False,
                    "message": "一括承認できる変数がありません"
                               "（対象は status=interpreted かつ rank A/B）"}
    elif action == "approve":
        ids = [str(i).strip().upper() for i in (var_ids or []) if str(i).strip()]
        if not ids:
            return {"ok": False, "message": "var_ids が空です"}
        unknown = [i for i in ids if i not in by_id]
        if unknown:
            return {"ok": False, "message": f"未定義の var_id: {', '.join(unknown)}"}
        done = [i for i in ids if by_id[i].get("status") == "approved"]
        if done:
            return {"ok": False,
                    "message": f"既に承認済み: {', '.join(done)}（他で更新されています）"}
        # クライアント表示を信用せず、サーバ側で承認可否を判定する（①②承認と同じ原則）
        bad = [i for i in ids if not _dict_approvable(by_id[i])]
        if bad:
            return {"ok": False,
                    "message": f"desc が未確定のため承認できません（rank D・空・「不明」）: "
                               f"{', '.join(bad)}。「修正して承認…」で意味を書いてください"}
    else:
        return {"ok": False, "message": f"不明な action: {action}"}

    ok, msg = _run_vars_cmd(rootp, V.cmd_approve, ids=",".join(ids), by=approver)
    return _dict_after_approve(root, msg) if ok else {"ok": False, "message": msg}
