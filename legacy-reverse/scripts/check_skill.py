#!/usr/bin/env python3
"""check_skill.py — skill 自身の整合性チェック（skill の「機械レビュー」）。

対象プロジェクトの成果物を見る review_checks.py と対になるもので、**この skill の
ドキュメントとスクリプトが食い違っていないか**を LLM を使わずに検証する。
改修でスクリプトを消した・サブコマンドを変えたのに文書が古いまま、という
ドリフトを人のレビュー前に機械で落とすための関門。

検査するもの（意味は見ない。機械で確実に判定できるものだけ）:

  missing-path    文書が参照するファイル（scripts/*.py・references/*.md・
                  Markdown リンク先）が実在しない ＝ 削除・改名の取りこぼし
  unknown-command 文書のコマンド例のサブコマンドが、そのスクリプトの
                  argparse に存在しない
  unknown-option  同じくオプション（--xxx）がスクリプトに存在しない
  frontmatter     SKILL.md の frontmatter の欠落・name とディレクトリ名の不一致

使い方:
  python check_skill.py                 # skill 一式（スクリプトの親ディレクトリ）を検査
  python check_skill.py --json          # 機械可読（エージェントの自己修正ループ用）
  python check_skill.py --root <path>   # 別の skill ディレクトリを検査

exit 0 = 問題なし / 1 = 問題あり。**NG が残る状態で「直しました」と報告しないこと。**
直し方は各指摘の `hint` に入っている（詳細は ARCHITECTURE.md「skill 自体の保守」）。
"""
import argparse
import json
import re
import sys
from pathlib import Path

# --- 検査対象 ---------------------------------------------------------------
# 手書きの文書だけを見る。生成物（MANUAL.html・_site 等）は元の .md を直せば
# 追随するので対象外にする（二重に指摘しない）。
DOC_GLOBS = ("*.md", "references/*.md", "skills/*/SKILL.md",
             "assets/templates/*.md", "slides/*.html")
SKIP_PARTS = {"_site", "_sitework", "_pdfwork", "_manualwork", ".quarto",
              "__pycache__", "examples", "selftest"}
SIBLING_DOCS = ("../docs/legacy-reverse/*.qmd",)   # 設計・仕様書サイト（あれば）

# 対象プロジェクト側のパス。skill の中には存在しないので実在チェックしない
PROJECT_PREFIXES = ("docs/", "data/", "src/", "tests/", "legacy/", "pdf/",
                    ".legacy-reverse/", "docs-sphinx/", "dist/", ".claude/",
                    "docs\\", "data\\")
# skill の外にある既知のファイル（対象プロジェクト・他 skill・リポジトリ直下）
EXTERNAL_FILES = {"bench.py", "conftest.py", "qtpdf.py", "server.py", "conf.py",
                  "setup.py", "make_pdf.py", "make_manual.py", "ledger_old.py",
                  "sphinx-conf.py"}

# 文書内のコマンド例を拾うための別名（`ledger` = python <LR>/scripts/ledger.py）
ALIASES = {"ledger": "ledger.py"}

PATH_TOKEN = re.compile(
    r"(?<![\w./\\-])((?:scripts|hooks|references|assets|skills|slides)/[\w./-]+\.\w+)")
PY_TOKEN = re.compile(r"(?<![\w./\\-])([\w-]+\.py)\b")
MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+?)(?:#[^)]*)?\)")
ADD_PARSER = re.compile(r"add_parser\(\s*[\"']([\w-]+)[\"']")
OPT_LITERAL = re.compile(r"[\"'](--[\w-]+)[\"']")
FRONT_KEY = re.compile(r"^([\w-]+):\s*(.*)$")

HINTS = {
    "missing-path": "参照先が消えている。正しいパスに直すか、記述ごと削除する"
                    "（改名なら文書側を新しい名前に合わせる）",
    "unknown-command": "そのサブコマンドは実在しない。スクリプトの argparse を正として"
                       "文書を直す（機能自体が要るならスクリプト側に実装する）",
    "unknown-option": "そのオプションは実在しない。スクリプトの argparse を正として"
                      "文書を直す（削除済みオプションの記述が残っていないか確認）",
    "frontmatter": "SKILL.md の frontmatter を直す（name はディレクトリ名と一致、"
                   "description は起動条件が分かる1文、user-invocable: true）",
}


def _rel(root: Path, p: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


def doc_files(root: Path) -> list:
    out = []
    for pat in DOC_GLOBS:
        out += [p for p in root.glob(pat) if p.is_file()]
    for pat in SIBLING_DOCS:
        out += [p for p in root.glob(pat) if p.is_file()]
    return sorted({p.resolve() for p in out
                   if not (SKIP_PARTS & set(p.parts))})


def script_specs(root: Path) -> dict:
    """{スクリプト名: {"path": Path, "subs": set, "opts": set}}。

    argparse を実行せず**ソースの静的走査**で拾う（import の副作用を持ち込まない）。
    """
    specs = {}
    for d in ("scripts", "hooks"):
        for p in sorted((root / d).glob("*.py")):
            if SKIP_PARTS & set(p.parts):
                continue
            src = p.read_text(encoding="utf-8-sig", errors="replace")
            specs[p.name] = {"path": p,
                             "subs": set(ADD_PARSER.findall(src)),
                             "opts": set(OPT_LITERAL.findall(src))}
    return specs


# --- (1) パス参照の実在 -----------------------------------------------------

def check_paths(root: Path, rel: str, lineno: int, line: str,
                base: Path, specs: dict, problems: list) -> None:
    def report(target: str) -> None:
        problems.append({"file": rel, "line": lineno, "kind": "missing-path",
                         "message": f"参照先が存在しない: {target}",
                         "hint": HINTS["missing-path"]})

    for m in PATH_TOKEN.finditer(line):
        target = m.group(1)
        if target.startswith(PROJECT_PREFIXES):
            continue
        # slides/index.html の assets/… のように、書いた文書からの相対も許す
        if not ((root / target).exists() or (base / target).exists()):
            report(target)

    for m in PY_TOKEN.finditer(line):
        name = m.group(1)
        if name in EXTERNAL_FILES or name in specs:
            continue
        if any(f"{d}/{name}" in line for d in ("scripts", "hooks")):
            continue                       # パス付き参照は上のループが見ている
        report(name)

    if rel.startswith("assets/templates/"):
        return          # 成果物テンプレのリンクは対象プロジェクトの docs/ で解決する
    for m in MD_LINK.finditer(line):
        target = m.group(1)
        if re.match(r"^(https?:|mailto:|#|/)", target) or "{{" in target:
            continue
        if target.startswith(PROJECT_PREFIXES):
            continue
        if not (base / target).exists():
            report(target)


# --- (2) コマンド例のサブコマンド・オプション -------------------------------

# コマンド例とみなす文脈（散文中の言及を拾わないための絞り込み）:
#   行頭（インデント・プロンプト記号のみ先行）／`python …` に続く／
#   バッククォート直後（`scripts/ledger.py …` のようなパス付きも含む）
CMD_CONTEXT = re.compile(r"(?:^[\s$>]*|python3?\s+\S*|`[\w./<>-]*)$")


def _segments(line: str, specs: dict) -> list:
    """1行から「スクリプト名 + それ以降」のコマンド片を取り出す。

    散文（「判定ロジックは ledger.py の status_of() に一元化」等）を誤ってコマンドと
    見なさないよう、**コマンドらしい文脈にある言及だけ**を対象にする。
    """
    out = []
    for name in list(specs) + list(ALIASES):
        # パス付きの起動（<LR>/scripts/ledger.py …）も拾うため、区切りの / \ は許す
        for m in re.finditer(rf"(?<![\w.-]){re.escape(name)}(?![\w.-])", line):
            script = ALIASES.get(name, name)
            if script not in specs:
                continue
            if not CMD_CONTEXT.search(line[:m.start()]):
                continue
            out.append((script, line[m.end():]))
    return out


def _tokens(rest: str) -> list:
    rest = rest.split("#")[0].split("|")[0].split("&&")[0]
    # コマンド例は ASCII。非 ASCII（日本語の地の文）が出たらそこから先は説明文なので切る
    cut = next((i for i, ch in enumerate(rest) if ord(ch) > 0x7F), len(rest))
    toks = []
    for raw in rest[:cut].replace("`", " ").replace("\\", " ").split():
        tok = raw.strip("\"'()、。,，;:）（")
        if not tok or "<" in tok or "{" in tok or "$" in tok:
            continue
        toks.append(tok)
    return toks


def check_command(rel: str, lineno: int, line: str, specs: dict,
                  problems: list) -> None:
    for script, rest in _segments(line, specs):
        spec = specs[script]
        toks = _tokens(rest)
        words = [t for t in toks if re.fullmatch(r"[a-z][\w-]*", t)
                 and t not in ("python", "python3")]
        # `--` 単体・表の区切り（---）・mermaid の矢印（-->）は除く
        opts = [t for t in toks if re.fullmatch(r"--[a-z][\w-]*", t)]

        # サブコマンド: 持つスクリプトだけ検査する。値と紛れるので
        # 「どれか1つが実在すれば OK」の緩い判定にして誤検知を避ける
        if spec["subs"] and words and not (set(words) & spec["subs"]):
            problems.append({
                "file": rel, "line": lineno, "kind": "unknown-command",
                "message": f"{script} に無いサブコマンド: {words[0]}"
                           f"（実在: {', '.join(sorted(spec['subs']))}）",
                "hint": HINTS["unknown-command"]})
        for opt in opts:
            if opt not in spec["opts"]:
                problems.append({
                    "file": rel, "line": lineno, "kind": "unknown-option",
                    "message": f"{script} に無いオプション: {opt}",
                    "hint": HINTS["unknown-option"]})


# --- (3) SKILL.md の frontmatter --------------------------------------------

def check_frontmatter(root: Path, problems: list) -> None:
    targets = [(root / "SKILL.md", "legacy-reverse")]
    targets += [(p, p.parent.name) for p in sorted(root.glob("skills/*/SKILL.md"))]
    for path, want in targets:
        rel = _rel(root, path)
        if not path.exists():
            problems.append({"file": rel, "line": 0, "kind": "frontmatter",
                             "message": "SKILL.md が無い", "hint": HINTS["frontmatter"]})
            continue
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        if not lines or lines[0].strip() != "---":
            problems.append({"file": rel, "line": 1, "kind": "frontmatter",
                             "message": "frontmatter（--- で囲む）が無い",
                             "hint": HINTS["frontmatter"]})
            continue
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        fm = {}
        for raw in lines[1:end or 1]:
            m = FRONT_KEY.match(raw)
            if m:
                fm[m.group(1)] = m.group(2).strip()
        for key in ("name", "description", "user-invocable"):
            if not fm.get(key):
                problems.append({"file": rel, "line": 2, "kind": "frontmatter",
                                 "message": f"frontmatter に {key} が無い",
                                 "hint": HINTS["frontmatter"]})
        if fm.get("name") and fm["name"] != want:
            problems.append({
                "file": rel, "line": 2, "kind": "frontmatter",
                "message": f"name がディレクトリ名と違う: {fm['name']} ≠ {want}",
                "hint": HINTS["frontmatter"]})


# --- 実行 -------------------------------------------------------------------

def check(root: Path) -> dict:
    root = root.resolve()
    specs = script_specs(root)
    problems: list = []
    files = doc_files(root)
    for path in files:
        rel = _rel(root, path)
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            check_paths(root, rel, i, line, path.parent, specs, problems)
            check_command(rel, i, line, specs, problems)
    check_frontmatter(root, problems)
    problems.sort(key=lambda p: (p["file"], p["line"]))
    return {"ok": not problems, "root": str(root),
            "scanned": {"docs": len(files), "scripts": len(specs)},
            "problems": problems}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None,
                    help="skill のルート（既定: このスクリプトの親ディレクトリ）")
    ap.add_argument("--json", action="store_true", help="機械可読出力")
    args = ap.parse_args()
    root = Path(args.root) if args.root else Path(__file__).resolve().parent.parent
    res = check(root)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        n = res["scanned"]
        print(f"check_skill: 文書 {n['docs']} 件 / スクリプト {n['scripts']} 件を検査")
        if res["ok"]:
            print("[OK] 文書とスクリプトの不整合なし")
        else:
            print(f"[NG] {len(res['problems'])} 件")
            cur = None
            for p in res["problems"]:
                if p["file"] != cur:
                    cur = p["file"]
                    print(f"\n  {cur}")
                print(f"    {p['line']}: [{p['kind']}] {p['message']}")
                print(f"      → {p['hint']}")
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
