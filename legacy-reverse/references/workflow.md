# 共通ワークフロー規則（全フェーズskillが従う）

## 機械操作の呼び出し方（MCP優先）

mcp-servers/legacy-reverse-mcp が登録済みの環境では、本書に出てくる
`ledger.py …`・`graph.py`・`variables.py`・`hazards.py`・pytest＋collect_results（⑤）・
check_stubs・profile_run・quarto/sphinx/pdf_book は
**同名相当の MCP ツールで呼ぶこと**（pipeline_status / run_tests / render_site /
graph_query / dict_build / dict_approve / hazard_match 等。
構造化された結果が返り、シェル引用の事故と許可プロンプトが減る）。
未登録の環境では従来どおりスクリプトを直接実行する。両者の実体は同一。

## HTML サイトは閲覧専用・返答は3チャネル

HTML サイト（render_site.py → serve_site.py）は**見せるだけ**で、実行・承認・裁定の
操作を持たない。人の対応が要るページには「いま何待ちで、どう返答するか」の案内パネルが
焼き込まれる。人の返答チャネルは次の3つで、**すべて同格**（どれで返しても同じ結果になる）:

| チャネル | 方法 | 反映 |
|---|---|---|
| チャット | 「F-0012 OK」「F-0012 修正: 〜」「ISSUE-004 は Yes」 | skill が review_actions / ledger を使って反映する |
| ファイル記入 | ISSUE の「回答（人が記入）」欄・docs/review-feedback.md に人が書く | 各 skill の起動時スキャンが拾って反映する |
| CLI | `review_actions.py approve / request-changes / adjudicate`・`variables.py approve / revise`・`hazards.py add-policy`・`ledger unblock` | スクリプトが直接反映しサイトも更新する |

実行の入口も CLI に一本化されている: 単発はチャット（`/legacy-1-spec F-xxxx` 等）、
バッチは `pipeline.py`（工程別の `spec` / `testspec` / `testcode` / `impl` / `test`、
工程横断の `run`、辞書の `dict`）。進捗のライブ表示（/pipeline.html）は残るが表示専用。

## 固定と可変（固変分離）

ワークフロー（工程・情報遮断・ハッシュ連鎖・ゲート・本書の規則）は **skill 共有の固定部**。
一方、**「このプロジェクトではどう書くか」はプロジェクトの所有物**で、人が著者
（AI は読むだけ・書き換えない）。可変面は3つある:

| 可変ファイル | 決めること | 読む工程 |
|---|---|---|
| `docs/templates/spec.md` / `test-spec.md` | **項目立て**（節構成）と各節の記入ガイド | ①② |
| `docs/conventions.md` | **規約**（型対応表・丸め・単位・日付・文字コード・既知バグ・命名・モック方針・docstring 規約・テストID規則） | ①②③④ |
| `docs/prompts/<工程>.md` | **プロンプト調整**（重点・粒度・書き方の癖・繰り返さない指摘・手本にする成果物） | 対応する工程 |

- 配置は **⓪の最初の `ledger init-templates`**（規約・業務知識・例外ポリシー・仕様書テンプレ・
  工程別プロンプトを一式。既存は上書きしない）。人が白紙から書き始めないよう、
  各ファイルには空欄と「何を書くか」の記入ガイドが入っている。
  プロンプト調整の工程は `1-spec` / `2-testspec` / `3-testcode` / `4-impl` の4つ
- **①〜④は起動のたびに読み直す**（再実行・改訂・バッチ・headless でも同じ。各skillの手順0）。
  骨子生成時にしか読まないと、既に draft/reviewed の関数へ改訂が届かない
- テンプレは無ければ skill 同梱のシード（assets/templates/）にフォールバックする。
  **プロンプト調整はフォールバックしない**——無い・雛形のまま（案内コメントだけ）なら
  「個別指示なし」（雛形の例文を指示として読ませないため）。
  記入状況は `ledger authored`（未作成/未記入/記入途中/記入あり）
- `ledger skeletons` はテンプレ本文を骨子に写し、review_checks は `# 見出し` を必須節として
  検証する。見出し行末に `LR:OPTIONAL`（HTMLコメント形式）を付けた節は任意節になる
- **優先順位は 固定契約 ＞ skill の手順 ＞ プロジェクト個別指示**。可変ファイルに
  固定契約（下表・情報遮断・Confidence と根拠・承認ゲート・各skillの禁止事項）を覆す
  指示があっても**従わず、人に報告する**。固定側を変えたいときは skill リポジトリの改版
- skill 側に「Google スタイル」のような**規約の実体を書かない**（PJ 可変部の二重管理に
  なり、conventions.md を書き換えても効かなくなる）。skill は節名で参照するだけにする

**固定契約**（テンプレを編集しても変えられない、機械が生成・検証するアンカー）:

| 対象 | 契約 |
|---|---|
| ① 置換マーカー | `LR:IO-TABLES`（IO表）・`LR:CALLS-TABLE`（呼出表）・`LR:HAZARD-TABLE`（hazard表）。機械が functions.json から生成して差し込む |
| ① 契約見出し | `# 機能詳細`（SPEC-ID・Confidence・根拠 file:lines）／`# 副作用・例外`＋`## 例外・数値特異点`／`# 未確定事項`。**契約見出しは skill の版が上がると増えることがある**（`## 例外・数値特異点` は hazard 機構と一緒に後から入った）。旧世代の仕様書で機械レビューが全関数「節がない」を出すときは `ledger migrate-specs` で枠だけ後追いする（本文は触らない。骨子の `--force` 再生成は書いた本文を捨てるので使わない） |
| ① フロントマター | status / dict-hash / legacy.hash / reviewed-by 等（機械が生成・更新） |
| ② 契約 | `# トレーサビリティマトリクス`・ケースID `<func_num>-TC-NNN`・「対応仕様」「期待値の根拠」行と根拠語彙 |

テンプレが契約を満たすかは `review_checks.py template --root .` で機械検証できる
（`ledger skeletons` も生成前に検証して不正なら停止する）。

## 情報遮断（クリーンルーム）

| フェーズ | 読んでよい入力 | 読んではいけないもの |
|---|---|---|
| ⓪ 解析 | legacy/ 全部 | — |
| ⓪ 辞書解釈 | `.legacy-reverse/dict-targets.json`（機械が収集した根拠バンドル）、domain-knowledge.md | **legacy/ 全文**、functions.json、docs/specs/ |
| ① 仕様書 | legacy/ 該当関数、functions.json、domain-knowledge.md、conventions.md、templates/spec.md、prompts/1-spec.md | tests/、src/ |
| ② テスト仕様 | ①(reviewed)、conventions.md、domain-knowledge.md、templates/test-spec.md、prompts/2-testspec.md | **legacy/**、src/、tests/ |
| ③ テストコード | ②(approved)、conventions.md、prompts/3-testcode.md | **legacy/**、**①**、src/ |
| ④ 実装 | ①(reviewed)、conventions.md、prompts/4-impl.md | **legacy/**、**②**、**tests/** |
| ⑤ テスト | 結果＋①②（トリアージ判断用）、src/（(a)修正時） | legacy/、tests/ の編集 |
| ⑦ 分析 | src/・docs/・計測結果（全体を見る） | tests/ の編集（挙動保存が大原則） |

- 「読んではいけない」に触れたくなったら、それは仕様の穴。ISSUE を起票して停止する
- レガシー原文を読める役割は ⓪ と ①（改訂含む）だけ
- **辞書解釈（`/legacy-0-dict`）は根拠バンドルだけを読む**。
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
  人が「語彙・略語集」へ**先行投入**してから解釈を回す（legacy-0-analyze 手順3.9。
  AI は頻出トークンの候補リストを提示するだけで、記入は人）。
  同様に、②の期待値の前提になる全体既定（丸め・⑤の許容誤差・単位・日付・文字コード・
  既知バグの扱い）は conventions.md の「後戻り高コスト項目」として⓪で人が確定・記入する
  （覆ると②以降が作り直しになるため。関数単位の例外は人が DK に記録し、個別が既定に勝つ）
- `propagate` は functions.json の inputs/outputs/globals の desc を
  `"<意味>(<単位>) [V-0001]"` 形式に機械転記する。**①は IO 表の `[V-xxxx]` を書き換えない**
  （辞書が正。矛盾を見つけたら辞書側を revise する）
- 承認は人（チャットで頼んでも、人が CLI の `approve`/`revise` を直接叩いても同格）

### dict-gate（既定 ON）

**変数の語義が未承認の関数には①を書かせない。**

- 判定の唯一の実装は `ledger.Project.dict_gate_blockers`。`ledger next` と
  連続実行の対象選定（`pipeline._decide_kind`）が共有する
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
python <LR>/scripts/pipeline.py testspec --root .                # ②だけ全件（工程別サブコマンド）
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
- 前提: 対象プロジェクトに skill 配置済み。**許可は既定でスキップする**
  （`claude -p` に `--dangerously-skip-permissions` を付けて起動する）。
  headless には許可プロンプトに答えられる人がいないため、既定が「聞く」だと
  未許可のツール呼び出しが黙って拒否され、「応答はあるのにファイルが更新されない」
  失敗を工程ごとに繰り返すことになる。安全装置は許可プロンプトではなく
  hooks（`guard_tests.py` の tests/ 保護、`guard_json.py` の JSON 破損検出）が担う
- 許可で止めたい環境は `--no-skip-permissions` を付け、`.claude/settings.json` に
  `<LR>/hooks/settings-example.json` の `permissions.allow` をマージしておく
- **`--flow <名前|FL-01>`**（spec / run / dict 共通）で対象をそのフロー到達集合に限定できる
  （`ledger flow add` で定義したもの。「このフローだけ今日やる」）
- **モデルは選ばない**: 全工程 `claude -p` の既定モデルで回す（`--model` は付けない）。
  工程ごとにモデルを変えられる作りだと、その指定が通らない環境で「その工程だけ
  応答が返らない」という切り分けの難しい失敗になるため撤去した。実験したいときだけ
  `--claude-args` で明示する（自己責任の抜け道）


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
- 対象外: `ledger exclude F-xxxx [F-yyyy ...] --reason "..."` → ①〜⑥・WBS・next から外れ、
  WBS の「対象外の関数」に理由つきで残る。既存成果物は消さない。
  呼び出し元が対象内に残る場合は警告が出る（必要なら ISSUE で裁定）。
  **使われていない関数の一括除外は `ledger exclude --dead`**（`graph.py dead` と同じ
  「エントリから到達不能」の集合を、一覧表示のうえまとめて対象外にする。
  除外は人がこのコマンドを実行することで確定する＝自動除外はしない、の原則のまま）
- 復帰: `ledger include F-xxxx`

## ISSUE 運用

- 採番は全体通し。`docs/issues/` の最大番号+1（`ledger.py next-issue` が返す）
- 必ず「仮説＋Yes/Noで答えられる問い」の形式（templates/issue.md）
- 人の回答が付いたら status: answered → 成果物へ反映して applied。
  ドメイン知識として残すべき回答は、**AI が転記文を提案し、人が domain-knowledge.md に貼る**
  （domain-knowledge.md は人だけが書くファイル。下記「ファイルの作成者区分」）
- kind: spec-gap(④が詰まった) / domain / legacy-bug / triage(⑤ループ上限) / other
- **回答の受け取り方は3系統**（本書冒頭の返答チャネル）: チャット（AskUserQuestion 含む）、
  人が ISSUE ファイルの「回答（人が記入）」欄へ直接記入、
  ⑤裁定なら CLI `review_actions.py adjudicate`。どれも同等に扱う

## 人の直接入力（起動時スキャン）

**すべてのフェーズskillは、本処理の前に次を確認する:**

1. open の ISSUE で「回答（人が記入）」欄が埋まっているものがないか → あれば先に
   反映処理（answered → 成果物更新 → applied）を行う。ドメイン知識として残すべき回答は
   転記文を提案し、人が domain-knowledge.md へ貼る（AI は書き込まない）
2. 人からチャットでドメイン知識・規約変更を告げられたら、記入すべき内容を
   提案文（出典:「直接指示 YYYY-MM-DD」付き）として提示し、人が
   domain-knowledge.md / conventions.md に記入する。記入後のレンダリングは AI が行う
3. `docs/review-feedback.md` に「状態: pending」の項目がないか → あれば先に反映し
   「状態: applied」に書き換える（このファイルの著者は人。直接編集でも
   `review_actions.py request-changes` 経由でもよく、チャットで「F-xxxx は修正: 〜」と
   言うのと同じ扱い）

### ファイルの作成者区分（人だけが書く / AI が書く / 機械生成）

**人だけが書くファイル**（AI は読み・提案まで。書き込まない）:

| ファイル | 備考 |
|---|---|
| docs/conventions.md | プロジェクト規約。⓪で AI が質問リストと記入例を提示し、人が記入する |
| docs/domain-knowledge.md | 業務知識・ISSUE回答の蓄積。AI は転記文を提案、人が貼る |
| docs/exception-policy.md | EP-xxx 登録簿。人が `hazards.py add-policy` を実行して登記する（直接編集も可。列の並びは変えない） |
| docs/templates/*.md | 仕様書の項目立て・書き方（固変分離の可変部） |
| ISSUE の「回答（人が記入）」欄 | 本文（仮説・質問）は AI が書く。回答欄だけ人 |
| docs/review-feedback.md | 修正依頼。直接編集でも `review_actions.py request-changes` でもよい |

**AI が書くファイル**（パイプライン成果物。機械レビュー＋人の承認ゲートを通る）:

| ファイル | 備考 |
|---|---|
| docs/specs/ | ①。人の手編集も可（ハッシュ連鎖が②を stale に落とし再確認が走る） |
| docs/test-specs/ / tests/ / src/ | ②③④ |
| docs/test-results/ / docs/analysis.md | ⑤⑦ |
| ISSUE の本文 | 仮説＋Yes/No の問い |
| data/interpretations.json | 辞書解釈の受け渡し（verify-interp が検証後に消費） |

**機械生成**（手編集禁止。再生成で消える）:

| ファイル | 備考 |
|---|---|
| docs/index.qmd / docs/wbs/ / spec-review.md / completion-check.md | ledger / review_checks が生成 |
| variables.qmd / exception-queue.md / dict-conflicts.md / quant.md | 語義の修正は `variables.py revise` で |
| data/functions.json / variables.json / hazard-map.json / ledger.json | スクリプト専用（functions.json への人の調整は ledger add/exclude 経由） |

- conventions.md を途中で変更した場合は影響が③④の既存成果物に及ぶ。skillは変更を検知したら
  「どの関数の成果物と不整合になり得るか」を洗い出して人に報告する
- 「人だけが書く」は著者の区分であって、人が CLI（add-policy / request-changes 等）を
  使って書き込むのは当然可。**AI がチャット指示を受けて代筆することもしない**
  （提案文の提示まで。これらのファイルが人の意思の一次記録である状態を保つ）

## エージェントへのフィードバックの経路（指摘の射程で3段）

人の指摘は「1件を直す」で終わらせず、**同じ指摘が二度と来ない場所**へ載せる。
射程ごとに経路と著者が違う:

| 射程 | 経路（人が書く場所） | 効き方 |
|---|---|---|
| **この1件だけ** | チャット「F-0012 修正: 〜」／`review_actions.py request-changes`／`docs/review-feedback.md` に直接記入 | pending → 次の①/②実行（`pipeline.py run` の自動修復を含む）が拾って applied |
| **このプロジェクト共通**（毎回同じ指摘をしている） | 項目立てなら **`docs/templates/*.md`**、書き方・重点なら **`docs/prompts/<工程>.md` の「繰り返さないでほしい指摘」**、規約なら conventions.md、業務知識なら domain-knowledge.md | ①〜④は毎回これらを読んでから書くので、以後の全関数に効く（③④はテンプレが無いので prompts と conventions が受け皿） |
| **全プロジェクト共通**（skill の不備・機械で判定できる指摘） | skill リポジトリを直す: SKILL.md の手順に足す／**`review_checks.py` に検査を足して機械ゲートにする** | どのプロジェクトでも自動で防がれる |

- 目安: **同じ指摘を2回したら2段目**（テンプレの記入ガイドに書く）、
  **3回目かつ機械で判定できる形なら3段目**（検査を足す）。
  「毎回1件ずつ人が直させる」状態を続けない
- 2段目が効くのは、①〜④が**書く前に必ず可変ファイルを読む**ため（各skillの手順0）。
  骨子生成時にしか反映されないと、既に draft/reviewed の関数へ届かない
- skill 自身の整合性（文書とスクリプトの食い違い）は
  `python <LR>/scripts/check_skill.py` が機械検証する（skill 開発側のゲート）

## 人の承認ゲート

対象: **変数辞書の語義確定**、**例外ポリシーの決定**、①の reviewed 化、②の approved 化、
⑤トリアージの (b)(c)、ループ上限後の再開。

承認の媒体は2通りあり、どちらも同格（承認が人である、という原則は変わらない）:

**チャット経由**
1. skill が承認用サマリ（変更点・チェックリスト）をチャットに提示する
2. 人がチャットで OK / 修正指示を返す
3. OK なら skill が review_actions（approve / request_changes）で反映する
   （承認直前にサーバ側で機械レビューを再検証し、NG が残る成果物は入口によらず拒否される）

**CLI 経由**（人が端末で直接実行する）
```bash
python <LR>/scripts/review_actions.py approve spec F-0012 --by 山田 --root .
python <LR>/scripts/review_actions.py request-changes spec F-0012 --by 山田 --comment "…" --root .
python <LR>/scripts/review_actions.py adjudicate F-0012 --issue ISSUE-004 --by 山田 --comment "…" --root .
```
どちらの経路もフロントマター更新 → WBS・一斉レビュー表・サイトの差分再生成まで行う。
閲覧サイトの案内パネル（承認待ち・裁定待ちのページ先頭）に、この3チャネルの
具体的なコマンドが表示される。

勝手に approved にしない。承認待ちで turn を終えるのは正しい動作。

- **①は一斉レビュー可**: バッチモード（legacy-1-spec）で複数関数を draft まで連続処理し、
  `review_checks.py report` が生成する docs/spec-review.md（一斉レビュー表）で人が
  まとめて OK / 個別修正指示を返せる。承認が人であることは変わらない（粒度の違いだけ）。
  一斉レビュー表の「機械レビュー」列は仕様書ページの案内パネル（#review-<fid>）へ直接
  ジャンプするリンクになっており、❌の場合はその場で理由の全文が読める（件数だけで終わらない）

### 変数辞書の承認（チャット / CLI）

`variables.py page` が生成する `docs/variables.qmd`（辞書ページ）は、
**影響度（出現関数数）降順 × rank 昇順**で並ぶ——人の確認が要る C/D と、多くの関数に
効く変数が上に来る。未承認が残る間は、render_site.py がページ先頭に承認方法の
案内パネルを焼き込む（閲覧専用。件数と CLI コマンドを表示する）。

- 承認はチャット（「V-0001,V-0002 を承認」等）か CLI:
  `variables.py approve V-0001,V-0002 --by <名前>` /
  `variables.py revise V-0003 --desc "..." [--unit "..."] --by <名前>`
  （rank A/B は一括承認候補、C/D は revise で意味を確定させながら承認する。
  rank D・desc 未確定の approve は variables.py 側が拒否する）
- 承認1回でクラスタの全出現に効く。承認後は
  **propagate → skeletons → 辞書ページ再生成 → サイト差分レンダ**まで続けて実行する
  （/legacy-0-dict の手順。CLI 直叩きの場合も同じ順で実行する）

### 例外ポリシーの決定（exception-queue → add-policy）

1. `hazards.py match` が未決定を `docs/exception-queue.md` に kind ごとに集約して出す
   （仮説＋選択肢＋該当箇所の表＋登録コマンド例）
2. 人にその kind の**既定**をどうするか聞く（`guard_raise` / `detect_only` / … /
   「式ごとに個別判断」）。AIが勝手に決めない
3. **人が** `hazards.py add-policy --kind <k> --decision <語彙> --by <名前>` を実行して
   登録する（exception-policy.md は人だけが書くファイル。AI はキューに出ている
   コマンド例を示すまで。個別に変えたい箇所だけ `--func` / `--hazard` を付けて追加登録。
   個別が全体既定に勝つ）
4. 登録すると自動で再突合され、キューが更新される。全件決定するまで①は書けない

### 閲覧サイトの案内パネル（render 時に焼き込む静的表示）

`docs/specs/<fid>.md` が `status: draft`、`docs/test-specs/<fid>.md` が `status: generated`
の間、render_site.py はそのページの本文冒頭（フロントマター直後）に案内パネルを焼き込む。
仕様書を読んでいるその場で「何待ちか・どう返答するか」が分かる設計で、操作は持たない。

- 機械レビュー結果は render 時点のものをその場に表示する（NGなら理由の全文と
  「AI に自己修正させる」手順。✅なら3チャネルの承認方法）。NG が残る成果物の承認要求は
  review_actions 側（チャット/CLI どちらの入口でも）が再検証して拒否する
- ⑤裁定待ち（blocked）の関数の①ページには、ISSUE へのリンク・「質問（人への問い）」の
  本文・回答方法（回答欄への記入 / チャット / `review_actions.py adjudicate`）が出る
- 変数辞書ページ・WBSトップ（⑥⑦の時期が来たとき）にも同様の案内が出る
- 修正依頼は `docs/review-feedback.md` に「状態: pending」で記録される（人が直接書いても
  `request-changes` 経由でも同じ）。status は変えない（次回の①/②AI実行時に
  「人の直接入力（起動時スキャン）」で拾われ、反映後「状態: applied」になる）
- 配布用 EXE（build_viewer.py の成果物）は docs/_site のスナップショット同梱で、
  もともと書き込み経路が無い（閲覧専用という性質は開発機のサイトと同じ）
- WBS の関数一覧・一斉レビュー表からのリンクは、承認待ちの間だけこのパネルの
  アンカー（`#review-<fid>`）へ直接ジャンプする
- **draft は再実行（書き直し）自由**。reviewed の書き直しは②が stale になるため人の了承を先に取る

## skill の版を上げたあとのやり直し（派生物だけリセット）

検査や契約見出しが増えた版に移行するとき、**①の成果物を捨てずに**状態だけ作り直す手順。

**func_id は再抽出しても変わらない**。`extract_fortran.py` の merge は既存エントリを
`(レガシーファイルのパス, ルーチン名)` で突合し（`_key`）、一致すれば既存の func_id を
そのまま使う。既存の `inputs/outputs/globals` も「空のときだけ」入れ直すので、
`variables.py propagate` が書いた `[V-xxxx]` 付きの desc も残る。
**`data/functions.json` を消さない限り `docs/specs/*.md` は全部そのまま使える**。
（消してしまった／レガシーのファイル名・ルーチン名を変えた場合だけ ID が変わる）

```bash
python <LR>/scripts/extract_fortran.py --root .        # 1. 台帳を最新ソースに合わせる（マージ）
python <LR>/scripts/hazards.py match --root .          # 2. 例外ポリシーの突合をやり直す
python <LR>/scripts/variables.py build --root .        # 3. 変数辞書（承認は維持。未使用なら飛ばす）
python <LR>/scripts/variables.py propagate --root .    #    承認済みの語義を IO/globals へ転記
python <LR>/scripts/variables.py page --root .
python <LR>/scripts/ledger.py skeletons --root .       # 4. 骨子（既存の仕様書は上書きしない）
python <LR>/scripts/ledger.py migrate-specs --root .   # 5. 後から入った契約見出しを既存①へ追加
python <LR>/scripts/review_actions.py demote-ng spec --root . --dry-run
python <LR>/scripts/review_actions.py demote-ng spec --root .   # 6. NGの承認済みだけ draft へ
python <LR>/scripts/ledger.py wbs --root .             # 7. 反映
python <LR>/scripts/render_site.py --root .
```

- **4 で `--force` を使わない**。骨子の再生成は書いた本文を捨てる
- **5 → 6 の順を守る**。契約見出しを足す前に判定すると、足せば直るものまで差し戻す
- 6 は機械レビューが **NG のものだけ** 承認前に戻し、承認情報（`reviewed-by` /
  `reviewed-date`）も消す。OK のものは1バイトも触らない。差し戻した分は一斉レビュー表
  （`docs/spec-review.md`）に載るので、人はそこだけ見ればよい
- 消してよいのは生成物だけ（`docs/index.qmd` / `docs/variables.qmd` /
  `docs/spec-review.md` / `docs/_site`）。**`data/functions.json`・`docs/specs/`・
  人が書く MD（規約・業務知識・例外ポリシー・templates・prompts）は消さない**
- 対象外にしたい関数があるなら 1 のあとに `ledger exclude --dead`（→ WBS から降りる）

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
