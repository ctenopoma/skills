// 全デザイン共通の土台。design / style のどちらにも属さない普遍的な体裁。
// 読み込み順: base.typ → designs/*.typ → styles/*.typ

// --- コード: 欧文は等幅、和文はゴシックへフォールバック ---
#show raw: set text(font: ("JetBrains Mono", "Noto Sans CJK JP"))

// --- 見出しは両端揃えにしない ---
// テンプレートが `par(justify: true)` を掛けるため、見出しの日本語が
// 「イ ン ス ト ー ル」のように1文字ずつ離れる。組版として誤り。
//
// コードブロックにも同じ間延びが出るが、`show raw: set par(justify: false)` は
// 逆効果だった(1語ずつ改行される)。コード側は行の折り返し自体を抑える。
#show heading: set par(justify: false)

// --- 目次と章の切れ目 ---
// 既定では目次のすぐ下に本文が続き、本文の書き出しが目次の一部に見える。
// 目次のあとと章(レベル1見出し)の前で必ず改頁して、切れ目を明確にする。
//
// weak: true にしてあるので、すでにページ先頭にいるときは何も起きない。
// 「目次の改頁」と「最初の章の改頁」が重なっても空白ページはできない。
// 目次のあとと章頭の改頁は Typst の show ルールでは書けない
// (テンプレートが本文を関数の引数として組むため、show ルールの出力が
//  コンテナ扱いになり pagebreak が拒否される)。
// filters/pagebreaks.lua が AST に直接挿し込む形で実現している。

// --- 図表と本文の間隔、そして改ページ ---
// 既定では図表が本文に近すぎて塊が分離して見えないため、上下に余白を取る。
// あわせて breakable を有効にする。figure は既定でページをまたげず、
// 1ページに収まらない長い表は分割されずにページ下端からあふれ、
// 次の行が重なって潰れる(実際に踏んだ)。
#show figure: set block(above: 1.8em, below: 1.8em, breakable: true)
#show figure.caption: set text(size: 9pt)
#show figure.caption: set block(above: 0.7em, below: 0.7em)

// --- 図表を版面の中央に置く ---
// 図・表・リストはブロック要素として本文から独立させ、左右中央に配置する。
// 表のセルまで中央寄せが継承されるのは下の table.cell で打ち消している。
// caption 側も明示しないと、コードリスト(quarto-float-lst)のキャプションだけ
// 左寄せのまま残る(Quarto がリストのキャプションを block で包むため)。
#show figure: set align(center)
#show figure.caption: set align(center)

// --- 表: 外枠で囲う ---
// セル側の罫線(designs/*.typ が定義)に加え、表全体を1本の枠線で囲む。
#show table: it => block(stroke: 0.9pt + luma(90), breakable: true, it)

// --- 表: セルの組み方 ---
// Quarto は align: (auto, ...) を渡すため、figure の中央寄せを継承してセルまで
// 中央寄せになる。表は左揃えの方が読みやすいので明示する。
// 両端揃えも列幅が狭いと字間が伸びて読みにくいため切る。
#show table.cell: set align(start + top)
#show table.cell: set par(justify: false)
#show table.cell.where(y: 0): set text(weight: 600)

// 表の中のコードは少し小さく。長い識別子は折り返せないので、
// 等倍のままだと列からはみ出して隣の列の文字に重なる。
#show table.cell: it => {
  show raw: set text(size: 0.86em)
  it
}

// 改ページ時のヘッダ行再表示は Typst の table.header(repeat) が担う(既定で有効)
