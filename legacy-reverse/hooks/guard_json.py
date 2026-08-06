#!/usr/bin/env python3
"""PostToolUse hook: .json ファイルの編集直後に構文検証し、壊れていたら差し戻す。

自動実行（①〜⑦の headless Claude）が functions.json 等を編集した際、
末尾カンマなどで JSON を壊したまま次へ進むと、以降の全走査
（ledger.Project → load_json）が失敗してバッチごと止まる。
編集の直後（PostToolUse）に json.loads で検証し、NG なら exit 2 + stderr で
エージェント自身にその場で修正させる（人手の復旧を待たせない）。
settings-example.json の設定でプロジェクトに登録する。
"""
import json
import sys
from pathlib import Path


def main() -> None:
    # Windows コンソール(cp932)での文字化け防止
    for stream in (sys.stderr, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if payload.get("tool_name", "") not in ("Edit", "Write", "NotebookEdit"):
        sys.exit(0)
    file_path = str(payload.get("tool_input", {}).get("file_path", ""))
    if not file_path.lower().endswith(".json"):
        sys.exit(0)
    p = Path(file_path)
    if not p.is_file():
        sys.exit(0)
    try:
        json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        line = (p.read_text(encoding="utf-8-sig").splitlines() or [""])[
            max(e.lineno - 1, 0)] if e.lineno else ""
        print(
            f"この編集で {p.name} が JSON として壊れました（{e.lineno}行{e.colno}列: {e.msg}）。\n"
            f"  該当行: {line.strip()[:120]}\n"
            "よくある原因は配列・オブジェクト末尾の余分なカンマです。"
            "このファイルはパイプライン全体が毎回読む正データなので、"
            "次の作業に進む前に必ず構文エラーを修正してください。",
            file=sys.stderr,
        )
        sys.exit(2)
    except OSError:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
