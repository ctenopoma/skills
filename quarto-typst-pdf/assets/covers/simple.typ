// 表紙: simple — 罫線1本の落ち着いた構え。社内資料・技術ブログのまとめ向け。
//
// @@名前@@ は qtpdf.py が文書のメタデータで置換する差し込み口。
// 値が無い項目は `none` に置換され、下の filter で行ごと消える。
// (Typst の波括弧と衝突しないよう、この記法を使っている)

#{
  set page(header: none, footer: none, numbering: none)
  set align(left)

  v(6cm)

  let classification = @@CLASSIFICATION@@
  if classification != none {
    block(
      stroke: 0.6pt + luma(90), inset: (x: 7pt, y: 4pt),
      text(size: 9pt, weight: 600, tracking: 0.08em, classification),
    )
    v(0.8cm)
  }

  text(size: 26pt, weight: 700, @@TITLE@@)

  let subtitle = @@SUBTITLE@@
  if subtitle != none {
    v(0.35cm)
    text(size: 14pt, fill: luma(70), subtitle)
  }

  v(0.9cm)
  line(length: 100%, stroke: 1.2pt + luma(40))
  v(0.5cm)

  // 発行情報。空の項目は行ごと出さない。
  set text(size: 10pt, fill: luma(55))
  grid(
    columns: (auto, 1fr), row-gutter: 0.4em, column-gutter: 1.2em,
    ..(
      ("文書番号", @@NUMBER@@),
      ("版数", @@VERSION@@),
      ("発行日", @@DATE@@),
      ("作成", @@AUTHOR@@),
    )
      .filter(pair => pair.at(1) != none)
      .map(pair => (text(weight: 600, pair.at(0)), pair.at(1)))
      .flatten()
  )

  pagebreak(weak: false)
  // 表紙は数に入れない。本文を1ページ目として数え直す。
  counter(page).update(1)
}
