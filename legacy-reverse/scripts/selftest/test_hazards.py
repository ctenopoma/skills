#!/usr/bin/env python3
"""例外ポリシー機構（M4）のセルフテスト（pytest不要・素の assert）。

`python test_hazards.py` で実行する。検証範囲:

1. extract_fortran.py の hazard 検出器 — 検知すべきもの／してはならないもの（誤検知）の両方
   （fixture_hazards/legacy: 固定形式 haz.f と自由形式 hazfree.f90）
2. 再抽出時のマージ — hazards は常に上書き、manual エントリは触らない
3. hazards.py match — 全体既定 → 関数 → 個別 の3段階優先順位、hazard-map.json、質問キュー
4. hazards.py add-policy — テンプレからの生成・EP-ID 自動採番・追記・再突合
5. review_checks.py spec — (a)検討漏れ (b)EP捏造 (c)未決定のまま仕様化 のNGと、
   hazard を持たない関数の素通り
6. 後方互換 — hazards キーの無い旧 functions.json では hazard 検査が全部スキップされる
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
FIXTURE = HERE / "fixture_hazards"
HAZARDS_PY = SCRIPTS_DIR / "hazards.py"

sys.path.insert(0, str(SCRIPTS_DIR))
import extract_fortran                                             # noqa: E402
import hazards                                                     # noqa: E402
import review_checks                                               # noqa: E402


def run_cli(root, *args):
    """hazards.py を subprocess で実行し (returncode, stdout, stderr) を返す。"""
    cmd = [sys.executable, str(HAZARDS_PY), *args, "--root", str(root)]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
    return r.returncode, r.stdout, r.stderr


def new_project(tmp: Path, name: str, with_legacy: bool = True) -> Path:
    root = tmp / name
    root.mkdir(parents=True)
    if with_legacy:
        shutil.copytree(FIXTURE / "legacy", root / "legacy")
    return root


def extracted_project(tmp: Path, name: str) -> tuple:
    root = new_project(tmp, name)
    res = extract_fortran.extract(str(root), write=True)
    assert res["ok"], res
    data = json.loads((root / "data" / "functions.json").read_text(encoding="utf-8"))
    return root, data


def by_name(data: dict) -> dict:
    return {f["legacy"]["name"]: f for f in data["functions"]}


def triples(f: dict) -> list:
    return [(h["kind"], h["expr"], tuple(h["vars"])) for h in f.get("hazards", [])]


# ---------- 1. 検出器 ----------

def test_detectors(tmp: Path):
    root, data = extracted_project(tmp, "detect")
    fn = by_name(data)
    assert set(fn) == {"HAZCALC", "HAZMSG", "RATIO"}, sorted(fn)

    # 固定形式: 検知すべきものが過不足なく並ぶ（順序は行順、hz_id は関数内連番）
    assert triples(fn["HAZCALC"]) == [
        ("div_by_var", "A / B", ("B",)),                 # 分母が変数
        ("div_by_var", "R / (A + B)", ("A", "B")),       # 分母が式 → vars は両方
        ("sqrt_arg", "SQRT(A)", ("A",)),
        ("sqrt_arg", "DSQRT(B + 1.0D0)", ("B",)),        # 倍精度版・指数部は数値として除く
        ("log_arg", "ALOG(B)", ("B",)),
        ("array_index_var", "TBL(N)", ("N",)),           # 型宣言の配列
        ("array_index_var", "WK(N)", ("N",)),            # DIMENSION 文の配列
        ("array_index_var", "COEF(N)", ("N",)),          # COMMON 内の配列
    ], triples(fn["HAZCALC"])
    assert [h["hz_id"] for h in fn["HAZCALC"]["hazards"]] == \
        [f"H-0001-{i:02d}" for i in range(1, 9)]
    lines = {h["hz_id"]: h["line"] for h in fn["HAZCALC"]["hazards"]}
    src = (root / "legacy" / "haz.f").read_text(encoding="utf-8").splitlines()
    assert "R = A / B" in src[lines["H-0001-01"] - 1], src[lines["H-0001-01"] - 1]
    assert "TBL(N)" in src[lines["H-0001-06"] - 1], src[lines["H-0001-06"] - 1]

    # 誤検知してはならないもの（フィクスチャに全部入っている）
    exprs = [e for f in data["functions"] for _, e, _ in triples(f)]
    for ng, why in [("/ 4", "純リテラルの分母"), ("2.0D0", "純リテラルの分母(倍精度)"),
                    ("SQRT(2.0)", "定数だけの SQRT"), ("TBL(3)", "定数添字"),
                    ("//", "文字列連結"), ("/=", "比較演算子"), ("(/", "配列構成子"),
                    ("CFG", "COMMON ブロック名の /"), ("1X", "FORMAT 文の /")]:
        assert not any(ng in e for e in exprs), f"{why} を hazard として誤検知した: {exprs}"

    # 自由形式: 文字列連結・/=・配列構成子は拾わず、除算だけ拾う
    assert triples(fn["HAZMSG"]) == [
        ("div_by_var", "real(cnt) / den", ("DEN",)),
        ("div_by_var", "rate / ratio(cnt)", ("CNT",)),   # 関数名は変数に数えない
    ], triples(fn["HAZMSG"])
    assert triples(fn["RATIO"]) == [
        ("div_by_var", "1.0 / real(k + 1)", ("K",)),     # 組み込み関数名も変数に数えない
    ], triples(fn["RATIO"])
    assert [h["hz_id"] for h in fn["HAZMSG"]["hazards"]] == ["H-0002-01", "H-0002-02"]

    # 抽出サマリ（監査ログ）にも件数が出る
    rep = json.loads((root / "data" / "extract-report.json").read_text(encoding="utf-8"))
    assert rep["hazard_counts"] == {"div_by_var": 5, "sqrt_arg": 2,
                                    "log_arg": 1, "array_index_var": 3}, rep["hazard_counts"]
    print("OK  検出器（検知すべき / 誤検知してはならない）")


def test_detector_table_is_extensible():
    """kind → 走査関数のテーブルとして公開されていること（検出器の追加口）。"""
    assert set(extract_fortran.HAZARD_DETECTORS) == {
        "div_by_var", "sqrt_arg", "log_arg", "array_index_var"}
    assert all(callable(v) for v in extract_fortran.HAZARD_DETECTORS.values())
    print("OK  検出器テーブル（拡張可能）")


# ---------- 2. マージ ----------

def test_merge_overwrites_hazards(tmp: Path):
    root, data = extracted_project(tmp, "merge")
    fj = root / "data" / "functions.json"
    expected = {f["func_id"]: f.get("hazards", []) for f in data["functions"]}

    # 人が書き換えた hazards（ソース由来なので上書きされるべき）と manual エントリ
    for f in data["functions"]:
        if f["func_id"] == "F-0001":
            f["hazards"] = [{"hz_id": "H-0001-01", "kind": "手書き", "line": 1,
                             "expr": "HAND", "vars": []}]
            f["inputs"] = [{"name": "A", "legacy_type": "REAL", "new_type": "float",
                            "desc": "手で書いた説明"}]
    data["functions"].append({
        "func_id": "F-0099", "manual": True,
        "legacy": {"file": "legacy/vendor.f", "name": "VENDOR", "lines": "1-1",
                   "kind": "subroutine"},
        "new": {"module": "src/newpkg/vendor.py", "name": "vendor", "signature": ""},
        "inputs": [], "outputs": [], "globals": [], "external_files": [], "calls": [],
        "hazards": [{"hz_id": "H-0099-01", "kind": "div_by_var", "line": 1,
                     "expr": "KEEPME", "vars": ["Z"]}]})
    fj.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    res = extract_fortran.extract(str(root), write=True)
    assert res["ok"] and res["added"] == 0, res
    raw = fj.read_text(encoding="utf-8")
    assert "_hazards" not in raw, "内部キー _hazards が漏れている"
    data2 = json.loads(raw)
    got = {f["func_id"]: f.get("hazards", []) for f in data2["functions"]}
    for fid, hz in expected.items():
        assert got[fid] == hz, f"{fid}: hazards が再抽出で上書きされていない"
    manual = next(f for f in data2["functions"] if f["func_id"] == "F-0099")
    assert manual["hazards"][0]["expr"] == "KEEPME", "manual エントリの hazards を触っている"
    f1 = next(f for f in data2["functions"] if f["func_id"] == "F-0001")
    assert f1["inputs"][0]["desc"] == "手で書いた説明", "手修正（desc）が消えた"
    print("OK  再抽出マージ（hazards は常に上書き・manual は不変）")


# ---------- 3. match（3段階の優先順位）とキュー ----------

POLICY_HEADER = ["---", 'title: "例外ポリシー登録簿"', "---", "",
                 "| EP-ID | 対象 | 適用範囲 | 決定 | 備考 | 承認 |",
                 "|-------|------|---------|------|------|------|"]


def write_policy(root: Path, rows: list) -> None:
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "exception-policy.md").write_text(
        "\n".join(POLICY_HEADER + rows) + "\n", encoding="utf-8")


def test_match_precedence(tmp: Path):
    root, _ = extracted_project(tmp, "match")
    write_policy(root, [
        "| EP-001 | div_by_var | 全体既定 | guard_raise | 0割は例外送出 | 山田 2026-08-08 |",
        "| EP-002 | div_by_var F-0001 | 関数 | detect_only | この関数は記載のみ | 山田 2026-08-08 |",
        "| EP-003 | div_by_var F-0001 H-0001-02 | 個別 | caller_guarantees | 上流保証 | 山田 2026-08-08 |",
    ])
    res = hazards.match_hazards(root, write=True)
    assert res["total"] == 11 and res["decided"] == 5 and res["undecided"] == 6, res
    m = json.loads((root / "data" / "hazard-map.json").read_text(encoding="utf-8"))
    assert m["H-0001-02"]["ep"] == "EP-003", m["H-0001-02"]      # 個別 > 関数 > 全体既定
    assert m["H-0001-01"]["ep"] == "EP-002", m["H-0001-01"]      # 関数 > 全体既定
    assert m["H-0002-01"]["ep"] == "EP-001", m["H-0002-01"]      # 全体既定
    assert m["H-0003-01"] == {"ep": "EP-001", "decision": "guard_raise",
                              "func_id": "F-0003", "kind": "div_by_var"}, m["H-0003-01"]
    assert m["H-0001-03"]["ep"] is None, m["H-0001-03"]          # sqrt_arg は未決定
    assert m["H-0001-01"]["decision"] == "detect_only"
    assert res["by_kind"]["div_by_var"] == {"total": 5, "decided": 5}, res["by_kind"]
    assert res["by_kind"]["sqrt_arg"] == {"total": 2, "decided": 0}, res["by_kind"]

    # 同ランクに複数ある場合は後から登録した行が勝つ
    write_policy(root, [
        "| EP-001 | sqrt_arg | 全体既定 | detect_only | 先に決めた | 山田 2026-08-08 |",
        "| EP-002 | sqrt_arg | 全体既定 | guard_raise | 決め直した | 山田 2026-08-09 |",
    ])
    m2 = hazards.match_hazards(root, write=False)["map"]
    assert m2["H-0001-03"]["ep"] == "EP-002", m2["H-0001-03"]
    print("OK  match の3段階優先順位（個別 > 関数 > 全体既定）")


def test_queue(tmp: Path):
    root, _ = extracted_project(tmp, "queue")
    write_policy(root, [
        "| EP-001 | div_by_var | 全体既定 | guard_raise | 0割は例外送出 | 山田 2026-08-08 |"])
    hazards.match_hazards(root, write=True)
    q = (root / "docs" / "exception-queue.md").read_text(encoding="utf-8")
    assert "未決定の hazard: **6 件**" in q, q[:400]
    assert "## sqrt_arg — 2 箇所" in q and "## log_arg — 1 箇所" in q
    assert "## array_index_var — 3 箇所" in q
    assert "## div_by_var" not in q, "決定済みの kind がキューに残っている"
    for opt in hazards.DECISIONS:
        assert f"`{opt}`" in q, f"選択肢 {opt} が質問に出ていない"
    assert "式ごとに個別判断" in q
    assert "H-0001-03" in q and "legacy/haz.f:17" in q and "`SQRT(A)`" in q
    assert "add-policy --kind sqrt_arg" in q, "登録コマンドの案内がない"

    # 全部決めたら「未決定なし」で上書きされる
    write_policy(root, [
        "| EP-001 | div_by_var | 全体既定 | guard_raise | | 山田 2026-08-08 |",
        "| EP-002 | sqrt_arg | 全体既定 | guard_raise | | 山田 2026-08-08 |",
        "| EP-003 | log_arg | 全体既定 | guard_value | 0 のとき 0.0 | 山田 2026-08-08 |",
        "| EP-004 | array_index_var | 全体既定 | detect_only | | 山田 2026-08-08 |"])
    res = hazards.match_hazards(root, write=True)
    assert res["ok"] and res["undecided"] == 0, res
    q2 = (root / "docs" / "exception-queue.md").read_text(encoding="utf-8")
    assert "未決定の例外ポリシーはありません" in q2, q2[:400]
    assert "H-0001-03" not in q2, "キューが上書きされていない"

    # 語彙外の決定は登録簿NGとして報告し、その EP は適用しない
    write_policy(root, [
        "| EP-001 | div_by_var | 全体既定 | ignore_it | 語彙外 | 山田 2026-08-08 |"])
    res = hazards.match_hazards(root, write=False)
    assert res["undecided"] == 11 and res["policy_problems"], res
    assert "語彙外" in res["policy_problems"][0]
    print("OK  質問キュー（未決定の集約・全決定時の上書き・語彙検証）")


# ---------- 4. add-policy ----------

def test_add_policy(tmp: Path):
    root, _ = extracted_project(tmp, "addpolicy")
    assert not (root / "docs" / "exception-policy.md").exists()

    rc, out, err = run_cli(root, "add-policy", "--kind", "div_by_var",
                           "--decision", "guard_raise", "--by", "山田",
                           "--note", "0割は ZeroDivisionError")
    assert rc == 0, err
    assert "EP-001" in out, out
    pol_path = root / "docs" / "exception-policy.md"
    assert pol_path.exists(), "テンプレからの生成に失敗"
    text = pol_path.read_text(encoding="utf-8")
    assert "| EP-ID | 対象 | 適用範囲 | 決定 | 備考 | 承認 |" in text
    assert "guard_raise" in text and "山田" in text

    # テンプレのコメント内にある記入例は EP として読まれない（採番が 001 から始まる）
    pol = hazards.parse_policy(root)
    assert [p["ep"] for p in pol["policies"]] == ["EP-001"], pol["policies"]
    assert pol["problems"] == [], pol["problems"]
    p0 = pol["policies"][0]
    assert (p0["kind"], p0["func"], p0["hazard"], p0["rank"]) == \
        ("div_by_var", None, None, hazards.RANK_GLOBAL), p0
    assert "2026" in p0["approval"] or p0["approval"].startswith("山田"), p0["approval"]

    # 追記は連番。--hazard を付けると適用範囲が「個別」になる
    rc, out, err = run_cli(root, "add-policy", "--kind", "sqrt_arg",
                           "--hazard", "H-0001-03", "--decision", "caller_guarantees",
                           "--by", "佐藤")
    assert rc == 0, err
    assert "EP-002" in out, out
    pol = hazards.parse_policy(root)
    assert [p["ep"] for p in pol["policies"]] == ["EP-001", "EP-002"]
    assert pol["policies"][1]["rank"] == hazards.RANK_HAZARD
    assert pol["policies"][1]["hazard"] == "H-0001-03"

    # 追記後は自動で再突合され、hazard-map.json が更新される
    m = json.loads((root / "data" / "hazard-map.json").read_text(encoding="utf-8"))
    assert m["H-0001-01"]["ep"] == "EP-001"
    assert m["H-0001-03"]["ep"] == "EP-002"
    assert m["H-0001-04"]["ep"] is None, "別の sqrt_arg まで個別EPが効いている"

    # 語彙外の決定は登録できない
    rc, out, err = run_cli(root, "add-policy", "--kind", "log_arg",
                           "--decision", "ignore_it", "--by", "山田")
    assert rc != 0 and "語彙外" in (out + err), (rc, out, err)
    assert len(hazards.parse_policy(root)["policies"]) == 2, "NGなのに行が増えた"

    # status のサマリ
    rc, out, err = run_cli(root, "status", "--json")
    assert rc != 0, "未決定が残っているのに exit 0"
    s = json.loads(out)
    assert s["total"] == 11 and s["decided"] == 6 and s["undecided"] == 5, s
    assert s["by_kind"]["sqrt_arg"] == {"total": 2, "decided": 1}, s["by_kind"]
    print("OK  add-policy（テンプレ生成・自動採番・追記・再突合）と status")


# ---------- 5. review_checks（①仕様書の機械レビュー） ----------

FUNCS_WITH_HAZ = {
    "project": {"name": "haz-selftest", "legacy_lang": "fortran",
                "new_lang": "python", "package": "newpkg"},
    "functions": [
        {"func_id": "F-0001",
         "legacy": {"file": "legacy/haz.f", "name": "HAZCALC", "lines": "2-31"},
         "new": {"module": "src/newpkg/haz.py", "name": "hazcalc", "signature": ""},
         "inputs": [], "outputs": [], "globals": [], "external_files": [], "calls": [],
         "hazards": [
             {"hz_id": "H-0001-01", "kind": "div_by_var", "line": 10,
              "expr": "A / B", "vars": ["B"]},
             {"hz_id": "H-0001-02", "kind": "sqrt_arg", "line": 17,
              "expr": "SQRT(A)", "vars": ["A"]}]},
        {"func_id": "F-0002",
         "legacy": {"file": "legacy/haz.f", "name": "NOHAZ", "lines": "2-31"},
         "new": {"module": "src/newpkg/haz.py", "name": "nohaz", "signature": ""},
         "inputs": [], "outputs": [], "globals": [], "external_files": [], "calls": [],
         "hazards": []},
    ]}

HAZ_TABLE_OK = """
## 例外・数値特異点

| hazard | 種別 | 箇所 | 適用EP | 仕様記述 |
|--------|------|------|--------|----------|
| H-0001-01 | div_by_var | haz.f:10 | EP-001 | 分母 B が 0 のとき ZeroDivisionError |
| H-0001-02 | sqrt_arg | haz.f:17 | EP-002 | A が負のとき ValueError |
"""


def spec_text(fid: str, haz_section: str) -> str:
    num = fid.replace("-", "").replace("F", "")
    return f"""---
title: "関数仕様書: hazcalc"
func-id: "{fid}"
status: draft
reviewed-by: null
reviewed-date: null
legacy:
  file: "legacy/haz.f"
  name: "HAZCALC"
  lines: "2-31"
new:
  module: "src/newpkg/haz.py"
  signature: "hazcalc(a, b)"
---

# 概要

A を B で割って結果を返す。

# インタフェース

## 入力

| # | 名前 | レガシー型 | 新型 | 説明 | Confidence |
|---|------|-----------|------|------|:---:|
| 1 | A | REAL | float | 被除数 | 🟢 |

# 機能詳細

## SPEC-{num}-01: 除算

A を B で割る。

**Confidence: 🟢** — 根拠: `legacy/haz.f:10-11`

# 副作用・例外

なし。
{haz_section}
# 未確定事項

| ISSUE | 内容 | 状態 |
|-------|------|------|
"""


def review_project(tmp: Path, name: str, haz_section: str, *,
                   funcs: dict = None, policy_rows: list = None,
                   run_match: bool = True, fid: str = "F-0001") -> Path:
    root = new_project(tmp, name)
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / "functions.json").write_text(
        json.dumps(funcs or FUNCS_WITH_HAZ, ensure_ascii=False, indent=2), encoding="utf-8")
    write_policy(root, policy_rows if policy_rows is not None else [
        "| EP-001 | div_by_var | 全体既定 | guard_raise | 0割は例外送出 | 山田 2026-08-08 |",
        "| EP-002 | sqrt_arg | 全体既定 | guard_raise | 負値は例外送出 | 山田 2026-08-08 |"])
    if run_match:
        hazards.match_hazards(root, write=True)
    (root / "docs" / "specs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "specs" / f"{fid}.md").write_text(spec_text(fid, haz_section),
                                                       encoding="utf-8")
    return root


def test_review_ok(tmp: Path):
    root = review_project(tmp, "rv_ok", HAZ_TABLE_OK)
    r = review_checks.check_spec(str(root), "F-0001")
    assert r["ok"], r["problems"]
    assert r["warnings"] == [], r["warnings"]
    print("OK  review: hazard 全件記載＋実在EP＋決定済み → OK")


def test_review_missing_section(tmp: Path):
    root = review_project(tmp, "rv_nosec", "")
    r = review_checks.check_spec(str(root), "F-0001")
    assert not r["ok"]
    assert any("「例外・数値特異点」節がない" in p and "2 件" in p for p in r["problems"]), \
        r["problems"]
    print("OK  review (a): 節が無い → NG（検討漏れ）")


def test_review_missing_row(tmp: Path):
    section = HAZ_TABLE_OK.replace(
        "| H-0001-02 | sqrt_arg | haz.f:17 | EP-002 | A が負のとき ValueError |\n", "")
    root = review_project(tmp, "rv_norow", section)
    r = review_checks.check_spec(str(root), "F-0001")
    assert not r["ok"]
    assert any("H-0001-02" in p and "検討漏れ" in p for p in r["problems"]), r["problems"]
    assert not any("H-0001-01" in p for p in r["problems"]), r["problems"]
    print("OK  review (a): hz_id の行が無い → NG（検討漏れ）")


def test_review_fabricated_ep(tmp: Path):
    section = HAZ_TABLE_OK.replace("EP-002", "EP-999")
    root = review_project(tmp, "rv_fakeep", section)
    r = review_checks.check_spec(str(root), "F-0001")
    assert not r["ok"]
    assert any("EP-999" in p and "存在しない" in p for p in r["problems"]), r["problems"]
    assert not any("EP-001" in p for p in r["problems"]), r["problems"]
    print("OK  review (b): 実在しない EP-ID の引用 → NG（捏造）")


def test_review_undecided(tmp: Path):
    # sqrt_arg のポリシーを登録していない = H-0001-02 は未決定のまま
    root = review_project(tmp, "rv_undec", HAZ_TABLE_OK, policy_rows=[
        "| EP-001 | div_by_var | 全体既定 | guard_raise | 0割は例外送出 | 山田 2026-08-08 |"])
    r = review_checks.check_spec(str(root), "F-0001")
    assert not r["ok"]
    assert any("H-0001-02" in p and "未決定" in p for p in r["problems"]), r["problems"]
    # EP-002 は登録簿に無いので捏造としても弾かれる（未決定のまま書けない、が二重に効く）
    assert any("EP-002" in p and "存在しない" in p for p in r["problems"]), r["problems"]
    print("OK  review (c): 未決定（EP未割当）の hazard の仕様化 → NG")


def test_review_no_map_is_warning(tmp: Path):
    root = review_project(tmp, "rv_nomap", HAZ_TABLE_OK, run_match=False)
    r = review_checks.check_spec(str(root), "F-0001")
    assert r["ok"], r["problems"]
    assert any("hazard-map.json" in w for w in r["warnings"]), r["warnings"]
    print("OK  review: hazard-map.json が無い場合は警告のみ（EP実在検証は継続）")


def test_review_function_without_hazards(tmp: Path):
    root = review_project(tmp, "rv_nohaz", "", fid="F-0002")
    r = review_checks.check_spec(str(root), "F-0002")
    assert r["ok"], r["problems"]          # 節が無くても hazards が無ければ従来どおり
    assert r["warnings"] == [], r["warnings"]
    print("OK  review: hazard を持たない関数は素通り")


# ---------- 6. 後方互換 ----------

def test_backward_compat_old_functions_json(tmp: Path):
    """hazards キー自体が無い旧 functions.json でも全チェックがスキップされる。"""
    old = json.loads(json.dumps(FUNCS_WITH_HAZ))
    for f in old["functions"]:
        f.pop("hazards", None)
    root = review_project(tmp, "rv_old", "", funcs=old, policy_rows=[], run_match=False)
    r = review_checks.check_spec(str(root), "F-0001")
    assert r["ok"], r["problems"]
    assert r["warnings"] == [], r["warnings"]

    # hazards.py 側も落ちない（0件として扱う）
    assert hazards.collect_hazards(old) == []
    res = hazards.match_hazards(root, write=True)
    assert res["ok"] and res["total"] == 0, res
    assert "未決定の例外ポリシーはありません" in \
        (root / "docs" / "exception-queue.md").read_text(encoding="utf-8")

    # excluded の関数の hazard は対象外
    data = json.loads(json.dumps(FUNCS_WITH_HAZ))
    data["functions"][0]["excluded"] = True
    assert hazards.collect_hazards(data) == []
    print("OK  後方互換（hazards 無しの旧データ・excluded 関数）")


def test_migrate_specs_adds_contract_head(tmp: Path):
    """旧世代の仕様書（節が無い）に、本文を壊さず契約見出しを足せること。

    「## 例外・数値特異点」は hazard 機構と一緒に**後から**入った契約見出しなので、
    それ以前に書かれた仕様書は全関数で「節がない」を出し続ける。骨子の再生成
    （--force）は書いた本文を捨てるため使えない＝機械的な後追いが要る。
    """
    import ledger
    root = review_project(tmp, "rv_migrate", "")
    sp = root / "docs" / "specs" / "F-0001.md"
    before = sp.read_text(encoding="utf-8")
    r = review_checks.check_spec(str(root), "F-0001")
    assert any("「例外・数値特異点」節がない" in x for x in r["problems"]), r["problems"]

    prj = ledger.Project(root)
    ledger.cmd_migrate_specs(prj, types.SimpleNamespace(dry_run=True))
    assert sp.read_text(encoding="utf-8") == before, "--dry-run が書き換えている"

    ledger.cmd_migrate_specs(prj, types.SimpleNamespace(dry_run=False))
    after = sp.read_text(encoding="utf-8")
    assert "## 例外・数値特異点" in after
    assert "H-0001-01" in after and "H-0001-02" in after, "hazard 表が入っていない"
    # 適用EP は機械の突合結果（hazard-map.json）をそのまま載せる＝①に書かせない
    assert "EP-001" in after and "EP-002" in after, "適用EPが転記されていない"
    for keep in ("# 概要", "A を B で割って結果を返す。", "# 機能詳細",
                 "# 副作用・例外", "# 未確定事項", "| 1 | A | REAL |"):
        assert keep in after, f"本文が壊れた: {keep}"
    assert (after.index("# 副作用・例外") < after.index("## 例外・数値特異点")
            < after.index("# 未確定事項")), "挿入位置がテンプレの並びと違う"

    r2 = review_checks.check_spec(str(root), "F-0001")
    assert not any("節がない" in x for x in r2["problems"]), r2["problems"]

    ledger.cmd_migrate_specs(prj, types.SimpleNamespace(dry_run=False))
    assert sp.read_text(encoding="utf-8") == after, "2回目で二重に入っている（冪等でない）"
    print("OK  migrate-specs（旧世代の仕様書に契約見出しを後追い・冪等）")


def test_migrate_specs_fills_ep_later(tmp: Path):
    """例外ポリシーを後から決めても、空欄の適用EPだけ後追いで埋まること。

    節を足した時点では EP 未決定 → 「EP未割当のまま仕様化」の指摘が残る。決定して
    match を回し直したあと migrate-specs を再実行すれば、節がある関数も空欄だけ
    埋まって指摘が消える（人や①が書いた値は上書きしない）。
    """
    import ledger
    root = review_project(tmp, "rv_ep_later", "", policy_rows=[], run_match=True)
    sp = root / "docs" / "specs" / "F-0001.md"
    ledger.cmd_migrate_specs(ledger.Project(root), types.SimpleNamespace(dry_run=False))
    assert "| EP-0" not in sp.read_text(encoding="utf-8"), "EP 未決定なのに埋まっている"
    r = review_checks.check_spec(str(root), "F-0001")
    assert any("EP 未割当" in x for x in r["problems"]), r["problems"]

    write_policy(root, [
        "| EP-001 | div_by_var | 全体既定 | guard_raise | 0割は例外送出 | 山田 2026-08-20 |",
        "| EP-002 | sqrt_arg | 全体既定 | guard_raise | 負値は例外送出 | 山田 2026-08-20 |"])
    hazards.match_hazards(root, write=True)

    ledger.cmd_migrate_specs(ledger.Project(root), types.SimpleNamespace(dry_run=False))
    after = sp.read_text(encoding="utf-8")
    assert "EP-001" in after and "EP-002" in after, "決定後も適用EPが埋まらない"
    r = review_checks.check_spec(str(root), "F-0001")
    assert r["ok"], r["problems"]

    ledger.cmd_migrate_specs(ledger.Project(root), types.SimpleNamespace(dry_run=False))
    assert sp.read_text(encoding="utf-8") == after, "冪等でない"

    # 人が書いた値は上書きしない
    sp.write_text(after.replace("| EP-001 |", "| EP-009 |"), encoding="utf-8")
    ledger.cmd_migrate_specs(ledger.Project(root), types.SimpleNamespace(dry_run=False))
    assert "EP-009" in sp.read_text(encoding="utf-8"), "人の記入を機械が上書きした"
    print("OK  migrate-specs（例外ポリシーを後から決めても適用EPが埋まる）")


def main() -> int:
    tests = [test_detectors, test_merge_overwrites_hazards, test_match_precedence,
             test_queue, test_add_policy, test_review_ok, test_review_missing_section,
             test_review_missing_row, test_review_fabricated_ep, test_review_undecided,
             test_review_no_map_is_warning, test_review_function_without_hazards,
             test_backward_compat_old_functions_json, test_migrate_specs_adds_contract_head,
             test_migrate_specs_fills_ep_later]
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_detector_table_is_extensible()
        for t in tests:
            t(tmp)
    print(f"\nPASS: {len(tests) + 1}件すべて成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())
