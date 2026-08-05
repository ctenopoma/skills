# 引き継ぎ: legacy-reverse 改善タスク（Web セッションから）

> ローカルの Claude Code でこのファイルを読ませて「続きを実装して」と言えば再開できます。
> 用が済んだらこのファイルは削除してください（dist ブランチには入れない）。

## 現在地

- 作業ブランチ: `claude/legacy-reverse-dist-wbs-links-26goav`（このファイルがあるブランチ）
- 配布ブランチ: `legacy-reverse-dist`（利用側が pull する方。作業ブランチから随時マージ）
- ここまでの主な変更（全て両ブランチ反映済み）:
  - progress_summary の大規模時 failed 修正（MCP の出力切り詰めが原因だった）
  - `ledger add / exclude / include`（関数リストの人による後追い調整。物理削除禁止）
  - `extract_c.py`（C/C++ 抽出。Fortran↔C の呼び出しをアンダースコア規約込みで自動リンク）
  - メインルーチンの抽出（Fortran program / F77 暗黙メイン / C main → F-0000）
  - render_site.py の差分レンダリング（2000関数で1時間→数十秒。--full で全体+検索索引）
  - pipeline.py: claude 起動プリフライト（Windows の .ps1/PATH 問題対策）、
    エージェント応答全文の保存（.legacy-reverse/agent-logs/）、失敗分類
  - ライブ進捗ページ `/pipeline.html`（serve_site.py が配信。WBS ナビバーからリンク済み）

## 次のタスク: ①レビューをブラウザから完結させる（設計合意済み・未実装）

背景: 人の①レビューは現状「spec-review.md を読む→チャットで OK/修正指示」。
機械レビューNGの理由も表に出ておらず、ブラウザから原因に辿り着けない。

実装内容（/pipeline.html と同じ方式で serve_site.py に同居させる）:

1. **機械レビューNG理由の可視化**
   - `review_checks.make_report` を拡張し、関数ごとの NG 理由リストを
     機械可読 JSON（例: docs/spec-review.json か .legacy-reverse/review-status.json）に出す
   - spec-review.md の表にも理由（先頭数件）を載せる
2. **`/review.html`（serve_site.py に埋め込みページ + ルート追加）**
   - draft 一覧: 概要・🟢🟡🔴内訳・機械レビュー結果（理由を展開表示）・未確定質問・
     仕様書本文へのリンク
   - 各行に [承認] / [修正依頼（コメント欄つき）] ボタン
   - POST /review-action を serve_site が受けて書き込み:
     - 承認 → docs/specs/<fid>.md の frontmatter を status: reviewed +
       approved-by（初回にページで承認者名を入力・保存）+ approved-date に更新。
       更新後に ledger wbs を再生成（差分レンダで数秒）
     - 修正依頼 → docs/review-feedback.md に追記し、workflow.md の
       「人の直接入力（起動時スキャン）」の対象に加える（各フェーズ skill が拾って反映）
   - 制約: 127.0.0.1 bind のみ・配布 EXE（FROZEN）では書き込み系ルートを無効化
   - WBS ナビバーに「①レビュー」リンク（pipeline.html と同じ静的フォールバックパターン）
3. ドキュメント更新: workflow.md（承認ゲートの媒体にブラウザを追加）・MANUAL.md・
   legacy-1-spec の SKILL.md（review-feedback.md のスキャンを起動時処理に追記）

設計上の合意事項:
- 「承認は人」の原則は不変。媒体がチャット→ブラウザになるだけ
- ①の完全性は「①だけで④が書ける」ことが基準（クリーンルーム）。①の不足は
  ②（⚠未確定）と④（spec-gap ISSUE）が構造的に検知し、修正は必ず①に還流させる

## 検証のやり方（このセッションで使っていた方法）

- ダミープロジェクトを作って ledger/pipeline を直接叩く（Quarto はこの環境に無かったので
  モック quarto で render_site の挙動を検証していた。ローカルに Quarto があれば実レンダでよい）
- serve_site は `--no-open --port 8xxx` で起動して curl / ブラウザで確認
- 変更は作業ブランチに commit → 動作確認後、ユーザーの指示で legacy-reverse-dist へマージ
