#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vscode_theme.py — VSCode のカラーテーマ(JSON/JSONC)を、Typst の
`raw(theme: "xxx.tmTheme")` が読み込める TextMate 形式(.tmTheme, plist XML)へ変換する。

## なぜこの変換が成立するか

Typst の `raw(theme:)` は Rust の syntect クレートでシンタックスハイライトを行っており、
syntect は TextMate 由来の配色ファイル(.tmTheme = plist XML)を読む。この plist に
最低限必要な構造は次の2点:

  1. ルートの dict が "name"(テーマ名の文字列)と "settings"(配列)を持つこと。
  2. "settings" 配列の **先頭要素だけ** "scope" キーを持たず、"settings" 辞書に
     background / foreground などエディタ全体の既定色(グローバル設定)を書く。
     2番目以降の要素は "scope"(TextMate スコープ。カンマ区切りで複数可)と
     "settings" 辞書(foreground / background / fontStyle)を持つ、トークンごとの規則。

VSCode のカラーテーマ JSON は、まさにこの TextMate の配色モデルをそのまま
踏襲しているため機械変換できる:

  - "tokenColors": [{ "scope": ..., "settings": {...} }, ...]
    → tmTheme の2番目以降の要素にほぼ1対1で対応する。
  - "colors" の "editor.background" / "editor.foreground" などの
    エディタ全体設定 → tmTheme の先頭要素(グローバル設定)に対応する。
    対応表は COLOR_KEY_MAP を参照。

対して "semanticTokenColors"(LSP のセマンティックハイライト用。VSCode 独自の
仕組みで TextMate スコープの概念を持たない)は tmTheme 側に対応する仕組みが
無いため、このスクリプトでは変換せず読み飛ばす(その旨を stderr に報告する)。

## JSONC への対応

VSCode のテーマ JSON はコメント入り・末尾カンマ入り(JSONC)であることが普通なので、
標準の json モジュールに渡す前に簡易的なコメント除去・末尾カンマ除去を行う。
文字列リテラル内の "//" や "," は壊さないよう、1文字ずつ状態機械で読む。

## 使い方

    python vscode_theme.py <入力.json> <出力.tmTheme> [--name テーマ名]

"""

from __future__ import annotations

import argparse
import json
import plistlib
import re
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# JSONC (コメント・末尾カンマ入り JSON) を標準 JSON へ正規化する
# ---------------------------------------------------------------------------
def strip_jsonc(text: str) -> str:
    """JSONC のコメント(// と /* */)を取り除く。

    文字列リテラルの中身は変更してはいけないので、"in_string" 状態を追いながら
    1文字ずつ走査する状態機械にしている(正規表現の一発置換だと文字列中の
    "//" を誤ってコメント扱いしてしまう)。
    """
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    escape = False
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            i += 1
            continue

        if c == '"':
            in_string = True
            out.append(c)
            i += 1
            continue

        if c == "/" and i + 1 < n and text[i + 1] == "/":
            # 行コメント: 改行まで読み飛ばす
            j = text.find("\n", i)
            i = n if j == -1 else j
            continue

        if c == "/" and i + 1 < n and text[i + 1] == "*":
            # ブロックコメント: */ まで読み飛ばす
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue

        out.append(c)
        i += 1

    stripped = "".join(out)
    # 末尾カンマ ( , の後に } か ] しか無い ) を除去。
    # コメントは既に消えているので、この正規表現は文字列内の "," には
    # マッチしない(文字列の中で直後が } や ] になる位置は稀だが、念のため
    # 「カンマの直後が空白類のみを挟んで閉じ括弧」というパターンに限定している)。
    stripped = re.sub(r",(\s*[}\]])", r"\1", stripped)
    return stripped


def load_jsonc(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8-sig")  # BOM 付きでも読めるように
    normalized = strip_jsonc(raw)
    return json.loads(normalized)


# ---------------------------------------------------------------------------
# VSCode "colors" → tmTheme グローバル設定(settings[0])への対応表
# ---------------------------------------------------------------------------
# 右辺は syntect の ThemeSettings が読む plist キー名。
# syntect 側で未知のキーは無視されるだけで壊れないので、対応が怪しいものより
# 明確に対応が取れるものだけを載せている。
COLOR_KEY_MAP: dict[str, str] = {
    "editor.background": "background",
    "editor.foreground": "foreground",
    "editorCursor.foreground": "caret",
    "editor.lineHighlightBackground": "lineHighlight",
    "editor.selectionBackground": "selection",
    "editor.selectionForeground": "selectionForeground",
    "editor.inactiveSelectionBackground": "inactiveSelection",
    "editor.findMatchBackground": "findHighlight",
    "editor.findMatchHighlightForeground": "findHighlightForeground",
    "editorGutter.background": "gutter",
    "editorLineNumber.foreground": "gutterForeground",
    "editorGroup.border": "shadow",
}

# raw(theme:) が最低限見るのはこの2つ。無い場合はテーマの "type" から
# 妥当な既定値を補う(VSCode のテーマは editor.background/foreground を
# 大抵持っているので、実際にはほぼ通らない保険)。
FALLBACK_BY_TYPE = {
    "dark": {"background": "#1E1E1E", "foreground": "#D4D4D4"},
    "light": {"background": "#FFFFFF", "foreground": "#000000"},
}


def build_global_settings(theme: dict[str, Any]) -> dict[str, str]:
    colors = theme.get("colors", {}) or {}
    global_settings: dict[str, str] = {}
    for vscode_key, tm_key in COLOR_KEY_MAP.items():
        value = colors.get(vscode_key)
        if isinstance(value, str) and value:
            global_settings[tm_key] = normalize_color(value)

    if "background" not in global_settings or "foreground" not in global_settings:
        theme_type = theme.get("type", "dark")
        fallback = FALLBACK_BY_TYPE.get(theme_type, FALLBACK_BY_TYPE["dark"])
        missing = [k for k in ("background", "foreground") if k not in global_settings]
        for key in missing:
            global_settings[key] = fallback[key]
        print(
            f"[vscode_theme] 警告: colors.editor.{'background' if 'background' in missing else ''}"
            f"{' / ' if len(missing) == 2 else ''}"
            f"{'foreground' if 'foreground' in missing else ''} が無いため既定色で補いました"
            f"({theme_type} 用フォールバック)。",
            file=sys.stderr,
        )
    return global_settings


def normalize_color(value: str) -> str:
    """#RGB / #RRGGBB / #RRGGBBAA を #RRGGBB(AA) の形へ揃える。

    tmTheme(plist) の色は文字列としてそのまま渡せば syntect 側で解釈するが、
    3桁短縮形 (#abc) は plist の慣習に無いので6桁へ展開しておく。
    """
    v = value.strip()
    if not v.startswith("#"):
        return v
    hex_part = v[1:]
    if len(hex_part) == 3:
        hex_part = "".join(ch * 2 for ch in hex_part)
        return "#" + hex_part.upper()
    return "#" + hex_part.upper()


# ---------------------------------------------------------------------------
# VSCode "tokenColors" → tmTheme settings[1:] への変換
# ---------------------------------------------------------------------------
def build_token_rules(theme: dict[str, Any]) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    token_colors = theme.get("tokenColors", [])
    if not isinstance(token_colors, list):
        return rules

    for entry in token_colors:
        if not isinstance(entry, dict):
            continue
        scope = entry.get("scope")
        settings_in = entry.get("settings")
        if not scope or not isinstance(settings_in, dict):
            # scope も settings も無いエントリは tmTheme 側で意味を持たないので捨てる
            continue

        # scope はリストのことも、"a, b, c" のような文字列のこともある。
        # tmTheme はカンマ区切りの1文字列を期待するので統一する。
        if isinstance(scope, list):
            scope_str = ", ".join(str(s) for s in scope)
        else:
            scope_str = str(scope)

        settings_out: dict[str, str] = {}
        if "foreground" in settings_in and settings_in["foreground"]:
            settings_out["foreground"] = normalize_color(str(settings_in["foreground"]))
        if "background" in settings_in and settings_in["background"]:
            settings_out["background"] = normalize_color(str(settings_in["background"]))
        if "fontStyle" in settings_in and settings_in["fontStyle"]:
            # VSCode/TextMate 双方とも "italic bold underline" のような
            # 空白区切り文字列なのでそのまま渡せる。
            settings_out["fontStyle"] = str(settings_in["fontStyle"])

        if not settings_out:
            continue

        rule: dict[str, Any] = {"scope": scope_str, "settings": settings_out}
        name = entry.get("name")
        if isinstance(name, str) and name:
            rule["name"] = name
        rules.append(rule)

    return rules


# ---------------------------------------------------------------------------
# 変換されないキーの報告
# ---------------------------------------------------------------------------
IGNORED_TOP_LEVEL_KEYS = {
    "semanticHighlighting",
    "semanticTokenColors",
    "$schema",
    "author",
    "maintainers",
    "include",
}


def report_ignored(theme: dict[str, Any]) -> None:
    for key in theme:
        if key in IGNORED_TOP_LEVEL_KEYS:
            note = ""
            if key == "semanticTokenColors":
                note = "(LSPのセマンティックハイライト用でTextMateスコープを持たないため変換不可)"
            elif key == "semanticHighlighting":
                note = "(有効/無効フラグのみで配色情報を持たないため対象外)"
            print(f"[vscode_theme] 無視: \"{key}\" {note}".rstrip(), file=sys.stderr)


# ---------------------------------------------------------------------------
# 変換本体
# ---------------------------------------------------------------------------
def convert(theme: dict[str, Any], name_override: str | None) -> dict[str, Any]:
    report_ignored(theme)

    name = name_override or theme.get("name") or "Converted VSCode Theme"
    global_settings = build_global_settings(theme)
    token_rules = build_token_rules(theme)

    if not token_rules:
        print(
            "[vscode_theme] 警告: tokenColors から有効な規則を1つも変換できませんでした。"
            "配色がほぼ既定色のままになります。",
            file=sys.stderr,
        )

    settings_array: list[dict[str, Any]] = [{"settings": global_settings}]
    settings_array.extend(token_rules)

    return {
        "name": name,
        "settings": settings_array,
    }


def write_tmtheme(tm_theme: dict[str, Any], out_path: Path) -> None:
    with out_path.open("wb") as fp:
        plistlib.dump(tm_theme, fp, fmt=plistlib.FMT_XML, sort_keys=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="VSCode のカラーテーマ(JSON/JSONC)を Typst raw(theme:) 用の .tmTheme に変換する"
    )
    parser.add_argument("input", type=Path, help="VSCode テーマの *-color-theme.json")
    parser.add_argument("output", type=Path, help="出力する .tmTheme (plist XML)")
    parser.add_argument("--name", default=None, help="tmTheme の name を上書きする")
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"[vscode_theme] エラー: 入力ファイルが見つかりません: {args.input}", file=sys.stderr)
        return 1

    try:
        theme = load_jsonc(args.input)
    except json.JSONDecodeError as e:
        print(f"[vscode_theme] エラー: JSON として読めませんでした: {e}", file=sys.stderr)
        return 1

    tm_theme = convert(theme, args.name)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_tmtheme(tm_theme, args.output)

    rule_count = len(tm_theme["settings"]) - 1
    print(
        f"[vscode_theme] 変換完了: {args.input} → {args.output} "
        f"(name=\"{tm_theme['name']}\", token規則={rule_count}件)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
