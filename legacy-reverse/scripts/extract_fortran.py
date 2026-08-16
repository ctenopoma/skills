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


def _paren_span(s: str, i: int) -> int:
    """s[i] が '(' のとき、対応する ')' の位置を返す。閉じていなければ -1。"""
    depth, quote = 0, None
    for j in range(i, len(s)):
        ch = s[j]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return j
    return -1


def _call_args(s: str, pos: int) -> list:
    """s の pos 直後にある実引数リスト `( ... )` を depth0 で分割して返す。括弧が無ければ []。"""
    i = pos
    while i < len(s) and s[i] == " ":
        i += 1
    if i >= len(s) or s[i] != "(":
        return []
    end = _paren_span(s, i)
    if end < 0:
        return []
    inner = s[i + 1:end]
    if not inner.strip():
        return []
    return _split_depth0(inner, ",")


RE_SIMPLE_VAR = re.compile(r"^[a-z]\w*$")
RE_ARRAY_REF = re.compile(r"^[a-z]\w*\s*\(")


def _arg_to_var(arg: str, func_names) -> str:
    """実引数1個 → 変数名（大文字）。変数名に還元できなければ None。

    単純な変数名はそのまま。配列要素参照 `X(I)` は `X` に落とす（配列全体と同一実体のため）。
    式・リテラル・文字列・関数呼び出しの結果は None。
    """
    a = arg.strip()
    if not a:
        return None
    if RE_SIMPLE_VAR.match(a):
        return a.upper()
    if RE_ARRAY_REF.match(a):
        i = a.index("(")
        if _paren_span(a, i) == len(a) - 1:       # 全体が name(...) の形
            base = a[:i].strip()
            if base in INTRINSICS or base in func_names:
                return None                       # 関数呼び出し＝式なので還元できない
            return base.upper()
    return None


# ---------- hazard（例外・数値特異点）スキャン ----------
# 設計は references/graph-dict-design.md「P5. 例外ポリシー機構」。
# 「0割」は一例で、検出器は kind → 走査関数のテーブル（HAZARD_DETECTORS）に足せば増やせる。
# 走査関数の契約: fn(low, ctx) -> [{"expr": 部分式, "vars": [大文字変数名], "col": 開始位置}]
#   low = 文字列リテラルを空白化して小文字化した文（analyze_unit と同じもの。
#         文字列の中身は空白なので `'A/B'` のような見せかけの除算は拾わない）
#   ctx = {"arrays": 宣言済み配列名, "funcs": function 名, "orig": 原文（expr の切り出し用）}
# 検出結果は merge で hz_id（H-<func連番>-<枝番>）を振る。ソース完全導出なので常に上書き。

SQRT_FUNCS = frozenset("sqrt dsqrt csqrt cdsqrt qsqrt".split())
LOG_FUNCS = frozenset("log alog dlog clog cdlog log10 alog10 dlog10".split())

# 実行文でない（＝式として評価されない）文の先頭語。宣言の `/` は COMMON ブロック名や
# DATA の初期値区切りで、除算ではない。FORMAT 中の `/` は改行指定
NONEXEC_HEAD = frozenset("""
common data save dimension parameter equivalence external intrinsic implicit namelist
format use include entry subroutine function program module submodule interface block
end endif enddo endwhere contains integer real doubleprecision double complex logical
character type class allocatable pointer target public private protected optional
intent sequence procedure import stop return continue go goto
""".split())

RE_IDENT = re.compile(r"[a-z_]\w*")
RE_NUMLIT = re.compile(r"\b\d+\.?\d*(?:[ed][-+]?\d+)?")
RE_NAME_PAREN = re.compile(r"\b([a-z]\w*)\s*\(")


def _is_exec_stmt(low: str) -> bool:
    """式を含み得る実行文か（宣言・FORMAT 等を除く）。行ラベル付きの文も実行文とみなす。"""
    m = re.match(r"([a-z]\w*)", low)
    return not (m and m.group(1) in NONEXEC_HEAD)


def _expr_vars(expr: str, ctx: dict) -> list:
    """部分式に含まれる変数名（大文字・出現順）。数値リテラル・演算子・既知の関数名は除く。

    `X(I)` のような参照は配列名と添字変数の両方を返す（どちらが 0/範囲外でも問題になる）。
    未知の名前で後ろに `(` が続くものは配列とみなして残す（暗黙の型宣言があるため）。
    """
    e = re.sub(r"\.\w+\.", " ", expr)          # .and. / .true. 等
    e = RE_NUMLIT.sub(" ", e)                  # 2.0d0 / 1.0e-3 の指数部を名前と誤認しない
    out = []
    for m in RE_IDENT.finditer(e):
        name = m.group(0)
        if name.startswith("_"):               # 3.0_8 のような kind 指定の残り
            continue
        nxt = e[m.end():].lstrip()[:1]
        if nxt == "(" and name not in ctx["arrays"] \
                and (name in INTRINSICS or name in ctx["funcs"]):
            continue                           # 関数呼び出し（値であって変数ではない）
        u = name.upper()
        if u not in out:
            out.append(u)
    return out


def _primary_after(s: str, i: int):
    """s[i] の直後にある1項（primary）の (開始, 終了) を返す。取れなければ None。"""
    j = i + 1
    while j < len(s) and s[j] == " ":
        j += 1
    if j >= len(s):
        return None
    if s[j] == "(":
        e = _paren_span(s, j)
        return (j, e + 1) if e > 0 else None
    m = re.match(r"[+-]?\s*[\w.]+", s[j:])
    if not m:
        return None
    end = j + m.end()
    k = end
    while k < len(s) and s[k] == " ":
        k += 1
    if k < len(s) and s[k] == "(":             # 配列参照・関数呼び出しの括弧まで含める
        e = _paren_span(s, k)
        if e > 0:
            end = e + 1
    return (j, end)


def _primary_before(s: str, i: int):
    """s[i] の直前にある1項の (開始, 終了)。expr の見た目を整えるためだけに使う。"""
    j = i - 1
    while j >= 0 and s[j] == " ":
        j -= 1
    if j < 0:
        return None
    if s[j] == ")":
        depth, k = 0, j
        while k >= 0:
            if s[k] == ")":
                depth += 1
            elif s[k] == "(":
                depth -= 1
                if depth == 0:
                    break
            k -= 1
        if k < 0:
            return None
        m = re.search(r"[a-z_]\w*$", s[:k])
        return (m.start() if m else k, j + 1)
    m = re.search(r"[\w.]+$", s[:j + 1])
    return (m.start(), j + 1) if m else None


def _expr_text(orig: str, a: int, b: int, limit: int = 80) -> str:
    """原文の該当部分式。空白を詰め、長ければ limit 字で切る。"""
    s = re.sub(r"\s+", " ", orig[a:b]).strip()
    return s if len(s) <= limit else s[:limit - 1] + "…"


def _scan_div_by_var(low: str, ctx: dict) -> list:
    """分母に変数を含む除算。`//`（文字列連結）・`/=`・`(/ /)`（配列構成子）は除く。"""
    hits, n = [], len(low)
    for i, ch in enumerate(low):
        if ch != "/":
            continue
        if i + 1 < n and low[i + 1] in "/=)":       # a//b（連結）・a/=b・(/…/)
            continue
        if i > 0 and low[i - 1] in "/(":            # //b の2本目・(/…
            continue
        span = _primary_after(low, i)
        if not span:
            continue
        vs = _expr_vars(low[span[0]:span[1]], ctx)
        if not vs:
            continue                                # 純リテラルの分母（/2.0 等）は対象外
        left = _primary_before(low, i)
        hits.append({"expr": _expr_text(ctx["orig"], left[0] if left else i, span[1]),
                     "vars": vs, "col": i})
    return hits


def _scan_intrinsic_arg(low: str, ctx: dict, names) -> list:
    """指定した組み込み関数群の引数に変数が含まれるもの（定義域の特異点）。"""
    hits = []
    for m in RE_NAME_PAREN.finditer(low):
        if m.group(1) not in names:
            continue
        e = _paren_span(low, m.end() - 1)
        if e < 0:
            continue
        vs = _expr_vars(low[m.end():e], ctx)
        if vs:
            hits.append({"expr": _expr_text(ctx["orig"], m.start(), e + 1),
                         "vars": vs, "col": m.start()})
    return hits


def _scan_sqrt_arg(low: str, ctx: dict) -> list:
    return _scan_intrinsic_arg(low, ctx, SQRT_FUNCS)


def _scan_log_arg(low: str, ctx: dict) -> list:
    return _scan_intrinsic_arg(low, ctx, LOG_FUNCS)


def _scan_array_index_var(low: str, ctx: dict) -> list:
    """宣言済み配列の添字に変数（範囲外参照の候補）。vars は添字側の変数のみ。"""
    hits = []
    for m in RE_NAME_PAREN.finditer(low):
        if m.group(1) not in ctx["arrays"]:
            continue
        e = _paren_span(low, m.end() - 1)
        if e < 0:
            continue
        vs = []
        for part in _split_depth0(low[m.end():e], ","):
            for v in _expr_vars(part, ctx):
                if v not in vs:
                    vs.append(v)
        if vs:
            hits.append({"expr": _expr_text(ctx["orig"], m.start(), e + 1),
                         "vars": vs, "col": m.start()})
    return hits


# kind → 走査関数。ここに1行足せば検出器が増える（hz_id は登録順→行順で振られる）
HAZARD_DETECTORS = {
    "div_by_var": _scan_div_by_var,
    "sqrt_arg": _scan_sqrt_arg,
    "log_arg": _scan_log_arg,
    "array_index_var": _scan_array_index_var,
}


def scan_hazards(stmts: list, arrays: set, func_names) -> list:
    """文リスト → hazard のリスト（hz_id は merge で付与）。"""
    hazards, seen = [], set()
    ctx = {"arrays": arrays, "funcs": set(func_names), "orig": ""}
    for ln, low, orig in stmts:
        if not _is_exec_stmt(low):
            continue
        ctx["orig"] = orig
        for kind, scan in HAZARD_DETECTORS.items():
            for h in sorted(scan(low, ctx), key=lambda x: x["col"]):
                key = (kind, ln, h["expr"], tuple(h["vars"]))
                if key in seen:
                    continue
                seen.add(key)
                hazards.append({"kind": kind, "line": ln,
                                "expr": h["expr"], "vars": h["vars"]})
    return hazards


def _function_names(units: list) -> set:
    """呼び出し推定の対象となる function 名の集合（組み込みは除く）。"""
    return {u["name"] for u in units if u["kind"] == "function"} - INTRINSICS


def _fname_pattern(fnames: set):
    """function 名を1本の交替正規表現にまとめる（2000関数規模でも1回の走査で済ませる）。"""
    if not fnames:
        return None
    return re.compile(r"\b(" + "|".join(re.escape(n) for n in
                                        sorted(fnames, key=len, reverse=True)) + r")\s*\(")


# ---------- 解析（状態機械） ----------

def scan_file(rel: str, stmts: list, warnings: list) -> list:
    """1ファイル分の文リストから program unit を抽出する。"""
    units, stack = [], []
    iface_depth = 0

    def close_unit(frame, end_ln):
        if frame.get("kind") in ("subroutine", "function", "program") and not frame["iface"]:
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
                or re.match(r"^block\s*data\b", low):
            stack.append({"kind": "container"})
            continue
        mp = re.match(r"^program\s+(\w+)", low)
        if mp:                                    # メインルーチン（merge で F-0000 を割り当てる）
            stack.append({"kind": "program", "name": mp.group(1), "args": [],
                          "result": None, "file": rel, "start": ln, "end": None,
                          "stmts": [], "entries": [], "iface": iface_depth > 0,
                          "is_main": True})
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
                       if f.get("kind") in ("subroutine", "function", "program")), None)
        if holder is None and not stack and low:
            # program 文の無い暗黙のメインルーチン（FORTRAN 77 で合法。END までを1ユニット）。
            # メイン判定は program 文/この検出によるもので、ファイル名（main.f 等）は見ない
            holder = {"kind": "program", "name": "main", "args": [], "result": None,
                      "file": rel, "start": ln, "end": None, "stmts": [],
                      "entries": [], "iface": False, "is_main": True, "implicit": True}
            warnings.append(f"{rel}: program 文の無い暗黙のメインルーチンを検出（{ln}行目〜）")
            stack.append(holder)
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
        if frame.get("kind") in ("subroutine", "function", "program"):
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
           or RE_FUNC.match(low) or low.startswith("entry ") \
           or re.match(r"^program\s+\w+", low):
            n += 1
    return n


# ---------- ユニット → functions.json エントリ ----------

def analyze_unit(u: dict, func_names=frozenset(), fn_pat=None) -> dict:
    """引数の型/intent・COMMON・use・呼び出し・外部ファイルを本文から拾う。

    あわせて call_sites（呼び出し1件ごとの物理行・実引数）と hazards（0割・SQRT/LOG の
    引数・変数添字などの数値特異点）を作る。fn_pat が与えられたときは
    call 文でない function 参照（`Y = FOO(X)`）も推定して `inferred: True` で記録する。
    callee の func_id 解決と hz_id の採番は merge が行う
    （この時点では他ファイルの関数も自分の func_id も知らないため）。
    """
    decls, calls, commons, uses, files = {}, [], [], [], []
    arrays = set()                      # 宣言済み配列名（array_index_var の判定に使う）
    raw_sites = []                      # (物理行, 呼び先名, 実引数の文字列, 推定フラグ)
    for ln, low, orig in u["stmts"]:
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
            dim_attr = bool(attrs and re.search(r"\bdimension\b", attrs))
            for ent in _split_depth0(entities, ","):
                name = re.match(r"\s*([a-z]\w*)", ent)
                if name:
                    decls[name.group(1)] = (typ, intent)
                    if dim_attr or re.match(r"\s*[a-z]\w*\s*\(", ent):
                        arrays.add(name.group(1))       # REAL X(10) / DIMENSION 属性つき
        if low.startswith("dimension"):
            rest = re.sub(r"^dimension\s*(::)?", "", low).strip()
            for ent in _split_depth0(rest, ","):
                ma = re.match(r"\s*([a-z]\w*)\s*\(", ent)
                if ma:
                    arrays.add(ma.group(1))
        if low.startswith("common"):
            rest = low[len("common"):].strip()
            blocks = re.findall(r"/\s*(\w*)\s*/([^/]*)", rest)
            if not blocks and rest:
                blocks = [("", rest)]
            for bname, members in blocks:
                mem = [re.match(r"\s*([a-z]\w*)", x).group(1)
                       for x in _split_depth0(members, ",") if re.match(r"\s*[a-z]\w*", x)]
                for x in _split_depth0(members, ","):
                    ma = re.match(r"\s*([a-z]\w*)\s*\(", x)     # COMMON /B/ TBL(10)
                    if ma:
                        arrays.add(ma.group(1))
                commons.append((bname or "(無名)", mem))
        mu = RE_USE.match(low)
        if mu:
            uses.append(mu.group(1))
        calls += RE_CALL.findall(low)
        for mc in RE_CALL.finditer(low):
            raw_sites.append((ln, mc.group(1), _call_args(low, mc.end()), False))
        if fn_pat is not None and not md \
                and not low.startswith(("external", "intrinsic", "dimension",
                                        "common", "save", "data", "parameter")):
            for mf in fn_pat.finditer(low):
                if mf.group(1) == u["name"] or re.search(r"\bcall\s*$", low[:mf.start()]):
                    continue              # 自身の再帰参照と call 文（上で拾い済み）は除く
                raw_sites.append((ln, mf.group(1), _call_args(low, mf.end() - 1), True))
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

    call_sites = []
    for ln, nm, args, inferred in raw_sites:
        site = {"name": nm.upper(), "line": ln,
                "args": [_arg_to_var(a, func_names) for a in args]}
        if inferred:
            site["inferred"] = True
        call_sites.append(site)
    # hazard は宣言を全部読んでから（配列名が揃ってから）走査する
    hazards = scan_hazards(u["stmts"], arrays, func_names)
    return {"inputs": inputs, "outputs": outputs, "globals": globals_,
            "external_files": ext, "call_names": sorted(set(calls)),
            "call_sites": call_sites, "hazards": hazards}


def infer_function_calls(units: list, analyses: dict) -> dict:
    """function 名の参照 `name(` を全ユニット本文から探し、呼び出しとして推定する。

    2000関数規模を想定し、全 function 名を1本の交替正規表現にまとめて走査する。
    """
    pat = _fname_pattern(_function_names(units))
    if pat is None:
        return {}
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


def _lookup_call(id_by_name: dict, name: str, self_id: str):
    """呼び出し名 → func_id。言語またぎ（Fortran↔C）のアンダースコア規約も試す。

    Fortran の `call foo` は C 側では foo または foo_（コンパイラ規約）で定義される。
    逆に C から Fortran を呼ぶときは foo_ と書くことが多い。
    """
    n = name.upper()
    for cand in (n, n.rstrip("_"), n + "_"):
        fid = id_by_name.get(cand)
        if fid and fid != self_id:
            return fid
    return None


def merge(existing: dict, units: list, analyses: dict, inferred: dict,
          package: str, root_name: str, report: dict, lang: str = "fortran") -> dict:
    data = existing or {"project": {"name": root_name, "legacy_lang": lang,
                                    "new_lang": "python", "package": package}}
    data.setdefault("project", {}).setdefault("package", package)
    if lang not in data["project"].get("legacy_lang", ""):    # 混在（fortran+c 等）を記録
        data["project"]["legacy_lang"] = f"{data['project'].get('legacy_lang', '')}+{lang}".strip("+")
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
            if f.pop("manual", None):     # 手動追加が実ソースで確認できたので通常エントリへ昇格
                report.setdefault("manual_confirmed", []).append(f["func_id"])
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
            f.setdefault("_call_sites", an["call_sites"])
            f.setdefault("_hazards", an["hazards"])
        else:
            # メインルーチン（Fortran program / C main）は予約番号 F-0000
            if u.get("is_main") and not any(x["func_id"] == "F-0000" for x in funcs):
                fid = "F-0000"
            else:
                if u.get("is_main"):
                    report["warnings"].append(
                        f"{u['file']}:{u['name']} もメイン候補だが F-0000 は使用済み"
                        f"→ 通常採番。どれが本物のメインか確認する")
                fid = f"F-{next_num:04d}"
                next_num += 1
            f = {"func_id": fid,
                 # Fortran は大文字慣習、C/C++ は原文の大小文字を保つ（display_name）
                 "legacy": {"file": u["file"],
                            "name": u.get("display_name") or u["name"].upper(),
                            "lines": lines, "kind": u["kind"]},
                 "new": {"module": f"src/{package}/{_snake(Path(u['file']).stem)}.py",
                         "name": _snake(u["name"]), "signature": ""},
                 "inputs": an["inputs"], "outputs": an["outputs"],
                 "globals": an["globals"], "external_files": an["external_files"],
                 "calls": [], "_call_names": call_names,
                 "_call_sites": an["call_sites"], "_hazards": an["hazards"]}
            funcs.append(f)
            by_key[k] = f
            report["added"].append(f["func_id"])

    for k, f in by_key.items():
        # manual（人が意図して後追い追加）はソースに無くて当然なので警告しない
        if k not in seen and not f.get("manual"):
            report["missing_in_source"].append(
                {"func_id": f["func_id"], "legacy": f["legacy"]})

    # 呼び出し名 → func_id 解決（大文字小文字を無視。Fortran↔C のアンダースコア規約も突合）
    id_by_name = {}
    for f in funcs:
        id_by_name.setdefault(f["legacy"]["name"].upper(), f["func_id"])
    for f in funcs:
        names = f.pop("_call_names", None)
        sites = f.pop("_call_sites", None)
        hazs = f.pop("_hazards", None)
        if names is None:
            # 今回の抽出対象外（別言語など）のエントリ。前回までの未解決名を
            # 今回追加された関数と突合する（Fortran→C のリンクはここで繋がる）
            pending = f.get("unresolved_calls") or []
            if pending:
                got = [(nm, _lookup_call(id_by_name, nm, f["func_id"])) for nm in pending]
                hit = sorted({fid for _, fid in got if fid})
                if hit:
                    f["calls"] = sorted(set(f.get("calls", [])) | set(hit))
                    report.setdefault("cross_resolved", []).append(
                        {"func_id": f["func_id"], "resolved": hit})
                rest = [nm for nm, fid in got if not fid]
                if rest:
                    f["unresolved_calls"] = rest
                else:
                    f.pop("unresolved_calls", None)
            continue
        resolved, unresolved = [], []
        for n in names:
            fid = _lookup_call(id_by_name, n, f["func_id"])
            if fid:
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
            f["unresolved_calls"] = sorted(set(unresolved))   # 後続の別言語抽出で自動解決
        else:
            f.pop("unresolved_calls", None)
        # call_sites はソースから完全に導出されるので常に上書き（手修正の対象外）
        if sites is not None:
            resolved_sites = []
            for s in sites:
                fid = _lookup_call(id_by_name, s["name"], f["func_id"])
                site = {"callee": fid} if fid else {}
                site.update({"name": s["name"], "line": s["line"], "args": s["args"]})
                if s.get("inferred"):
                    site["inferred"] = True
                resolved_sites.append(site)
            f["call_sites"] = resolved_sites
        # hazards も同じくソース完全導出。hz_id は H-<func連番>-<枝番>（関数内連番）
        if hazs is not None:
            num = f["func_id"].split("-", 1)[-1]
            f["hazards"] = [{"hz_id": f"H-{num}-{i:02d}", "kind": h["kind"],
                             "line": h["line"], "expr": h["expr"], "vars": h["vars"]}
                            for i, h in enumerate(hazs, 1)]
            for h in f["hazards"]:
                report.setdefault("hazard_counts", {})
                report["hazard_counts"][h["kind"]] = \
                    report["hazard_counts"].get(h["kind"], 0) + 1
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
        # 暗黙メインは素朴カウント側では数えようがない（宣言行が無い）ので突合から除外
        explicit = sum(1 for u in file_units if not u.get("implicit"))
        per_file.append({"file": rel, "units": len(file_units), "naive_count": nv})
        if nv != explicit:
            mismatches.append({"file": rel, "parsed": explicit, "naive": nv})
        units += file_units

    mains = [u for u in units if u.get("is_main")]
    if len(mains) > 1:
        warnings.append("メインルーチン候補が複数ある: "
                        + ", ".join(f"{u['file']}:{u['name']}" for u in mains)
                        + "（F-0000 は最初の1件。どれが本物のメインか⓪で確認する）")

    fnames = _function_names(units)
    fn_pat = _fname_pattern(fnames) if infer_calls else None
    analyses = {(u["file"], u["name"]): analyze_unit(u, fnames, fn_pat) for u in units}
    inferred = infer_function_calls(units, analyses) if infer_calls else {}

    report = {"added": [], "updated_lines": [], "missing_in_source": [],
              "calls_diff": [], "unresolved_calls": [], "cross_resolved": [],
              "hazard_counts": {},
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
            "hazards_total": sum(report["hazard_counts"].values()),
            "hazard_counts": report["hazard_counts"],
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
              f" 推定呼出 {res['inferred_call_sites']} 未解決名 {res['unresolved_call_names']}"
              f" hazard {res['hazards_total']}")
        if res["hazards_total"]:
            print("  hazard 内訳: "
                  + " ".join(f"{k}={v}" for k, v in sorted(res["hazard_counts"].items()))
                  + "  → `python hazards.py match --root <project>` で EP と突合する")
        for m in res["completeness_mismatches"]:
            print(f"  突合NG: {m['file']} parsed={m['parsed']} naive={m['naive']}")
        for w in res["warnings"]:
            print(f"  警告: {w}")
        if res["written"]:
            print(f"  監査ログ: {res['report_path']}")
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
