// 表紙: formal — 枠で囲った文書管理情報つき。仕様書・納品資料向け。
//
// @@名前@@ は qtpdf.py が文書のメタデータで置換する差し込み口。
// 値が無い項目は `none` に置換され、行ごと消える。

#{
  set page(header: none, footer: none, numbering: none)

  let classification = @@CLASSIFICATION@@
  if classification != none {
    align(right, block(
      fill: luma(30), inset: (x: 9pt, y: 5pt),
      text(size: 9.5pt, weight: 700, fill: white, tracking: 0.1em, classification),
    ))
  }

  v(1fr)

  align(center)[
    #block(width: 100%, stroke: (top: 1.6pt + black, bottom: 1.6pt + black),
           inset: (y: 1.4cm))[
      #text(size: 24pt, weight: 700, @@TITLE@@)
      #{
        let subtitle = @@SUBTITLE@@
        if subtitle != none [
          #v(0.5cm)
          #text(size: 13pt, fill: luma(60), subtitle)
        ]
      }
    ]
  ]

  v(1fr)

  // 文書管理情報。仕様書の表紙にあるべき項目を罫線表で出す。
  let rows = (
    ("文書番号", @@NUMBER@@),
    ("版数", @@VERSION@@),
    ("発行日", @@DATE@@),
    ("作成", @@AUTHOR@@),
  ).filter(pair => pair.at(1) != none)

  if rows.len() > 0 {
    align(right, block(width: 8.5cm, table(
      columns: (auto, 1fr),
      inset: (x: 8pt, y: 6pt),
      stroke: 0.5pt + luma(70),
      fill: (x, y) => if x == 0 { luma(238) } else { none },
      ..rows.map(pair => (text(size: 9.5pt, weight: 600, pair.at(0)),
                          text(size: 9.5pt, pair.at(1)))).flatten()
    )))
  }

  v(1.5cm)
  pagebreak(weak: false)
  // 表紙は数に入れない。本文を1ページ目として数え直す。
  counter(page).update(1)
}
