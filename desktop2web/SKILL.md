---
name: desktop2web
description: C# WinFormsデスクトップアプリをWebアプリ（React+Tailwind / Pythonバックエンド）へ派生開発するパイプラインの全体管理。画面は再設計・機能とDBは鏡移しの2レーン。進捗確認・次アクション提案・セットアップ。「デスクトップアプリのWeb化の状況」「次どの画面をやる」などで使う。実作業は d2w-0-analyze / d2w-policy / d2w-screen / d2w-6-check と legacy-reverse ①〜⑤⑦。
user-invocable: true
---

# desktop2web — WinForms→Web 派生開発の全体管理

必読: [references/workflow.md](references/workflow.md)（2レーン規則）、
[references/schema.md](references/schema.md)（データスキーマ）。
機能レーンは **legacy-reverse skill 群をそのまま使う**（規約だけ本skillの conventions.md）。
`d2w` = `python <本skill>/scripts/d2w_ledger.py`、`ledger` = legacy-reverse の ledger.py。

## 呼ばれたらすること

1. 人の直接入力スキャン（legacy-reverse workflow.md と同じ: ISSUE回答欄・規約手編集を拾う）
2. `data/screens.json` の有無を確認
   - ない → 未セットアップ。`/d2w-0-analyze` を案内
   - ある → `d2w wbs` の要点（画面/機能の進捗、open ISSUE、⏳機能待ちの画面）と
     `d2w next` の提案を見せる
3. Ⓐ方針書が approved でなければ、画面イタレーションに入る前に `/d2w-policy` を促す

## フェーズと担当

| フェーズ | skill | 成果物 |
|---|---|---|
| ⓪ 解析 | d2w-0-analyze | screens.json / functions.json / schema.json / CRUD / 骨子 |
| Ⓐ 方針 | d2w-policy | docs/policy.md（approved まで） |
| 画面 | d2w-screen | 画面票・モック・実装・E2E（1画面=1イタレーション） |
| 機能・DB | legacy-1〜5 | 仕様書〜テスト結果（C#→Python 鏡移し） |
| ⑥ | d2w-6-check | 完了検証 |
| ⑦ | legacy-7-analyze | 性能・保守性・セキュリティ（挙動保存） |

## セットアップ

1. schema.md のプロジェクト構成でディレクトリを作る
2. legacy-reverse の hook を登録（backend/tests/ 保護）。E2E freeze 後は frontend/e2e/ も対象
3. `assets/templates/conventions.md` を docs/ に置き、人と確定
4. MCP（legacy-reverse-mcp）登録は任意だが推奨
