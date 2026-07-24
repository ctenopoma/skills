#!/usr/bin/env python3
"""④のスタブ検出。検出ゼロなら exit 0、あれば一覧を表示して exit 1。

検出対象:
  - 本体が pass / Ellipsis / docstringのみ の関数
  - raise NotImplementedError
  - TODO / FIXME コメント

使い方: python check_stubs.py <file.py> [<file.py> ...]
"""
import ast
import re
import sys
from pathlib import Path


def check_file(path: Path) -> list:
    src = path.read_text(encoding="utf-8-sig")
    problems = []
    for i, line in enumerate(src.splitlines(), 1):
        if re.search(r"#.*\b(TODO|FIXME)\b", line):
            problems.append((i, f"TODO/FIXME コメント: {line.strip()[:60]}"))
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [(e.lineno or 0, f"構文エラー: {e.msg}")]
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise):
            name = ""
            exc = node.exc
            if isinstance(exc, ast.Call):
                exc = exc.func
            if isinstance(exc, ast.Name):
                name = exc.id
            if name == "NotImplementedError":
                problems.append((node.lineno, "raise NotImplementedError"))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body = body[1:]  # docstring を除く
            if not body or all(
                isinstance(st, ast.Pass)
                or (isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant)
                    and st.value.value is Ellipsis)
                for st in body
            ):
                problems.append((node.lineno, f"空実装の関数: {node.name}"))
    return problems


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: check_stubs.py <file.py> ...")
    found = False
    for arg in sys.argv[1:]:
        for lineno, msg in check_file(Path(arg)):
            found = True
            print(f"{arg}:{lineno}: {msg}")
    if found:
        print("\nスタブ検出 → ④は未完了。埋められない場合は spec-gap ISSUE を起票して停止せよ")
        sys.exit(1)
    print("stubs: なし")


if __name__ == "__main__":
    main()
