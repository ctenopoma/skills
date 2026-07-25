---
name: d2w-6-check
description: desktop2webのフェーズ⑥。全画面がe2e-pass・全機能が⑤pass・CRUD網羅を機械検証し、最終レポートと成果物を出す。「Web化の完了チェック」で使う。
user-invocable: true
---

# d2w-6-check — ⑥ 完了検証

親skill desktop2web の references/ に従う。

## チェック項目

1. Ⓐ方針書が approved
2. 全画面票が e2e-pass（E2E の frozen ハッシュが現物と一致していること）
3. 全機能が legacy-reverse の⑥基準を満たす（`ledger check` を内部実行）
4. **CRUD網羅**: `d2w crud` を再生成し、(a) legacy で存在した 機能×テーブル 操作が
   新実装側に全て存在する（消えた操作＝機能落ち）、(b) legacy に無かった操作が
   増えていない（増えた操作＝勝手な機能追加）を突合
5. 旧画面カバレッジ: legacy_screens の全 L-xxx がいずれかの新画面票の `legacy` に
   現れている（拾い忘れた画面の検出。意図的な廃止は方針書に記載があること）
6. open ISSUE ゼロ

## 手順

1. 上記を機械チェックして docs/completion-check.md を生成（不備は関数×フェーズ／
   画面×状態の作業リストにして人へ報告。このskillでは直さない）
2. pass なら最終レンダリング: `d2w wbs` → quarto render → Sphinx（backend）→
   PDF（legacy-reverse の pdf_book.py。仕様書・テスト仕様書・テスト結果＋画面票の4冊）
3. ⑦（legacy-7-analyze。バックエンドの性能・保守性・セキュリティ）へ誘導。
   フロント側の性能（バンドルサイズ・LCP）は⑦の観点に追加してよい

## 禁止

- 不備をこのskill内で直すこと／completion-check.md の手編集
