// 改訂バー — 変更されたブロックの左外側に縦線を引く。印刷校正の慣習に沿った見せ方。
//
// scripts/revision.py が git 差分から `::: {.revision-added}` /
// `{.revision-modified}` の Div を挿入し、filters/revision.lua が
// この関数の呼び出しに変換する。
//
// 追加は実線、変更は破線。白黒印刷でも種類を見分けられるようにしている。

#let revision-bar(kind, body) = {
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
      paint: luma(35),
      dash: if kind == "added" { none } else { (3pt, 2.5pt) },
    )),
    body,
  ))
}
