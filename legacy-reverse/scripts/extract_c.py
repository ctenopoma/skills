#!/usr/bin/env python3
"""C / C++ ソースの静的解析 → functions.json 生成/マージ（⓪の機械抽出）。

Fortran プロジェクトが依存する C/C++（数値ルーチン・I/O ラッパ等）を、
extract_fortran.py と同じスキーマ・同じマージ規約で functions.json に載せる。
再実行は常にマージ（func_id 不変・手修正保持）。

言語またぎのリンク:
  - Fortran の `CALL FOO` ↔ C の foo / foo_（アンダースコア規約）は
    merge の解決器が自動で突合する
  - 片方の抽出時点で未解決だった呼び出し名はエントリの unresolved_calls に残り、
    もう片方の抽出が走った時点で自動でリンクされる（実行順は不問）

パーサの範囲（外部依存なしの状態機械 + 正規表現）:
  - 対象拡張子: .c .h .cc .cpp .cxx .hpp .hh
  - ファイルスコープの関数定義（本体 { } を持つもの）。プロトタイプ宣言は対象外
  - namespace / extern "C" ブロックは透過（中の定義をファイルスコープ扱い）
  - クラスメソッドはクラス定義の外の `Class::method` 定義のみ拾う（kind: method）
  - K&R 旧スタイル・関数マクロ・テンプレートの特殊化は非対応
    → 完全性突合（素朴カウントとの2系統チェック）の差分として人/AIレビューに回る

使い方:
  python extract_c.py --root . --package <pkg> --write
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_fortran import merge  # noqa: E402  同一スキーマ・同一マージ規約を共有する

C_EXTS = {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"}

# 呼び出し検出から除外する標準ライブラリ・言語キーワード（未解決名の洪水を防ぐ）
C_KEYWORDS = {
    "if", "for", "while", "switch", "return", "sizeof", "do", "else", "case",
    "goto", "break", "continue", "typedef", "struct", "union", "enum", "static",
    "const", "extern", "void", "int", "char", "float", "double", "long", "short",
    "unsigned", "signed", "defined", "new", "delete", "throw", "catch", "try",
    "template", "typename", "namespace", "using", "class", "public", "private",
    "protected", "operator", "static_cast", "dynamic_cast", "const_cast",
    "reinterpret_cast", "decltype", "alignof", "noexcept", "assert",
}
C_BUILTINS = set("""
printf fprintf sprintf snprintf vprintf vfprintf vsnprintf puts putchar putc fputc fputs
scanf fscanf sscanf getchar getc fgetc fgets gets
fopen freopen fclose fflush fread fwrite fseek ftell rewind fgetpos fsetpos feof ferror
clearerr remove rename tmpfile tmpnam setbuf setvbuf perror
malloc calloc realloc free aligned_alloc
memcpy memmove memset memcmp memchr
strcpy strncpy strcat strncat strcmp strncmp strchr strrchr strstr strlen strtok
strspn strcspn strpbrk strerror strdup
atoi atol atoll atof strtol strtoul strtoll strtoull strtod strtof
abs labs llabs div ldiv rand srand qsort bsearch exit abort atexit system getenv
isalpha isdigit isalnum isspace isupper islower ispunct isprint iscntrl isxdigit
toupper tolower
sin cos tan asin acos atan atan2 sinh cosh tanh exp log log10 log2 pow sqrt cbrt
ceil floor round trunc fabs fmod fmin fmax fma hypot copysign nan isnan isinf
frexp ldexp modf erf erfc tgamma lgamma
time clock difftime mktime localtime gmtime strftime
setjmp longjmp signal raise
open close read write lseek creat unlink access dup dup2 pipe fork exec wait
""".split())


# ---------- 前処理（コメント・文字列・プリプロセッサを潰して構造だけ残す） ----------

def strip_comments(text: str) -> str:
    """コメントを同じ長さの空白に置換（文字列リテラルは保持。行番号を保つ）。"""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif c == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            j = n - 2 if j < 0 else j
            seg = text[i:j + 2]
            out.append("".join(ch if ch == "\n" else " " for ch in seg))
            i = j + 2
        elif c in "\"'":
            q, j = c, i + 1
            while j < n and text[j] != q:
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:min(j + 1, n)])
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def blank_strings(text: str) -> str:
    """文字列・文字リテラルの中身を空白に（長さ・行番号を保つ）。"""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            q, j = c, i + 1
            while j < n and text[j] != q:
                j += 2 if text[j] == "\\" else 1
            seg = text[i + 1:min(j, n)]
            out.append(q + "".join(ch if ch == "\n" else " " for ch in seg)
                       + (q if j < n else ""))
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def blank_preprocessor(text: str) -> str:
    """#include / #define 等の行を空白化（継続行 \\ も含む。行番号を保つ）。"""
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("#"):
            while i < len(lines):
                cont = lines[i].rstrip().endswith("\\")
                lines[i] = " " * len(lines[i])
                i += 1
                if not cont:
                    break
        else:
            i += 1
    return "\n".join(lines)


# ---------- 関数定義の抽出（brace 深さの状態機械） ----------

RE_TRANSPARENT = re.compile(r'(?:namespace(?:\s+[\w:]+)?|extern\s*"C\+*"?)\s*$')
RE_NAME = re.compile(r"([A-Za-z_~][\w]*(?:\s*::\s*~?[\w]+)*)\s*$")
CTRL = {"if", "for", "while", "switch", "catch", "return", "do", "sizeof"}


def _header_function(header: str):
    """深さ0の `{` 直前のヘッダから (関数名, 引数文字列, 戻り型) を取る。定義でなければ None。"""
    h = header.strip()
    if not h or "(" not in h:
        return None
    if re.search(r"=\s*$|=[^=]*\($", h):          # `int a[] = {` / `auto f = [](){`
        return None
    # 末尾の修飾（const / noexcept / override / throw() / -> T）を剥がして引数括弧を特定
    h2 = re.sub(r"(?:\s*(?:const|noexcept|override|final)\b|\s*->\s*[\w:<>,\s*&]+"
                r"|\s*throw\s*\([^)]*\))+\s*$", "", h)
    if not h2.endswith(")"):
        return None
    depth, i = 0, len(h2) - 1
    while i >= 0:                                  # 引数の開き括弧まで戻る
        if h2[i] == ")":
            depth += 1
        elif h2[i] == "(":
            depth -= 1
            if depth == 0:
                break
        i -= 1
    if i <= 0:
        return None
    params = h2[i + 1:-1]
    m = RE_NAME.search(h2[:i])
    if not m:
        return None
    name = re.sub(r"\s+", "", m.group(1))
    base = name.split("::")[-1].lstrip("~")
    if base in CTRL or base in {"operator"}:
        return None
    ret = h2[:m.start()].strip()
    ret = re.sub(r"^(?:static|inline|extern|constexpr|virtual|explicit|friend"
                 r"|__\w+)\s+", "", ret).strip()
    if re.match(r"^(?:struct|class|union|enum)\b", h2) and "::" not in name:
        return None                                # struct X { ... }（型定義）
    return name, params, ret


def scan_c_file(rel: str, code: str, warnings: list) -> tuple:
    """(関数unit一覧, ファイルスコープのグローバル変数名一覧) を返す。"""
    units, globals_ = [], []
    stack = []                                     # 各 { の透過フラグ
    stmt_start = 0
    i, n = 0, len(code)
    eff_depth = 0
    while i < n:
        c = code[i]
        if c == "{":
            header = code[stmt_start:i]
            transparent = bool(RE_TRANSPARENT.search(header.strip()))
            fn = None if transparent or eff_depth else _header_function(header)
            if fn:
                name, params, ret = fn
                j, d = i + 1, 1
                while j < n and d:
                    d += {"{": 1, "}": -1}.get(code[j], 0)
                    j += 1
                start_ln = code.count("\n", 0, stmt_start + len(header) - len(header.lstrip())) + 1
                units.append({"file": rel, "name": name, "display_name": name,
                              "kind": "method" if "::" in name else "function",
                              "start": start_ln, "end": code.count("\n", 0, j) + 1,
                              "params": params, "ret": ret,
                              "body": (i + 1, j - 1)})
                i = j
                stmt_start = i
                continue
            stack.append(transparent)
            if not transparent:
                eff_depth += 1
            stmt_start = i + 1
        elif c == "}":
            if stack and not stack.pop():
                eff_depth = max(0, eff_depth - 1)
            stmt_start = i + 1
        elif c == ";":
            if eff_depth == 0 and not stack.count(False):
                stmt = code[stmt_start:i].strip()
                globals_ += file_scope_globals(stmt)
            stmt_start = i + 1
        i += 1
    return units, sorted(set(globals_))


def file_scope_globals(stmt: str) -> list:
    """ファイルスコープの変数宣言文から変数名を拾う（プロトタイプ・typedef 等は除外）。"""
    s = stmt.strip()
    if (not s or "(" in s
            or re.match(r"^(?:typedef|using|template|struct|class|union|enum|friend)\b", s)):
        return []
    if not re.match(r"^(?:static\s+|extern\s+|const\s+|constexpr\s+|volatile\s+)*"
                    r"[A-Za-z_][\w:<>,\s*&]*\s[\w*&\s]*[A-Za-z_]\w*", s):
        return []
    names = []
    for part in re.split(r",(?![^<(\[]*[>)\]])", re.sub(r"=.*$", "", s)):
        m = re.search(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])*\s*$", part.strip())
        if m and m.group(1) not in C_KEYWORDS:
            names.append(m.group(1))
    return names


# ---------- 関数単位の解析（引数・呼び出し・グローバル・外部ファイル） ----------

def parse_params(params: str) -> list:
    """引数文字列 → [(名前, 型, 出力か)]。出力=ポインタ / 非const参照。"""
    p = params.strip()
    if not p or p == "void":
        return []
    out = []
    for part in re.split(r",(?![^<(\[]*[>)\]])", p):
        part = part.strip()
        if not part or part == "...":
            continue
        m = re.search(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])*\s*$", part)
        if not m or m.group(1) in C_KEYWORDS:      # `int` だけ等（名前なしプロトタイプ）
            continue
        name, typ = m.group(1), part[:m.start()].strip()
        writable = (("*" in typ or "*" in part[m.end():] or "[" in part[m.end():]
                     or "&" in typ) and "const" not in typ)
        out.append((name, typ or part, writable))
    return out


RE_CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
RE_FOPEN = re.compile(r'\b(fopen|freopen)\s*\(\s*"([^"]*)"\s*,\s*"([^"]*)"')
RE_STREAM = re.compile(r'\b[io]?fstream\s+\w+\s*[({]\s*"([^"]*)"')
RE_OPEN = re.compile(r'\bopen\s*\(\s*"([^"]*)"')


def analyze_c_unit(u: dict, code_str: str, code_bare: str, file_globals: list) -> dict:
    """extract_fortran.analyze_unit と同じキーの解析結果を返す。"""
    b0, b1 = u["body"]
    body = code_bare[b0:b1]                        # 構造のみ（文字列は空白）
    body_str = code_str[b0:b1]                     # 文字列リテラル入り（fopen 用）

    inputs, outputs = [], []
    for name, typ, writable in parse_params(u["params"]):
        row = {"name": name, "legacy_type": typ, "new_type": "", "desc": ""}
        inputs.append(row)
        if writable:
            outputs.append({**row, "desc": "ポインタ/参照渡し（出力の可能性。要確認）"})
    if u["ret"] and u["ret"] != "void":
        outputs.append({"name": "return", "legacy_type": u["ret"],
                        "new_type": "", "desc": "戻り値"})

    local_names = {n for n, _, _ in parse_params(u["params"])}
    for m in re.finditer(r"\b(?:int|long|short|float|double|char|unsigned|signed|auto|size_t)"
                         r"[\w\s*&<>,:]*?\b([A-Za-z_]\w*)\s*[=;,\[]", body):
        local_names.add(m.group(1))
    # `std::ifstream in("f")` のようなコンストラクタ宣言を呼び出しと誤認しない
    for m in re.finditer(r"\b(?:std\s*::\s*)?(?:[io]?fstream|[io]?stringstream)"
                         r"\s+([A-Za-z_]\w*)", body):
        local_names.add(m.group(1))

    calls = sorted({m.group(1) for m in RE_CALL.finditer(body)}
                   - C_KEYWORDS - C_BUILTINS - local_names - {u["name"].split("::")[-1]})

    globals_ = []
    for g in file_globals:
        if g in local_names or not re.search(rf"\b{re.escape(g)}\b", body):
            continue
        write = bool(re.search(rf"\b{re.escape(g)}\s*(?:\[[^\]]*\])?\s*(?:=[^=]|\+\+|--|[-+*/|&^]=)",
                               body) or re.search(rf"(?:\+\+|--)\s*{re.escape(g)}\b", body))
        globals_.append({"name": g, "access": "write" if write else "read",
                         "desc": "ファイルスコープ変数"})

    files = []
    for m in RE_FOPEN.finditer(body_str):
        files.append({"path": m.group(2),
                      "access": "write" if set(m.group(3)) & set("wa+") else "read", "desc": ""})
    for m in RE_STREAM.finditer(body_str):
        files.append({"path": m.group(1), "access": "", "desc": "fstream"})
    for m in RE_OPEN.finditer(body_str):
        files.append({"path": m.group(1), "access": "", "desc": ""})

    return {"inputs": inputs, "outputs": outputs, "globals": globals_,
            "external_files": list({f["path"]: f for f in files}.values()),
            "call_names": calls}


def naive_count(code_bare: str) -> int:
    """完全性突合の第2系統: 列0の閉じ括弧（伝統的なC/C++整形の関数末尾）を数える。

    namespace / extern "C" の閉じ括弧も列0に来るため、開き括弧の数だけ引く。
    `};`（型定義の末尾）は数えない。スタイル依存のヒューリスティックなので、
    差分が出たら completeness_mismatches 経由で人/AIが原因を確認する。
    """
    closes = len(re.findall(r"(?m)^\}\s*$", code_bare))
    wrappers = len(re.findall(r'\bnamespace\b[^;{=()]*\{|extern\s*"C[^"]*"\s*\{', code_bare))
    return closes - wrappers


# ---------- エントリポイント ----------

def extract(root: str, legacy_dir: str = "legacy", package: str = None,
            write: bool = False) -> dict:
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
    analyses = {}
    for path in sorted(src_dir.rglob("*")):
        if path.suffix.lower() not in C_EXTS:
            continue
        rel = str(path.relative_to(rootp)).replace("\\", "/")
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
        code_str = strip_comments(raw)
        code_bare = blank_preprocessor(blank_strings(code_str))
        file_units, file_globals = scan_c_file(rel, code_bare, warnings)
        for u in file_units:
            analyses[(u["file"], u["name"])] = analyze_c_unit(
                u, code_str, code_bare, file_globals)
        nv = naive_count(code_bare)
        per_file.append({"file": rel, "units": len(file_units), "naive_count": nv})
        if nv != len(file_units):
            mismatches.append({"file": rel, "parsed": len(file_units), "naive": nv,
                               "note": "素朴カウントは列0の `}`（スタイル依存）"})
        units += file_units

    report = {"added": [], "updated_lines": [], "missing_in_source": [],
              "calls_diff": [], "unresolved_calls": [], "cross_resolved": [],
              "inferred_calls": [],
              "completeness_mismatches": mismatches, "warnings": warnings,
              "files": per_file}
    data = merge(existing, units, analyses, {}, package, rootp.name, report, lang="c")

    if write:
        fj.parent.mkdir(parents=True, exist_ok=True)
        fj.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (rootp / "data" / "extract-report-c.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {"ok": True, "written": write,
            "files": len(per_file), "functions_total": len(data.get("functions", [])),
            "added": len(report["added"]), "missing_in_source": len(report["missing_in_source"]),
            "completeness_mismatches": mismatches,
            "cross_resolved": len(report["cross_resolved"]),
            "unresolved_call_names": len(report["unresolved_calls"]),
            "warnings": warnings,
            "report_path": str(rootp / "data" / "extract-report-c.json") if write else None}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--legacy-dir", default="legacy")
    ap.add_argument("--package", default=None)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    res = extract(args.root, args.legacy_dir, args.package, write=args.write)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        if not res["ok"]:
            sys.exit(f"error: {res['error']}")
        mode = "書き込み" if res["written"] else "ドライラン"
        print(f"[{mode}] C/C++ ファイル {res['files']} / 関数 {res['functions_total']}"
              f"（新規 {res['added']}） 完全性差分 {len(res['completeness_mismatches'])}"
              f" 言語またぎ解決 {res['cross_resolved']} 未解決名 {res['unresolved_call_names']}")
        for m in res["completeness_mismatches"]:
            print(f"  突合NG: {m['file']} parsed={m['parsed']} naive={m['naive']}")
        for w in res["warnings"]:
            print(f"  警告: {w}")
        if res["written"]:
            print(f"  監査ログ: {res['report_path']}")
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
