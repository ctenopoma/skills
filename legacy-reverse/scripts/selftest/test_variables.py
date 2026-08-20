#!/usr/bin/env python3
"""variables.py のセルフテスト（pytest不要・素の assert）。

`python test_variables.py` で実行する。scripts/selftest/fixture_vars/ を毎回テンポラリに
コピーして CLI を叩き、生成物（data/variables.json・functions.json・docs/*.qmd）を検証する
（フィクスチャ自体は書き換えない）。

フィクスチャの構造:
  legacy/main.f  F-0000 MAIN     COMMON /TAXCOM/ RATE, LIMIT
                                 CALL CALCTAX(AMT, WRK, TAX) / CONVRT(FXR, YEN)
                                 CALL CONVRT(2.0D0, YEN2)  ← 実引数がリテラル（args=null）
                                 DATA AMT / WRITE+FORMAT / 宣言直上コメント
  legacy/tax.f   F-0001 CALCTAX(A, R, T)  COMMON /TAXCOM/ RTBL, LIM（別名参照）
  legacy/util.f  F-0002 CONVRT(RATE, OUT) ← MAIN の RATE とは別実体（同名別義）
                 F-0003 PACKIT(N)         EQUIVALENCE (WORK, WBUF)

検証項目:
  build          COMMON別名・call_binding・EQUIVALENCE のクラスタリング、同名別義の分離、
                 args=null の非結線、根拠バンドル5種、再実行の冪等性
  verify-interp  ルーブリック A/B/C/D と NG（欠落・余剰・引用捏造）
  approve/revise/propagate  転記書式と冪等性
  再 build       承認維持（occurrence_added）とクラスタ変化（cluster_changed）
  list-targets / page / conflicts  の生成
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

sys.path.insert(0, str(SCRIPTS_DIR))
import variables  # noqa: E402

_TMPDIRS = []


def make_project() -> Path:
    """フィクスチャをテンポラリにコピーして対象プロジェクトを作る。"""
    tmp = Path(tempfile.mkdtemp(prefix="lr-vars-"))
    _TMPDIRS.append(tmp)
    root = tmp / "proj"
    shutil.copytree(FIXTURE, root)
    return root


def run(root: Path, *args, root_first=False):
    """variables.py を subprocess 実行して (rc, stdout, stderr) を返す。"""
    cmd = [sys.executable, str(VARIABLES_PY)]
    cmd += (["--root", str(root), *args] if root_first else [*args, "--root", str(root)])
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
    return r.returncode, r.stdout, r.stderr


def load_vars(root: Path):
    return json.loads((root / "data" / "variables.json").read_text(encoding="utf-8"))["variables"]


def by_occ(vars_, key: str):
    """occurrence "F-0000:RATE" を含む変数を1件返す。"""
    hits = [v for v in vars_ if any(f"{o['func_id']}:{o['name']}" == key for o in v["occurrences"])]
    assert len(hits) == 1, f"{key} を含む変数が {len(hits)}件"
    return hits[0]


def occ_keys(v):
    return {f"{o['func_id']}:{o['name']}" for o in v["occurrences"]}


def ev_of(v, kind):
    """指定 kind の根拠の ev_id（先頭1件）。"""
    for e in v["evidence"]:
        if e["kind"] == kind:
            return e["ev_id"]
    raise AssertionError(f"{v['var_id']} に kind={kind} の根拠が無い: "
                         f"{[e['kind'] for e in v['evidence']]}")


def write_interp(root: Path, obj):
    (root / "data" / "interpretations.json").write_text(
        json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def edit_functions(root: Path, fn):
    p = root / "data" / "functions.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    fn(data)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------- build: クラスタリング ----------

def test_build_clustering():
    root = make_project()
    rc, out, err = run(root, "build")
    assert rc == 0, err
    vars_ = load_vars(root)
    assert len(vars_) == 9, [v["canonical_name"] for v in vars_]

    # ① COMMON 別名参照（/TAXCOM/ 位置1: RATE ↔ RTBL）が同一変数
    v_rate = by_occ(vars_, "F-0000:RATE")
    assert occ_keys(v_rate) == {"F-0000:RATE", "F-0001:RTBL"}
    assert v_rate["aliases"] == ["RATE", "RTBL"]
    assert v_rate["canonical_name"] == "RATE"
    assert any(l["kind"] == "common" for l in v_rate["links"])
    # 位置2（LIMIT ↔ LIM）も別変数として同様に結合
    v_lim = by_occ(vars_, "F-0000:LIMIT")
    assert occ_keys(v_lim) == {"F-0000:LIMIT", "F-0001:LIM"}
    assert v_lim["canonical_name"] == "LIMIT"          # 出現数同数なら情報量の多い名前
    assert v_rate["var_id"] != v_lim["var_id"]

    # ② call_sites の実引数 ↔ 仮引数（位置対応）
    v_amt = by_occ(vars_, "F-0000:AMT")
    assert occ_keys(v_amt) == {"F-0000:AMT", "F-0001:A"}
    link = [l for l in v_amt["links"] if l["kind"] == "call_binding"]
    assert link and link[0]["from"] == "F-0000:AMT" and link[0]["to"] == "F-0001:A"
    assert link[0]["line"] == 14
    assert occ_keys(by_occ(vars_, "F-0000:WRK")) == {"F-0000:WRK", "F-0001:R"}
    assert occ_keys(by_occ(vars_, "F-0000:TAX")) == {"F-0000:TAX", "F-0001:T"}
    # 役割は functions.json の inputs/outputs 由来
    roles = {o["name"]: o["role"] for o in v_amt["occurrences"]}
    assert roles == {"AMT": "local", "A": "input"}

    # args が null（CALL CONVRT(2.0D0, YEN2) の第1実引数）は結線しない。
    # 第2実引数 YEN2 だけが OUT に結線される
    v_out = by_occ(vars_, "F-0002:OUT")
    assert occ_keys(v_out) == {"F-0000:YEN", "F-0000:YEN2", "F-0002:OUT"}
    assert not any("2.0D0" in (o["name"] or "") for v in vars_ for o in v["occurrences"])

    # ③ EQUIVALENCE（同一関数内の WORK ↔ WBUF）
    v_eq = by_occ(vars_, "F-0003:WORK")
    assert occ_keys(v_eq) == {"F-0003:WORK", "F-0003:WBUF"}
    assert any(l["kind"] == "equivalence" for l in v_eq["links"])

    # 別関数の同名だけでは結合しない（MAIN の RATE と CONVRT の RATE は別実体）
    v_fx = by_occ(vars_, "F-0002:RATE")
    assert v_fx["var_id"] != v_rate["var_id"]
    assert occ_keys(v_fx) == {"F-0000:FXR", "F-0002:RATE"}
    # 同名別クラスタは相互に name_collision
    assert "name_collision" in v_fx["flags"] and "name_collision" in v_rate["flags"]
    assert sum(1 for v in vars_ if "name_collision" in v["flags"]) == 2
    assert "同名別義(name_collision): 2件" in out
    print("OK  build クラスタリング（COMMON別名 / call_binding / EQUIVALENCE / 同名別義 / null実引数）")


def test_build_evidence():
    root = make_project()
    assert run(root, "build")[0] == 0
    vars_ = load_vars(root)

    v_rate = by_occ(vars_, "F-0000:RATE")
    kinds = [e["kind"] for e in v_rate["evidence"]]
    assert "comment" in kinds and "common_pos" in kinds and "usage_expr" in kinds
    cmt = [e for e in v_rate["evidence"] if e["kind"] == "comment"][0]
    assert cmt["file"] == "legacy/main.f" and cmt["line"] == 3
    assert "ANNUAL TAX RATE" in cmt["text"]
    cpos = [e for e in v_rate["evidence"] if e["kind"] == "common_pos"]
    assert len(cpos) == 2 and "COMMON /TAXCOM/ 位置1" in cpos[0]["text"]

    v_amt = by_occ(vars_, "F-0000:AMT")
    di = [e for e in v_amt["evidence"] if e["kind"] == "data_init"]
    assert di and "DATA AMT" in di[0]["text"]
    assert "課税対象額" in [e["text"] for e in v_amt["evidence"] if e["kind"] == "comment"][0]

    v_tax = by_occ(vars_, "F-0000:TAX")
    fl = [e for e in v_tax["evidence"] if e["kind"] == "format_label"]
    assert fl and "TAX AMOUNT" in fl[0]["text"], v_tax["evidence"]

    # usage_expr は上限5件
    for v in vars_:
        assert sum(1 for e in v["evidence"] if e["kind"] == "usage_expr") <= 5
    # ev_id は E-<var連番>-<枝番>、evidence_hash は8桁
    for v in vars_:
        num = v["var_id"].split("-")[1]
        for i, e in enumerate(v["evidence"], 1):
            assert e["ev_id"] == f"E-{num}-{i:02d}"
        assert len(v["evidence_hash"]) == 8
    print("OK  build 根拠バンドル（comment / format_label / data_init / usage_expr / common_pos）")


def test_build_idempotent():
    root = make_project()
    assert run(root, "build")[0] == 0
    first = (root / "data" / "variables.json").read_bytes()
    assert run(root, "build", root_first=True)[0] == 0     # --root を前に置く形式も動く
    assert (root / "data" / "variables.json").read_bytes() == first
    assert all(v["flags"] in ([], ["name_collision"]) for v in load_vars(root))
    print("OK  build 再実行の冪等性（var_id・flags が変化しない）")


# ---------- verify-interp ----------

def _rubric_payload(root: Path):
    vars_ = load_vars(root)
    v_a = by_occ(vars_, "F-0000:RATE")      # comment 引用 → A
    v_b = by_occ(vars_, "F-0002:RATE")      # usage_expr 引用＋ドメイン知識の語 → B
    v_c = by_occ(vars_, "F-0003:WORK")      # usage_expr のみ → C
    v_d = by_occ(vars_, "F-0000:NCNT")      # 引用なし → D
    payload = {
        v_a["var_id"]: {"desc": "年間税率", "unit": "無次元(比率)", "rank_claim": "A",
                        "evidence_cited": [ev_of(v_a, "comment")], "notes": ""},
        v_b["var_id"]: {"desc": "為替レート", "unit": "円/ドル", "rank_claim": "A",
                        "evidence_cited": [ev_of(v_b, "usage_expr")], "notes": ""},
        v_c["var_id"]: {"desc": "作業用配列", "unit": None, "rank_claim": "B",
                        "evidence_cited": [ev_of(v_c, "usage_expr")], "notes": ""},
        v_d["var_id"]: {"desc": "処理件数", "unit": "件", "rank_claim": "A",
                        "evidence_cited": [], "notes": ""},
    }
    ids = ",".join(sorted(payload))
    return payload, ids, (v_a, v_b, v_c, v_d)


def test_verify_interp_rubric():
    root = make_project()
    assert run(root, "build")[0] == 0
    payload, ids, (v_a, v_b, v_c, v_d) = _rubric_payload(root)
    write_interp(root, payload)
    rc, out, err = run(root, "verify-interp", "--ids", ids)
    assert rc == 0, out + err
    vars_ = {v["var_id"]: v for v in load_vars(root)}

    a = vars_[v_a["var_id"]]
    assert a["rank"] == "A" and a["status"] == "interpreted"
    assert a["desc"] == "年間税率" and a["unit"] == "無次元(比率)"
    assert a["confidence_basis"] == [ev_of(v_a, "comment")]

    b = vars_[v_b["var_id"]]
    assert b["rank"] == "B", b            # LLM の申告 A ではなく検証側が B に決める
    assert "domain-knowledge" in out

    c = vars_[v_c["var_id"]]
    assert c["rank"] == "C" and c["status"] == "interpreted"

    d = vars_[v_d["var_id"]]
    assert d["rank"] == "D" and d["status"] == "unreviewed"   # マージ拒否
    assert d["desc"] == "不明" and "needs_human" in d["flags"]

    # applied として別名保存され、interpretations.json は消費される
    assert not (root / "data" / "interpretations.json").exists()
    applied = list((root / "data").glob("interpretations-applied-*.json"))
    assert len(applied) == 1
    assert json.loads(applied[0].read_text(encoding="utf-8")) == payload
    print("OK  verify-interp ルーブリック（A/B/C/D と rank の検証側決定）")


def test_verify_interp_ng():
    root = make_project()
    assert run(root, "build")[0] == 0
    payload, ids, (v_a, v_b, v_c, v_d) = _rubric_payload(root)
    before = (root / "data" / "variables.json").read_bytes()

    # 欠落
    short = {k: v for k, v in payload.items() if k != v_c["var_id"]}
    write_interp(root, short)
    rc, out, err = run(root, "verify-interp", "--ids", ids)
    assert rc == 1 and "欠落" in out and v_c["var_id"] in out

    # 余剰
    extra = dict(payload)
    extra["V-0002"] = {"desc": "余計な解釈", "evidence_cited": []}
    write_interp(root, extra)
    rc, out, err = run(root, "verify-interp", "--ids",
                       ",".join(sorted(k for k in payload)))
    assert rc == 1 and "余剰" in out and "V-0002" in out

    # 引用捏造（存在しない ev_id / 他変数の ev_id）
    bogus = json.loads(json.dumps(payload))
    bogus[v_a["var_id"]]["evidence_cited"] = ["E-9999-01"]
    bogus[v_c["var_id"]]["evidence_cited"] = [ev_of(v_a, "comment")]
    write_interp(root, bogus)
    rc, out, err = run(root, "verify-interp", "--ids", ids)
    assert rc == 1 and "引用NG" in out and "E-9999-01" in out

    # NG のときは1件もマージしない（variables.json は無傷、入力も残る）
    assert (root / "data" / "variables.json").read_bytes() == before
    assert (root / "data" / "interpretations.json").exists()
    assert not list((root / "data").glob("interpretations-applied-*.json"))
    print("OK  verify-interp NG（欠落 / 余剰 / 引用捏造 → 非マージで exit 1）")


# ---------- approve / revise / propagate ----------

def _approved_project():
    """A(年間税率) を承認、AMT を revise で承認したプロジェクトを作る。"""
    root = make_project()
    assert run(root, "build")[0] == 0
    payload, ids, (v_a, v_b, v_c, v_d) = _rubric_payload(root)
    write_interp(root, payload)
    assert run(root, "verify-interp", "--ids", ids)[0] == 0
    rc, out, err = run(root, "approve", v_a["var_id"], "--by", "テスト太郎")
    assert rc == 0, err
    v_amt = by_occ(load_vars(root), "F-0000:AMT")
    rc, out, err = run(root, "revise", v_amt["var_id"], "--desc", "課税対象額",
                       "--unit", "円", "--by", "テスト太郎")
    assert rc == 0, err
    return root, v_a["var_id"], v_amt["var_id"]


def test_approve_and_propagate_idempotent():
    root, vid_rate, vid_amt = _approved_project()
    vars_ = {v["var_id"]: v for v in load_vars(root)}
    assert vars_[vid_rate]["status"] == "approved"
    assert vars_[vid_rate]["approved_by"] == "テスト太郎" and vars_[vid_rate]["approved_date"]
    assert vars_[vid_amt]["status"] == "approved" and vars_[vid_amt]["unit"] == "円"

    # desc 未確定の変数は approve できない（「不明」も拒否）
    v_d = by_occ(load_vars(root), "F-0000:NCNT")
    rc, out, err = run(root, "approve", v_d["var_id"], "--by", "テスト太郎")
    assert rc == 1 and "desc" in (out + err)

    rc, out, err = run(root, "propagate")
    assert rc == 0, err
    funcs = json.loads((root / "data" / "functions.json").read_text(encoding="utf-8"))["functions"]
    fmap = {f["func_id"]: f for f in funcs}
    a_row = [r for r in fmap["F-0001"]["inputs"] if r["name"] == "A"][0]
    assert a_row["desc"] == f"課税対象額(円) [{vid_amt}]", a_row
    # COMMON メンバーはブロック擬似エントリに注記される
    g0 = fmap["F-0000"]["globals"][0]["desc"]
    g1 = fmap["F-0001"]["globals"][0]["desc"]
    assert f"RATE=年間税率(無次元(比率)) [{vid_rate}]" in g0, g0
    assert f"RTBL=年間税率(無次元(比率)) [{vid_rate}]" in g1, g1
    # 未承認の変数は転記されない
    assert [r for r in fmap["F-0001"]["inputs"] if r["name"] == "R"][0]["desc"] == ""

    snapshot = (root / "data" / "functions.json").read_bytes()
    rc, out, err = run(root, "propagate")
    assert rc == 0, err
    assert "desc 0件" in out and "注記 0件" in out
    assert (root / "data" / "functions.json").read_bytes() == snapshot
    print("OK  approve / revise / propagate（転記書式と冪等性）")


def test_revise_reaches_untouched_skeleton():
    """人が辞書を直したら、①未着手の骨子まで反映されること。

    語義の正は辞書。revise → propagate → skeletons の順で、
      - IO 行（inputs/outputs/globals）の desc が差し替わる
      - **COMMON ブロックの注記も差し替わる**（var_id の存在だけを見て冪等扱いすると
        古い意味が残る。実際そうなっていた）
      - status: skeleton の骨子は機械生成ブロックごと作り直され、①が読む IO 表が現在値になる
      - draft / reviewed（書かれた成果物）は触らない
    """
    import subprocess

    root, vid_rate, vid_amt = _approved_project()

    def led(*a):
        r = subprocess.run([sys.executable, str(SCRIPTS_DIR / "ledger.py"),
                            "--root", str(root), *a],
                           capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 0, r.stderr
        return r.stdout

    assert run(root, "propagate")[0] == 0
    led("skeletons")
    reviewed_before = (root / "docs" / "specs" / "F-0001.md").read_text(encoding="utf-8")

    def funcs():
        return {f["func_id"]: f for f in json.loads(
            (root / "data" / "functions.json").read_text(encoding="utf-8"))["functions"]}

    g0 = funcs()["F-0000"]["globals"][0]["desc"]
    assert f"RATE=年間税率(無次元(比率)) [{vid_rate}]" in g0, g0

    # 人が語義を直す
    rc, out, err = run(root, "revise", vid_rate, "--desc", "年利率（改訂）",
                       "--unit", "％", "--by", "山田")
    assert rc == 0, err
    rc, out, err = run(root, "propagate")
    assert rc == 0, err

    g0 = funcs()["F-0000"]["globals"][0]["desc"]
    assert f"RATE=年利率（改訂）(％) [{vid_rate}]" in g0, g0
    assert "年間税率" not in g0, f"COMMON 注記に古い意味が残っている: {g0}"

    out = led("skeletons")
    assert "作り直し" in out, out
    sk = (root / "docs" / "specs" / "F-0000.md").read_text(encoding="utf-8")
    assert "年利率（改訂）" in sk, "①未着手の骨子に新しい語義が届いていない"
    assert "年間税率" not in sk, "骨子に古い語義が残っている"
    # 書かれた成果物（reviewed）は1バイトも触らない
    assert (root / "docs" / "specs" / "F-0001.md").read_text(encoding="utf-8") == reviewed_before
    print("OK  辞書の修正が①未着手の骨子まで届く（書かれた成果物は不変）")


def test_spec_must_keep_dict_refs():
    """①が辞書からの転記（`… [V-xxxx]`）を書き換えたら機械レビューが NG にすること。

    語義の正は辞書側。指摘を人が毎回繰り返さずに済むよう、pipeline のリトライ
    ループに乗る形（＝review_checks の problems）で機械が見る。
    """
    import review_checks

    root, vid_rate, vid_amt = _approved_project()
    assert run(root, "propagate")[0] == 0
    spec_dir = root / "docs" / "specs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    sp = spec_dir / "F-0001.md"

    fmap = {f["func_id"]: f for f in json.loads(
        (root / "data" / "functions.json").read_text(encoding="utf-8"))["functions"]}
    glob_desc = fmap["F-0001"]["globals"][0]["desc"]     # COMMON 擬似エントリ（注記つき）

    def write(desc_cell: str) -> None:
        sp.write_text(
            "---\ntitle: t\nfunc-id: \"F-0001\"\nstatus: draft\n---\n\n"
            "# 概要\n\nx\n\n## 入力\n\n"
            "| # | 名前 | 説明 |\n|---|------|------|\n"
            f"| 1 | A | {desc_cell} |\n\n## グローバル状態\n\n"
            "| 名前 | 説明 |\n|------|------|\n"
            f"| {fmap['F-0001']['globals'][0]['name']} | {glob_desc} |\n", encoding="utf-8")

    # 転記をそのまま載せている → この検査は何も言わない
    write(f"課税対象額(円) [{vid_amt}]")
    assert review_checks._check_dict_refs(root, "F-0001", sp.read_text(encoding="utf-8")) == []

    # ①が自分の言葉で書き換えた → NG（期待する文言をそのまま示す）
    write("入力の金額")
    problems = review_checks._check_dict_refs(root, "F-0001", sp.read_text(encoding="utf-8"))
    assert problems, "辞書の転記を書き換えても素通りしている"
    assert vid_amt in problems[0] and "課税対象額(円)" in problems[0], problems
    assert "revise" in problems[0], "辞書側を直す導線が案内されていない"

    # check_spec 経由でも同じ指摘が出る（pipeline の検証はこちらを通る）
    assert any(vid_amt in x for x in review_checks.check_spec(str(root), "F-0001")["problems"])

    # propagate が置けていない変数は要求しない（誤検知を作らない）
    funcs = root / "data" / "functions.json"
    funcs.write_text(funcs.read_text(encoding="utf-8").replace(f" [{vid_amt}]", "")
                     .replace(f" [{vid_rate}]", ""), encoding="utf-8")
    review_checks._FUNCS_CACHE.clear()
    assert review_checks._check_dict_refs(root, "F-0001", sp.read_text(encoding="utf-8")) == []
    print("OK  ①は辞書の転記を書き換えられない（機械レビューで NG）")


# ---------- 再 build（P4: 後発合流） ----------

def test_rebuild_occurrence_added():
    root, vid_rate, vid_amt = _approved_project()
    # 追加開発で PACKIT が CALCTAX を呼ぶようになった（新しい実引数 3つ）
    def add_site(data):
        for f in data["functions"]:
            if f["func_id"] == "F-0003":
                f.setdefault("calls", []).append("F-0001")
                f["call_sites"] = [{"callee": "F-0001", "name": "CALCTAX", "line": 13,
                                    "args": ["PAMT", "PR", "PT"]}]
    edit_functions(root, add_site)
    rc, out, err = run(root, "build")
    assert rc == 0, err
    vars_ = {v["var_id"]: v for v in load_vars(root)}

    amt = vars_[vid_amt]
    assert occ_keys(amt) == {"F-0000:AMT", "F-0001:A", "F-0003:PAMT"}
    assert amt["status"] == "approved" and amt["approved_by"] == "テスト太郎"
    assert amt["desc"] == "課税対象額" and amt["unit"] == "円"
    assert "occurrence_added" in amt["flags"]
    # 影響の無かった承認済み変数はフラグも付かない
    rate = vars_[vid_rate]
    assert rate["status"] == "approved" and "occurrence_added" not in rate["flags"]
    assert "cluster_changed" not in rate["flags"]
    print("OK  再build 承認維持（occurrence_added）")


def test_rebuild_cluster_changed():
    root, vid_rate, vid_amt = _approved_project()
    # 追加開発で MAIN が COMMON の RATE をそのまま CONVRT に渡すようになった
    # → 別実体だった2クラスタが併合される
    def rebind(data):
        for f in data["functions"]:
            if f["func_id"] == "F-0000":
                for s in f["call_sites"]:
                    if s["name"] == "CONVRT" and s["args"][0] == "FXR":
                        s["args"][0] = "RATE"
    edit_functions(root, rebind)
    rc, out, err = run(root, "build")
    assert rc == 0, err
    assert "クラスタ変化: 1件" in out
    vars_ = {v["var_id"]: v for v in load_vars(root)}

    rate = vars_[vid_rate]
    assert occ_keys(rate) == {"F-0000:RATE", "F-0001:RTBL", "F-0002:RATE"}
    assert "cluster_changed" in rate["flags"]
    assert rate["status"] == "unreviewed"                 # 意味の同一性の前提が崩れた
    assert rate["approved_by"] is None and rate["approved_date"] is None
    assert rate["desc"] == "年間税率"                       # 参考として desc は残す
    # 併合された側（FXR/RATE）は消滅し、同名別義も解消される
    assert not any("name_collision" in v["flags"] for v in vars_.values())
    # 無関係の承認済み変数は維持
    assert vars_[vid_amt]["status"] == "approved"
    print("OK  再build クラスタ変化（cluster_changed → status を unreviewed に戻す）")


# ---------- list-targets / page / conflicts ----------

def test_list_targets():
    root = make_project()
    assert run(root, "build")[0] == 0
    rc, out, err = run(root, "list-targets", "--limit", "3")
    assert rc == 0, err
    rows = json.loads(out)
    assert len(rows) == 3
    r0 = rows[0]
    assert set(r0) >= {"var_id", "canonical_name", "aliases", "occurrences", "evidence", "links"}
    assert r0["occurrences"][0]["legacy_name"]            # 関数名の文脈が入る
    assert r0["occurrences"][0]["role"]
    print("OK  list-targets（解釈バッチ用の最小コンテキスト）")


def test_page_and_conflicts():
    root, vid_rate, vid_amt = _approved_project()
    assert run(root, "propagate")[0] == 0
    rc, out, err = run(root, "page")
    assert rc == 0, err
    page = (root / "docs" / "variables.qmd").read_text(encoding="utf-8")
    assert page.startswith("---\ntitle: \"変数辞書\"\n---")
    assert "手編集禁止" in page
    for v in load_vars(root):
        assert f"{{#dict-{v['var_id']}}}" in page
    assert "# 確認待ち" in page and "# 一括承認候補" in page and "# 承認済み" in page
    assert "同名別義" in page
    # 承認済みセクションに承認した2件が並ぶ
    approved_sec = page.split("# 承認済み", 1)[1]
    assert vid_rate in approved_sec and vid_amt in approved_sec
    # 並び順: 出現関数数 降順 × rank 昇順（C/D が先）
    queue = page.split("# 確認待ち", 1)[1].split("# 一括承認候補", 1)[0]
    order = [l.split("]")[0].lstrip("| [") for l in queue.splitlines() if l.startswith("| [V-")]
    vmap = {v["var_id"]: v for v in load_vars(root)}
    keys = [(-len({o["func_id"] for o in vmap[i]["occurrences"]}),
             variables.RANK_ORDER.get(vmap[i]["rank"], 0)) for i in order]
    assert keys == sorted(keys), keys

    rc, out, err = run(root, "conflicts")
    assert rc == 0, err
    md = (root / "docs" / "dict-conflicts.md").read_text(encoding="utf-8")
    # reviewed 済みの F-0001 は辞書参照を持たないので候補に挙がる（自動修正はしない）
    assert "F-0001" in md and vid_amt in md and "辞書参照なし" in md
    assert "手編集禁止" in md
    print("OK  page / conflicts（アンカー・並び順・矛盾候補の列挙）")


def main():
    tests = [
        test_build_clustering,
        test_build_evidence,
        test_build_idempotent,
        test_verify_interp_rubric,
        test_verify_interp_ng,
        test_approve_and_propagate_idempotent,
        test_spec_must_keep_dict_refs,
        test_revise_reaches_untouched_skeleton,
        test_rebuild_occurrence_added,
        test_rebuild_cluster_changed,
        test_list_targets,
        test_page_and_conflicts,
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
