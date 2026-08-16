#!/usr/bin/env python3
"""変数辞書のブラウザ承認ウィジェット（M2b）のセルフテスト（pytest不要・素の assert）。

`python test_dict_widget.py` で実行する。scripts/selftest/fixture_vars/ を毎回テンポラリに
コピーし、build → verify-interp → page まで進めてから、

  (a) render_site 側のウィジェット注入（review_actions.dict_widget_html /
      render_site.maybe_inject_review_widget / transform_yml のナビバー救済）
  (b) review_actions.dict_action（approve / approve_all_ab / revise）が
      variables.json を正しく更新し、propagate・辞書ページ再生成まで走ること
  (c) rank D・desc 未確定の approve をサーバ側で拒否すること
  (d) serve_site の防御構造（WRITE_ROUTES 登録・_cross_site_reason・FROZEN 判定）

を検証する。HTTP 層は起動しない（ユニットの範囲を超えるため、ルート登録と
クロスサイト判定の存在確認までにとどめる）。

(b) では refresh_site（ledger wbs ＋ quarto render）だけ差し替える——Quarto は
CI/テスト環境に無いことがあり、ここで検証したいのは「呼ばれること」だけのため。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixture_vars"
VARIABLES_PY = SCRIPTS_DIR / "variables.py"
TEMPLATE_YML = SCRIPTS_DIR.parent / "assets" / "templates" / "_quarto.yml"

sys.path.insert(0, str(SCRIPTS_DIR))
import variables            # noqa: E402
import review_actions       # noqa: E402
import render_site          # noqa: E402
import browser_run          # noqa: E402
import serve_site           # noqa: E402

_TMPDIRS = []


def make_project() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="lr-dictw-"))
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


def prepared() -> tuple:
    """build → 仮の interpretations を verify-interp でマージ → page まで進めた状態。

    戻り値は (root, {"A": vid, "B": vid, "C": vid, "D": vid})。
    A/B は一括承認候補、C は確認待ち、D は「不明」でマージ拒否された人のキュー。
    """
    root = make_project()
    assert run(root, "build")[0] == 0
    vars_ = load_vars(root)
    v_a = by_occ(vars_, "F-0000:RATE")      # comment 引用 → A
    v_b = by_occ(vars_, "F-0002:RATE")      # usage_expr ＋ドメイン知識の語 → B
    v_c = by_occ(vars_, "F-0003:WORK")      # usage_expr のみ → C
    v_d = by_occ(vars_, "F-0000:NCNT")      # 引用なし → D（desc は「不明」）
    payload = {
        v_a["var_id"]: {"desc": "年間税率", "unit": "無次元(比率)", "rank_claim": "A",
                        "evidence_cited": [ev_of(v_a, "comment")]},
        v_b["var_id"]: {"desc": "為替レート", "unit": "円/ドル", "rank_claim": "A",
                        "evidence_cited": [ev_of(v_b, "usage_expr")]},
        v_c["var_id"]: {"desc": "作業用配列", "unit": None, "rank_claim": "B",
                        "evidence_cited": [ev_of(v_c, "usage_expr")]},
        v_d["var_id"]: {"desc": "処理件数", "unit": "件", "rank_claim": "A",
                        "evidence_cited": []},
    }
    (root / "data" / "interpretations.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    rc, out, err = run(root, "verify-interp", "--ids", ",".join(sorted(payload)))
    assert rc == 0, out + err
    assert run(root, "page")[0] == 0
    return root, {"A": v_a["var_id"], "B": v_b["var_id"],
                  "C": v_c["var_id"], "D": v_d["var_id"]}


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


def funcs_of(root: Path):
    data = json.loads((root / "data" / "functions.json").read_text(encoding="utf-8"))
    return {f["func_id"]: f for f in data["functions"]}


# ---------- (a) ウィジェット注入 ----------

def test_widget_bulk_bar_and_rows():
    root, ids = prepared()
    html = review_actions.dict_widget_html(str(root))
    assert html, "未承認があるのにウィジェットが出ない"
    assert html.startswith("```{=html}") and html.rstrip().endswith("```")
    assert '<script src="/lr-widgets.js"></script>' in html

    # 一括承認バー: status=interpreted かつ rank A/B の2件だけが対象
    assert "A/B の 2 件を一括承認" in html, html[:600]
    assert 'onclick="lrDict.approveAllAB()"' in html
    assert 'id="dict-msg-all"' in html

    vars_ = {v["var_id"]: v for v in load_vars(root)}
    pending = [v for v in vars_.values() if v["status"] != "approved"]
    assert len(pending) == 9                       # フィクスチャは全9変数・まだ承認ゼロ
    for v in pending:                              # 個別操作は未承認の全行に出る
        vid = v["var_id"]
        assert f'id="dict-op-{vid}"' in html, vid
        assert f'href="#dict-{vid}"' in html, vid   # 表の行アンカーへの相互リンク
        assert f"lrDict.toggleRevise('{vid}')" in html
        assert f'id="dict-desc-{vid}"' in html and f'id="dict-unit-{vid}"' in html

    # 承認ボタンは desc 確定済みだけ。rank D と未解釈（desc 空）は disabled
    for key in ("A", "B", "C"):
        assert f"lrDict.approve('{ids[key]}')" in html, key
    assert f"lrDict.approve('{ids['D']}')" not in html
    assert html.count("disabled title=") == 6      # D 1件 ＋ 未解釈 5件

    # 表の行アンカーは variables.py page が出したものと一致する
    page = page_text(root)
    for v in pending:
        assert f"{{#dict-{v['var_id']}}}" in page
    print("OK  ウィジェット（一括承認バー・個別操作・アンカー・disabled 判定）")


def test_widget_injection_and_hidden_when_done():
    root, ids = prepared()
    body = render_site.transform_doc(page_text(root))
    out = render_site.maybe_inject_review_widget(root, Path("variables.qmd"), body)
    assert out != body
    head, _, rest = out.partition("\n---\n")       # フロントマターの直後に入る
    assert 'class="lr-dict-widget"' in rest.split("# 確認待ち")[0]
    assert 'title: "変数辞書"' in head

    # 関係ないページには入らない
    other = render_site.maybe_inject_review_widget(root, Path("domain-knowledge.md"), body)
    assert other == body

    # 全件承認済みならウィジェットは出ない（承認済み行に操作を出さない、の徹底）
    all_ids = [v["var_id"] for v in load_vars(root)]
    with _Refresh():
        for vid in all_ids:
            r = review_actions.dict_action(str(root), "revise", "テスト太郎",
                                           var_id=vid, desc="確定済みの意味", unit="")
            assert r["ok"], r
    assert review_actions.dict_widget_html(str(root)) is None
    assert render_site.maybe_inject_review_widget(root, Path("variables.qmd"), body) == body
    print("OK  注入位置（フロントマター直後）・対象ページ限定・全承認時は非表示")


def test_navbar_entry():
    yml = TEMPLATE_YML.read_text(encoding="utf-8-sig")
    without = render_site.transform_yml(yml, has_variables=False)
    assert "variables.qmd" not in without
    with_ = render_site.transform_yml(yml, has_variables=True)
    assert "- href: variables.qmd" in with_ and "text: 変数辞書" in with_
    # WBS（index）エントリの直後・インデントを保って挿す
    lines = [l.rstrip() for l in with_.splitlines()]
    i = lines.index("      - href: variables.qmd")
    assert lines[i - 2] == "      - href: index.qmd"
    assert lines[i + 1] == "        text: 変数辞書"
    # 二重追加しない（既にエントリのあるテンプレ世代）
    assert render_site.transform_yml(with_, has_variables=True).count("href: variables.qmd") == 1
    # 既存ナビバー救済（バッチ状況・マニュアル）を壊していない
    assert "pipeline.html" in with_ and "manual.html" in with_
    print("OK  ナビバー救済（docs/variables.qmd がある時だけ「変数辞書」を追加）")


# ---------- (b) dict_action ----------

def test_dict_action_approve_all_ab():
    root, ids = prepared()
    spec_before = (root / "docs" / "specs" / "F-0001.md").read_bytes()
    with _Refresh() as rf:
        res = review_actions.dict_action(str(root), "approve_all_ab", "テスト太郎")
    assert res["ok"], res
    assert "承認 2件" in res["message"], res
    assert rf.calls == ["dict"]                    # サイト差分レンダまで走る

    vars_ = {v["var_id"]: v for v in load_vars(root)}
    for key in ("A", "B"):
        v = vars_[ids[key]]
        assert v["status"] == "approved" and v["approved_by"] == "テスト太郎"
        assert v["approved_date"]
    assert vars_[ids["C"]]["status"] == "interpreted"     # rank C は一括の対象外
    assert vars_[ids["D"]]["status"] == "unreviewed"

    # propagate まで走っている（IO の desc と COMMON ブロック注記）
    fmap = funcs_of(root)
    rate_row = [r for r in fmap["F-0002"]["inputs"] if r["name"] == "RATE"][0]
    assert rate_row["desc"] == f"為替レート(円/ドル) [{ids['B']}]", rate_row
    assert f"RATE=年間税率(無次元(比率)) [{ids['A']}]" in fmap["F-0000"]["globals"][0]["desc"]
    # 辞書ページも再生成されている（承認済みセクションに移動）
    approved_sec = page_text(root).split("# 承認済み", 1)[1]
    assert ids["A"] in approved_sec and ids["B"] in approved_sec
    # 骨子生成（ledger.py skeletons）は既存の成果物を上書きしない
    assert (root / "docs" / "specs" / "F-0001.md").read_bytes() == spec_before

    # 2度目は対象なし
    with _Refresh():
        again = review_actions.dict_action(str(root), "approve_all_ab", "テスト太郎")
    assert not again["ok"] and "一括承認できる変数がありません" in again["message"]
    print("OK  dict_action approve_all_ab（A/B のみ承認 → propagate → ページ再生成 → 再レンダ）")


def test_dict_action_approve_and_revise():
    root, ids = prepared()
    with _Refresh() as rf:
        res = review_actions.dict_action(str(root), "approve", "テスト太郎",
                                         var_ids=[ids["C"].lower()])   # 小文字も受ける
    assert res["ok"], res
    vars_ = {v["var_id"]: v for v in load_vars(root)}
    assert vars_[ids["C"]]["status"] == "approved"
    assert vars_[ids["A"]]["status"] == "interpreted"      # 指定した1件だけ
    assert rf.calls == ["dict"]

    # 既に承認済みのものは弾く（他所で更新されたケース）
    with _Refresh():
        dup = review_actions.dict_action(str(root), "approve", "テスト太郎", var_ids=[ids["C"]])
    assert not dup["ok"] and "既に承認済み" in dup["message"]

    # revise: desc/unit を確定して同時に承認（rank D もここから承認できる）
    with _Refresh() as rf:
        rev = review_actions.dict_action(str(root), "revise", "テスト花子",
                                         var_id=ids["D"], desc="処理件数", unit="件")
    assert rev["ok"], rev
    assert rf.calls == ["dict"]
    v_d = {v["var_id"]: v for v in load_vars(root)}[ids["D"]]
    assert v_d["status"] == "approved" and v_d["desc"] == "処理件数" and v_d["unit"] == "件"
    assert v_d["approved_by"] == "テスト花子"
    assert "needs_human" not in v_d["flags"]

    # unit 空文字は「単位なし」として確定する
    with _Refresh():
        assert review_actions.dict_action(str(root), "revise", "テスト花子",
                                          var_id=ids["A"], desc="年間の税率", unit="")["ok"]
    v_a = {v["var_id"]: v for v in load_vars(root)}[ids["A"]]
    assert v_a["desc"] == "年間の税率" and v_a["unit"] is None
    print("OK  dict_action approve / revise（個別承認・二重承認拒否・rank D の修正承認）")


# ---------- (c) サーバ側の拒否 ----------

def test_dict_action_rejects():
    root, ids = prepared()
    vars_ = load_vars(root)
    plain = [v for v in vars_ if v["status"] == "unreviewed" and not (v.get("desc") or "")][0]

    cases = [
        # rank D（desc が「不明」）の approve は拒否
        (dict(action="approve", var_ids=[ids["D"]]), ["rank D", ids["D"]]),
        # desc が空（未解釈）の approve も拒否
        (dict(action="approve", var_ids=[plain["var_id"]]), ["desc が未確定"]),
        # 混在（1件でも不可なら1件も承認しない）
        (dict(action="approve", var_ids=[ids["A"], ids["D"]]), [ids["D"]]),
        (dict(action="approve", var_ids=[]), ["var_ids が空"]),
        (dict(action="approve", var_ids=["V-9999"]), ["未定義の var_id"]),
        (dict(action="revise", var_id=ids["D"], desc="  "), ["意味（desc）"]),
        (dict(action="revise", var_id=ids["D"], desc="不明"), ["意味（desc）"]),
        (dict(action="revise", var_id="V-9999", desc="x"), ["未定義の var_id"]),
        (dict(action="bogus"), ["不明な action"]),
    ]
    before = (root / "data" / "variables.json").read_bytes()
    for kw, needles in cases:
        with _Refresh() as rf:
            res = review_actions.dict_action(str(root), kw.pop("action"), "テスト太郎", **kw)
        assert not res["ok"], (kw, res)
        for n in needles:
            assert n in res["message"], (kw, res["message"])
        assert rf.calls == [], (kw, "拒否したのに伝搬・再レンダが走っている")
    assert (root / "data" / "variables.json").read_bytes() == before   # 1件も書き換えない

    # variables.json が無いプロジェクトでは静かに断る（ウィジェットも出さない）
    empty = make_project()
    res = review_actions.dict_action(str(empty), "approve_all_ab", "テスト太郎")
    assert not res["ok"] and "variables.json" in res["message"]
    assert review_actions.dict_widget_html(str(empty)) is None
    print("OK  拒否（rank D / desc 空・「不明」/ 未定義 id / 不明 action → 無変更）")


# ---------- (d) serve_site の防御構造 ----------

def test_serve_site_route_and_guards():
    import inspect
    assert "/dict-action" in serve_site.Handler.WRITE_ROUTES
    assert hasattr(serve_site.Handler, "_handle_dict_action")
    src = inspect.getsource(serve_site.Handler.do_POST)
    assert '"/dict-action"' in src and "_handle_dict_action" in src
    # 既存 POST と同じ防御を通ることの確認（do_POST の前段で共通に判定している）
    for guard in ("WRITE_ROUTES", "127.0.0.1", "_cross_site_reason", "FROZEN"):
        assert guard in src, guard

    def reason(host, origin=None):
        headers = {"Host": host}
        if origin:
            headers["Origin"] = origin
        fake = types.SimpleNamespace(headers=headers,
                                     LOCAL_NAMES=serve_site.Handler.LOCAL_NAMES)
        return serve_site.Handler._cross_site_reason(fake)

    assert reason("127.0.0.1:8123") is None
    assert reason("localhost:8123", "http://127.0.0.1:8123") is None
    assert "不正な Host" in (reason("evil.example.com") or "")          # DNSリバインディング
    assert "クロスサイト" in (reason("127.0.0.1:8123", "https://evil.example.com") or "")
    print("OK  serve_site（WRITE_ROUTES 登録・Host/Origin 検証・FROZEN 無効化の存在）")


# ---------- 既存ウィジェットの回帰 ----------

def test_existing_widgets_untouched():
    js = browser_run.WIDGETS_JS
    for name in ("window.lrRun", "window.lrCheck", "window.lrReview",
                 "window.lrAdj", "window.lrAnalyze", "window.lrDict"):
        assert name in js, name
    assert js.count("/review-action") >= 1 and js.count("/dict-action") >= 1

    # ①②承認ウィジェットは従来どおり「承認待ちのときだけ」出る
    root, ids = prepared()
    assert review_actions.reviewable_status("spec") == "draft"
    # フィクスチャの F-0001 は reviewed 済み → ウィジェットなし
    assert review_actions.widget_html(str(root), "spec", "F-0001") is None
    (root / "docs" / "specs" / "F-0002.md").write_text(
        '---\ntitle: "t"\nstatus: draft\n---\n\n# 本文\n', encoding="utf-8")
    w = review_actions.widget_html(str(root), "spec", "F-0002")
    assert w and "lrReview.approve('F-0002','spec')" in w
    assert "lrDict" not in w                       # 辞書ウィジェットは混ざらない
    print("OK  既存①②承認ウィジェット・共有JSの回帰")


def main():
    tests = [
        test_widget_bulk_bar_and_rows,
        test_widget_injection_and_hidden_when_done,
        test_navbar_entry,
        test_dict_action_approve_all_ab,
        test_dict_action_approve_and_revise,
        test_dict_action_rejects,
        test_serve_site_route_and_guards,
        test_existing_widgets_untouched,
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
