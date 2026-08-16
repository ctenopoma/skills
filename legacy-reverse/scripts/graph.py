#!/usr/bin/env python3
"""legacy-reverse コールグラフ CLI（scripts/graph.py）。

対象プロジェクトのルートで実行する（--root で変更可）。
data/functions.json（正データ）から毎回グラフを構築して答える、依存ゼロ・LLM不使用の
導出層。外部へのグラフエクスポートは行わない（この開発範囲で閉じる。設計は
references/graph-dict-design.md の P1 節）。

サブコマンド:
  reachable <fid...> [--flow <name|flow_id>] [--json]
                              到達可能集合（fid・legacy名・深さ）。fid複数可、--flowで代替
  callers <fid> [--transitive] [--json]
                              逆方向。既定は直接の呼び出し元、--transitive で推移的な影響範囲
  between <from> <to> [--json]
                              BFS最短経路（fidの列）。経路が無ければその旨を表示
  dead [--json]               エントリ（flowsの和集合 → 無ければF-0000 → それも無ければ
                              入次数0の関数を暫定エントリとして警告）から到達不能な関数。
                              excluded は「対象外(除外済み)」として別掲し、dead候補には出さない。
                              exclude の自動実行はしない（候補列挙のみ。実行は `ledger exclude`）
  cycles [--json]             Tarjan の SCC でサイズ2以上の成分（循環）を列挙
  summary                     ノード/エッジ数・エントリ・到達率・dead件数・循環数（JSON）

Python ライブラリとしても使う（ledger.py の flows 絞り込み・variables.py の伝搬が import する）:
  load_functions(root) -> dict          functions.json をそのまま返す
  graph_from_data(data) -> dict         {fid: set(callee fid)}（内部用・data から直接構築）
  load_graph(root) -> dict[fid,set]     ファイルから読んでグラフ構築（load_functions+graph_from_data）
  reverse_graph(g) -> dict[fid,set]     辺を反転した隣接リスト
  reachable(g, entries) -> set          entries から到達可能な fid 集合（entries 自身も含む）
  reachable_depths(g, entries) -> dict  fid -> entries からの最短深さ（entries は 0）
  bfs_path(g, src, dst) -> list | None  最短経路（fidの列）。無ければ None
  sccs(g) -> list[set]                  強連結成分（Tarjan・反復実装）。サイズ1の自明成分も含む
"""
import argparse
import json
import sys
from collections import deque
from pathlib import Path

# ---------- ライブラリ API ----------


def load_functions(root) -> dict:
    """data/functions.json を読み込む。無い・壊れている場合は分かりやすいエラーで終了する。"""
    path = Path(root) / "data" / "functions.json"
    if not path.exists():
        sys.exit(f"error: {path} が存在しない（先に⓪の抽出を実行する）")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        sys.exit(f"error: {path} が JSON として読めない（{e.lineno}行{e.colno}列: {e.msg}）")


def graph_from_data(data: dict) -> dict:
    """functions.json の内容（dict）から隣接リストを構築する。

    ノードは functions 配列の全 func_id（excluded も含む——ノードとしては残し、
    dead 判定側で区別表示する）。`calls` は既に func_id 配列に解決済みの正データ
    なのでそのまま辺にする。`unresolved_calls`（名前だけの未解決呼び出し）は
    func_id を持たないため辺にしない。functions.json に存在しない func_id への
    calls 参照（データ不整合）は防御的に無視する。
    """
    funcs = data.get("functions", [])
    ids = {f["func_id"] for f in funcs}
    g = {fid: set() for fid in ids}
    for f in funcs:
        fid = f["func_id"]
        for callee in f.get("calls", []):
            if callee in ids:
                g[fid].add(callee)
    return g


def load_graph(root) -> dict:
    """functions.json を読んでグラフを構築する（load_functions + graph_from_data）。"""
    return graph_from_data(load_functions(root))


def reverse_graph(g: dict) -> dict:
    """辺の向きを反転した隣接リストを返す（呼び出し元を辿るため）。"""
    rg = {n: set() for n in g}
    for n, callees in g.items():
        for c in callees:
            rg.setdefault(c, set()).add(n)
    return rg


def reachable_depths(g: dict, entries) -> dict:
    """entries からの多始点 BFS で最短深さを返す（entries 自身は深さ0）。

    g に存在しない fid は無視する（呼び出し側で事前検証する想定）。
    """
    depth = {e: 0 for e in entries if e in g}
    q = deque(depth)
    while q:
        n = q.popleft()
        for c in g.get(n, ()):
            if c not in depth:
                depth[c] = depth[n] + 1
                q.append(c)
    return depth


def reachable(g: dict, entries) -> set:
    """entries から到達可能な fid 集合（entries 自身も含む）。"""
    return set(reachable_depths(g, entries))


def bfs_path(g: dict, src: str, dst: str):
    """src から dst への最短経路（fid の列）。到達不能なら None。"""
    if src not in g:
        return None
    if src == dst:
        return [src]
    prev = {src: None}
    q = deque([src])
    while q:
        n = q.popleft()
        for c in g.get(n, ()):
            if c in prev:
                continue
            prev[c] = n
            if c == dst:
                path = [c]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])
                path.reverse()
                return path
            q.append(c)
    return None


def sccs(g: dict) -> list:
    """Tarjan の強連結成分分解（反復実装）。

    再帰版は数千ノード規模で Python の再帰上限に当たり得るため、
    明示スタック（(node, 隣接イテレータ) の組）で DFS を模倣する。
    戻り値はサイズ1の自明な成分も含む fid の set のリスト
    （呼び出し側の `cycles` コマンドがサイズ2以上に絞る）。
    """
    counter = [0]
    index: dict = {}
    lowlink: dict = {}
    on_stack: dict = {}
    node_stack: list = []
    result: list = []

    for start in g:
        if start in index:
            continue
        work = [(start, iter(g.get(start, ())))]
        index[start] = lowlink[start] = counter[0]
        counter[0] += 1
        node_stack.append(start)
        on_stack[start] = True

        while work:
            v, it = work[-1]
            try:
                w = next(it)
            except StopIteration:
                work.pop()
                if work:
                    parent = work[-1][0]
                    lowlink[parent] = min(lowlink[parent], lowlink[v])
                if lowlink[v] == index[v]:
                    comp = set()
                    while True:
                        w2 = node_stack.pop()
                        on_stack[w2] = False
                        comp.add(w2)
                        if w2 == v:
                            break
                    result.append(comp)
                continue
            if w not in index:
                index[w] = lowlink[w] = counter[0]
                counter[0] += 1
                node_stack.append(w)
                on_stack[w] = True
                work.append((w, iter(g.get(w, ()))))
            elif on_stack.get(w):
                lowlink[v] = min(lowlink[v], index[w])
    return result


# ---------- 補助（CLI向け） ----------


def legacy_name(f: dict) -> str:
    return (f.get("legacy") or {}).get("name", "?")


def is_excluded(f: dict) -> bool:
    return bool(f.get("excluded"))


def _fmap(data: dict) -> dict:
    return {f["func_id"]: f for f in data.get("functions", [])}


def _check_fid(fmap: dict, fid: str) -> None:
    if fid not in fmap:
        sys.exit(f"error: {fid} は functions.json に存在しない")


def get_flow(data: dict, key: str) -> dict:
    """flow_id または name でフローを引く。無ければ定義済み一覧つきで終了する。"""
    flows = data.get("flows") or []
    for fl in flows:
        if fl.get("flow_id") == key or fl.get("name") == key:
            return fl
    available = ", ".join(f"{fl.get('flow_id')}({fl.get('name')})" for fl in flows)
    available = available or "(flows は未定義)"
    sys.exit(f"error: フロー '{key}' が見つからない。定義済み: {available}")


def default_entries(data: dict, g: dict):
    """dead / summary が使う既定エントリを決める。

    優先順位: flows[].entries の和集合 → F-0000 → （どちらも無ければ）入次数0の関数。
    戻り値は (entries: list[str], 説明文字列)。
    """
    fmap = _fmap(data)
    flows = data.get("flows") or []
    if flows:
        entries = sorted({e for fl in flows for e in fl.get("entries", [])})
        for fid in entries:
            if fid not in fmap:
                sys.exit(f"error: flows の entries に functions.json 未定義の func_id '{fid}' がある")
        return entries, f"flows({len(flows)}件)の和集合"
    if "F-0000" in fmap:
        return ["F-0000"], "F-0000"
    rg = reverse_graph(g)
    indeg0 = sorted(n for n in g if not rg.get(n))
    print("warning: functions.json に flows も F-0000 も無い。"
          "呼び出し元を持たない関数を暫定エントリとして扱う", file=sys.stderr)
    return indeg0, "呼び出し元を持たない関数（暫定エントリ）"


# ---------- サブコマンド ----------


def cmd_reachable(data: dict, g: dict, args) -> None:
    fmap = _fmap(data)
    if args.flow and args.fids:
        sys.exit("error: fid 指定と --flow は同時に指定できない")
    if args.flow:
        fl = get_flow(data, args.flow)
        entries = fl.get("entries", [])
        if not entries:
            sys.exit(f"error: フロー '{args.flow}' に entries が無い")
    elif args.fids:
        entries = args.fids
    else:
        sys.exit("error: エントリを指定する（fid を1つ以上、または --flow <name|flow_id>）")
    for fid in entries:
        _check_fid(fmap, fid)

    depths = reachable_depths(g, entries)
    rows = sorted(depths.items(), key=lambda kv: (kv[1], kv[0]))
    if args.json:
        print(json.dumps([
            {"func_id": fid, "legacy_name": legacy_name(fmap.get(fid, {})),
             "depth": d, "excluded": is_excluded(fmap.get(fid, {}))}
            for fid, d in rows
        ], ensure_ascii=False, indent=1))
        return
    print(f"エントリ: {', '.join(entries)} / 到達可能: {len(rows)}件")
    for fid, d in rows:
        tag = " [対象外]" if is_excluded(fmap.get(fid, {})) else ""
        print(f"{fid}\t{legacy_name(fmap.get(fid, {}))}\t{d}{tag}")


def cmd_callers(data: dict, g: dict, args) -> None:
    fmap = _fmap(data)
    _check_fid(fmap, args.fid)
    rg = reverse_graph(g)
    if args.transitive:
        depths = reachable_depths(rg, [args.fid])
        depths.pop(args.fid, None)
        rows = sorted(depths.items(), key=lambda kv: (kv[1], kv[0]))
    else:
        rows = [(fid, 1) for fid in sorted(rg.get(args.fid, ()))]

    if args.json:
        print(json.dumps([
            {"func_id": fid, "legacy_name": legacy_name(fmap.get(fid, {})),
             "depth": d, "excluded": is_excluded(fmap.get(fid, {}))}
            for fid, d in rows
        ], ensure_ascii=False, indent=1))
        return
    kind = "推移的な影響範囲" if args.transitive else "直接の呼び出し元"
    print(f"{args.fid} の{kind}: {len(rows)}件")
    for fid, d in rows:
        tag = " [対象外]" if is_excluded(fmap.get(fid, {})) else ""
        print(f"{fid}\t{legacy_name(fmap.get(fid, {}))}\t{d}{tag}")


def cmd_between(data: dict, g: dict, args) -> None:
    fmap = _fmap(data)
    _check_fid(fmap, args.from_fid)
    _check_fid(fmap, args.to_fid)
    path = bfs_path(g, args.from_fid, args.to_fid)

    if args.json:
        print(json.dumps({
            "from": args.from_fid, "to": args.to_fid,
            "found": path is not None,
            "path": path,
            "length": (len(path) - 1) if path else None,
        }, ensure_ascii=False, indent=1))
        return
    if path is None:
        print(f"{args.from_fid} -> {args.to_fid}: 経路なし")
        return
    print(f"{args.from_fid} -> {args.to_fid}: {len(path) - 1}ホップ")
    chain = " -> ".join(
        f"{fid}({legacy_name(fmap.get(fid, {}))})" + ("*" if is_excluded(fmap.get(fid, {})) else "")
        for fid in path
    )
    print(chain)
    if any(is_excluded(fmap.get(fid, {})) for fid in path):
        print("* = 対象外(excluded)")


def cmd_dead(data: dict, g: dict, args) -> None:
    fmap = _fmap(data)
    entries, desc = default_entries(data, g)
    reached = reachable(g, entries)
    excluded_ids = sorted(fid for fid, f in fmap.items() if is_excluded(f))
    excluded_set = set(excluded_ids)
    dead = sorted(fid for fid in g if fid not in reached and fid not in excluded_set)

    if args.json:
        print(json.dumps({
            "entries": entries, "entries_source": desc,
            "total": len(g), "reachable": len(reached),
            "dead": [{"func_id": fid, "legacy_name": legacy_name(fmap[fid])} for fid in dead],
            "excluded": [
                {"func_id": fid, "legacy_name": legacy_name(fmap[fid]),
                 "reason": fmap[fid].get("excluded_reason", "")}
                for fid in excluded_ids
            ],
        }, ensure_ascii=False, indent=1))
        return
    print(f"エントリ: {desc}（{len(entries)}件） / 到達可能: {len(reached)}/{len(g)}件")
    print(f"dead 候補（{len(dead)}件。exclude の自動実行はしない。人が ledger exclude で判断）:")
    for fid in dead:
        print(f"  {fid}\t{legacy_name(fmap[fid])}")
    print(f"対象外・除外済み（{len(excluded_ids)}件）:")
    for fid in excluded_ids:
        print(f"  {fid}\t{legacy_name(fmap[fid])}\t{fmap[fid].get('excluded_reason', '')}")


def cmd_cycles(data: dict, g: dict, args) -> None:
    fmap = _fmap(data)
    comps = [sorted(c) for c in sccs(g) if len(c) >= 2]
    comps.sort(key=lambda c: c[0])

    if args.json:
        print(json.dumps([
            [{"func_id": fid, "legacy_name": legacy_name(fmap[fid])} for fid in c]
            for c in comps
        ], ensure_ascii=False, indent=1))
        return
    print(f"循環: {len(comps)}件")
    for c in comps:
        names = ", ".join(f"{fid}({legacy_name(fmap[fid])})" for fid in c)
        print(f"  [{names}]")


def cmd_summary(data: dict, g: dict, args) -> None:
    fmap = _fmap(data)
    entries, desc = default_entries(data, g)
    reached = reachable(g, entries)
    excluded_ids = {fid for fid, f in fmap.items() if is_excluded(f)}
    dead = [fid for fid in g if fid not in reached and fid not in excluded_ids]
    n_edges = sum(len(v) for v in g.values())
    n_cycles = sum(1 for c in sccs(g) if len(c) >= 2)

    summary = {
        "nodes": len(g),
        "edges": n_edges,
        "entries": entries,
        "entries_source": desc,
        "reachable": len(reached),
        "reachable_ratio": round(len(reached) / len(g), 4) if g else None,
        "dead": len(dead),
        "excluded": len(excluded_ids),
        "cycles": n_cycles,
        "flows": len(data.get("flows") or []),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".", help="対象プロジェクトのルート")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("reachable", help="到達可能集合（fid・legacy名・深さ）")
    s.add_argument("fids", nargs="*", help="エントリの func_id（複数可）")
    s.add_argument("--flow", help="フロー名 or flow_id をエントリ指定に使う（fid指定の代わり）")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("callers", help="逆方向（直接の呼び出し元 / --transitive で推移的な影響範囲）")
    s.add_argument("fid")
    s.add_argument("--transitive", action="store_true", help="推移的な影響範囲（全ての祖先）まで辿る")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("between", help="from→to の最短経路（BFS）")
    s.add_argument("from_fid", metavar="from")
    s.add_argument("to_fid", metavar="to")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("dead", help="エントリから到達不能な関数（exclude候補の列挙のみ。自動execludeはしない）")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("cycles", help="SCC（Tarjan・反復実装）でサイズ2以上の成分（循環）")
    s.add_argument("--json", action="store_true")

    sub.add_parser("summary", help="ノード/エッジ数・エントリ・到達率・dead件数・循環数（JSON）")

    args = ap.parse_args()
    root = Path(args.root).resolve()
    data = load_functions(root)
    g = graph_from_data(data)

    {
        "reachable": cmd_reachable,
        "callers": cmd_callers,
        "between": cmd_between,
        "dead": cmd_dead,
        "cycles": cmd_cycles,
        "summary": cmd_summary,
    }[args.cmd](data, g, args)


if __name__ == "__main__":
    main()
