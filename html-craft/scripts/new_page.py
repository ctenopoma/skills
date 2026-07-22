"""自己完結HTMLの雛形を生成する。

使い方:
    python new_page.py slides    出力先.html --title "タイトル"
    python new_page.py dashboard 出力先.html --title "タイトル"
    python new_page.py demo      出力先.html --title "タイトル"

assets/ の CSS・JS をインライン展開した「1ファイルで完結する」HTML を作る。
生成後、本文(スライド・カード・モデル関数)を書き足していく。
"""

import argparse
import sys
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"

SLIDES_BODY = """\
<header class="topbar">
  <span class="deck-title">{title}</span>
  <button id="modeToggle">一覧表示</button>
</header>
<div class="progress"><div id="bar"></div></div>

<main>

<section class="slide title-slide">
  <span class="chip" style="align-self:center">TOPIC</span>
  <h1>{title}</h1>
  <p class="lead">サブタイトルをここに。</p>
  <p class="hint"><kbd>→</kbd> キーで次へ / <kbd>←</kbd> で戻る</p>
</section>

<section class="slide">
  <h2>最初のスライド</h2>
  <p>ここに内容を書く。1枚1メッセージ。</p>
</section>

</main>

<footer class="nav">
  <button id="prev">←</button>
  <span class="counter" id="counter"></span>
  <button id="next">→</button>
</footer>
"""

DASHBOARD_BODY = """\
<main>
  <div class="page-header">
    <h1>{title}</h1>
    <p class="meta">データ更新日: YYYY-MM-DD | 出典: (ここに書く)</p>
  </div>

  <div class="grid cols-4">
    <div class="card kpi">
      <div class="label">指標A</div>
      <div class="value">--</div>
    </div>
    <div class="card kpi">
      <div class="label">指標B</div>
      <div class="value">--</div>
    </div>
  </div>

  <div class="controls">
    <label>期間
      <select id="periodFilter"><option value="all">すべて</option></select>
    </label>
  </div>

  <div class="grid cols-2">
    <div class="card">
      <h2>グラフ1</h2>
      <figure class="chart" id="chart1"><!-- SVG をここに描く --></figure>
    </div>
    <div class="card">
      <h2>明細</h2>
      <div id="table1"></div>
    </div>
  </div>
</main>

<script>
// データはここに埋め込む(自己完結にするため外部 fetch はしない)
const DATA = [
  // {{"period": "2026-01", "a": 10, "b": 20}},
];

function render() {{
  // DATA とフィルタ状態から #chart1 (SVG) と #table1 を描き直す
}}

document.getElementById("periodFilter").addEventListener("change", render);
render();
</script>
"""

DEMO_BODY = """\
<main>
  <div class="page-header">
    <h1>{title}</h1>
    <p class="meta">パラメータを動かすと下の図が変わる。</p>
  </div>

  <div class="controls">
    <label>パラメータ k
      <input type="range" id="paramK" min="0" max="100" value="50">
      <span class="value-readout" id="paramKValue">50</span>
    </label>
    <button class="ghost" id="resetBtn">リセット</button>
  </div>

  <div class="card">
    <figure class="chart" id="stage"><!-- SVG をここに描く --></figure>
    <p class="caption">図の説明をここに。</p>
  </div>

  <div class="card">
    <h2>この図の見方</h2>
    <p>何が起きているかの説明をここに書く。</p>
  </div>
</main>

<script>
function model(k) {{
  // パラメータ -> 計算結果。ここが「見せたい原理」の本体
  return [];
}}

function render() {{
  const k = Number(document.getElementById("paramK").value);
  document.getElementById("paramKValue").textContent = k;
  const result = model(k);
  // result から #stage の SVG を描き直す
}}

document.getElementById("paramK").addEventListener("input", render);
document.getElementById("resetBtn").addEventListener("click", () => {{
  document.getElementById("paramK").value = 50;
  render();
}});
render();
</script>
"""

PAGE = """\
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
{css}
</style>
</head>
<body>
{body}
{deck_js}
</body>
</html>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["slides", "dashboard", "demo"])
    ap.add_argument("output")
    ap.add_argument("--title", default="タイトル未設定")
    args = ap.parse_args()

    if args.mode == "slides":
        css = (ASSETS / "deck.css").read_text(encoding="utf-8")
        body = SLIDES_BODY.format(title=args.title)
        deck_js = "<script>\n" + (ASSETS / "deck.js").read_text(encoding="utf-8") + "\n</script>"
    else:
        css = (ASSETS / "page.css").read_text(encoding="utf-8")
        body = (DASHBOARD_BODY if args.mode == "dashboard" else DEMO_BODY).format(
            title=args.title
        )
        deck_js = ""

    out = Path(args.output)
    if out.exists():
        sys.exit(f"エラー: {out} は既に存在する(上書きしない)")
    out.write_text(
        PAGE.format(title=args.title, css=css, body=body, deck_js=deck_js),
        encoding="utf-8",
    )
    print(f"{args.mode} の雛形を書き出した: {out}")


if __name__ == "__main__":
    main()
