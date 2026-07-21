// design: engineering-note — 技術ブログ向け。ゴシック、余白広め、アクセント色つき
#let accent = rgb("#1F6F8B")

#set text(font: ("Noto Sans CJK JP",))
#show heading: set text(font: ("Noto Sans CJK JP",))
#show heading.where(level: 1): set text(fill: accent)
#show heading.where(level: 2): set text(fill: accent.darken(15%))
#show link: set text(fill: accent)

#set table(
  inset: (x: 8pt, y: 6pt),
  stroke: 0.4pt + luma(200),
  fill: (x, y) => if y == 0 { accent.lighten(88%) }
                  else if calc.odd(y) { luma(250) } else { none },
)
#show table.cell.where(y: 0): set text(fill: accent.darken(25%))
