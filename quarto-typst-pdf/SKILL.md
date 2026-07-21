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

## 進め方

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

### 4. 結果を目で見る

**ログだけで「直った」と言わない。** 表の見え方、図のはみ出し、空白ページ、
フォントの埋め込みは、実際にページを見ないと分からない。

```bash
qtpdf probe _pdf/README.pdf --pages 1,3,5
```

ページ数・空白ページ・図表番号の数・埋め込みフォントを報告し、指定ページを
PNG に書き出す。**書き出した PNG は Read ツールで開いて目視すること。**
持ち込んだフォントが実際に使われたかは、埋め込みフォント一覧で確認できる。

### 5. 表紙・透かし・改訂バー

```bash
# 表紙(simple: 罫線1本 / formal: 文書管理情報つき)
qtpdf build . 仕様書.md --cover formal --version 1.2 --doc-number DOC-001 \
  --classification 社外秘 --author "チーム名" --date 2026-07-21

# レビュー用に DRAFT の透かしを入れる
qtpdf build --watermark DRAFT

# git の2リビジョン間の変更を改訂バーで示す(追加=実線 / 変更=破線)
qtpdf revise 仕様書.md --base HEAD~1 --design spec-sheet --style jis-jp
```

表紙の値は文書の frontmatter からも拾う(`title` / `subtitle` / `author` /
`date` / `version` / `doc-number` / `classification`)。コマンドラインの指定が優先。

`revise` は git 差分を**ブロック単位**(段落・見出し・コードブロック・箇条書き)に
丸めてから判定する。行単位のままでは「1文字直した段落」と「書き直した段落」が
区別できないため。変更率も併せて報告する。

### 6. 調整する

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

## 判断のしかた

- **要望が曖昧なときは、まず1パターン出して見せる。** 「技術ブログっぽく」なら
  `engineering-note` × `blog-flat`、「仕様書」なら `spec-sheet` × `jis-jp` を
  既定にして、出してから直す。最初に質問を並べない。
- **個別PDFか合本かは、文書の関係で決める。** 独立した記事なら個別、
  章立てで通し番号や通し目次が要るなら合本。
- **エラーが出たら `references/pitfalls.md` を読む。** Quarto と Typst の
  組み合わせで実際に踏んだ問題と対処が書いてある。推測で直す前に確認する。
- **テンプレート(`assets/`)を直したら `qtpdf matrix` を通す。** design か style を
  触ると他の組み合わせが壊れることがある。

## 参照

必要になったときだけ読む。常時読み込まない。

| ファイル | 中身 |
| --- | --- |
| `references/design-style.md` | design / style の選び方、2層の契約(拡張時の約束) |
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
