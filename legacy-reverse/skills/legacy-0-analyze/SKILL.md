---
name: legacy-0-analyze
description: レガシー移植パイプラインのフェーズ⓪。レガシーコードを解析して全関数リスト（functions.json）を作り、WBS・関数仕様書骨子・プロジェクト規約を生成する。「レガシーを解析して」「関数リストを作って」「移植の土台を作って」で使う。
user-invocable: true
---

# legacy-0-analyze — ⓪ リポジトリ解析

親skill legacy-reverse の references/workflow.md・schema.md に従う。
`ledger` = `python <legacy-reverseのルート>/scripts/ledger.py`。

## 手順

### 1. ヒアリング（勝手に決めない）

- レガシーコードの場所と言語（Fortran / C# / その他）
- 新パッケージ名・src 配置
- conventions.md の中身: 型対応表（例: `PIC 9(9)V99`→`Decimal`、`REAL*8`→`float`）、
  命名規則、モック方針。テンプレ `assets/templates/conventions.md` を埋めて
  **docs/conventions.md として確定させ、人のOKをもらう**

### 2. 解析（機械抽出が正。LLMは意味づけのみ）

抽出する情報は schema.md の functions.json スキーマが正。関数ごとに:
入力 / 出力 / グローバル状態（読み書き別）/ 参照外部ファイル / 呼び出しサブルーチン。

- **Fortran**: **必ず機械抽出を使う**（LLMで列挙しない）:
  ```bash
  python <LR>/scripts/extract_fortran.py --root . --package <pkg> --write
  ```
  （MCP登録済みなら `extract_functions` ツール。）subroutine/function/entry の列挙、
  引数と型/intent、COMMON、USE、`open/inquire` の外部ファイル、call と関数参照の
  呼び出し推定まで自動。**再実行は常にマージ（func_id 不変・手修正保持）なので、
  中断してもやり直しにならない。** LLMの仕事はこの後だけ:
  1. `data/extract-report.json` を読み、`completeness_mismatches`（2系統カウントの
     差分）と `inferred_calls`（関数参照からの推定。配列参照の誤検知があり得る）と
     `unresolved_calls`（外部/ベンダー関数の疑い）をレビューして functions.json を確定
  2. 各関数・引数の desc を充填し、型対応表に従って new.signature を決める
- **C / C++**（Fortran が依存する数値ルーチン・I/O ラッパ等も含めて対象にする）:
  ```bash
  python <LR>/scripts/extract_c.py --root . --write
  ```
  （MCP登録済みなら `extract_c_functions` ツール。）ファイルスコープの関数定義・
  クラス外メソッド定義（`Class::method`）・引数と型（ポインタ/非const参照は出力候補）・
  ファイルスコープ変数の読み書き・fopen/fstream の外部ファイル・呼び出しを抽出する。
  **Fortran↔C の呼び出し（`CALL FOO` ↔ `foo`/`foo_` のアンダースコア規約）は
  マージ時に自動でリンクされる。** 片方の抽出時点で未解決だった呼び出し名は
  エントリの `unresolved_calls` に残り、もう片方の抽出で解決されるので実行順は不問。
  K&Rスタイル・関数マクロ・テンプレート特殊化は非対応→完全性突合の差分としてレビューに回る
- **C#**: public/internal メソッドを列挙。static フィールド・シングルトンがグローバル状態。
  `File.*`/`Stream` 系が外部ファイル（機械抽出器が未整備のため従来手順。
  抽出方法を設計したらスクリプト化して scripts/ に足すこと）
- その他の言語: 同じ抽出項目を満たす方法をその場で設計する（grep→LLM読解の併用）

### 3. 完全性チェック

Fortran は extract_fortran.py が内蔵（状態機械パースと素朴カウントの2系統突合。
`completeness_mismatches` が空なら OK）。それ以外の言語は関数定義の機械カウント
（grep 等）と functions.json の件数を突合する。
不一致は原因を特定。判断がつかないものは ISSUE 起票（`ledger next-issue` で採番）。

### 4. 生成

```bash
ledger skeletons   # docs/specs/ に骨子（フロントマター＋IO表は⓪時点で充填済み）
ledger wbs         # docs/index.qmd
```

- `assets/templates/_quarto.yml` と `assets/templates/wbs.css` を docs/ にコピーし、
  _quarto.yml のプロジェクト名を埋めて、初回の
  `python <LR>/scripts/render_site.py --root .` を実行する（`quarto render docs` は
  直接叩かない＝Mermaid が描画されない。以後、全フェーズの最後にHTML更新する。workflow.md 参照）
- WBS のコールグラフ図は `ledger wbs` が functions.json の `calls` から自動生成する。
  図が読めない規模（60関数超）では省略され、依存は表の「依存」列だけになる

新関数のシグネチャ（functions.json の new.signature）は型対応表に従って⓪で決める。
これが③④の共通契約になる。

### 5. 人の調整（⓪以降いつでも）

関数リストは人がいつでも調整できる。**どちらも functions.json の手書き編集や
エントリの物理削除では行わない**（再抽出で別IDとして復活し、成果物との紐付けが切れる）:

- **追加**（抽出漏れ・関数分割など）: `ledger add NAME --file legacy/x.f --lines 10-50`
  → manual フラグ付きで採番される。inputs/outputs/desc/signature を functions.json に
  充填してから `ledger skeletons` → `ledger wbs`。以後は他の関数と同じ①〜⑤の対象になる
- **削除**（デッドコード・移植不要の判断）: `ledger exclude F-xxxx --reason "理由"`
  → ①〜⑥・WBS・next の対象から外れ、WBS の「対象外の関数」に理由つきで残る。
  復帰は `ledger include F-xxxx`。呼び出し元が残っている場合は警告が出るので、
  仕様への影響を確認し、判断が要るなら ISSUE に出す

### 6. 報告

関数数・コールグラフの循環有無・open ISSUE・推奨着手順の先頭5件を報告し、
`/legacy-1-spec` へ誘導する。

## 禁止

- functions.json に確信のない情報を書くこと（不明は ISSUE に出す。推測で埋めない）
- WBS・骨子の手書き修正（必ず functions.json を直して再生成）
