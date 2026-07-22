"""データ処理 MCP サーバ(DuckDB ベース)。

- 準備:      pip install "mcp[cli]" duckdb
- STDIO:     python server.py
- HTTP:      python server.py --http   (http://127.0.0.1:8000/mcp)

設計方針: 大きいデータを LLM のコンテキストに入れない。
CSV / Parquet は DuckDB のテーブルとして登録し、処理はすべてサーバ側の SQL で行う。
Claude に返すのは「スキーマ・件数・要約・先頭数十行」だけ。
"""

import sys

import duckdb

from mcp.server.fastmcp import FastMCP

MAX_ROWS = 50  # クエリ結果としてLLMに返す最大行数

mcp = FastMCP("data")

con = duckdb.connect()  # プロセス内のインメモリDB(セッション中は保持される)


def _table(rows, columns) -> str:
    """結果をMarkdown表にする。"""
    lines = ["| " + " | ".join(columns) + " |",
             "| " + " | ".join("---" for _ in columns) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(str(v) for v in r) + " |")
    return "\n".join(lines)


@mcp.tool()
def load(path: str, name: str = "") -> str:
    """CSV / Parquet ファイルをテーブルとして読み込む。分析の最初に必ず呼ぶ。

    Args:
        path: データファイルのパス(.csv / .parquet)
        name: テーブル名(省略時はファイル名から自動決定)
    """
    if not name:
        name = path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
        name = "".join(c if c.isalnum() else "_" for c in name)
    reader = "read_parquet" if path.lower().endswith(".parquet") else "read_csv_auto"
    con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM {reader}(?)", [path])
    n = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
    cols = con.execute(f"DESCRIBE {name}").fetchall()
    schema = "\n".join(f"- {c[0]}: {c[1]}" for c in cols)
    return f"テーブル '{name}' として読み込んだ({n:,} 行, {len(cols)} 列)\n{schema}"


@mcp.tool()
def profile(name: str) -> str:
    """テーブルの品質プロファイルを返す。列ごとの型・欠損数・ユニーク数・代表値。
    読み込み後の品質チェックに使う。

    Args:
        name: load で付けたテーブル名
    """
    total = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
    out = [f"テーブル '{name}': {total:,} 行"]
    for col, typ, *_ in con.execute(f"DESCRIBE {name}").fetchall():
        q = con.execute(
            f'SELECT count(*) - count("{col}"), count(DISTINCT "{col}") FROM {name}'
        ).fetchone()
        nulls, uniq = q
        if any(t in typ.upper() for t in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "BIGINT")):
            mn, mx, avg = con.execute(
                f'SELECT min("{col}"), max("{col}"), round(avg("{col}"), 4) FROM {name}'
            ).fetchone()
            detail = f"min={mn}, max={mx}, mean={avg}"
        else:
            top = con.execute(
                f'SELECT "{col}", count(*) c FROM {name} GROUP BY 1 ORDER BY c DESC LIMIT 3'
            ).fetchall()
            detail = "top: " + ", ".join(f"{v}({c})" for v, c in top)
        out.append(f"- {col} [{typ}] 欠損 {nulls} / ユニーク {uniq} / {detail}")
    return "\n".join(out)


@mcp.tool()
def query(sql: str) -> str:
    """SQL(DuckDB 方言)を実行して結果を返す。集計・結合・変換はすべてこれで行う。
    結果は最大 50 行まで。大きい結果をそのまま見ようとせず、集計してから返すこと。

    Args:
        sql: 実行する SELECT 文
    """
    res = con.execute(sql)
    columns = [d[0] for d in res.description]
    rows = res.fetchmany(MAX_ROWS + 1)
    truncated = len(rows) > MAX_ROWS
    body = _table(rows[:MAX_ROWS], columns)
    note = f"\n(先頭 {MAX_ROWS} 行のみ表示。続きが必要なら絞り込むか集計する)" if truncated else ""
    return f"{body}{note}"


@mcp.tool()
def export(sql: str, out_path: str) -> str:
    """SQL の結果をファイルに書き出す。前処理済みデータを他ツール(plot-mcp 等)に
    渡すときに使う。拡張子で形式を自動判定(.csv / .parquet)。

    Args:
        sql: 実行する SELECT 文
        out_path: 出力ファイルパス
    """
    fmt = "(FORMAT PARQUET)" if out_path.lower().endswith(".parquet") else "(HEADER, DELIMITER ',')"
    con.execute(f"COPY ({sql}) TO '{out_path}' {fmt}")
    n = con.execute(f"SELECT count(*) FROM ({sql})").fetchone()[0]
    return f"{n:,} 行を書き出した: {out_path}"


@mcp.tool()
def list_tables() -> str:
    """読み込み済みのテーブル一覧を返す。"""
    rows = con.execute("SHOW TABLES").fetchall()
    if not rows:
        return "テーブルはまだない。load でファイルを読み込む。"
    return "\n".join(f"- {r[0]}" for r in rows)


if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http")
    else:
        mcp.run()  # 既定は STDIO
