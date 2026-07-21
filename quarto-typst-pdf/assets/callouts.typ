// callout(note/tip/important/caution/warning)の見た目。Zenn の `:::message` の
// ような、左に色帯を立てた淡い囲みに揃える。design / style どちらにも属さない
// 共通部品なので base.typ と同格の単体ファイルにしてある。
//
// Quarto は Typst 出力で `#callout(body:, title:, background_color:, icon:,
// icon_color:, body_background_color:)` という関数を自前定義し、本文側は
// `#callout(...)` を直接呼ぶだけ(`quarto render --to typst -M keep-typ:true`
// で実際の出力を確認した。推測ではない)。この `#let callout(...) = {...}` は
// header-includes(base.typ やこのファイル)より前で定義されるので、同名で
// 再定義すれば本文の呼び出しは後勝ちでこちらに差し替わる。パラメータ名は
// Quarto 側の呼び出しに合わせて変えない。

// --- crossref との互換性(ここが一番の制約) ---
// callout に `{#cau-xxx}` のようなラベルを付けると、Quarto はテンプレート内の
// 別の show ルール(`#show figure: it => {...}`、pandoc テンプレート側に
// 焼き込まれておりこちらでは触れない)が、完成した callout の中身を
// `it.body.children.at(1).body.children.at(0)` のように深く分解して
// タイトル部分を「種類+通し番号: タイトル」に差し替える。この分解は
// Quarto 既定実装の入れ子構造をそのまま前提にしている:
//   外側 block → [タイトル用 block, 本文用 block] の2つを `+` で連結
//   タイトル用 block → 中の block → [アイコン(あれば), タイトル] の並び
// ここで構造そのものを変えると crossref 付き callout がその場でエラーになる
// (未ラベルの callout は影響を受けない)。そのため見た目(色・余白・太さ)は
// 大きく変えつつ、入れ子の段数と「アイコン有りなら子2つ・無しなら子1つ」と
// いう並びだけは Quarto 既定と一致させている。

// --- 種類の判定 ---
// 関数には種類の文字列(note/tip/...)が渡ってこない。呼び出し側で分かるのは
// icon_color / background_color / icon の実体だけなので、Quarto が既定で
// 割り当てる icon_color の16進値をキーに逆引きする。この値は Quarto 本体の
// filters/modules/constants.lua に固定値として書かれているもの
// (note=0758E5 / tip=00A047 / important=CC1914 / caution=FC5300 /
// warning=EB9113)で、Quarto が将来この既定色を変えたり、既定5種以外の
// callout(独自クラス)が来た場合は "other" に落として、Quarto から渡された
// 色をそのまま使う(壊れずに地味な灰色で表示されるだけにする)。
#let callout-kind-by-icon-color = (
  "#0758e5": "note",
  "#00a047": "tip",
  "#cc1914": "important",
  "#fc5300": "caution",
  "#eb9113": "warning",
)

// --- 配色 ---
// 帯色は「種類ごとに一目で見分けられること」と「白黒印刷でも濃淡だけで
// 区別できること」を優先し、5色のグレースケール輝度をわざとずらしてある
// (important > warning > caution > note > tip の順に帯が濃い。
// カラー印刷では色相でも見分けられるので、この順序自体に重要度の意味は無い)。
// 背景はどれも帯色と同系統をごく薄く淡くしたトーンにして、原色のバッジ然と
// した派手さを避け、日本語の技術文書として落ち着いた印象にしている。
#let callout-palette = (
  important: (band: rgb("#643154"), bg: rgb("#f6f1f4")),
  warning:   (band: rgb("#9d3c32"), bg: rgb("#f7f1f0")),
  caution:   (band: rgb("#94652c"), bg: rgb("#f7f4f0")),
  note:      (band: rgb("#5a81aa"), bg: rgb("#f2f3f6")),
  tip:       (band: rgb("#5da887"), bg: rgb("#f2f5f4")),
  other:     (band: rgb("#6b6b6b"), bg: rgb("#f3f3f3")),
)

#let callout(body: [], title: "Callout", background_color: rgb("#dddddd"), icon: none, icon_color: black, body_background_color: white) = {
  let kind = callout-kind-by-icon-color.at(lower(icon_color.to-hex()), default: "other")
  let colors = callout-palette.at(kind, default: (band: icon_color, bg: background_color))

  // タイトル行を組む。crossref の show ルールが「アイコンがあれば子2つ、
  // 無ければ子1つ」を前提に children を数えるため、アイコン無しのときに
  // 空のアイコン枠を挟まない(Quarto 既定実装と同じ組み方)。
  let title-row = [#if icon != none [#text(colors.band, weight: 900)[#icon] ]#text(weight: "bold", fill: colors.band.darken(15%))[#title]]

  // タイトル部分。本文が無い callout(`::: {.callout-note}` だけ)でも
  // 上下の余白が対称になるよう、本文の有無で下側の inset を変える。
  let title-block = block(inset: 1pt, width: 100%, below: 0pt,
    block(fill: colors.bg, width: 100%,
      inset: (x: 11pt, top: 8pt, bottom: if body == [] { 8pt } else { 4pt }),
      title-row))

  // 本文部分。crossref の show ルールが `old_callout.body.children.at(1)` で
  // そのまま拾い直すため、本文があるときだけ足す(Quarto 既定実装と同じ)。
  let body-block = if body != [] {
    block(inset: 1pt, width: 100%,
      block(fill: colors.bg, width: 100%, inset: (x: 11pt, top: 2pt, bottom: 9pt), body))
  }

  block(
    breakable: true, // Quarto 既定は false で改ページ非対応。長い callout が
                      // ページをまたげないと直前で丸ごと次ページへ送られて
                      // 空白ができるため、ここでは true に上書きする。
    fill: colors.bg,
    stroke: (left: 2.6pt + colors.band), // Zenn の `:::message` を意識した
                                          // 左の色帯。全周を線で囲まず片側だけ
                                          // にすることで、淡い背景と合わせて
                                          // 圧が強くならないようにしている。
    radius: 3pt, // 角丸は控えめに。丸すぎるとカード風になり技術文書として浮く。
    width: 100%,
    title-block + body-block
  )
}

// --- crossref ラベル付き callout の日本語対応 ---
// Quarto の definitions.typ は種類名を `upper(kind.first()) + kind.slice(1)` で
// 整形するが、slice はバイト単位なので日本語ロケール(lang: ja)だと
// 「注意」の途中で切って `string index 1 is not a character boundary` で落ちる。
// Quarto 本体のバグで、素の quarto render でも再現する。
//
// ここで先回りして figure を組み立て直し、Quarto の壊れたルールに渡さない。
// 種類名の整形は文字単位で行うので多バイトでも安全。
#show figure: it => {
  if type(it.kind) != str or not it.kind.starts-with("quarto-callout-") {
    return it
  }

  let kind = it.kind.slice("quarto-callout-".len())
  let label = if kind.clusters().len() > 0 {
    // 先頭1文字だけ大文字化(ASCII のときだけ意味がある。日本語はそのまま)
    upper(kind.clusters().first()) + kind.clusters().slice(1).join()
  } else { kind }

  let num = context counter(figure.where(kind: it.kind)).display(it.numbering)
  block(width: 100%)[
    #it.body
    #v(-0.4em)
    #align(right, text(size: 8.5pt, fill: luma(110), [#label #num]))
  ]
}
