#!/usr/bin/env python3
"""種別ごとの合本PDFを作る（quarto-typst-pdf skill の book コマンドの前処理）。

Quarto 1.10 系は「章フロントマターに title があると book+typst のレンダリングが落ちる」
不具合があるため、フロントマターを剥がして title を H1 に昇格・既存見出しを1段降格した
章ファイル群を docs/_pdfwork/<種別>/ に用意し、そこへ qtpdf book を実行する。

使い方:
  python pdf_book.py specs        --root . --output pdf/関数仕様書.pdf --title 関数仕様書
  python pdf_book.py test-specs   --root . --output pdf/テスト仕様書.pdf --title テスト仕様書
  python pdf_book.py test-results --root . --output pdf/テスト結果報告書.pdf --title テスト結果報告書
                                  （test-results は関数ごとに最新の1件のみ束ねる）

qtpdf.py は隣の quarto-typst-pdf skill から自動で探す（--qtpdf で明示指定も可）。
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ledger import parse_frontmatter  # noqa: E402

AUTHOR_LINE = '#text(size: title-main-3, weight: "bold", author)'
AUTHOR_SAFE = ('#text(size: title-main-3, weight: "bold", '
               '{ let a = if type(author) == array { author.join(", ") } else { author }; '
               'if a == none { "" } else { a } })')


def find_quarto() -> str:
    q = shutil.which("quarto")
    if q:
        return q
    cand = Path.home() / ".local" / "quarto" / "bin" / "quarto.exe"
    if cand.exists():
        return str(cand)
    sys.exit("error: quarto が見つからない（qtpdf.py install で導入）")


def patch_orange_book(book_dir: Path) -> Path | None:
    """orange-book パッケージのローカルコピーに2つのパッチを当てる。

    1. author 既定値が配列のまま #text に渡って落ちる問題（Quarto 1.10 の book+typst）
    2. 章頭の pagebreak(to: "odd") による空白ページ（qtpdf と同じパッチ）
    """
    pkgs = book_dir / "pkgs"
    if not (pkgs / "preview").exists():
        share = Path(find_quarto()).resolve().parent.parent / "share"
        src = share / "extension-subtrees/orange-book/_extensions/orange-book/typst/packages/preview"
        if not src.is_dir():
            return None
        shutil.copytree(src, pkgs / "preview")
    for lib in (pkgs / "preview").rglob("lib.typ"):
        text = lib.read_text(encoding="utf-8")
        text = text.replace('pagebreak(to: "odd")', "pagebreak(weak: true)")
        text = text.replace(AUTHOR_LINE, AUTHOR_SAFE)
        lib.write_text(text, encoding="utf-8", newline="\n")
    return pkgs


def find_qtpdf(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    for base in (Path(__file__).resolve().parents[2],):
        cand = base / "quarto-typst-pdf" / "scripts" / "qtpdf.py"
        if cand.exists():
            return cand
    sys.exit("error: qtpdf.py が見つからない。--qtpdf で quarto-typst-pdf/scripts/qtpdf.py を指定せよ")


def prepare_chapter(src: Path) -> str:
    text = src.read_text(encoding="utf-8-sig")
    fm = parse_frontmatter(text)
    parts = text.split("---", 2)
    body = parts[2] if len(parts) >= 3 and text.lstrip().startswith("---") else text
    # 見出しを1段降格（コードフェンス内は触らない）。PDFでは相対リンクとアンカーを平文化する
    # （合本内に存在しないラベル参照は Typst エラーになるため。HTML版にはリンクが残る）
    out, in_fence = [], False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        if not in_fence:
            if re.match(r"^#{1,5} ", line):
                line = "#" + line
            line = re.sub(r"\{#[^}]*\}", "", line)                       # 見出しアンカー定義
            line = re.sub(r"\[([^\]]*)\]\((?!https?://)[^)]*\)", r"\1", line)  # 相対リンク
        out.append(line)
    title = fm.get("title", src.stem)
    return f"# {title}\n" + "\n".join(out).lstrip("\n") + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["specs", "test-specs", "test-results"])
    ap.add_argument("--root", default=".")
    ap.add_argument("--output", required=True, help="出力PDF（root からの相対パス可）")
    ap.add_argument("--title", required=True)
    ap.add_argument("--design", default="spec-sheet")
    ap.add_argument("--fonts", default=None)
    ap.add_argument("--qtpdf", default=None)
    args = ap.parse_args()

    root = Path(args.root).resolve()
    src_dir = root / "docs" / args.kind
    files = sorted(p for p in src_dir.glob("*.md"))
    if args.kind == "test-results":
        latest: dict = {}
        for p in files:
            latest[p.stem.split("_", 1)[0]] = p  # ソート済みなので最後＝最新が残る
        files = [latest[k] for k in sorted(latest)]
    if not files:
        sys.exit(f"error: {src_dir} に .md がない")

    work = root / "docs" / "_pdfwork" / args.kind
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    for p in files:
        (work / p.name).write_text(prepare_chapter(p), encoding="utf-8")

    out_pdf = (root / args.output) if not Path(args.output).is_absolute() else Path(args.output)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(find_qtpdf(args.qtpdf)), "book", str(work).replace("\\", "/"),
           "--title", args.title, "--design", args.design,
           "--output", str(out_pdf).replace("\\", "/")]  # YAML二重引用符内の \ 対策
    if args.fonts:
        cmd += ["--fonts", str(args.fonts).replace("\\", "/")]
    r = subprocess.run(cmd)
    if r.returncode == 0 and out_pdf.exists():
        print(f"wrote {out_pdf}")
        return

    # --- フォールバック: orange-book にパッチして自前で再レンダリング ---
    print("qtpdf book が失敗。orange-book パッチ＋再レンダリングを試みる")
    book_dir = work / "_pdfbook"
    if not book_dir.exists():
        sys.exit("error: _pdfbook が無い（qtpdf book の前段で失敗している）")
    yml = book_dir / "_quarto.yml"
    text = yml.read_text(encoding="utf-8")
    text = re.sub(r'(?m)^  output-file: .*$', '  output-file: "bundle.pdf"', text)
    yml.write_text(text, encoding="utf-8", newline="\n")
    pkgs = patch_orange_book(book_dir)
    env = dict(os.environ)
    if pkgs:
        env["TYPST_PACKAGE_PATH"] = str(pkgs)
    r2 = subprocess.run([find_quarto(), "render", str(book_dir)], env=env)
    made = list((work / "_pdf").glob("*.pdf")) + list((book_dir / "_book").glob("*.pdf"))
    if r2.returncode != 0 or not made:
        sys.exit(f"error: フォールバックも失敗（quarto exit={r2.returncode}）")
    shutil.copy2(made[0], out_pdf)
    print(f"wrote {out_pdf}（フォールバック経由）")


if __name__ == "__main__":
    main()
