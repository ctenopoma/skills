# MCP & Skills 学習カリキュラム

Claude Code を題材に、Agent Skills と MCP サーバの「使い方 → 作り方 → 連携」を
ハンズオンで学ぶための教材。各章とも **動かす → 中身を読む → 自分で改造する** の順で進める。

## ハンズオンの進め方(紙芝居版)

ハンズオン本体は **[slides/index.html](slides/index.html) をブラウザで直接開いて**進める。
サーバ起動も外部通信も不要(`file://` で完結)。矢印キーでページをめくる紙芝居形式で、
右上の「一覧表示」で全ページを縦に並べることもできる。

各章の README(下記)は同じ内容のテキスト版で、検索・引用・差分管理用の記録として置いている。

Claude Code を前提に書いているが、MCP は標準プロトコルなので、作ったサーバは
OpenWebUI・Claude Desktop・Cursor などからもそのまま使える。各章末に他ツールでの
利用方法をまとめている。

## 前提環境

- Claude Code が動作すること(LLM への API 通信が可能なこと)
- Python 3.10+ / pip
- Node.js / npm(OSS の MCP サーバ利用に使用)
- 外部 Web API へのアクセスは**不要**。すべての教材はローカルで完結する
  (プロキシ環境でも npm / pip / LLM API が通れば実施可能)

## カリキュラム全体像

| レベル | 内容 | 学ぶこと |
| --- | --- | --- |
| **A(基礎)** | ToDo 管理を題材に Skill と MCP の基本を一周する | SKILL.md の書き方 / OSS MCP サーバの利用 / 内製 MCP サーバ(STDIO → HTTP) / Skill と MCP の連携 |
| **B(中級)** | 入出力をテキスト以外に広げる | 画像を返す MCP(プロット描画) / PDF を扱う MCP(ローカル文献ライブラリ) / tools 以外の機能(resources) |
| **C(上級)** | 業務を置き換える本格スキルを作る | 知識と処理の置き場所の設計(SKILL.md / references / assets / scripts) / マルチエージェントの分担執筆 / ツール導入型スキルの題材選び |

## レベルA: 基礎(ToDo 題材)

1. [01-skills](level-a/01-skills/README.md) — Skill の使い方・作り方。
   最小構成の SKILL.md から始めて、references/ による段階的開示まで
2. [02-mcp](level-a/02-mcp/README.md) — MCP サーバ。
   OSS サーバの利用 → Python で ToDo サーバを内製(STDIO)→ 同じサーバを HTTP 化
3. [03-skills-x-mcp](level-a/03-skills-x-mcp/README.md) — 連携。
   ToDo MCP を使う「作業分解プランナー」スキルを作り、**MCP=道具 / Skill=手順書** という役割分担を体感する

## レベルB: 中級(入出力をテキスト以外に広げる)

1. [plot-mcp](level-b/plot-mcp/README.md) — CSV を渡すとグラフを描いて**画像で返す** MCP サーバ。
   レスポンスがテキスト以外になる最小例。LLM が描いたグラフを自分で見て考察できる
2. [paper-library-mcp](level-b/paper-library-mcp/README.md) — 手元の PDF 文献フォルダを全文検索できる
   MCP サーバ。**入力がバイナリ**のパターン(pypdf でのテキスト抽出)と、
   tools に加えて **resources 機能** を学ぶ

## レベルC: 上級(本格スキル開発)

- [level-c](level-c/README.md) — 「業務をひとつ置き換える規模のスキル」の設計法。
  このリポジトリの [html-craft](../html-craft/)(スライド / ダッシュボード / デモを
  自己完結HTML 1ファイルで作るスキル)を実例に、SKILL.md / references / assets / scripts の
  置き場所設計、統合スキルの description 設計、マルチエージェントでの分担執筆までを学ぶ

## 付録: 周辺エコシステム

- [appendix-ecosystem](appendix-ecosystem/README.md) — 作ったスキル・MCP サーバを
  「チームに配って運用する」ための周辺技術。配布形態(git / プラグイン / pip・npm / Docker /
  ゲートウェイ)、LiteLLM Proxy(LLM ゲートウェイ)と MCP Gateway、MCP Inspector、
  FastMCP 2.x、uv/uvx、Ollama + OpenWebUI、セキュリティの注意

## 用語の整理(最初にこれだけ)

- **Agent Skills**: Claude への「手順書」。`SKILL.md` という Markdown ファイルで、
  いつ発動するか(description)と、何をどの順でやるか(本文)を書く。コードは不要
- **MCP (Model Context Protocol)**: LLM アプリに「道具」を生やすための標準プロトコル。
  MCP サーバはツール(関数)やリソース(データ)を提供し、Claude Code などのクライアントがそれを呼ぶ
- 関係: **Skill は手順書、MCP は道具箱**。道具の使い方・使う順番を手順書に書くと、両者が連携する(→ 03 章)
