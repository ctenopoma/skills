#!/usr/bin/env python3
"""legacy-reverse/MANUAL.md から MANUAL.html（自己完結）と MANUAL.pdf を作る。

出力は2つとも `legacy-reverse/` 直下に置き、そのまま配れるようにする:

  - **MANUAL.html** — 画像・CSS・JS をすべて埋め込んだ**単一ファイル**。
    受け取った人はダブルクリックで開くだけでよい（サーバ・ネットワーク不要）。
    左に目次を出し、スクリーンショットは枠付きで表示する。
  - **MANUAL.pdf** — quarto-typst-pdf skill（qtpdf.py）の engineering-note デザイン。

ビルドは作業ディレクトリ `_manualwork/` の中だけで行う（`legacy-reverse/` に
Quarto の設定ファイルや中間生成物を残さないため）。フォントは設計書PDF用に
取得済みの `_pdfwork/fonts` があれば使い回す。

使い方:
    python docs/legacy-reverse/make_manual.py            # HTML と PDF の両方
    python docs/legacy-reverse/make_manual.py --html     # HTML だけ（速い）
    python docs/legacy-reverse/make_manual.py --pdf      # PDF だけ
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SKILL = REPO / "legacy-reverse"
QTPDF = REPO / "quarto-typst-pdf" / "scripts" / "qtpdf.py"
WORK = HERE / "_manualwork"
PDFWORK = HERE / "_pdfwork"

SRC = SKILL / "MANUAL.md"
OUT_HTML = SKILL / "MANUAL.html"
OUT_PDF = SKILL / "MANUAL.pdf"

# 単一ファイル配布なので外部CSSは使えない。include-in-header で流し込む。
# 目的は3つだけ: 本文を読みやすい幅に、スクリーンショットを枠付きに、
# 表を詰めて横に伸ばさない（トラブルシューティング表が長いため）。
HEADER_CSS = """\
<style>
  body { line-height: 1.75; }
  main { font-size: 1.02rem; }
  h1 { border-bottom: 2px solid #dee2e6; padding-bottom: .3em; margin-top: 2.2em; }
  h2 { margin-top: 1.8em; }
  main img {
    max-width: 100%; border: 1px solid #ced4da; border-radius: 6px;
    box-shadow: 0 1px 6px rgba(0,0,0,.08); margin: .6em 0;
  }
  figcaption { font-size: .87rem; color: #6c757d; margin-top: .4em; }
  main table { font-size: .93rem; }
  main table th { background: #f1f3f5; white-space: nowrap; }
  main table td { vertical-align: top; }
  blockquote {
    border-left: 4px solid #4f46e5; background: #f8f9fb;
    padding: .6em 1em; margin: 1em 0; color: #343a40;
  }
  code { font-size: .92em; }
  #TOC { font-size: .9rem; }
  @media print { main img { box-shadow: none; } }
</style>
"""


# HTML の書式設定。CLI の -M で渡すと日本語（目次）が Windows のコードページで
# 壊れるため、UTF-8 の metadata ファイルにして渡す
HTML_META = """\
embed-resources: true
standalone: true
toc: true
toc-depth: 3
toc-location: left
toc-title: "目次"
toc-expand: 2
theme: cosmo
anchor-sections: true
code-copy: true
smooth-scroll: true
link-external-newwindow: true
fig-cap-location: bottom
include-in-header: "_header.html"
"""


def prepare(name: str) -> Path:
    """作業ディレクトリ（html / pdf）に MANUAL.md と画像を複製し、そのディレクトリを返す。

    HTML と PDF でディレクトリを分けるのは、PDF 側が置く `_quarto.yml`（Typst 用）を
    HTML のレンダリングが拾ってしまわないようにするため。
    """
    d = WORK / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    shutil.copytree(SKILL / "assets" / "manual", d / "assets" / "manual")
    (d / "MANUAL.md").write_text(SRC.read_text(encoding="utf-8"), encoding="utf-8")
    return d


def build_html() -> None:
    d = prepare("html")
    quarto = shutil.which("quarto") or str(Path.home() / ".local/quarto/bin/quarto.exe")
    (d / "_header.html").write_text(HEADER_CSS, encoding="utf-8")
    (d / "_html.yml").write_text(HTML_META, encoding="utf-8")
    subprocess.run([quarto, "render", "MANUAL.md", "--to", "html",
                    "--metadata-file", "_html.yml"], cwd=d, check=True)
    produced = d / "MANUAL.html"
    if not produced.exists():
        sys.exit(f"error: {produced} が生成されなかった")
    shutil.copy2(produced, OUT_HTML)
    mb = OUT_HTML.stat().st_size / 1024 / 1024
    print(f"MANUAL.html ({mb:.1f} MB・自己完結) → {OUT_HTML}")


def build_pdf() -> None:
    d = prepare("pdf")

    def run(*args: str) -> None:
        subprocess.run([sys.executable, str(QTPDF), *args], cwd=d, check=True)

    if (PDFWORK / "fonts").exists():
        shutil.copytree(PDFWORK / "fonts", d / "fonts")  # 設計書PDF用の取得済みを流用
    else:
        run("fonts", "fonts")
    run("init")
    run("build", ".", "--design", "engineering-note")
    made = d / "_pdf" / "MANUAL.pdf"
    if not made.exists():
        sys.exit(f"error: {made} が生成されなかった")
    shutil.copy2(made, OUT_PDF)
    mb = OUT_PDF.stat().st_size / 1024 / 1024
    print(f"MANUAL.pdf ({mb:.1f} MB) → {OUT_PDF}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--html", action="store_true", help="HTML だけ作る")
    ap.add_argument("--pdf", action="store_true", help="PDF だけ作る")
    args = ap.parse_args()
    both = not (args.html or args.pdf)
    if both or args.html:
        build_html()
    if both or args.pdf:
        build_pdf()


if __name__ == "__main__":
    main()
