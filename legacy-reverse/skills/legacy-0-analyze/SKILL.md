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

### 2. 解析（言語ごとに方法を選ぶ）

抽出する情報は schema.md の functions.json スキーマが正。関数ごとに:
入力 / 出力 / グローバル状態（読み書き別）/ 参照外部ファイル / 呼び出しサブルーチン。

- **Fortran**: `subroutine`/`function`/`entry` を列挙。COMMON ブロック・module 変数・
  EQUIVALENCE がグローバル状態。`open/read/write` 文が外部ファイル。`call` が呼び出し
- **C#**: public/internal メソッドを列挙。static フィールド・シングルトンがグローバル状態。
  `File.*`/`Stream` 系が外部ファイル
- その他の言語: 同じ抽出項目を満たす方法をその場で設計する（grep→LLM読解の併用）

大規模なら Explore サブエージェントでファイル分割して並列抽出し、結果をマージする。

### 3. 完全性チェック（必須）

関数定義の機械カウント（grep 等）と functions.json の件数を突合する。
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

### 5. 報告

関数数・コールグラフの循環有無・open ISSUE・推奨着手順の先頭5件を報告し、
`/legacy-1-spec` へ誘導する。

## 禁止

- functions.json に確信のない情報を書くこと（不明は ISSUE に出す。推測で埋めない）
- WBS・骨子の手書き修正（必ず functions.json を直して再生成）
