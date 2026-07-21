// 改訂箇所の見せ方。
//
// scripts/revision.py が git 差分から `::: {.revision-added}` /
// `{.revision-modified}` の Div を挿入し、filters/revision.lua が
// `#revision-mark("added")[...]` の呼び出しに変換する。
//
// 見せ方は3通り。qtpdf.py の --revision-mark で選ぶ。
//   bar   … 左外側に縦線(既定)。追加=実線 / 変更=破線。本文の色は変えない
//   color … 文字色を変える。追加=青 / 変更=赤。線は引かない
//   both  … 両方
//
// 色を使う版は画面で見るときに分かりやすいが、白黒印刷では区別が消える。
// 線の版は白黒でも残る。用途に応じて選ぶ。

#let revision-colors = (
  added: rgb("#1A5FB4"),
  modified: rgb("#B3261E"),
)

#let make-revision-mark(mode) = (kind, body) => {
  let colored = if mode == "color" or mode == "both" {
    text(fill: revision-colors.at(kind), body)
  } else {
    body
  }

  if mode == "color" {
    return colored
  }

  let gap = 0.5cm

  // ブロックの左罫線をそのまま改訂バーにする。罫線は中身の高さに一致し、
  // 改ページで分割されたときは各ページの断片にそれぞれ引かれる。
  // 本文の位置を動かさないよう、外側を負の padding で引き戻して
  // バーだけを余白側へ出す。
  pad(left: -gap, block(
    width: 100% + gap,
    inset: (left: gap),
    breakable: true,
    stroke: (left: (
      thickness: 2pt,
      paint: if mode == "both" { revision-colors.at(kind) } else { luma(35) },
      dash: if kind == "added" { none } else { (3pt, 2.5pt) },
    )),
    colored,
  ))
}

// 既定は線。qtpdf.py が --revision-mark を受けたときは、この後ろで再定義する。
#let revision-mark = make-revision-mark("bar")
