---
name: legacy-5-test
description: レガシー移植パイプラインのフェーズ⑤。テストを実行して結果報告書を自動生成し、失敗時は実装修正ループを回す（テスト側の変更は禁止・人の裁定制）。「F-xxxx のテストを回して」で使う。
user-invocable: true
---

# legacy-5-test — ⑤ テスト実行とループ

親skill legacy-reverse の references/workflow.md に従う。
`LR` = legacy-reverse のルート、`ledger` = `python <LR>/scripts/ledger.py`。

引数: func-id。

## 手順

1. 事前検証（NG なら理由を報告して停止）:
   ```bash
   ledger verify <func-id>    # ②のstale・テスト改変・blocked を検知
   ```
2. `ledger phase-start 5 <func-id>`（hook が tests/ 編集をブロック）
3. 実行と収集:
   ```bash
   PYTHONPATH=<LR>/scripts pytest <test_file> -p tc_report_plugin
   python <LR>/scripts/collect_results.py <func-id>
   ```
   exit 0=pass / 1=fail / 2=blocked（上限到達） / 3=マーカー突合エラー
4. 結果別の対応:
   - **pass** → `ledger phase-end` → `ledger wbs` → 完了報告
   - **fail** → 失敗をトリアージして分類を報告書の「トリアージ分類（暫定）」に記入:
     - **(a) 実装が①を満たしていない** → src/ を修正して手順3へ戻る（承認不要。
       修正対象は src/ のみ。①と②は読み比べてよいが編集しない）
     - **(b) テストコードが②と食い違う / (c) ②①の仕様自体が怪しい** →
       証拠（②の記述 vs テストコードの実装 vs 実測値）付きで ISSUE を起票し、
       `ledger phase-end` して**停止**。人の承認後の正規経路は
       (b): /legacy-3-testcode で再実装→再freeze、(c): /legacy-1-spec 改訂→伝搬
   - **blocked (exit 2)** → 自動起票された triage ISSUE の「仮説」欄に失敗分析
     （何を試し、なぜ駄目だったか、(a)/(b)/(c) どれと考えるか）を記入し、
     `ledger phase-end` して停止。人へ裁定を依頼する
   - **exit 3** → ケースIDとマーカーの不整合。③の不備なので ISSUE 起票して停止
5. どの経路でも最後に `ledger wbs`

## 再開（人の裁定後）

1. 人が ISSUE に回答 → 反映（(a)ヒントなら④へ、(b)なら③、(c)なら①→②→③伝搬）
2. `ledger unblock <func-id>`（attempt が1にリセットされる）
3. 人がこのskillを再トリガ

## 禁止

- tests/ の編集（hook が拒否する。回避を試みない）
- ISSUE・承認なしで (b)(c) 側の変更をすること
- attempt を稼ぐための空実行
