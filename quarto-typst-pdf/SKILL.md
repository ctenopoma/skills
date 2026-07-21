---
name: quarto-typst-pdf
description: Markdown や Jupyter Notebook を Quarto × Typst で技術文書PDFにする。環境が無いところへの導入、複数文書の合本、デザイン(配色・フォント・表)とスタイル(章節番号・図表番号・目次・ページ番号)の指定、レンダリング結果の目視調整までを扱う。「PDFにして」「資料をPDF化」「Quarto」「Typst」「合本にまとめて」「表紙やヘッダを直して」などで使う。
user-invocable: true
---

# quarto-typst-pdf — 技術文書をPDFにする

Markdown と Jupyter Notebook を、ローカル完結でPDFに組版する。日英混在・Mermaid・
Draw.io・数式を含む技術資料を想定している。外部APIは使わない。

実行系は `scripts/qtpdf.py` にまとまっている。**Bash や Python を自分で書く前に、
まずこのスクリプトのサブコマンドで足りないか確認すること。**

```bash
python ~/.claude/skills/quarto-typst-pdf/scripts/qtpdf.py <サブコマンド>
```

以降 `qtpdf` と略記する。Windows では `python` の代わりに `py` が要ることがある。

## 進め方の原則 — 手順書ではなく会話で決める

**このスキルは「言われたとおりに変換する道具」ではない。** ユーザーは自分の資料を
どう組めるか知らないことが多い。**選べることを見せて、一緒に決める**のが仕事。

### 最初にすること: 資料を見て、提案する

いきなりビルドしない。**まず対象を調べ、何ができるかを具体的に示す。**

1. 対象フォルダの中身を見る(`.md` と `.ipynb` の数、見出し構成、図、表、
   相互リンク、git 履歴の有無)
2. **その資料に即した選択肢を提示する。** 一般論ではなく「この5本は相互に
   リンクし合っているので1冊にまとめられます」のように、見た内容で語る
3. 決まったところから作り、**出して見せてから細部を詰める**

### 必ず確認すること(勝手に決めない)

- **1本にまとめるか、別々に出すか。** これは既定を置いてはいけない。
  相互リンクのある文書群・章立ての教材は合本が自然だが、独立した記事なら別々。
  **見つけた文書を列挙して、どう束ねたいか聞く。**
- **どういう見た目にするか。** 4種の design を用途つきで示す。迷っていそうなら
  `qtpdf matrix` で候補だけ並べて見比べてもらう。
- **番号の付け方。** 「第1章 / 図1-1」の日本式か、番号なしの記事風か。
  原稿に手書きの番号があれば `--strip-numbers` が要ることも伝える。

### 提案してよいもの(気づいたら黙っていない)

資料を見て次に当てはまるものがあれば、**聞かれなくても選択肢として出す**。

- git 履歴がある → 「前の版からの変更箇所を示せます(線 / 文字色 / 両方)。
  コミット履歴から改訂履歴ページも自動で作れます」
- 仕様書・納品物らしい → 表紙(文書番号・版数・機密区分)を付けられます
- レビュー段階らしい → DRAFT の透かしを入れられます
- 素の文章に注意書きが埋もれている → Info / Warning の囲いに整えられます
  (`references/authoring.md` の基準に従う。原文の意味は変えない)
- コードが多い → VSCode のテーマと同じ配色にできます
- Zenn 記事 → そのまま取り込めます

### やってはいけないこと

- **手順を並べて終わりにしない。** 使い方を聞かれても、コマンド一覧を返すだけで
  なく「今回の資料ならこうします」と結びつける。
- **黙って既定で作り切らない。** 特に合本か個別かを勝手に決めない。
- **一度で完成させようとしない。** 1パターン出す → 見てもらう → 直す、が基本。

## 実行のしかた

### 1. 環境を見る

```bash
qtpdf doctor
```

Quarto・フォント・Chromium・Java の有無と、足りない場合の次の手を報告する。
**必ず最初に実行する。** 結果を読んでから動く。

- Quarto が無い → `qtpdf install`(`~/.local/quarto` にポータブル導入。
  管理者権限も PATH 変更も不要。winget が使えない環境でも通る)
- フォントが無い → `qtpdf fonts fonts`(推奨セットをプロジェクトの `fonts/` へ)

フォントは **OS にインストールしない**。フォルダに置いて Typst に渡す方式なので、
インストールが禁止されている環境でも動き、フォルダごと持ち運べば再現する。

### 2. プロジェクトに設定を作る

```bash
qtpdf init            # カレントに _quarto.yml とプロファイル一式、.gitignore 追記
```

生成物(PDF・中間ファイル・フォントバイナリ)は `.gitignore` に入る。
**ビルド設定は git に残し、生成物は残さない**のが既定の方針。

### 3. PDFにする

```bash
qtpdf build                                   # 見つかった文書を全部
qtpdf build --design spec-sheet --style jis-jp
qtpdf build . README.md notebooks/01.ipynb    # 対象を指定
```

ルート直下の `*.md` と `notebooks/*.ipynb` を自動で見つける。
`.md` は元ファイルを変更せず、shadow `.qmd` を作ってから組版する
(GitHub 流の Mermaid 記法変換と、日本語見出しアンカーの固定のため)。

複数文書を1冊にまとめるなら:

```bash
qtpdf book --title "設計資料" --author "チーム名"
```

### 4. 出す → 調べる → 見る → 直す(必ず回す)

**一度で決まらない。試行錯誤は毎回起きる前提で進める。**
実際、表の列幅・図の大きさ・改頁・二重番号は、どれも出してみて初めて分かった。

```bash
qtpdf check _pdf/README.pdf              # 機械で調べる
qtpdf probe _pdf/README.pdf --pages 1,5  # 目で見る(PNGを書き出す)
```

1. **`check`** — 紙の外へのはみ出し、ほぼ空のページ、豆腐、埋め込みフォントを
   機械的に判定する。出たものは確実に不具合なので、まずこれを潰す。
2. **`probe` + Read** — 書き出したPNGを **Read ツールで実際に開いて見る**。
3. **直す** → 1 に戻る。所見が消えるまで繰り返す。

**`check` が黙っていても「問題なし」ではない。** 拾えるのは座標の異常だけで、
「表が潰れて読めない」「図の中の日本語が豆腐」は座標上は正常に見える。
**2 を飛ばさないこと。**

見るページは **表がある / 図がある / 章の変わり目 / 最終ページ** を必ず含める。
何を見るかの一覧は `references/inspect.md` にある。

**ログだけで「直った」と言わない。** 見たページと、そこで何を確認したかを述べる。

### 5. 見た目のレビューは別のエージェントに渡す

自分で出したものを自分で見ると、見たいものが見えてしまう。
**ページ数が多いとき(目安10ページ超)、または見た目を詰める段階では、
レビューを別のエージェントに投げる。** 本体の文脈も汚さずに済む。

渡し方(`references/inspect.md` に詳しく書いてある):

- PNG の**パスだけ渡し、エージェント自身に Read で開かせる**。
  こちらの見立ては伝えない。伝えるとそれを裏付ける方向に引っ張られる。
- `references/inspect.md` の観点一覧を渡し、**1項目ずつ確認させる**。
- 出させるのは「ページ番号 + 何がおかしいか + どう直すべきか」。
  「概ね良い」は要らない。
- 直したら**もう一度同じ観点で見てもらう**。直した箇所が別を壊すことがある
  (表の列幅を変えたら別の表があふれた、が実際に起きた)。

### 6. 表紙・透かし・改訂バー

```bash
# 表紙(simple: 罫線1本 / formal: 文書管理情報つき)
qtpdf build . 仕様書.md --cover formal --version 1.2 --doc-number DOC-001 \
  --classification 社外秘 --author "チーム名" --date 2026-07-21

# レビュー用に DRAFT の透かしを入れる
qtpdf build --watermark DRAFT

# git の2リビジョン間の変更を明示する
qtpdf revise 仕様書.md --base HEAD~1 --revision-mark bar     # 線(既定)
qtpdf revise 仕様書.md --base HEAD~1 --revision-mark color   # 文字色
qtpdf revise 仕様書.md --base HEAD~1 --revision-mark both    # 線+色

# git 履歴から改訂履歴ページを自動生成(表紙の次・目次の前に入る)
qtpdf revise 仕様書.md --base HEAD~1 --cover formal --revision-history
```

**改訂の見せ方は必ず聞く。** 3通りあり、用途で選ぶものだから既定で押し切らない。

| 指定 | 見え方 | 向く場面 |
| --- | --- | --- |
| `bar` | 左外側に縦線。追加=実線 / 変更=破線 | 白黒印刷でも残る。紙で回覧する校正 |
| `color` | 文字色。追加=青 / 変更=赤 | 画面で見るとき。どこが変わったか一目で分かる |
| `both` | 線と色の両方 | 画面と紙の両方で配る |

`--revision-history` を付けると、git のコミット履歴から
「版数 / 日付 / 改訂内容 / 作成」の表を自動で作る。版数はタグがあればタグ、
無ければ短縮ハッシュ。**表紙 → 改訂履歴 → 目次 → 本文** の順に並ぶ。

表紙の値は文書の frontmatter からも拾う(`title` / `subtitle` / `author` /
`date` / `version` / `doc-number` / `classification`)。コマンドラインの指定が優先。

`revise` は git 差分を**ブロック単位**(段落・見出し・コードブロック・箇条書き)に
丸めてから判定する。行単位のままでは「1文字直した段落」と「書き直した段落」が
区別できないため。変更率も併せて報告する。

### 7. 調整する

要望に応じて design / style を変える。両者は直交していて、どの組み合わせでも
壊れない(`references/design-style.md` に選び方と拡張時の約束を書いてある)。

| 見た目 (design) | 番号と組織 (style) |
| --- | --- |
| `engineering-note` 技術ブログ向け | `jis-jp` 第1章 / 図1-1 / 目次3階層 |
| `spec-sheet` 仕様書・白黒印刷耐性 | `western` Figure 1.1 / 目次2階層 |
| `handbook` 長編・外余白広め | `blog-flat` 見出し番号なし / 図1 通し |
| `minimal-mono` 単色・RFC風 | `clause` 全見出し番号 / ページ「3 / 12」 |

どれが好みか決まっていないときは、見比べてもらう:

```bash
qtpdf matrix --designs spec-sheet engineering-note --styles jis-jp
```

引数を省くと16通り全部を出す。テンプレートを触ったあとの回帰確認にも使う。

### 改頁

目次のあとと章(最上位の見出し)の前では必ず改頁する。記事のように短い文書で
章ごとに改頁すると間延びする場合は、`-M chapter-breaks:false` で切れる。

## 判断のしかた

- **質問は絞る。** 聞くのは「1本にまとめるか」「どの見た目か」の2つで足りる。
  残りは提案として示し、要るかどうかだけ答えてもらう。質問を並べ立てない。
- **見た目の希望が曖昧なら、聞き返す前に1つ出す。** 「技術ブログっぽく」なら
  `engineering-note` × `blog-flat`、「仕様書」なら `spec-sheet` × `jis-jp`。
  出したものを見てもらうほうが、言葉で詰めるより早い。
- **エラーが出たら `references/pitfalls.md` を読む。** Quarto と Typst の
  組み合わせで実際に踏んだ問題と対処が書いてある。推測で直す前に確認する。
- **テンプレート(`assets/`)を直したら `qtpdf matrix` を通す。** design か style を
  触ると他の組み合わせが壊れることがある。

## 参照

必要になったときだけ読む。常時読み込まない。

| ファイル | 中身 |
| --- | --- |
| `references/design-style.md` | design / style の選び方、2層の契約(拡張時の約束) |
| `references/inspect.md` | 出力の見かた。確認の観点一覧とレビューの投げ方 |
| `references/pitfalls.md` | Quarto × Typst で踏んだ問題と対処。エラー時の一次資料 |
| `references/fonts.md` | 推奨フォントと入手先。`fonts/README.md` として配置される |
| `references/authoring.md` | 素の Markdown を読みやすく整えるときの判断基準 |
| `references/plantuml.md` | Corretto と plantuml.jar の導入。ライセンス上の理由も |

## 資産

`assets/` の中身は検証済みで、`qtpdf init` が作る `_quarto.yml` から参照される。

- `base.typ` — 全体共通。コードのフォント、図表まわりの余白、表の外枠とセルの組み方
- `callouts.typ` — Info / Warning などの囲い5種
- `revision.typ` — 改訂バー(追加=実線 / 変更=破線)
- `designs/*.typ` — 配色・フォント・表の罫線と塗り(見た目だけ)
- `styles/*.typ` — 図表番号の書式と採番範囲(番号だけ)
- `covers/*.typ` — 表紙。`@@KEY@@` を qtpdf がメタデータで置換する
- `themes/*.tmTheme` — コードの配色(VSCode テーマから変換したもの)
- `filters/numbering.lua` — 表の列幅を内容量に応じて配分し、キャプションの無い
  表・図にも Quarto と同じカウンタで番号を振る
- `filters/fit-images.lua` — Mermaid / Draw.io の図を本文幅に収める
- `filters/revision.lua` — 改訂マーカーの Div を Typst の改訂バーに橋渡しする

## 図とコードの色

```bash
# PlantUML(Amazon Corretto と plantuml.jar をユーザー領域に導入して使う)
qtpdf diagram design.puml figures/design.png --fonts fonts
# → 生成した画像を Markdown に ![](figures/design.png) で埋める

# Zenn記法(:::message 等)の記事を Quarto の .qmd に変換
qtpdf zenn article.md article.qmd
qtpdf zenn --dir path/to/zenn-contents --out converted/

# コードの配色を VSCode テーマに合わせる
qtpdf build --code-theme anthropic-dark      # 他: anthropic-light
```

Mermaid と Draw.io は Markdown に直接書ける(変換不要)。PlantUML だけは
Quarto が直接扱えないので、先に画像へビルドしてから埋め込む。

執筆支援(素の文章を callout やコードブロックに整える)を頼まれたら、
`references/authoring.md` の判断基準に従うこと。原文の意味を変えないのが第一。

## 制約として残っているもの

- **タグ付きPDF(PDF/UA)は出せない。** Quarto 同梱の Typst が対応するまで待ち。
  代替として、画像の代替テキスト欠落は警告できる。
- **PlantUML は Java が要る。** ライセンス上 Amazon Corretto を使う
  (`references/plantuml.md`)。Mermaid と Draw.io だけなら Java 不要。
- **Draw.io SVG の図中フォントは SVG 側の指定のまま。** 本文フォントとは揃わない。
  揃えたい場合は Draw.io 側でフォントを変えるか、PNG で書き出す。
