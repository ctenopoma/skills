#!/usr/bin/env python3
"""graph.py のセルフテスト（pytest不要・素の assert）。

`python test_graph.py` で実行する。ライブラリ API（load_functions / graph_from_data /
load_graph / reverse_graph / reachable / reachable_depths / bfs_path / sccs）と、
CLI サブコマンド（reachable / callers / between / dead / cycles / summary）の両方を、
scripts/selftest/fixture_graph/ の functions.json フィクスチャで検証する。

フィクスチャの構造（すべて legacy_name つき func_id）:
  F-0000(MAIN) -> F-0001(SUB1), F-0002(SUB2)
  F-0001(SUB1) -> F-0003(CALCTAX)
  F-0002(SUB2) -> F-0003(CALCTAX), F-0004(PING)
  F-0004(PING) <-> F-0005(PONG)              循環（サイズ2）
  F-0006(BATCHENTRY) -> F-0007(BATCHLEAF)    F-0000からは到達不能（flows経由でのみ到達）
  F-0008(OLDUNUSED)                          excluded=true・孤立
  flows: FL-01(F-0000), FL-02(F-0006)
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixture_graph"
GRAPH_PY = SCRIPTS_DIR / "graph.py"

sys.path.insert(0, str(SCRIPTS_DIR))
import graph  # noqa: E402


def run_cli(*args):
    """graph.py を subprocess で実行し (returncode, stdout, stderr) を返す。

    Windows のコンソールコードページ（cp932等）ではなく常に utf-8 で
    子プロセスの標準出力/エラーを取れるよう PYTHONIOENCODING を強制する。
    """
    cmd = [sys.executable, str(GRAPH_PY), "--root", str(FIXTURE_ROOT), *args]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
    return r.returncode, r.stdout, r.stderr


# ---------- ライブラリ API ----------

def test_load_functions_and_graph_from_data():
    data = graph.load_functions(FIXTURE_ROOT)
    assert data["project"]["name"] == "graph-selftest"
    assert len(data["functions"]) == 9

    g = graph.graph_from_data(data)
    assert set(g.keys()) == {f"F-000{i}" for i in range(9)}
    assert g["F-0000"] == {"F-0001", "F-0002"}
    assert g["F-0001"] == {"F-0003"}
    assert g["F-0002"] == {"F-0003", "F-0004"}
    assert g["F-0003"] == set()
    assert g["F-0004"] == {"F-0005"}
    assert g["F-0005"] == {"F-0004"}
    assert g["F-0006"] == {"F-0007"}
    assert g["F-0007"] == set()
    # excluded 関数もノードとして残る（辺は無い）
    assert g["F-0008"] == set()
    print("OK  load_functions / graph_from_data")


def test_load_graph_matches_graph_from_data():
    g1 = graph.load_graph(FIXTURE_ROOT)
    g2 = graph.graph_from_data(graph.load_functions(FIXTURE_ROOT))
    assert g1 == g2
    print("OK  load_graph")


def test_reverse_graph():
    g = graph.load_graph(FIXTURE_ROOT)
    rg = graph.reverse_graph(g)
    assert rg["F-0003"] == {"F-0001", "F-0002"}
    assert rg["F-0004"] == {"F-0002", "F-0005"}
    assert rg["F-0000"] == set()
    assert rg["F-0007"] == {"F-0006"}
    print("OK  reverse_graph")


def test_reachable_and_depths():
    g = graph.load_graph(FIXTURE_ROOT)
    depths = graph.reachable_depths(g, ["F-0000"])
    assert depths["F-0000"] == 0
    assert depths["F-0001"] == 1 and depths["F-0002"] == 1
    assert depths["F-0003"] == 2
    assert depths["F-0004"] == 2
    assert depths["F-0005"] == 3
    assert "F-0006" not in depths and "F-0007" not in depths and "F-0008" not in depths

    reached = graph.reachable(g, ["F-0000"])
    assert reached == {"F-0000", "F-0001", "F-0002", "F-0003", "F-0004", "F-0005"}

    # 複数エントリ（多始点）
    reached2 = graph.reachable(g, ["F-0000", "F-0006"])
    assert reached2 == {"F-0000", "F-0001", "F-0002", "F-0003", "F-0004", "F-0005",
                         "F-0006", "F-0007"}
    print("OK  reachable / reachable_depths")


def test_bfs_path():
    g = graph.load_graph(FIXTURE_ROOT)
    path = graph.bfs_path(g, "F-0000", "F-0004")
    assert path == ["F-0000", "F-0002", "F-0004"], path
    assert graph.bfs_path(g, "F-0000", "F-0000") == ["F-0000"]
    assert graph.bfs_path(g, "F-0000", "F-0007") is None  # 到達不能
    assert graph.bfs_path(g, "F-0004", "F-0005") == ["F-0004", "F-0005"]
    print("OK  bfs_path")


def test_sccs():
    g = graph.load_graph(FIXTURE_ROOT)
    comps = graph.sccs(g)
    big = [c for c in comps if len(c) >= 2]
    assert len(big) == 1
    assert big[0] == {"F-0004", "F-0005"}
    # 全ノードがどこかの成分に属する（サイズ1の自明成分も含む）
    covered = set()
    for c in comps:
        covered |= c
    assert covered == set(g.keys())
    print("OK  sccs")


def test_default_entries_variants():
    data = graph.load_functions(FIXTURE_ROOT)
    g = graph.graph_from_data(data)

    # 1) flows あり（フィクスチャそのまま）: FL-01(F-0000) + FL-02(F-0006) の和集合
    entries, desc = graph.default_entries(data, g)
    assert sorted(entries) == ["F-0000", "F-0006"]
    assert "flows" in desc

    # 2) flows なし・F-0000 あり（in-memory で flows を除去。ディスク上のフィクスチャは変更しない）
    data_no_flows = json.loads(json.dumps(data))
    del data_no_flows["flows"]
    entries2, desc2 = graph.default_entries(data_no_flows, g)
    assert entries2 == ["F-0000"]
    assert desc2 == "F-0000"

    # 3) flows も F-0000 も無い: 入次数0のノードを暫定エントリにする（警告つき）
    data_none = {"functions": [f for f in data["functions"] if f["func_id"] != "F-0000"]}
    # F-0000 を削っただけでは F-0000 への参照が残るが graph_from_data は防御的に無視する
    g_none = graph.graph_from_data(data_none)
    entries3, desc3 = graph.default_entries(data_none, g_none)
    # 入次数0: F-0001,F-0002(誰もcallしない, F-0000は無い), F-0006, F-0008
    assert set(entries3) == {"F-0001", "F-0002", "F-0006", "F-0008"}
    assert "暫定エントリ" in desc3
    print("OK  default_entries（flowsあり／なし／どちらも無し の3パターン）")


# ---------- CLI ----------

def test_cli_reachable():
    rc, out, err = run_cli("reachable", "F-0000", "--json")
    assert rc == 0, err
    rows = json.loads(out)
    ids = {r["func_id"] for r in rows}
    assert ids == {"F-0000", "F-0001", "F-0002", "F-0003", "F-0004", "F-0005"}
    by_id = {r["func_id"]: r for r in rows}
    assert by_id["F-0000"]["depth"] == 0
    assert by_id["F-0005"]["depth"] == 3
    assert by_id["F-0001"]["legacy_name"] == "SUB1"
    assert all(not r["excluded"] for r in rows)

    # --flow 指定
    rc, out, err = run_cli("reachable", "--flow", "FL-02", "--json")
    assert rc == 0, err
    ids2 = {r["func_id"] for r in json.loads(out)}
    assert ids2 == {"F-0006", "F-0007"}

    # 未知の fid はエラー終了
    rc, out, err = run_cli("reachable", "F-9999")
    assert rc == 1
    assert "F-9999" in (out + err)

    # fid と --flow の同時指定はエラー
    rc, out, err = run_cli("reachable", "F-0000", "--flow", "FL-01")
    assert rc == 1
    print("OK  CLI reachable")


def test_cli_callers():
    rc, out, err = run_cli("callers", "F-0003", "--json")
    assert rc == 0, err
    direct = {r["func_id"] for r in json.loads(out)}
    assert direct == {"F-0001", "F-0002"}

    rc, out, err = run_cli("callers", "F-0004", "--transitive", "--json")
    assert rc == 0, err
    trans = {r["func_id"] for r in json.loads(out)}
    # F-0004 の推移的呼び出し元: F-0002(直接), F-0005(循環), F-0000(F-0002経由)
    assert trans == {"F-0002", "F-0005", "F-0000"}
    assert "F-0004" not in trans

    rc, out, err = run_cli("callers", "F-9999")
    assert rc == 1
    print("OK  CLI callers")


def test_cli_between():
    rc, out, err = run_cli("between", "F-0000", "F-0004", "--json")
    assert rc == 0, err
    res = json.loads(out)
    assert res["found"] is True
    assert res["path"] == ["F-0000", "F-0002", "F-0004"]
    assert res["length"] == 2

    rc, out, err = run_cli("between", "F-0000", "F-0007", "--json")
    assert rc == 0, err
    res = json.loads(out)
    assert res["found"] is False
    assert res["path"] is None

    rc, out, err = run_cli("between", "F-0000", "F-9999")
    assert rc == 1
    print("OK  CLI between")


def test_cli_dead():
    # フィクスチャそのまま（flows定義あり）: FL-01+FL-02 の和集合で F-0006/F-0007 も到達可能
    # → dead 候補は 0 件、excluded の F-0008 だけが別掲される
    rc, out, err = run_cli("dead", "--json")
    assert rc == 0, err
    res = json.loads(out)
    assert res["dead"] == []
    assert [e["func_id"] for e in res["excluded"]] == ["F-0008"]
    assert res["entries_source"].startswith("flows")
    assert sorted(res["entries"]) == ["F-0000", "F-0006"]

    # テキスト出力にも「exclude の自動実行はしない」旨の文言があること
    rc, out, err = run_cli("dead")
    assert rc == 0, err
    assert "自動実行はしない" in out
    print("OK  CLI dead")


def test_cli_cycles():
    rc, out, err = run_cli("cycles", "--json")
    assert rc == 0, err
    comps = json.loads(out)
    assert len(comps) == 1
    ids = {n["func_id"] for n in comps[0]}
    assert ids == {"F-0004", "F-0005"}
    print("OK  CLI cycles")


def test_cli_summary():
    rc, out, err = run_cli("summary")
    assert rc == 0, err
    s = json.loads(out)
    assert s["nodes"] == 9
    assert s["edges"] == sum(len(v) for v in graph.load_graph(FIXTURE_ROOT).values())
    assert s["dead"] == 0
    assert s["excluded"] == 1
    assert s["cycles"] == 1
    assert s["flows"] == 2
    assert s["reachable"] == 8  # 9件中 excluded の F-0008 だけ到達集合に入らない
    print("OK  CLI summary")


def main():
    tests = [
        test_load_functions_and_graph_from_data,
        test_load_graph_matches_graph_from_data,
        test_reverse_graph,
        test_reachable_and_depths,
        test_bfs_path,
        test_sccs,
        test_default_entries_variants,
        test_cli_reachable,
        test_cli_callers,
        test_cli_between,
        test_cli_dead,
        test_cli_cycles,
        test_cli_summary,
    ]
    for t in tests:
        t()
    print(f"\nPASS: {len(tests)}件すべて成功")


if __name__ == "__main__":
    main()
