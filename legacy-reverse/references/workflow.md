# 共通ワークフロー規則（全フェーズskillが従う）

## 機械操作の呼び出し方（MCP優先）

mcp-servers/legacy-reverse-mcp が登録済みの環境では、本書に出てくる
`ledger.py …`・`graph.py`・`variables.py`・`hazards.py`・pytest＋collect_results（⑤）・
check_stubs・profile_run・quarto/sphinx/pdf_book は
**同名相当の MCP ツールで呼ぶこと**（pipeline_status / run_tests / render_site /
graph_query / dict_build / dict_approve / hazard_match 等。
構造化された結果が返り、シェル引用の事故と許可プロンプトが減る）。
未登録の環境では従来どおりスクリプトを直接実行する。両者の実体は同一。

## 情報遮断（クリーンルーム）

| フェーズ | 読んでよい入力 | 読んではいけないもの |
|---|---|---|
| ⓪ 解析 | legacy/ 全部 | — |
| ⓪ 辞書解釈 | `.legacy-reverse/dict-targets.json`（機械が収集した根拠バンドル）、domain-knowledge.md | **legacy/ 全文**、functions.json、docs/specs/ |
| ① 仕様書 | legacy/ 該当関数、functions.json、domain-knowledge.md | tests/、src/ |
| ② テスト仕様 | ①(reviewed)、conventions.md、domain-knowledge.md | **legacy/**、src/、tests/ |
| ③ テストコード | ②(approved)、conventions.md | **legacy/**、**①**、src/ |
| ④ 実装 | ①(reviewed)、conventions.md | **legacy/**、**②**、**tests/** |
| ⑤ テスト | 結果＋①②（トリアージ判断用）、src/（(a)修正時） | legacy/、tests/ の編集 |
| ⑦ 分析 | src/・docs/・計測結果（全体を見る） | tests/ の編集（挙動保存が大原則） |

- 「読んではいけない」に触れたくなったら、それは仕様の穴。ISSUE を起票して停止する
- レガシー原文を読める役割は ⓪ と ①（改訂含む）だけ
- **辞書解釈（`pipeline.py dict` / `/legacy-0-dict`）は根拠バンドルだけを読む**。
  legacy 全文を読ませないのは、根拠として引用できない「読んだ気配」で desc を埋めさせない
  ため（引用は ev_id でしか行えず、実在しない ev_id は `verify-interp` が弾く）。
  根拠から意味を決められないものは desc「不明」＋引用なしで返すのが**正しい振る舞い**で、
  それらは rank D として人のキューに回る。書いてよいファイルは
  `data/interpretations.json` 1つだけ

## ハッシュ連鎖

- ②のフロントマター `spec-hash` に生成時点の①のハッシュ、ledger.json に③freeze時のハッシュを記録
- `python <LR>/scripts/ledger.py verify <func-id>` で連鎖を検証。不一致＝上流が改訂された＝下流は「要再生成」
- ハッシュは `ledger.py hash <path>`（sha256 先頭8桁）
- **dict-hash 連鎖**（変数辞書がある場合のみ）: ①仕様書のフロントマター `dict-hash` に、
  その関数の approved 変数の (var_id, desc) 集合のハッシュを `ledger skeletons` が刻む。
  承認後に語義が改訂されると `ledger verify` が NG、WBS の要対応に「⚠辞書stale」・
  関数一覧に「辞書⚠」が出る。**reviewed 済みの仕様書は自動修正しない**——
  `variables.py conflicts` が docs/dict-conflicts.md に矛盾候補を列挙するだけで、
  直すかどうかは人が決める（骨子のままの仕様書は次の `ledger skeletons` が現在値へ同期する）

## 機械レビュー（ハルシネーション・省略の検知ゲート）

LLM 成果物は人に見せる前に `review_checks.py`（MCP: review_spec / review_testspec /
review_all）で機械検証する。**NG が残る成果物を「できました」と報告してはいけない。**

| フェーズ | ゲート | 検知するもの |
|---|---|---|
| ① draft後 | `review_checks.py spec <fid>` | 実在しない `file:lines` の引用、🟢なのに根拠なし、プレースホルダ残存（省略）、必須節欠落、原本ハッシュ不一致、**hazard の検討漏れ・EP-ID の捏造・未決定のまま仕様化** |
| ② 承認依頼前 | `review_checks.py testspec <fid>` | 🟢仕様項目のケース漏れ、①に無いSPEC-IDの参照（捏造）、TC参照先の不在、根拠の規定外表記、spec-hash 鮮度、**挙動が変わる hazard（guard_raise/guard_value/legacy_preserve）の境界ケース漏れ** |
| ③ freeze前 | `pytest --collect-only` ＋ marker突合（collect_results が exit 3） | ケースIDとテスト関数の過不足 |
| ④ 完了前 | `check_stubs.py` | 空実装・NotImplementedError・TODO/FIXME（スタブ化の検知） |
| ⑤ | `ledger verify` ＋ collect_results | ②stale・テスト改変・blocked |
| ⑥ 前 | `review_checks.py all` | 全関数の①②の総点検 |

機械で検知できない「意味のすり替え」（式は書いてあるがレガシーと違う等）は、
人レビューと④→⑤の失敗ループが受け持つ。疑わしければ ISSUE。

## グラフ・変数辞書・フロー・例外ポリシー（⓪の拡張層）

設計の正は [graph-dict-design.md](graph-dict-design.md)。ここは運用規則だけ。
**どれも functions.json からの導出物**で、再抽出すれば自動追随する（グラフとフロー到達集合は
保存すらしない）。データの形は [schema.md](schema.md) が正。

### コールグラフ（graph.py・依存ゼロ・LLM不使用）

```bash
python <LR>/scripts/graph.py --root . summary            # ノード/エッジ数・到達率・dead件数（JSON）
python <LR>/scripts/graph.py --root . dead               # エントリから到達不能な関数（exclude 候補）
python <LR>/scripts/graph.py --root . reachable F-0000   # 到達集合（--flow でフロー指定も可）
python <LR>/scripts/graph.py --root . callers F-0087 --transitive   # 影響範囲（逆方向）
python <LR>/scripts/graph.py --root . between F-0000 F-0087         # 最短経路（BFS）
python <LR>/scripts/graph.py --root . cycles             # SCC（Tarjan）でサイズ2以上の循環
```

- `dead` は**列挙するだけで自動 exclude はしない**（除外は人の判断＝`ledger exclude`）。
  excluded の関数は「対象外(除外済み)」として別掲される
- ledger.py / variables.py / pipeline.py はライブラリとして import する（二重実装しない）

### 変数辞書（variables.py）— 手順は /legacy-0-dict skill が正

```bash
python <LR>/scripts/variables.py build --root .          # クラスタリング＋根拠収集（常にマージ）
python <LR>/scripts/variables.py list-targets --limit 30 --root .   # 未解釈の根拠バンドル（JSON）
python <LR>/scripts/variables.py verify-interp --root .  # interpretations.json を機械検証してマージ
python <LR>/scripts/variables.py page --root .           # docs/variables.qmd を生成
python <LR>/scripts/variables.py approve V-0001,V-0002 --by <名前> --root .
python <LR>/scripts/variables.py revise V-0003 --desc "..." [--unit "..."] --by <名前> --root .
python <LR>/scripts/variables.py propagate --root .      # approved を functions.json の IO/globals へ転記
python <LR>/scripts/variables.py conflicts --root .      # docs/dict-conflicts.md（reviewed との矛盾候補）
```

- **機械が正、AIは意味づけ**。クラスタリング・根拠収集・rank 判定・伝搬は決定的。
  LLM が書けるのは `data/interpretations.json` だけで、範囲逸脱は `verify-interp` が弾く
- **rank は LLM の申告でなく検証側が決める**（A/B/C/D）。D（引用なし）はマージせず
  人のキューに残る。A/B は一括承認候補、C/D は1件ずつ人が確定させる
- rank B は domain-knowledge.md の語との一致で決まる。⓪で人から聞ける略語・区分値は
  「語彙・略語集」へ**先行投入**してから解釈を回す（legacy-0-analyze 手順3.9）。
  同様に、②の期待値の前提になる全体既定（丸め・⑤の許容誤差・単位・日付・文字コード・
  既知バグの扱い）は conventions.md の「後戻り高コスト項目」として⓪で人と確定させる
  （覆ると②以降が作り直しになるため。関数単位の例外は DK に記録し、個別が既定に勝つ）
- `propagate` は functions.json の inputs/outputs/globals の desc を
  `"<意味>(<単位>) [V-0001]"` 形式に機械転記する。**①は IO 表の `[V-xxxx]` を書き換えない**
  （辞書が正。矛盾を見つけたら辞書側を revise する）
- 承認は人（チャットの `approve`/`revise` と、辞書ページのウィジェットは同格）

### dict-gate（既定 ON）

**変数の語義が未承認の関数には①を書かせない。**

- 判定の唯一の実装は `ledger.Project.dict_gate_blockers`。`ledger next` と
  ブラウザの実行ボタン（`browser_run._decide_kind`）が共有する
- 免除: ①data/variables.json が無いプロジェクト（従来どおり）
  ②spec が既に **draft / reviewed** の関数（仕様化済みを今更止めても意味がない）
- 解除: `ledger next --no-dict-gate`。除外された関数と未承認 var_id は next が理由つきで表示する
- ゲートに掛かったら、辞書の承認を進めるのが正しい対処（解除は例外運用）

### フロー（作業スコープ）

```bash
ledger flow add 月次バッチ --entry F-0000[,F-0006] [--desc "..."]
ledger flow list        # flow_id・名前・entries・到達関数数
ledger flow rm 月次バッチ
```

- main が複数あるとき・main 内の大分岐を別扱いしたいときに、人が定義する
  （分岐先の代表サブルーチンをエントリに指定する）
- `ledger next --flow <名前|FL-01>` / `pipeline.py spec|run|dict --flow <名前|FL-01>` で
  作業対象をその到達集合に限定できる。WBS には「フロー別進捗」表が出る
  （flows 未定義なら表ごと出ない＝従来出力と一致）
- 関数は複数フローに属し得るので**成果物は従来どおり関数単位**。骨子の新規生成時のみ
  フロントマターに `flows:` を記載する（文脈付与のみ）

### 例外ポリシー（hazards.py）— 0割はその一例

**検知（機械）→ 登録簿と突合（機械）→ 未決定は人に質問 → 決定を登録 → 再突合**。
Fortran は 0割でも Inf を作って走り続けるが Python は停止するため、
**決めずに①→④へ進むことはできない**（①の機械レビューが NG にする）。

```bash
python <LR>/scripts/hazards.py status --root .           # 総数・kind別・決定済み/未決定
python <LR>/scripts/hazards.py match --root .            # 突合 → data/hazard-map.json ＋ 質問キュー
python <LR>/scripts/hazards.py add-policy --kind div_by_var --decision guard_raise \
       --by <承認者> [--func F-0012 | --hazard H-0012-01] [--note "..."] --root .
```

- ⓪の抽出が `functions.json` の `hazards` に検知結果を記録する（常に上書き）
- 未決定は `docs/exception-queue.md` に kind ごとに集約して「仮説＋選択肢」で出る。
  人の回答を `add-policy` で `docs/exception-policy.md`（EP-xxx 登録簿）に登録すると、
  同種の全箇所に一括で効く（自動で再突合まで走る）
- 決定の語彙: `detect_only` / `guard_raise` / `guard_value`（値を備考に明記）/
  `legacy_preserve` / `caller_guarantees`（根拠を備考に必須）。
  適用範囲は 全体既定 → 関数 → 個別 hazard の順に個別が勝つ
- 流れ: **hazard 検知 → EP 決定 → ①の「例外・数値特異点」節（hazard × 適用EP × 仕様記述）
  → ②の境界ケース**。①②の機械レビューが各段の抜けを突合する

### 推奨作業順（⓪〜①）

1. `extract_fortran.py --write`（⓪の機械抽出）
2. `graph.py summary` / `graph.py dead` で全体像と孤立関数を確認 → 対象外は `ledger exclude`
3. （任意）`ledger flow add` でフローを定義（main が複数・大分岐があるときだけ）
4. `hazards.py match` → `docs/exception-queue.md` を人に見せて決定 → `hazards.py add-policy`
5. `variables.py build` → **略語・区分値の先行投入**（頻出トークンを人に見せて
   domain-knowledge.md の語彙表へ。rank B の母集団になる）
   → **辞書フェーズ（`/legacy-0-dict`）** → 人の承認 → `propagate`
6. `ledger skeletons` → `ledger wbs` → ①（dict-gate が効いた状態で `ledger next`）

### 既に①が走っているプロジェクトへの後付け

- `variables.py build` は**いつ実行しても安全**（functions.json は読むだけ。
  変更するのは data/variables.json のみ）
- dict-gate は draft / reviewed の関数を免除するので、**進行中の①は止まらない**。
  ゲートが効くのは骨子のまま残っている関数だけ
- reviewed 済みの仕様書は辞書の承認では書き換わらない。`variables.py conflicts` が
  docs/dict-conflicts.md に矛盾候補を出すので、人が読んで必要なものだけ①改訂する
- `hazards.py match` も同様に読み取りのみ。ただし**未決定 hazard がある関数の①は
  以後の機械レビューで NG になる**ので、EP を決めてから①を再実行する

## 無人バッチ実行（pipeline.py ドライバ）

①の全件実行（2000件規模）はエージェントの会話内ループでは行わない
（コンテキスト上限・コンパクション劣化のため）。`scripts/pipeline.py` を使う:

```bash
python <LR>/scripts/pipeline.py spec --root . [--max-funcs 200] [--budget-usd 20]  # ①のみ
python <LR>/scripts/pipeline.py run  --root .                    # ①〜⑤を工程横断
python <LR>/scripts/pipeline.py run  --root . --only testspec    # ②だけ全件（工程単位）
python <LR>/scripts/pipeline.py priority F-0012                  # ⭐優先（実行中でも効く）
```

- **1関数 = 1つの新しい headless Claude プロセス**（`claude -p "/legacy-1-spec F-xxxx"`）。
  コンテキストが積み上がらず、1関数あたりのトークンは常に一定
- 各関数の完了は LLM の申告でなく**ファイル状態で契約検証**
  （status: draft ＋ 機械レビューNGゼロ）。NG はリトライ→スキップ記録、連続失敗で停止
- **タイムアウト・レートリミット耐性**: 1関数ごとに秒数上限で打ち切り（ハング対策）。
  レートリミット/利用枠上限は失敗に数えず指数バックオフで待機→同じ関数から自動再開
  （利用枠の時間リセットを人手なしで跨げる。待機累計の上限超過で安全停止）
- チャンクごとに WBS・一斉レビュー表を自動更新。人は**溜まった draft を随時レビュー**してよい
- **ライブ進捗**: 実行中は `.legacy-reverse/pipeline-status.json` を常時更新しており、
  serve_site.py を立てていれば `http://127.0.0.1:<port>/pipeline.html` で
  「いま何を実行中か・成功率・失敗の内訳・ETA・エージェント応答」がリアルタイムに見える
  （Quarto を通さないポーリング表示。WBS の再生成は不要）
- 中断（Ctrl-C・電源断）はどこでも安全。同じコマンドで続きから再開
- 実行ログ: `.legacy-reverse/pipeline-log.jsonl`（関数別の結果・コスト・所要）
- 前提: 対象プロジェクトに skill 配置済み、headless 用に必要ツールを
  `.claude/settings.json` で allow（または `--skip-permissions` を明示）
- **`--flow <名前|FL-01>`**（spec / run / dict 共通）で対象をそのフロー到達集合に限定できる
  （`ledger flow add` で定義したもの。「このフローだけ今日やる」）
- **モデル階層**: kind ごとに既定モデルを持てる（`browser_run.KINDS` の `model` キー）。
  現状 `dict`（辞書解釈）だけが **sonnet** 既定で、①〜⑤は指定なし＝従来の既定モデル。
  CLI の `--model <id>` は全 kind を一括上書きする

### 辞書解釈バッチ（`pipeline.py dict`）

①〜⑤と違い**対象は関数でなく変数のチャンク**（既定40件＝headless 1プロセス）。

```bash
python <LR>/scripts/pipeline.py dict --root . [--chunk 40] [--max-vars 500] [--model sonnet]
```

1. `variables.py list-targets` 相当で未解釈（status: unreviewed）の根拠バンドルを取り出し、
   `.legacy-reverse/dict-targets.json` に書いて claude を1回起動する
2. 契約検証は **`variables.py verify-interp --ids <チャンク>` の exit code**
   （LLM の自己申告は使わない）。成功すればその呼び出しが variables.json へのマージまで済ませる
3. チャンクごとに辞書ページを再生成しサイトを更新する——人は**実行中でも並行して承認できる**
4. 前提: `variables.py build` 済み（variables.json が無ければ即エラー）

ロック（run.lock）・レート耐性・中断安全・`/pipeline.html` のライブ進捗は①〜⑤と共有する。

### ブラウザからの単発実行（browser_run.py。試作・①〜⑤対応）

「1関数だけ様子を見ながら進めたい」向けに、pipeline.py の実行ロジック
（`run_one` / `RunStatus` / 起動プリフライト・agent-logs 保存）をそのまま流用した
単発トリガーがある。render_site.py が①仕様書ページに「N を実行する」ボタンを
埋め込み、押すと serve_site.py の `POST /run-phase` が `browser_run.start()` を呼ぶ:

- ボタンは常に①仕様書ページに出る（関数の「ホーム」として工程を通じて存在し
  続けるページのため）。`browser_run._decide_kind` が①〜④のうち次に着手すべき
  ものを判定する: skeleton→①、reviewed かつ test-spec 無し→②、
  test-spec が approved かつ `Project.status_of` の test_code_ok が False→③、
  test_code_ok かつ impl_ok が False→④。draft/generated 中（承認待ち）は
  承認ウィジェット側の担当なので None（ボタンを出さない）
- `pipeline.py` の `verify_spec`/`verify_testspec`/`verify_testcode`/`verify_impl`
  は全て `(ok, why, problems)` の3-tupleで統一されている。`verify_testcode` は
  `Project.status_of` の test_code_ok/test_code_tampered を見る（③自体には①②の
  ような静的レビューは無く、freeze前の marker突合が機械チェックの役割）。
  `verify_impl` は `check_stubs.check_file` をそのまま呼んで problems を作る。
  `problems` は `RunStatus.result()` を経由して pipeline-status.json の
  `recent[].problems` にそのまま乗り、`/pipeline.html` が承認ウィジェットと
  同じ見た目（赤箱＋箇条書き）で描画する
- **③④は hook ガード（phase-start/phase-end）を気にしなくてよい**。
  `ledger phase-start 4 <fid>` は legacy-4-impl/SKILL.md の手順としてAI自身が
  呼ぶので、headless 実行（`claude -p`）でもチャット実行と同じに効く。
  オーケストレータ（pipeline.py/browser_run.py）側は何も特別なことをしていない
- ④が `ok=True` で完了したら `docs-sphinx` の有無を見て、あれば
  `ledger sphinx-index` → `python -m sphinx -b html docs-sphinx docs/_site/api`
  を実行し「新コード詳細(API)」を作り直す（`browser_run._build_sphinx_if_needed`。
  MCP の `render_site(with_sphinx=True)` と同じ2段構え）
- 実行はバックグラウンドスレッドで行い、POSTは即座に返る（数分かかる処理をHTTPで
  待たせない）。進捗はページ側のポーリングと `/pipeline.html` の両方で見える。
  **状態を finished にするのは WBS・サイトの更新（と④ならSphinx）が終わった後**
  ——先に finished にすると、ポーリング側が「終わった」と判断してまだ古いままの
  ページを reload してしまう（承認ウィジェットへの切替が反映されない）ため
- **排他制御は実行スロットロックで双方向**。バッチ（pipeline.py）とブラウザ単発は
  同じ `.legacy-reverse/run.lock`（`O_CREAT|O_EXCL` の原子的なファイル作成。
  `pipeline.acquire_run_lock`）を取り合う——バッチがブラウザ実行を、ブラウザが
  バッチを、どちらの方向でも締め出す。pipeline-status.json の running チェック
  （`pipeline.current_run_state`）は分かりやすいエラーメッセージ用の補助で、
  本命はロック。ロックには保持プロセスの **PID** が入っており、取得失敗時に
  死活を確認してクラッシュの残骸（Ctrl+C・強制終了で finally が走らなかった
  ケース）は自動解除する。status ファイル側も同様に `pid` を持ち、書いた
  プロセスが死んでいる running は無視される——**残骸がボタンやバッチを永久に
  塞ぐことはない**。ロックは実行完了までスレッド側で保持し、検証NG等での
  早期returnはその場で解放する
- **中止できる**: 実行中はボタン横に「中止」が出る（`POST /run-cancel` →
  `browser_run.cancel()` が cancel_event を set → `run_claude` がプロセスツリー
  ごと kill。Windows は claude.cmd の子に node が居るため taskkill /T を使う）。
  レート待機中も event.wait で即座に打ち切れる。serve_site.py の終了時
  （Ctrl+C）も `browser_run.shutdown()` が同じ経路で中止して片付けを待つので、
  claude の孤児プロセスやロック残留を残さない
- 単発の `rate_wait_total` は **900秒**（バッチの6時間と違い、人がボタンを押して
  待っている対話操作なので、15分で諦めて「今は混んでいる」ことを返す）。
  それ以外の既定値は `pipeline.RUN_ARG_DEFAULTS` をバッチと共有する
- claude の起動プリフライトは `start()`（POST の同期部分）で1回だけ行い、
  解決済みコマンドをスレッドに渡す。起動不能はボタン押下の直後に分かり、
  スレッド側の「state=running だが current 未設定」の空白時間も短くなる
- ウィジェットの JS（実行・⑥・承認の3種）は `browser_run.WIDGETS_JS` に集約し、
  render_site.py が `_site/lr-widgets.js` として書き出す。各ページは
  `<script src="/lr-widgets.js">` で参照する（ページごとの複製をやめ、修正1箇所）
- FROZEN・ローカルホスト限定に加え **Host/Origin ヘッダ検証**（DNSリバインディング・
  `text/plain` フォームのCSRF対策）を全 POST に掛ける（serve_site.py の
  `WRITE_ROUTES` と `_cross_site_reason` にまとめてある）
- **修正依頼・機械NGの再実行**: draft/generated（承認待ち）は通常ボタンを出さないが、
  人の修正依頼(pending)か機械レビューNGが残っている間だけ、承認ウィジェット内に
  「再実行」ボタンが出る（`review_actions.widget_html` が埋め込み、サーバ側は
  `_decide_kind(include_rerun=True)` で検証。`_rerun_wanted` が
  `pending_feedback_kinds` と `check_spec/check_testspec` で判定）。
  `request_changes` は送信後に `refresh_site` を呼んでボタンを即座に出す
- **⑤裁定（ISSUE回答→unblock）もブラウザで完結**: blocked の関数の①ページに
  裁定ウィジェット（`review_actions.adjudicate_widget_html`）が出て、ISSUE の
  「質問（人への問い）」をその場に表示する。回答を書いて送ると
  `review_actions.adjudicate` が ISSUE に回答を記入（status: answered）→
  `ledger unblock` → サイト更新まで行う。リロード後は「⑤を実行する」ボタンに
  切り替わり、AI が回答を反映して再テストする
- **連続実行（ブラウザからのバッチ）**: `/pipeline.html` の「連続実行」→
  `POST /run-batch` → `browser_run.batch_start`。全関数を走査して
  `_decide_kind(include_rerun=True)` が返す「次に着手できる工程」を1件ずつ実行し、
  実行後に再走査する（③が終われば同じ関数の④、承認が下りれば次工程、と自動で進む。
  承認・裁定待ちと完了はスキップ）。失敗した (関数, 工程) は同一バッチ内で再試行せず、
  連続3件失敗で安全停止。5件ごとに `refresh_site` するので、人は実行中も
  ブラウザで承認・裁定を並行できる。上限件数指定可（予算上限は CLI の
  `--budget-usd` のみ。ブラウザUIからは外した）。停止は単発と同じ `/run-cancel`。
  「この1件だけスキップ」は `/run-skip`（item_cancel イベント。バッチは続行）。
  ブラウザ実行は**既定で --dangerously-skip-permissions を付ける**（browser_run.SKIP_PERMISSIONS=True。
  ボタン押下は人の明示操作・サーバは 127.0.0.1 限定 + Host/Origin 検証のため）。
  permissions.allow で管理したい環境は serve_site.py --no-skip-permissions で外せる。
  レート待機は無人前提なのでバッチ既定の6時間。
  残タスクは `GET /batch-queue`（実行順・検索・件数集計。`_scan_targets` と同じ判定）、
  ⭐優先は `POST /batch-priority` → `.legacy-reverse/batch-priority.json`
  （order=割り込み順・retry=失敗スキップの解除。次の走査で反映）
- CLI の pipeline.py は `spec`（①専用・従来バッチ）/ `run`（①〜⑤工程横断。
  browser_run の `_scan_targets`/`KINDS` を共有し⭐優先も反映。`--only testspec` の
  ように工程を限定すれば「②だけ全件」等の工程単位バッチにもなる）/ `dict` /
  `priority`（⭐優先の ON/OFF・一覧。ブラウザの⭐と同じ `browser_run.prioritize` を
  呼ぶだけでロックを取らないため、**バッチ実行中に端末から割り込み順を変えられる**）。
  連続実行はロック（run.lock）・RunStatus・/pipeline.html 表示をすべて共有する。
  ブラウザ画面でできる操作は原則 CLI にも同じ入口を用意する（画面専用機能を作らない）
- 現状は①〜⑥（⑥は下記別枠）。⑦は探索的な改善ループで形が大きく異なるため別途設計が要る

### ⑤（テスト実行）の verify_fn が①〜④と違う点

`verify_test` は `retries=0` で呼ばれる（KINDS の `"test"` エントリで指定。
`run_one` は `cfg.get("retries", RUN_DEFAULTS.retries)` で kind ごとの上書きに対応）。
理由: legacy-5-test/SKILL.md の設計上、(a)実装バグは1回の headless 実行の中で
AI 自身が「src/ 修正→⑤再実行」を attempt 上限までループし、pass か blocked
（attempt上限到達で自動 ISSUE 起票）のどちらかで自然に止まる。**blocked は
orchestrator から見て「異常な失敗」ではなく「設計どおりの正常な停止」**——
ここで orchestrator 側がさらにリトライしても `ledger verify` が blocked を検知して
即座に断るだけの空実行になる（SKILL.md で「attempt を稼ぐための空実行」は
明示的に禁止）。`_decide_kind` も `blocked_by` が立っている間は "test" を返さず
ボタンを出さない（人が ISSUE に回答して `unblock` するまで再実行できない）。
`classify_ng` は「裁定待ち」を専用の分類（⛔人の裁定待ち・正常）に振り分け、
機械的な異常（claude起動不可・タイムアウト等）と区別して集計する。

### ⑥（完了検証）は browser_run.py の中でも別枠

⑥は headless Claude を呼ばない純粋な機械チェック（`ledger check`）で、数秒〜
数十秒で終わる。①〜⑤のような「バックグラウンドスレッド起動＋ポーリング」は
不要——`browser_run.run_check()` が POST をブロックしたまま同期的に実行して
結果を返す。ボタンは docs/index.qmd（WBSトップ）に出るが、**全関数の⑤が pass する
まで表示しない**（`browser_run.check_widget_html`。時期が来る前のボタンは
「①も終わっていないのに⑥が押せる」という誤解のもとになるため。
⑦のウィジェットも同様に⑥が pass するまで表示しない。途中の不足確認は
WBS の進捗表・spec-review.md、CLI なら `ledger check` が担う）。
排他は①〜⑤・CLI バッチと同じ `.legacy-reverse/run.lock` を共有する
（バッチも同じロックを取るようになったため、check 実行中にバッチが割り込む
隙間は無い。逆に⑥の数十秒はバッチ開始がロック取得失敗で断られるが、
`ledger check` は短時間で終わるので再実行すればよい）。

### ⑦（分析）は「機械計測 → LLM提案」の2段構え。適用はブラウザ化しない

「計測せずに提案しない」が⑦の大原則なので、ブラウザの「⑦分析を実行する」
（WBSトップ・⑥がpassのときだけ有効）は2段で動く（`browser_run.analyze_start`）:

1. **定量評価（決定的・LLM不要）**: `quant_analyze.py` が cProfile
   （profile_run.py に委譲。bench.py があれば本命、無ければスモーク）＋
   radon cc/mi・ruff・bandit・pip-audit を機械実行し、`.legacy-reverse/quant.json` と
   `docs/quant.md` に集約する。未導入ツールはエラーにせず「未実施」として記録
2. **提案（headless Claude 1回）**: 実測データを読ませて docs/analysis.md に
   施策候補（OPT-/REF-/SEC-、期待効果・リスク・優先順位、実測値の引用）を記入させる。
   **施策の適用・コード変更・施策票の起票は明示的に禁止**したプロンプトで起動する

検証は `pipeline.verify_analysis`（analysis.md の存在・プレースホルダ残存・
候補ゼロなら「候補なし」明記・quant.md の存在）。ロック・中止・進捗表示は
①〜⑤と同じ枠を共有する（fid は "analyze" 固定）。

改善の適用側（施策票 approved → 1施策1コミット → 再計測 → 達成判定 → 未達revert）は
git 操作とロールバック判断が絡み「1回実行して検証」の枠に収まらないため、
ブラウザ化しない（分析までがブラウザ、適用はチャット駆動のまま）。

`/pipeline.html` は2.5秒ごとにポーリングするが、「直近の結果」テーブルは
**中身が変わった時だけ**再描画する（変化がなければ innerHTML に一切触れない）。
以前は毎回丸ごと再描画しており、人が開いた `<details>`（NG理由の展開）が
次のポーリングで即座に閉じる不具合があった。再描画が発生する場合も
`data-rid` で開いていた行を判別し、開閉状態を復元する。

## 再開（レジューム）

進捗の正はすべてファイル（functions.json / ledger.json / 各フロントマター）にあり、
会話コンテキストには無い。**どのタイミングで中断しても、次の3コマンドから再開する**:

```bash
python <LR>/scripts/ledger.py status --summary   # 全体状況（2000関数でも数行）
python <LR>/scripts/ledger.py next --all --limit 20   # 着手可能な関数の一覧
python <LR>/scripts/review_checks.py all --root .     # 成果物の健全性
```

やり直し・再列挙は禁止。⓪の再実行も extract_fortran.py がマージ動作
（func_id 不変・手修正保持）なので安全。全関数を列挙した長大な出力をコンテキストに
読み込まないこと（summary と next --all で足りる）。

## 関数リストの人による調整（⓪以降いつでも）

人の「この関数も追加して」「この関数は移植しない」は ledger の専用コマンドで反映する。
**functions.json のエントリの物理削除・手書き追記はしない**（⓪の再実行で別IDとして
復活・重複し、成果物との紐付けが切れる）:

- 追加: `ledger add NAME [--file legacy/x.f --lines 10-50 --calls F-0001,...]`
  → manual フラグ付きで採番。inputs/outputs/desc/signature を充填してから
  `ledger skeletons` → `ledger wbs`。以後は通常の①〜⑤対象
- 対象外: `ledger exclude F-xxxx --reason "..."` → ①〜⑥・WBS・next から外れ、
  WBS の「対象外の関数」に理由つきで残る。既存成果物は消さない。
  呼び出し元が対象内に残る場合は警告が出る（必要なら ISSUE で裁定）
- 復帰: `ledger include F-xxxx`

## ISSUE 運用

- 採番は全体通し。`docs/issues/` の最大番号+1（`ledger.py next-issue` が返す）
- 必ず「仮説＋Yes/Noで答えられる問い」の形式（templates/issue.md）
- 人の回答が付いたら status: answered → 成果物へ反映して applied。ドメイン知識なら domain-knowledge.md へ転記
- kind: spec-gap(④が詰まった) / domain / legacy-bug / triage(⑤ループ上限) / other
- **回答の受け取り方は2系統**: チャットでの回答（AskUserQuestion 含む）と、人がISSUEファイルの
  「回答（人が記入）」欄へ直接記入する方法。どちらも同等に扱う

## 人の直接入力（起動時スキャン）

**すべてのフェーズskillは、本処理の前に次を確認する:**

1. open の ISSUE で「回答（人が記入）」欄が埋まっているものがないか → あれば先に
   反映処理（answered → 成果物更新 → applied → 必要なら domain-knowledge.md 転記）を行う
2. 人からチャットでドメイン知識・規約変更を告げられたら、その場で
   domain-knowledge.md（出典:「直接指示 YYYY-MM-DD」）/ conventions.md に反映する
3. `docs/review-feedback.md` に「状態: pending」の項目がないか → あれば先に反映し
   「状態: applied」に書き換える（ブラウザの承認ウィジェットの「修正依頼」が書く。
   人がチャットで「F-xxxx は修正: 〜」と言うのと同じ扱い。詳細は次節）

手編集の可否:

| ファイル | 手編集 | 備考 |
|---|:---:|---|
| conventions.md / domain-knowledge.md / ISSUEの回答欄 | ⭕ | 人が著者。編集後は render_site.py（またはskillに依頼） |
| exception-policy.md | ⭕ | 人が承認する登録簿。`hazards.py add-policy` 経由が基本（列の並びは変えない） |
| specs/ | △ | 編集可。ハッシュ連鎖が②を stale に落とし再確認が走る（設計どおり） |
| index.qmd / test-results/ / completion-check.md | ❌ | 自動生成。再生成で消える |
| variables.qmd / exception-queue.md / dict-conflicts.md | ❌ | 自動生成。語義の修正は `variables.py revise` か辞書ページのウィジェットで |
| data/variables.json / hazard-map.json | ❌ | スクリプト専用。手編集しない |

- conventions.md を途中で変更した場合は影響が③④の既存成果物に及ぶ。skillは変更を検知したら
  「どの関数の成果物と不整合になり得るか」を洗い出して人に報告する

## 人の承認ゲート

対象: **変数辞書の語義確定**、**例外ポリシーの決定**、①の reviewed 化、②の approved 化、
⑤トリアージの (b)(c)、ループ上限後の再開。

承認の媒体は2通りあり、どちらも同格（承認が人である、という原則は変わらない）:

**チャット経由**
1. skill が承認用サマリ（変更点・チェックリスト）をチャットに提示する
2. 人がチャットで OK / 修正指示を返す
3. OK なら skill がフロントマター（status, reviewed-by/approved-by, reviewed-date/approved-date）を更新する

**ブラウザ経由**（①②のみ。render_site.py が draft/generated 状態の仕様書・
テスト仕様書ページに埋め込む承認ウィジェット。詳細は次項）
1. 人が WBS サイトで該当ページを開く（機械レビュー結果が全文その場に出ている）
2. 「承認する」または「修正依頼…」を押す
3. serve_site.py の `/review-action` がフロントマターを更新し、WBS・一斉レビュー表・
   サイトを差分再生成する（数秒で反映。ページを再読み込みすれば見える）

勝手に approved にしない。承認待ちで turn を終えるのは正しい動作。

- **①は一斉レビュー可**: バッチモード（legacy-1-spec）で複数関数を draft まで連続処理し、
  `review_checks.py report` が生成する docs/spec-review.md（一斉レビュー表）で人が
  まとめて OK / 個別修正指示を返せる。承認が人であることは変わらない（粒度の違いだけ）。
  一斉レビュー表の「機械レビュー」列は仕様書ページの承認ウィジェットへ直接ジャンプする
  リンクになっており、❌の場合はその場で理由の全文が読める（件数だけで終わらない）

### 変数辞書の承認（辞書ページ / チャット）

`variables.py page` が生成する `docs/variables.qmd` に、render_site.py が承認ウィジェットを
埋め込む（未承認が1件も無ければ埋め込まない）。ナビバーの「変数辞書」は
docs/variables.qmd が存在するときだけ自動で追加される。

- 並び順は**影響度（出現関数数）降順 × rank 昇順**——人の確認が要る C/D と、
  多くの関数に効く変数が上に来る。`rank A/B` はチェックボックスで**一括承認**、
  `C/D` は1件ずつ desc/unit を修正入力して承認する（修正と承認が同時に確定する）
- 送信先は `POST /dict-action`（serve_site.py）。既存の `/review-action` と同じ防御
  （127.0.0.1 限定・Host/Origin 検証・配布EXEでは無効化）。承認可否（rank D・desc 未確定の
  拒否）は**クライアントの表示を信用せずサーバ側で再判定**する
- 承認1回でクラスタの全出現に効く。承認後は
  **propagate → skeletons → 辞書ページ再生成 → サイト差分レンダ**まで自動で走る
- チャット承認も同格: `variables.py approve V-0001,V-0002 --by <名前>` /
  `variables.py revise V-0003 --desc "..." --by <名前>`（同じライブラリ関数を通る）

### 例外ポリシーの決定（exception-queue → add-policy）

1. `hazards.py match` が未決定を `docs/exception-queue.md` に kind ごとに集約して出す
   （仮説＋選択肢＋該当箇所の表＋登録コマンド例）
2. 人にその kind の**既定**をどうするか聞く（`guard_raise` / `detect_only` / … /
   「式ごとに個別判断」）。AIが勝手に決めない
3. 回答を `hazards.py add-policy --kind <k> --decision <語彙> --by <名前>` で登録する
   （個別に変えたい箇所だけ `--func` / `--hazard` を付けて追加登録。個別が全体既定に勝つ）
4. 登録すると自動で再突合され、キューが更新される。全件決定するまで①は書けない

### ブラウザからの承認・修正依頼（レビューウィジェット）

`docs/specs/<fid>.md` が `status: draft`、`docs/test-specs/<fid>.md` が `status: generated`
の間、render_site.py はそのページの本文冒頭（フロントマター直後）に承認ウィジェットを
埋め込む。仕様書を読んでいるその場で完結させる設計で、別ページには分離していない
（一覧ページと詳細ページを往復させない）。

- 機械レビュー結果は render 時点のものをその場に埋め込み表示する（NGなら理由の全文。
  ✅なら「承認する」ボタンが有効）。**NGの間は承認ボタンをグレーアウトして押せなくする**
  （UI 側の抑止）。加えて `/review-action` は承認要求のたびにサーバ側で機械レビューを
  再実行し、NGなら拒否する（disabled 属性を無視した直接POSTにも効く二重の防御）
- 「修正依頼…」はコメント欄に理由を書いて送ると `docs/review-feedback.md` に
  「状態: pending」で追記される。status は変えない（次回の①/②AI実行時に
  「人の直接入力（起動時スキャン）」で拾われ、反映後「状態: applied」になる）
- 承認・修正依頼は **127.0.0.1 からのみ**受け付ける（`--host 0.0.0.0` で LAN 公開していても
  リモートから成果物を書き換えさせない）。配布用 EXE（build_viewer.py の成果物）は
  docs/_site のスナップショットを同梱しているだけで元プロジェクトへの書き込み経路が
  無いため、レビュー操作自体を無効化している
- WBS の関数一覧・一斉レビュー表からのリンクは、承認待ちの間だけこのウィジェットの
  アンカー（`#review-<fid>`）へ直接ジャンプする
- **draft は再実行（書き直し）自由**。reviewed の書き直しは②が stale になるため人の了承を先に取る

## ④⑤ループと再開

- attempt は「最後の裁定以降の⑤実行回数」。上限3
- 3回目 fail → triage ISSUE 自動起票、ledger に blocked_by 記録、WBS ⛔。④⑤skillは blocked 中の実行を拒否
- 人が ISSUE を裁定 → 反映(applied) → `ledger.py unblock <func-id>` → 人が⑤（または②③の再生成）を再トリガ。attempt は1から

## スタブ禁止（④）

- `NotImplementedError`・本体が pass/... だけの関数・TODO/FIXME は `check_stubs.py` が検出し、④は未完了扱い
- 実装しきれない場合は spec-gap ISSUE を起票して停止 → ①改訂（レガシー確認・根拠付き）→ ハッシュ伝搬 → ④再開

## 出力・レンダリング（フェーズごとに必ずHTML更新）

- 各フェーズの最後に必ず実行する（人がブラウザで途中経過を常に確認できる状態を保つ）:
  1. `ledger.py wbs` で WBS を再生成
  2. `python <LR>/scripts/render_site.py --root .` で HTML サイトを更新
     （`docs/_quarto.yml` はテンプレから⓪で配置済み）
- **`quarto render docs` を直接叩かない。** Quarto は Mermaid を `.qmd` でしか描けないため、
  render_site.py が `docs/_sitework/` に `.qmd` の影コピーを作ってから render する
  （出力先は従来どおり `docs/_site/`）
- **レンダリングは差分が既定**（2000関数級の全体レンダは1時間級になるため）。
  変わったページだけ再レンダするので、フェーズ末の更新は数十秒で済む。
  _quarto.yml / wbs.css の変更・変更ページ多数のときは自動で全体レンダに切り替わる。
  差分ではサイト内検索の索引が更新されないため、まとまった節目に `--full` を1回かける
- **⓪の時点でもリンク切れは出ない**: ナビバーが参照する未生成ページ
  （domain-knowledge / conventions / completion-check / analysis）は `ledger wbs` が
  「いつ生成されるか」を書いたスタブを docs/ に置く（⑥⑦や人の記入で自然に上書き）。
  render_site.py も影コピー側で同じ救済をする（旧プロジェクト・api 用）。
  WBS 側も、仕様書ファイルが存在しない関数はリンクにしない（ledger.py `_spec_ref`）
- HTML サイト生成に必要なのは **`quarto` バイナリだけ**（quarto-typst-pdf skill は不要）。
  未導入なら quarto.org のインストーラ、または管理者権限を避けたい場合は
  quarto-typst-pdf skill の `qtpdf.py install` でポータブル導入
  （`~/.local/quarto/bin/quarto`。PATH 登録不要。render_site.py はここも探す）
- **合本PDF（pdf_book.py）だけは quarto-typst-pdf が必要**。`legacy-reverse` の
  **隣**のディレクトリから `quarto-typst-pdf/scripts/qtpdf.py` を探すので、
  対象プロジェクトへ配置する場合は `.claude/skills/` に並べて置く（`--qtpdf` で明示も可）
- 閲覧は `python <LR>/scripts/serve_site.py --root .`
  - 127.0.0.1 のみに bind（仕様書は社内資料。LAN に出すのは `--host 0.0.0.0` を明示したときだけ）
  - ポートはプロジェクト名から決まる固定値（8100-8899）。複数プロジェクトを同時に立てても
    ぶつからず、埋まっていれば空きへずらす。URL はブックマークできる
  - キャッシュ無効なので、再レンダリング後はブラウザの更新だけで最新になる
  - `--render` で配信前に作り直す。`--watch` は docs/ の変更を検知して自動再レンダリング
  - 素の `python -m http.server` を使う場合は **cwd を _site の外にして** `--directory` で指定する
    （_site の中を cwd にすると再レンダリング時の削除がロックされて失敗する。
    serve_site.py は cwd を動かさないのでこの問題は起きない）
- 成果物フロントマターに独自キーを足すときは Quarto 予約キーと衝突させない（coverage → tc-coverage の前例）

### WBS の大規模対応（200関数超で自動分割）

`ledger.py wbs` は 200 関数を超えると自動でページを分割する:

- **index.qmd はダッシュボード**: 進捗サマリ・要対応（⛔blocked / ⚠stale / ⚠改変 /
  ❌fail）・Open ISSUE・次の一手（上位10）・レガシーファイル別の進捗表
- **全関数の明細は docs/wbs/<ファイル別>.qmd**（自動生成・手編集禁止）。
  `_quarto.yml` の render に `wbs/*.qmd` が必要（テンプレは対応済み。旧プロジェクトは
  render_site.py が影コピー側で自動追記する）
- 200 以下では従来どおり1ページに全関数表

### 配布（単体実行ファイル / EXE）

レビュアーや管理側に進捗を見せるのに、共有サーバを立てたり社内公開したりしなくてよい。
サイトを同梱した実行ファイルを渡し、**各自が自分のローカルホストで開く**。

```bash
pip install pyinstaller                                  # 初回のみ
python <LR>/scripts/build_viewer.py --root .             # → <root>/dist/<プロジェクト>-wbs[.exe]
```

- 中身は「render_site.py で作った `docs/_site` ＋ serve_site.py」を PyInstaller で1ファイル化したもの。
  起動すると同梱サイトを一時展開して 127.0.0.1 で配信し、既定ブラウザを開く。
  **渡す相手に Python も Quarto も要らない**（4MB のサイト込みで 9MB 程度）
- 外部フォント（Google Fonts）への参照はビルド時に除去するので、
  閉じたネットワークの PC でも待たされず、開いただけで外に通信も飛ばない
- **クロスコンパイル不可**。Windows 用 `.exe` は Windows 上でビルドする（PyInstaller の仕様）
- 中身はビルド時点のスナップショット。進捗が動いたら作り直す（`--no-render` で再レンダリング省略）
- 署名なし1ファイル EXE が SmartScreen 等に止められる環境では `--onedir`（フォルダ配布）にする。
  サイトを別配布したい場合は `--no-embed`（実行ファイルの隣の `_site` を配信するビューアになる）

### WBS の横幅（列が多い表への対処）

Quarto の既定は本文800px固定で、関数名が長いと8列の表に押されて何行にも折り返る。
`ledger.py wbs` は次の3点をセットで出す（手で index.qmd を直さないこと。上書きされる）:

- フロントマターに `page-layout: full`（ページ枠を画面幅に広げる）
- 関数一覧とコールグラフを `::: {.column-screen-inset}` で囲む（本文枠の外に出す）
- 関数一覧に `.wbs-funcs` クラス。`docs/wbs.css`（テンプレからコピー）が
  「関数名は折り返さない／依存(func-id)列が幅を譲る／状態列は最小幅」を決める

`docs/wbs.css` が無い場合は render_site.py がテンプレのCSSで代替する（警告を出す）。
仕様書など他のページは既定の記事レイアウトのまま（本文の可読性を優先）。

### 図（Mermaid）

- 成果物（`.md`）には **GitHub 流の ```mermaid** で書く。HTML は render_site.py、
  PDF は qtpdf.py が同じ変換（→ ```{mermaid}）をしてから render する
- **`.md` に ```{mermaid} と書いてはいけない。** サイト全体の render が
  "You must use the .qmd extension for documents with executable code." で落ちる
  （`.md` に ```mermaid をそのまま置いて `quarto render docs` した場合は、逆に
  render は通るが mermaid.js が読み込まれず図にならない。どちらも実機確認済み）
- 描く対象の目安: WBS＝コールグラフ（ledger.py wbs が自動生成）、
  ①仕様書＝分岐が3本以上ある処理の flowchart、ISSUE＝データの流れ。
  表で足りるものを無理に図にしない
- Mermaid の PDF 化には Chromium 系ブラウザが要る（`qtpdf.py doctor` で確認できる）。
  HTML はブラウザ側で描画するので不要

### PDF（種別ごとの合本）

```bash
python <LR>/scripts/pdf_book.py specs        --root . --output pdf/関数仕様書.pdf     --title 関数仕様書
python <LR>/scripts/pdf_book.py test-specs   --root . --output pdf/テスト仕様書.pdf   --title テスト仕様書
python <LR>/scripts/pdf_book.py test-results --root . --output pdf/テスト結果報告書.pdf --title テスト結果報告書
```

- pdf_book.py は Quarto 1.10 系の book+typst の2つの不具合（章フロントマターの title で
  テンプレートが落ちる / orange-book の author 既定値で Typst が落ちる）を前処理とパッチで回避する
- PDF では相対リンク・アンカーは平文化される（HTML版にリンクが残る）
- 生成後は `qtpdf.py check <pdf>` で豆腐・はみ出しを機械チェックする

### ④の詳細仕様（Sphinx）

- `docs-sphinx/` はテンプレ（assets/templates/sphinx-conf.py, sphinx-index.rst）から⓪で配置。
  テーマは Read the Docs（依存: `python -m pip install sphinx sphinx-rtd-theme`）
- `python -m sphinx -b html docs-sphinx docs/_site/api` で生成
- **順序は必ず「render_site.py → sphinx」**（render_site.py が _site を作り直すため）。
  WBS のナビバー「新コード詳細(API)」= `api/index.html` から導線が通る
