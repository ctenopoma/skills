#!/usr/bin/env python3
"""②テスト仕様の hazard 境界ケース網羅チェック（review_checks._check_testspec_hazards）の
セルフテスト。pytest 不要: `python test_testspec_hazards.py`

fixture_hazards を一時ディレクトリにコピーして抽出 → ポリシー登録 → 突合まで進め、
②に hz_id 引用が有る/無い場合の NG 判定と、対象外決定（detect_only）・未決定・
hazard-map 無しの素通りを検証する。
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))

import review_checks  # noqa: E402

PY = sys.executable


def run(args, cwd, ok=(0,)):
    r = subprocess.run([PY] + args, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode in ok, f"exit {r.returncode}: {' '.join(args)}\n{r.stdout}\n{r.stderr}"
    return r.stdout


def write_testspec(root: Path, fid: str, body: str):
    p = root / "docs" / "test-specs" / f"{fid}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\n" f'func-id: "{fid}"\n' "status: generated\n---\n\n" + body,
                 encoding="utf-8")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="lr-tshaz-"))
    try:
        for item in (HERE / "fixture_hazards").iterdir():
            dst = tmp / item.name
            shutil.copytree(item, dst) if item.is_dir() else shutil.copy2(item, dst)
        run([str(SCRIPTS / "extract_fortran.py"), "--root", ".", "--package", "pkg",
             "--write"], tmp)
        funcs = json.loads((tmp / "data" / "functions.json").read_text(encoding="utf-8-sig"))
        f1 = next(f for f in funcs["functions"] if f["func_id"] == "F-0001")
        by_kind = {}
        for h in f1["hazards"]:
            by_kind.setdefault(h["kind"], []).append(h["hz_id"])
        div = by_kind["div_by_var"]          # 2件（guard_raise にする → ②必須）
        sqrt = by_kind["sqrt_arg"]           # detect_only にする → ②不要
        # log_arg / array_index_var は未決定のまま → ②不要

        # hazard-map が無いうちは警告のみ（素通り）
        write_testspec(tmp, "F-0001", "# ケース\n本文に hz_id 引用なし\n")
        res = review_checks.check_testspec(str(tmp), "F-0001")
        assert not any("境界ケース" in p for p in res["problems"]), res["problems"]
        assert any("hazard 境界ケースの検証を省略" in w for w in res["warnings"]), res["warnings"]
        print("OK  hazard-map 無し → 警告のみ（NG にしない）")

        run([str(SCRIPTS / "hazards.py"), "add-policy", "--kind", "div_by_var",
             "--decision", "guard_raise", "--by", "selftest", "--root", "."], tmp)
        run([str(SCRIPTS / "hazards.py"), "add-policy", "--kind", "sqrt_arg",
             "--decision", "detect_only", "--by", "selftest", "--root", "."], tmp)
        # 未決定（log/array）が残るため exit 1 だが hazard-map.json は書かれる（設計どおり）
        run([str(SCRIPTS / "hazards.py"), "match", "--root", "."], tmp, ok=(0, 1))
        review_checks._POLICY_CACHE.clear()

        # guard_raise の hz_id を1件だけ引用 → 引用しなかった div のみ NG。
        # detect_only(sqrt)・未決定(log/array) は要求されない
        write_testspec(tmp, "F-0001",
                       f"# ケース\n## F0001-TC-001\n{div[0]} の0割で ZeroDivisionError\n")
        res = review_checks.check_testspec(str(tmp), "F-0001")
        miss = [p for p in res["problems"] if "境界ケース" in p]
        assert len(miss) == 1 and div[1] in miss[0], res["problems"]
        assert not any(h in p for p in miss for h in sqrt), miss
        print("OK  guard_raise 未引用のみ NG（detect_only・未決定は対象外）")

        # 全 guard_raise を引用 → 境界ケース NG は消える
        write_testspec(tmp, "F-0001",
                       "# ケース\n" + "\n".join(f"## F0001-TC-00{i+1}\n{h} の境界"
                                                for i, h in enumerate(div)) + "\n")
        res = review_checks.check_testspec(str(tmp), "F-0001")
        assert not any("境界ケース" in p for p in res["problems"]), res["problems"]
        print("OK  全 guard_raise 引用 → 境界ケース NG なし")

        # hazard を持たない関数は素通り
        f_no = next((f for f in funcs["functions"]
                     if not f.get("hazards") and f["func_id"] != "F-0001"), None)
        if f_no:
            write_testspec(tmp, f_no["func_id"], "# ケース\n")
            res = review_checks.check_testspec(str(tmp), f_no["func_id"])
            assert not any("境界ケース" in p for p in res["problems"]), res["problems"]
            print("OK  hazard 無しの関数は素通り")

        print("\nPASS: testspec hazard 境界ケースチェック 全項目成功")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
