# 付録: 周辺エコシステム — 配布・運用・開発支援

> **紙芝居版**: [../slides/x1-ecosystem.html](../slides/x1-ecosystem.html) をブラウザで開く。
> この README は同内容のテキスト版(記録用)。

作ったスキルと MCP サーバを「自分の手元で動く」から「チームに配って運用する」に
進めるための周辺技術を整理する。ツール名・記法はバージョンで変わるため、
ここでは役割と選び方を押さえる(細部は各ツールのドキュメントで確認)。

## 1. 全体地図

```
作る            試す              配る                     運用する           使う
mcp SDK    →   MCP Inspector →   git / pip・npm /    →   LiteLLM Proxy →   Claude Code
(FastMCP)                        Docker / ゲートウェイ     (キー・コスト・      Claude Desktop
                                                          アクセス制御)       OpenWebUI / Cursor
```

先に大事な整理: **Skill はクライアント側のファイル、MCP はサーバ**。
だから配布の仕組みがまったく違う。

## 2. Skills の配布 — ファイルを配る問題

Claude Code のスキルはただの Markdown+同梱ファイルなので、配布はファイル共有の問題になる。

| 形態 | やり方 | 向き |
| --- | --- | --- |
| 個人用を git 共有 | `~/.claude/skills` を git リポジトリにする(**このリポジトリ方式**) | 個人の複数PC、少人数 |
| プロジェクト同梱 | `<repo>/.claude/skills/` に入れてコミット | そのプロジェクトの全員 |
| Claude Code プラグイン | スキル・コマンド・MCP設定を束ね、マーケットプレイス(git リポジトリ)として配布。利用者は `/plugin` で導入 | 部署・組織への本格配布 |

Claude Code のスキルは**自分の PC に置くファイル**なので、配布方法は「ファイルを
どう届けるか」= git / プラグイン。一方、プログラムから Claude API を呼ぶアプリの
ためには、スキルを Anthropic に預けておく別の仕組み(Skills API)がある(§5)。
LiteLLM の機能一覧にある「Skills」はそれのこと。

## 3. MCP サーバの配布 — 4形態

| 形態 | やり方 | 特徴 |
| --- | --- | --- |
| ① ソース+git | clone して `python server.py` を登録(**教材方式**) | 一番単純。Python 環境は利用者任せ |
| ② パッケージ | pip / npm で配布し、`uvx <pkg>` / `npx <pkg>` で実行 | 依存ごと隔離実行。社内 PyPI/npm ミラーがあれば楽 |
| ③ Docker | イメージ化して `docker run`(STDIO)または HTTP で公開 | 依存を完全に封じる。実行環境の統一 |
| ④ 中央ゲートウェイ | HTTP サーバとして1箇所に立て、全員が URL を登録 | 教材の `--http` の延長。個々の PC にインストール不要 |

②の補足: `pyproject.toml` の `[project.scripts]` でエントリポイントを切っておくと
`uvx your-mcp-server` の一発で起動できる形で配れる。

## 4. LiteLLM Proxy — LLM ゲートウェイと MCP Gateway

OSS の LLM ゲートウェイ。社内に1台立てて、全員がそこ経由で LLM API を使う構成にする。
プロキシ環境の社内 AI 基盤としては定番の位置づけ。

- **仮想キー**: 本物の API キーはゲートウェイだけが持ち、利用者には仮想キーを発行
- **コスト管理**: ユーザー/チーム別の使用量集計、予算、レート制限
- **モデル振り分け**: 複数プロバイダ・複数モデルを1つの OpenAI 互換 API に集約
- **クライアント設定**: Claude Code は `ANTHROPIC_BASE_URL` をゲートウェイに向ける。
  社内アプリは OpenAI 互換の接続先として登録する

さらに近年の LiteLLM には **MCP Gateway 機能**があり、MCP サーバをゲートウェイに
登録して配布できる(機能の有無・記法はバージョン依存。導入前に確認):

- 利用者は LiteLLM の MCP エンドポイント **1つ**をクライアントに登録するだけ
- どのツール群を誰に見せるかを仮想キー単位でアクセス制御できる
- ツール呼び出しのログ・監査もゲートウェイに集まる

つまり「§3 ④の HTTP 化」を組織規模にしたもの。内製 MCP サーバの社内配布の本命。

## 5. Skills API — 「LiteLLM の機能にある Skills」の正体

**前提のおさらい**: スキルの実体は、SKILL.md や references/ が入った**1つのフォルダ**。
Claude Code は「自分の PC にあるそのフォルダ」を読んで動く。

**困る場面**: Claude は Claude Code 以外からも使う。自社のプログラムに Claude API を
組み込む場合(社内チャットボットや文書の自動生成など)、Claude は Anthropic のサーバの
中で動くので、**あなたの PC のフォルダは読めない**。

**解決策 = Skills API**: スキルのフォルダを zip にして Anthropic に**登録して預けておく**
仕組み。API で Claude を呼ぶときに「登録してあるこのスキルを使え」と指定すると、
Anthropic のサーバ側でそのスキルが読み込まれる。登録し直すたびに版(v1, v2, …)として
記録される。LiteLLM の機能一覧にある「Skills」は、この登録・指定をゲートウェイ経由で
扱えるようにする対応のこと。

| 同じスキルのフォルダを… | 置く場所 | 配る方法 |
| --- | --- | --- |
| Claude Code で使うなら | 自分の PC | git / プラグイン |
| API を組み込んだアプリで使うなら | Anthropic に登録 | Skills API |

フォルダの中身(SKILL.md の書き方)はどちらも同じ。この教材で作ったスキルは、
将来 API アプリを作るときもそのまま使える。

## 6. 開発支援ツール

- **MCP Inspector**: `npx @modelcontextprotocol/inspector python server.py`。
  ブラウザ GUI でツール一覧・呼び出しテスト・resources 閲覧ができるデバッガ。
  **Claude Code に繋ぐ前に Inspector で叩く**と、サーバの問題かクライアントの問題かを
  切り分けられる。内製開発の必携
- **FastMCP 2.x**(jlowin/fastmcp): 教材で使った公式 SDK 同梱の FastMCP の発展版 OSS。
  認証、複数サーバの合成 (mount)、既存サーバのプロキシ化、クライアントライブラリなど。
  学習・小規模運用は `mcp[cli]` で十分。認証付きで公開する段階になったら検討
- **uv / uvx**: Python の高速パッケージマネージャ。`uvx` は「インストールせず隔離実行」で、
  MCP サーバの配布・実行の事実上の標準になりつつある(Node 側の `npx` に対応)

## 7. 教材との接続マップ

| 教材で作った物 | そのままの配布先 |
| --- | --- |
| スキル(task-planner, html-craft 等) | このリポジトリ(git)→ プラグイン化 → API アプリに進むなら Skills API |
| todo / plot / paper-library サーバ | ソース+git → パッケージ化 → HTTP 化 → ゲートウェイ登録、と段階的に格上げ |
| 紙芝居教材(slides/) | ファイル配布のみで完結(それが html-craft の思想) |

次の実務ステップの候補:
① MCP Inspector を入れて内製サーバの開発ループを速くする →
② LiteLLM Proxy を立てて LLM アクセスを一元化する →
③ その上で MCP Gateway に内製サーバを登録して配布する
