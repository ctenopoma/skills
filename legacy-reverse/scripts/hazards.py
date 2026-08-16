#!/usr/bin/env python3
"""hazards.py — 例外ポリシー機構（0割はその一例）。

⓪の機械抽出（extract_fortran.py）が functions.json に記録した hazards
（div_by_var / sqrt_arg / log_arg / array_index_var …）を、人が承認した
ポリシー登録簿 docs/exception-policy.md（EP-xxx）と突合する。

  検知（機械） → 登録簿と突合（機械） → 未決定は人に質問（キュー） → 決定を登録 → 再突合

Fortran は 0割でも Inf を作って走り続けるが、Python は ZeroDivisionError で停止する。
「未決定のまま①を書いて④へ進む」は事故になるため、①の機械レビュー（review_checks.py spec）
が未決定 hazard の仕様化をNGにする。設計は references/graph-dict-design.md「P5」。

サブコマンド:
  match [--json]        全 hazard に EP を割り当てて data/hazard-map.json を保存。
                        未決定は docs/exception-queue.md に kind ごとに集約して質問形式で出力
                        （全件決定済みなら「未決定なし」で上書き）
  add-policy --kind <k> [--func F-0012 | --hazard H-0012-01]
             --decision <語彙> [--note "..."] --by <名前>
                        docs/exception-policy.md に EP 行を追記（EP-ID 自動採番・承認者/日付記録）。
                        ファイルが無ければテンプレ（assets/templates/exception-policy.md）から生成。
                        追記後は自動で再突合する
  status [--json]       hazard 総数・kind 別・決定済み/未決定の数行サマリ

適用範囲は 全体既定 → 関数 → 個別 hazard の順に「より個別なもの」が勝つ。
同じ範囲に複数の EP があれば後から登録した（EP-ID が下の行にある）ものが勝つ。

Python ライブラリとしても使う（review_checks.py が import する）:
  parse_policy(root) -> dict          登録簿のパース結果（policies / ep_ids / problems）
  load_hazard_map(root) -> dict       data/hazard-map.json（無ければ {}）
  collect_hazards(root|data) -> list  functions.json の全 hazard を平坦化
  match_hazards(root, write=False)    突合結果
"""
import argparse
import datetime
import json
import re
import sys
from pathlib import Path

# 決定の語彙（設計書 P5）。ここに無い語は登録簿のパース時にNGとして報告する
DECISIONS = {
    "detect_only": "仕様に記載のみ（コードでは検知せず、挙動もレガシーのまま）",
    "guard_raise": "ガードして例外送出（Python の例外として仕様化する）",
    "guard_value": "代替値で継続（値を備考に明記すること）",
    "legacy_preserve": "レガシー挙動を再現（IEEE の Inf/NaN 等をそのまま流す）",
    "caller_guarantees": "呼び出し元が保証（根拠を備考に必須記載）",
}
# 適用範囲のランク（大きいほど個別＝強い）
RANK_GLOBAL, RANK_FUNC, RANK_HAZARD = 1, 2, 3
RANK_LABEL = {RANK_GLOBAL: "全体既定", RANK_FUNC: "関数", RANK_HAZARD: "個別"}

RE_EP = re.compile(r"^EP-(\d+)$")
RE_FID = re.compile(r"^F-\d+$")
RE_HZID = re.compile(r"^H-\d+-\d+$")
RE_KIND = re.compile(r"^[a-z][a-z0-9_]*$")

POLICY_REL = "docs/exception-policy.md"
QUEUE_REL = "docs/exception-queue.md"
MAP_REL = "data/hazard-map.json"
TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "templates" / "exception-policy.md"


# ---------- 読み込み ----------

def load_functions(root) -> dict:
    path = Path(root) / "data" / "functions.json"
    if not path.exists():
        sys.exit(f"error: {path} が存在しない（先に⓪の抽出を実行する）")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        sys.exit(f"error: {path} が JSON として読めない（{e.lineno}行{e.colno}列: {e.msg}）")


def collect_hazards(src) -> list:
    """functions.json（dict）またはプロジェクトルート → hazard の平坦なリスト。

    excluded（移植対象外）の関数の hazard は対象にしない（①〜⑥の対象外のため）。
    hazards キーを持たない旧データでも単に空になる（後方互換）。
    """
    data = src if isinstance(src, dict) else load_functions(src)
    out = []
    for f in data.get("functions", []):
        if f.get("excluded"):
            continue
        for h in f.get("hazards", []):
            out.append({"hz_id": h.get("hz_id", ""), "func_id": f["func_id"],
                        "func_name": f.get("legacy", {}).get("name", ""),
                        "file": f.get("legacy", {}).get("file", ""),
                        "kind": h.get("kind", ""), "line": h.get("line"),
                        "expr": h.get("expr", ""), "vars": h.get("vars", [])})
    return out


def parse_policy(root) -> dict:
    """docs/exception-policy.md の表を素朴にパースする。

    行の形: | EP-001 | div_by_var F-0012 H-0012-01 | 個別 | guard_raise | 備考 | 承認者 日付 |
    「対象」列のトークンから kind / func_id / hz_id を拾い、適用範囲のランクを決める
    （「適用範囲」列は表示用。トークンから決めるので列の表記ゆれに強い）。
    """
    path = Path(root) / POLICY_REL
    res = {"path": str(path), "exists": path.exists(), "policies": [], "problems": []}
    if not path.exists():
        return res
    text = path.read_text(encoding="utf-8-sig")
    # HTML コメント内（テンプレの記入例・注釈）は表として読まない。行番号は保つため空白化する
    text = re.sub(r"<!--.*?-->", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or not RE_EP.match(cells[0]):
            continue                                  # ヘッダ・区切り行・凡例
        if len(cells) < 4:
            res["problems"].append(f"{cells[0]}: 列が足りない（EP-ID/対象/適用範囲/決定 が必要）")
            continue
        toks = re.findall(r"[\w\-]+", cells[1])
        hz = next((t for t in toks if RE_HZID.match(t)), None)
        fid = next((t for t in toks if RE_FID.match(t)), None)
        kind = next((t for t in toks if RE_KIND.match(t)), None)
        rank = RANK_HAZARD if hz else (RANK_FUNC if fid else RANK_GLOBAL)
        decision = cells[3].strip("`* ")
        if decision not in DECISIONS:
            res["problems"].append(
                f"{cells[0]}: 決定「{decision}」は語彙外（{' / '.join(DECISIONS)}）")
        if hz and fid and not hz.startswith(f"H-{fid.split('-', 1)[-1]}-"):
            res["problems"].append(f"{cells[0]}: 対象の {fid} と {hz} が食い違っている")
        res["policies"].append(
            {"ep": cells[0], "kind": kind, "func": fid, "hazard": hz, "rank": rank,
             "scope_label": cells[2] if len(cells) > 2 else RANK_LABEL[rank],
             "decision": decision, "note": cells[4] if len(cells) > 4 else "",
             "approval": cells[5] if len(cells) > 5 else "", "line": i})
    seen = {}
    for p in res["policies"]:
        if p["ep"] in seen:
            res["problems"].append(f"{p['ep']}: EP-ID が重複している（{seen[p['ep']]}行目と{p['line']}行目）")
        seen[p["ep"]] = p["line"]
    res["ep_ids"] = sorted(seen)
    return res


def load_hazard_map(root) -> dict:
    path = Path(root) / MAP_REL
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}


# ---------- 突合 ----------

def match_hazards(root, write: bool = False) -> dict:
    """全 hazard に EP を割り当てる。write=True で hazard-map.json と質問キューを書く。"""
    hazards = collect_hazards(root)
    pol = parse_policy(root)
    usable = [(i, p) for i, p in enumerate(pol["policies"]) if p["decision"] in DECISIONS]

    matched, unmatched = [], []
    for h in hazards:
        best = None
        for i, p in usable:
            if p["kind"] and p["kind"] != h["kind"]:
                continue
            if p["hazard"] and p["hazard"] != h["hz_id"]:
                continue
            if p["func"] and p["func"] != h["func_id"]:
                continue
            if best is None or (p["rank"], i) >= (best[1]["rank"], best[0]):
                best = (i, p)                      # 同ランクなら後に登録された方が勝つ
        if best:
            h = dict(h, ep=best[1]["ep"], decision=best[1]["decision"],
                     scope=RANK_LABEL[best[1]["rank"]])
            matched.append(h)
        else:
            unmatched.append(h)

    hz_map = {}
    for h in matched:
        hz_map[h["hz_id"]] = {"ep": h["ep"], "decision": h["decision"],
                              "func_id": h["func_id"], "kind": h["kind"]}
    for h in unmatched:
        hz_map[h["hz_id"]] = {"ep": None, "decision": None,
                              "func_id": h["func_id"], "kind": h["kind"]}
    hz_map = {k: hz_map[k] for k in sorted(hz_map)}

    by_kind = {}
    for h in hazards:
        d = by_kind.setdefault(h["kind"], {"total": 0, "decided": 0})
        d["total"] += 1
        d["decided"] += 1 if hz_map.get(h["hz_id"], {}).get("ep") else 0

    res = {"ok": not unmatched and not pol["problems"],
           "total": len(hazards), "decided": len(matched), "undecided": len(unmatched),
           "by_kind": by_kind, "policies": len(pol["policies"]),
           "policy_path": pol["path"], "policy_exists": pol["exists"],
           "policy_problems": pol["problems"],
           "map": hz_map, "unmatched": unmatched}
    if write:
        rootp = Path(root)
        mp = rootp / MAP_REL
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text(json.dumps(hz_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        qp = write_queue(root, unmatched, pol)
        res["map_path"], res["queue_path"] = str(mp), str(qp)
    return res


# ---------- 質問キュー ----------

def _options_line() -> str:
    """人に見せる選択肢の1行（決定語彙＋「式ごとに個別判断」）。"""
    return " / ".join(f"`{d}`" for d in DECISIONS) + " / 式ごとに個別判断"


HYPOTHESIS = {
    "div_by_var": "分母に変数を含む除算。Fortran は 0除算でも Inf を作って継続するが、"
                  "Python は ZeroDivisionError で停止する（＝挙動が変わる）。",
    "sqrt_arg": "SQRT 系の引数に変数。負値で Fortran は NaN/実行時エラー、"
                "Python の math.sqrt は ValueError で停止する。",
    "log_arg": "LOG 系の引数に変数。0・負値で Fortran は -Inf/NaN、"
               "Python の math.log は ValueError で停止する。",
    "array_index_var": "配列の添字に変数。Fortran は境界検査なしで隣を読むことがあるが、"
                       "Python は IndexError で停止する（負の添字は末尾参照になる）。",
}


def write_queue(root, unmatched: list, pol: dict) -> Path:
    """未決定 hazard を kind ごとに集約して docs/exception-queue.md に書く。"""
    rootp = Path(root)
    out = rootp / QUEUE_REL
    lines = ["---", 'title: "例外ポリシー 未決定キュー"', "date: last-modified", "---", "",
             "<!-- hazards.py match による自動生成。手編集禁止"
             "（決定は docs/exception-policy.md に EP 行として登録する） -->", ""]
    if pol["problems"]:
        lines += ["::: {.callout-important}", "## 登録簿の記載に問題があります", ""]
        lines += [f"- {p}" for p in pol["problems"]]
        lines += ["", ":::", ""]
    if not unmatched:
        lines += ["未決定の例外ポリシーはありません 🎉", "",
                  f"登録済みポリシー: {len(pol['policies'])} 件"
                  f"（[{POLICY_REL}](exception-policy.md)）", ""]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out

    groups = {}
    for h in unmatched:
        groups.setdefault(h["kind"], []).append(h)
    lines += [f"未決定の hazard: **{len(unmatched)} 件**（{len(groups)} 種類）。"
              "kind ごとに既定を1つ決めれば同種の全箇所に一括で効きます"
              "（個別に上書きしたい箇所だけ後から追加登録できます）。", "",
              "決定の語彙:", ""]
    lines += [f"- `{k}` — {v}" for k, v in DECISIONS.items()]
    lines += [""]
    for kind, hs in sorted(groups.items()):
        lines += [f"## {kind} — {len(hs)} 箇所", "",
                  HYPOTHESIS.get(kind, "（この kind の仮説は未登録）"), "",
                  f"**既定をどうしますか**: {_options_line()}", "",
                  "登録コマンド（例）:", "", "```bash",
                  f"python hazards.py add-policy --kind {kind} "
                  f"--decision guard_raise --by <承認者> --root <project>",
                  "```", "",
                  "| hz_id | 関数 | 箇所 | 式 | 変数 |",
                  "|-------|------|------|----|------|"]
        for h in sorted(hs, key=lambda x: x["hz_id"]):
            expr = h["expr"].replace("|", "\\|")
            lines.append(f"| {h['hz_id']} | {h['func_id']} {h['func_name']} "
                         f"| {h['file']}:{h['line']} | `{expr}` | {', '.join(h['vars'])} |")
        lines.append("")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


# ---------- 登録簿への追記 ----------

def _ensure_policy_file(root) -> Path:
    path = Path(root) / POLICY_REL
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    if TEMPLATE.exists():
        path.write_text(TEMPLATE.read_text(encoding="utf-8-sig"), encoding="utf-8")
    else:                                   # テンプレが無い環境でも動くように最小の表を作る
        path.write_text("\n".join([
            "---", 'title: "例外ポリシー登録簿"', "---", "",
            "| EP-ID | 対象 | 適用範囲 | 決定 | 備考 | 承認 |",
            "|-------|------|---------|------|------|------|", ""]) + "\n", encoding="utf-8")
    return path


def add_policy(root, kind: str, decision: str, by: str, func: str = None,
               hazard: str = None, note: str = "") -> dict:
    if decision not in DECISIONS:
        return {"ok": False, "error": f"決定「{decision}」は語彙外（{' / '.join(DECISIONS)}）"}
    if not kind or not RE_KIND.match(kind):
        return {"ok": False, "error": f"kind「{kind}」が不正（小文字の識別子で指定する）"}
    if func and not RE_FID.match(func):
        return {"ok": False, "error": f"--func は F-0012 形式で指定する（{func}）"}
    if hazard and not RE_HZID.match(hazard):
        return {"ok": False, "error": f"--hazard は H-0012-01 形式で指定する（{hazard}）"}
    if not by:
        return {"ok": False, "error": "--by（承認者）は必須（誰が決めたかを残す）"}

    path = _ensure_policy_file(root)
    pol = parse_policy(root)
    nums = [int(RE_EP.match(p["ep"]).group(1)) for p in pol["policies"]]
    ep = f"EP-{(max(nums) + 1) if nums else 1:03d}"
    rank = RANK_HAZARD if hazard else (RANK_FUNC if func else RANK_GLOBAL)
    target = " ".join(x for x in (kind, func, hazard) if x)
    today = datetime.date.today().isoformat()
    # 表を壊さないよう | はエスケープ。空欄は "-"（Windows コンソールの cp932 で
    # 出力できない文字は使わない。この行はそのまま端末にも出す）
    note_cell = note.replace("|", "\\|") if note else "-"
    row = (f"| {ep} | {target} | {RANK_LABEL[rank]} | {decision} "
           f"| {note_cell} | {by} {today} |")

    lines = path.read_text(encoding="utf-8-sig").splitlines()
    last = max((i for i, l in enumerate(lines) if l.strip().startswith("|")), default=None)
    if last is None:                        # 表が無いファイル → 末尾にヘッダごと足す
        lines += ["", "| EP-ID | 対象 | 適用範囲 | 決定 | 備考 | 承認 |",
                  "|-------|------|---------|------|------|------|", row]
    else:
        lines.insert(last + 1, row)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    res = {"ok": True, "ep": ep, "row": row, "scope": RANK_LABEL[rank], "path": str(path)}
    if (Path(root) / "data" / "functions.json").exists():
        m = match_hazards(root, write=True)          # 登録したら即座に再突合
        res["rematch"] = {k: m[k] for k in ("total", "decided", "undecided")}
    return res


# ---------- status ----------

def status(root) -> dict:
    m = match_hazards(root, write=False)
    return {"ok": m["undecided"] == 0 and not m["policy_problems"],
            "total": m["total"], "decided": m["decided"], "undecided": m["undecided"],
            "by_kind": m["by_kind"], "policies": m["policies"],
            "policy_exists": m["policy_exists"], "policy_problems": m["policy_problems"]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["match", "add-policy", "status"])
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--kind")
    ap.add_argument("--func")
    ap.add_argument("--hazard")
    ap.add_argument("--decision")
    ap.add_argument("--note", default="")
    ap.add_argument("--by")
    args = ap.parse_args()

    if args.cmd == "add-policy":
        if not args.kind or not args.decision:
            sys.exit("error: --kind と --decision は必須")
        res = add_policy(args.root, args.kind, args.decision, args.by,
                         func=args.func, hazard=args.hazard, note=args.note)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=1))
        elif not res["ok"]:
            sys.exit(f"error: {res['error']}")
        else:
            print(f"{res['ep']} を登録（適用範囲: {res['scope']}）→ {res['path']}")
            print(f"  {res['row']}")
            if "rematch" in res:
                r = res["rematch"]
                print(f"  再突合: hazard {r['total']} 件中 決定済み {r['decided']} / "
                      f"未決定 {r['undecided']}")
        sys.exit(0 if res["ok"] else 1)

    if args.cmd == "match":
        res = match_hazards(args.root, write=True)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=1))
        else:
            print(f"hazard {res['total']} 件: 決定済み {res['decided']} / "
                  f"未決定 {res['undecided']}（EP {res['policies']} 件）")
            for k, v in sorted(res["by_kind"].items()):
                print(f"  {k}: {v['decided']}/{v['total']}")
            for p in res["policy_problems"]:
                print(f"  登録簿NG: {p}")
            print(f"  → {res['map_path']} / {res['queue_path']}")
            if res["undecided"]:
                print("  未決定があるので docs/exception-queue.md を人に見せて決めてもらう"
                      "（決定は hazards.py add-policy で登録）")
        sys.exit(0 if res["ok"] else 1)

    res = status(args.root)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(f"hazard 総数 {res['total']}: 決定済み {res['decided']} / 未決定 {res['undecided']}")
        for k, v in sorted(res["by_kind"].items()):
            print(f"  {k}: {v['decided']}/{v['total']}")
        print(f"ポリシー登録簿: {'あり' if res['policy_exists'] else 'なし'}"
              f"（EP {res['policies']} 件）")
        for p in res["policy_problems"]:
            print(f"  登録簿NG: {p}")
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
