# legacy-reverse クイックリファレンス（作業用）

作業中に手元へ置く1枚。初見の人は [slides/index.html](slides/index.html)
（スライド版チュートリアル）から。背景説明・スクリーンショット付きの解説は
[MANUAL.html](MANUAL.html) / MANUAL.pdf を参照。

- **`<LR>`** = 配置済み skill のルート = `<project>/.claude/skills/legacy-reverse`
- **`ledger`** = `python <LR>/scripts/ledger.py` の略記（そういう実行ファイルはありません）
- コマンドは断りがなければ**プロジェクトルートで実行**します（だから `--root .`）

## いま何をすべきか（迷ったら・再開するときは、常にこの3つ）

```bash
ledger status --summary                              # 全体状況（2000関数でも数行）
ledger next --all --limit 20                         # 着手可能な関数と次フェーズ
python <LR>/scripts/review_checks.py all --root .    # 成果物の健全性
```

チャットなら `/legacy-reverse` と打つだけ（上記を実行して次の一手を提案してくる）。
**中断からのやり直し・再列挙は不要**。進捗の正はすべてファイル側にある。

## フェーズ起動 → 完了条件 → 機械ゲート

| フェーズ | 起動 | 完了条件 | 通過必須の機械ゲート |
|---|---|---|---|
| ⓪ 解析 | `/legacy-0-analyze` | functions.json 確定・例外ポリシー決定済み | `extract_fortran.py --write`（Fortranは必ず機械抽出。再実行=マージで安全）＋ extract-report の突合ゼロ＋ `hazards.py status` の未決定ゼロ |
| ⓪ 変数辞書 | `/legacy-0-dict` | 変数が approved（人がOK） | `variables.py verify-interp` が NGゼロ |
| ① 仕様書 | `/legacy-1-spec F-xxxx`（「まとめてN件」でバッチ） | status: reviewed（人がOK。バッチは spec-review.md で一斉レビュー） | `review_checks.py spec F-xxxx` が NGゼロ（dict-gate: 語義が未承認の関数は①に進めない） |
| ② テスト仕様 | `/legacy-2-testspec F-xxxx` | status: approved（人がOK・⚠未確定ゼロ） | `review_checks.py testspec F-xxxx` が NGゼロ |
| ③ テストコード | `/legacy-3-testcode F-xxxx` | `ledger freeze-tests` 済み | `pytest --collect-only` ＋ マーカー突合（exit 3 なら不整合） |
| ④ 実装 | `/legacy-4-impl F-xxxx` | スタブゼロ | `check_stubs.py <module>` |
| ⑤ テスト | `/legacy-5-test F-xxxx` | result: pass | `ledger verify` → pytest → `collect_results.py` |
| ⑥ 完了検証 | `/legacy-6-check` | 両方 exit 0 | `ledger check` ＋ `review_checks.py all` |
| ⑦ 分析・改善 | `/legacy-7-analyze` | 施策票 achieved | テスト全pass維持（挙動保存） |

## ⑤ fail の裁定（3分類）

| 分類 | 原因 | 操作 |
|---|---|---|
| (a) 実装バグ | 実装が①を満たさない | 何もしない（AIが④修正→⑤を自走、上限3回） |
| (b) テストコードバグ | ③が②とズレ | ISSUE を承認 → `/legacy-3-testcode` 再生成→再freeze |
| (c) 仕様の誤り | ②①が間違い | ISSUE で裁定 → `/legacy-1-spec` 改訂（下流stale は自動検知） |

⛔（3回失敗で自動ブロック）からの復帰:
ISSUE に回答 → AIが反映 → `ledger unblock F-xxxx` → `/legacy-5-test F-xxxx` 再トリガ。

## よく使う ledger コマンド

```bash
ledger status [F-xxxx] [--json|--summary]   # 状況（--summary は要約JSON）
ledger next [--all --limit N]               # 次の一手（--all で一覧）
ledger next --flow 月次バッチ                # フロー到達集合だけに絞る
ledger next --no-dict-gate                  # 変数辞書のゲートを解除（既定は ON）
ledger verify F-xxxx                        # ハッシュ連鎖検証（②stale・③改変・辞書stale・blocked）
ledger wbs                                  # WBS再生成（200関数超は自動でページ分割）
ledger next-issue                           # 次の ISSUE 番号
ledger unblock F-xxxx                       # 裁定反映後のブロック解除
ledger add NAME [--file legacy/x.f ...]     # 関数の後追い追加（→ skeletons → wbs）
ledger exclude F-xxxx --reason "..."        # 移植対象から外す（物理削除はしない）
ledger include F-xxxx                       # 対象外からの復帰
ledger flow add 月次バッチ --entry F-0000    # 作業スコープ（フロー）を定義
ledger flow list / ledger flow rm 月次バッチ # 一覧（到達関数数つき）／削除
```

## コールグラフを調べる（graph.py・LLM不使用・依存ゼロ）

```bash
python <LR>/scripts/graph.py --root . summary              # 規模・エントリ・到達率・dead件数（JSON）
python <LR>/scripts/graph.py --root . dead                 # 到達不能な関数（exclude 候補。自動除外はしない）
python <LR>/scripts/graph.py --root . reachable F-0000     # 到達集合（--flow <名前> でも指定可）
python <LR>/scripts/graph.py --root . callers F-0087 --transitive   # 影響範囲（逆方向）
python <LR>/scripts/graph.py --root . between F-0000 F-0087         # 最短経路
python <LR>/scripts/graph.py --root . cycles               # 循環（SCC）
```

## 変数辞書（①より先。詳しくは `/legacy-0-dict`）

```bash
python <LR>/scripts/variables.py build --root .            # 構築・再構築（常にマージ・承認維持）
python <LR>/scripts/variables.py list-targets --limit 30 --root .   # 未解釈の根拠バンドル
python <LR>/scripts/pipeline.py  dict --root . --chunk 40           # 解釈を無人バッチで（既定 sonnet）
python <LR>/scripts/variables.py verify-interp --root .    # 機械検証してマージ（rank は機械が決める）
python <LR>/scripts/variables.py page --root .             # docs/variables.qmd（ナビバーに自動で出る）
python <LR>/scripts/variables.py approve V-0001,V-0002 --by <名前> --root .
python <LR>/scripts/variables.py revise  V-0003 --desc "年間税率" --unit "比率" --by <名前> --root .
python <LR>/scripts/variables.py propagate --root .        # 承認した語義を仕様書の IO 表へ転記
python <LR>/scripts/variables.py conflicts --root .        # reviewed 仕様書との矛盾候補
```

承認はチャット（「V-0001,V-0002 を承認」）か上の CLI。辞書ページ `/variables.html` は
閲覧用（未承認が残る間は承認方法の案内が出る）。承認後は propagate → 骨子 → サイト更新。

## 例外ポリシー（0割・SQRT/LOG・変数添字）

```bash
python <LR>/scripts/hazards.py status --root .             # 総数・kind別・決定済み/未決定
python <LR>/scripts/hazards.py match  --root .             # 突合 → docs/exception-queue.md（質問キュー）
python <LR>/scripts/hazards.py add-policy --kind div_by_var --decision guard_raise \
       --by <名前> [--func F-0012 | --hazard H-0012-01] [--note "..."] --root .
```

決定の語彙: `detect_only` / `guard_raise` / `guard_value` / `legacy_preserve` /
`caller_guarantees`。適用範囲は 全体既定 → 関数 → 個別 の順に個別が勝つ。
**未決定を残したまま①は書けない**（機械レビューがNGにする）。

## 機械レビュー（ハルシネーション検知）

```bash
python <LR>/scripts/review_checks.py spec F-xxxx --root .      # ①: 引用実在・🟢根拠・省略
python <LR>/scripts/review_checks.py testspec F-xxxx --root .  # ②: トレーサビリティ・捏造SPEC
python <LR>/scripts/review_checks.py report --root .           # ①draft の一斉レビュー表を生成
python <LR>/scripts/review_checks.py all --root . [--json]     # 総点検（⑥前・再開時）
```

## ①のバッチ実行と一斉レビュー（全件・2000件規模対応）

数十件までは会話で「仕様書をまとめて10件進めて」でよい。
**全件（数百件〜）は無人ドライバで回す**（1関数=1 headless プロセス。
エージェントのトークン上限に依存しない）:

```bash
python <LR>/scripts/pipeline.py run  --root .                  # ①〜⑤を工程横断で無人実行（⭐優先反映）
python <LR>/scripts/pipeline.py run  --root . --max-funcs 100  # 今日は100件だけ
python <LR>/scripts/pipeline.py spec --root .                  # ①だけ全件 draft まで
python <LR>/scripts/pipeline.py run  --root . --dry-run        # 対象と実行順の確認のみ
python <LR>/scripts/pipeline.py run  --root . --flow 月次バッチ # そのフローの到達集合だけ
python <LR>/scripts/pipeline.py run  --root . --only testspec  # ②だけ全件（工程単位バッチ）
python <LR>/scripts/pipeline.py dict --root . --chunk 40       # ⓪変数辞書の解釈（既定 sonnet）
python <LR>/scripts/pipeline.py spec --root . --model opus     # モデルを一括上書き
python <LR>/scripts/pipeline.py priority F-0012                # ⭐優先ON（実行中でも次に割り込む）
python <LR>/scripts/pipeline.py priority F-0012 --off          # ⭐優先解除／引数なしで一覧
```

どちらの方式でも draft は `spec-review.md`（一斉レビュー表）に溜まり、
あなたは表を見て「全部OK」か「F-xxxx は修正: 〜」と返すだけ。

- **途中で止まっても同じコマンド/指示で再開できる**（書きかけ draft は機械NGとして
  検出され先に直される。処理済み draft は二重に書き直されない）
- 全件を待たずに、溜まった draft を随時レビューして reviewed 化してよい
- draft の書き直しは何度でも頼める（reviewed の書き直しは②が要再承認になる旨を確認される）
- ドライバの記録: `.legacy-reverse/pipeline-log.jsonl`（関数別の結果・コスト）

## ⓪ 機械抽出（Fortran）

```bash
python <LR>/scripts/extract_fortran.py --root . --package <pkg> --write
# → data/functions.json（マージ・func_id不変） + data/extract-report.json（監査ログ）
# レビューすべき項目: completeness_mismatches / inferred_calls / unresolved_calls
```

## レンダリング（フェーズ末に必ず）

```bash
ledger wbs && python <LR>/scripts/render_site.py --root .   # quarto render docs は直接叩かない
python <LR>/scripts/render_site.py --root . --full          # 節目に1回（検索索引も更新）
```

## 画面を開く（閲覧専用）・操作は CLI かチャット

```bash
python <LR>/scripts/serve_site.py --root .          # 配信＋ブラウザを開く（URLはプロジェクト固定）
python <LR>/scripts/serve_site.py --root . --watch  # docs/ の編集を検知して自動再レンダ
```

**HTML は見せるだけ**（実行・承認のボタンは無い）。人の対応が要るページには
「何待ちか・どう返答するか」の案内パネルが出る。返答は3チャネル（すべて同格）:
チャット（「F-0012 OK」「修正: 〜」）／ファイル記入（ISSUE 回答欄・review-feedback.md）／CLI。

| 画面 | URL | 見えるもの |
|------|-----|-----------|
| WBS | `/` | 進捗・要対応（⚠辞書stale もここ）・フロー別進捗。⑥⑦の時期が来たら実行方法の案内 |
| 変数辞書 | `/variables.html` | 語義と根拠の一覧（未承認が残る間は承認方法の案内） |
| 仕様書 | `/specs/F-xxxx.html` | 本文＋機械レビュー結果＋承認待ち/裁定待ちの案内パネル |
| 一斉レビュー | `/spec-review.html` | draft の一覧（承認できる/AI修正待ちの状態・検索・フィルタ） |
| バッチ状況 | `/pipeline.html` | 連続実行のライブ進捗・残タスク（実行順・人待ち）・失敗の内訳と応答ログ |
| マニュアル | `/manual.html` | 操作マニュアル（本書の詳説版。EXE 配布にも同梱） |

## 承認・修正依頼・裁定（CLI）

```bash
python <LR>/scripts/review_actions.py approve spec F-0012 --by 名前 --root .
python <LR>/scripts/review_actions.py approve testspec F-0012 --by 名前 --root .
python <LR>/scripts/review_actions.py request-changes spec F-0012 --by 名前 --comment "…" --root .
python <LR>/scripts/review_actions.py adjudicate F-0012 --issue ISSUE-004 --by 名前 --comment "…" --root .
```

どの入口でも承認直前に機械レビューを再検証（NG が残る成果物は承認不可）。
反映後は WBS・一斉レビュー表・サイトの再生成まで自動。

連続実行（①〜⑤を工程横断で自動）は `pipeline.py run`。`pipeline.py spec` は①専用、
⭐割り込みは `pipeline.py priority F-xxxx`（実行中でも効く）。

## MCP ツール対応（登録済み環境ではこちらが呼ばれる）

extract_functions・extract_c_functions=機械抽出 / review_spec・review_testspec・review_all=機械レビュー /
progress_summary=status --summary / next_actions=next --all / run_tests=⑤一括 /
add_function・exclude_function・include_function=関数リストの後追い調整 /
graph_query=コールグラフ照会（summary/dead/reachable/callers/between/cycles）/
dict_build・dict_list_targets・dict_verify_interp・dict_approve・dict_propagate・dict_page・dict_conflicts=変数辞書 /
hazard_status・hazard_match・hazard_add_policy=例外ポリシー / flow_add・flow_list・flow_remove=作業スコープ /
generate_wbs / render_site / completion_check / freeze_tests / block / unblock ほか全44。

## 画面の記号

✅ 完了 ／ ▲ 作業中（draft・generated 等、承認前） ／ ☐ 未着手 ／
⛔ 裁定待ちで停止（ISSUEに回答を） ／ ❌ 機械レビューNG（このままでは承認不可） ／
⚠ 要再確認（4種: ①改訂で②が stale ／ freeze後のテスト改変 ／ ②の期待値が未確定 ／
辞書⚠＝①生成後に変数の語義が改訂された）
