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

- レガシーコードの場所と言語（機械抽出できるのは Fortran / C・C++。その他は AI と人で列挙）
- 新パッケージ名・src 配置
- conventions.md の中身: 型対応表（例: `PIC 9(9)V99`→`Decimal`、`REAL*8`→`float`）、
  命名規則、モック方針、**後戻り高コスト項目**（丸め・⑤の許容誤差／単位・スケール／
  日付規則／文字コード／既知バグの扱い既定。②の期待値の前提になるため、後から覆ると
  ②以降が全て作り直しになる）。
  **conventions.md は人だけが書くファイル**（workflow.md「ファイルの作成者区分」）。
  AI がやるのは、テンプレ `assets/templates/conventions.md` を docs/conventions.md へ
  コピーして枠を用意し、決めるべき項目の質問リストと記入例をチャットに提示するまで。
  **記入・確定は人**が行い、記入後に AI が読み合わせて矛盾・抜けを指摘する

### 2. 解析（機械抽出が正。LLMは意味づけのみ）

抽出する情報は schema.md の functions.json スキーマが正。関数ごとに:
入力 / 出力 / グローバル状態（読み書き別）/ 参照外部ファイル / 呼び出しサブルーチン。

- **Fortran**: **必ず機械抽出を使う**（LLMで列挙しない）:
  ```bash
  python <LR>/scripts/extract_fortran.py --root . --package <pkg> --write
  ```
  （MCP登録済みなら `extract_functions` ツール。）subroutine/function/entry と
  **メインルーチン（program ユニット → F-0000 に採番。program 文の無い F77 の
  暗黙メインも検出する。判定はファイル名によらない）**の列挙、
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

### 3.5. グラフの確認（抽出直後・LLMは数えない）

```bash
python <LR>/scripts/graph.py --root . summary   # ノード/エッジ数・エントリ・到達率・dead件数
python <LR>/scripts/graph.py --root . dead      # どのエントリからも到達不能な関数
```

（MCP登録済みなら `graph_query`。）

- `summary` の到達率が極端に低い／エントリが想定と違うときは、抽出漏れか
  メインルーチンの誤判定を疑う（`unresolved_calls` と併せて確認する）
- `dead` は**exclude 候補の列挙にすぎない**。自動除外はしない。
  1件ずつ人に「これは移植対象外でよいか」を確認し、OK のものだけ
  `ledger exclude F-xxxx --reason "..."` にする
- 循環があれば `graph.py cycles`（WBS の警告と同じもの）で内訳を見る

### 3.6. フロー定義のヒアリング（任意。該当するときだけ）

**次のどちらかに当てはまるときだけ**、人に「作業スコープを分けますか」と聞く:

- メインルーチンが複数ある（`F-0000` 以外にも実質のエントリがある）
- main の中に大きな分岐があり、系統ごとに別々に進めたい

```bash
ledger flow add 月次バッチ --entry F-0000 --desc "..."
ledger flow list
```

エントリは「分岐先の代表サブルーチン」でよい。定義すると WBS に「フロー別進捗」表が出て、
`ledger next --flow <名前>` / `pipeline.py spec|run|dict --flow <名前>` で対象を絞れる。
**当てはまらないなら定義しない**（未定義時は F-0000 を既定エントリとする従来動作）。

### 3.7. 例外ポリシーの初期決定（hazards。人に聞く）

⓪の抽出は数値特異点（0割・SQRT/LOG の定義域・変数添字）を機械検知して
functions.json の `hazards` に記録している。**Fortran は 0割でも走り続けるが Python は
停止する**ので、ここで扱いを決めておかないと①以降が進まない。

```bash
python <LR>/scripts/hazards.py status --root .   # 総数・kind別・決定済み/未決定
python <LR>/scripts/hazards.py match  --root .   # 突合 → docs/exception-queue.md（質問キュー）
```

1. `docs/exception-queue.md` を人に見せる（kind ごとに仮説・選択肢・該当箇所が出ている）
2. **kind ごとの既定をどうするか人に聞く**。AIが決めない。語彙は
   `detect_only` / `guard_raise` / `guard_value`（値も聞く）/ `legacy_preserve` /
   `caller_guarantees`（根拠も聞く）
3. **人が**登録コマンドを実行する（exception-policy.md は人だけが書くファイル。
   AI は exception-queue.md に出ているコマンド例を示すまで。EP-ID 自動採番。
   登録すると自動で再突合される）:
   ```bash
   python <LR>/scripts/hazards.py add-policy --kind div_by_var --decision guard_raise \
          --by <承認者> --note "..." --root .
   ```
   個別に変えたい箇所だけ `--func F-0012` / `--hazard H-0012-01` を付けて追加登録する
   （個別が全体既定に勝つ）
4. `status` の未決定が 0 になるのが⓪の完了条件のひとつ
   （残ったまま①へ進むと `review_checks.py spec` が NG にする）

### 3.8. 変数辞書の初期構築

```bash
python <LR>/scripts/variables.py build --root .
```

（MCP登録済みなら `dict_build`。）COMMON の同一位置・実引数↔仮引数・EQUIVALENCE・
同一関数内の同名から変数クラスタを作り、コメント・FORMAT 文字列・初期値・使用式を
根拠として収集する。**再実行は常にマージ**（var_id 不変・承認維持）。

出力された件数（rank・同名別義）を報告し、**辞書フェーズ `/legacy-0-dict` へ誘導する**。
既定では変数の語義が未承認の関数は①に進めない（dict-gate）ので、
⓪の次は①ではなく辞書、という順になる。

### 3.9. ドメイン知識の先行投入（略語・区分値。人に聞く）

辞書の解釈を始める前に、人が知っている語彙を `docs/domain-knowledge.md` の
「語彙・略語集」「区分値・番兵値」へ先行投入する。
**domain-knowledge.md は人だけが書くファイル**——AI はテンプレ
（`assets/templates/domain-knowledge.md`）のコピーで枠を用意し、記入候補を提示するまで:

1. build の出力・変数名一覧から頻出トークン（例: `ZAN`・`KBN`・`RITU`）を人に見せ、
   「読める略語はあるか」を聞く。**わかるものだけでよい**（網羅は求めない）
2. 区分値・番兵値（KBN=9 はエラー、999999=上限なし等）で自明なものがあれば聞く
3. 回答を表形式の**転記文として提示**（出典:「⓪ヒアリング YYYY-MM-DD」付き）し、
   **人が表に貼って保存**したら AI が `render_site.py` でHTML更新

`verify-interp` の rank B 判定は domain-knowledge.md の語との一致で決まるため、
ここで投入した語彙は辞書解釈を一括承認候補（rank B）へ押し上げ、
rank C/D の1件ずつ確認と ISSUE の往復を減らす。
**人がわからない語を推測で埋めないこと**（それは辞書フェーズの根拠ベース解釈が受け持つ）。

### 4. 生成

```bash
ledger init-templates   # 仕様書テンプレのシードを docs/templates/ へコピー（初回のみ）
ledger skeletons        # docs/specs/ に骨子（フロントマター＋IO表は⓪時点で充填済み）
ledger wbs              # docs/index.qmd
```

- **docs/templates/（spec.md・test-spec.md）は人だけが書くファイル**。仕様書の項目立てと
  書き方ガイドをプロジェクトに合わせて人が編集する（固定契約は workflow.md「固定と可変」。
  編集後は `review_checks.py template --root .` で契約チェック）。編集不要ならそのままでよい

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

関数数・コールグラフの循環有無・dead 件数・hazard の決定済み/未決定・変数辞書の件数・
open ISSUE・推奨着手順の先頭5件を報告し、**`/legacy-0-dict`（辞書フェーズ）へ誘導する**。
辞書を使わない（dict-gate を解除して進める）と人が決めた場合だけ `/legacy-1-spec` へ。

## 禁止

- **conventions.md / domain-knowledge.md / exception-policy.md / docs/templates/ に
  AI が書き込むこと**（テンプレの初期コピーを除く。提案文の提示まで）
- functions.json に確信のない情報を書くこと（不明は ISSUE に出す。推測で埋めない）
- WBS・骨子の手書き修正（必ず functions.json を直して再生成）
- `graph.py dead` の結果を人に確認せず exclude すること
- 例外ポリシー（EP）を人に聞かずに決めること
