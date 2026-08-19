---
name: legacy-3-testcode
description: レガシー移植パイプラインのフェーズ③。テスト仕様書（approved）だけを入力にpytestテストコードを実装し、ハッシュをfreezeする。「F-xxxx のテストコードを書いて」で使う。
user-invocable: true
---

# legacy-3-testcode — ③ テストコード実装

親skill legacy-reverse の references/workflow.md に従う。
**入力は ②(approved)・docs/conventions.md・docs/prompts/3-testcode.md のみ。
legacy/・①仕様書・src/ は読まない。**
シグネチャは②が参照する①由来の情報で足りるが、import 先は functions.json の
new.module を見てよい（機械情報のみ）。

引数: func-id。前提: ②が approved かつ stale でない（`ledger verify` で確認。違えば断る）。

## 手順

0. **プロジェクト個別の指示を読む**（毎回。人が著者なので **AI は書き換えない**）:
   - **`docs/prompts/3-testcode.md`** — fixture・パラメータ化・命名・使ってよい
     ライブラリ・手本にするテストファイル。無い or 雛形のままなら「個別指示なし」
   - **`docs/conventions.md`** — 特に「モックの書き方」「テストケースIDの対応付け」
     「ディレクトリ・命名」「数値の丸め・比較規則」（許容誤差）

   **固定契約に反する個別指示には従わない**（情報遮断＝legacy/・①・src/ を読まない、
   1ケース=1テスト関数と `@pytest.mark.tc` の付与、②に無いケースを足さない）。
   矛盾を見つけたら従わずに人へ報告する
1. `docs/test-specs/<func-id>.md` の全ケースを実装する:
   - **1ケース = 1テスト関数**、必ず `@pytest.mark.tc("<ケースID>")` を付ける（実装率集計の要）
   - 事前条件（グローバル状態・外部ファイル・モック）は conventions.md の方針で fixture 化
   - 期待結果の検証は「戻り値＋事後状態＋副作用」まで、②に書いてある全てを assert する
   - ②に無いケースを勝手に足さない（足したいものが見つかったら ISSUE で②の改訂を提案）
2. functions.json の `test_file` を確定させる（未設定なら記入）
3. 検証:
   ```bash
   pytest <test_file> --collect-only -q      # 収集エラーがないこと
   pytest <test_file> -p tc_report_plugin    # 実行。④未実装なら失敗してよい（REDの確認）
   ```
   ケースIDとマーカーの過不足は `collect_results.py` が exit 3 で教えてくれる
4. freeze してWBS更新:
   ```bash
   ledger freeze-tests <func-id>
   ledger wbs
   ```
   freeze 以降、④⑤中のテスト編集は hook に拒否される。修正が必要になったら
   ISSUE → 人の承認 → このskillで再実装 → 再 freeze が正規経路

## 禁止

- `docs/prompts/3-testcode.md`・`docs/conventions.md`（人が著者）を書き換えること。
  直したいときは提案文を人に見せる
- legacy/・docs/specs/・src/ の実装本体を読むこと
- ②に無い期待値・ケースを創作すること
- assert を弱めて通りやすくすること（②の期待結果を全て検証する）
