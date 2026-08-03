"""自己完結HTMLを PDF に書き出す(Microsoft Edge のヘッドレス印刷を使用。追加インストール不要)。

使い方:
    python to_pdf.py 入力.html 出力.pdf

スライド(deck.css)は印刷用CSSで全スライドが縦に並んで出力される。
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_edge() -> str:
    exe = shutil.which("msedge")
    if exe:
        return exe
    for path in EDGE_CANDIDATES:
        if Path(path).exists():
            return path
    sys.exit("エラー: msedge.exe が見つからない。Chrome があれば chrome.exe でも同じ引数で動く。")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input")
    ap.add_argument("output")
    args = ap.parse_args()

    src = Path(args.input).resolve()
    if not src.exists():
        sys.exit(f"エラー: {src} がない")
    dst = Path(args.output).resolve()

    # 専用プロファイルで起動する。既定プロファイルだと起動済みの Edge に処理が
    # 委譲されてしまうため。また Edge はプロセス終了の数秒後に PDF を書き出す
    # ことがあるので、ファイルの出現をポーリングして待つ
    with tempfile.TemporaryDirectory(
        prefix="htmlcraft-edge-", ignore_cleanup_errors=True
    ) as profile:
        cmd = [
            find_edge(),
            "--headless",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--user-data-dir={profile}",
            f"--print-to-pdf={dst}",
            src.as_uri(),
        ]
        subprocess.run(cmd, check=True, timeout=120)
        for _ in range(30):
            if dst.exists() and dst.stat().st_size > 0:
                break
            time.sleep(1)
    if not dst.exists():
        sys.exit("エラー: PDF が生成されなかった")
    print(f"PDF を書き出した: {dst} ({dst.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
