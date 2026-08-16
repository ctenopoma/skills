#!/usr/bin/env python3
"""extract_fortran.py の call_sites 抽出のスモークテスト。

pytest は不要。`python test_extract_callsites.py` で走る素の assert 形式。
fixture_callsites/legacy を一時ディレクトリにコピーして extract() を回し、
call_sites（callee 解決・args の null 化・inferred フラグ・行番号）と、
既存フィールド（件数・calls・unresolved_calls・完全性突合）の両方を検証する。
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import extract_fortran                                            # noqa: E402

FIXTURE = HERE / "fixture_callsites"


def setup_project(tmp: Path) -> Path:
    """フィクスチャを一時プロジェクトにコピーする。"""
    shutil.copytree(FIXTURE / "legacy", tmp / "legacy")
    return tmp


def by_name(data: dict) -> dict:
    return {f["legacy"]["name"]: f for f in data["functions"]}


def sites_of(f: dict) -> dict:
    """call_sites を呼び先名 → サイト（同名は最初の1件）に落とす。"""
    out = {}
    for s in f.get("call_sites", []):
        out.setdefault(s["name"], s)
    return out


def check_line(src: Path, line: int, needle: str, what: str) -> None:
    """line が「文の開始物理行」を指していることを原文で確かめる。"""
    text = src.read_text(encoding="utf-8").splitlines()
    assert 1 <= line <= len(text), f"{what}: 行 {line} が範囲外"
    assert needle.lower() in text[line - 1].lower(), \
        f"{what}: {src.name}:{line} は {needle!r} を含まない → {text[line - 1]!r}"


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = setup_project(Path(td))
        res = extract_fortran.extract(str(root), write=True)

        # ---- 既存フィールドが壊れていないこと ----
        assert res["ok"], res
        assert res["completeness_mismatches"] == [], res["completeness_mismatches"]
        assert res["functions_total"] == 5, res["functions_total"]
        assert res["added"] == 5, res["added"]
        assert res["warnings"] == [], res["warnings"]

        fj = root / "data" / "functions.json"
        raw = fj.read_text(encoding="utf-8")
        assert "_call_sites" not in raw and "_call_names" not in raw, "内部キーが漏れている"
        data = json.loads(raw)
        fn = by_name(data)
        assert set(fn) == {"MAIN", "CALCTAX", "REPORT", "FACTOR", "PUTLINE"}, sorted(fn)
        assert fn["MAIN"]["func_id"] == "F-0000", fn["MAIN"]["func_id"]

        calc = root / "legacy" / "calc.f"
        util = root / "legacy" / "util.f90"

        # ---- 既存の calls / unresolved_calls（後方互換） ----
        assert set(fn["MAIN"]["calls"]) == {fn["CALCTAX"]["func_id"],
                                            fn["REPORT"]["func_id"],
                                            fn["FACTOR"]["func_id"]}, fn["MAIN"]["calls"]
        assert fn["MAIN"]["unresolved_calls"] == ["FINISH"], fn["MAIN"]["unresolved_calls"]
        assert set(fn["REPORT"]["calls"]) == {fn["PUTLINE"]["func_id"],
                                              fn["FACTOR"]["func_id"]}, fn["REPORT"]["calls"]
        assert fn["CALCTAX"]["calls"] == [] and fn["PUTLINE"]["calls"] == []

        # ---- 固定形式：単純変数だけの CALL ----
        ms = sites_of(fn["MAIN"])
        assert set(ms) == {"CALCTAX", "REPORT", "FACTOR", "FINISH"}, sorted(ms)
        assert ms["CALCTAX"]["callee"] == fn["CALCTAX"]["func_id"]
        assert ms["CALCTAX"]["args"] == ["AMT", "RATE", "TAX"], ms["CALCTAX"]
        assert "inferred" not in ms["CALCTAX"]
        check_line(calc, ms["CALCTAX"]["line"], "CALL CALCTAX", "CALCTAX 呼び出し")

        # ---- 継続行をまたぐ CALL：配列要素は配列名へ、式・文字列は null ----
        assert ms["REPORT"]["callee"] == fn["REPORT"]["func_id"]
        assert ms["REPORT"]["args"] == ["TAX", "TBL", None, None, "MODE"], ms["REPORT"]
        check_line(calc, ms["REPORT"]["line"], "CALL REPORT", "REPORT 呼び出し（継続行）")

        # ---- 関数参照（Y = FOO(X)）は inferred。ファイルをまたいで解決される ----
        assert ms["FACTOR"]["callee"] == fn["FACTOR"]["func_id"]
        assert ms["FACTOR"]["args"] == ["MODE"], ms["FACTOR"]
        assert ms["FACTOR"]["inferred"] is True, ms["FACTOR"]
        check_line(calc, ms["FACTOR"]["line"], "FACTOR(MODE)", "FACTOR 参照")

        # ---- 未解決の呼び先は callee を省いて name だけ残す ----
        assert "callee" not in ms["FINISH"], ms["FINISH"]
        assert ms["FINISH"]["args"] == [], ms["FINISH"]
        check_line(calc, ms["FINISH"]["line"], "CALL FINISH", "FINISH 呼び出し")

        # ---- 自由形式 ----
        rs = sites_of(fn["REPORT"])
        assert set(rs) == {"FACTOR", "PUTLINE"}, sorted(rs)
        assert rs["FACTOR"]["args"] == ["MODE"] and rs["FACTOR"]["inferred"] is True
        check_line(util, rs["FACTOR"]["line"], "factor(mode)", "自由形式の関数参照")
        assert rs["PUTLINE"]["callee"] == fn["PUTLINE"]["func_id"]
        assert rs["PUTLINE"]["args"] == ["TAG", None, "E", None], rs["PUTLINE"]
        check_line(util, rs["PUTLINE"]["line"], "call putline", "自由形式の CALL")

        # ---- 呼び出しの無いユニットは空配列 ----
        assert fn["CALCTAX"]["call_sites"] == [], fn["CALCTAX"]["call_sites"]
        assert fn["FACTOR"]["call_sites"] == [], fn["FACTOR"]["call_sites"]
        assert fn["PUTLINE"]["call_sites"] == [], fn["PUTLINE"]["call_sites"]

        # ---- 再抽出：call_sites は常に上書き、manual エントリは触らない ----
        data["functions"].append({
            "func_id": "F-0099", "manual": True,
            "legacy": {"file": "legacy/vendor.f", "name": "VENDOR",
                       "lines": "1-1", "kind": "subroutine"},
            "new": {"module": "src/newpkg/vendor.py", "name": "vendor", "signature": ""},
            "inputs": [], "outputs": [], "globals": [], "external_files": [],
            "calls": [], "call_sites": [{"name": "KEEPME", "line": 1, "args": ["Z"]}]})
        for f in data["functions"]:
            if f["func_id"] == "F-0000":
                f["call_sites"] = [{"name": "HANDEDIT", "line": 999, "args": []}]
                f["inputs"] = [{"name": "AMT", "legacy_type": "REAL*8",
                                "new_type": "Decimal", "desc": "手で書いた説明"}]
        fj.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        res2 = extract_fortran.extract(str(root), write=True)
        assert res2["ok"] and res2["added"] == 0, res2
        assert res2["functions_total"] == 6, res2["functions_total"]
        assert res2["completeness_mismatches"] == [], res2["completeness_mismatches"]
        data2 = json.loads(fj.read_text(encoding="utf-8"))
        fn2 = by_name(data2)
        assert fn2["MAIN"]["func_id"] == "F-0000", "func_id が動いた"
        assert sites_of(fn2["MAIN"]) == ms, "ソース由来の call_sites が上書きされていない"
        assert fn2["MAIN"]["inputs"][0]["desc"] == "手で書いた説明", "手修正が消えた"
        vendor = next(f for f in data2["functions"] if f["func_id"] == "F-0099")
        assert vendor["call_sites"] == [{"name": "KEEPME", "line": 1, "args": ["Z"]}], \
            "manual エントリの call_sites を触っている"

    print("OK: extract_fortran call_sites セルフテスト 全項目 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
