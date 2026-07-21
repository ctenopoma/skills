// design: handbook — 長編マニュアル・書籍向け。外余白を広く取り、章見出しを大きく
#set page(margin: (inside: 2.2cm, outside: 3.4cm, y: 2.6cm))
#set text(font: ("Noto Sans CJK JP",))
#show heading.where(level: 1): set text(size: 1.9em, font: ("Noto Serif CJK JP",))
#show heading.where(level: 2): set text(font: ("Noto Serif CJK JP",))

#set table(
  inset: (x: 8pt, y: 6pt),
  stroke: (x, y) => (
    top: if y <= 1 { 0.6pt + luma(80) } else { 0.3pt + luma(210) },
    bottom: 0.3pt + luma(210),
    left: 0.3pt + luma(225),
    right: 0.3pt + luma(225),
  ),
  fill: (x, y) => if y == 0 { luma(238) } else { none },
)
