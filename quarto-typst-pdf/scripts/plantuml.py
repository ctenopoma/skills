"""plantuml.py — PlantUML の図を Quarto×Typst のPDFに埋め込むための単体変換スクリプト。

Quarto は `.puml` を直接扱えない。そのため「事前にこのスクリプトで画像へビルドし、
その画像を Markdown に `![...](foo.png)` として埋め込む」という経路を取る
(qtpdf.py が扱う Mermaid / Draw.io とは違い、PlantUML はビルド時点で画像化が終わっている
必要がある)。qtpdf.py 側の改修はこのスクリプトの役割ではない。呼び出し側(親)が
`.puml` を見つけて本スクリプトで変換してから `qtpdf.py build` に渡すこと。

    python plantuml.py <入力.puml> <出力.png|.svg> [--fonts DIR] [--font NAME]
    python plantuml.py doctor            # Corretto / plantuml.jar の有無を報告する

## 前提(ライセンス・権限まわりの制約)

- Java は **Amazon Corretto のみ**を使う。Oracle JDK や Temurin など他ベンダの
  JDK/JRE は探さないし使わない(ライセンス上の制約)。
  `~/.local/corretto` にポータブル展開されている前提(qtpdf.py が Quarto を
  `~/.local/quarto` に展開するのと同じ思想)。導入方法は `references/plantuml.md`。
- `plantuml.jar` は **このスキルの git リポジトリには置かない**(30MB近い巨大バイナリ
  のため)。`~/.local/plantuml/plantuml.jar` などユーザー領域に置く。
- レンダリングはすべてローカルの `java -jar plantuml.jar` で完結する。PlantUML の
  公式オンラインサーバ(plantuml.com 等)へは一切送らない。

## 日本語フォントの注入について

図中のテキストは既定では Java (AWT/Java2D) が見つけたフォントで描かれ、本文で使う
「Noto Sans CJK JP」とは揃わない(OS に別の和文フォントが入っていればそちらが
使われてしまい、豆腐にはならなくても本文とフォントが不揃いになる)。これを避けるため:

1. `.puml` の `@start...` 直後に `skinparam defaultFontName "<--font>"` を注入する
   (既に指定があれば注入しない)。
2. Java 起動時に `-Dsun.java2d.fontpath=<--fonts>` でフォントフォルダを渡す。
   これは Windows で OS にインストールせずに Java へ和文フォントを認識させる手段。
   **注意**: このオプションを付けると Java から見えるフォント検索パスが
   指定フォルダだけに置き換わる(追加ではなく上書き)。Noto Sans/Serif CJK JP は
   ラテン文字・記号も含む総合フォントなので実害は無いが、フォルダには
   qtpdf の `fonts/` と同じセット(Noto Sans/Serif CJK JP、JetBrains Mono)を
   渡しておくのが安全。

出力形式は PNG を推奨する。PNG はこの時点でグリフがラスタに焼き込まれるため、
Typst 側のフォント解決に一切依存しない。SVG は `<text font-family="...">` を
文字列のまま持つ形式なので、Typst(内部で resvg 系エンジンを使う)がその
フォント名を解決できないと文字化け・豆腐のリスクがある
(`references/pitfalls.md` に書いた draw.io SVG と同じ注意点)。
確実に和文を出したい場合は PNG を使うこと。

数式(AsciiMath / `<math>...</math>` タグ、`@startmath`)は plantuml.jar に同梱の
JLaTeXMath で描画される。JLaTeXMath は自前のTeXフォントを使うため、
`sun.java2d.fontpath` を絞っても数式の描画には影響しない(検証済み)。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Windows のコンソール既定は cp932 で、日本語を出すと落ちることがあるため固定する。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

CORRETTO_HOME = Path.home() / ".local" / "corretto"
PLANTUML_HOME = Path.home() / ".local" / "plantuml"
DEFAULT_FONT = "Noto Sans CJK JP"

CORRETTO_URL_TEMPLATE = "https://corretto.aws/downloads/latest/amazon-corretto-{ver}-x64-{os}-jdk.zip"


# --------------------------------------------------------------------------
# 環境の検出
# --------------------------------------------------------------------------

def _java_exe_name() -> str:
    return "java.exe" if os.name == "nt" else "java"


def _is_corretto(java_path: str) -> bool:
    """そのjavaがAmazon Corretto かどうかを `-version` の出力から確認する。"""
    try:
        r = subprocess.run([java_path, "-version"], capture_output=True, text=True, timeout=10)
        text = ((r.stdout or "") + (r.stderr or "")).lower()
        return "corretto" in text
    except Exception:
        return False


def find_java() -> str | None:
    """Amazon Corretto の java 実行体を探す。Corretto と確認できないものは使わない。"""
    # 1. 明示指定(環境変数)。ユーザーが意図的に指定したものは信頼して使う。
    for var in ("CORRETTO_HOME", "PLANTUML_JAVA_HOME"):
        env = os.environ.get(var)
        if env:
            cand = Path(env) / "bin" / _java_exe_name()
            if cand.exists():
                return str(cand)

    # 2. 既定の展開先(qtpdf.py の QUARTO_HOME と同じ思想: ~/.local/corretto)
    cand = CORRETTO_HOME / "bin" / _java_exe_name()
    if cand.exists():
        return str(cand)

    # 3. PATH 上に java があっても、Corretto だと確認できたときだけ使う
    #    (ライセンス制約: Oracle JDK / Temurin 等を誤って使わないため)
    which_java = shutil.which("java")
    if which_java and _is_corretto(which_java):
        return which_java

    return None


def find_plantuml_jar() -> Path | None:
    env = os.environ.get("PLANTUML_JAR")
    if env and Path(env).exists():
        return Path(env)
    cand = PLANTUML_HOME / "plantuml.jar"
    if cand.exists():
        return cand
    return None


INSTALL_HELP = """\
PlantUML の実行に必要なものが見つかりません。

  Java (Amazon Corretto): {java_status}
  plantuml.jar          : {jar_status}

導入方法は references/plantuml.md を参照してください。要点だけ書くと:

  1. Amazon Corretto (zip版) を取得して {corretto_home} に展開する
     取得元: https://corretto.aws/downloads/latest/amazon-corretto-21-x64-windows-jdk.zip
     (zip の中身は jdk21.x.y_z/ という1階層のフォルダなので、
      その中身を {corretto_home} 直下に置く。展開先に "bin/java.exe" が来ること)

  2. plantuml.jar を取得して {plantuml_home} に置く
     取得元: https://github.com/plantuml/plantuml/releases/latest
             ("plantuml.jar"という名前のアセット。ライセンス別の jar でも動くが
              数式描画(JLaTeXMath)を含むフル版である plantuml.jar を推奨)

どちらも管理者権限もインストーラも不要(zip展開のみ)。PATH は変更しなくてよい
(このスクリプトが直接パスを指定して呼ぶため)。

Java が用意できない環境では PlantUML だけは使えない。他の図(Mermaid / Draw.io)は
Java 無しでも qtpdf.py で従来どおり処理できる。
"""


def cmd_doctor(args) -> int:
    java = find_java()
    jar = find_plantuml_jar()

    print("## PlantUML 環境")
    if java:
        ver_line = subprocess.run([java, "-version"], capture_output=True, text=True).stderr.splitlines()
        ver = ver_line[0] if ver_line else "?"
        print(f"- Java (Corretto): {ver}  ({java})")
    else:
        print(f"- Java (Corretto): **未導入** → {CORRETTO_HOME} に展開してください")

    if jar:
        print(f"- plantuml.jar: {jar}")
    else:
        print(f"- plantuml.jar: **未導入** → {PLANTUML_HOME} に置いてください")

    print("\n## 判定")
    if java and jar:
        print("PlantUML の図を変換できる状態。")
        return 0
    print("不足あり。`python plantuml.py doctor` を再実行して確認しながら導入してください。")
    print()
    print(INSTALL_HELP.format(
        java_status="OK" if java else "未導入",
        jar_status="OK" if jar else "未導入",
        corretto_home=CORRETTO_HOME, plantuml_home=PLANTUML_HOME,
    ))
    return 1


def _need_tools() -> tuple[str, Path]:
    java = find_java()
    jar = find_plantuml_jar()
    if not java or not jar:
        print(INSTALL_HELP.format(
            java_status="OK" if java else "未導入",
            jar_status="OK" if jar else "未導入",
            corretto_home=CORRETTO_HOME, plantuml_home=PLANTUML_HOME,
        ), file=sys.stderr)
        sys.exit(1)
    return java, jar


# --------------------------------------------------------------------------
# フォント注入
# --------------------------------------------------------------------------

START_TAG = re.compile(r"^\s*@start\w+")
NON_ASCII = re.compile(r"[^\x00-\x7f]")


def inject_font(text: str, font: str) -> str:
    """`@start...` の直後に skinparam defaultFontName を差し込む。

    既に defaultFontName の指定があれば触らない(呼び出し側の指定を尊重する)。
    `@startmath` のように skinparam を解釈しない図種に対しても無害であることを
    確認済み(単純に無視される)。`@start` で始まらない断片(include されるパーツ
    など)は先頭にそのまま足す。
    """
    if "defaultFontName" in text:
        return text

    lines = text.splitlines()
    out: list[str] = []
    injected = False
    for line in lines:
        out.append(line)
        if not injected and START_TAG.match(line):
            out.append(f'skinparam defaultFontName "{font}"')
            injected = True
    if not injected:
        out = [f'skinparam defaultFontName "{font}"', *lines]
    return "\n".join(out)


# --------------------------------------------------------------------------
# 変換
# --------------------------------------------------------------------------

FORMAT_FLAG = {".png": "-tpng", ".svg": "-tsvg"}


def cmd_render(args) -> int:
    src = Path(args.input).resolve()
    dst = Path(args.output).resolve()
    if not src.exists():
        sys.exit(f"入力が見つかりません: {src}")

    fmt_flag = FORMAT_FLAG.get(dst.suffix.lower())
    if not fmt_flag:
        sys.exit(f"出力の拡張子は .png か .svg にしてください: {dst.name}")

    java, jar = _need_tools()

    text = src.read_text(encoding="utf-8")
    text = inject_font(text, args.font)

    if not args.fonts and NON_ASCII.search(text):
        print("警告: --fonts を指定していません。ASCII 以外の文字が含まれているため、"
              "OS に和文フォントが入っていないと豆腐になる可能性があります。",
              file=sys.stderr)

    cmd = [java]
    if args.fonts:
        font_dir = Path(args.fonts).resolve()
        if not font_dir.is_dir():
            sys.exit(f"--fonts で指定したフォルダがありません: {font_dir}")
        cmd.append(f"-Dsun.java2d.fontpath={font_dir}")
    cmd += ["-jar", str(jar), fmt_flag, "-pipe"]

    r = subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True)

    dst.parent.mkdir(parents=True, exist_ok=True)
    if r.stdout:
        dst.write_bytes(r.stdout)

    if r.returncode != 0:
        msg = (r.stderr or b"").decode("utf-8", errors="replace").strip()
        print(f"PlantUML がエラーを報告しました(終了コード {r.returncode})。", file=sys.stderr)
        if msg:
            print(msg, file=sys.stderr)
        if r.stdout:
            print(f"エラー内容を示す画像は書き出し済み: {dst}(デバッグ用。文面を確認すること)",
                  file=sys.stderr)
        return 1

    print(f"生成: {dst}")
    return 0


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="PlantUML の .puml を画像に変換する(Quarto×Typst に埋め込む前段)。",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor", help="Corretto / plantuml.jar の有無を報告する")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("render", help="入力を画像に変換する(既定のサブコマンド)")
    p.add_argument("input", help="入力 .puml")
    p.add_argument("output", help="出力先(.png または .svg)")
    p.add_argument("--fonts", help="和文フォントなどを含むフォルダ(qtpdf の fonts/ を想定)")
    p.add_argument("--font", default=DEFAULT_FONT,
                   help=f'skinparam defaultFontName に注入するフォント名(既定: "{DEFAULT_FONT}")')
    p.set_defaults(func=cmd_render)

    argv = sys.argv[1:]
    # `python plantuml.py foo.puml foo.png` のように render を省略した呼び出しを許す。
    if argv and argv[0] not in ("doctor", "render", "-h", "--help"):
        argv = ["render", *argv]

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
