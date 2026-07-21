"""改訂箇所を git の差分から機械的に判定し、PDFに改訂バーで明示する。

    python revision.py map <file> --base <rev>      変更ブロックを JSON で出す
    python revision.py mark <file> --base <rev> -o <out.qmd>
                                                    改訂マーカーを埋めた qmd を作る

判定の粒度:
  git の差分は行単位だが、行のままだと「段落の途中1文字」と「段落まるごと」が
  区別できない。ここでは Markdown のブロック(段落・見出し・コードブロック・表・
  箇条書き)へ丸め、ブロックごとに added / modified と変更率を返す。

出力:
  mark は各ブロックの直前に `::: {.revision-added}` / `{.revision-modified}` の
  Div を開き、直後に閉じる。Typst 側(assets/revision.typ)がこの Div を
  左右の余白の改訂バーとして描く。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^#{1,6}\s")
TABLE_ROW = re.compile(r"^\s*\|")
LIST_ITEM = re.compile(r"^\s*([-*+]|\d+[.)])\s")


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def git_root(path: Path) -> Path:
    r = _run(["git", "rev-parse", "--show-toplevel"], path.parent)
    if r.returncode != 0:
        sys.exit("git リポジトリではありません。改訂バーは git 差分から作ります。")
    return Path(r.stdout.strip())


def changed_lines(file: Path, base: str) -> tuple[set[int], set[int]]:
    """base との差分から、現在のファイルにおける追加行・変更行の行番号を得る。

    unified diff の hunk を読み、+ 行の行番号を集める。同じ hunk に - 行が
    あれば「変更」、無ければ「追加」とみなす。
    """
    root = git_root(file)
    rel = file.resolve().relative_to(root).as_posix()
    r = _run(["git", "diff", "--unified=0", base, "--", rel], root)
    if r.returncode != 0:
        sys.exit(f"git diff に失敗しました: {r.stderr.strip()}")

    added, modified = set(), set()
    new_ln, removals, pending = 0, 0, []

    def flush():
        target = modified if removals else added
        target.update(pending)

    for line in r.stdout.splitlines():
        m = re.match(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if m:
            flush()
            pending, removals = [], int(m.group(1) or 1) if m.group(1) != "0" else 0
            new_ln = int(m.group(2))
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            pending.append(new_ln)
            new_ln += 1
        elif line.startswith("-"):
            removals += 1
    flush()
    return added, modified


def split_blocks(lines: list[str]) -> list[tuple[int, int]]:
    """Markdown を「ブロック」に切る。返すのは 1 始まりの (開始行, 終了行)。

    空行で区切るのが基本だが、コードブロックの中の空行では切らない。
    見出しは単独ブロックにする。
    """
    blocks, start, in_fence = [], None, False

    for i, raw in enumerate(lines, start=1):
        if FENCE.match(raw):
            if not in_fence:
                if start is not None:
                    blocks.append((start, i - 1))
                start, in_fence = i, True
            else:
                in_fence = False
                blocks.append((start, i))
                start = None
            continue

        if in_fence:
            continue

        if not raw.strip():
            if start is not None:
                blocks.append((start, i - 1))
                start = None
            continue

        if HEADING.match(raw):
            if start is not None:
                blocks.append((start, i - 1))
            blocks.append((i, i))
            start = None
            continue

        if start is None:
            start = i

    if start is not None:
        blocks.append((start, len(lines)))
    return blocks


def map_revisions(file: Path, base: str) -> list[dict]:
    lines = file.read_text(encoding="utf-8").splitlines()
    added, modified = changed_lines(file, base)
    touched = added | modified

    out = []
    for begin, end in split_blocks(lines):
        hits = [n for n in range(begin, end + 1) if n in touched]
        if not hits:
            continue
        span = end - begin + 1
        out.append({
            "start": begin,
            "end": end,
            "kind": "added" if all(n in added for n in hits) else "modified",
            "ratio": round(len(hits) / span, 3),
            "preview": lines[begin - 1][:60],
        })
    return out


def mark(file: Path, base: str, out_path: Path) -> list[dict]:
    """改訂マーカー Div で変更ブロックを挟んだ qmd を書き出す。"""
    text = file.read_text(encoding="utf-8")
    lines = text.splitlines()
    blocks = map_revisions(file, base)

    # 後ろから挿入して行番号のずれを防ぐ
    for b in sorted(blocks, key=lambda x: x["start"], reverse=True):
        lines.insert(b["end"], ":::")
        lines.insert(b["start"] - 1, f'::: {{.revision-{b["kind"]}}}')

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return blocks


def main() -> int:
    ap = argparse.ArgumentParser(prog="revision", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("map", "mark"):
        p = sub.add_parser(name)
        p.add_argument("file")
        p.add_argument("--base", required=True, help="比較元のリビジョン(例: HEAD~1, v1.0)")
        if name == "mark":
            p.add_argument("-o", "--output", required=True)

    args = ap.parse_args()
    file = Path(args.file).resolve()

    if args.cmd == "map":
        blocks = map_revisions(file, args.base)
        print(json.dumps(blocks, ensure_ascii=False, indent=2))
        return 0

    blocks = mark(file, args.base, Path(args.output).resolve())
    n_add = sum(1 for b in blocks if b["kind"] == "added")
    print(f"## 改訂箇所  {len(blocks)} ブロック(追加 {n_add} / 変更 {len(blocks) - n_add})")
    for b in blocks:
        print(f"- {b['kind']:8} 行{b['start']}-{b['end']} 変更率{b['ratio']:.0%}  {b['preview']}")
    print(f"\n出力: {args.output}")
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
