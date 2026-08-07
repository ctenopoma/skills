# 共通ワークフロー規則（全フェーズskillが従う）

## 機械操作の呼び出し方（MCP優先）

mcp-servers/legacy-reverse-mcp が登録済みの環境では、本書に出てくる
`ledger.py …`・pytest＋collect_results（⑤）・check_stubs・profile_run・quarto/sphinx/pdf_book は
**同名相当の MCP ツールで呼ぶこと**（pipeline_status / run_tests / render_site 等。
構造化された結果が返り、シェル引用の事故と許可プロンプトが減る）。
未登録の環境では従来どおりスクリプトを直接実行する。両者の実体は同一。

## 情報遮断（クリーンルーム）

| フェーズ | 読んでよい入力 | 読んではいけないもの |
|---|---|---|
| ⓪ 解析 | legacy/ 全部 | — |
| ① 仕様書 | legacy/ 該当関数、functions.json、domain-knowledge.md | tests/、src/ |
| ② テスト仕様 | ①(reviewed)、conventions.md、domain-knowledge.md | **legacy/**、src/、tests/ |
| ③ テストコード | ②(approved)、conventions.md | **legacy/**、**①**、src/ |
| ④ 実装 | ①(reviewed)、conventions.md | **legacy/**、**②**、**tests/** |
| ⑤ テスト | 結果＋①②（トリアージ判断用）、src/（(a)修正時） | legacy/、tests/ の編集 |
| ⑦ 分析 | src/・docs/・計測結果（全体を見る） | tests/ の編集（挙動保存が大原則） |

- 「読んではいけない」に触れたくなったら、それは仕様の穴。ISSUE を起票して停止する
- レガシー原文を読める役割は ⓪ と ①（改訂含む）だけ

## ハッシュ連鎖

- ②のフロントマター `spec-hash` に生成時点の①のハッシュ、ledger.json に③freeze時のハッシュを記録
- `python <LR>/scripts/ledger.py verify <func-id>` で連鎖を検証。不一致＝上流が改訂された＝下流は「要再生成」
- ハッシュは `ledger.py hash <path>`（sha256 先頭8桁）

## 機械レビュー（ハルシネーション・省略の検知ゲート）

LLM 成果物は人に見せる前に `review_checks.py`（MCP: review_spec / review_testspec /
review_all）で機械検証する。**NG が残る成果物を「できました」と報告してはいけない。**

| フェーズ | ゲート | 検知するもの |
|---|---|---|
| ① draft後 | `review_checks.py spec <fid>` | 実在しない `file:lines` の引用、🟢なのに根拠なし、プレースホルダ残存（省略）、必須節欠落、原本ハッシュ不一致 |
| ② 承認依頼前 | `review_checks.py testspec <fid>` | 🟢仕様項目のケース漏れ、①に無いSPEC-IDの参照（捏造）、TC参照先の不在、根拠の規定外表記、spec-hash 鮮度 |
| ③ freeze前 | `pytest --collect-only` ＋ marker突合（collect_results が exit 3） | ケースIDとテスト関数の過不足 |
| ④ 完了前 | `check_stubs.py` | 空実装・NotImplementedError・TODO/FIXME（スタブ化の検知） |
| ⑤ | `ledger verify` ＋ collect_results | ②stale・テスト改変・blocked |
| ⑥ 前 | `review_checks.py all` | 全関数の①②の総点検 |

機械で検知できない「意味のすり替え」（式は書いてあるがレガシーと違う等）は、
人レビューと④→⑤の失敗ループが受け持つ。疑わしければ ISSUE。

## 無人バッチ実行（pipeline.py ドライバ）

①の全件実行（2000件規模）はエージェントの会話内ループでは行わない
（コンテキスト上限・コンパクション劣化のため）。`scripts/pipeline.py` を使う:

```bash
python <LR>/scripts/pipeline.py spec --root . [--max-funcs 200] [--budget-usd 20]
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
  レート待機は無人前提なのでバッチ既定の6時間。
  残タスクは `GET /batch-queue`（実行順・検索・件数集計。`_scan_targets` と同じ判定）、
  ⭐優先は `POST /batch-priority` → `.legacy-reverse/batch-priority.json`
  （order=割り込み順・retry=失敗スキップの解除。次の走査で反映）
- CLI の pipeline.py は `spec`（①専用・従来バッチ）と `run`（①〜⑤工程横断。
  browser_run の `_scan_targets`/`KINDS` を共有し⭐優先も反映）の2サブコマンド。
  連続実行はロック（run.lock）・RunStatus・/pipeline.html 表示をすべて共有する
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
| specs/ | △ | 編集可。ハッシュ連鎖が②を stale に落とし再確認が走る（設計どおり） |
| index.qmd / test-results/ / completion-check.md | ❌ | 自動生成。再生成で消える |

- conventions.md を途中で変更した場合は影響が③④の既存成果物に及ぶ。skillは変更を検知したら
  「どの関数の成果物と不整合になり得るか」を洗い出して人に報告する

## 人の承認ゲート

対象: ①の reviewed 化、②の approved 化、⑤トリアージの (b)(c)、ループ上限後の再開。

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
