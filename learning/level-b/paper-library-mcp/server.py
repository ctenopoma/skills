"""手元の PDF フォルダを検索できる「文献ライブラリ」MCP サーバ(レベルB)。

- 準備:      pip install "mcp[cli]" pypdf
- STDIO:     python server.py
- HTTP:      python server.py --http   (http://127.0.0.1:8000/mcp)

ライブラリの場所は既定で ~/paper-library(環境変数 PAPER_LIBRARY_DIR で変更可)。
ここに PDF を入れて reindex すると、キーワード検索できるようになる。

レベルAとの違いは2つ:
  1. 入力がバイナリ(PDF)— pypdf でテキスト抽出してから SQLite に入れる
  2. tools に加えて resources を使う — 検索は「ツール」、本文全文は「リソース」
"""

import os
import sqlite3
import sys
from pathlib import Path

from pypdf import PdfReader

from mcp.server.fastmcp import FastMCP

LIB_DIR = Path(os.environ.get("PAPER_LIBRARY_DIR", str(Path.home() / "paper-library")))
DB_PATH = LIB_DIR / ".index.db"

mcp = FastMCP("paper-library")


def _db() -> sqlite3.Connection:
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "CREATE TABLE IF NOT EXISTS papers("
        "filename TEXT PRIMARY KEY, title TEXT, pages INTEGER, text TEXT)"
    )
    return con


def _extract(pdf_path: Path) -> tuple[str, int, str]:
    """PDF からタイトル・ページ数・本文テキストを取り出す。"""
    reader = PdfReader(pdf_path)
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    title = ""
    if reader.metadata and reader.metadata.title:
        title = str(reader.metadata.title).strip()
    if not title:  # メタデータにタイトルがなければ本文の最初の行で代用
        title = next(
            (ln.strip() for ln in text.splitlines() if ln.strip()), pdf_path.stem
        )[:120]
    return title, len(reader.pages), text


@mcp.tool()
def reindex() -> str:
    """ライブラリフォルダ内の PDF を走査してインデックスを作り直す。
    PDF を追加・削除したあと、検索の前に一度呼ぶ。
    """
    con = _db()
    con.execute("DELETE FROM papers")
    count, errors = 0, []
    for pdf in sorted(LIB_DIR.glob("*.pdf")):
        try:
            title, pages, text = _extract(pdf)
            con.execute(
                "INSERT INTO papers VALUES (?, ?, ?, ?)", (pdf.name, title, pages, text)
            )
            count += 1
        except Exception as e:
            errors.append(f"{pdf.name}: {e}")
    con.commit()
    msg = f"{count} 件の PDF をインデックスした(場所: {LIB_DIR})"
    if errors:
        msg += "\n読めなかったファイル:\n" + "\n".join(errors)
    return msg


@mcp.tool()
def list_papers() -> str:
    """インデックス済みの文献一覧(ファイル名・タイトル・ページ数)を返す。"""
    rows = _db().execute(
        "SELECT filename, title, pages FROM papers ORDER BY filename"
    ).fetchall()
    if not rows:
        return f"インデックスが空。{LIB_DIR} に PDF を置いて reindex を呼ぶ。"
    return "\n".join(f"- {f} | {t} ({p}p)" for f, t, p in rows)


@mcp.tool()
def search_papers(query: str) -> str:
    """キーワードで文献を全文検索し、ヒット箇所の前後(スニペット)を返す。
    本文の全文が必要なときはリソース paper://<ファイル名> を読む。

    Args:
        query: 検索キーワード(部分一致)
    """
    rows = _db().execute(
        "SELECT filename, title, text FROM papers "
        "WHERE title LIKE ? OR text LIKE ? ORDER BY filename",
        (f"%{query}%", f"%{query}%"),
    ).fetchall()
    if not rows:
        return f"「{query}」に一致する文献はない"
    out = []
    for filename, title, text in rows:
        pos = text.lower().find(query.lower())
        snippet = (
            text[max(0, pos - 80) : pos + 80].replace("\n", " ") if pos >= 0 else ""
        )
        out.append(f"- {filename} | {title}\n  …{snippet}…")
    out.append(f"\n(全文を読むにはリソース paper://<ファイル名> を参照)")
    return "\n".join(out)


@mcp.resource("paper://{filename}")
def read_paper(filename: str) -> str:
    """指定した PDF の本文テキスト全文。要約や精読はこれを読んでから行う。"""
    row = _db().execute(
        "SELECT text FROM papers WHERE filename = ?", (filename,)
    ).fetchone()
    if row is None:
        return f"{filename} はインデックスにない。reindex 済みか確認する。"
    return row[0]


if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http")
    else:
        mcp.run()  # 既定は STDIO
