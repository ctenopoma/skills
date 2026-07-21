// design: spec-sheet — 仕様書・納品資料向け。明朝本文、白黒印刷耐性、罫線しっかり
#set text(font: ("Noto Serif CJK JP",))
#show heading: set text(font: ("Noto Serif CJK JP",), weight: 700)
#show link: set text(fill: black)

#set table(
  inset: (x: 7pt, y: 5pt),
  stroke: 0.5pt + luma(70),
  fill: (x, y) => if y == 0 { luma(228) } else { none },
)
