---
name: legacy-reverse
description: レガシーコード（Fortran/C/C++等）をPython へ仕様ベースで移植するリバースエンジニアリング・パイプラインの全体管理。導入セットアップ、WBSによる進捗確認、次アクションの提案を行う。「レガシー移植の状況」「次どの関数をやる」「legacy-reverse をセットアップ」などで使う。各フェーズの実作業は legacy-0-analyze / legacy-0-dict 〜 legacy-6-check の各skillで行う。
user-invocable: true
---

# legacy-reverse — レガシー移植パイプラインの全体管理

パイプライン: ⓪解析 → ⓪変数辞書 → ①仕様書 → ②テスト仕様 → ③テストコード → ④実装 → ⑤テスト → ⑥完了検証。
フェーズのトリガはすべて人。このskillは「状況を見せて、次を提案する」係。

必読: [references/workflow.md](references/workflow.md)（情報遮断・ハッシュ連鎖・ISSUE・承認・ループ規則）、
[references/schema.md](references/schema.md)（プロジェクト構成とデータスキーマ）。
グラフ層・変数辞書・フロー・例外ポリシーの設計は
[references/graph-dict-design.md](references/graph-dict-design.md)。

以下 `LR` = このskillのルート、`ledger` = `python <LR>/scripts/ledger.py`。

## 呼ばれたらすること

1. **人の直接入力のスキャン**（workflow.md「人の直接入力」参照）: open ISSUE の回答欄・
   docs/review-feedback.md の pending に記入があれば先に反映する。
   conventions.md / domain-knowledge.md の手編集（人だけが書くファイル）にも気づいたら反映確認
2. 対象プロジェクトに `data/functions.json` があるか確認
   - **ない** → 未セットアップ。下記「セットアップ」を案内し、`/legacy-0-analyze` を勧める
   - **ある** → `ledger status --summary` と `ledger next --all --limit 10` を実行し、
     進捗サマリ・open ISSUE・⛔ blocked をまとめて見せ、次の一手を提案する。
     **大規模（数百〜2000関数）でも全関数リストは読まない**（summary で足りる。
     workflow.md「再開（レジューム）」参照）。
     `next` が dict-gate で関数を除外していたら `/legacy-0-dict`（辞書の承認）へ、
     `hazards.py status` に未決定が残っていたら例外ポリシーの決定へ誘導する
3. open ISSUE がある場合は必ず最初に列挙する（人の判断待ちが最優先）
4. ⛔ blocked の関数は「ISSUE裁定 → 反映 → `ledger unblock <func-id>` → 再トリガ」の手順を添える
5. 人から「これ覚えておいて」系の業務知識を聞いたら、DK-ID・出典（直接指示 YYYY-MM-DD）
   付きの**転記文を提案**し、人が domain-knowledge.md に貼る（人だけが書くファイル。
   AI は書き込まない）。貼られたら HTML を再レンダリングする

## セットアップ（新規プロジェクト）

1. schema.md のプロジェクト構成でディレクトリを作る
2. hook と権限を登録: `<LR>/hooks/settings-example.json` の内容を対象プロジェクトの
   `.claude/settings.json` にマージ（`hooks` = ④⑤中の tests/ 編集をブロックする
   安全装置。`permissions.allow` = 無人バッチの headless が使うツール一式）
3. `ledger init-templates` で**人が書くファイル一式**の雛形を配置し
   （conventions.md・domain-knowledge.md・exception-policy.md・docs/templates/・
   docs/prompts/。記入状況は `ledger authored`）、
   **項目立て・書き方をプロジェクトに合わせて人が編集**するよう案内する
   （固変分離。編集不要ならそのままでよい。固定契約は workflow.md「固定と可変」）
4. `/legacy-0-analyze` へ

## 各フェーズと担当skill

| フェーズ | skill | 主な成果物 |
|---|---|---|
| ⓪ 解析 | legacy-0-analyze | functions.json（call_sites・hazards）・仕様書骨子・WBS・conventions.md・例外ポリシー |
| ⓪ 変数辞書 | legacy-0-dict | data/variables.json・docs/variables.qmd（approved まで） |
| ① 仕様書 | legacy-1-spec | docs/specs/F-xxxx.md（reviewed まで） |
| ② テスト仕様 | legacy-2-testspec | docs/test-specs/F-xxxx.md（approved まで） |
| ③ テストコード | legacy-3-testcode | tests/（freeze まで） |
| ④ 実装 | legacy-4-impl | src/（スタブなし） |
| ⑤ テスト | legacy-5-test | docs/test-results/（pass or 裁定ISSUE） |
| ⑥ 完了検証 | legacy-6-check | docs/completion-check.md |
| ⑦ 分析・改善 | legacy-7-analyze | docs/perf.md・docs/analysis.md（挙動保存で適用まで） |

## レンダリング

- HTML は `python <LR>/scripts/render_site.py --root .`（docs/ 一式を1サイト、WBSがトップ）。
  **`quarto render docs` を直接叩かない**（Mermaid が描画されない。理由は render_site.py 冒頭）
- 閲覧は `python <LR>/scripts/serve_site.py --root .`（127.0.0.1 のみ／プロジェクトごとに固定ポート）。
  `--render` で作り直してから配信、`--watch` で docs/ の変更を監視して自動再レンダリング
- 人に渡すときは `python <LR>/scripts/build_viewer.py --root .` で
  サイト同梱の単体実行ファイル（EXE）にする。渡した相手は Python も Quarto も要らず、
  各自のローカルホストで開くだけ（詳細は [references/workflow.md](references/workflow.md) の「配布」）
- PDF は `pdf_book.py` で 仕様書 / テスト仕様書 / テスト結果 の種別ごとに個別
  （実体は quarto-typst-pdf skill）
- ④の詳細仕様（docstring）は Sphinx で HTML のみ
- 図は成果物（.md）に GitHub 流の ```mermaid で書く。```{mermaid} は render を落とす
