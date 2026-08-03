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

1. skill が承認用サマリ（変更点・チェックリスト）をチャットに提示する
2. 人がチャットで OK / 修正指示を返す
3. OK なら skill がフロントマター（status, approved-by, approved-date）を更新する

勝手に approved にしない。承認待ちで turn を終えるのは正しい動作。

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
- **⓪の時点でもリンク切れは出ない**: ナビバーが参照する未生成ページ
  （domain-knowledge / completion-check / analysis / api）は render_site.py が
  「いつ生成されるか」を書いたプレースホルダを影コピー側に自動で置く（成果物 docs/ は汚さない）。
  WBS 側も、仕様書ファイルが存在しない関数はリンクにしない（ledger.py `_spec_ref`）
- Quarto 未導入なら quarto-typst-pdf skill の `qtpdf.py install` でポータブル導入
  （`~/.local/quarto/bin/quarto`。PATH 登録不要）
- 閲覧は `docs/_site/` を静的サーバで配信。**cwd を _site の外にして** `--directory` で指定する
  （例: プロジェクトルートで `python -m http.server 8765 --directory docs/_site`。
  _site の中を cwd にすると再レンダリング時の削除がロックされて失敗する）
- 成果物フロントマターに独自キーを足すときは Quarto 予約キーと衝突させない（coverage → tc-coverage の前例）

### WBS の大規模対応（200関数超で自動分割）

`ledger.py wbs` は 200 関数を超えると自動でページを分割する:

- **index.qmd はダッシュボード**: 進捗サマリ・要対応（⛔blocked / ⚠stale / ⚠改変 /
  ❌fail）・Open ISSUE・次の一手（上位10）・レガシーファイル別の進捗表
- **全関数の明細は docs/wbs/<ファイル別>.qmd**（自動生成・手編集禁止）。
  `_quarto.yml` の render に `wbs/*.qmd` が必要（テンプレは対応済み。旧プロジェクトは
  render_site.py が影コピー側で自動追記する）
- 200 以下では従来どおり1ページに全関数表

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
