# レベルB-1: plot-mcp — 画像を返す MCP サーバ

> **ハンズオンは紙芝居版で**: [../../slides/b1-plot.html](../../slides/b1-plot.html) をブラウザで開く。
> この README は同内容のテキスト版(記録用)。

レベルAのツールはすべてテキストを返していた。この章では **戻り値が画像 (PNG)** になる
MCP サーバを作る。CSV を渡すとグラフを描いて返すサーバで、探索的なデータの下見に使える。

## 1. レベルAとの違い: コンテンツ型

MCP のツールが返せるのはテキストだけではない。主なコンテンツ型:

| 型 | 中身 | 例 |
| --- | --- | --- |
| TextContent | 文字列 | レベルAの全ツール |
| **ImageContent** | base64 の画像 + MIME タイプ | この章のグラフ |
| EmbeddedResource | リソース参照 | B-2 で扱う |

Python SDK では `Image` 型を返すだけで、SDK が base64 化と MIME タイプ付与をやってくれる。
クライアント側の LLM は返ってきた画像を**見る**ことができる。つまり
「グラフを描かせて、そのグラフを見て考察させる」が1往復でできる。

## 2. コードの骨格

完成品: [server.py](server.py)。要点は2つ。

```python
from mcp.server.fastmcp import FastMCP, Image

def _fig_to_image(fig) -> Image:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return Image(data=buf.getvalue(), format="png")

@mcp.tool()
def plot_csv(csv_path: str, x: str, y: str, kind: str = "line") -> Image:
    """CSV から列を選んでグラフを描き、PNG 画像で返す。..."""
```

ツールは3つ。docstring で「使う順番」を誘導している点に注目
(describe で下見 → plot / histogram で描画)。

- `describe_csv` — 行数・列名・基本統計量(テキスト)
- `plot_csv` — 折れ線 / 散布図 / 棒グラフ(画像)
- `histogram` — 1列の分布(画像)

`@mcp.tool()` + docstring という作りはレベルAと**完全に同じ**。
変わったのは戻り値の型だけ。

## 3. 動かす

```bash
pip install "mcp[cli]" matplotlib pandas
claude mcp add plot -- python C:/work_space/skills/learning/level-b/plot-mcp/server.py
```

サンプルデータ [sample/timeseries.csv](sample/timeseries.csv)(day, sensor_a, sensor_b の60日分)で動作確認:

1. 「`.../sample/timeseries.csv` ってどんなデータ?」→ `describe_csv`
2. 「センサー2つの推移をグラフにして」→ `plot_csv` → **会話に画像が出る**
3. 「sensor_b の分布は?外れ値ある?」→ `histogram` → 画像を見た上での考察が返る

手元の実データ CSV に差し替えて試すとよい。

## 4. 改造課題

- `kind` に `"box"`(箱ひげ図)を追加する
- 日本語の列名でラベルが文字化けする場合の対処(matplotlib のフォント設定)を入れる
- `save_plot(csv_path, x, y, out_path)` を追加し、画像で返すだけでなくファイルにも保存できるようにする
- 「describe → 分布 → 関係」の順で下見する手順を Skill 化する(レベルAの Step 3 の応用)

## 5. 他ツールでの利用

レベルAと同じ。`--http` で起動すれば OpenWebUI 等からも使える。
画像コンテンツの表示はクライアントの対応状況に依存する(Claude Code / Claude Desktop は表示できる)。

---

次: [レベルB-2: paper-library-mcp](../paper-library-mcp/README.md)
