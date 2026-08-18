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

0. **起動時スキャン**: `docs/review-feedback.md` に対象func-idの「状態: pending」が
   ないか確認（人が修正依頼を記入している場合がある。直接記入でも
   `review_actions.py request-changes` 経由でも同じ形式）。
   あれば内容を反映してから「状態: applied」に書き換える
1. **プロジェクトの `docs/templates/test-spec.md`（人が著者。無ければ skill 同梱シード）**の
   形式で `docs/test-specs/<func-id>.md` を生成:
   - フロントマターの `spec-hash` に `ledger hash docs/specs/<func-id>.md` の値を記録
   - **観点**: 同値分割・境界値・異常系・グローバル状態・外部ファイルI/O・副作用。
     期待結果は戻り値だけでなく事後のグローバル状態・ファイル書き込みまで書く
   - **サブルーチン欄**: ①の呼び出しリストから列挙し、MOCK/REAL を conventions に従い決定
   - **期待値の根拠**: 仕様書🟢 / 人間確認済み / ⚠未確定 のいずれか
     （レガシー実行環境は無い前提。期待値を①から導けなければ捏造せず ⚠未確定）
   - **例外・数値特異点の境界ケース**（下記）
2. 🟡🔴由来・⚠未確定のケースは ISSUE 起票（仮説＋Yes/No形式）し、未確定事項表にリンク
3. **トレーサビリティマトリクス**を書き、**機械レビュー（必須ゲート）**を実行:
   ```bash
   python <LR>/scripts/review_checks.py testspec <func-id> --root .
   ```
   （MCP なら `review_testspec`）。検知対象: 🟢仕様項目のケース漏れ、①に存在しない
   SPEC-ID の参照（捏造）、ケース定義のないTC参照、期待値の根拠の規定外表記、
   hazard 境界ケースの漏れ。
   NG ゼロにしてから次へ（⚠未確定は承認依頼時点では残ってよい。approved 時にゼロ）
4. **人へ承認依頼**: ケース一覧（分類・根拠の内訳）＋要回答の質問一覧を提示
   （人は CLI `review_actions.py approve/request-changes testspec …` で直接返してもよい。
   その場合は反映まで CLI 側で完結する）
5. 人が質問に回答したら期待値を確定（根拠を「人間確認済み」に）。ドメイン知識として
   残すべき回答は転記文を提案し、**人が domain-knowledge.md に貼る**（AI は書き込まない）
6. 人のOKが出たら `review_actions.py approve testspec <func-id> --by <名前>` 相当で
   status: approved に反映（機械レビューの再検証込み）→ `ledger wbs`
   （CLI で承認済みの場合この更新は不要）

## 例外・数値特異点からの境界ケース導出（機械が突合する）

①の「例外・数値特異点」節には `hazard × 適用EP × 仕様記述` の表がある。
**決定が挙動に現れる hazard には、hz_id を引用した TC を必ず書く**:

| 決定 | 境界ケースの要否 | 書くこと |
|---|:---:|---|
| `guard_raise` | **必須** | 特異点で規定の例外が送出されること（0・0近傍・負値・添字の下限上限） |
| `guard_value` | **必須** | 特異点で代替値が返ること（①に書かれた値そのもの） |
| `legacy_preserve` | **必須** | Inf/NaN 等のレガシー挙動が再現されること |
| `detect_only` | 任意 | （挙動を変えない決定なので必須ではない） |
| ポリシー未決定 | 対象外 | そもそも①が書けていない＝①へ差し戻す |

- ケース名・期待結果のどこかに **`H-xxxx-nn` を文字列として含める**
  （`review_checks.py testspec` がこれを突合する。引用が無いと NG）
- 期待値の根拠は①の記述から導く。①に書かれていなければ推測せず ⚠未確定 → ISSUE
- **legacy/ は読まない**。hazard の情報源は①の節だけ（情報遮断は従来どおり）

## 完了条件（approved にしてよい条件）

- 全🟢仕様項目にケース1件以上 / ⚠未確定ゼロ / 人のOK
- 挙動が変わる hazard（guard_raise / guard_value / legacy_preserve）に境界ケースがある

## 禁止

- legacy/・src/・tests/ を読むこと
- 期待値を推測で埋めて根拠を「仕様書🟢」と偽ること
- 人のOKなしで approved にすること
- 挙動が変わる hazard の境界ケースを省くこと
