---
name: legacy-2-testspec
description: レガシー移植パイプラインのフェーズ②。関数仕様書（reviewed）だけを入力に単体テスト仕様書を作成し、人の承認を得る。「F-xxxx のテスト仕様を作って」で使う。
user-invocable: true
---

# legacy-2-testspec — ② テスト仕様書作成

親skill legacy-reverse の references/workflow.md に従う。
**入力は ①(reviewed)・docs/conventions.md・docs/domain-knowledge.md のみ。
legacy/・src/・tests/ は読まない**（触れたくなったら仕様の穴 → ISSUE）。

引数: func-id。前提: ①が reviewed（違えば断って ① へ誘導）。

## 手順

1. テンプレ `assets/templates/test-spec.md` の形式で `docs/test-specs/<func-id>.md` を生成:
   - フロントマターの `spec-hash` に `ledger hash docs/specs/<func-id>.md` の値を記録
   - **観点**: 同値分割・境界値・異常系・グローバル状態・外部ファイルI/O・副作用。
     期待結果は戻り値だけでなく事後のグローバル状態・ファイル書き込みまで書く
   - **サブルーチン欄**: ①の呼び出しリストから列挙し、MOCK/REAL を conventions に従い決定
   - **期待値の根拠**: 仕様書🟢 / 人間確認済み / ⚠未確定 のいずれか
     （レガシー実行環境は無い前提。期待値を①から導けなければ捏造せず ⚠未確定）
2. 🟡🔴由来・⚠未確定のケースは ISSUE 起票（仮説＋Yes/No形式）し、未確定事項表にリンク
3. **トレーサビリティマトリクス**を書き、**機械レビュー（必須ゲート）**を実行:
   ```bash
   python <LR>/scripts/review_checks.py testspec <func-id> --root .
   ```
   （MCP なら `review_testspec`）。検知対象: 🟢仕様項目のケース漏れ、①に存在しない
   SPEC-ID の参照（捏造）、ケース定義のないTC参照、期待値の根拠の規定外表記。
   NG ゼロにしてから次へ（⚠未確定は承認依頼時点では残ってよい。approved 時にゼロ）
4. **人へ承認依頼**: ケース一覧（分類・根拠の内訳）＋要回答の質問一覧を提示
5. 人が質問に回答したら期待値を確定（根拠を「人間確認済み」に）、回答は
   ISSUE 経由で domain-knowledge.md に転記
6. 人のOKが出たら status: approved / approved-by / approved-date を更新 → `ledger wbs`

## 完了条件（approved にしてよい条件）

- 全🟢仕様項目にケース1件以上 / ⚠未確定ゼロ / 人のOK

## 禁止

- legacy/・src/・tests/ を読むこと
- 期待値を推測で埋めて根拠を「仕様書🟢」と偽ること
- 人のOKなしで approved にすること
