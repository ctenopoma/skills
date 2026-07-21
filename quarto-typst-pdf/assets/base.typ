// 全デザイン共通の土台。design / style のどちらにも属さない普遍的な体裁。
// 読み込み順: base.typ → designs/*.typ → styles/*.typ

// --- コード: 欧文は等幅、和文はゴシックへフォールバック ---
#show raw: set text(font: ("JetBrains Mono", "Noto Sans CJK JP"))

// --- 図表と本文の間隔 ---
// 既定では図表が本文に近すぎて塊が分離して見えないため、上下に余白を取る。
#show figure: set block(above: 1.8em, below: 1.8em)
#show figure.caption: set text(size: 9pt)
#show figure.caption: set block(above: 0.7em, below: 0.7em)

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

// 改ページ時のヘッダ行再表示は Typst の table.header(repeat) が担う(既定で有効)
