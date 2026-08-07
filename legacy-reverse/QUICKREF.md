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
| ⓪ 解析 | `/legacy-0-analyze` | functions.json 確定 | `extract_fortran.py --write`（Fortranは必ず機械抽出。再実行=マージで安全）＋ extract-report の突合ゼロ |
| ① 仕様書 | `/legacy-1-spec F-xxxx`（「まとめてN件」でバッチ） | status: reviewed（人がOK。バッチは spec-review.md で一斉レビュー） | `review_checks.py spec F-xxxx` が NGゼロ |
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
ledger verify F-xxxx                        # ハッシュ連鎖検証（②stale・③改変・blocked）
ledger wbs                                  # WBS再生成（200関数超は自動でページ分割）
ledger next-issue                           # 次の ISSUE 番号
ledger unblock F-xxxx                       # 裁定反映後のブロック解除
ledger add NAME [--file legacy/x.f ...]     # 関数の後追い追加（→ skeletons → wbs）
ledger exclude F-xxxx --reason "..."        # 移植対象から外す（物理削除はしない）
ledger include F-xxxx                       # 対象外からの復帰
```

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
python <LR>/scripts/pipeline.py spec --root .                  # ①を全件 draft まで無人実行
python <LR>/scripts/pipeline.py spec --root . --max-funcs 200  # 今日は200件だけ
python <LR>/scripts/pipeline.py spec --root . --dry-run        # 対象の確認のみ
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

## 画面を開く・ブラウザから操作する

```bash
python <LR>/scripts/serve_site.py --root .          # 配信＋ブラウザを開く（URLはプロジェクト固定）
python <LR>/scripts/serve_site.py --root . --watch  # docs/ の編集を検知して自動再レンダ
```

**`python -m http.server` で開かないこと。** 承認・実行・裁定のボタンは
`serve_site.py` 経由でしか動きません（素の配信だと押しても無反応になります）。

| 画面 | URL | できること |
|------|-----|-----------|
| WBS | `/` | 進捗・要対応。⑥⑦の実行ボタン（時期が来たら出る） |
| 仕様書 | `/specs/F-xxxx.html` | ①〜⑤の実行ボタン／承認・修正依頼／⑤の裁定 |
| 一斉レビュー | `/spec-review.html` | draft をまとめて承認・修正依頼（行内ボタン） |
| **バッチ状況** | `/pipeline.html` | **連続実行の開始/停止・残タスクの⭐優先・人待ちの承認/裁定・失敗の再実行** |

連続実行（①〜⑤を工程横断で自動）は `/pipeline.html` から。CLI の `pipeline.py` は**①専用**。

## MCP ツール対応（登録済み環境ではこちらが呼ばれる）

extract_functions・extract_c_functions=機械抽出 / review_spec・review_testspec・review_all=機械レビュー /
progress_summary=status --summary / next_actions=next --all / run_tests=⑤一括 /
add_function・exclude_function・include_function=関数リストの後追い調整 /
generate_wbs / render_site / completion_check / freeze_tests / block / unblock ほか全30。

## 画面の記号

✅ 完了 ／ ▲ 作業中（draft・generated 等、承認前） ／ ☐ 未着手 ／
⛔ 裁定待ちで停止（ISSUEに回答を） ／ ❌ 機械レビューNG（このままでは承認不可） ／
⚠ 要再確認（3種: ①改訂で②が stale ／ freeze後のテスト改変 ／ ②の期待値が未確定）
