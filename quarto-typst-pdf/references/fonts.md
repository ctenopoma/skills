# fonts/ — PDF組版用の持ち込みフォント

Quarto × Typst でのPDF化に使うフォントの置き場。**OS にはインストールしない**
(セキュリティ制約でフォントのインストールが拒否される環境でも動かすため)。
Typst にはレンダ時に `font-paths`(`_quarto.yml` で設定済み)経由で渡る。

バイナリは git にコミットしない(`.gitignore` 済み)。別環境で揃えるときは、
このフォルダを指定して次を実行する。

```bash
python ~/.claude/skills/quarto-typst-pdf/scripts/qtpdf.py fonts fonts
```

手動で入れる場合は下記から取得する。**可変フォント(VF)ではなくウェイト別の
static 版**を選ぶこと。Typst は可変フォントのウェイト軸の扱いに制約があり、
太字が意図どおりに出ないことがある。

| ファイル | 役割 | 入手先 |
| --- | --- | --- |
| NotoSansCJKjp-Regular.otf / -Bold.otf | 本文ゴシック | https://github.com/notofonts/noto-cjk (Sans/OTF/Japanese/) |
| NotoSerifCJKjp-Regular.otf / -Bold.otf | 明朝(spec-sheet デザインの本文) | https://github.com/notofonts/noto-cjk (Serif/OTF/Japanese/) |
| JetBrainsMono-{Regular,Bold,Italic,BoldItalic}.ttf | コード | https://github.com/JetBrains/JetBrainsMono/releases |

Typst 側でのフォント名: `Noto Sans CJK JP` / `Noto Serif CJK JP` / `JetBrains Mono`

数式フォントは Typst 同梱(New Computer Modern Math)なので追加不要。
