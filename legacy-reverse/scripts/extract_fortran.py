#!/usr/bin/env python3
"""extract_fortran.py — ⓪の機械抽出。Fortran ソースを静的解析して functions.json を生成・更新する。

エージェントに任せていた「関数の列挙・引数・COMMON・呼び出し関係・外部ファイル」の抽出を
決定的なプログラムにしたもの。LLM は結果の意味づけ（desc・型対応・シグネチャ決定）だけを行う。

設計上の約束:
- 再実行は常にマージ。func_id は不変、既存エントリの手修正（desc 等）は保持する。
  2000関数規模でも「途中でやり直したら採番が変わった」が起きない
- 完全性チェック内蔵: 状態機械パースと単純行カウントの2系統で件数を突合し、差分を報告する
- 出力は data/functions.json（正データ）と data/extract-report.json（監査ログ:
  推定した呼び出し・未解決名・マージ差分・ソースから消えた関数）

対応: 固定形式(.f/.for/.f77/.ftn/.fpp) と自由形式(.f90/.f95/.f03/.f08)。
subroutine / function / entry を関数として抽出。interface ブロック内の宣言は定義でないので除外。
制限: 固定形式は 72 桁まで（73 桁以降はシーケンス番号とみなす）。INCLUDE 展開はしない。

使い方:
  python extract_fortran.py --root <project> [--legacy-dir legacy] [--package newpkg]
                            [--write] [--json] [--no-infer-calls]
  --write なしはドライラン（functions.json を変更せず要約のみ）
"""
import argparse
import json
import re
import sys
from pathlib import Path

FIXED_EXTS = {".f", ".for", ".f77", ".ftn", ".fpp"}
FREE_EXTS = {".f90", ".f95", ".f03", ".f08"}

# 呼び出し推定から除外する組み込み関数（よく出るもの）
INTRINSICS = frozenset("""
abs achar acos adjustl adjustr aimag aint alog alog10 allocated amax0 amax1 amin0 amin1 amod anint
asin associated atan atan2 btest ceiling char cmplx conjg cos cosh count dabs dacos dasin datan
datan2 dble dcos dcosh dexp dim dlog dlog10 dmax1 dmin1 dmod dot_product dprod dsin dsinh dsqrt
dtan dtanh epsilon exp float floor huge iabs iachar iand ibclr ibits ibset ichar idint idnint ieor
ifix index int ior ishft ishftc isign len len_trim lge lgt lle llt log log10 matmul max max0 max1
maxloc maxval merge min min0 min1 minloc minval mod modulo mvbits nint not present product real
repeat reshape scan sign sin sinh size spread sqrt sum tan tanh tiny transfer transpose trim
ubound lbound verify
""".split())

CONSTRUCT_END = {"if", "do", "select", "where", "forall", "associate", "critical",
                 "team", "enum", "type"}

RE_SUB = re.compile(
    r"^(?:(?:recursive|pure|elemental|impure|module)\s+)*subroutine\s+([a-z]\w*)\s*(?:\(([^)]*)\))?")
RE_FUNC = re.compile(
    r"^(?:(?:recursive|pure|elemental|impure|module)\s+"
    r"|(?:integer|real|doubleprecision|double\s+precision|complex|logical|character"
    r"|type\s*\([^)]*\)|class\s*\([^)]*\))(?:\s*\*\s*\w+|\s*\([^)]*\))?\s+)*"
    r"function\s+([a-z]\w*)\s*(?:\(([^)]*)\))?(?:\s*result\s*\(\s*(\w+)\s*\))?")
RE_ENTRY = re.compile(r"^entry\s+([a-z]\w*)\s*(?:\(([^)]*)\))?")
RE_END = re.compile(r"^end\b\s*(\w+)?\s*(\w+)?\s*$")
RE_CALL = re.compile(r"\bcall\s+([a-z]\w*)")
RE_USE = re.compile(r"^use\s*(?:,\s*\w+\s*::\s*)?([a-z]\w*)")
RE_TYPEDECL = re.compile(
    r"^(integer|real|doubleprecision|double\s+precision|complex|logical|character"
    r"|type\s*\(\s*\w+\s*\)|class\s*\(\s*[\w*]+\s*\))\s*(\*\s*\w+|\([^)]*\))?\s*(.*)$")
RE_OPEN_FILE = re.compile(r"\bopen\s*\(.*?\bfile\s*=\s*(?:'([^']*)'|\"([^\"]*)\"|([\w%]+))",
                          re.DOTALL | re.IGNORECASE)
RE_INQUIRE_FILE = re.compile(r"\binquire\s*\(.*?\bfile\s*=\s*(?:'([^']*)'|\"([^\"]*)\"|([\w%]+))",
                             re.DOTALL | re.IGNORECASE)


# ---------- 行の正規化（コメント除去・継続行結合） ----------

def _strip_inline_comment(line: str) -> str:
    """文字列リテラル外の ! 以降を落とす。"""
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            out.append(ch)
        elif ch == "!":
            break
        else:
            out.append(ch)
    return "".join(out)


def _blank_strings(s: str) -> str:
    """解析用に文字列リテラルの中身を空白化する（' や " の対応は保つ）。"""
    out, quote = [], None
    for ch in s:
        if quote:
            if ch == quote:
                quote = None
                out.append(ch)
            else:
                out.append(" ")
        elif ch in "'\"":
            quote = ch
            out.append(ch)
        else:
            out.append(ch)
    return "".join(out)


def physical_lines(path: Path) -> list:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def statements_fixed(lines: list) -> list:
    """固定形式 → [(開始行番号, 文)]。col1 コメント、col6 継続、73桁以降は無視。"""
    stmts = []
    for i, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        c0 = raw[0] if raw else ""
        if c0 in "cC*!dD" and not raw[:6].strip().isdigit():
            # d/D はデバッグ行。ラベル行（数字）はコード
            if c0 in "cC*!dD" and c0 not in "dD" or (c0 in "dD" and not raw[1:6].strip()):
                continue
        if raw.lstrip().startswith("!"):
            continue
        if "\t" in raw[:6]:                      # タブ形式（DEC 流儀）
            head, _, rest = raw.partition("\t")
            cont = bool(rest[:1].isdigit() and rest[0] != "0")
            code = rest[1:] if cont else rest
        elif len(raw) < 7:
            code, cont = raw[5:].strip(), False   # ラベルのみ等の短行
        else:
            cont = raw[5] not in " 0"
            code = raw[6:72]
        code = _strip_inline_comment(code).rstrip()
        if not code.strip():
            continue
        if cont and stmts:
            ln, prev = stmts[-1]
            stmts[-1] = (ln, prev + " " + code.strip())
        else:
            stmts.append((i, code.strip()))
    return stmts


def statements_free(lines: list) -> list:
    """自由形式 → [(開始行番号, 文)]。! コメント、行末 & 継続、; 分割。"""
    stmts, pending, pend_ln = [], None, 0
    for i, raw in enumerate(lines, 1):
        code = _strip_inline_comment(raw).rstrip()
        if not code.strip():
            continue
        seg = code.strip()
        if pending is not None:
            seg = seg[1:].strip() if seg.startswith("&") else seg
            seg = pending + " " + seg
            ln = pend_ln
            pending = None
        else:
            ln = i
        if seg.endswith("&"):
            pending, pend_ln = seg[:-1].rstrip(), ln
            continue
        for part in _split_depth0(seg, ";"):
            if part.strip():
                stmts.append((ln, part.strip()))
    if pending:
        stmts.append((pend_ln, pending))
    return stmts


def _split_depth0(s: str, sep: str) -> list:
    out, depth, cur, quote = [], 0, [], None
    for ch in s:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == sep and depth == 0:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    out.append("".join(cur))
    return out


# ---------- 解析（状態機械） ----------

def scan_file(rel: str, stmts: list, warnings: list) -> list:
    """1ファイル分の文リストから program unit を抽出する。"""
    units, stack = [], []
    iface_depth = 0

    def close_unit(frame, end_ln):
        if frame.get("kind") in ("subroutine", "function") and not frame["iface"]:
            frame["end"] = end_ln
            units.append(frame)
            for e in frame["entries"]:
                e["end"] = end_ln
                units.append(e)

    for ln, st in stmts:
        low = _blank_strings(st).lower().strip()

        m = RE_END.match(low)
        if m:
            kw, kw2 = m.group(1), m.group(2)
            if kw in CONSTRUCT_END or (kw == "block" and kw2 != "data"):
                continue                          # end if / end do / end type 等
            if stack:
                frame = stack.pop()
                if frame.get("kind") == "interface":
                    iface_depth = max(0, iface_depth - 1)
                else:
                    close_unit(frame, ln)
            continue

        if re.match(r"^(abstract\s+)?interface\b", low):
            iface_depth += 1
            stack.append({"kind": "interface"})
            continue
        if re.match(r"^(module|submodule)\s*(?!procedure\b)[a-z(]", low) \
                or re.match(r"^program\s+\w+", low) or re.match(r"^block\s*data\b", low):
            stack.append({"kind": "container"})
            continue

        ms, mf = RE_SUB.match(low), RE_FUNC.match(low)
        if ms or mf:
            m2 = ms or mf
            frame = {"kind": "subroutine" if ms else "function", "name": m2.group(1),
                     "args": [a.strip() for a in (m2.group(2) or "").split(",") if a.strip()],
                     "result": (mf.group(3) if mf else None),
                     "file": rel, "start": ln, "end": None,
                     "stmts": [], "entries": [], "iface": iface_depth > 0}
            stack.append(frame)
            continue

        holder = next((f for f in reversed(stack)
                       if f.get("kind") in ("subroutine", "function")), None)
        me = RE_ENTRY.match(low)
        if me and holder is not None:
            holder["entries"].append(
                {"kind": "entry", "name": me.group(1),
                 "args": [a.strip() for a in (me.group(2) or "").split(",") if a.strip()],
                 "result": None, "file": rel, "start": ln, "end": None,
                 "stmts": holder["stmts"], "entries": [], "iface": holder["iface"]})
            continue
        if holder is not None:
            holder["stmts"].append((ln, low, st))   # low=解析用(文字列空白化) / st=原文

    while stack:
        frame = stack.pop()
        if frame.get("kind") in ("subroutine", "function"):
            warnings.append(f"{rel}: {frame['name']} の end が見つからない（EOFで閉じた）")
            close_unit(frame, stmts[-1][0] if stmts else frame["start"])
    return units


def naive_count(stmts: list) -> int:
    """完全性突合用の独立カウント（unit の入れ子や end の対応は追わない素朴な数え方）。

    interface ブロック内の宣言だけは除外する（定義でないため。ここを数えると
    interface があるファイル全部が誤検知になる）。
    """
    n, iface = 0, 0
    for _, st in stmts:
        low = _blank_strings(st).lower().strip()
        if re.match(r"^(abstract\s+)?interface\b", low):
            iface += 1
            continue
        if re.match(r"^end\s*interface\b", low):
            iface = max(0, iface - 1)
            continue
        if iface or low.startswith("end"):
            continue
        if re.match(r"^(?:(?:recursive|pure|elemental|impure|module)\s+)*subroutine\s+\w+", low) \
           or RE_FUNC.match(low) or low.startswith("entry "):
            n += 1
    return n


# ---------- ユニット → functions.json エントリ ----------

def analyze_unit(u: dict) -> dict:
    """引数の型/intent・COMMON・use・呼び出し・外部ファイルを本文から拾う。"""
    decls, calls, commons, uses, files = {}, [], [], [], []
    for _, low, orig in u["stmts"]:
        md = RE_TYPEDECL.match(low)
        if md and not re.match(r"^(real|integer|complex|logical|character|type|class)\s*function\b",
                               low):
            typ = (md.group(1) + (md.group(2) or "")).replace(" ", "")
            rest = md.group(3)
            attrs, _, entities = rest.partition("::")
            if not _:
                attrs, entities = "", rest
            intent = (re.search(r"intent\s*\(\s*(in|out|inout)\s*\)", attrs) or [None, None])[1] \
                if attrs else None
            for ent in _split_depth0(entities, ","):
                name = re.match(r"\s*([a-z]\w*)", ent)
                if name:
                    decls[name.group(1)] = (typ, intent)
        if low.startswith("common"):
            rest = low[len("common"):].strip()
            blocks = re.findall(r"/\s*(\w*)\s*/([^/]*)", rest)
            if not blocks and rest:
                blocks = [("", rest)]
            for bname, members in blocks:
                mem = [re.match(r"\s*([a-z]\w*)", x).group(1)
                       for x in _split_depth0(members, ",") if re.match(r"\s*[a-z]\w*", x)]
                commons.append((bname or "(無名)", mem))
        mu = RE_USE.match(low)
        if mu:
            uses.append(mu.group(1))
        calls += RE_CALL.findall(low)
        # ファイル名は文字列リテラルの中身なので原文から拾う（low は文字列が空白化済み）
        for mo in list(RE_OPEN_FILE.finditer(orig)) + list(RE_INQUIRE_FILE.finditer(orig)):
            val = (mo.group(1) or mo.group(2) or "").strip() \
                or f"(変数: {(mo.group(3) or '').lower()})"
            if val:
                files.append(val)

    inputs, outputs = [], []
    for a in u["args"]:
        typ, intent = decls.get(a, ("", None))
        row = {"name": a.upper(), "legacy_type": typ, "new_type": "", "desc": ""}
        if intent == "out":
            outputs.append(row)
        elif intent == "inout":
            inputs.append(row)
            outputs.append(dict(row))
        else:
            inputs.append(row)
    if u["kind"] == "function":
        rname = u["result"] or u["name"]
        typ = decls.get(rname, ("", None))[0]
        outputs.append({"name": (u["result"] or u["name"]).upper(),
                        "legacy_type": typ, "new_type": "", "desc": "戻り値"})

    globals_ = [{"name": f"COMMON /{b}/", "access": "", "desc": " ".join(mem[:20])}
                for b, mem in commons]
    globals_ += [{"name": f"USE {m}", "access": "", "desc": "モジュール変数（要確認）"}
                 for m in sorted(set(uses))]
    ext = [{"path": f, "access": "", "desc": ""} for f in dict.fromkeys(files)]
    return {"inputs": inputs, "outputs": outputs, "globals": globals_,
            "external_files": ext, "call_names": sorted(set(calls))}


def infer_function_calls(units: list, analyses: dict) -> dict:
    """function 名の参照 `name(` を全ユニット本文から探し、呼び出しとして推定する。

    2000関数規模を想定し、全 function 名を1本の交替正規表現にまとめて走査する。
    """
    fnames = {u["name"] for u in units if u["kind"] == "function"} - INTRINSICS
    if not fnames:
        return {}
    pat = re.compile(r"\b(" + "|".join(re.escape(n) for n in
                                       sorted(fnames, key=len, reverse=True)) + r")\s*\(")
    inferred = {}
    for u in units:
        body = "\n".join(low for _, low, _o in u["stmts"]
                         if not RE_TYPEDECL.match(low)
                         and not low.startswith(("external", "intrinsic", "dimension",
                                                 "common", "save", "data", "parameter")))
        hits = set(pat.findall(body)) - {u["name"]}
        if hits:
            inferred[(u["file"], u["name"])] = sorted(hits)
    return inferred


# ---------- マージ ----------

def _key(file: str, name: str):
    return (file.replace("\\", "/").lower(), name.upper())


def _snake(name: str) -> str:
    return re.sub(r"\W", "_", name.lower())


def merge(existing: dict, units: list, analyses: dict, inferred: dict,
          package: str, root_name: str, report: dict) -> dict:
    data = existing or {"project": {"name": root_name, "legacy_lang": "fortran",
                                    "new_lang": "python", "package": package}}
    data.setdefault("project", {}).setdefault("package", package)
    funcs = data.setdefault("functions", [])
    by_key = {_key(f["legacy"]["file"], f["legacy"]["name"]): f for f in funcs}
    name_to_unit = {}
    for u in units:
        name_to_unit.setdefault(u["name"].upper(), u)

    nums = [int(m.group(1)) for f in funcs if (m := re.match(r"F-(\d+)", f["func_id"]))]
    next_num = max(nums) + 1 if nums else 1

    seen = set()
    for u in sorted(units, key=lambda x: (x["file"], x["start"])):
        k = _key(u["file"], u["name"])
        seen.add(k)
        an = analyses[(u["file"], u["name"])]
        call_names = sorted(set(an["call_names"])
                            | set(inferred.get((u["file"], u["name"]), [])))
        lines = f"{u['start']}-{u['end']}"
        if k in by_key:
            f = by_key[k]
            if f["legacy"].get("lines") != lines:
                report["updated_lines"].append(
                    {"func_id": f["func_id"], "old": f["legacy"].get("lines"), "new": lines})
                f["legacy"]["lines"] = lines
            f["legacy"].setdefault("kind", u["kind"])
            for field, val in (("inputs", an["inputs"]), ("outputs", an["outputs"]),
                               ("globals", an["globals"]),
                               ("external_files", an["external_files"])):
                if not f.get(field):
                    f[field] = val
            new_calls = [c for c in call_names]  # 名前のまま。後段で func_id 化
            f.setdefault("_call_names", new_calls)
        else:
            f = {"func_id": f"F-{next_num:04d}",
                 "legacy": {"file": u["file"], "name": u["name"].upper(),
                            "lines": lines, "kind": u["kind"]},
                 "new": {"module": f"src/{package}/{_snake(Path(u['file']).stem)}.py",
                         "name": _snake(u["name"]), "signature": ""},
                 "inputs": an["inputs"], "outputs": an["outputs"],
                 "globals": an["globals"], "external_files": an["external_files"],
                 "calls": [], "_call_names": call_names}
            next_num += 1
            funcs.append(f)
            by_key[k] = f
            report["added"].append(f["func_id"])

    for k, f in by_key.items():
        if k not in seen:
            report["missing_in_source"].append(
                {"func_id": f["func_id"], "legacy": f["legacy"]})

    # 呼び出し名 → func_id 解決（大文字小文字を無視）
    id_by_name = {}
    for f in funcs:
        id_by_name.setdefault(f["legacy"]["name"].upper(), f["func_id"])
    for f in funcs:
        names = f.pop("_call_names", None)
        if names is None:
            continue
        resolved, unresolved = [], []
        for n in names:
            fid = id_by_name.get(n.upper())
            if fid and fid != f["func_id"]:
                resolved.append(fid)
            elif n not in INTRINSICS:
                unresolved.append(n.upper())
        if not f.get("calls"):
            f["calls"] = resolved
        elif set(f["calls"]) != set(resolved):
            report["calls_diff"].append({"func_id": f["func_id"],
                                         "existing": f["calls"], "extracted": resolved})
        if unresolved:
            report["unresolved_calls"].append({"func_id": f["func_id"], "names": unresolved})
    return data


# ---------- エントリポイント ----------

def extract(root: str, legacy_dir: str = "legacy", package: str = None,
            write: bool = False, infer_calls: bool = True) -> dict:
    rootp = Path(root).resolve()
    src_dir = rootp / legacy_dir
    if not src_dir.is_dir():
        return {"ok": False, "error": f"{src_dir} がない"}
    existing = {}
    fj = rootp / "data" / "functions.json"
    if fj.exists():
        existing = json.loads(fj.read_text(encoding="utf-8-sig"))
    package = package or existing.get("project", {}).get("package") or "newpkg"

    units, warnings, per_file, mismatches = [], [], [], []
    for path in sorted(src_dir.rglob("*")):
        ext = path.suffix.lower()
        if ext not in FIXED_EXTS | FREE_EXTS:
            continue
        rel = str(path.relative_to(rootp)).replace("\\", "/")
        lines = physical_lines(path)
        stmts = statements_fixed(lines) if ext in FIXED_EXTS else statements_free(lines)
        file_units = scan_file(rel, stmts, warnings)
        nv = naive_count(stmts)
        per_file.append({"file": rel, "units": len(file_units), "naive_count": nv})
        if nv != len(file_units):
            mismatches.append({"file": rel, "parsed": len(file_units), "naive": nv})
        units += file_units

    analyses = {(u["file"], u["name"]): analyze_unit(u) for u in units}
    inferred = infer_function_calls(units, analyses) if infer_calls else {}

    report = {"added": [], "updated_lines": [], "missing_in_source": [],
              "calls_diff": [], "unresolved_calls": [],
              "inferred_calls": [{"file": k[0], "name": k[1].upper(), "calls": v}
                                 for k, v in sorted(inferred.items())],
              "completeness_mismatches": mismatches, "warnings": warnings,
              "files": per_file}
    data = merge(existing, units, analyses, inferred, package, rootp.name, report)

    if write:
        fj.parent.mkdir(parents=True, exist_ok=True)
        fj.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (rootp / "data" / "extract-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {"ok": True, "written": write,
            "files": len(per_file), "functions_total": len(data.get("functions", [])),
            "added": len(report["added"]), "missing_in_source": len(report["missing_in_source"]),
            "completeness_mismatches": mismatches,
            "inferred_call_sites": len(report["inferred_calls"]),
            "unresolved_call_names": len(report["unresolved_calls"]),
            "warnings": warnings,
            "report_path": str(rootp / "data" / "extract-report.json") if write else None}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--legacy-dir", default="legacy")
    ap.add_argument("--package", default=None)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-infer-calls", action="store_true")
    args = ap.parse_args()
    res = extract(args.root, args.legacy_dir, args.package,
                  write=args.write, infer_calls=not args.no_infer_calls)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        if not res["ok"]:
            sys.exit(f"error: {res['error']}")
        mode = "書き込み" if res["written"] else "ドライラン"
        print(f"[{mode}] ファイル {res['files']} / 関数 {res['functions_total']}"
              f"（新規 {res['added']}） 完全性差分 {len(res['completeness_mismatches'])}"
              f" 推定呼出 {res['inferred_call_sites']} 未解決名 {res['unresolved_call_names']}")
        for m in res["completeness_mismatches"]:
            print(f"  突合NG: {m['file']} parsed={m['parsed']} naive={m['naive']}")
        for w in res["warnings"]:
            print(f"  警告: {w}")
        if res["written"]:
            print(f"  監査ログ: {res['report_path']}")
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
