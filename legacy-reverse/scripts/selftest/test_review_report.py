#!/usr/bin/env python3
"""一斉レビュー表（①仕様書 / ②テスト仕様書）のセルフテスト。

検証すること:
  (a) `review_checks.make_reports` が①②の両方の表を作る
      （②が表に出ない＝人が「まとめて承認」できない、という取りこぼしの回帰防止）
  (b) ②の行は ケース数・⚠未確定 を持ち、⚠未確定が残る行は「⚠回答待ち」になる
  (c) ⚠未確定が残る②は `review_actions.approve` がどの入口でも拒否する
      （完了条件「⚠未確定ゼロ」を承認の入口で守る）
  (d) 表のページは閲覧専用のまま（fetch / form / POST を持たない）で、
      1行の高さに上限がある（.sr-cell のクランプ。長い概要で表が縦に伸びない）
"""
import shutil
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixture_vars"
sys.path.insert(0, str(SCRIPTS_DIR))
import review_checks       # noqa: E402
import review_actions      # noqa: E402

TESTSPEC = """\
---
title: "テスト仕様書: calctax"
func-id: "F-0001"
spec-ref: "specs/F-0001.md"
spec-hash: "{spec_hash}"
status: generated
approved-by: null
approved-date: null
---

# テスト方針

同値分割と境界値。丸めは規約の切り捨てに従う。{filler}

# テストケース

## 0001-TC-001: 通常の課税 {{#tc-001}}

| 項目 | 内容 |
|---|---|
| 対応仕様 | SPEC-F0001-01 |
| 期待値の根拠 | {evidence} |

# トレーサビリティマトリクス

| 仕様ID | 内容 | テストケース |
|---|---|---|
| SPEC-F0001-01 | 税額計算 | 0001-TC-001 |

# 未確定事項
"""


def make_project(evidence: str = "⚠未確定") -> Path:
    """fixture をコピーし、①draft ＋②generated が1件ずつある状態にする。"""
    root = Path(tempfile.mkdtemp(prefix="lr-report-")) / "proj"
    shutil.copytree(FIXTURE, root)
    spec = root / "docs" / "specs" / "F-0001.md"
    spec.write_text(spec.read_text(encoding="utf-8-sig")
                    .replace("status: reviewed", "status: draft"), encoding="utf-8")
    ts = root / "docs" / "test-specs"
    ts.mkdir(parents=True, exist_ok=True)
    (ts / "F-0001.md").write_text(
        TESTSPEC.format(spec_hash=review_checks.sha8(spec), evidence=evidence,
                        filler="長い方針をだらだら書いた行。" * 8), encoding="utf-8")
    return root


def test_reports_cover_both_phases():
    root = make_project()
    out = review_checks.make_reports(str(root))
    assert set(out) == {"spec", "testspec"}
    assert (root / "docs" / "spec-review.md").exists()
    assert (root / "docs" / "testspec-review.md").exists()
    assert out["spec"]["drafts"] == 1 and out["testspec"]["drafts"] == 1


def test_testspec_row_shows_cases_and_pending():
    root = make_project()
    review_checks.make_report(str(root), "testspec")
    page = (root / "docs" / "testspec-review.md").read_text(encoding="utf-8")
    assert "| 関数 | テスト方針 | ケース | ⚠未確定 |" in page
    row = [l for l in page.splitlines() if l.startswith("| [")][0]
    assert "⚠1" in row, row
    assert "sr-hold" in row, "⚠未確定が残る行は「⚠回答待ち」になる"
    assert "test-specs/F-0001.md" in row


def test_answered_testspec_becomes_approvable():
    root = make_project(evidence="仕様書🟢")
    res = review_checks.make_report(str(root), "testspec")
    row = [l for l in (root / "docs" / "testspec-review.md")
           .read_text(encoding="utf-8").splitlines() if l.startswith("| [")][0]
    assert "sr-can" in row, row
    assert res["pending_answer"] == []


def test_pending_blocks_approve(monkeypatch):
    root = make_project()
    monkeypatch.setattr(review_actions, "refresh_site", lambda *a, **k: True)
    r = review_actions.approve(str(root), "testspec", "F-0001", "tester")
    assert not r["ok"] and "⚠未確定" in r["message"]
    fm = review_actions.parse_frontmatter(
        (root / "docs" / "test-specs" / "F-0001.md").read_text(encoding="utf-8-sig"))
    assert fm["status"] == "generated", "拒否したのに status が動いてはいけない"


def test_page_is_view_only_and_row_height_capped():
    root = make_project()
    review_checks.make_reports(str(root))
    for name in ("spec-review.md", "testspec-review.md"):
        page = (root / "docs" / name).read_text(encoding="utf-8")
        low = page.lower()
        assert "fetch(" not in low and "<form" not in low and "xmlhttprequest" not in low
        assert "-webkit-line-clamp" in page and ".sr-cell" in page, "1行の高さの上限"
