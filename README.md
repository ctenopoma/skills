# skills

Claude Code の個人用スキル(`~/.claude/skills/`)を複数PC間で共有管理するためのリポジトリ。

## 構成

各スキルはこの直下にディレクトリを作り、中に `SKILL.md` を置く。

```
skills/
  my-skill-name/
    SKILL.md
    (必要に応じて references/ や scripts/ など)
```

## 新しいPCでのセットアップ

既存の `~/.claude/skills` が(空 or 未作成)であることを確認してから:

```bash
# 既存フォルダがあれば退避
mv ~/.claude/skills ~/.claude/skills.bak 2>/dev/null

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
