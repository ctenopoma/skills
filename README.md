# skills — legacy-reverse 配布ブランチ

**このブランチ(`legacy-reverse-dist`)は legacy-reverse 関連だけを切り出した配布用。**
他のスキル・MCP サーバは削除してある(全部入りは `main` ブランチ)。

## 収録内容

| ディレクトリ | 内容 |
| --- | --- |
| [legacy-reverse/](legacy-reverse/) | レガシーコード(Fortran / C・C++ 等)を Python へ仕様ベースで移植するリバースエンジニアリング・パイプライン(skill 本体・スクリプト・hook) |
| [mcp-servers/legacy-reverse-mcp/](mcp-servers/legacy-reverse-mcp/) | 上記の機械操作を型付きツール化した MCP サーバ(30 ツール) |
| [docs/legacy-reverse/](docs/legacy-reverse/) | 設計・仕様書(Quarto サイト + 全8章の合本 PDF 1 冊同梱) |
| [quarto-typst-pdf/](quarto-typst-pdf/) | PDF 出力の依存 skill(legacy-reverse の pdf_book.py・MANUAL/設計書の PDF 再生成が使う) |

## まず読むもの

| 文書 | 対象読者 |
| --- | --- |
| [legacy-reverse/slides/index.html](legacy-reverse/slides/index.html) | 初見の操作者(ブラウザで開くだけのチュートリアル) |
| [legacy-reverse/QUICKREF.md](legacy-reverse/QUICKREF.md) | 作業中の操作者(コマンド即引き 1 枚) |
| [legacy-reverse/MANUAL.md](legacy-reverse/MANUAL.md) / MANUAL.html / MANUAL.pdf | 操作者(背景と操作の意味・トラブル対処)。HTML は画像込みの**単一ファイル**なのでそのまま配れる |
| [docs/legacy-reverse/pdf/](docs/legacy-reverse/pdf/) | 設計者・保守者・運用管理者(設計・仕様書。全8章の合本 PDF) |

ドキュメントの再生成:

```bash
python docs/legacy-reverse/make_manual.py   # MANUAL.html + MANUAL.pdf
python docs/legacy-reverse/make_pdf.py      # 設計・仕様書の章別 PDF
quarto render docs/legacy-reverse           # 設計・仕様書の HTML サイト(_site/)
```

## 導入(対象プロジェクト側)

詳細は [legacy-reverse/README.md](legacy-reverse/README.md) の「導入」を参照。要点:

1. `legacy-reverse/` を対象プロジェクトの `.claude/skills/legacy-reverse` に配置
2. `legacy-reverse/skills/legacy-*` を `.claude/skills/` 直下にもコピー
3. `legacy-reverse/hooks/settings-example.json` を `.claude/settings.json` にマージ(必須)
4. `.mcp.json` に `mcp-servers/legacy-reverse-mcp/server.py` を**絶対パス**で登録(推奨。このリポジトリを参照するので残しておく)
5. Quarto を入れる(HTML サイト生成に必要)。Claude Code で `/legacy-reverse` を実行してセットアップ確認

合本PDF まで出す場合のみ、`quarto-typst-pdf/` も `.claude/skills/` に置く(HTML だけなら不要)。
