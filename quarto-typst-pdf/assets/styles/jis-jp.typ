// style: jis-jp — 日本の技術文書の作法。図表は「図1-1」の章-連番、ページ番号は算用数字
#let _ch() = {
  let c = counter(heading).get()
  if c.len() > 0 { c.first() } else { 0 }
}

#set figure(numbering: n => {
  let ch = _ch()
  if ch > 0 { numbering("1-1", ch, n) } else { numbering("1", n) }
})

// 章が変わったら図表番号を振り直す
#show heading.where(level: 1): it => {
  counter(figure.where(kind: "quarto-float-fig")).update(0)
  counter(figure.where(kind: "quarto-float-tbl")).update(0)
  it
}
