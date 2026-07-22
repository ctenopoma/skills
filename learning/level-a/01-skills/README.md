# Step 1: Agent Skills の使い方・作り方

> **ハンズオンは紙芝居版で**: [../../slides/a1-skills.html](../../slides/a1-skills.html) をブラウザで開く。
> この README は同内容のテキスト版(記録用)。

Skill は Claude への「手順書」で、実体はただの Markdown ファイル。この章では
最小構成のスキルを手で書き、動かし、段階的に拡張していく。

## 1. Skill の仕組み

Claude Code は起動時に以下の場所から `SKILL.md` を探す。

| 置き場所 | スコープ |
| --- | --- |
| `~/.claude/skills/<スキル名>/SKILL.md` | 個人用(全プロジェクト共通) |
| `<プロジェクト>/.claude/skills/<スキル名>/SKILL.md` | そのプロジェクト専用 |

`SKILL.md` は frontmatter(`name` と `description`)+ 本文で構成される。

```markdown
---
name: my-skill
description: いつ発動してほしいかを書く。Claude はこの文を見て使うか判断する
---

ここに手順を書く(Markdown)
```

重要なのは **description だけが常時 Claude のコンテキストに載る**こと。
本文は「使う」と判断されて初めて読み込まれる。だから

- description には「何をするか」+「どんな依頼のときに使うか」を具体的に書く(発動条件)
- 本文は必要になってから読まれるので、多少長くても他タスクの邪魔にならない

これを**段階的開示 (progressive disclosure)** と呼ぶ。さらに大きい情報は
`references/` に分け、本文から「必要なら references/xxx.md を読め」と誘導する(後述)。

## 2. ハンズオン1: 最小のスキルを作る(commit-message)

コミットメッセージを Conventional Commits 形式で書かせるスキルを作る。
ファイル1個で完結する最小構成。

```bash
mkdir -p ~/.claude/skills/commit-message
```

`~/.claude/skills/commit-message/SKILL.md` を作成する。
完成例は [examples/commit-message/SKILL.md](examples/commit-message/SKILL.md) にあるので、
まず自分で書いてみてから見比べるのがおすすめ。

動作確認: Claude Code を**再起動**し(スキルは起動時に読み込まれる)、
適当な変更を `git add` した状態で「コミットメッセージ書いて」と頼む。
スキルの手順(diff を見る → type を選ぶ → 形式に沿って書く)どおりに動けば成功。

> うまく発動しないときは description を疑う。「コミットメッセージを書く」という
> 依頼文と description の文面が結びつくか、という観点で書き直す。

## 3. ハンズオン2: references/ で拡張する(daily-log)

次は「今日の作業ログを記録する」スキル。テンプレートが長くなるので
`references/` に分離し、段階的開示を体験する。

```
daily-log/
├── SKILL.md              # 手順(短い)
└── references/
    └── format.md         # ログのテンプレート(長い)
```

完成例: [examples/daily-log/](examples/daily-log/)

ポイントは SKILL.md 本文の「ログを書くときは references/format.md のテンプレートに従う」
という一文。Claude は実際にログを書く段になって初めて format.md を読む。
テンプレートを差し替えたいときも SKILL.md には触らなくて済む。

動作確認: 再起動後「今日のログをつけて。◯◯をやった」と話しかける。
`~/daily-logs/2026-07-22.md` のような日付ファイルが所定の形式で作られれば成功。

## 4. ハンズオン3: OSS のスキルを使う

スキルは自作だけでなく、公開されているものを取ってきて使える。公式のサンプル集が
GitHub にある(git clone で取得できる)。

```bash
git clone https://github.com/anthropics/skills.git anthropic-skills
ls anthropic-skills/
cp -r anthropic-skills/<スキル名> ~/.claude/skills/
```

- 導入は「フォルダを置いて再起動」だけ。インストールという概念がない
- 取ってきたスキルの SKILL.md を読むのが最良の教材。description の書き方、
  references/ や scripts/ の使い方など、自作の手本になる
- スキルの実体はただのフォルダなので、社内共有も「git リポジトリに置く」だけで済む
  (このリポジトリ自体がその実例)

## 5. さらに先へ: scripts/ パターン

手順の中に「決まりきった処理」があるなら、スクリプトにして同梱し、
SKILL.md から「この場合は `scripts/xxx.py` を実行せよ」と指示できる。
LLM に毎回コードを書かせるより速く、結果も安定する。

実例として、このリポジトリの [quarto-typst-pdf](../../../quarto-typst-pdf/) は
references/ + scripts/ + assets/ をフル活用した本格的なスキル。
最小構成との差分を眺めると、スキルがどこまで育てられるかが分かる。

## 6. 改造課題

- commit-message に「日本語 / 英語をユーザーの言語に合わせる」ルールを足す
- daily-log のテンプレートに「明日やること」欄を足す(format.md だけの変更で済むことを確認)
- 自分の定型作業をひとつ選んでスキル化する

## 7. 他ツールでの活用

Skill の実体は「発動条件つきの手順書 Markdown」なので、Claude Code 以外でも流用できる。

- **Claude Desktop / claude.ai**: Skills 機能に同じ SKILL.md 形式でアップロードできる
- **OpenWebUI やその他のチャット UI**: SKILL.md 本文をシステムプロンプトや
  カスタム指示に貼れば、同じ手順書として機能する(自動発動の仕組みがないぶん、
  常時適用 or 手動で貼る運用になる)

「ツール固有の機能」ではなく「手順の文書化」なので、資産として腐りにくいのが Skill の利点。

---

次: [Step 2: MCP サーバ](../02-mcp/README.md)
