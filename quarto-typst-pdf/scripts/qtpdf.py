"""qtpdf — Quarto × Typst で技術文書をPDFにするための実行系。

サブコマンド:
    doctor              環境を調べて、足りないものと導入方法を報告する
    install             Quarto をポータブル導入する(管理者権限・PATH変更なし)
    fonts <dir>         推奨フォントをそのフォルダへダウンロードする
    init [dir]          プロジェクトにビルド設定を作る(_quarto.yml など)
    build [dir]         md → shadow qmd を作ってレンダリングする
    book [dir]          全文書を1冊の合本PDFにする
    revise <file>       git 差分から改訂バー付きのPDFを出す
    matrix [dir]        design × style の組み合わせを一括レンダする
    diagram <in> <out>  PlantUML の .puml を画像にする
    zenn <in> <out>     Zenn記法の Markdown を .qmd に変換する
    probe <pdf>         ページ数・埋め込みフォント・空白ページを調べ、PNGを書き出す

どのサブコマンドも、判断に使える形(見出し付きの短い報告)で結果を返す。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ASSETS = SKILL_DIR / "assets"

# Windows のコンソール既定は cp932 で、日本語や罫線記号を出すと落ちる。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

DESIGNS = ["engineering-note", "spec-sheet", "handbook", "minimal-mono"]
STYLES = ["jis-jp", "western", "blog-flat", "clause"]

QUARTO_HOME = Path.home() / ".local" / "quarto"

RECOMMENDED_FONTS = [
    ("NotoSansCJKjp-Regular.otf",
     "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/OTF/Japanese/NotoSansCJKjp-Regular.otf"),
    ("NotoSansCJKjp-Bold.otf",
     "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/OTF/Japanese/NotoSansCJKjp-Bold.otf"),
    ("NotoSerifCJKjp-Regular.otf",
     "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Serif/OTF/Japanese/NotoSerifCJKjp-Regular.otf"),
    ("NotoSerifCJKjp-Bold.otf",
     "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Serif/OTF/Japanese/NotoSerifCJKjp-Bold.otf"),
]
JETBRAINS_ZIP = "https://github.com/JetBrains/JetBrainsMono/releases/download/v2.304/JetBrainsMono-2.304.zip"
JETBRAINS_WANTED = {"JetBrainsMono-Regular.ttf", "JetBrainsMono-Bold.ttf",
                    "JetBrainsMono-Italic.ttf", "JetBrainsMono-BoldItalic.ttf"}


# --------------------------------------------------------------------------
# 環境
# --------------------------------------------------------------------------

def find_quarto() -> str | None:
    q = shutil.which("quarto")
    if q:
        return q
    for cand in (QUARTO_HOME / "bin" / "quarto.exe", QUARTO_HOME / "bin" / "quarto"):
        if cand.exists():
            return str(cand)
    return None


def _run(cmd, **kw) -> subprocess.CompletedProcess:
    """出力を取り込んで実行する。Windows の既定コードページで落ちないよう UTF-8 固定。"""
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


def _err(r: subprocess.CompletedProcess) -> str:
    """失敗理由として見せる文字列。stderr が空なら stdout を使う。"""
    return ((r.stderr or "") + (r.stdout or ""))[-800:]


def need_quarto() -> str:
    q = find_quarto()
    if not q:
        sys.exit("Quarto が見つかりません。`qtpdf.py install` で導入してください。")
    return q


def cmd_doctor(args) -> int:
    print("## 環境")
    quarto = find_quarto()
    if quarto:
        ver = (_run([quarto, "--version"]).stdout or "?").strip()
        print(f"- Quarto: {ver}  ({quarto})")
    else:
        print("- Quarto: **未導入** → `qtpdf.py install`(ポータブル導入・管理者権限不要)")

    print(f"- Python: {sys.version.split()[0]}")
    for name, note in [("node", "Mermaid / Draw.io CLI に使う"),
                       ("java", "PlantUML に必要。ライセンス面では Amazon Corretto を推奨")]:
        path = shutil.which(name)
        print(f"- {name}: {path or '**無し** — ' + note}")

    chrome = _find_chrome()
    print(f"- Chromium系ブラウザ: {chrome or '**無し** — Mermaid の画像化に必要'}")

    # PlantUML は Corretto(ライセンス上の指定)と plantuml.jar が要る。
    corretto = Path.home() / ".local/corretto/bin/java.exe"
    jar = Path.home() / ".local/plantuml/plantuml.jar"
    if corretto.exists() and jar.exists():
        print("- PlantUML: 利用可(Amazon Corretto + plantuml.jar をユーザー領域に導入済み)")
    else:
        print("- PlantUML: **未整備** → `python scripts/plantuml.py doctor` で導入手順を確認")

    print("\n## フォント")
    font_dir = Path(args.fonts) if args.fonts else Path.cwd() / "fonts"
    if font_dir.is_dir():
        files = sorted(p.name for p in font_dir.iterdir() if p.suffix.lower() in (".otf", ".ttf", ".ttc"))
        print(f"- 持ち込みフォント: {font_dir} に {len(files)} 個")
        for f in files:
            print(f"  - {f}")
        missing = [n for n, _ in RECOMMENDED_FONTS if n not in files]
        if missing:
            print(f"- 不足(推奨セット): {', '.join(missing)} → `qtpdf.py fonts {font_dir}`")
    else:
        print(f"- 持ち込みフォント: {font_dir} が無い → `qtpdf.py fonts {font_dir}`")
        print("  OS にインストールせずフォルダに置く方式。管理者権限が要らず、そのまま持ち運べる。")

    print("\n## 判定")
    if quarto and (font_dir.is_dir()):
        print("PDF化できる状態。`qtpdf.py init` → `qtpdf.py build` へ。")
    else:
        print("不足あり。上の → の手順を実行してから再確認。")
    return 0


def _find_chrome() -> str | None:
    for c in ("chrome", "msedge", "chromium"):
        p = shutil.which(c)
        if p:
            return p
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"):
        if Path(p).exists():
            return p
    return None


def cmd_install(args) -> int:
    if find_quarto() and not args.force:
        print("Quarto は導入済み。入れ直すなら --force。")
        return 0

    print("最新版を調べています…")
    with urllib.request.urlopen("https://api.github.com/repos/quarto-dev/quarto-cli/releases/latest") as r:
        ver = json.load(r)["tag_name"].lstrip("v")

    suffix = "win.zip" if os.name == "nt" else "linux-amd64.tar.gz"
    url = f"https://github.com/quarto-dev/quarto-cli/releases/download/v{ver}/quarto-{ver}-{suffix}"
    QUARTO_HOME.parent.mkdir(parents=True, exist_ok=True)
    archive = QUARTO_HOME.parent / f"quarto-{ver}.{'zip' if suffix.endswith('zip') else 'tgz'}"

    print(f"取得中: {url}")
    urllib.request.urlretrieve(url, archive)

    if QUARTO_HOME.exists():
        shutil.rmtree(QUARTO_HOME)
    print(f"展開中: {QUARTO_HOME}")
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(QUARTO_HOME)
    else:
        shutil.unpack_archive(str(archive), str(QUARTO_HOME))
    archive.unlink()

    quarto = find_quarto()
    if not quarto:
        return 1
    print(f"導入完了: {quarto}")
    print("PATH には登録していない。このスクリプトが直接呼ぶので設定変更は不要。")
    return 0


def cmd_fonts(args) -> int:
    dest = Path(args.dir).resolve()
    dest.mkdir(parents=True, exist_ok=True)

    for name, url in RECOMMENDED_FONTS:
        target = dest / name
        if target.exists():
            print(f"skip {name}")
            continue
        print(f"取得中 {name}")
        urllib.request.urlretrieve(url, target)

    if not (dest / "JetBrainsMono-Regular.ttf").exists():
        print("取得中 JetBrains Mono")
        tmp = dest / "_jbm.zip"
        urllib.request.urlretrieve(JETBRAINS_ZIP, tmp)
        with zipfile.ZipFile(tmp) as z:
            for n in z.namelist():
                base = n.rsplit("/", 1)[-1]
                if base in JETBRAINS_WANTED:
                    (dest / base).write_bytes(z.read(n))
        tmp.unlink()

    shutil.copy(SKILL_DIR / "references" / "fonts.md", dest / "README.md")
    print(f"\n完了: {dest}")
    print("OS へのインストールはしていない。Typst には font-paths 経由で渡る。")
    return 0


# --------------------------------------------------------------------------
# プロジェクト設定
# --------------------------------------------------------------------------

QUARTO_YML = """\
project:
  type: default
  output-dir: _pdf

execute:
  enabled: false   # ノートブックは実行せず、保存済みの出力をそのまま組版する

# `- quarto` の後に置くことで Quarto 本体(crossref 等)の後段で走る。
# numbering.lua は Quarto が float 化しなかった表・図に番号を振るため、
# Quarto の処理結果を見てから動く必要がある。
filters:
  - quarto
  - {assets}/filters/revision.lua
  - {assets}/filters/numbering.lua
  - {assets}/filters/fit-images.lua

format:
  typst:
    papersize: a4
    margin:
      x: 2.2cm
      y: 2.4cm
    lang: {lang}
    mainfont: "Noto Sans CJK JP"
    monofont: "JetBrains Mono"
    font-paths: {fonts}
    fontsize: 10.5pt
    toc: true
    toc-depth: 2
    include-in-header:
      - {assets}/base.typ
      - {assets}/callouts.typ
      - {assets}/revision.typ
"""

DESIGN_YML = """\
# design: {name} — 見た目(配色・フォント・表の体裁)
format:
  typst:
{extra}\
    include-in-header:
      - {assets}/designs/{name}.typ
"""

# 本文フォントと文字サイズは Quarto のオプションで指定する。
# design の .typ 側で #set text(font:) しても、テンプレートの set text が
# 後に来て上書きされるため効かない。
DESIGN_EXTRA = {
    "engineering-note": '    mainfont: "Noto Sans CJK JP"\n    fontsize: 10.5pt\n',
    "spec-sheet": '    mainfont: "Noto Serif CJK JP"\n    fontsize: 10pt\n',
    "handbook": '    mainfont: "Noto Sans CJK JP"\n    fontsize: 10.5pt\n',
    "minimal-mono": '    mainfont: "Noto Sans CJK JP"\n    fontsize: 10pt\n',
}

STYLE_EXTRA = {
    "jis-jp": ('    number-sections: true\n    section-numbering: "1.1.1 "\n'
               '    toc-depth: 3\n    page-numbering: "1"\n'),
    "western": ('    number-sections: true\n    section-numbering: "1.1"\n'
                '    toc-depth: 2\n    page-numbering: "1"\n'),
    "blog-flat": '    number-sections: false\n    toc-depth: 2\n    page-numbering: "1"\n',
    "clause": ('    number-sections: true\n    section-numbering: "1.1.1 "\n'
               '    toc-depth: 3\n    page-numbering: "1 / 1"\n'),
}

STYLE_SUPPLEMENT = {
    "jis-jp": ("図", "表"), "blog-flat": ("図", "表"),
    "clause": ("図", "表"), "western": ("Figure", "Table"),
}

GITIGNORE_BLOCK = """
# --- PDF化(Quarto × Typst)の生成物 ---
/_pdf/
/_pdfbook/
*__pdf.qmd
*__pdf.typ
*__pdf_files/
/.quarto/
**/*.quarto_ipynb

# 持ち込みフォント(バイナリはコミットしない。入手先は fonts/README.md)
/fonts/*
!/fonts/README.md
"""


def cmd_init(args) -> int:
    root = Path(args.dir).resolve()
    assets = ASSETS.as_posix()
    fonts = args.fonts or "fonts"

    (root / "_quarto.yml").write_text(
        QUARTO_YML.format(assets=assets, fonts=fonts, lang=args.lang),
        encoding="utf-8", newline="\n")
    print("作成: _quarto.yml")

    for d in DESIGNS:
        (root / f"_quarto-{d}.yml").write_text(
            DESIGN_YML.format(name=d, assets=assets, extra=DESIGN_EXTRA[d]),
            encoding="utf-8", newline="\n")
    for s in STYLES:
        fig, tbl = STYLE_SUPPLEMENT[s]
        body = (f"# style: {s} — 番号と組織(章節番号・図表番号・目次・ページ番号)\n"
                "format:\n  typst:\n"
                + STYLE_EXTRA[s]
                + f"    include-in-header:\n      - {assets}/styles/{s}.typ\n"
                + f'fig-supplement: "{fig}"\ntbl-supplement: "{tbl}"\n')
        if fig == "図":
            body += ('crossref:\n  fig-title: "図"\n  tbl-title: "表"\n'
                     '  fig-prefix: "図"\n  tbl-prefix: "表"\n  title-delim: ""\n')
        (root / f"_quarto-{s}.yml").write_text(body, encoding="utf-8", newline="\n")
    print(f"作成: design {len(DESIGNS)}種 / style {len(STYLES)}種 のプロファイル")

    gi = root / ".gitignore"
    existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if "/_pdf/" not in existing:
        gi.write_text(existing.rstrip() + "\n" + GITIGNORE_BLOCK, encoding="utf-8", newline="\n")
        print("追記: .gitignore(生成PDF・中間生成物・フォントバイナリを除外)")

    font_dir = root / fonts
    if not font_dir.is_dir():
        print(f"\nフォント未配置。`qtpdf.py fonts {font_dir}` で推奨セットを取得してください。")
    return 0


# --------------------------------------------------------------------------
# ビルド
# --------------------------------------------------------------------------

MERMAID_FENCE = re.compile(r"^(\s*)```mermaid\s*$", re.MULTILINE)
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
LINK_TARGET = re.compile(r"\]\(#([^)]+)\)")


def _slug(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"[*_`\[\]()]", "", s)
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def _pin_anchors(body: str) -> str:
    """見出しに明示IDを付ける。

    スラッガーの方言差(GitHub / VSCode / Pandoc)で文書内リンクが切れると
    Typst が「ラベルが無い」とコンパイルエラーになるため、リンク先を集めて
    ハイフン無視で一致する見出しにはそのIDを採用する。
    """
    targets = {t.replace("-", ""): t for t in LINK_TARGET.findall(body)}
    out, in_fence = [], False
    for line in body.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        m = None if in_fence else HEADING.match(line)
        if m and "{#" not in line:
            slug = _slug(m.group(2))
            slug = targets.get(slug.replace("-", ""), slug)
            if slug:
                line = f"{m.group(1)} {m.group(2)} {{#{slug}}}"
        out.append(line)
    return "\n".join(out)


MANUAL_NUMBER = re.compile(r"^(#{1,6})\s+\d+(?:\.\d+)*\.?\s+(?=\S)")


def strip_manual_numbers(body: str) -> str:
    """見出しの先頭に手で振られた番号を落とす。

    「## 1. 概要」のような原文に自動採番を重ねると「2.1 1. 概要」と二重になる。
    番号を style 側に一本化したいときに使う(既定では実行しない。原文の意図的な
    番号を消してしまう可能性があるため)。
    """
    out, in_fence = [], False
    for line in body.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            line = MANUAL_NUMBER.sub(r"\1 ", line)
        out.append(line)
    return "\n".join(out)


def _transform_md(text: str, strip_numbers: bool = False) -> str:
    text = MERMAID_FENCE.sub(r"\1```{mermaid}", text)
    if strip_numbers:
        text = strip_manual_numbers(text)
    return _pin_anchors(text)


def make_shadow(src: Path, strip_numbers: bool = False) -> Path:
    """GitHub互換の .md から Quarto 用の shadow .qmd を作る(元ファイルは変更しない)。"""
    text = src.read_text(encoding="utf-8")

    title, lines = src.stem, text.splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        m = re.match(r"^#\s+(.+)$", line)
        if m:
            title = m.group(1).strip()
            lines = lines[:i] + lines[i + 1:]
        break

    shadow = src.with_name(src.stem + "__pdf.qmd")
    shadow.write_text(
        f'---\ntitle: "{title}"\noutput-file: "{src.stem}.pdf"\n'
        "shift-heading-level-by: -1\n---\n\n"
        + _transform_md("\n".join(lines), strip_numbers),
        encoding="utf-8", newline="\n")
    return shadow


def discover(root: Path) -> list[Path]:
    """PDF化の対象を見つける。ルート直下の .md と notebooks/ 配下の .ipynb。"""
    docs = [p for p in sorted(root.glob("*.md"))
            if not p.name.endswith("__pdf.qmd") and p.name.lower() != "changelog.md"]
    for pattern in ("notebooks/*.ipynb", "*.ipynb"):
        docs += sorted(root.glob(pattern))
    return [d for d in docs if ".ipynb_checkpoints" not in str(d)]


def _profiles(args) -> list[str]:
    p = []
    if args.design:
        p.append(args.design)
    if args.style:
        p.append(args.style)
    return p


COVERS = ["simple", "formal"]

COVER_KEYS = {
    "TITLE": "title", "SUBTITLE": "subtitle", "AUTHOR": "author",
    "DATE": "date", "VERSION": "version",
    "NUMBER": "doc-number", "CLASSIFICATION": "classification",
}


def _typst_str(value) -> str:
    """Typst の文字列リテラルにする。値が無ければ none。"""
    if value in (None, ""):
        return "none"
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_cover(root: Path, name: str, meta: dict) -> Path:
    """表紙テンプレートのプレースホルダを文書メタデータで埋め、生成物を返す。

    表紙は文書ごとに値が違うので、テンプレートをそのまま include できない。
    @@KEY@@ を置換した実体を作業ファイルとして書き出し、それを読ませる。
    """
    template = (ASSETS / "covers" / f"{name}.typ").read_text(encoding="utf-8")
    for placeholder, key in COVER_KEYS.items():
        template = template.replace(f"@@{placeholder}@@", _typst_str(meta.get(key)))
    out = root / f".qtpdf-cover-{name}.typ"
    out.write_text(template, encoding="utf-8", newline="\n")
    return out


WATERMARK = """\
// DRAFT 透かし。レビュー段階のPDFが最終版と取り違えられる事故を防ぐ。
#set page(background: rotate(-30deg, text(
  size: 100pt, weight: 700, fill: rgb(0, 0, 0, 26), tracking: 0.1em, "{text}",
)))
"""


def build_watermark(root: Path, label: str) -> Path:
    out = root / ".qtpdf-watermark.typ"
    out.write_text(WATERMARK.format(text=label), encoding="utf-8", newline="\n")
    return out


def read_front_matter(doc: Path) -> dict:
    """.qmd / .md 先頭の frontmatter を素朴に読む(表紙に流す値の取得用)。"""
    meta = {}
    try:
        text = doc.read_text(encoding="utf-8")
    except OSError:
        return meta
    if not text.startswith("---"):
        return meta
    body = text.split("---", 2)
    if len(body) < 3:
        return meta
    for line in body[1].splitlines():
        if ":" not in line or line.startswith((" ", "\t", "#")):
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta


EXTRA_PROFILE = "qtpdfx"


def _extra_profile(root: Path, headers: list[str]) -> str | None:
    """生成した Typst を読ませるための一時プロファイルを書く。

    `-M include-in-header:...` ではリストに追加できず既存の指定を潰すため、
    プロファイルとして渡す(プロファイル同士なら include-in-header は合成される)。
    """
    if not headers:
        return None
    body = "format:\n  typst:\n"
    # コードテーマを使うときは Skylighting を止め、言語タグを保つフィルタを足す。
    # (Skylighting が生きていると Typst の raw(theme:) は無視される)
    if any("code-theme" in h for h in headers):
        body += "    syntax-highlighting: none\n"
    body += "    include-in-header:\n"
    body += "".join(f"      - {h}\n" for h in headers)
    if any("code-theme" in h for h in headers):
        body += ("filters:\n  - quarto\n"
                 f"  - {(ASSETS / 'filters/code-theme.lua').as_posix()}\n"
                 f"  - {(ASSETS / 'filters/revision.lua').as_posix()}\n"
                 f"  - {(ASSETS / 'filters/numbering.lua').as_posix()}\n"
                 f"  - {(ASSETS / 'filters/fit-images.lua').as_posix()}\n")
    (root / f"_quarto-{EXTRA_PROFILE}.yml").write_text(body, encoding="utf-8", newline="\n")
    return EXTRA_PROFILE


def code_themes() -> list[str]:
    return sorted(p.stem for p in (ASSETS / "themes").glob("*.tmTheme"))


def build_code_theme(root: Path, name: str) -> Path:
    """コードの配色を VSCode 由来の tmTheme に差し替える Typst を書く。

    scripts/vscode_theme.py が VSCode のテーマ JSON から生成したものを使う。
    """
    theme = ASSETS / "themes" / f"{name}.tmTheme"
    if not theme.exists():
        sys.exit(f"コードテーマ {name} がありません。利用可能: {', '.join(code_themes())}")

    # Typst はプロジェクト外の絶対パスを読めない(ルートより上は参照不可)。
    # テーマ本体をプロジェクトへ複製し、相対パスで参照する。
    local = root / f".qtpdf-theme-{name}.tmTheme"
    shutil.copy(theme, local)

    # 暗い配色のときはコード面の下地も暗くする。Quarto は raw ブロックに
    # 明るい灰色の背景を固定で敷くため、そのままだと文字が読めなくなる。
    bg = _theme_background(theme)
    dark = bg is not None and sum(int(bg[i:i + 2], 16) for i in (0, 2, 4)) < 3 * 128

    out = root / ".qtpdf-code-theme.typ"
    out.write_text(
        "// コードの配色(VSCode テーマ由来の tmTheme)。\n"
        "// Skylighting を止めて Typst 自身のハイライタに任せるため、\n"
        "// filters/code-theme.lua と syntax-highlighting: none がセットで要る。\n"
        f'#set raw(theme: "{local.name}")\n'
        + (f'#show raw.where(block: true): set block(fill: rgb("#{bg}"))\n'
           f'#show raw.where(block: true): set text(fill: rgb("#{_theme_foreground(theme)}"))\n'
           if dark else ""),
        encoding="utf-8", newline="\n")
    return out


def _plist_global(theme: Path, key: str) -> str | None:
    """tmTheme の先頭グローバル設定から色を1つ取る(background / foreground)。"""
    text = theme.read_text(encoding="utf-8")
    m = re.search(r"<key>" + key + r"</key>\s*<string>#([0-9A-Fa-f]{6})", text)
    return m.group(1) if m else None


def _theme_background(theme: Path) -> str | None:
    return _plist_global(theme, "background")


def _theme_foreground(theme: Path) -> str:
    return _plist_global(theme, "foreground") or "E0E0E0"


def _extra_headers(root: Path, doc: Path, args) -> list[str]:
    """表紙・透かしのように文書ごとに生成する Typst を集める。"""
    extra = []
    if getattr(args, "code_theme", None):
        extra.append(str(build_code_theme(root, args.code_theme)))
    # 透かしを先に置く。後ろに置くと、先に1ページ使い切る表紙に掛からない。
    if getattr(args, "watermark", None):
        extra.append(str(build_watermark(root, args.watermark)))
    cover = getattr(args, "cover", None)
    if cover:
        meta = read_front_matter(doc)
        for key in ("version", "doc-number", "classification", "subtitle", "date"):
            val = getattr(args, key.replace("-", "_"), None)
            if val:
                meta[key] = val
        if args.author:
            meta["author"] = args.author
        extra.append(str(build_cover(root, cover, meta)))
    return extra


def cmd_revise(args) -> int:
    """改訂版PDFを出す。git 差分から変更ブロックを判定し、改訂バー付きで組版する。"""
    import revision  # 同じ scripts/ にある

    root = Path(args.dir).resolve()
    src = Path(args.file)
    src = src if src.is_absolute() else root / src

    marked = src.with_name(src.stem + "__rev.qmd")
    blocks = revision.mark(src, args.base, marked)

    n_add = sum(1 for b in blocks if b["kind"] == "added")
    print(f"## 改訂箇所  {len(blocks)} ブロック(追加 {n_add} / 変更 {len(blocks) - n_add})")
    for b in blocks:
        print(f"- {b['kind']:8} 行{b['start']}-{b['end']} 変更率{b['ratio']:.0%}  {b['preview']}")

    if src.suffix == ".md":
        # shadow と同じ前処理(mermaid 記法・アンカー)を通す
        text = marked.read_text(encoding="utf-8")
        marked.write_text(
            f'---\ntitle: "{src.stem}"\noutput-file: "{src.stem}-rev.pdf"\n'
            "shift-heading-level-by: -1\n---\n\n" + _transform_md(text),
            encoding="utf-8", newline="\n")

    args.targets = [str(marked)]
    args.cover = getattr(args, "cover", None)
    rc = _render_docs(root, [marked], args)
    marked.unlink(missing_ok=True)
    return rc


def cmd_build(args) -> int:
    root = Path(args.dir).resolve()
    if not (root / "_quarto.yml").exists():
        sys.exit("_quarto.yml がありません。先に `qtpdf.py init` を実行してください。")

    targets = [Path(t) for t in args.targets] if args.targets else discover(root)
    if not targets:
        sys.exit("PDF化する文書が見つかりません。")
    return _render_docs(root, targets, args)


def _render_docs(root: Path, targets: list[Path], args) -> int:
    quarto = need_quarto()
    rendered, failed = [], []
    for src in targets:
        src = src if src.is_absolute() else root / src
        doc = (make_shadow(src, getattr(args, "strip_numbers", False))
               if src.suffix == ".md" else src)
        cmd = [quarto, "render", str(doc.relative_to(root))]
        # 表紙と透かしは文書ごとに内容が変わるので、その都度生成して
        # 一時プロファイルとして足す。
        profiles = _profiles(args)
        extra = _extra_profile(root, _extra_headers(root, doc, args))
        if extra:
            profiles.append(extra)
        if profiles:
            cmd += ["--profile", ",".join(profiles)]
        r = _run(cmd, cwd=root)
        (rendered if r.returncode == 0 else failed).append((src.name, _err(r)))

    print(f"## レンダリング結果  {len(rendered)}/{len(targets)} 成功")
    for name, _ in rendered:
        print(f"- OK   {name}")
    for name, err in failed:
        last = [l for l in err.strip().splitlines() if l.strip()]
        print(f"- FAIL {name}: {last[-1] if last else '?'}")
    print(f"\n出力先: {root / '_pdf'}")
    return 1 if failed else 0


BOOK_YML = """\
project:
  type: book
  output-dir: ../_pdf

book:
  title: "{title}"
  author: "{author}"   # 注: typst の book テンプレートは author 必須(未指定だと落ちる)
  output-file: "{outfile}"
  chapters:
{chapters}

execute:
  enabled: false

format:
  typst:
    papersize: a4
    margin:
      x: 2.2cm
      y: 2.4cm
    lang: {lang}
    mainfont: "Noto Sans CJK JP"
    monofont: "JetBrains Mono"
    font-paths: {fonts}
    fontsize: 10.5pt
    toc: true
    toc-depth: 2
    include-in-header:
      - {assets}/base.typ
      - {assets}/callouts.typ
      - {assets}/revision.typ
      - {assets}/designs/{design}.typ

fig-supplement: "図"
tbl-supplement: "表"

filters:
  - quarto
  - {assets}/filters/revision.lua
  - {assets}/filters/numbering.lua
  - {assets}/filters/fit-images.lua
"""


def cmd_book(args) -> int:
    root = Path(args.dir).resolve()
    quarto = need_quarto()
    work = root / "_pdfbook"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    chapters = []
    for i, src in enumerate(discover(root)):
        if src.suffix == ".md":
            name = "index.qmd" if i == 0 else src.stem + ".qmd"
            (work / name).write_text(
                _transform_md(src.read_text(encoding="utf-8"), args.strip_numbers),
                                     encoding="utf-8", newline="\n")
        else:
            name = src.name
            shutil.copy2(src, work / name)
        chapters.append(name)

    for extra in ("figures", "images", "img", "assets"):
        d = root / extra
        if d.is_dir():
            shutil.copytree(d, work / extra,
                            ignore=shutil.ignore_patterns("__pycache__", "*.py"))

    (work / "_quarto.yml").write_text(BOOK_YML.format(
        title=args.title or root.name, author=args.author, outfile=args.output or root.name,
        chapters="\n".join(f"    - {c}" for c in chapters),
        lang=args.lang, fonts=(root / (args.fonts or "fonts")).as_posix(),
        assets=ASSETS.as_posix(), design=args.design or "engineering-note",
    ), encoding="utf-8", newline="\n")

    env = dict(os.environ)
    patched = _patch_orange_book(work)
    if patched:
        env["TYPST_PACKAGE_PATH"] = str(patched)

    r = _run([quarto, "render", str(work)], env=env)
    print(f"## 合本  章数 {len(chapters)}")
    if r.returncode != 0:
        for line in _err(r).strip().splitlines()[-8:]:
            print("  " + line)
        return 1
    print(f"出力先: {root / '_pdf'}")
    return 0


def _patch_orange_book(work: Path) -> Path | None:
    """章頭の「奇数ページまで送る」改頁を弱い改頁に差し替えたパッケージを用意する。

    Quarto の book(typst)は orange-book テンプレートを使い、章頭で
    pagebreak(to: "odd") が固定で入るため空白ページができる。設定では消せない。
    パッチ版を TYPST_PACKAGE_PATH で優先解決させる(Quarto 本体は書き換えない)。
    """
    quarto = find_quarto()
    if not quarto:
        return None
    share = Path(quarto).resolve().parent.parent / "share"
    src = share / "extension-subtrees/orange-book/_extensions/orange-book/typst/packages/preview"
    if not src.is_dir():
        return None
    dst = work / "pkgs"
    shutil.copytree(src, dst / "preview")
    for lib in (dst / "preview").rglob("lib.typ"):
        text = lib.read_text(encoding="utf-8")
        if 'pagebreak(to: "odd")' in text:
            lib.write_text(text.replace('pagebreak(to: "odd")', "pagebreak(weak: true)"),
                           encoding="utf-8", newline="\n")
    return dst


def cmd_matrix(args) -> int:
    root = Path(args.dir).resolve()
    quarto = need_quarto()
    target = args.target or (discover(root)[0] if discover(root) else None)
    if not target:
        sys.exit("対象が見つかりません。")
    src = Path(target)
    src = src if src.is_absolute() else root / src
    doc = make_shadow(src) if src.suffix == ".md" else src

    designs = args.designs or DESIGNS
    styles = args.styles or STYLES
    ok, failed = 0, []
    for d in designs:
        for s in styles:
            tag = f"{d}__{s}"
            out = root / "_pdf/matrix" / tag
            r = _run([quarto, "render", str(doc.relative_to(root)),
                      "--profile", f"{d},{s}", "--output-dir", str(out)], cwd=root)
            if r.returncode == 0:
                ok += 1
                pdfs = list(out.glob("*.pdf"))
                if pdfs:
                    _preview(pdfs[0], root / "_pdf/matrix/_preview", tag, (0, 1))
            else:
                failed.append(tag)
            print(f"  {'OK  ' if r.returncode == 0 else 'FAIL'} {tag}", flush=True)

    print(f"\n## 適合テスト  {ok}/{len(designs) * len(styles)} 通り成功")
    for t in failed:
        print(f"- FAIL {t}")
    print(f"目視用PNG: {root / '_pdf/matrix/_preview'}")
    return 1 if failed else 0


# --------------------------------------------------------------------------
# 検査
# --------------------------------------------------------------------------

def _preview(pdf: Path, out_dir: Path, tag: str, pages) -> None:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(pdf)
    for i in pages:
        if i < len(doc):
            doc[i].render(scale=1.5).to_pil().save(out_dir / f"{tag}_p{i + 1}.png")


def _delegate(script: str, argv: list[str]) -> int:
    """同じ scripts/ にある単体ツールへそのまま渡す。"""
    r = subprocess.run([sys.executable, str(SKILL_DIR / "scripts" / script), *argv])
    return r.returncode


def cmd_diagram(args) -> int:
    """PlantUML の .puml を画像にする(Markdown へは画像として埋め込む)。"""
    argv = [args.input, args.output]
    if args.fonts:
        argv += ["--fonts", args.fonts]
    if args.font:
        argv += ["--font", args.font]
    return _delegate("plantuml.py", argv)


def cmd_zenn(args) -> int:
    """Zenn記法の Markdown を Quarto の .qmd に変換する。"""
    if args.dir:
        argv = ["--dir", args.dir] + (["--out", args.out] if args.out else [])
    else:
        argv = [args.input, args.output]
    return _delegate("zenn.py", argv)


def cmd_probe(args) -> int:
    try:
        import pypdfium2 as pdfium
        from pypdf import PdfReader
    except ImportError:
        sys.exit("pip install pypdfium2 pypdf が必要です。")

    pdf = Path(args.pdf).resolve()
    doc = pdfium.PdfDocument(pdf)
    reader = PdfReader(str(pdf))

    fonts = set()
    for page in reader.pages:
        for f in page.get("/Resources", {}).get("/Font", {}).values():
            fonts.add(str(f.get_object().get("/BaseFont")).split("+")[-1])

    blank, labels = [], set()
    for i in range(len(doc)):
        t = doc[i].get_textpage().get_text_range()
        if len(t.strip()) <= 4:
            blank.append(i + 1)
        labels |= set(re.findall(r"(?:図|表|Figure|Table)\s?[\d.\-]+", t))

    print(f"## {pdf.name}")
    print(f"- ページ数: {len(doc)}")
    print(f"- 空白ページ: {blank or 'なし'}")
    print(f"- 図表番号: {len(labels)} 個")
    print("- 埋め込みフォント:")
    for f in sorted(fonts):
        print(f"  - {f}")

    pages = [int(p) - 1 for p in args.pages.split(",")] if args.pages else [0]
    out = pdf.parent / "_preview"
    _preview(pdf, out, pdf.stem, pages)
    print(f"\nページ画像: {out}  (Read ツールで開いて目視確認する)")
    return 0


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(prog="qtpdf", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor"); p.add_argument("--fonts"); p.set_defaults(func=cmd_doctor)
    p = sub.add_parser("install"); p.add_argument("--force", action="store_true"); p.set_defaults(func=cmd_install)
    p = sub.add_parser("fonts"); p.add_argument("dir"); p.set_defaults(func=cmd_fonts)

    p = sub.add_parser("init")
    p.add_argument("dir", nargs="?", default=".")
    p.add_argument("--lang", default="ja"); p.add_argument("--fonts")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("build")
    p.add_argument("dir", nargs="?", default=".")
    p.add_argument("targets", nargs="*")
    p.add_argument("--design", choices=DESIGNS); p.add_argument("--style", choices=STYLES)
    p.add_argument("--strip-numbers", action="store_true",
                   help="見出し先頭の手書き番号を落とす(自動採番との二重を防ぐ)")
    p.add_argument("--cover", choices=COVERS, help="表紙を差し込む")
    p.add_argument("--code-theme", help="コードの配色(assets/themes の tmTheme 名)")
    p.add_argument("--watermark", help='透かし文字(例: DRAFT)')
    p.add_argument("--author"); p.add_argument("--subtitle"); p.add_argument("--date")
    p.add_argument("--version"); p.add_argument("--doc-number"); p.add_argument("--classification")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("book")
    p.add_argument("dir", nargs="?", default=".")
    p.add_argument("--title"); p.add_argument("--author", default="")
    p.add_argument("--output"); p.add_argument("--design", choices=DESIGNS)
    p.add_argument("--strip-numbers", action="store_true",
                   help="見出し先頭の手書き番号を落とす(自動採番との二重を防ぐ)")
    p.add_argument("--lang", default="ja"); p.add_argument("--fonts")
    p.set_defaults(func=cmd_book)

    p = sub.add_parser("revise")
    p.add_argument("file"); p.add_argument("--base", required=True,
                                           help="比較元のリビジョン(例: HEAD~1, v1.0)")
    p.add_argument("dir", nargs="?", default=".")
    p.add_argument("--design", choices=DESIGNS); p.add_argument("--style", choices=STYLES)
    p.add_argument("--watermark"); p.add_argument("--author")
    p.add_argument("--code-theme")
    p.set_defaults(func=cmd_revise)

    p = sub.add_parser("matrix")
    p.add_argument("dir", nargs="?", default=".")
    p.add_argument("--target")
    p.add_argument("--designs", nargs="*"); p.add_argument("--styles", nargs="*")
    p.set_defaults(func=cmd_matrix)

    p = sub.add_parser("diagram", help="PlantUML の .puml を画像にする")
    p.add_argument("input"); p.add_argument("output")
    p.add_argument("--fonts"); p.add_argument("--font")
    p.set_defaults(func=cmd_diagram)

    p = sub.add_parser("zenn", help="Zenn記法の Markdown を .qmd に変換する")
    p.add_argument("input", nargs="?"); p.add_argument("output", nargs="?")
    p.add_argument("--dir"); p.add_argument("--out")
    p.set_defaults(func=cmd_zenn)

    p = sub.add_parser("probe")
    p.add_argument("pdf"); p.add_argument("--pages")
    p.set_defaults(func=cmd_probe)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
