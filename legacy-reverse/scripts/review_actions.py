#!/usr/bin/env python3
"""review_actions.py — ①②の承認・修正依頼と⑤の裁定（CLI / skill から呼ぶ）。

HTML サイトは**閲覧専用**（render_site.py が状態と返答方法の案内を焼き込むだけ）。
人の返答は次の3チャネルのどれでもよく、すべて同格:

  1. チャット   … 「F-0012 OK」「F-0012 修正: …」→ skill がこのモジュールを使って反映
  2. ファイル記入 … ISSUE の「回答（人が記入）」欄 / docs/review-feedback.md に人が書く
                   → 各 skill の起動時スキャンが反映
  3. CLI        … このファイルを直接実行する:

     python review_actions.py approve spec F-0012 --by 山田 --root .
     python review_actions.py approve testspec F-0012 --by 山田 --root .
     python review_actions.py request-changes spec F-0012 --by 山田 --comment "…" --root .
     python review_actions.py adjudicate F-0012 --issue ISSUE-004 --by 山田 --comment "…" --root .

どの経路でも、承認は**サーバ側（このモジュール）で機械レビューを再検証してから**行う
（NG付きの成果物を承認しない、という原則を入口によらず強制する）。
承認・裁定の後は WBS・一斉レビュー表・サイトの再生成まで自動で行う。

変数辞書の承認は variables.py（approve / revise）が同じ役割を担う。
"""
import argparse
import datetime
import re
import subprocess
import time
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
    """このステータスの間は人の承認待ち（閲覧サイトに案内パネルが出る）。"""
    return KINDS[kind]["pending_status"]


def pending_feedback_kinds(root: str, fid: str) -> set:
    """review-feedback.md に「状態: pending」で残っている修正依頼の kind 集合。

    連続実行（pipeline.py run）が修正依頼済みの draft/generated を
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


def blocked_issue(root: str, fid: str) -> str | None:
    """⑤で blocked（裁定待ち）ならブロックしている ISSUE の ID を返す。"""
    import pipeline   # 遅延import（pipeline → review_actions の向きで先に張られるため）
    p = pipeline._project(Path(root).resolve())
    return (p.ledger.get(fid) or {}).get("blocked_by")


def adjudicate(root: str, fid: str, issue_id: str, approver: str, comment: str) -> dict:
    """⑤裁定の実処理（チャット/CLI どちらもここを通る）。

    ISSUE に回答を記入（status: answered）し、`ledger unblock` で裁定待ちを解除する。
    """
    if not comment.strip():
        return {"ok": False, "message": "回答が空です"}
    rootp = Path(root).resolve()
    current = blocked_issue(root, fid)
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
            text += f"\n- 回答（裁定）: {one_line}\n"
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
    refreshed = refresh_site(root, "adjudicate")     # WBS の⛔行と該当ページを更新
    msg = (f"{fid} の裁定を完了しました（{issue_id}: answered・unblock済み）。"
           f"次は ⑤ の再実行（/legacy-5-test {fid} か pipeline.py run）で回答を反映します")
    if not refreshed:
        msg += "（注意: サイト更新に失敗。render_site.py を手動実行してください）"
    return {"ok": True, "message": msg}


_REFRESH_MU = threading.Lock()


def refresh_site(root: str, kind: str) -> bool:
    """WBS・（①なら）一斉レビュー表・サイトを更新する（差分レンダなので数秒）。

    戻り値は全工程の成否。失敗を無音にしない——ここが黙って失敗すると、閲覧側は
    リロードしても古いページのままで手がかりが一切残らない。
    同一プロセス内で同時に走ると render_site 同士が _sitework を取り合うため、
    ロックで直列化する。
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
            # 規模によっては render が分単位かかる。無音で待たせると
            # バッチが固まったようにしか見えないので、開始と所要を必ず出す
            print(f"  … {name} 実行中", flush=True)
            t0 = time.time()
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if r.returncode != 0:
                print(f"note: {name} が失敗（exit={r.returncode}）: "
                      f"{(r.stderr or r.stdout or '').strip()[-300:]}")
                ok = False
            else:
                print(f"  … {name} 完了（{time.time() - t0:.1f}s）", flush=True)
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
    # 入口（チャット/CLI）によらず、承認直前に機械レビューを再検証する
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
    """修正依頼を docs/review-feedback.md に記録する（著者は人。このコマンドは人の代筆）。

    人が review-feedback.md を直接編集して同じ形式で書いてもよい（同格）。
    どちらの場合も、次に skill / pipeline.py run が pending を検出して反映する。
    """
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
            "# 修正依頼（人の指摘）\n\n"
            "<!-- 人が書く（review_actions.py request-changes でも直接編集でもよい）。\n"
            "     各フェーズ skill は起動時に 状態: pending の項目を確認・反映し、\n"
            "     状態: applied に書き換える -->\n",
            encoding="utf-8")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with fb.open("a", encoding="utf-8") as f:
        f.write(f"\n## {fid}（{kind}）\n"
                f"- 起票: {now} / {approver}\n"
                "- 状態: pending\n"
                f"- 内容: {comment.strip()}\n")
    refreshed = refresh_site(root, kind)
    msg = (f"{fid} への修正依頼を記録しました。"
           f"反映は `/legacy-1-spec {fid}` 等の再実行か `pipeline.py run` が行います")
    if not refreshed:
        msg += "（注意: サイト更新に失敗。render_site.py を手動実行してください）"
    return {"ok": True, "message": msg}


# --- CLI --------------------------------------------------------------------

def main() -> None:
    import serve_site
    serve_site.use_utf8_console()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("approve", help="①/②を承認する（機械レビューを再検証してから）")
    a.add_argument("kind", choices=sorted(KINDS))
    a.add_argument("fid", help="関数ID（F-0012）")
    a.add_argument("--by", required=True, help="承認者名")
    a.add_argument("--root", default=".")

    rc = sub.add_parser("request-changes", help="①/②へ修正依頼を出す（review-feedback.md に記録）")
    rc.add_argument("kind", choices=sorted(KINDS))
    rc.add_argument("fid")
    rc.add_argument("--by", required=True, help="依頼者名")
    rc.add_argument("--comment", required=True, help="修正してほしい内容")
    rc.add_argument("--root", default=".")

    ad = sub.add_parser("adjudicate", help="⑤の裁定（ISSUEに回答を記入して unblock）")
    ad.add_argument("fid")
    ad.add_argument("--issue", required=True, help="ブロックしている ISSUE-xxx")
    ad.add_argument("--by", required=True, help="裁定者名")
    ad.add_argument("--comment", required=True, help="回答・裁定の内容")
    ad.add_argument("--root", default=".")

    args = ap.parse_args()
    if args.cmd == "approve":
        res = approve(args.root, args.kind, args.fid, args.by)
    elif args.cmd == "request-changes":
        res = request_changes(args.root, args.kind, args.fid, args.by, args.comment)
    else:
        res = adjudicate(args.root, args.fid, args.issue, args.by, args.comment)
    print(("OK " if res["ok"] else "NG ") + res["message"])
    if not res["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
