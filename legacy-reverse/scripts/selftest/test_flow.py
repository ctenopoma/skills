#!/usr/bin/env python3
"""M3（フロー / 作業スコープ）のセルフテスト（pytest不要・素の assert）。

`python test_flow.py` で走る。scripts/selftest/fixture_graph/data/functions.json を
一時ディレクトリへコピーして使う（graph.py の test_graph.py と同じフィクスチャ。
FL-01(メインフロー: F-0000 到達6件) / FL-02(サブバッチ: F-0006 到達2件) が定義済み、
F-0008 は excluded）。書き込みを伴うテストのみ、テストごとに個別の一時コピーを使う。

検証対象:
  - ledger.py flow add/rm/list（functions.json の保存・エントリ実在検証・excluded警告）
  - ledger.py next --flow（到達集合への絞り込み）
  - ledger.py wbs のフロー別進捗表（flows定義時のみ追加・未定義時は従来出力と完全一致）
  - ledger.py skeletons の flows フロントマター（所属フロー・複数所属・未定義時は出さない）
  - pipeline.py の対象選定（cmd_spec / cmd_run を --dry-run で直接呼び、
    claude を起動せず対象の絞り込みだけを検証する）
"""
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
FIXTURE_SRC = Path(__file__).resolve().parent / "fixture_graph" / "data" / "functions.json"
LEDGER_PY = SCRIPTS_DIR / "ledger.py"

sys.path.insert(0, str(SCRIPTS_DIR))
import ledger    # noqa: E402
import pipeline  # noqa: E402


# ---------- フィクスチャ準備 ----------

def setup_project(tmp: Path) -> Path:
    """fixture_graph/data/functions.json を一時プロジェクトへコピーする。"""
    (tmp / "data").mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE_SRC, tmp / "data" / "functions.json")
    return tmp


def load_data(root: Path) -> dict:
    return json.loads((root / "data" / "functions.json").read_text(encoding="utf-8-sig"))


def strip_flows(root: Path) -> None:
    """flows キーを取り除く（回帰確認用: 「flows未定義」プロジェクトを作る）。"""
    data = load_data(root)
    data.pop("flows", None)
    (root / "data" / "functions.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_ledger(root: Path, *args):
    """ledger.py を subprocess で実行し (returncode, stdout, stderr) を返す（utf-8固定）。"""
    cmd = [sys.executable, str(LEDGER_PY), "--root", str(root), *args]
    import os
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
    return r.returncode, r.stdout, r.stderr


# ---------- flow add / rm / list ----------

def test_flow_add_persists_and_validates():
    with tempfile.TemporaryDirectory() as td:
        root = setup_project(Path(td))

        rc, out, err = run_ledger(root, "flow", "add", "サブ検証", "--entry", "F-0002",
                                  "--desc", "検証用フロー")
        assert rc == 0, err
        assert "added: FL-03" in out, out

        data = load_data(root)
        flows = data["flows"]
        assert len(flows) == 3
        assert flows[2] == {"flow_id": "FL-03", "name": "サブ検証",
                            "entries": ["F-0002"], "desc": "検証用フロー"}
        # 既存の FL-01/FL-02 は触らない
        assert flows[0]["flow_id"] == "FL-01" and flows[1]["flow_id"] == "FL-02"
        print("OK  flow add: 保存内容")


def test_flow_add_rejects_unknown_entry():
    with tempfile.TemporaryDirectory() as td:
        root = setup_project(Path(td))
        before = load_data(root)

        rc, out, err = run_ledger(root, "flow", "add", "不正フロー", "--entry", "F-9999")
        assert rc == 1, out + err
        assert "F-9999" in (out + err)

        after = load_data(root)
        assert after == before, "エラー時に functions.json が変化してはいけない"
        print("OK  flow add: 未定義 func-id はエラーで functions.json 不変")


def test_flow_add_rejects_duplicate_name():
    with tempfile.TemporaryDirectory() as td:
        root = setup_project(Path(td))
        rc, out, err = run_ledger(root, "flow", "add", "メインフロー", "--entry", "F-0002")
        assert rc == 1, out + err
        assert "メインフロー" in (out + err)
        assert len(load_data(root)["flows"]) == 2
        print("OK  flow add: 同名フローはエラー")


def test_flow_add_warns_on_excluded_entry():
    with tempfile.TemporaryDirectory() as td:
        root = setup_project(Path(td))
        # F-0008 は fixture 上 excluded=true
        rc, out, err = run_ledger(root, "flow", "add", "対象外含み", "--entry", "F-0008")
        assert rc == 0, err
        assert "warn:" in out and "F-0008" in out
        data = load_data(root)
        assert data["flows"][-1]["entries"] == ["F-0008"]
        print("OK  flow add: excluded な entry は警告のみ（追加は成功）")


def test_flow_rm():
    with tempfile.TemporaryDirectory() as td:
        root = setup_project(Path(td))

        rc, out, err = run_ledger(root, "flow", "rm", "FL-01")
        assert rc == 0, err
        assert "removed: FL-01" in out
        data = load_data(root)
        assert [fl["flow_id"] for fl in data["flows"]] == ["FL-02"]

        # name 指定でも削除できる
        rc, out, err = run_ledger(root, "flow", "rm", "サブバッチ")
        assert rc == 0, err
        assert "removed: FL-02" in out
        data = load_data(root)
        assert not data.get("flows"), "flows が空になったら消えている、または空配列"

        rc, out, err = run_ledger(root, "flow", "rm", "存在しない")
        assert rc == 1
        assert "見つからない" in (out + err)
        print("OK  flow rm（flow_id指定／name指定／未知キーはエラー）")


def test_flow_list():
    with tempfile.TemporaryDirectory() as td:
        root = setup_project(Path(td))
        rc, out, err = run_ledger(root, "flow", "list")
        assert rc == 0, err
        assert "FL-01" in out and "メインフロー" in out and "entries=F-0000" in out
        assert "到達6件" in out
        assert "FL-02" in out and "到達2件" in out

        strip_flows(root)
        rc, out, err = run_ledger(root, "flow", "list")
        assert rc == 0, err
        assert "未定義" in out
        print("OK  flow list（定義あり／flows未定義）")


# ---------- next --flow ----------

def test_next_flow_scoping():
    with tempfile.TemporaryDirectory() as td:
        root = setup_project(Path(td))

        rc, out, err = run_ledger(root, "next", "--all", "--flow", "FL-02")
        assert rc == 0, err
        fids = {line.split("\t")[0] for line in out.strip().splitlines() if "\t" in line}
        assert fids == {"F-0006", "F-0007"}, fids

        rc, out, err = run_ledger(root, "next", "--all", "--flow", "FL-01")
        assert rc == 0, err
        fids = {line.split("\t")[0] for line in out.strip().splitlines() if "\t" in line}
        assert fids == {"F-0000", "F-0001", "F-0002", "F-0003", "F-0004", "F-0005"}, fids

        # --flow 未指定は従来どおり全関数（excluded の F-0008 を除く8件）
        rc, out, err = run_ledger(root, "next", "--all")
        assert rc == 0, err
        fids = {line.split("\t")[0] for line in out.strip().splitlines() if "\t" in line}
        assert fids == {"F-0000", "F-0001", "F-0002", "F-0003",
                        "F-0004", "F-0005", "F-0006", "F-0007"}, fids

        rc, out, err = run_ledger(root, "next", "--flow", "FL-99")
        assert rc == 1
        assert "見つからない" in (out + err)
        print("OK  next --flow（絞り込み／未指定時は全関数／未知フローはエラー）")


def test_resolve_flow_fids_and_actionable_direct():
    """ライブラリ API を直接呼ぶ版（pipeline.py が同じ関数を共用することの土台）。"""
    with tempfile.TemporaryDirectory() as td:
        root = setup_project(Path(td))
        p = ledger.Project(root)

        fids = ledger.resolve_flow_fids(p, "FL-02")
        assert fids == {"F-0006", "F-0007"}

        todo = ledger.actionable(p, phase="1", flow_fids=fids)
        assert {fid for fid, _ in todo} == {"F-0006", "F-0007"}

        todo_all = ledger.actionable(p, phase="1")
        assert {fid for fid, _ in todo_all} == {
            "F-0000", "F-0001", "F-0002", "F-0003", "F-0004", "F-0005", "F-0006", "F-0007"}
        print("OK  resolve_flow_fids / actionable（直接 import）")


# ---------- wbs ----------

def test_wbs_flow_table_and_no_flows_regression():
    with tempfile.TemporaryDirectory() as td_a, tempfile.TemporaryDirectory() as td_b:
        root_with = setup_project(Path(td_a))
        rc, out, err = run_ledger(root_with, "wbs")
        assert rc == 0, err
        content_with = (root_with / "docs" / "index.qmd").read_text(encoding="utf-8")
        assert "# フロー別進捗" in content_with
        assert "| メインフロー | F-0000 | 6 " in content_with
        assert "| サブバッチ | F-0006 | 2 " in content_with

        root_without = setup_project(Path(td_b))
        strip_flows(root_without)
        rc, out, err = run_ledger(root_without, "wbs")
        assert rc == 0, err
        content_without = (root_without / "docs" / "index.qmd").read_text(encoding="utf-8")
        assert "# フロー別進捗" not in content_without

        # flows定義時の出力から「フロー別進捗」節だけを取り除くと、
        # flows未定義時の出力と完全一致する（＝それ以外は一切変えていない証明）。
        # フィクスチャは F-0004<->F-0005 の循環を含むため、次節は必ず循環の callout になる。
        start = content_with.index("# フロー別進捗")
        end = content_with.index("::: {.callout-warning}", start)
        assert end > start
        stripped = content_with[:start] + content_with[end:]
        assert stripped == content_without, "flows未定義時と完全一致しない（回帰）"
        print("OK  wbs: フロー別進捗表の追加 と flows未定義時の完全一致")


# ---------- skeletons ----------

def test_skeletons_flows_frontmatter():
    with tempfile.TemporaryDirectory() as td_a, tempfile.TemporaryDirectory() as td_b:
        root_with = setup_project(Path(td_a))
        # F-0002 は FL-01(メインフロー) の到達集合に含まれる。ここへ entry が重なる
        # 別フローを追加し、複数所属のケースも見る
        rc, out, err = run_ledger(root_with, "flow", "add", "重複確認", "--entry", "F-0002")
        assert rc == 0, err

        rc, out, err = run_ledger(root_with, "skeletons")
        assert rc == 0, err

        def frontmatter_flows(fid: str) -> str | None:
            text = (root_with / "docs" / "specs" / f"{fid}.md").read_text(encoding="utf-8")
            for line in text.splitlines():
                if line.startswith("flows:"):
                    return line
            return None

        # F-0002 は「重複確認」の entry 自身。F-0002 が呼ぶ F-0003/F-0004 も
        # 「重複確認」の到達集合に入るため、いずれも両フロー所属になる
        assert frontmatter_flows("F-0002") == 'flows: ["メインフロー", "重複確認"]'
        assert frontmatter_flows("F-0003") == 'flows: ["メインフロー", "重複確認"]'
        # F-0001 は F-0000(メインフローのentry) からのみ到達し、F-0002からは到達しない
        assert frontmatter_flows("F-0001") == 'flows: ["メインフロー"]'
        assert frontmatter_flows("F-0006") == 'flows: ["サブバッチ"]'

        root_without = setup_project(Path(td_b))
        strip_flows(root_without)
        rc, out, err = run_ledger(root_without, "skeletons")
        assert rc == 0, err
        for fid in ("F-0000", "F-0006"):
            text = (root_without / "docs" / "specs" / f"{fid}.md").read_text(encoding="utf-8")
            assert "\nflows:" not in text
        print("OK  skeletons: flows フロントマター（複数所属／未定義時は出さない）")


# ---------- pipeline.py 対象選定（claude は起動しない: --dry-run 相当） ----------

def _spec_args(root: Path, flow: str | None) -> types.SimpleNamespace:
    d = dict(pipeline.RUN_ARG_DEFAULTS)
    return types.SimpleNamespace(
        root=str(root), chunk=10, max_funcs=0, budget_usd=0,
        max_consecutive_fail=3, pause=0, model=None, skip_permissions=False,
        claude_cmd=None, claude_args=[], dry_run=True, no_render=True,
        flow=flow, prompt_template="/legacy-1-spec {fid}", **d)


def _run_args(root: Path, flow: str | None,
              only: str | None = None) -> types.SimpleNamespace:
    d = dict(pipeline.RUN_ARG_DEFAULTS)
    return types.SimpleNamespace(
        root=str(root), chunk=5, max_funcs=0, budget_usd=0,
        max_consecutive_fail=3, pause=0, model=None, skip_permissions=False,
        claude_cmd=None, claude_args=[], dry_run=True, no_render=True,
        flow=flow, only=only, **d)


def test_pipeline_spec_targets_flow_filter():
    with tempfile.TemporaryDirectory() as td:
        root = setup_project(Path(td))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pipeline.cmd_spec(_spec_args(root, "FL-02"))
        out = buf.getvalue()
        assert "フロー FL-02: 2 関数に限定" in out, out
        assert "対象 2 件から開始" in out, out
        assert "F-0006" in out and "F-0007" in out
        assert "F-0000" not in out and "F-0001" not in out
        print("OK  pipeline.cmd_spec --flow（dry-run・claude未起動）")


def test_pipeline_run_targets_flow_filter():
    with tempfile.TemporaryDirectory() as td:
        root = setup_project(Path(td))
        # _decide_kind は docs/specs/<fid>.md の存在を前提にする（status: skeleton → kind spec）
        rc, out, err = run_ledger(root, "skeletons")
        assert rc == 0, err

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pipeline.cmd_run(_run_args(root, "FL-01"))
        out = buf.getvalue()
        assert "フロー FL-01: 6 関数に限定" in out, out
        assert "対象 6 工程から開始" in out, out
        for fid in ("F-0000", "F-0001", "F-0002", "F-0003", "F-0004", "F-0005"):
            assert fid in out, (fid, out)
        assert "F-0006" not in out and "F-0007" not in out
        print("OK  pipeline.cmd_run --flow（dry-run・claude未起動）")


def test_pipeline_run_only_filter():
    """--only による工程限定。フィクスチャの残工程は全件①なので、
    --only spec は全件・--only testspec は0件になるはず。"""
    with tempfile.TemporaryDirectory() as td:
        root = setup_project(Path(td))
        rc, out, err = run_ledger(root, "skeletons")
        assert rc == 0, err

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pipeline.cmd_run(_run_args(root, None, only="spec"))
        out = buf.getvalue()
        assert "工程を限定: ①仕様書" in out, out
        assert "から開始" in out, out

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            pipeline.cmd_run(_run_args(root, None, only="testspec"))
        out = buf.getvalue()
        assert "実行できる工程がない" in out, out

        try:
            pipeline.cmd_run(_run_args(root, None, only="spec,foo"))
            raise AssertionError("--only の不明工程で SystemExit にならなかった")
        except SystemExit as e:
            assert "foo" in str(e), e
        print("OK  pipeline.cmd_run --only（工程限定・不明工程はエラー）")


def main() -> None:
    tests = [
        test_flow_add_persists_and_validates,
        test_flow_add_rejects_unknown_entry,
        test_flow_add_rejects_duplicate_name,
        test_flow_add_warns_on_excluded_entry,
        test_flow_rm,
        test_flow_list,
        test_next_flow_scoping,
        test_resolve_flow_fids_and_actionable_direct,
        test_wbs_flow_table_and_no_flows_regression,
        test_skeletons_flows_frontmatter,
        test_pipeline_spec_targets_flow_filter,
        test_pipeline_run_targets_flow_filter,
        test_pipeline_run_only_filter,
    ]
    for t in tests:
        t()
    print(f"\nPASS: {len(tests)}件すべて成功")


if __name__ == "__main__":
    main()
