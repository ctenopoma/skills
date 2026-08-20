#!/usr/bin/env python3
"""閲覧専用サイト（案内パネル）と承認 CLI のセルフテスト（pytest不要・素の assert）。

`python test_view_only.py` で実行する。scripts/selftest/fixture_vars/ を毎回テンポラリに
コピーし、build → verify-interp → page まで進めてから、

  (a) render_site の案内パネル注入（変数辞書ページ・①draft ページ）が
      **静的**であること（<script>・fetch・POST を含まない）
  (b) 全承認済み・対象外ページには何も注入しないこと
  (c) review_actions の CLI 実処理: 機械レビューNGの draft は承認拒否、
      request-changes は review-feedback.md に pending を記録すること
  (d) serve_site が書き込み・実行ルート（do_POST / WRITE_ROUTES）を持たないこと

refresh_site（ledger wbs ＋ quarto render）は差し替える——Quarto は
CI/テスト環境に無いことがあり、ここで検証したいのは「呼ばれること」だけのため。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixture_vars"
VARIABLES_PY = SCRIPTS_DIR / "variables.py"
TEMPLATE_YML = SCRIPTS_DIR.parent / "assets" / "templates" / "_quarto.yml"

sys.path.insert(0, str(SCRIPTS_DIR))
import review_actions       # noqa: E402
import render_site          # noqa: E402
import serve_site           # noqa: E402

_TMPDIRS = []


def make_project() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="lr-viewonly-"))
    _TMPDIRS.append(tmp)
    root = tmp / "proj"
    shutil.copytree(FIXTURE, root)
    return root


def run(root: Path, *args):
    cmd = [sys.executable, str(VARIABLES_PY), *args, "--root", str(root)]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
    return r.returncode, r.stdout, r.stderr


def load_vars(root: Path):
    return json.loads((root / "data" / "variables.json").read_text(encoding="utf-8"))["variables"]


def by_occ(vars_, key: str):
    hits = [v for v in vars_ if any(f"{o['func_id']}:{o['name']}" == key for o in v["occurrences"])]
    assert len(hits) == 1, f"{key} を含む変数が {len(hits)}件"
    return hits[0]


def ev_of(v, kind):
    for e in v["evidence"]:
        if e["kind"] == kind:
            return e["ev_id"]
    raise AssertionError(f"{v['var_id']} に kind={kind} の根拠が無い")


def prepared() -> Path:
    """build → 仮の interpretations を verify-interp でマージ → page まで進めた状態。"""
    root = make_project()
    assert run(root, "build")[0] == 0
    vars_ = load_vars(root)
    v_a = by_occ(vars_, "F-0000:RATE")      # comment 引用 → A
    payload = {
        v_a["var_id"]: {"desc": "年間税率", "unit": "無次元(比率)", "rank_claim": "A",
                        "evidence_cited": [ev_of(v_a, "comment")]},
    }
    (root / "data" / "interpretations.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    rc, out, err = run(root, "verify-interp", "--ids", ",".join(sorted(payload)))
    assert rc == 0, out + err
    assert run(root, "page")[0] == 0
    return root


def page_text(root: Path) -> str:
    return (root / "docs" / "variables.qmd").read_text(encoding="utf-8")


class _Refresh:
    """refresh_site を差し替えて「呼ばれたか」だけ記録する（Quarto 非依存にする）。"""

    def __init__(self):
        self.calls = []
        self._orig = review_actions.refresh_site

    def __enter__(self):
        review_actions.refresh_site = lambda root, kind: (self.calls.append(kind) or True)
        return self

    def __exit__(self, *exc):
        review_actions.refresh_site = self._orig


def assert_static(html: str, label: str) -> None:
    """案内パネルが閲覧専用（JS・POST 無し）であること。"""
    for bad in ("<script", "fetch(", "jsonPost", "onclick=", "XMLHttpRequest"):
        assert bad not in html, f"{label}: {bad} が含まれている（閲覧専用でない）"


# ---------- (a)(b) 案内パネルの注入 ----------

def test_dict_notice():
    root = prepared()
    html = render_site.dict_notice_html(str(root))
    assert html, "未承認があるのに案内が出ない"
    assert html.startswith("```{=html}") and html.rstrip().endswith("```")
    assert_static(html, "dict notice")
    assert "variables.py" in html and "approve" in html and "revise" in html
    assert "未承認" in html

    body = render_site.transform_doc(page_text(root))
    out = render_site.maybe_inject_notice(root, Path("variables.qmd"), body)
    assert out != body
    head, _, rest = out.partition("\n---\n")       # フロントマターの直後に入る
    assert 'class="lr-notice"' in rest
    assert 'title: "変数辞書"' in head

    # 関係ないページには入らない
    other = render_site.maybe_inject_notice(root, Path("domain-knowledge.md"), body)
    assert other == body

    # 全件承認済みなら案内は出ない
    all_ids = [v["var_id"] for v in load_vars(root)]
    for vid in all_ids:
        rc, out2, err = run(root, "revise", vid, "--desc", "確定済みの意味", "--by", "テスト太郎")
        assert rc == 0, out2 + err
    assert render_site.dict_notice_html(str(root)) is None
    assert render_site.maybe_inject_notice(root, Path("variables.qmd"), body) == body
    print("OK  (a)(b) 変数辞書の案内パネル（静的・注入位置・全承認時は非表示）")


def test_review_notice():
    root = prepared()
    # フィクスチャの F-0001 は reviewed 済み → 案内なし
    assert render_site.review_notice_html(str(root), "spec", "F-0001") is None
    (root / "docs" / "specs" / "F-0002.md").write_text(
        '---\ntitle: "t"\nstatus: draft\n---\n\n# 本文\n', encoding="utf-8")
    w = render_site.review_notice_html(str(root), "spec", "F-0002")
    assert w, "draft なのに案内が出ない"
    assert_static(w, "review notice")
    assert "機械レビューNG" in w                    # 最小 draft は必須節が無い → NG 表示
    assert "review_actions.py" in w and "approve spec F-0002" in w
    assert "review-feedback.md" in w and "チャット" in w

    body = "---\ntitle: t\n---\n\n# 本文\n"
    out = render_site.maybe_inject_notice(root, Path("specs/F-0002.md"), body)
    assert 'class="lr-notice"' in out
    print("OK  (a) ①draft の案内パネル（静的・3チャネル・機械レビュー結果）")


# ---------- (c) review_actions の実処理 ----------

def test_approve_and_request_changes():
    root = prepared()
    (root / "docs" / "specs" / "F-0002.md").write_text(
        '---\ntitle: "t"\nstatus: draft\n---\n\n# 本文\n', encoding="utf-8")
    with _Refresh() as rf:
        # 機械レビューNGの draft は、どの入口からでも承認拒否
        r = review_actions.approve(str(root), "spec", "F-0002", "テスト太郎")
        assert not r["ok"] and "機械レビューNG" in r["message"]
        # 修正依頼は review-feedback.md に pending で記録される
        r = review_actions.request_changes(str(root), "spec", "F-0002",
                                           "テスト太郎", "丸め規則が本文に無い")
        assert r["ok"], r
        assert rf.calls, "refresh_site が呼ばれていない"
    fb = (root / "docs" / "review-feedback.md").read_text(encoding="utf-8")
    assert "## F-0002（spec）" in fb and "状態: pending" in fb
    assert review_actions.pending_feedback_kinds(str(root), "F-0002") == {"spec"}
    print("OK  (c) 承認拒否（機械レビューNG）と修正依頼の記録")


# ---------- (d) serve_site は閲覧専用 ----------

def test_serve_site_read_only():
    assert not hasattr(serve_site.Handler, "do_POST"), "do_POST が残っている"
    assert not hasattr(serve_site.Handler, "WRITE_ROUTES"), "WRITE_ROUTES が残っている"
    page = serve_site.PIPELINE_PAGE
    for bad in ("jsonPost", "/review-action", "/dict-action", "/run-phase", "/run-batch",
                "/run-cancel", "/run-skip", "/batch-priority", "/run-analyze", "/run-check"):
        assert bad not in page, f"pipeline.html に {bad} が残っている"
    assert "pipeline.py run" in page               # CLI への案内はある
    print("OK  (d) serve_site は GET のみ（表示専用の pipeline.html）")


def test_navbar_entry():
    yml = TEMPLATE_YML.read_text(encoding="utf-8-sig")
    without = render_site.transform_yml(yml, has_variables=False)
    assert "variables.qmd" not in without
    with_ = render_site.transform_yml(yml, has_variables=True)
    assert "- href: variables.qmd" in with_ and "text: 変数辞書" in with_
    print("OK  ナビバー（変数辞書エントリの有無）")


def test_agent_log_route():
    """/agent-logs/ が pipeline.agent_log_name と同じ規則で引き当てること。

    辞書バッチの識別子は "dict:V-0001〜V-0040" のようにファイル名に使えない文字を
    含む。画面は生の識別子でリンクを作るので、ルート側が同じ規則で潰さないと
    応答ログが必ず 404 になる（＝失敗の一次情報に到達できない）。
    """
    import functools
    import threading
    import urllib.error
    import urllib.parse
    import urllib.request

    import pipeline

    root = Path(tempfile.mkdtemp(prefix="lr-agentlog-"))
    _TMPDIRS.append(root)
    logs = root / ".legacy-reverse" / "agent-logs"
    logs.mkdir(parents=True)
    site = root / "docs" / "_site"
    site.mkdir(parents=True)
    (site / "index.html").write_text("x", encoding="utf-8")
    (root / "secret.txt").write_text("SECRET", encoding="utf-8")
    label = "dict:V-0001〜V-0040"
    (logs / pipeline.agent_log_name(label)).write_text("dict log body", encoding="utf-8")
    (logs / "F-0001.txt").write_text("spec log", encoding="utf-8")

    old_root = serve_site.Handler.state_root
    serve_site.Handler.state_root = root
    httpd = serve_site.bind_server(
        "127.0.0.1", 0, functools.partial(serve_site.Handler, directory=str(site)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def get(name: str) -> tuple:
        url = f"http://127.0.0.1:{port}/agent-logs/{urllib.parse.quote(name)}.txt"
        try:
            with urllib.request.urlopen(url) as r:
                return r.status, r.read().decode("utf-8").strip()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")

    try:
        assert get(label) == (200, "dict log body"), "辞書ラベルの応答ログが引けない"
        assert get("dict_V-0001_V-0040") == (200, "dict log body")   # 潰した名前でも可
        assert get("F-0001") == (200, "spec log")
        code, body = get("F-9999")
        assert code == 404, "無いログが 404 になっていない"
        # 素の 404 に戻さない（どこを探したか・何があるかが切り分けの一次情報）
        assert "応答ログが見つかりません" in body and "F-0001.txt" in body, body
        for bad in ("../secret", "../../etc/passwd", "..%2f..%2fsecret"):
            code, body = get(bad)
            assert code == 404, f"ディレクトリ外に出られてしまう: {bad}"
            assert "SECRET" not in body, f"ディレクトリ外の中身が漏れている: {bad}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        serve_site.Handler.state_root = old_root
    print("OK  応答ログのルート（辞書ラベル・ディレクトリ外拒否）")


def main():
    tests = [
        test_dict_notice,
        test_review_notice,
        test_approve_and_request_changes,
        test_serve_site_read_only,
        test_navbar_entry,
        test_agent_log_route,
    ]
    try:
        for t in tests:
            t()
        print(f"\nPASS: {len(tests)}件すべて成功")
    finally:
        for d in _TMPDIRS:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
