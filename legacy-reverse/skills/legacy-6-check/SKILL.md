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
   python <LR>/scripts/review_checks.py all --root .   # ①②の機械レビュー総点検
   ```
   チェック内容: ①reviewed / ②approved / ③hash一致 / ④存在 / ⑤pass /
   ハッシュ連鎖整合 / open ISSUE ゼロ / ⑤実装率100% ＋
   review_checks で根拠引用・トレーサビリティの総点検（両方 exit 0 が⑥pass）
2. **fail の場合**: 不備一覧を関数×フェーズで整理し、どのskillで埋めるかの
   作業リストにして人へ報告する（このskillでは直さない）
3. **pass の場合**: 最終成果物を出す（コマンドは workflow.md「出力・レンダリング」参照）
   - `ledger wbs` → `render_site.py` → Sphinx（この順。render_site が _site を作り直すため）
   - `pdf_book.py` で 関数仕様書 / テスト仕様書 / テスト結果報告書 の3冊のPDFを生成し、
     各PDFを `qtpdf.py check` で機械チェック
   - WBS ナビバーの「新コード詳細(API)」リンクが通ることを確認
4. 完了を報告。⑦（性能分析・静的解析リファクタリング）は別途着手する旨を添える

## 禁止

- 不備を⑥の中で「ついでに」直すこと（各フェーズskillの管轄に戻す）
- completion-check.md の手編集
