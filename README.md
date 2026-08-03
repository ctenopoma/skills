# skills

Claude Code の個人用スキル(`~/.claude/skills/`)を複数PC間で共有管理するためのリポジトリ。

## 学習教材

[learning/](learning/README.md) に、Agent Skills と MCP サーバの「使い方 → 作り方 → 連携」を
ハンズオンで学ぶカリキュラムがある(レベルA: 基礎 / B: 中級 / C: 上級)。

## 構成

各スキルはこの直下にディレクトリを作り、中に `SKILL.md` を置く。

```
skills/
  my-skill-name/
    SKILL.md
    (必要に応じて references/ や scripts/ など)
```

## 新しいPCでのセットアップ

`~/.claude/skills` が未作成(またはクリーンな状態)なら、clone先を直接指定するだけでOK。

```bash
git clone https://github.com/ctenopoma/skills.git ~/.claude/skills
```

Windows (コマンドプロンプト) の場合:

```bat
git clone https://github.com/ctenopoma/skills.git %USERPROFILE%\.claude\skills
```

既に `~/.claude/skills` に何かある場合は、先に退避してからclone:

```bash
mv ~/.claude/skills ~/.claude/skills.bak
git clone https://github.com/ctenopoma/skills.git ~/.claude/skills
```

## 更新の反映

```bash
cd ~/.claude/skills
git pull
```

## スキルを編集・追加したら

```bash
cd ~/.claude/skills
git add -A
git commit -m "update: ..."
git push
```

## 収録スキル

| スキル | 用途 |
| --- | --- |
| [quarto-typst-pdf](quarto-typst-pdf/) | Markdown / Jupyter Notebook を Quarto × Typst で技術文書PDFにする。環境導入・合本・デザイン指定・目視調整まで |
| [html-craft](html-craft/) | スライド・ダッシュボード・インタラクティブデモを自己完結HTML 1ファイルで作る。ブラウザ直開き・オフライン動作・PDF書き出し対応 |
| [eda-workflow](eda-workflow/) | 探索的データ解析(EDA)の手順書。品質チェック→分布→関係→深掘り→所見の型で、data-mcp / stats-mcp / plot-mcp を指揮する |
| [diagram](diagram/) | ポンチ絵・概念図(フロー図・構成図・シーケンス図等)を Mermaid または自己完結SVG で作る |

内製 MCP サーバは [mcp-servers/](mcp-servers/) にある(data-mcp: DuckDBデータ処理 / stats-mcp: 統計計算)。
