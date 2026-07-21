# 実際に踏んだ落とし穴と対処

llm_poc(md 2本 + Jupyter Notebook 11本、日英混在・Mermaid・Draw.io入り)で
検証したときに出た問題。いずれも `assets/` と `scripts/qtpdf.py` で対処済みだが、
挙動が変わったときの手がかりとして残す。

## Quarto / Typst の導入

- **winget が使えないことがある。** シェルによっては `winget` が PATH に無い。
  ポータブル zip を `~/.local/quarto` に展開する方式なら管理者権限も PATH 変更も
  要らない(`qtpdf.py install` がこれをやる)。セキュリティ制約のある環境ではむしろ本命。
- **Jupyter は要らない。** `execute: enabled: false` なら保存済みの出力をそのまま
  組版するだけなので、Python に jupyter が入っていなくてもノートブックをPDFにできる。
- **Mermaid には Chromium 系ブラウザが要る。** HTML 以外の出力では内部でヘッドレス
  実行するため。Windows なら Edge / Chrome があれば満たせるが、無い環境では図が出ない。

## Markdown の取り込み

- **GitHub 流の ` ```mermaid ` は Quarto では処理されない。** ` ```{mermaid} ` に
  変換が要る。元ファイルを書き換えず shadow `.qmd` を作って変換する。
- **日本語見出しのアンカーでコンパイルが落ちる。** `[…](#見出し)` のリンク先 slug は
  GitHub / VSCode / Pandoc で規則が違い、ズレると Typst が
  `label ... does not exist` で失敗する。リンク先を集めて見出しに明示IDを振る。
- **H1 を title に上げたら `shift-heading-level-by: -1` を併用する。** しないと
  本文の `##` が第2階層のままになり、目次と番号が1段ずれる。

## 図

- **Mermaid の画像に物理サイズが直接入る。** Quarto は `width: 25.17in` のような
  自然サイズを指定してくるため A4 からはみ出す。Lua フィルタで `width: 100%` に
  書き換え、`height` を落としてアスペクト比を保つ(`filters/fit-images.lua`)。
- **Mermaid 内の数式は既定の PNG 経路で出る。** KaTeX が SVG 内 `foreignObject`
  として吐く分には懸念があったが、Quarto の既定経路では問題なく描画された。
- **Draw.io SVG(`*.drawio.svg`)はそのまま埋まる。** 再編集性も保たれる。ただし
  図中フォントは SVG 側の指定(Meiryo 等 OS フォント)のままで本文とは揃わない。

## 表と図表番号

- **列幅が等間隔になる。** Pandoc は行の長い pipe table に相対列幅を割り当て、
  Quarto がそれを `columns: (33.33%, ...)` として出す。幅指定を落とすと
  `columns: N` になり Typst が内容量に応じて配分する(`filters/numbering.lua`)。
- **セルが中央寄せ・両端揃えになる。** Quarto は `align: (auto, ...)` を渡すので、
  figure の中央寄せをセルまで継承してしまう。`base.typ` で左揃え・両端揃えなしにする。
- **キャプションの無い表・図には番号が付かない。** Quarto は float 化したものだけを
  `#figure` にするため。同じ kind(`quarto-float-tbl` / `quarto-float-fig`)で包めば
  カウンタを共有して通し番号になる。
- **`numbering.lua` は Quarto 本体のフィルタの後で動かす。** `_quarto.yml` の
  `filters:` で `- quarto` の次に置く。前だと Quarto が後から float 化した表を
  二重に包んでしまう。
- **float の入れ物は class では見分けられない。** `quarto-scaffold` はセル出力の
  包みにも付く。直下に Typst の `#figure(` 生片を持つかどうかで判定する。
- **改ページ時のヘッダ行再表示は既定で効く。** Typst の `table.header(repeat: true)`
  を Quarto が出しているので、こちらで何かする必要はない。

## 合本(book)

- **`author` が未指定だとコンパイルエラー。** typst の book テンプレート
  (orange-book)が `author` を前提にしている。空文字でも与える。
- **章頭に空白ページが入る。** テンプレートに `pagebreak(to: "odd")` が固定で
  書かれており、設定では消せない。パッチ版パッケージを `TYPST_PACKAGE_PATH` で
  優先解決させると消える(Quarto 本体は書き換えない)。
- **プロジェクトローカルの `_extensions/` にコピーしても効かない。** パッケージ
  解決の経路が違うため。環境変数での上書きが要る。

## その他

- **`monofont` 指定が効かない。** typst テンプレートが拾わないので、
  `#show raw: set text(font: (...))` を include-in-header で注入する
  (`base.typ` が実施)。
- **ページ番号は Typst 側で `#set page(numbering:)` しても上書きされる。**
  テンプレートの `set page` が後に来るため。Quarto の `page-numbering` オプションを使う。
- **プロファイルは複数指定できる。** `--profile design,style` で両方の
  `include-in-header` が合成される。design と style を直交させられる根拠。
- **`-M include-in-header:...` では追加できない。** リスト型の指定を丸ごと
  置き換えてしまい、`base.typ` ごと消える。文書ごとに Typst を足したいときは
  一時プロファイルを書いて `--profile` に足す(表紙・透かし・コードテーマがこの方式)。
- **本文フォントは design の `.typ` では変えられない。** テンプレートの
  `set text(font: ...)` が後に来て勝つ。`_quarto-<design>.yml` の
  `mainfont` で指定する。

## 表紙・改訂バー・コードテーマ

- **Typst はプロジェクト外の絶対パスを読めない。** `#set raw(theme: "C:/...")` は
  「プロジェクト直下からの相対」と解釈されて file not found になる。tmTheme は
  プロジェクトへ複製してファイル名だけで参照する。
- **Quarto は Div の class を Typst に渡さない。** `::: {.revision-added}` は
  素の `#block[...]` になるため、show ルールでは種類を見分けられない。
  Lua フィルタで raw ブロックに変換して関数呼び出しにする。
- **改訂バーは `block` の左罫線で引く。** `line(end: (x, 100%))` を使うと
  100% が版面の残り高さに解決され、ブロックが1ページを丸ごと占有して壊れる。
  `block(stroke: (left: ...), breakable: true)` なら中身の高さに一致し、
  改ページで分割されても各断片に引かれる。
- **表紙は1ページ使い切って `counter(page).update(1)`。** しないと本文が
  2ページ目から始まる番号になる。
- **透かしは表紙より先に入れる。** 後に置くと、先に改頁する表紙には掛からない。

## Windows

- **コンソールの既定は cp932。** 日本語やダッシュ記号を print すると
  `UnicodeEncodeError` で落ちる。スクリプト冒頭で stdout/stderr を UTF-8 に
  再設定しておく。
- **`winget` が使えないことがある。** シェルによっては PATH に無い。
  ポータブル展開方式(Quarto / Corretto / plantuml.jar)なら影響を受けない。

## 実プロジェクトで踏んだもの

- **冒頭が生 HTML だと題が拾えない。** README は `<p align="center"><img ...></p>` で
  ロゴを置く書き方が多い。最初の非空行で H1 探索を打ち切るとファイル名が題になる。
  先頭の HTML 行は読み飛ばす。
- **生 HTML の `<img>` は Typst 出力で消える。** Markdown の画像記法へ直す。
  このとき `width` 属性を引き継がないと原寸で巨大化する。
- **`filters:` はプロファイル間でマージされない。** `include-in-header` は合成されるが、
  `filters` は置き換えになる。フィルタの有効・無効を切り替えたいときは、
  フィルタ自体は常時読み込み、メタデータ(`-M key:true`)で分岐させる。
- **Lua フィルタの `Meta` は本文より後に走る。** メタデータで挙動を変えるフィルタは、
  判定と書き換えを2パスに分ける(`return { {Meta=...}, {Header=...} }`)。
  1つにまとめると判定前に本文を通過してしまう。
- **手書きの見出し番号は Notebook にもある。** Markdown だけ前処理しても
  ノートブックの見出しが二重番号のまま残る。AST 段階(Lua)で落とせば両方に効く。
- **長い表がページ下端からあふれて行が重なる。** Typst の `figure` は既定で
  ページをまたげない。`numbering.lua` が表を figure で包むため、1ページに
  収まらない表が分割されずに潰れる。`#show figure: set block(breakable: true)`
  で解決する(`base.typ` が実施)。分割時のカラム名再表示はこれで初めて効く。
- **列幅を全部 auto にすると版面をはみ出す。** `columns: N`(全列 auto)は内容の
  自然幅で組むため、長い列があると右にあふれる。かといって等分は内容量を無視する。
  列ごとの最大セル幅から比例配分を計算し、合計 100% で明示する
  (`numbering.lua`)。**折り返せない語(コード中の識別子)の長さを下限として
  持たせないと、狭い列で隣の列に重なる。**
- **表の中のコードは少し小さくする。** 長い識別子は折り返せないので、
  本文と同じ大きさだと列からあふれる(`base.typ` で 0.86em)。
- **目次の直後に本文が詰まる。** 既定では間が空かず、本文の書き出しが目次の
  一部に見える。`#show outline:` で下に余白を足す。
