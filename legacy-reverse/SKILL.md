---
name: legacy-reverse
description: レガシーコード（Fortran/C#等）をPython へ仕様ベースで移植するリバースエンジニアリング・パイプラインの全体管理。導入セットアップ、WBSによる進捗確認、次アクションの提案を行う。「レガシー移植の状況」「次どの関数をやる」「legacy-reverse をセットアップ」などで使う。各フェーズの実作業は legacy-0-analyze 〜 legacy-6-check の各skillで行う。
user-invocable: true
---

# legacy-reverse — レガシー移植パイプラインの全体管理

パイプライン: ⓪解析 → ①仕様書 → ②テスト仕様 → ③テストコード → ④実装 → ⑤テスト → ⑥完了検証。
フェーズのトリガはすべて人。このskillは「状況を見せて、次を提案する」係。

必読: [references/workflow.md](references/workflow.md)（情報遮断・ハッシュ連鎖・ISSUE・承認・ループ規則）、
[references/schema.md](references/schema.md)（プロジェクト構成とデータスキーマ）。

以下 `LR` = このskillのルート、`ledger` = `python <LR>/scripts/ledger.py`。

## 呼ばれたらすること

1. 対象プロジェクトに `data/functions.json` があるか確認
   - **ない** → 未セットアップ。下記「セットアップ」を案内し、`/legacy-0-analyze` を勧める
   - **ある** → `ledger status` と `ledger next` を実行し、WBS（docs/index.qmd）の要点
     （進捗サマリ・open ISSUE・⛔ blocked）をまとめて見せ、次の一手を提案する
2. open ISSUE がある場合は必ず最初に列挙する（人の判断待ちが最優先）
3. ⛔ blocked の関数は「ISSUE裁定 → 反映 → `ledger unblock <func-id>` → 再トリガ」の手順を添える

## セットアップ（新規プロジェクト）

1. schema.md のプロジェクト構成でディレクトリを作る
2. hook を登録: `<LR>/hooks/settings-example.json` の内容を対象プロジェクトの
   `.claude/settings.json` にマージ（④⑤中の tests/ 編集をブロックする安全装置）
3. `/legacy-0-analyze` へ

## 各フェーズと担当skill

| フェーズ | skill | 主な成果物 |
|---|---|---|
| ⓪ 解析 | legacy-0-analyze | functions.json・仕様書骨子・WBS・conventions.md |
| ① 仕様書 | legacy-1-spec | docs/specs/F-xxxx.md（reviewed まで） |
| ② テスト仕様 | legacy-2-testspec | docs/test-specs/F-xxxx.md（approved まで） |
| ③ テストコード | legacy-3-testcode | tests/（freeze まで） |
| ④ 実装 | legacy-4-impl | src/（スタブなし） |
| ⑤ テスト | legacy-5-test | docs/test-results/（pass or 裁定ISSUE） |
| ⑥ 完了検証 | legacy-6-check | docs/completion-check.md |

## レンダリング

- HTML/PDF が欲しいと言われたら quarto-typst-pdf skill を使う。PDFは 仕様書 / テスト仕様書 /
  テスト結果 の種別ごとに個別、HTMLは docs/ 一式を1サイト（WBSがトップ）
- ④の詳細仕様（docstring）は Sphinx で HTML のみ
