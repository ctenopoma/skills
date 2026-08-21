#!/usr/bin/env python3
"""`ledger audit` のセルフテスト。

WBS の関数数と①バッチの対象件数が合わないとき、その差がどこへ消えたかを
内訳で出すコマンド。分類が actionable() の判定とずれると診断そのものが嘘に
なるため、「audit の『バッチの対象』件数 == actionable() の件数」を固定する。

検証対象:
  (a) excluded は WBS の母数に入らない（＝「除外分を足す」は二重計上）
  (b) 骨子なし・draft 待ち・blocked・reviewed がそれぞれ別の分類になる
  (c) 分類「① 未着手（バッチの対象）」の件数が actionable(phase=1) と一致する
  (d) functions.json に無い / excluded の仕様書ファイルを orphan として拾う
  (e) --json が機械可読で同じ内容を返す
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
LEDGER_PY = SCRIPTS_DIR / "ledger.py"

sys.path.insert(0, str(SCRIPTS_DIR))
import ledger        # noqa: E402

_TMPDIRS = []


def make_project() -> Path:
    """①の状態が全種類そろった最小プロジェクトを作る。"""
    tmp = Path(tempfile.mkdtemp(prefix="lr-audit-"))
    _TMPDIRS.append(tmp)
    root = tmp / "proj"
    (root / "data").mkdir(parents=True)
    (root / "docs" / "specs").mkdir(parents=True)
    (root / "legacy").mkdir()
    (root / "legacy" / "a.f").write_text("      SUBROUTINE SUB1\n      END\n", encoding="utf-8")

    funcs = []
    for i in range(1, 8):
        fid = f"F-{i:04d}"
        funcs.append({"func_id": fid,
                      "legacy": {"file": "legacy/a.f", "name": f"SUB{i}"},
                      "new": {"module": f"src/m{i}.py"},
                      "calls": []})
    funcs[6]["excluded"] = True                      # F-0007 は対象外
    (root / "data" / "functions.json").write_text(
        json.dumps({"project": {"name": "audit-fixture"}, "functions": funcs},
                   ensure_ascii=False), encoding="utf-8")
    (root / "data" / "ledger.json").write_text(
        json.dumps({"F-0005": {"blocked_by": "ISSUE-001"}}), encoding="utf-8")

    def spec(fid: str, status: str) -> None:
        (root / "docs" / "specs" / f"{fid}.md").write_text(
            f"---\nstatus: {status}\n---\n\n# 機能詳細\n", encoding="utf-8")

    spec("F-0001", "reviewed")
    spec("F-0002", "draft")
    spec("F-0003", "skeleton")                       # ← 唯一のバッチ対象
    spec("F-0005", "skeleton")                       # blocked なので対象外
    spec("F-0007", "skeleton")                       # excluded の仕様書＝orphan
    spec("F-0099", "draft")                          # functions.json に無い＝orphan
    # F-0004 / F-0006 は骨子なし
    return root


def run_audit(root: Path, *extra: str) -> tuple:
    r = subprocess.run([sys.executable, str(LEDGER_PY), "--root", str(root), "audit", *extra],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout + r.stderr


def test_buckets_and_orphans():
    root = make_project()
    p = ledger.Project(root)

    assert len(p.all_funcs()) == 7
    assert len(p.funcs()) == 6, "excluded は WBS の母数に入らない"

    _, groups = ledger.audit_buckets(p)
    got = {name: sorted(fids) for name, fids in groups.items()}
    assert got["① 完了（reviewed）"] == ["F-0001"]
    assert got["① draft（人のレビュー待ち。バッチは触らない）"] == ["F-0002"]
    assert got["骨子なし（docs/specs/<fid>.md が無い）"] == ["F-0004", "F-0006"]
    assert got["blocked（ISSUE-001 の裁定待ち）"] == ["F-0005"]
    assert got[ledger.AUDIT_TARGET] == ["F-0003"]

    code, out = run_audit(root)
    assert code == 0, out
    assert "骨子が無い関数が 2 件" in out, out
    assert "F-0007" in out and "F-0099" in out, "orphan な仕様書を拾えていない"


def test_target_count_matches_actionable():
    """内訳の「バッチの対象」は actionable() と一致していなければならない。

    ここがずれると audit は「バッチはこう見ている」を説明できなくなる。
    """
    root = make_project()
    p = ledger.Project(root)
    _, groups = ledger.audit_buckets(p)
    todo = [fid for fid, _ in ledger.actionable(p, phase="1", skip_wait=True)]
    # actionable は骨子なし（status "-"）も①の対象にする。audit はそれを
    # 別分類として見せるので、両者の和が actionable と一致する
    both = sorted(groups.get(ledger.AUDIT_TARGET, [])
                  + groups.get("骨子なし（docs/specs/<fid>.md が無い）", []))
    assert both == sorted(todo), (both, todo)


def test_json_output():
    root = make_project()
    code, out = run_audit(root, "--json")
    assert code == 0, out
    data = json.loads(out)
    assert data["all"] == 7 and data["wbs_n"] == 6
    assert data["excluded"] == ["F-0007"]
    assert data["missing_skeletons"] == ["F-0004", "F-0006"]
    assert sorted(data["orphan_spec_files"]) == ["F-0007", "F-0099"]
    assert data["actionable_phase1"] == sorted(data["actionable_phase1"])


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
    for d in _TMPDIRS:
        shutil.rmtree(d, ignore_errors=True)
    print("test_audit:", "OK" if not failed else f"{failed} 件失敗")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
