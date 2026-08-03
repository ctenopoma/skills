"""ToDo 管理 MCP サーバ(学習用の最小構成)。

- STDIO で起動:  python server.py
- HTTP  で起動:  python server.py --http   (http://127.0.0.1:8000/mcp)

データは ~/.todo-mcp.json に保存する。transport が変わっても
ツール定義(このファイルの本体)は一切変わらないことに注目。
"""

import json
import sys
from datetime import date
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DATA_FILE = Path.home() / ".todo-mcp.json"

mcp = FastMCP("todo")


def _load() -> list[dict]:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return []


def _save(tasks: list[dict]) -> None:
    DATA_FILE.write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@mcp.tool()
def add_task(title: str) -> str:
    """新しいタスクを追加する。ユーザーが「〜をタスクに入れて」「TODO追加」などと言ったときに使う。

    Args:
        title: タスクの内容(短い1文)
    """
    tasks = _load()
    task_id = max((t["id"] for t in tasks), default=0) + 1
    tasks.append(
        {
            "id": task_id,
            "title": title,
            "status": "open",
            "created": date.today().isoformat(),
            "completed": None,
        }
    )
    _save(tasks)
    return f"タスク {task_id} を追加した: {title}"


@mcp.tool()
def list_tasks(status: str = "open") -> str:
    """タスクの一覧を返す。ユーザーがタスクの確認・棚卸しをしたいときに使う。

    Args:
        status: "open"(未完了・既定) / "done"(完了済み) / "all"(すべて)
    """
    tasks = _load()
    if status != "all":
        tasks = [t for t in tasks if t["status"] == status]
    if not tasks:
        return f"該当するタスクはない (status={status})"
    lines = []
    for t in tasks:
        mark = "x" if t["status"] == "done" else " "
        done = f" (完了: {t['completed']})" if t["completed"] else ""
        lines.append(f"[{mark}] #{t['id']} {t['title']} (登録: {t['created']}){done}")
    return "\n".join(lines)


@mcp.tool()
def complete_task(task_id: int) -> str:
    """タスクを完了にする。ユーザーが「#1終わった」「〜を完了に」などと言ったときに使う。

    Args:
        task_id: 完了にするタスクの番号
    """
    tasks = _load()
    for t in tasks:
        if t["id"] == task_id:
            t["status"] = "done"
            t["completed"] = date.today().isoformat()
            _save(tasks)
            return f"タスク {task_id} を完了にした: {t['title']}"
    return f"タスク {task_id} が見つからない"


if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http")
    else:
        mcp.run()  # 既定は STDIO
