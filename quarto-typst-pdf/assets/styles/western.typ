// style: western — 英語圏の学術慣習。Figure 1.2 形式の章-連番
#let _ch() = {
  let c = counter(heading).get()
  if c.len() > 0 { c.first() } else { 0 }
}

#set figure(numbering: n => {
  let ch = _ch()
  if ch > 0 { numbering("1.1", ch, n) } else { numbering("1", n) }
})

#show heading.where(level: 1): it => {
  counter(figure.where(kind: "quarto-float-fig")).update(0)
  counter(figure.where(kind: "quarto-float-tbl")).update(0)
  it
}
