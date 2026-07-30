#!/usr/bin/env python3
"""レンダリング済みの docs/_site をローカルホストで配信する（WBSがトップ）。

`python -m http.server` を直接使わずにこれを通す理由:

  - **プロジェクトごとに固定ポート**: ポートをプロジェクト名から決めるので、複数の
    移植プロジェクトを同時に立てても衝突しない（衝突したら空きへずらす）。
    レビュアーは毎回同じ URL をブックマークできる。
  - **127.0.0.1 のみに bind**: 仕様書は社内資料なので、既定で LAN に漏らさない。
    共有したいときだけ明示的に `--host 0.0.0.0`（警告つき）。
  - **キャッシュ無効**: 再レンダリング後にブラウザの更新だけで最新が見える
    （no-store。ブラウザが古い .html/.js を握ったままになるのを防ぐ）。
  - **--watch**: docs/ の .md を書き換えたら自動で再レンダリング。
    ⑤の結果を貼りながら WBS を眺める用。
  - **EXE 化できる**: このファイルが PyInstaller のエントリポイント。
    `build_viewer.py` で _site を同梱した単体実行ファイルにできるので、
    Python も Quarto も入っていない相手に「実行するだけ」で渡せる。

使い方（開発機）:
    python serve_site.py --root .              # docs/_site を配信してブラウザを開く
    python serve_site.py --root . --render     # 先に render_site.py で作り直してから配信
    python serve_site.py --root . --watch      # docs/ の変更を監視して自動再レンダリング
    python serve_site.py --root . --port 8800 --no-open

配布用 EXE（_site 同梱。build_viewer.py が作る）:
    wbs-viewer.exe                             # ダブルクリック → ブラウザが開く
    wbs-viewer.exe --port 9000 --no-open
"""
import argparse
import functools
import http.server
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
import zlib
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
BUNDLE = Path(getattr(sys, "_MEIPASS", "")) if FROZEN else None
CONFIG_NAME = ".viewer.json"          # build_viewer.py が同梱サイトに書く { name, port }
WATCH_SUFFIXES = {".md", ".qmd", ".css", ".yml", ".yaml", ".json", ".png", ".svg"}

# 配信するものは Quarto の出力（html/js/css/woff/json）。Windows のレジストリ由来の
# mimetypes は当てにならない（.js が text/plain になって mermaid が動かない環境がある）
EXTRA_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".map": "application/json; charset=utf-8",
}


def use_utf8_console() -> None:
    """Windows の cp932 コンソールで日本語を print しても落ちないようにする。

    line_buffering は必須。EXE の出力をファイルやパイプに流したとき、既定のブロック
    バッファだと URL が表示されないまま溜まる（サーバは動いているのに無反応に見える）。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, OSError):
            pass


# --- サイトの場所とポート -------------------------------------------------

def resolve_site(args) -> tuple[Path, list[Path]]:
    """配信するディレクトリを決める。EXE では同梱サイト → exe の隣 → --root の順。

    戻り値は (採用したディレクトリ, 探した場所)。見つからなかったときの案内に使う。
    """
    if args.site:
        return Path(args.site).expanduser().resolve(), []
    tried = []
    if FROZEN:
        tried.append(BUNDLE / "_site")                       # 同梱版
        tried.append(Path(sys.executable).resolve().parent / "_site")   # EXE の隣
    tried.append(Path(args.root).expanduser().resolve() / "docs" / "_site")
    for cand in tried:
        if (cand / "index.html").exists():
            return cand, tried
    return tried[-1], tried


def site_config(site: Path) -> dict:
    try:
        return json.loads((site / CONFIG_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def lan_address() -> str | None:
    """LAN 側の自分の IP。--host 0.0.0.0 のときに配る URL を出すために使う。

    gethostbyname(gethostname()) は環境次第で 127.0.0.1 しか返さないので、
    外向きの経路を選ばせて出口側のアドレスを読む（UDP なのでパケットは飛ばない）。
    """
    for probe in ("192.0.2.1", "8.8.8.8"):           # TEST-NET-1 → 到達不能でも経路選択はされる
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((probe, 9))
                ip = s.getsockname()[0]
            if not ip.startswith("127."):
                return ip
        except OSError:
            continue
    return None


def default_port(key: str) -> int:
    """プロジェクト名から 8100-8899 の固定ポートを決める（プロジェクトごとに別ポート）。"""
    return 8100 + zlib.crc32(key.encode("utf-8")) % 800


def bind_server(host: str, port: int, handler, tries: int = 40):
    """port から順に空きを探して bind する。0 は OS 任せ。"""
    last = None
    for candidate in ([0] if port == 0 else range(port, port + tries)):
        try:
            return http.server.ThreadingHTTPServer((host, candidate), handler)
        except OSError as e:
            last = e
    raise SystemExit(f"error: ポート {port}〜{port + tries - 1} が全部埋まっている（{last}）")


# --- HTTP ハンドラ --------------------------------------------------------

class Handler(http.server.SimpleHTTPRequestHandler):
    server_version = "wbs-viewer"
    sys_version = ""
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, **EXTRA_TYPES}
    verbose = False

    def do_GET(self) -> None:
        if self.path == "/favicon.ico" and not (Path(self.directory) / "favicon.ico").exists():
            self.send_response(204)                  # ブラウザが必ず取りに来る。404 でログを汚さない
            self.end_headers()
            return
        super().do_GET()

    def end_headers(self) -> None:
        # 再レンダリング後にリロードだけで最新が出るように、一切キャッシュさせない
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        status = args[1] if len(args) > 1 else ""
        if self.verbose or not str(status).startswith("2"):
            sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")

    def log_error(self, fmt: str, *args) -> None:      # 404 等は上の log_message に集約
        self.log_message(fmt, *args)


# --- レンダリング（開発機のみ。EXE には quarto が入っていない）-----------

def render(root: Path) -> bool:
    if FROZEN:
        print("note: EXE では再レンダリングできない（Quarto が必要）。開発機で作り直して EXE を作り直すこと",
              file=sys.stderr)
        return False
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import render_site                                   # 隣の render_site.py を再利用（パス設定後）

    docs = root / "docs"
    if not docs.is_dir():
        print(f"error: {docs} がない", file=sys.stderr)
        return False
    work = docs / "_sitework"
    n = render_site.build_shadow(docs, work)
    r = subprocess.run([render_site.find_quarto(), "render", str(work)])
    shutil.rmtree(work, ignore_errors=True)
    if r.returncode != 0:
        print(f"error: quarto render 失敗（exit={r.returncode}）", file=sys.stderr)
        return False
    print(f"rendered {docs / '_site'}（{n} ページ）")
    return True


def watch(root: Path, interval: float = 1.0) -> None:
    """docs/ の成果物が変わったら再レンダリングする（--watch。デーモンスレッドで回す）。"""
    docs = root / "docs"

    def signature() -> frozenset:
        out = set()
        for p in docs.rglob("*"):
            rel = p.relative_to(docs)
            if any(part.startswith(("_", ".")) for part in rel.parts):
                continue                                  # _site / _sitework / .quarto
            if p.is_file() and p.suffix in WATCH_SUFFIXES:
                out.add((str(rel), p.stat().st_mtime_ns))
        return frozenset(out)

    prev = signature()
    while True:
        time.sleep(interval)
        try:
            cur = signature()
        except OSError:
            continue                                      # 書き込み途中に消えた等は次回に回す
        if cur == prev:
            continue
        time.sleep(0.6)                                    # 連続保存をまとめる
        try:
            cur = signature()
        except OSError:
            continue
        prev = cur
        print("\n変更を検知 → 再レンダリング中…")
        render(root)
        print("完了。ブラウザをリロードしてください")


# --- main -----------------------------------------------------------------

def main() -> None:
    use_utf8_console()
    ap = argparse.ArgumentParser(description="WBS/仕様書サイトをローカルホストで配信する")
    ap.add_argument("--root", default=".", help="対象プロジェクトのルート（既定: カレント）")
    ap.add_argument("--site", help="配信するディレクトリを直接指定（既定: <root>/docs/_site）")
    ap.add_argument("--port", type=int, help="待受ポート（既定: プロジェクト名から固定。0 で OS 任せ）")
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind するアドレス（既定: 127.0.0.1。LAN に公開するなら 0.0.0.0）")
    ap.add_argument("--render", action="store_true", help="配信前に render_site.py で作り直す")
    ap.add_argument("--watch", action="store_true", help="docs/ を監視して自動再レンダリング（--render を含む）")
    ap.add_argument("--no-open", dest="open", action="store_false", help="ブラウザを自動で開かない")
    ap.add_argument("--verbose", action="store_true", help="全リクエストをログに出す")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    if args.render or args.watch:
        ok = render(root)                                # EXE では note を出して False
        if FROZEN:
            args.watch = False                           # 再レンダリングできないので監視も無意味
        elif not ok:
            sys.exit(1)

    site, tried = resolve_site(args)
    if not (site / "index.html").exists():
        where = "".join(f"\n  探した場所: {t}" for t in tried) or f"\n  探した場所: {site}"
        hint = ("実行ファイルの隣に _site フォルダを置く（同梱版なら build_viewer.py で作り直す）"
                if FROZEN else
                f"先に `python {Path(__file__).name} --root {args.root} --render` か "
                f"`python render_site.py --root {args.root}` を実行する")
        sys.exit(f"error: index.html が見つからない。{hint}{where}")

    cfg = site_config(site)
    name = cfg.get("name") or root.name
    port = args.port if args.port is not None else cfg.get("port") or default_port(name)

    Handler.verbose = args.verbose
    httpd = bind_server(args.host, port, functools.partial(Handler, directory=str(site)))
    actual = httpd.server_address[1]
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{actual}/"

    print(f"\n  {name} の WBS/仕様書サイト")
    print(f"  配信元: {site}")
    print(f"  URL:    {url}")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        lan = lan_address()
        print(f"  LAN:    http://{lan}:{actual}/" if lan else "  LAN:    このマシンの IP でも開ける")
        print("  warning: ローカルホスト以外にも公開中。仕様書は社内資料である点に注意")
    if args.watch:
        print("  監視:   docs/ の変更で自動再レンダリング")
    print("  終了:   Ctrl+C\n")

    if args.watch:
        threading.Thread(target=watch, args=(root,), daemon=True).start()
    if args.open:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()   # bind 後に開く

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
