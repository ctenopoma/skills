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
    wbs.css             # WBS 関数一覧の列幅（テンプレからコピー）
    index.qmd           # WBS（ledger.py wbs で自動生成。手編集禁止）
    wbs/                # 200関数超で自動生成されるファイル別明細ページ（手編集禁止）
    spec-review.md      # ①draft の一斉レビュー表（review_checks.py report が自動生成。手編集禁止）
    review-feedback.md  # ブラウザの承認ウィジェットからの修正依頼（人が著者。AIが起動時に読み applied 化）
    _site/              # HTML出力（render_site.py が作り直す。git 管理外）
    _sitework/          # render 用の .qmd 影コピー（render_site.py の作業用。残らない）
    conventions.md      # プロジェクト規約（⓪で確定）
    domain-knowledge.md
    specs/F-xxxx.md
    test-specs/F-xxxx.md
    test-results/F-xxxx_YYYYMMDD-HHMM.md
    issues/ISSUE-xxx.md
    completion-check.md # ⑥（ledger.py check で自動生成）
  data/
    functions.json      # ⓪の解析結果（正データ。Fortran は extract_fortran.py が生成・マージ）
    extract-report.json # ⓪機械抽出の監査ログ（完全性突合・推定呼出・未解決名・マージ差分）
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
- **`F-0000` はメインルーチン予約番号**（Fortran の `program` ユニット / program 文の
  無い F77 暗黙メイン / C の `main`。判定はファイル名によらない）。
  候補が複数見つかった場合は最初の1件が F-0000、残りは通常採番＋警告（⓪で確認）。
  コールグラフの根になるため、推奨着手順では最後に回る
- `test_file` は ⓪では省略可。③が確定させる
- `unresolved_calls`（任意）— 抽出時に functions.json 内で解決できなかった呼び出し名。
  別言語の抽出（extract_c.py 等）が走った時点で自動解決されて calls に移る
  （Fortran↔C のアンダースコア規約 `foo`/`foo_` も突合される）。
  残っているものは外部/ベンダー関数の疑いとして⓪でレビューする
- `project.legacy_lang` は混在プロジェクトでは `"fortran+c"` のように連結される
- 任意フィールド（人の後追い調整。`ledger add / exclude / include` が読み書きする）:
  - `"manual": true` — 人の指示で後追い追加したエントリ（`ledger add`）。
    抽出の再実行で「ソースに無い」警告を出さない。実ソースで確認されたら自動で外れる
  - `"excluded": true` + `"excluded_reason": "..."` — 移植対象外（`ledger exclude`）。
    ①〜⑥・WBS・next の対象から外れ、WBS の「対象外の関数」に理由つきで載る
- **エントリの物理削除は禁止**。抽出の再実行でソースから別 func_id として復活し、
  成果物との紐付けが切れる。対象から外すのは必ず `ledger exclude`（フラグ）で行う

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

承認時は `reviewed-by`/`reviewed-date`（①）、`approved-by`/`approved-date`（②）を記録する
（誰が・いつ）。チャット承認・ブラウザ承認（`/review-action`）のどちらでも同じフィールドに書く。
draft / generated の間、render_site.py はそのページに承認ウィジェット（機械レビュー結果＋
承認・修正依頼ボタン）を埋め込む。詳細は workflow.md「人の承認ゲート」。
