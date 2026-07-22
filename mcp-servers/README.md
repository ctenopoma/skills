# mcp-servers

業務用の内製 MCP サーバ置き場(スキルではないので Claude Code のスキル読み込み対象外)。

| サーバ | 用途 | 依存 |
| --- | --- | --- |
| [data-mcp](data-mcp/) | DuckDB ベースのデータ処理。CSV/Parquet の読み込み・プロファイル・SQL 集計・書き出し。大きいデータをコンテキストに入れないための道具 | `pip install "mcp[cli]" duckdb` |
| [stats-mcp](stats-mcp/) | 統計計算。群間比較(t検定/ANOVA)・独立性検定・相関・正規性検定・ベースラインモデル(RandomForest+CV)。LLM に計算させないための道具 | `pip install "mcp[cli]" pandas scipy scikit-learn` |

## 登録(パスは各自の clone 先に読み替え)

```bash
claude mcp add data -- python C:/work_space/skills/mcp-servers/data-mcp/server.py
claude mcp add stats -- python C:/work_space/skills/mcp-servers/stats-mcp/server.py
```

どちらも `--http` を付けて起動すると Streamable HTTP(`http://127.0.0.1:8000/mcp`)になる。

## スキルとの連携

[eda-workflow](../eda-workflow/) スキルがこの2つ+ [plot-mcp](../learning/level-b/plot-mcp/)
を指揮して探索的データ解析を行う。plot-mcp も合わせて登録しておくとよい:

```bash
claude mcp add plot -- python C:/work_space/skills/learning/level-b/plot-mcp/server.py
```
