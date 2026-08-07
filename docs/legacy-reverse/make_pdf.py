#!/usr/bin/env python3
"""docs/legacy-reverse の .qmd から設計・仕様書の PDF を作って pdf/ へ置く。

既定は**全8章の合本1冊**(表紙・目次・通し番号つき)。章ごとの個別 PDF が要るときだけ
`--chapters` を付ける。

Quarto サイト用の .qmd は quarto-typst-pdf skill(qtpdf.py)にそのまま渡せないため、
作業ディレクトリ _pdfwork/ に .md へ変換して置いてからビルドする。変換内容:
  - ```{mermaid} → ```mermaid(qtpdf が shadow qmd で再変換する契約)
  - 他ページへの .qmd リンク / ページ内アンカー → 平文化(Typst のラベル解決で落ちるため)
  - lang: ja を frontmatter に注入(図キャプションを「図」にする)

使い方(要: ネットワーク or 取得済み _pdfwork/fonts、Chromium 系ブラウザ):
  python docs/legacy-reverse/make_pdf.py              # 合本 1 冊
  python docs/legacy-reverse/make_pdf.py --chapters   # 章別 8 冊
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
QTPDF = HERE.parents[1] / "quarto-typst-pdf" / "scripts" / "qtpdf.py"
WORK = HERE / "_pdfwork"
OUT = HERE / "pdf"

BOOK_NAME = "legacy-reverse設計・仕様書"
BOOK_TITLE = "legacy-reverse 設計・仕様書"
BOOK_AUTHOR = "legacy-reverse project"

NAMES = {
    "index.qmd": "0_全体像.md",
    "architecture.qmd": "1_アーキテクチャと設計判断.md",
    "pipeline.qmd": "2_パイプライン仕様.md",
    "data.qmd": "3_データ仕様.md",
    "quality.qmd": "4_品質ゲート仕様.md",
    "screens.qmd": "5_画面設計.md",
    "mcp-server.qmd": "6_MCPサーバ仕様.md",
    "operations.qmd": "7_運用設計.md",
}


def demote_headings(text: str) -> str:
    """見出しを 1 段下げる(# → ##)。合本のときだけ使う。

    Quarto の book では frontmatter の title が「章」になる。各ファイルの `#` を
    そのままにすると、章の中の節が章と同列に採番され、目次が
    「1 全体像 / 2 何を解決するシステムか / …」と平坦になってしまう。
    コードフェンス内の `#`(コメント・YAML)は触らない。
    """
    out, in_fence = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence and re.match(r"^#{1,5} ", line):
            line = "#" + line
        out.append(line)
    return "\n".join(out) + "\n"


def strip_subtitle(text: str) -> str:
    """章ごとの subtitle を落とす(合本では表紙・章見出しに二重で出るため)。"""
    return re.sub(r"(?m)^subtitle:.*\n", "", text, count=1)


def convert(for_book: bool = False) -> None:
    WORK.mkdir(exist_ok=True)
    # 章の追加・改番で使われなくなった .md / .pdf を消す。残すと古い番号の PDF が
    # そのままビルドされ、pdf/ に新旧が並ぶ（例: 5_MCPサーバ仕様 と 6_MCPサーバ仕様）
    keep = set(NAMES.values()) | {f"{BOOK_NAME}.md"}   # 合本の出力は消さない
    for old in list(WORK.glob("*.md")) + list((WORK / "_pdf").glob("*.pdf")):
        if old.suffix == ".md" and old.name in keep:
            continue
        if old.suffix == ".pdf" and old.with_suffix(".md").name in keep:
            continue
        old.unlink()
        print(f"stale を削除: {old.name}")
    for src_name, dst_name in NAMES.items():
        text = (HERE / src_name).read_text(encoding="utf-8")
        text = text.replace("```{mermaid}", "```mermaid")
        text = re.sub(r"\[([^\]]+)\]\([\w-]+\.qmd[^)]*\)", r"\1", text)
        text = re.sub(r"\[([^\]]+)\]\(#[^)]+\)", r"\1", text)
        text = text.replace("---\ntitle:", "---\nlang: ja\ntitle:", 1)
        if for_book:
            text = strip_subtitle(text)
            text = demote_headings(text)
        (WORK / dst_name).write_text(text, encoding="utf-8")


def run(*args: str) -> None:
    subprocess.run([sys.executable, str(QTPDF), *args], cwd=WORK, check=True)


def build_book() -> None:
    """全 8 章を通し番号・目次つきの 1 冊にまとめる(既定の成果物)。"""
    run("book", ".", "--title", BOOK_TITLE, "--author", BOOK_AUTHOR,
        "--output", BOOK_NAME, "--design", "handbook", "--lang", "ja")
    made = WORK / "_pdf" / f"{BOOK_NAME}.pdf"
    if not made.exists():
        sys.exit(f"error: {made} が生成されなかった")
    OUT.mkdir(exist_ok=True)
    shutil.copy2(made, OUT / made.name)
    print(f"pdf/{made.name}  ({made.stat().st_size / 1024 / 1024:.1f} MB)")


def build_chapters() -> None:
    """章ごとの個別 PDF(--chapters 指定時のみ)。特定の章だけ配りたいとき用。"""
    run("build", ".", "--design", "spec-sheet", "--style", "jis-jp")
    OUT.mkdir(exist_ok=True)
    for pdf in sorted((WORK / "_pdf").glob("*.pdf")):
        if pdf.stem == BOOK_NAME:
            continue
        shutil.copy2(pdf, OUT / pdf.name)
        print(f"pdf/{pdf.name}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chapters", action="store_true",
                    help="合本ではなく章ごとの個別 PDF を出す")
    args = ap.parse_args()

    convert(for_book=not args.chapters)
    if not (WORK / "fonts").exists():
        run("fonts", "fonts")
    if not (WORK / "_quarto.yml").exists():
        run("init")
    build_chapters() if args.chapters else build_book()


if __name__ == "__main__":
    main()
