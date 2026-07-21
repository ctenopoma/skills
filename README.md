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
