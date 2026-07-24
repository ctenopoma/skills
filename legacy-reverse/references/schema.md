# データスキーマ

対象プロジェクト側のディレクトリ構成と、台帳ファイルのスキーマ。パスはすべて対象プロジェクトのルート基準。

## プロジェクト構成

```
<project>/
  legacy/               # レガシー原文（読み取り専用。読めるのは ⓪ と ①改訂エージェントのみ）
  src/<package>/        # ④ 新実装
  tests/                # ③ テストコード（⑤ループ中は hook が編集拒否）
  docs/
    _quarto.yml         # HTMLサイト＋PDF出力設定
    index.qmd           # WBS（ledger.py wbs で自動生成。手編集禁止）
    conventions.md      # プロジェクト規約（⓪で確定）
    domain-knowledge.md
    specs/F-xxxx.md
    test-specs/F-xxxx.md
    test-results/F-xxxx_YYYYMMDD-HHMM.md
    issues/ISSUE-xxx.md
    completion-check.md # ⑥（ledger.py check で自動生成）
  data/
    functions.json      # ⓪の解析結果（正データ）
    ledger.json         # ハッシュ・ブロック状態の台帳（スクリプトのみが書く）
  .legacy-reverse/
    state.json          # 実行中フェーズ（hook の判定に使う）
    last-run.json       # pytest プラグインの出力（⑤の一時データ）
```

## data/functions.json

```json
{
  "project": {
    "name": "string",
    "legacy_lang": "fortran | csharp | ...",
    "new_lang": "python",
    "package": "newpkg"
  },
  "functions": [
    {
      "func_id": "F-0123",
      "legacy": { "file": "legacy/tax.cbl", "name": "CALC-TAX", "lines": "120-240" },
      "new": {
        "module": "src/newpkg/tax.py",
        "name": "calc_tax",
        "signature": "calc_tax(amount: Decimal, rate_code: str) -> Decimal"
      },
      "inputs":  [ { "name": "WK-AMOUNT", "legacy_type": "PIC 9(9)V99", "new_type": "Decimal", "desc": "課税対象額" } ],
      "outputs": [ { "name": "WK-TAX", "legacy_type": "PIC 9(9)V99", "new_type": "Decimal", "desc": "税額" } ],
      "globals": [ { "name": "TAX-RATE-TABLE", "access": "read", "desc": "税率マスタ" } ],
      "external_files": [ { "path": "RATEMST.dat", "access": "read", "desc": "税率マスタファイル" } ],
      "calls": [ "F-0087" ],
      "test_file": "tests/test_tax.py"
    }
  ]
}
```

- `func_id` は `F-` + 4桁連番。`calls` は func_id の配列（コールグラフの正データ）
- `test_file` は ⓪では省略可。③が確定させる

## data/ledger.json

スクリプト（ledger.py / collect_results.py）だけが読み書きする。手編集禁止。

```json
{
  "F-0123": {
    "test_code_hash": "e5f6a7b8",        // ③ freeze 時のテストファイル sha256 先頭8桁
    "blocked_by": null,                    // ループ上限到達時の ISSUE-ID。人の裁定後 unblock
    "attempt_reset_at": "2026-07-25T10:00" // これ以降の結果ファイル数 = attempt
  }
}
```

## .legacy-reverse/state.json

```json
{ "phase": "5", "func_id": "F-0123" }
```

- フェーズskillが開始時に `ledger.py phase-start <n> <func-id>`、終了時に `phase-end` で管理
- hook (guard_tests.py) は phase が 4 または 5 のとき tests/ への Edit/Write を拒否する

## フロントマターの status 遷移（完了判定の正）

| 成果物 | 遷移 | WBS ✅条件 |
|---|---|---|
| ①spec | skeleton → draft → reviewed | reviewed |
| ②test-spec | generated → approved | approved かつ spec-hash が現物と一致 |
| ③test-code | （ファイル＋ledger） | ledger の test_code_hash が現物と一致 |
| ④impl | （ファイル） | new.module が存在しスタブ検出ゼロ |
| ⑤test | （最新結果ファイル） | result: pass（＝実装率100%かつ失敗0） |
