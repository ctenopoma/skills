"""PDFの体裁くずれを機械的に見つける。

    python inspect_pdf.py <pdf> [--json]

目視で気づいていたものを検出に落としたもの。実際に踏んだ順に:

  outside   … 文字が紙の外へ出ている。figure が改頁できずあふれた等
  edge      … 文字が紙の端すれすれ。表が版面を超えた等
  blank     … ほぼ空のページ。章頭改頁で区切り線だけ取り残された等
  tofu      … 豆腐(グリフが無い)になっている可能性
  fonts     … 埋め込みフォント(持ち込みフォントが本当に使われたか)

「見た目が良いか」は判定しない。それは人か、ページ画像を見る別のエージェントの仕事。
ここが拾うのは「明らかに壊れている」ものだけ。**偽陽性を出さないことを最優先**する。

行の重なりも検出しようとしたが、表があると行のまとまりが列をまたぐため
正常な文書で誤検出し、逆に本当に潰れた文書では検出できなかった。当てにならない
検出は載せないほうがよい(潰れた場合はたいてい紙の外へ出るので outside で拾える)。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

EDGE_MM = 5.0        # 紙の端からこれ以内に文字があれば「すれすれ」
OUTSIDE_TOL_MM = 3.0 # グリフの枠は字面より少し大きい。この程度は許す
MIN_CHARS = 12       # これ未満ならほぼ空のページ(表紙・中扉は除く)
MM = 72 / 25.4       # mm → PDF ポイント


def _chars(page) -> list[tuple[float, float, float, float]]:
    tp = page.get_textpage()
    out = []
    for i in range(tp.count_chars()):
        try:
            box = tp.get_charbox(i, loose=False)
        except Exception:
            continue
        left, bottom, right, top = box
        if right <= left or top <= bottom:
            continue  # 空白文字など
        out.append((left, bottom, right, top))
    return out


def _lines(chars) -> list[tuple[float, float, float, float]]:
    """文字を行にまとめる。

    下端の丸めで束ねると、同じ行でも大きさの違う文字(コードと地の文、
    添字)が別の行になり、隣接行が必ず重なって見える。縦の重なりで
    束ねること。
    """
    if not chars:
        return []

    ordered = sorted(chars, key=lambda c: (-c[3], c[0]))
    lines = []
    cur = list(ordered[0])

    for left, bottom, right, top in ordered[1:]:
        center = (bottom + top) / 2
        if cur[1] < center < cur[3]:  # いまの行の縦範囲に中心が入る = 同じ行
            cur[0] = min(cur[0], left)
            cur[1] = min(cur[1], bottom)
            cur[2] = max(cur[2], right)
            cur[3] = max(cur[3], top)
        else:
            lines.append(tuple(cur))
            cur = [left, bottom, right, top]

    lines.append(tuple(cur))
    lines.sort(key=lambda b: -b[1])
    return lines


def _overlap(a: tuple, b: tuple) -> float:
    """2つの矩形の縦方向の重なりを、低いほうの高さに対する割合で返す。"""
    top = min(a[3], b[3])
    bottom = max(a[1], b[1])
    if top <= bottom:
        return 0.0
    height = min(a[3] - a[1], b[3] - b[1])
    return (top - bottom) / height if height > 0 else 0.0


def inspect(pdf_path: Path) -> dict:
    import pypdfium2 as pdfium
    from pypdf import PdfReader

    doc = pdfium.PdfDocument(pdf_path)
    findings = {"outside": [], "edge": [], "blank": [], "tofu": []}

    for i in range(len(doc)):
        page = doc[i]
        width, height = page.get_width(), page.get_height()
        chars = _chars(page)
        text = page.get_textpage().get_text_range()

        # 1ページ目は表紙のことがあるので、文字が少なくても咎めない。
        if len(text.strip()) < MIN_CHARS and i > 0:
            findings["blank"].append(i + 1)
            continue

        # 紙の外へ出ているか、端すれすれか。
        # 版面(余白)は design ごとに違うので、紙そのものを基準にする。
        edge = EDGE_MM * MM
        out_by = max(
            max((-c[1] for c in chars), default=0),        # 下へ
            max((c[3] - height for c in chars), default=0),  # 上へ
            max((-c[0] for c in chars), default=0),        # 左へ
            max((c[2] - width for c in chars), default=0),  # 右へ
        )
        if out_by > OUTSIDE_TOL_MM * MM:
            findings["outside"].append({"page": i + 1, "mm": round(out_by / MM, 1)})
        else:
            near = max(
                max((edge - c[1] for c in chars), default=0),
                max((c[3] - (height - edge) for c in chars), default=0),
                max((edge - c[0] for c in chars), default=0),
                max((c[2] - (width - edge) for c in chars), default=0),
            )
            if near > 0:
                findings["edge"].append({"page": i + 1, "mm": round(near / MM, 1)})

        if "�" in text or "□" in text:
            findings["tofu"].append(i + 1)

    reader = PdfReader(str(pdf_path))
    fonts = set()
    for page in reader.pages:
        for f in page.get("/Resources", {}).get("/Font", {}).values():
            base = f.get_object().get("/BaseFont")
            if base:
                fonts.add(str(base).split("+")[-1])

    return {"file": pdf_path.name, "pages": len(doc),
            "fonts": sorted(fonts), "findings": findings}


def report(result: dict) -> int:
    f = result["findings"]
    total = sum(len(v) for v in f.values())

    print(f"## {result['file']}  {result['pages']}ページ")
    if total == 0:
        print("体裁くずれは見つからなかった。あとはページ画像で見た目を確認する。")
    else:
        labels = {
            "outside": "紙の外へはみ出し(改頁できずにあふれている)",
            "edge": "紙の端すれすれ(版面を超えている)",
            "blank": "ほぼ空のページ",
            "tofu": "豆腐(フォントにグリフが無い)",
        }
        for key, items in f.items():
            if not items:
                continue
            pages = [x["page"] if isinstance(x, dict) else x for x in items]
            print(f"- **{labels[key]}**: {pages}")

    print(f"- 埋め込みフォント: {', '.join(result['fonts']) or '(なし)'}")
    return 1 if total else 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="inspect_pdf", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = inspect(Path(args.pdf).resolve())
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    return report(result)


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
