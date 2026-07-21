"""zenn.py — Zenn記法の Markdown を Quarto(.qmd)記法に変換する。

Zenn(https://zenn.dev)の記事は GitHub Flavored Markdown に独自拡張を加えた
記法(frontmatter・message/details ブロック・埋め込みカード等)を使っており、
そのままでは Quarto/Typst で組版できない(コンテナ記法は Pandoc の fenced-div
とは意味が違うし、`@[card](url)` のような埋め込みは Quarto が知らない記法)。

このスクリプトは実在の Zenn 記事(articles/*.md)を実際に読んで洗い出した記法を
元に、機械的に走査して Quarto が解釈できる記法へ変換する。**元ファイルは一切
変更しない**(読み込むだけで、出力は別ファイルに書く)。

対応する記法:
    - frontmatter (title/emoji/type/topics/published) → Quarto frontmatter
      (title はそのまま採用、それ以外は zenn-* という素通しの独自キーとして
      残す。PDFの見た目には影響しないが、情報としては欠落させない)
    - `:::message` / `:::message alert`
        → `::: {.callout-note}` / `::: {.callout-warning}`
    - `:::details タイトル`
        → `::: {.callout-note collapse="true"}` + 中に `## タイトル`
          (Quarto の callout はこの内側の見出みだしをタイトルとして吸収し、
          目次・章番号には数えない。PDF では details の折りたたみは効かない
          ので、展開済みの note として出す)
    - 入れ子(`::::` 以降、4個以上のコロン)
        → ネストの深さに応じてコロン数を振り直す(3, 4, 5, ...)。
          Pandoc の fenced-div は「外側ほど長いフェンス」であることだけが
          要件なので、元原稿のコロン数が不揃いでも安全な形に正規化できる。
    - `@[card](url)` `@[tweet](url)` などの埋め込み → 通常のリンクに変換
      (PDFでは埋め込み再生や OGP カード表示ができないため)
    - コードブロックのファイル名指定 ```lang:filename / ```lang: 説明
        → コード直前に「**太字のファイル名**」というキャプション行を添える。
          Quarto の `{.lang filename="..."}` 属性は一見それらしく見えるが、
          Typst 出力では実際には何も表示されない(未実行コードには効かない)
          ことを実機ビルドで確認済みなので、確実に見える手段としてこちらを使う。
    - ` ```mermaid ` → ` ```{mermaid} `(qtpdf.py の shadow 生成と同じ変換。
      zenn.py の出力は .qmd としてそのまま qtpdf build に渡すため、
      qtpdf 側の .md 専用の変換を経由しない分をここで肩代わりする)
    - 文書内リンク `[...](#見出し)` が壊れないよう、見出しに明示アンカーを
      付与する(qtpdf.py の shadow 生成と同じアルゴリズム)

変換できない・意味が失われる記法(Zenn独自の脚注 `^[...]`、画像サイズ指定
`![](img =600x)`、message/details 以外の未知のコンテナ、対応の取れない
`:::` 等)は黙って落とさず、標準エラー出力に警告として列挙する。

使い方:
    python zenn.py <入力.md> <出力.qmd>
    python zenn.py --dir <Zenn記事フォルダ> [--out <出力フォルダ>]

`--dir` は配下の `*.md` を再帰的に探して一括変換する(`node_modules` は除外)。
`--out` を省略すると `--dir` と同じ場所に `<元のファイル名>.qmd` を作る。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Windows のコンソール既定は cp932 で、日本語や記号を出すと落ちることがある。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


# ============================================================================
# 正規表現(全体で使い回す)
# ============================================================================

# コードフェンスの開始/継続判定。バッククォート・チルダのどちらにも対応。
FENCE_LINE = re.compile(r"^(\s*)(`{3,}|~{3,})")
FENCE_OPEN = re.compile(r"^(\s*)(`{3,}|~{3,})[ \t]*(.*)$")

# Zenn のコンテナ記法。message / details のみ(Zennが提供するのはこの2種)。
CONTAINER_OPEN = re.compile(r"^:{3,}\s*(message|details)\b[ \t]*(.*)$")
CONTAINER_CLOSE = re.compile(r"^:{3,}\s*$")
UNKNOWN_CONTAINER = re.compile(r"^:{3,}\s*([A-Za-z][\w-]*)")

# Zenn の埋め込み記法。@[card](url) @[tweet](url) @[youtube](id) など。
EMBED = re.compile(r"@\[([A-Za-z0-9_-]+)\]\(([^)]*)\)")
URL_LIKE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")

# 見出しとアンカー(qtpdf.py の _pin_anchors と同じアルゴリズム)。
# ATX見出しは CommonMark 上、行頭に半角スペース最大3個までのインデントを許す
# (Zenn記事でも、リスト内から書き始めた見出しなどでこの形が実際に出てくる)。
HEADING = re.compile(r"^( {0,3})(#{1,6})\s+(.+?)\s*$")
LINK_TARGET = re.compile(r"\]\(#([^)]+)\)")

# 変換できない・情報が失われる可能性がある記法の検出用。
FOOTNOTE_INLINE = re.compile(r"\^\[")
IMG_SIZE_SUFFIX = re.compile(r"!\[[^\]]*\]\([^)]*[ \t]+=\d+x?\d*\)")

# コードフェンスの言語表記のうち、Zenn でよく使われる略記を正式名に寄せる。
# (無くても壊れはしないが、シンタックスハイライトの当たりが良くなる)
LANG_ALIAS = {
    "cmd": "dos", "bat": "dos", "batch": "dos",
    "ps": "powershell", "ps1": "powershell",
}


# ============================================================================
# frontmatter
# ============================================================================

def split_frontmatter(text: str) -> tuple[list[str], str]:
    """先頭の `---` 〜 `---` を frontmatter として切り出す。無ければ空リスト。"""
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                body = "\n".join(lines[i + 1:])
                return lines[1:i], body.lstrip("\n")
    return [], text


def _parse_scalar(rest: str) -> str:
    """`key:` の右側をそのまま1つの値として切り出す(末尾コメントは落とす)。

    Zenn の frontmatter は素朴な `key: value` の並びで、ネストした構造は
    使わない。フル YAML パーサを持ち込まず、以下の3パターンだけを扱う。
    """
    rest = rest.strip()
    if not rest:
        return rest
    if rest[0] in "\"'":
        quote = rest[0]
        end = rest.find(quote, 1)
        if end != -1:
            return rest[:end + 1]
        return rest
    if rest[0] == "[":
        end = rest.find("]")
        if end != -1:
            return rest[:end + 1]
        return rest
    # 素のスカラ値。` #` 以降はコメントとして落とす。
    hash_pos = rest.find(" #")
    return rest[:hash_pos].rstrip() if hash_pos != -1 else rest.rstrip()


def parse_zenn_frontmatter(fm_lines: list[str]) -> dict[str, str]:
    """Zenn frontmatter を `{key: raw_value}` の素朴な辞書にする。"""
    fm: dict[str, str] = {}
    key_re = re.compile(r"^([A-Za-z_][\w-]*):[ \t]?(.*)$")
    for line in fm_lines:
        m = key_re.match(line)
        if not m:
            continue
        key, rest = m.groups()
        fm[key] = _parse_scalar(rest)
    return fm


def render_quarto_frontmatter(fm: dict[str, str], warnings: list[str],
                               fallback_title: str) -> str:
    """Quarto frontmatter を組み立てる。

    title はそのまま採用する。emoji/type/topics/published は PDF の見た目には
    直接関係しないが、勝手に消さず `zenn-*` という独自キーとして残す
    (Quarto/Pandoc は未知の frontmatter キーを無視するだけなので害はない)。
    """
    title = fm.get("title")
    if not title:
        warnings.append(
            "frontmatter に title が無かったため、ファイル名から仮のタイトルを設定した"
            f"(仮タイトル: {fallback_title})。本来のタイトルを確認すること。")
        title = f'"{fallback_title}"'

    lines = ["---", f"title: {title}"]
    for zenn_key, quarto_key in (
        ("emoji", "zenn-emoji"),
        ("type", "zenn-type"),
        ("topics", "zenn-topics"),
        ("published", "zenn-published"),
    ):
        if zenn_key in fm and fm[zenn_key]:
            lines.append(f"{quarto_key}: {fm[zenn_key]}")
    lines.append("---")
    return "\n".join(lines) + "\n"


# ============================================================================
# コードフェンス(言語:ファイル名 / mermaid)
# ============================================================================

def convert_code_fences(body: str, warnings: list[str]) -> str:
    """```lang:filename / ```lang: 説明 を Quarto の filename 属性に変える。

    ```mermaid は qtpdf.py の shadow 生成と同じく ```{mermaid} に変える
    (zenn.py の出力は .qmd としてそのままビルドに渡すため、.md 専用の
    その変換を経由しない分をここで行う必要がある)。
    """
    lines = body.split("\n")
    out: list[str] = []
    in_fence = False
    fence_char = ""

    for lineno, line in enumerate(lines, 1):
        if not in_fence:
            m = FENCE_OPEN.match(line)
            if not m:
                out.append(line)
                continue
            indent, marker, info = m.groups()
            in_fence = True
            fence_char = marker[0]
            info = info.strip()

            if not info:
                out.append(line)
                continue

            if ":" in info:
                lang, filename = (p.strip() for p in info.split(":", 1))
            else:
                lang, filename = info, ""

            if lang.lower() == "mermaid":
                if filename:
                    warnings.append(
                        f"{lineno}行目: mermaid フェンスのファイル名指定 \"{filename}\" は"
                        " Quarto の実行セルには渡せないため無視した")
                out.append(f"{indent}{marker}{{mermaid}}")
                continue

            if filename and lang:
                # Quarto の `{.lang filename="..."}` 属性は Typst 出力では
                # 何も表示されずに黙って消える(実際にビルドして .typ の中間
                # 出力を見て確認した)。実行セルの `#| filename:` オプションは
                # 未実行コードには効かないため使えない。確実に見える形として、
                # コード直前に太字のキャプション行を1つ添える。
                canon = LANG_ALIAS.get(lang.lower(), lang)
                out.append(f"{indent}**`{filename}`**")
                out.append("")
                out.append(f"{indent}{marker}{canon}")
                continue

            if filename and not lang:
                warnings.append(
                    f"{lineno}行目: コードフェンス情報 \"{info}\" は言語名が空のため"
                    " ファイル名キャプションに変換できず、そのまま出力した")
                out.append(line)
                continue

            canon = LANG_ALIAS.get(info.lower())
            out.append(f"{indent}{marker}{canon}" if canon else line)
            continue

        # フェンス内部。閉じフェンス(同じ記号が3個以上だけの行)を判定する。
        if re.match(rf"^\s*{re.escape(fence_char)}{{3,}}\s*$", line):
            in_fence = False
            fence_char = ""
        out.append(line)

    if in_fence:
        warnings.append("コードフェンスが閉じられないままファイル末尾に達した")
    return "\n".join(out)


# ============================================================================
# message / details コンテナ(入れ子対応)
# ============================================================================

def convert_containers(body: str, warnings: list[str]) -> str:
    """`:::message` `:::details` を Quarto callout に変換する(入れ子対応)。

    Zenn は入れ子のときに外側のコロンを増やす(`::::` の中に `:::`)が、
    元原稿のコロン数は必ずしも厳密ではないので信用しない。開始行が来るたびに
    スタックへ積み、深さに応じてコロン数(3, 4, 5, ...)を振り直して出力する。
    こうすると Pandoc の fenced-div が要求する「外側ほど長いフェンス」を
    常に満たせる。
    """
    lines = body.split("\n")
    out: list[str] = []
    stack: list[dict] = []
    in_fence = False

    for lineno, line in enumerate(lines, 1):
        if FENCE_LINE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue

        m_open = CONTAINER_OPEN.match(line)
        if m_open:
            kind, rest = m_open.groups()
            depth = len(stack) + 1
            fence = ":" * (depth + 2)
            if kind == "message":
                alert = rest.strip().lower().startswith("alert")
                cls = "callout-warning" if alert else "callout-note"
                out.append(f"{fence} {{.{cls}}}")
            else:  # details
                title = rest.strip()
                out.append(f'{fence} {{.callout-note collapse="true"}}')
                if title:
                    out.append("")
                    out.append(f"## {title}")
                else:
                    warnings.append(
                        f"{lineno}行目: :::details にタイトルが無い。"
                        " 内容だけの note callout として出力した")
            stack.append({"kind": kind, "depth": depth})
            continue

        m_close = CONTAINER_CLOSE.match(line)
        if m_close:
            if not stack:
                warnings.append(
                    f"{lineno}行目: 対応する開始行の無い \":::\" 系の行を検出。"
                    " そのまま出力したので内容を確認すること")
                out.append(line)
                continue
            entry = stack.pop()
            out.append(":" * (entry["depth"] + 2))
            continue

        out.append(line)

    if stack:
        warnings.append(
            f"閉じられていない :::message / :::details が {len(stack)} 個ある"
            "(ファイル末尾まで開いたまま)。原稿を確認すること")
    return "\n".join(out)


# ============================================================================
# 埋め込み記法(@[card](url) 等)
# ============================================================================

def convert_embeds(body: str, warnings: list[str]) -> str:
    """`@[type](value)` を通常のリンクに変換する。

    PDF では OGP カードやツイート・動画の埋め込み表示ができないため、
    「何のリンクか」が分かる形のテキストリンクに落とす。value が URL の形を
    していない場合(ID のみの指定など)はリンク先が正しいか保証できないため
    警告を出す。
    """
    lines = body.split("\n")
    out: list[str] = []
    in_fence = False

    for lineno, line in enumerate(lines, 1):
        if FENCE_LINE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue

        def _sub(m: re.Match, lineno=lineno) -> str:
            provider, value = m.group(1), m.group(2).strip()
            if URL_LIKE.match(value):
                href = value
            else:
                warnings.append(
                    f"{lineno}行目: @[{provider}]({value}) はURL形式ではないため、"
                    " リンク先として正しいか確認すること")
                href = value
            return f"[{provider}: {value}]({href})"

        out.append(EMBED.sub(_sub, line))

    return "\n".join(out)


# ============================================================================
# 見出しアンカーの固定(qtpdf.py の _pin_anchors と同じアルゴリズム)
# ============================================================================

def _slug(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"[*_`\[\]()]", "", s)
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def pin_heading_anchors(body: str, warnings: list[str]) -> str:
    """文書内リンク `[...](#見出し)` が Typst で解決できるよう見出しにIDを振る。

    GitHub / VSCode / Pandoc でスラッグ化の方言が違うため、リンク先スラッグと
    見出しのスラッグがズレると Typst が `label ... does not exist` で落ちる
    (qtpdf.py の pitfalls.md 参照)。リンク先を集め、ハイフンを無視して
    一致する見出しにはそのIDを明示的に付与する。zenn.py の出力は .qmd として
    qtpdf.py の shadow 生成(.md 専用)を経由しないため、ここで同じ処理をする。

    あわせて、行頭にスペースが付いた ATX 見出し(`  #### 見出し` のような形)を
    列0まで詰める。GitHub/CommonMark は見出し前に半角スペース最大3個までを
    許容するが、Quarto(Pandoc の markdown リーダー)は列0の `#` しか見出しと
    認識せず、インデント付きの見出しはただの本文として escape されてしまう。
    実在の Zenn 記事(リスト内から書き始めた見出し等)でこの形が出てくるため、
    見つけたら座標を正規化しないとリンク切れで Typst のコンパイルが落ちる。
    """
    targets = {t.replace("-", ""): t for t in LINK_TARGET.findall(body)}
    out: list[str] = []
    used: dict[str, int] = {}
    in_fence = False
    for lineno, line in enumerate(body.split("\n"), 1):
        if FENCE_LINE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        m = None if in_fence else HEADING.match(line)
        if m and "{#" not in line:
            indent, hashes, heading_text = m.groups()
            if indent:
                warnings.append(
                    f"{lineno}行目: 行頭にスペースが付いた見出し \"{hashes} {heading_text}\" を"
                    " 列0まで詰めた(Quartoは列0の見出ししか認識しないため)")
            slug = _slug(heading_text)
            slug = targets.get(slug.replace("-", ""), slug)
            if slug:
                # 同じ見出し文言が複数回出てくると同じスラッグが重複し、
                # Typst が "duplicate label" で落ちる。GitHub のスラッガーに
                # 合わせて2回目以降は -1, -2 ... を付けて衝突を避ける
                # (ただしリンク先として明示的に指定されたスラッグはそのまま優先する)。
                if slug in used and slug not in targets.values():
                    used[slug] += 1
                    slug = f"{slug}-{used[slug]}"
                else:
                    used.setdefault(slug, 0)
                line = f"{hashes} {heading_text} {{#{slug}}}"
            elif indent:
                line = f"{hashes} {heading_text}"
        out.append(line)
    return "\n".join(out)


# ============================================================================
# 未対応記法の検出(変換はせず、警告だけ出す)
# ============================================================================

def scan_unhandled(body: str, warnings: list[str]) -> None:
    """変換ロジックが無い(=情報が落ちる可能性がある)記法を検出して警告する。"""
    in_fence = False
    for lineno, line in enumerate(body.split("\n"), 1):
        if FENCE_LINE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        if FOOTNOTE_INLINE.search(line):
            warnings.append(
                f"{lineno}行目: Zenn のインライン脚注 ^[...] を検出。"
                " Quarto/Pandoc は [^id] … [^id]: 本文 という参照形式の脚注しか"
                " 認識しないため、手動での書き換えが必要")

        if IMG_SIZE_SUFFIX.search(line):
            warnings.append(
                f"{lineno}行目: Zenn の画像サイズ指定 (例: ![](img =600x)) を検出。"
                " Quarto では ![alt](path){width=...} 形式に手動で書き換えること")

        m_unknown = UNKNOWN_CONTAINER.match(line)
        if m_unknown and m_unknown.group(1) not in ("message", "details"):
            warnings.append(
                f"{lineno}行目: 未知のコンテナ記法 ':::{m_unknown.group(1)}' を検出。"
                " message/details 以外は変換対象外なのでそのまま出力した")


# ============================================================================
# 全体の変換
# ============================================================================

def convert_text(text: str, warnings: list[str], fallback_title: str) -> str:
    fm_lines, body = split_frontmatter(text)
    if not fm_lines:
        warnings.append(
            "frontmatter(--- で囲まれたメタデータ)が見つからなかった。"
            " title 等は仮の値になる")

    scan_unhandled(body, warnings)

    fm = parse_zenn_frontmatter(fm_lines)
    header = render_quarto_frontmatter(fm, warnings, fallback_title)

    body = convert_code_fences(body, warnings)
    body = convert_containers(body, warnings)
    body = convert_embeds(body, warnings)
    body = pin_heading_anchors(body, warnings)

    return header + "\n" + body.strip("\n") + "\n"


def convert_file(src: Path, dst: Path) -> list[str]:
    text = src.read_text(encoding="utf-8")
    warnings: list[str] = []
    out_text = convert_text(text, warnings, fallback_title=src.stem)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(out_text, encoding="utf-8", newline="\n")
    return warnings


# ============================================================================
# CLI
# ============================================================================

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Zenn記法の Markdown を Quarto(.qmd)記法に変換する")
    ap.add_argument("input", nargs="?", help="入力の .md ファイル")
    ap.add_argument("output", nargs="?", help="出力の .qmd ファイル")
    ap.add_argument("--dir", help="この配下の *.md を再帰的に一括変換する"
                                   "(node_modules は除外)")
    ap.add_argument("--out", help="--dir 使用時の出力フォルダ"
                                   "(省略時は入力と同じ場所に .qmd を作る)")
    args = ap.parse_args(argv)

    if args.dir:
        src_root = Path(args.dir)
        if not src_root.is_dir():
            print(f"エラー: {src_root} はフォルダではない", file=sys.stderr)
            return 1
        out_root = Path(args.out) if args.out else src_root
        targets = sorted(
            p for p in src_root.rglob("*.md")
            if "node_modules" not in p.parts)
        if not targets:
            print(f"警告: {src_root} 配下に .md が見つからなかった", file=sys.stderr)
            return 0

        total_warnings = 0
        for src in targets:
            rel = src.relative_to(src_root)
            dst = (out_root / rel).with_suffix(".qmd")
            warnings = convert_file(src, dst)
            total_warnings += len(warnings)
            tag = f"({len(warnings)}件警告)" if warnings else ""
            print(f"変換: {src} -> {dst} {tag}")
            for w in warnings:
                print(f"  [警告] {src.name}: {w}", file=sys.stderr)
        print(f"{len(targets)}本を変換した(警告 合計{total_warnings}件)")
        return 0

    if not args.input or not args.output:
        ap.error("入力と出力を指定するか、--dir を使ってください")

    src, dst = Path(args.input), Path(args.output)
    if not src.is_file():
        print(f"エラー: {src} が見つからない", file=sys.stderr)
        return 1

    warnings = convert_file(src, dst)
    print(f"変換: {src} -> {dst}")
    for w in warnings:
        print(f"[警告] {w}", file=sys.stderr)
    if warnings:
        print(f"{len(warnings)}件の警告あり。上記を確認すること。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
