#!/usr/bin/env python3
"""skill 自身の整合性（check_skill.py）のセルフテスト。

2つのことを検証する:

  (a) **いまの skill 一式に不整合が無いこと**（回帰テスト本体）。
      文書がスクリプトの実体とズレたらここで落ちる——エージェントは
      `python scripts/check_skill.py` を実行し、NG がゼロになるまで直す
  (b) 検査器そのものが、仕込んだ不整合をちゃんと検知すること
      （通らない検査は「常に OK」を返すだけで無意味なため）

`python test_skill_docs.py` でも pytest でも走る。
"""
import shutil
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
SKILL_ROOT = SCRIPTS_DIR.parent

sys.path.insert(0, str(SCRIPTS_DIR))
import check_skill  # noqa: E402


def test_skill_is_consistent():
    """(a) 現状の skill に不整合が無い（文書とスクリプトが一致している）。"""
    res = check_skill.check(SKILL_ROOT)
    if not res["ok"]:
        lines = [f"  {p['file']}:{p['line']} [{p['kind']}] {p['message']}"
                 for p in res["problems"]]
        raise AssertionError(
            f"skill の文書とスクリプトが食い違っている（{len(res['problems'])}件）:\n"
            + "\n".join(lines)
            + "\n  → python scripts/check_skill.py で詳細（直し方の hint つき）")
    print(f"OK  (a) 不整合なし（文書 {res['scanned']['docs']} 件 / "
          f"スクリプト {res['scanned']['scripts']} 件）")


def _sandbox() -> Path:
    """skill の文書・スクリプトだけを写した検査用のコピー（生成物は持ち込まない）。"""
    tmp = Path(tempfile.mkdtemp(prefix="lr-skillchk-"))
    root = tmp / "legacy-reverse"
    root.mkdir()
    for name in ("scripts", "hooks", "references", "skills", "assets", "slides"):
        src = SKILL_ROOT / name
        if src.exists():
            shutil.copytree(src, root / name,
                            ignore=shutil.ignore_patterns("__pycache__"))
    for md in SKILL_ROOT.glob("*.md"):
        shutil.copy2(md, root / md.name)
    # 生成物（MANUAL.html/pdf・設計サイト）は重いので中身は持ち込まず、
    # 存在だけ用意する（リンク切れの誤検知を避けるため）
    for name in ("MANUAL.html", "MANUAL.pdf"):
        (root / name).touch()
    (root.parent / "docs" / "legacy-reverse").mkdir(parents=True, exist_ok=True)
    return root


def test_detects_injected_drift():
    """(b) 仕込んだ不整合（消えたスクリプト参照・無いサブコマンド・無いオプション・
    frontmatter 欠落）を検知する。"""
    root = _sandbox()
    try:
        base = check_skill.check(root)
        assert base["ok"], f"サンドボックス自体が NG: {base['problems'][:3]}"

        probe = root / "QUICKREF.md"
        original = probe.read_text(encoding="utf-8")
        cases = [
            ("missing-path", "`scripts/ghost_module.py` を実行する"),
            ("unknown-command", "python <LR>/scripts/ledger.py --root . nosuchcmd"),
            ("unknown-option", "python <LR>/scripts/ledger.py --root . wbs --nosuchopt"),
        ]
        for kind, injected in cases:
            probe.write_text(original + "\n" + injected + "\n", encoding="utf-8")
            res = check_skill.check(root)
            kinds = {p["kind"] for p in res["problems"]}
            assert kind in kinds, f"{kind} を検知できない（injected: {injected!r}）"
        probe.write_text(original, encoding="utf-8")

        skill_md = root / "skills" / "legacy-1-spec" / "SKILL.md"
        text = skill_md.read_text(encoding="utf-8")
        skill_md.write_text(text.replace("name: legacy-1-spec", "name: wrong-name", 1),
                            encoding="utf-8")
        res = check_skill.check(root)
        assert any(p["kind"] == "frontmatter" for p in res["problems"]), \
            "frontmatter の name 不一致を検知できない"
        skill_md.write_text(text, encoding="utf-8")

        assert check_skill.check(root)["ok"], "戻したのに NG が残っている"
        print("OK  (b) 仕込んだ不整合を4種すべて検知（戻すと OK に戻る）")
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def main():
    tests = [test_skill_is_consistent, test_detects_injected_drift]
    for t in tests:
        t()
    print(f"\nPASS: {len(tests)}件すべて成功")


if __name__ == "__main__":
    main()
