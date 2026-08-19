#!/usr/bin/env python3
"""工程別プロンプト調整（docs/prompts/）のセルフテスト。

固変分離の「可変」その3（プロジェクト個別のプロンプト調整）が、
配置・判定・skill 側の導線の3点で成立していることを検証する:

  (a) `ledger init-templates` がシードを配置し、既存を上書きしない
  (b) `prompt_path` はプロジェクト側のみを返す（シードへフォールバックしない）／
      `prompt_is_seed` が「雛形のまま」と「人が記入済み」を区別する
  (c) ①〜④の SKILL.md が、対応する docs/prompts/<工程>.md と conventions.md を
      実際に読む手順を持っている（導線が繋がっているか＝過去に抜けていた点）
"""
import shutil
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
SKILL_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))
import ledger  # noqa: E402


class _Args:
    pass


def _project() -> Path:
    root = Path(tempfile.mkdtemp(prefix="lr-prompts-"))
    (root / "docs").mkdir()
    (root / "data").mkdir()
    (root / "data" / "functions.json").write_text(
        '{"project": {"name": "t", "package": "p"}, "functions": []}', encoding="utf-8")
    return root


def test_init_and_detection():
    """(a)(b) 配置・非上書き・シード判定・フォールバックしないこと。"""
    root = _project()
    try:
        p = ledger.Project(root)
        assert ledger.prompt_path(root, "1-spec") is None, \
            "未配置なら None（同梱シードにフォールバックしてはいけない）"

        ledger.cmd_init_templates(p, _Args())
        for phase in ledger.PROMPT_PHASES:
            path = ledger.prompt_path(root, phase)
            assert path is not None and path.exists(), f"{phase} が配置されていない"
            assert ledger.prompt_is_seed(path), f"{phase} は配置直後なら「雛形のまま」"

        target = root / "docs" / "prompts" / "1-spec.md"
        marker = "\n## 重点\n\n- 単位変換は必ず式で書く\n"
        target.write_text(target.read_text(encoding="utf-8") + marker, encoding="utf-8")
        assert not ledger.prompt_is_seed(target), "人が書いたら「記入あり」になる"

        ledger.cmd_init_templates(p, _Args())          # 2回目
        assert marker in target.read_text(encoding="utf-8"), \
            "init-templates が人の記入を上書きしている"

        try:
            ledger.prompt_path(root, "9-nope")
        except KeyError:
            pass
        else:
            raise AssertionError("未知の工程は KeyError にする")
        print("OK  (a)(b) 配置・非上書き・シード判定・フォールバックなし")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_skills_read_variable_files():
    """(c) ①〜④の SKILL.md に、可変ファイルを読む導線があること。

    「入力に含める」と宣言しているだけで手順に読むステップが無い、という
    以前の抜けを回帰テストとして固定する。
    """
    wants = {
        "legacy-1-spec": ["docs/prompts/1-spec.md", "docs/conventions.md",
                          "docs/templates/spec.md"],
        "legacy-2-testspec": ["docs/prompts/2-testspec.md", "docs/conventions.md",
                              "docs/templates/test-spec.md"],
        "legacy-3-testcode": ["docs/prompts/3-testcode.md", "docs/conventions.md"],
        "legacy-4-impl": ["docs/prompts/4-impl.md", "docs/conventions.md"],
    }
    for skill, refs in wants.items():
        text = (SKILL_ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        for ref in refs:
            assert ref in text, f"{skill}/SKILL.md が {ref} を参照していない"
        assert "固定契約" in text, f"{skill}/SKILL.md に優先順位（固定契約が勝つ）の明示がない"
    # PJ 可変部の実体を skill が持たない（二重管理の防止）
    impl = (SKILL_ROOT / "skills" / "legacy-4-impl" / "SKILL.md").read_text(encoding="utf-8")
    assert "Google スタイル" not in impl, \
        "docstring 規約の実体が skill に書かれている（conventions.md が正であるべき）"
    print(f"OK  (c) ①〜④の {len(wants)} skill すべてに可変ファイルの導線あり")


def main():
    tests = [test_init_and_detection, test_skills_read_variable_files]
    for t in tests:
        t()
    print(f"\nPASS: {len(tests)}件すべて成功")


if __name__ == "__main__":
    main()
