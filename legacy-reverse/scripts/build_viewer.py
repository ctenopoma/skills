#!/usr/bin/env python3
"""WBS/仕様書サイトを「実行するだけで見られる」単体実行ファイル（EXE）に固める。

やること: `render_site.py` で docs/_site を作り直し、それを丸ごと同梱した
`serve_site.py` を PyInstaller で 1ファイルに固める。できた EXE は

  1. 自分の中の _site を一時展開して 127.0.0.1 で配信し
  2. 既定ブラウザを開く

ので、**渡す相手に Python も Quarto も要らない**。各自が自分のローカルホストで
自分用に立てる形なので、共有サーバも社内公開も不要（＝仕様書を外に出さない）。

使い方（開発機）:
    python build_viewer.py --root .                 # → <root>/dist/<プロジェクト>-wbs[.exe]
    python build_viewer.py --root . --no-render     # 既存の docs/_site をそのまま同梱
    python build_viewer.py --root . --name tax-wbs --port 8300
    python build_viewer.py --root . --no-embed      # EXE の隣の _site を配信するビューアだけ作る
    python build_viewer.py --root . --onedir        # 1ファイルにせずフォルダ配布（起動が速い）

前提:
    pip install pyinstaller

注意:
  - **クロスコンパイルはできない**。Windows 用 .exe は Windows 上で、
    Linux 用バイナリは Linux 上でビルドすること（PyInstaller の仕様）。
  - EXE の中の仕様書は**ビルド時点のスナップショット**。進捗が動いたら作り直す。
  - 署名なし 1ファイル EXE は SmartScreen / 一部のウイルス対策に止められることがある。
    社内配布で引っかかるなら `--onedir` でフォルダ配布にするか、社内の署名を当てる。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENTRY = HERE / "serve_site.py"


def default_port(key: str) -> int:
    return 8100 + zlib.crc32(key.encode("utf-8")) % 800     # serve_site.py と同じ規則


def project_name(root: Path) -> str:
    """functions.json の project.name（なければディレクトリ名）。"""
    try:
        data = json.loads((root / "data" / "functions.json").read_text(encoding="utf-8-sig"))
        return data.get("project", {}).get("name") or root.name
    except (OSError, ValueError):
        return root.name


def slug(*candidates: str) -> str:
    """EXE 名に使える形に落とす。プロジェクト名が日本語だけなら次の候補（ディレクトリ名）へ。"""
    for name in candidates:
        out = "".join(c if (c.isascii() and (c.isalnum() or c in "-_")) else "-" for c in name)
        out = "-".join(filter(None, out.split("-"))).lower()
        if out:
            return out
    return "wbs"


def check_pyinstaller() -> None:
    r = subprocess.run([sys.executable, "-m", "PyInstaller", "--version"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("error: PyInstaller がない。`pip install pyinstaller` で入れる")
    print(f"PyInstaller {r.stdout.strip()} / Python {sys.version.split()[0]} / {sys.platform}")


def do_render(root: Path) -> None:
    r = subprocess.run([sys.executable, str(HERE / "render_site.py"), "--root", str(root)])
    if r.returncode != 0:
        sys.exit(f"error: render_site.py 失敗（exit={r.returncode}）。--no-render で既存の _site を使うこともできる")


def cut_web_fonts(stage: Path) -> int:
    """CSS 先頭の Google Fonts への @import を落として、完全にオフラインで開けるようにする。

    Quarto の bootswatch テーマ（cosmo 等）は `@import "https://fonts.googleapis.com/…"` を
    埋める。閉じたネットワークの PC で開くと毎回そこで待たされる上、社内資料を見ただけで
    外に通信が飛ぶ。フォント指定には Segoe UI 等のフォールバックが並んでいるので、
    import を消しても見た目はほぼ変わらない（オフライン時の表示と同じになる）。
    """
    # min.css では `@import"https://…";` と空白なしで並ぶので \s* にする
    pat = re.compile(r"""@import\s*(?:url\(\s*)?["']https?://fonts\.(?:googleapis|gstatic)\.com[^"']*["']\s*\)?\s*;""")
    n = 0
    for css in stage.rglob("*.css"):
        body = css.read_text(encoding="utf-8", errors="ignore")
        cut = pat.sub("", body)
        if cut != body:
            css.write_text(cut, encoding="utf-8")
            n += 1
    return n


def stage_site(site: Path, name: str, port: int, work: Path) -> Path:
    """_site を作業領域へコピーし、EXE の既定値（プロジェクト名・ポート）を書き込む。"""
    stage = work / "_site"
    shutil.copytree(site, stage)
    (stage / ".viewer.json").write_text(
        json.dumps({"name": name, "port": port}, ensure_ascii=False), encoding="utf-8")
    n = sum(1 for p in stage.rglob("*") if p.is_file())
    size = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file())
    print(f"同梱: {site}（{n} ファイル / {size / 1e6:.1f} MB）")
    cut = cut_web_fonts(stage)
    if cut:
        print(f"外部フォント参照を除去: {cut} ファイル（オフラインで開けるように）")
    return stage


def main() -> None:
    ap = argparse.ArgumentParser(description="WBS/仕様書サイトを単体実行ファイルに固める")
    ap.add_argument("--root", default=".", help="対象プロジェクトのルート")
    ap.add_argument("--name", help="実行ファイル名（既定: <プロジェクト>-wbs）")
    ap.add_argument("--port", type=int, help="EXE の既定ポート（既定: プロジェクト名から固定）")
    ap.add_argument("--dist", help="出力先（既定: <root>/dist）")
    ap.add_argument("--no-render", dest="render", action="store_false",
                    help="レンダリングせず既存の docs/_site を同梱する")
    ap.add_argument("--no-embed", dest="embed", action="store_false",
                    help="サイトを同梱しない（EXE の隣の _site を配信するビューアになる）")
    ap.add_argument("--onedir", action="store_true", help="1ファイルにせずフォルダで出力する")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not ENTRY.exists():
        sys.exit(f"error: {ENTRY} がない（このスクリプトは legacy-reverse/scripts/ の中で動かす）")
    check_pyinstaller()

    name = project_name(root)
    exe_name = args.name or f"{slug(name, root.name)}-wbs"
    port = args.port if args.port is not None else default_port(name)
    dist = Path(args.dist).expanduser().resolve() if args.dist else root / "dist"

    if args.embed and args.render:
        do_render(root)

    work = Path(tempfile.mkdtemp(prefix="wbs-viewer-"))
    try:
        cmd = [sys.executable, "-m", "PyInstaller",
               "--onedir" if args.onedir else "--onefile",
               "--console", "--noconfirm", "--clean",
               "--name", exe_name,
               "--distpath", str(dist),
               "--workpath", str(work / "build"),
               "--specpath", str(work),
               "--exclude-module", "tkinter",      # webbrowser 経由で引っ張られる。GUI は使わない
               "--exclude-module", "PyInstaller"]  # render_site.py 経由の取り込みを防ぐ
        if args.embed:
            site = root / "docs" / "_site"
            if not (site / "index.html").exists():
                sys.exit(f"error: {site}/index.html がない。--no-render を外すか render_site.py を先に実行する")
            stage = stage_site(site, name, port, work)
            cmd += ["--add-data", f"{stage}{os.pathsep}_site"]
        else:
            print("同梱なし: EXE の隣に _site フォルダを置いて配る形になる")
        cmd.append(str(ENTRY))

        print("\nビルド中…（初回は 1〜2 分かかる）")
        r = subprocess.run(cmd)
        if r.returncode != 0:
            sys.exit(f"error: PyInstaller 失敗（exit={r.returncode}）")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    produced = dist / (f"{exe_name}.exe" if os.name == "nt" else exe_name)
    if args.onedir:
        produced = dist / exe_name / produced.name
    if not produced.exists():                       # 命名規則が変わった場合の保険
        cands = [p for p in dist.rglob(f"{exe_name}*") if p.is_file()]
        produced = cands[0] if cands else produced

    print(f"\nwrote {produced}"
          + (f"（{produced.stat().st_size / 1e6:.1f} MB）" if produced.exists() else " ← 見つからない"))
    if args.embed:
        print(f"起動すると http://127.0.0.1:{port}/ で配信してブラウザが開く（Ctrl+C で停止）")
    else:
        print("配布時は実行ファイルの隣に docs/_site を _site という名前で置く"
              "（ポートは置いたフォルダ名から決まる。固定したいなら --port で起動する）")
    if args.onedir:
        print(f"配布するのは {dist / exe_name} フォルダ一式（中の実行ファイル単体では動かない）")
    if os.name != "nt":
        print("note: これは今のプラットフォーム用のバイナリ。Windows 用 .exe は Windows 上でビルドすること")


if __name__ == "__main__":
    main()
