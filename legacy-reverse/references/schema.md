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
    review-feedback.md  # 修正依頼（人が著者: 直接記入 or review_actions.py request-changes。AIが起動時に読み applied 化）
    variables.qmd       # 変数辞書ページ（variables.py page が自動生成。手編集禁止。承認は variables.py approve/revise）
    dict-conflicts.md   # 辞書と reviewed 仕様書の矛盾候補（variables.py conflicts が自動生成。手編集禁止）
    exception-policy.md # 例外ポリシー登録簿 EP-xxx（人が承認する規約。hazards.py add-policy が追記）
    exception-queue.md  # 未決定 hazard の質問キュー（hazards.py match が自動生成。手編集禁止）
    _site/              # HTML出力（render_site.py が作り直す。git 管理外）
    _sitework/          # render 用の .qmd 影コピー（render_site.py の作業用。残らない）
    templates/          # 仕様書の項目立て・書き方テンプレ（人が著者。ledger init-templates でシード配置）
                        #   ※ conventions.md・domain-knowledge.md・exception-policy.md・prompts/ も同じコマンドが配置する
    prompts/            # 工程別のPJ個別指示 1-spec/2-testspec/3-testcode/4-impl.md
                        #   （人が著者。①〜④が起動のたびに読む。無い・雛形のままなら個別指示なし）
    conventions.md      # プロジェクト規約（⓪で人が記入・確定。人だけが書く）
    domain-knowledge.md # 業務知識・ISSUE回答の蓄積（人だけが書く。AIは転記文の提案まで）
    specs/F-xxxx.md
    test-specs/F-xxxx.md
    test-results/F-xxxx_YYYYMMDD-HHMM.md
    issues/ISSUE-xxx.md
    completion-check.md # ⑥（ledger.py check で自動生成）
  data/
    functions.json      # ⓪の解析結果（正データ。Fortran は extract_fortran.py が生成・マージ）
    extract-report.json # ⓪機械抽出の監査ログ（完全性突合・推定呼出・未解決名・マージ差分）
    ledger.json         # ハッシュ・ブロック状態の台帳（スクリプトのみが書く）
    variables.json      # 変数辞書（variables.py build が生成・マージ。スクリプトのみが書く）
    interpretations.json          # LLM の解釈の受け渡し用（verify-interp が検証後に消費して削除）
    interpretations-applied-*.json # 消費済みの解釈（verify-interp が退避。監査用）
    hazard-map.json     # hazard → 適用EP・決定 の突合結果（hazards.py match が生成）
  .legacy-reverse/
    state.json          # 実行中フェーズ（hook の判定に使う）
    last-run.json       # pytest プラグインの出力（⑤の一時データ）
    dict-targets.json   # 辞書解釈バッチが LLM に渡す根拠バンドル（pipeline.py dict の一時データ）
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
      "call_sites": [
        { "callee": "F-0087", "name": "CALCTAX", "line": 152, "args": ["WK-AMOUNT", "RATE", null] }
      ],
      "hazards": [
        { "hz_id": "H-0123-01", "kind": "div_by_var", "line": 152, "expr": "X / Y", "vars": ["Y"] }
      ],
      "test_file": "tests/test_tax.py"
    }
  ],
  "flows": [
    { "flow_id": "FL-01", "name": "月次バッチ", "entries": ["F-0000"], "desc": "" }
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
- `call_sites`（任意）— 呼び出し1件ごとの物理行と実引数。`args` は実引数のうち単純な
  変数名だけを大文字で入れ、式・リテラル・還元できないものは `null`（`X(I)` は `X` に落とす）。
  `callee` は解決できた場合のみ入り、未解決なら `name` だけ残る。call 文でない関数参照
  （`Y = CALCTAX(X)`）から推定したものは `"inferred": true` が付く。
  `calls` の生成ロジックは従来どおりで、call_sites は付加情報（variables.py の
  実引数↔仮引数クラスタリングが使う）
- `hazards`（任意）— ⓪が機械検知した数値特異点。`hz_id` は `H-<func_id連番部>-<2桁枝番>`
  （関数内連番）。初期 kind は `div_by_var` / `sqrt_arg` / `log_arg` / `array_index_var`。
  `hazards.py match` が docs/exception-policy.md と突合する
- **`call_sites` と `hazards` はソースから完全に導出されるため、再抽出時は常に上書き**
  （手修正の対象外。`calls` や desc のような「手修正保持」は適用されない）
- `flows`（任意・トップレベル）— 作業スコープ。`ledger flow add/rm/list` が読み書きする。
  到達集合は保存せず graph.py がその都度計算する（再抽出に自動追随）。
  未定義時の既定エントリは F-0000（現行挙動と互換）
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

## data/variables.json（変数辞書）

`variables.py build` だけが生成・マージする。手編集禁止（語義の変更は
`variables.py revise` 経由）。設計の正は
[graph-dict-design.md](graph-dict-design.md) の「P2. 変数辞書」。

```json
{
  "variables": [
    {
      "var_id": "V-0001",
      "canonical_name": "RATE",
      "aliases": ["RATE", "R8TBL"],
      "desc": "年間税率", "unit": "無次元(比率)",
      "status": "unreviewed | interpreted | approved",
      "rank": "A | B | C | D",
      "confidence_basis": ["E-0001-01"],
      "occurrences": [
        { "func_id": "F-0001", "name": "RATE", "role": "input",
          "legacy_type": "REAL*8", "line": 120 }
      ],
      "links": [
        { "kind": "call_binding", "from": "F-0001:WK_R", "to": "F-0087:RATE", "line": 152 }
      ],
      "evidence": [
        { "ev_id": "E-0001-01", "kind": "comment", "file": "legacy/tax.f",
          "line": 118, "text": "C  RATE: ANNUAL TAX RATE" }
      ],
      "evidence_hash": "a1b2c3d4",
      "approved_by": null, "approved_date": null,
      "flags": []
    }
  ]
}
```

- `var_id` は `V-` + 4桁連番。ノード＝`(func_id, 変数名)` を Union-Find で結合した
  クラスタが1エントリ。結合根拠は①同一 COMMON の同一位置 ②call_sites の実引数↔仮引数
  ③EQUIVALENCE ④同一関数内の同名 の4つだけ（**別関数の同名は結合しない**）
- `evidence.kind`: `comment` / `format_label` / `data_init` / `usage_expr` / `common_pos`。
  `ev_id` は var 内連番。`evidence_hash` は evidence 配列の正規化 sha256 先頭8桁で、
  再 build 時の承認維持判定に使う
- `rank` は LLM の申告でなく `verify-interp` が根拠種別から決める
  （A: comment/format_label/data_init を引用 / B: domain-knowledge.md または links で
  結ばれた approved 済み変数 / C: usage_expr・common_pos のみ / D: 引用なし＝マージ拒否）
- `flags`: `name_collision`（同名別義）/ `occurrence_added`（出現だけ増えた＝承認は維持）/
  `cluster_changed`（分裂・併合で status を unreviewed に戻した）/ `needs_human`（rank D）
- **再 build は常にマージ**（var_id 不変・evidence_hash が変わらなければ承認と desc を維持）

## data/interpretations.json（LLM 解釈の受け渡し）

LLM（`pipeline.py dict` または `/legacy-0-dict`）が書く唯一のファイル。
variables.json は編集しない。`variables.py verify-interp` が機械検証してマージし、
成功したら `data/interpretations-applied-<YYYYMMDD-HHMM>.json` へ退避して元ファイルを消す。

```json
{ "V-0001": { "desc": "年間税率", "unit": "無次元(比率)",
              "rank_claim": "A", "evidence_cited": ["E-0001-01"], "notes": "" } }
```

- 検証: 対象 var_id 集合と完全一致（欠落・余剰は全件差し戻し）／`evidence_cited` が
  その var に実在する ev_id か（捏造検知）／desc が空でないか
- rank D（引用なし）は**マージせず** desc「不明」・`needs_human` で人のキューに残す

## data/hazard-map.json（hazard × 例外ポリシーの突合結果）

`hazards.py match` が生成する。review_checks.py が①②の検査で読む。

```json
{ "H-0012-01": { "ep": "EP-001", "decision": "guard_raise",
                 "func_id": "F-0012", "kind": "div_by_var" } }
```

- 未決定の hazard も `"ep": null, "decision": null` でエントリを持つ
  （＝①が仕様化してよいかの判定材料）。未決定分は docs/exception-queue.md に質問として出る
- 決定の語彙: `detect_only` / `guard_raise` / `guard_value` / `legacy_preserve` /
  `caller_guarantees`。適用範囲は 全体既定 → 関数 → 個別 hazard の順に個別が勝つ

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

①仕様書のフロントマターには、辞書がある（data/variables.json が存在する）プロジェクトでのみ
次の2キーが載る。どちらも `ledger skeletons` が骨子生成時に書き、**①は値を書き換えない**:

| キー | 値 | 意味 |
|---|---|---|
| `dict-hash` | `"a1b2c3d4"`（approved が0件なら `""`） | その関数の approved 変数の (var_id, desc) 集合の sha256 先頭8桁。①生成後に語義が改訂されると `ledger verify` が NG、WBS の要対応に「⚠辞書stale」が出る。骨子（status: skeleton）のままの仕様書は次の `ledger skeletons` が現在値へ自動同期する（承認直後にいきなり stale にならないため） |
| `flows` | `["月次バッチ"]` | 所属フロー名（骨子の新規生成時のみ記載。文脈付与だけで機械判定には使わない） |

承認時は `reviewed-by`/`reviewed-date`（①）、`approved-by`/`approved-date`（②）を記録する
（誰が・いつ）。チャット承認・CLI 承認（`review_actions.py approve`）のどちらでも同じ
フィールドに書く。draft / generated の間、render_site.py はそのページに閲覧専用の
案内パネル（機械レビュー結果＋返答方法）を焼き込む。詳細は workflow.md「人の承認ゲート」。

変数辞書の承認は成果物フロントマターではなく data/variables.json の
`status` / `approved_by` / `approved_date` に記録する（`variables.py approve / revise`。
チャット経由でも同じライブラリ関数を通る）。
