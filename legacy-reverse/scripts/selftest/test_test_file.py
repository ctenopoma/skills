#!/usr/bin/env python3
"""③の test_file 登録（set-test-file / freeze-tests）のセルフテスト。

`python test_test_file.py` でも pytest でも走る（素の assert）。

背景: functions.json の `test_file` は⓪では空で、③が確定させる。ここが手編集
（＝書き忘れが起きる経路）だったため、③が終わったつもりで⑤へ進もうとすると
`functions.json に test_file が未設定` でパイプラインが止まっていた。
CLI（ledger set-test-file / freeze-tests のパス指定・自動判定）で塞いだので、
その入口が生きていることを回帰として固定する。

検証対象:
  - set-test-file: ②のケースIDの @pytest.mark.tc からの自動判定・明示パス
  - 候補ゼロ / 複数 のときエラーで functions.json を書き換えないこと
  - freeze-tests: test_file 未設定でもその場で確定させて freeze できること
  - pipeline.verify_testcode（③の契約検証）が freeze 後に ok になること
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
LEDGER_PY = SCRIPTS_DIR / "ledger.py"

sys.path.insert(0, str(SCRIPTS_DIR))
import pipeline  # noqa: E402

FUNCTIONS = {
    "project": {"name": "test-file-fixture"},
    "functions": [
        {"func_id": "F-0123",
         "legacy": {"file": "legacy/tax.f", "name": "CALCTAX", "lines": "1-10"},
         "new": {"module": "src/demo/tax.py", "name": "calc_tax",
                 "signature": "calc_tax(amount)"},
         "calls": []},
    ],
}

TEST_SPEC = """---
status: approved
---

## F0123-TC-001: 通常

## F0123-TC-002: 異常
"""

TEST_CODE = '''import pytest


@pytest.mark.tc("F0123-TC-001")
def test_normal():
    assert True


@pytest.mark.tc("F0123-TC-002")
def test_error():
    assert True
'''


def setup_project(tmp: Path, test_code_at: str = "tests/test_tax.py") -> Path:
    """②が approved・③のテストコードだけある（test_file 未設定）プロジェクトを作る。"""
    (tmp / "data").mkdir(parents=True, exist_ok=True)
    (tmp / "docs" / "test-specs").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "functions.json").write_text(
        json.dumps(FUNCTIONS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (tmp / "data" / "ledger.json").write_text("{}\n", encoding="utf-8")
    (tmp / "docs" / "test-specs" / "F-0123.md").write_text(TEST_SPEC, encoding="utf-8")
    if test_code_at:
        p = tmp / test_code_at
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(TEST_CODE, encoding="utf-8")
    return tmp


def load_data(root: Path) -> dict:
    return json.loads((root / "data" / "functions.json").read_text(encoding="utf-8-sig"))


def saved_test_file(root: Path) -> str:
    return load_data(root)["functions"][0].get("test_file")


def run_ledger(root: Path, *args):
    cmd = [sys.executable, str(LEDGER_PY), "--root", str(root), *args]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
    return r.returncode, r.stdout, r.stderr


# ---------- set-test-file ----------

def test_set_test_file_autodetects_from_markers():
    with tempfile.TemporaryDirectory() as td:
        root = setup_project(Path(td))
        rc, out, err = run_ledger(root, "set-test-file", "F-0123")
        assert rc == 0, out + err
        assert saved_test_file(root) == "tests/test_tax.py", out
        assert "freeze-tests" in out, "次にやることを案内していない: " + out
        print("OK  set-test-file: マーカーから自動判定して functions.json に保存")


def test_set_test_file_accepts_explicit_path():
    with tempfile.TemporaryDirectory() as td:
        root = setup_project(Path(td), test_code_at="tests/unit/kept.py")
        rc, out, err = run_ledger(root, "set-test-file", "F-0123", "tests/unit/kept.py")
        assert rc == 0, out + err
        assert saved_test_file(root) == "tests/unit/kept.py", out
        print("OK  set-test-file: 明示パス（ファイル名が test_*.py でなくても可）")


def test_set_test_file_accepts_not_yet_created_path():
    """③に入る時点ではテストファイルはまだ無い。先に「どこに書くか」を登録できること。"""
    with tempfile.TemporaryDirectory() as td:
        root = setup_project(Path(td), test_code_at="")
        rc, out, err = run_ledger(root, "set-test-file", "F-0123", "tests/test_tax.py")
        assert rc == 0, out + err
        assert saved_test_file(root) == "tests/test_tax.py", out
        assert "まだファイルが無い" in out, "未作成であることを警告していない: " + out
        print("OK  set-test-file: 未作成のパスも登録できる（②の直後に使える）")


def test_freeze_rejects_missing_file():
    """freeze はハッシュを取るので実在必須（登録と freeze で要件が違う）。"""
    with tempfile.TemporaryDirectory() as td:
        root = setup_project(Path(td), test_code_at="")
        assert run_ledger(root, "set-test-file", "F-0123", "tests/test_tax.py")[0] == 0
        rc, out, err = run_ledger(root, "freeze-tests", "F-0123")
        assert rc == 1, out + err
        assert "存在しない" in (out + err), out + err
        print("OK  freeze-tests: 実在しないパスは freeze しない")


def test_reads_and_migrates_test_file_under_new():
    """`new.test_file` に入っていても「未設定」にせず読み、正の位置へ寄せること。

    ③が手編集で確定させていた時代のデータが実在する（new.module の隣に書かれる）。
    直下だけを見ていると③以降が「test_file が未設定」で止まってしまう。
    """
    with tempfile.TemporaryDirectory() as td:
        root = setup_project(Path(td))
        data = load_data(root)
        data["functions"][0]["new"]["test_file"] = "tests/test_tax.py"
        (root / "data" / "functions.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        # 読み取り: 「未設定」ではなく freeze 未実行として扱われる
        ok, why, _ = pipeline.verify_testcode(str(root), "F-0123")
        assert not ok and "未設定" not in why, why

        # 書き込み: 直下へ移して new からは消す
        rc, out, err = run_ledger(root, "set-test-file", "F-0123")
        assert rc == 0, out + err
        f = load_data(root)["functions"][0]
        assert f.get("test_file") == "tests/test_tax.py", f
        assert "test_file" not in f["new"], f["new"]
        print("OK  new.test_file: 読めるし、正の位置へ移設される")


def test_set_test_file_reports_no_candidate_with_hint():
    with tempfile.TemporaryDirectory() as td:
        root = setup_project(Path(td), test_code_at="")
        rc, out, err = run_ledger(root, "set-test-file", "F-0123")
        assert rc == 1, out + err
        msg = out + err
        assert "test_file" in msg and "set-test-file" in msg, msg
        print("OK  set-test-file: 候補ゼロは直し方つきのエラー")


def test_no_candidate_message_names_the_cause():
    """空振りの理由を4通りに切り分けて言うこと（「見つからない」だけだと直せない）。"""
    cases = [
        # (プロジェクトの作り方, メッセージに出るべき語)
        ("no_testspec", "②がまだ無い"),
        ("bad_heading", "ケースIDを抽出できない"),
        ("no_test_files", "1つも無い"),
        ("no_marker", "マーカーが無い"),
    ]
    for kind, expected in cases:
        with tempfile.TemporaryDirectory() as td:
            root = setup_project(Path(td), test_code_at="")
            if kind == "no_testspec":
                (root / "docs" / "test-specs" / "F-0123.md").unlink()
            elif kind == "bad_heading":
                (root / "docs" / "test-specs" / "F-0123.md").write_text(
                    "---\nstatus: approved\n---\n\n# ケース一覧\n", encoding="utf-8")
            elif kind == "no_marker":
                (root / "tests").mkdir(exist_ok=True)
                (root / "tests" / "test_tax.py").write_text(
                    "def test_normal():\n    assert True\n", encoding="utf-8")
            rc, out, err = run_ledger(root, "set-test-file", "F-0123")
            assert rc == 1, out + err
            assert expected in (out + err), f"[{kind}] 理由が出ていない: {out + err}"
        print(f"OK  自動判定の空振り理由: {kind}")


def test_set_test_file_reports_multiple_candidates():
    with tempfile.TemporaryDirectory() as td:
        root = setup_project(Path(td))
        (root / "tests" / "test_tax_extra.py").write_text(TEST_CODE, encoding="utf-8")
        before = load_data(root)
        rc, out, err = run_ledger(root, "set-test-file", "F-0123")
        assert rc == 1, out + err
        msg = out + err
        assert "test_tax.py" in msg and "test_tax_extra.py" in msg, msg
        assert load_data(root) == before, "エラー時に functions.json が変化してはいけない"
        print("OK  set-test-file: 候補が複数なら両方を挙げてエラー（勝手に選ばない）")


# ---------- freeze-tests ----------

def test_freeze_tests_sets_test_file_when_unset():
    with tempfile.TemporaryDirectory() as td:
        root = setup_project(Path(td))
        rc, out, err = run_ledger(root, "freeze-tests", "F-0123")
        assert rc == 0, out + err
        assert saved_test_file(root) == "tests/test_tax.py", out
        led = json.loads((root / "data" / "ledger.json").read_text(encoding="utf-8-sig"))
        assert led["F-0123"]["test_code_hash"], led
        ok, why, _ = pipeline.verify_testcode(str(root), "F-0123")
        assert ok, f"③の契約検証が通らない: {why}"
        print("OK  freeze-tests: test_file 未設定でも確定＋freeze され、③の検証が通る")


def test_freeze_tests_accepts_path_argument():
    with tempfile.TemporaryDirectory() as td:
        root = setup_project(Path(td), test_code_at="tests/unit/kept.py")
        rc, out, err = run_ledger(root, "freeze-tests", "F-0123", "tests/unit/kept.py")
        assert rc == 0, out + err
        assert saved_test_file(root) == "tests/unit/kept.py", out
        print("OK  freeze-tests: 第2引数のパスで確定できる")


def test_verify_testcode_message_tells_how_to_fix():
    with tempfile.TemporaryDirectory() as td:
        root = setup_project(Path(td))
        ok, why, _ = pipeline.verify_testcode(str(root), "F-0123")
        assert not ok
        assert "set-test-file" in why, why
        print("OK  verify_testcode: 未設定の理由に直し方（set-test-file）が入る")


def main() -> None:
    tests = [
        test_set_test_file_autodetects_from_markers,
        test_set_test_file_accepts_explicit_path,
        test_set_test_file_accepts_not_yet_created_path,
        test_freeze_rejects_missing_file,
        test_reads_and_migrates_test_file_under_new,
        test_set_test_file_reports_no_candidate_with_hint,
        test_set_test_file_reports_multiple_candidates,
        test_no_candidate_message_names_the_cause,
        test_freeze_tests_sets_test_file_when_unset,
        test_freeze_tests_accepts_path_argument,
        test_verify_testcode_message_tells_how_to_fix,
    ]
    for t in tests:
        t()
    print(f"\nPASS: {len(tests)}件すべて成功")


if __name__ == "__main__":
    main()
