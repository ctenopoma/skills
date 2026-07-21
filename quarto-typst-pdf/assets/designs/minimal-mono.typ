// design: minimal-mono — 社内設計文書・RFC向け。ほぼ単色、タイポグラフィ主導
#set text(font: ("Noto Sans CJK JP",))
#show heading: set text(font: ("Noto Sans CJK JP",), fill: luma(20))
#show link: set text(fill: luma(40))

// 罫線は最小限(横罫のみ)。外枠は base.typ が引く
#set table(
  inset: (x: 9pt, y: 6pt),
  stroke: (x, y) => (
    top: if y <= 1 { 0.5pt + luma(60) } else { 0.25pt + luma(215) },
    bottom: 0.25pt + luma(215),
    left: none, right: none,
  ),
  fill: none,
)
#show table.cell.where(y: 0): set text(weight: 700)
