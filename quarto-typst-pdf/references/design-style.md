# デザイン × スタイルの選び方と拡張

見た目(**design**)と、組織・番号の付け方(**style**)は独立した関心事なので、
掛け合わせて指定する。`--design spec-sheet --style jis-jp` のように2つのノブで
16通りを供給する。

## design — どう見えるか

| design | 想定 | 特徴 |
| --- | --- | --- |
| `engineering-note` | 技術ブログ | ゴシック、アクセント色(青緑)、明るい表、縞模様 |
| `spec-sheet` | 仕様書・納品資料 | 明朝本文、白黒印刷に耐える、しっかりした罫線、やや小さめ |
| `handbook` | 長編マニュアル・書籍 | 外側の余白を広く、章見出しを大きく、傍注や改訂バーの余地 |
| `minimal-mono` | 社内設計文書・RFC | ほぼ単色、横罫のみ(外枠は共通で付く)、タイポグラフィ主導 |

## style — どう組織され、どう番号が振られるか

| style | 章節番号 | 図表番号 | 目次 | ページ番号 |
| --- | --- | --- | --- | --- |
| `jis-jp` | 1.1.1 | 図1-1 / 表1-1(章ごとに振り直し) | 3階層 | 1 |
| `western` | 1.1 | Figure 1.1 / Table 1.1 | 2階層 | 1 |
| `blog-flat` | なし | 図1 / 表1(文書通し) | 2階層 | 1 |
| `clause` | 1.1.1 | 図1 / 表1 | 3階層 | 3 / 12 |

## 選び方の目安

- 社外に出す仕様書・納品物 → `spec-sheet` × `jis-jp`
- 技術ブログ・社内共有記事 → `engineering-note` × `blog-flat`
- 章立てのある長い教材・マニュアル → `handbook` × `jis-jp`(合本と相性が良い)
- 英語圏に出す資料 → `spec-sheet` または `handbook` × `western`
- 版管理する内部設計文書 → `minimal-mono` × `clause`

迷ったら `qtpdf.py matrix` で候補だけレンダして見比べる
(`--designs spec-sheet handbook --styles jis-jp` のように絞れる)。

## 2層の契約 — 追加するときに守ること

新しい design / style を足すときは、互いの担当を侵さない。これを守る限り、
どの組み合わせでも合成が破綻しない。

**designs/*.typ が触ってよいもの**
色、フォント(役割別)、寸法スケール、ページ余白、表の罫線と塗り、
リンク色、見出しの字面。**番号には触らない。**

**styles/*.typ が触ってよいもの**
図表番号の書式と採番範囲、章での図表カウンタのリセット。
章節番号・目次の深さ・ページ番号は Typst ではなく `_quarto-<style>.yml` 側の
`section-numbering` / `toc-depth` / `page-numbering` で指定する
(Typst で `#set page(numbering:)` してもテンプレートに上書きされるため)。
**配色やフォントには触らない。**

図表の supplement(「図」「表」/ Figure / Table)は style 側の
`fig-supplement` / `tbl-supplement` と `crossref` で揃える。Quarto が float 化した
ものと、`numbering.lua` が番号を振ったものの両方に効かせる必要がある。
