---
name: legacy-6-check
description: レガシー移植パイプラインのフェーズ⑥。全関数が①〜⑤を満たしているかを機械検証し、完了レポートと最終成果物（HTML/PDF）を出す。「完了チェックして」「移植の最終確認」で使う。
user-invocable: true
---

# legacy-6-check — ⑥ 完了検証

親skill legacy-reverse の references/workflow.md に従う。
全関数の①〜⑤が揃ってから実行する最終ゲート。

## 手順

1. 検証を実行:
   ```bash
   ledger check     # docs/completion-check.md を生成。不備ありなら exit 1
   ```
   チェック内容: ①reviewed / ②approved / ③hash一致 / ④存在 / ⑤pass /
   ハッシュ連鎖整合 / open ISSUE ゼロ / ⑤実装率100%
2. **fail の場合**: 不備一覧を関数×フェーズで整理し、どのskillで埋めるかの
   作業リストにして人へ報告する（このskillでは直さない）
3. **pass の場合**: 最終成果物を出す
   - `ledger wbs` で WBS を最新化
   - quarto-typst-pdf skill で docs/ を HTMLサイト＋PDF（仕様書・テスト仕様書・
     テスト結果の種別ごとに個別）にレンダリング
   - Sphinx（autodoc + napoleon）で src/ の docstring から新コード詳細仕様の HTML を生成し、
     WBS からリンクが通ることを確認
4. 完了を報告。⑦（性能分析・静的解析リファクタリング）は別途着手する旨を添える

## 禁止

- 不備を⑥の中で「ついでに」直すこと（各フェーズskillの管轄に戻す）
- completion-check.md の手編集
