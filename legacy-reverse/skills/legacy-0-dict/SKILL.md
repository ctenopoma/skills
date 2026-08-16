---
name: legacy-0-dict
description: レガシー移植パイプラインのフェーズ⓪拡張（変数辞書）。レガシーの変数を機械クラスタリングし、機械が集めた根拠だけを使って語義を解釈し、人の承認を経て仕様書へ伝搬させる。「変数辞書を作って」「変数の意味を整理して」「辞書の承認を進めたい」で使う。
user-invocable: true
---

# legacy-0-dict — ⓪ 変数辞書

親skill legacy-reverse の references/workflow.md・schema.md に従う。
設計の正は `references/graph-dict-design.md`「P2. 変数辞書」。
`ledger` = `python <LR>/scripts/ledger.py`、`vars` = `python <LR>/scripts/variables.py`。

**なぜ①より先か**: レガシーの変数名（`R8TBL`・`WKA`）は仕様書の IO 表にそのまま出る。
語義が確定していないまま①を書くと、同じ実体に関数ごとに違う説明が付き、
後から直すと全仕様書に波及する。先に「1変数=1語義」を人が確定させてから①へ渡す
（既定では未承認の語義が残る関数の①を止める＝dict-gate）。

## 原則

- **機械が正、AIは意味づけ**。クラスタリング・根拠収集・rank 判定・伝搬は
  variables.py が決定的に行う。AI が書けるのは `data/interpretations.json` だけ
- **legacy 全文は読まない**。読むのは機械が集めた根拠バンドルだけ
  （`.legacy-reverse/dict-targets.json` または `vars list-targets` の出力）
- **人の承認ゲート**。語義を確定させるのは人。AIは仮説＋根拠で聞く

## 手順

### 1. build（辞書の構築・再構築）

```bash
python <LR>/scripts/variables.py build --root .
```

ノード＝`(func_id, 変数名)`。結合は機械的根拠だけ（Union-Find）:
①同一 COMMON の同一位置 ②call_sites の実引数↔仮引数（位置対応）③EQUIVALENCE
④同一関数スコープ内の同名。**別関数の同名だけでは結合しない**
（同名別義は別エントリになり、相互に `name_collision` が立つ）。

出力の見方: `rank` 別件数 / `status` 別件数 / 同名別義・クラスタ変化・出現追加の件数。
**再実行は常にマージ**なので、いつ何度実行してもよい。

### 2. list-targets（解釈対象の確認）

```bash
python <LR>/scripts/variables.py list-targets --limit 30 --root .
```

status=unreviewed の変数について、var_id / 名前・別名 / 出現（関数・役割・型・行）/
links / evidence（`ev_id`・kind・file・line・text）だけを JSON で返す。
これが解釈の**唯一の入力**。

### 3. 解釈（少量はチャット、全件はバッチ）

**始める前に** `docs/domain-knowledge.md` の「語彙・略語集」を確認する。空なら
⓪の先行投入（legacy-0-analyze 手順3.9: 頻出略語・区分値を人に聞いて表に入れる）を
先に提案する——rank B（一括承認候補）に乗る解釈が増え、人の1件ずつ確認が減る。

**チャットで少量（数十件まで）**: list-targets の出力を読み、`data/interpretations.json` に書く:

```json
{ "V-0001": { "desc": "年間税率", "unit": "無次元(比率)", "rank_claim": "A",
              "evidence_cited": ["E-0001-01"], "notes": "" } }
```

**全件（数百件〜）は無人バッチ**（1チャンク=1 headless プロセス。トークン制約なし）:

```bash
python <LR>/scripts/pipeline.py dict --root . [--chunk 40] [--max-vars 500]
```

既定モデルは sonnet（`--model` で変更可）。チャンクごとに検証・マージ・辞書ページ再生成まで
自動で進むので、人は**実行中でも並行して承認**できる。

#### ルーブリック（rank は機械が決める。申告ではない）

| rank | 条件（`verify-interp` が判定） | 扱い |
|---|---|---|
| A | `comment` / `format_label` / `data_init` を引用している | 一括承認候補 |
| B | domain-knowledge.md の語、または links で結ばれた approved 済み変数を引用 | 一括承認候補 |
| C | `usage_expr` / `common_pos` のみ | 人が1件ずつ確認 |
| D | 引用なし | **マージ拒否**。desc は「不明」で人のキューに残る |

- `rank_claim` は書いてよいが最終判定は機械。食い違えば「LLM申告 A → 検証 C」と表示される
- **根拠から意味を決められないものは desc「不明」・`evidence_cited: []` で返すのが正しい**。
  推測で埋めない（rank D になって人へ回るのが設計どおりの動作）
- 引用できるのは**その変数の evidence に実在する ev_id だけ**（捏造は検証で全件差し戻し）
- 書いてよいファイルは `data/interpretations.json` **1つだけ**
  （variables.json も docs/ も functions.json も触らない）

### 4. verify-interp（機械検証してマージ）

```bash
python <LR>/scripts/variables.py verify-interp --root .   # または --ids V-0001..V-0040
```

検証項目: 対象 var_id 集合と完全一致（欠落・余剰があれば**全件差し戻し**）/ ev_id の実在 /
desc が空でない。通れば status を `interpreted` にしてマージし、
`data/interpretations-applied-<日時>.json` へ退避して interpretations.json を消す。
**NG が出たら直して再実行**（NG のままで「できました」と報告しない）。

### 5. page（辞書ページの生成）

```bash
python <LR>/scripts/variables.py page --root .
ledger wbs && python <LR>/scripts/render_site.py --root .
```

`docs/variables.qmd`（自動生成・手編集禁止）ができ、ナビバーに「変数辞書」が自動で出る。
未承認が残っていれば render_site.py が承認ウィジェットを埋め込む。

### 6. 人の承認（ブラウザ / チャット。どちらも同格）

**ブラウザ**（推奨。件数が多いとき）: 変数辞書ページを開くと、
**影響度（出現関数数）降順 × rank 昇順**で並んでいる——確認が要る C/D と、
多くの関数に効く変数が先頭に来る。

- rank A/B: チェックボックスで**一括承認**
- rank C/D: 1件ずつ desc/unit を修正入力して承認（修正と承認が同時に確定する）

**チャット**:

```bash
python <LR>/scripts/variables.py approve V-0001,V-0002 --by <名前> --root .
python <LR>/scripts/variables.py revise  V-0003 --desc "..." [--unit "..."] --by <名前> --root .
```

- 人に出すときは**影響度の大きいものから、根拠を添えて**提示する
  （「V-0001 RATE（12関数で使用）: 年間税率。根拠 legacy/tax.f:118 のコメント」）
- desc が空・「不明」のままの変数は `approve` できない（`revise` で確定させる）
- 承認1回でクラスタの全出現に効く。勝手に approved にしない

### 7. propagate（仕様書側へ伝搬）

ブラウザ承認では自動で走る。チャット承認のときは明示的に実行する:

```bash
python <LR>/scripts/variables.py propagate --root .   # functions.json の IO/globals の desc へ転記
ledger skeletons                                      # 骨子の IO 表と dict-hash を同期
ledger wbs && python <LR>/scripts/render_site.py --root .
```

- 転記の書式は `"<意味>(<単位>) [V-0001]"`。**①はこの `[V-xxxx]` を書き換えない**
- `ledger skeletons` は①未着手（status: skeleton）の骨子の `dict-hash` だけを現在値へ同期する
  （draft / reviewed には触れない）

### 8. 完了報告と次

`ledger next` を実行し、dict-gate で除外される関数が無くなったことを確認して
`/legacy-1-spec` へ誘導する。まだ未承認が残るなら、残件（rank 別）と
「承認を進めるか `--no-dict-gate` で先に進むか」を人に確認する。

## 追加開発・再抽出のあとの合流

レガシーに変更が入って再抽出した場合は、**`build` をもう一度実行するだけ**でよい:

- var_id は不変。`evidence_hash` が変わっていなければ承認と desc を維持する
- 出現が増えただけ → 承認維持＋ `occurrence_added`（辞書ページで任意に再確認）
- クラスタの分裂・併合 → status を `unreviewed` に戻し `cluster_changed`
  （意味の同一性の前提が崩れたときだけ人に戻す）
- 新規変数は unreviewed で入るので、手順3〜7を回す

reviewed 済みの仕様書は辞書の改訂で自動修正されない。矛盾候補は機械が出す:

```bash
python <LR>/scripts/variables.py conflicts --root .   # → docs/dict-conflicts.md
```

WBS に「⚠辞書stale」が出た関数は、①生成後に語義が改訂されたもの。
人に見せて、①を改訂するか辞書側を直すかを決めてもらう。

## 禁止

- legacy 原文を読んで desc を埋めること（引用できない根拠は根拠でない）
- 根拠が無い変数を推測で埋めること（desc「不明」・引用なしが正しい）
- `data/interpretations.json` 以外のファイルを書くこと（variables.json・docs/・functions.json）
- 人のOKなしで status を approved にすること
- 辞書ページ（docs/variables.qmd）を手編集すること（再生成で消える）
