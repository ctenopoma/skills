#!/usr/bin/env python3
"""docs/ を HTML サイト（WBSがトップ）にレンダリングする。Mermaid 図つき。

`quarto render docs` を直接叩かずにこのスクリプトを通すのは、Quarto が
「Mermaid は .qmd でしか描けない」制約を持つため（Quarto 1.10 で実機確認済み）:

  - `.md` に ```{mermaid} → *サイト全体の render が失敗する*
    （"You must use the .qmd extension for documents with executable code."）
  - `.md` に ```mermaid（GitHub流）→ render は通るが <pre class="mermaid"> が出るだけで
    mermaid.js が読み込まれず、図にならずソースが素のまま表示される
  - `.qmd` に ```{mermaid} → 図になる

成果物は人が読む・他ツールが扱う都合で `.md` のままにしておきたいので、
レンダリング直前に docs/_sitework/ へ `.qmd` の影コピーを作り、そこで render する
（qtpdf.py が PDF でやっている shadow と同じ考え方）。出力先は従来どおり docs/_site/。

影コピーで行う変換:
  - ```mermaid → ```{mermaid}
  - 相対リンクの .md → .qmd（Quarto がサイト内リンクを .html に張り替えられるように）
  - _quarto.yml の render グロブ・navbar href の .md → .qmd、output-dir を ../_site へ

使い方:
  python render_site.py --root .            # → docs/_site/index.html
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

MERMAID_FENCE = re.compile(r"^(\s*)```mermaid\s*$", re.MULTILINE)
MD_LINK = re.compile(r"\]\((?!https?://)([^)\s]+?)\.md(#[^)]*)?\)")
DOC_SUFFIXES = {".md", ".qmd"}


def find_quarto() -> str:
    q = shutil.which("quarto")
    if q:
        return q
    for cand in (Path.home() / ".local/quarto/bin/quarto.exe",
                 Path.home() / ".local/quarto/bin/quarto"):
        if cand.exists():
            return str(cand)
    sys.exit("error: quarto が見つからない（quarto-typst-pdf skill の qtpdf.py install で導入）")


def transform_doc(text: str) -> str:
    text = MERMAID_FENCE.sub(r"\1```{mermaid}", text)
    return MD_LINK.sub(lambda m: f"]({m.group(1)}.qmd{m.group(2) or ''})", text)


def transform_yml(text: str) -> str:
    text = re.sub(r"\.md\b", ".qmd", text)          # render グロブと navbar href
    if re.search(r"(?m)^\s*output-dir:", text):
        return re.sub(r"(?m)^(\s*)output-dir:.*$", r"\1output-dir: ../_site", text)
    return re.sub(r"(?m)^(\s*)type: website\s*$", r"\1type: website\n\1output-dir: ../_site", text)


def build_shadow(docs: Path, work: Path) -> int:
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    n = 0
    for src in sorted(docs.rglob("*")):
        rel = src.relative_to(docs)
        if any(part.startswith(("_", ".")) for part in rel.parts):
            continue                                 # _site / _sitework / _pdfwork / .quarto
        dst = work / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        elif src.suffix in DOC_SUFFIXES:
            dst.parent.mkdir(parents=True, exist_ok=True)
            body = transform_doc(src.read_text(encoding="utf-8-sig"))
            dst.with_suffix(".qmd").write_text(body, encoding="utf-8", newline="\n")
            n += 1
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)                   # 画像などのリソース
    css = docs / "wbs.css"
    if not css.exists():                             # ⓪より前に作られたプロジェクト救済
        tmpl = Path(__file__).resolve().parent.parent / "assets" / "templates" / "wbs.css"
        if tmpl.exists():
            shutil.copy2(tmpl, work / "wbs.css")
            print(f"note: docs/wbs.css が無いのでテンプレを使った（`cp {tmpl} {css}` で常設できる）")
    yml = docs / "_quarto.yml"
    if not yml.exists():
        sys.exit(f"error: {yml} がない（assets/templates/_quarto.yml を docs/ に配置する）")
    (work / "_quarto.yml").write_text(
        transform_yml(yml.read_text(encoding="utf-8-sig")), encoding="utf-8", newline="\n")
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="対象プロジェクトのルート")
    ap.add_argument("--keep-work", action="store_true", help="影コピー(_sitework)を残す（調査用）")
    args = ap.parse_args()

    docs = Path(args.root).resolve() / "docs"
    if not docs.is_dir():
        sys.exit(f"error: {docs} がない")
    work = docs / "_sitework"
    n = build_shadow(docs, work)

    r = subprocess.run([find_quarto(), "render", str(work)])
    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    if r.returncode != 0:
        sys.exit(f"error: quarto render 失敗（exit={r.returncode}）")
    print(f"wrote {docs / '_site'}（{n} ページ）")


if __name__ == "__main__":
    main()
