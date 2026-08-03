"""CSV を渡すとグラフを描いて「画像」で返す MCP サーバ(レベルB)。

- 準備:      pip install "mcp[cli]" matplotlib pandas
- STDIO:     python server.py
- HTTP:      python server.py --http   (http://127.0.0.1:8000/mcp)

レベルAの ToDo サーバとの違いは「ツールの戻り値が画像 (Image) になる」こと。
それ以外の作り(@mcp.tool() と docstring)は何も変わらない。
"""

import io
import sys

import matplotlib

matplotlib.use("Agg")  # 画面を持たない環境でも描画できるバックエンド

import matplotlib.pyplot as plt
import pandas as pd

from mcp.server.fastmcp import FastMCP, Image

mcp = FastMCP("plot")


def _fig_to_image(fig) -> Image:
    """matplotlib の Figure を PNG バイト列にして MCP の Image 型で返す。"""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return Image(data=buf.getvalue(), format="png")


@mcp.tool()
def describe_csv(csv_path: str) -> str:
    """CSV の概要(行数・列名・基本統計量)をテキストで返す。
    グラフを描く前に、どんな列があるかの下見に使う。

    Args:
        csv_path: CSV ファイルのパス
    """
    df = pd.read_csv(csv_path)
    return (
        f"行数: {len(df)}\n"
        f"列: {', '.join(df.columns)}\n\n"
        f"{df.describe(include='all').to_string()}"
    )


@mcp.tool()
def plot_csv(csv_path: str, x: str, y: str, kind: str = "line") -> Image:
    """CSV から列を選んでグラフを描き、PNG 画像で返す。
    時系列や2変数の関係を見たいときに使う。

    Args:
        csv_path: CSV ファイルのパス
        x: 横軸にする列名
        y: 縦軸にする列名(カンマ区切りで複数指定できる)
        kind: グラフの種類 "line" / "scatter" / "bar"
    """
    df = pd.read_csv(csv_path)
    fig, ax = plt.subplots(figsize=(7, 4))
    for col in [c.strip() for c in y.split(",")]:
        if kind == "scatter":
            ax.scatter(df[x], df[col], label=col, s=14)
        elif kind == "bar":
            ax.bar(df[x], df[col], label=col)
        else:
            ax.plot(df[x], df[col], label=col)
    ax.set_xlabel(x)
    ax.legend()
    ax.grid(alpha=0.3)
    return _fig_to_image(fig)


@mcp.tool()
def histogram(csv_path: str, column: str, bins: int = 20) -> Image:
    """CSV の1列のヒストグラムを PNG 画像で返す。分布の形や外れ値の確認に使う。

    Args:
        csv_path: CSV ファイルのパス
        column: 対象の列名
        bins: ビンの数(既定 20)
    """
    df = pd.read_csv(csv_path)
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.hist(df[column].dropna(), bins=bins, edgecolor="white")
    ax.set_xlabel(column)
    ax.set_ylabel("count")
    ax.grid(alpha=0.3)
    return _fig_to_image(fig)


if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http")
    else:
        mcp.run()  # 既定は STDIO
