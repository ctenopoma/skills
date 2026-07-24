#!/usr/bin/env python3
"""PreToolUse hook: ④⑤フェーズ中の tests/ への Edit/Write を拒否する。

.legacy-reverse/state.json の phase が "4" または "5" のとき、
tests/ 配下への Edit/Write/NotebookEdit をブロックする（exit 2 + stderr）。
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
    tool = payload.get("tool_name", "")
    if tool not in ("Edit", "Write", "NotebookEdit"):
        sys.exit(0)
    file_path = str(payload.get("tool_input", {}).get("file_path", "")).replace("\\", "/")
    cwd = payload.get("cwd", ".")
    state_path = Path(cwd) / ".legacy-reverse" / "state.json"
    if not state_path.exists():
        sys.exit(0)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        sys.exit(0)
    if state.get("phase") not in ("4", "5"):
        sys.exit(0)
    root = str(Path(cwd).resolve()).replace("\\", "/")
    rel = file_path[len(root):].lstrip("/") if file_path.startswith(root) else file_path
    if rel.startswith("tests/") or "/tests/" in rel:
        print(
            f"フェーズ{state['phase']}中は tests/ を編集できません。"
            "テスト側に問題があると思う場合は、証拠付きで ISSUE を起票し"
            "人の承認を待ってください（正規経路: ISSUE→承認→②③再生成）。",
            file=sys.stderr,
        )
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
