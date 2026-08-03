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

**自分で書いて動かす**(紙芝居版に写経用コードと仕様を掲載)。

1. **写経(骨格)**: `my-plot/server.py` を新規作成し、骨格+`_fig_to_image`
   (Figure → PNG バイト列 → `Image` 型で return)+`describe_csv`(下見用・テキスト)を写経する
2. **仕様から自作(2本)**: コードを見ずに実装する。Figure を作って `_fig_to_image(fig)` を返すだけ
   - `plot_csv(csv_path, x, y, kind="line")` — "line" / "scatter" / "bar" の3種
   - `histogram(csv_path, column, bins=20)` — 1列の分布。欠損は `dropna()` してから
   - docstring には「いつ使うか」も書く(histogram は「外れ値の確認に」等)
3. **動作確認**: `pip install "mcp[cli]" matplotlib pandas` →
   `claude mcp add plot -- python (my-plot/server.py の絶対パス)` → 再起動。
   サンプルデータ [sample/timeseries.csv](sample/timeseries.csv) で:
   - 「どんなデータ?」→ `describe_csv` / 「センサー2つの推移をグラフに」→ **会話に画像が出る** /
     「sensor_b の分布は?外れ値ある?」→ histogram の画像を Claude 自身が見て考察する
4. **答え合わせ**: 完成例 [server.py](server.py) と見比べる。観点:
   y のカンマ区切り複数列対応(+凡例)/ `plt.close(fig)` の後始末(サーバは長生きする)/
   docstring の役割づけ(describe「下見に使う」→ 描画、の順番誘導)

`@mcp.tool()` + docstring という作りはレベルAと**完全に同じ**。変わったのは戻り値の型だけ。
仕上げに手元の実データ CSV で一巡させるとよい。

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
