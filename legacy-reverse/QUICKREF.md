# legacy-reverse クイックリファレンス（作業用）

作業中に手元へ置く1枚。初見の人は [slides/index.html](slides/index.html)
（スライド版チュートリアル）から。背景説明・スクリーンショット付きの解説は
[MANUAL.md](MANUAL.md) / MANUAL.pdf を参照。
`<LR>` = この skill のルート、`ledger` = `python <LR>/scripts/ledger.py`。

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
```

## 機械レビュー（ハルシネーション検知）

```bash
python <LR>/scripts/review_checks.py spec F-xxxx --root .      # ①: 引用実在・🟢根拠・省略
python <LR>/scripts/review_checks.py testspec F-xxxx --root .  # ②: トレーサビリティ・捏造SPEC
python <LR>/scripts/review_checks.py report --root .           # ①draft の一斉レビュー表を生成
python <LR>/scripts/review_checks.py all --root . [--json]     # 総点検（⑥前・再開時）
```

## ①のバッチ実行と一斉レビュー（全件・2000件規模対応）

「仕様書をまとめて10件進めて」「①を全件進めて」→ AIが
`next --all --phase 1 --skip-draft` で対象を選び（draft=レビュー待ちは除外）、
機械レビューNGゼロの draft を連続生成 → `spec-review.md`（一斉レビュー表）へ誘導してくる。
あなたは表を見て「全部OK」か「F-xxxx は修正: 〜」と返すだけ。

- **途中で止まっても同じ指示で再開できる**（書きかけ draft は report の機械NGとして
  検出され先に直される。処理済み draft は二重に書き直されない）
- 全件を待たずに、溜まった draft を随時レビューして reviewed 化してよい
- draft の書き直しは何度でも頼める（reviewed の書き直しは②が要再承認になる旨を確認される）

## ⓪ 機械抽出（Fortran）

```bash
python <LR>/scripts/extract_fortran.py --root . --package <pkg> --write
# → data/functions.json（マージ・func_id不変） + data/extract-report.json（監査ログ）
# レビューすべき項目: completeness_mismatches / inferred_calls / unresolved_calls
```

## レンダリング（フェーズ末に必ず）

```bash
ledger wbs && python <LR>/scripts/render_site.py --root .   # quarto render docs は直接叩かない
python -m http.server 8765 --directory docs/_site           # cwd は _site の外で
```

## MCP ツール対応（登録済み環境ではこちらが呼ばれる）

extract_functions=機械抽出 / review_spec・review_testspec・review_all=機械レビュー /
progress_summary=status --summary / next_actions=next --all / run_tests=⑤一括 /
generate_wbs / render_site / completion_check / freeze_tests / block / unblock ほか全24。

## WBS の記号

✅ 完了 ／ ▲ 作業中（draft等） ／ ☐ 未着手 ／ ⛔ 裁定待ち（ISSUEに回答を） ／
⚠ stale・改変（上流改訂 or freeze後変更 → 要再確認）
