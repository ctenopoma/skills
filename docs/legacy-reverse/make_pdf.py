#!/usr/bin/env python3
"""docs/legacy-reverse の各 .qmd を個別 PDF にして pdf/ へ置く。

Quarto サイト用の .qmd は quarto-typst-pdf skill(qtpdf.py)にそのまま渡せないため、
作業ディレクトリ _pdfwork/ に .md へ変換して置いてからビルドする。変換内容:
  - ```{mermaid} → ```mermaid(qtpdf が shadow qmd で再変換する契約)
  - 他ページへの .qmd リンク / ページ内アンカー → 平文化(Typst のラベル解決で落ちるため)
  - lang: ja を frontmatter に注入(図キャプションを「図」にする)

使い方(要: ネットワーク or 取得済み _pdfwork/fonts、Chromium 系ブラウザ):
  python docs/legacy-reverse/make_pdf.py
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
QTPDF = HERE.parents[1] / "quarto-typst-pdf" / "scripts" / "qtpdf.py"
WORK = HERE / "_pdfwork"
OUT = HERE / "pdf"

NAMES = {
    "index.qmd": "0_全体像.md",
    "architecture.qmd": "1_アーキテクチャと設計判断.md",
    "pipeline.qmd": "2_パイプライン仕様.md",
    "data.qmd": "3_データ仕様.md",
    "quality.qmd": "4_品質ゲート仕様.md",
    "mcp-server.qmd": "5_MCPサーバ仕様.md",
    "operations.qmd": "6_運用設計.md",
}


def convert() -> None:
    WORK.mkdir(exist_ok=True)
    for src_name, dst_name in NAMES.items():
        text = (HERE / src_name).read_text(encoding="utf-8")
        text = text.replace("```{mermaid}", "```mermaid")
        text = re.sub(r"\[([^\]]+)\]\([\w-]+\.qmd[^)]*\)", r"\1", text)
        text = re.sub(r"\[([^\]]+)\]\(#[^)]+\)", r"\1", text)
        text = text.replace("---\ntitle:", "---\nlang: ja\ntitle:", 1)
        (WORK / dst_name).write_text(text, encoding="utf-8")


def run(*args: str) -> None:
    subprocess.run([sys.executable, str(QTPDF), *args], cwd=WORK, check=True)


def main() -> None:
    convert()
    if not (WORK / "fonts").exists():
        run("fonts", "fonts")
    if not (WORK / "_quarto.yml").exists():
        run("init")
    run("build", ".", "--design", "spec-sheet", "--style", "jis-jp")
    OUT.mkdir(exist_ok=True)
    for pdf in (WORK / "_pdf").glob("*.pdf"):
        shutil.copy2(pdf, OUT / pdf.name)
        print(f"pdf/{pdf.name}")


if __name__ == "__main__":
    main()
