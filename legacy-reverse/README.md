# legacy-reverse

レガシーコード（Fortran / C・C++ 等）を Python へ仕様ベースで移植するリバースエンジニアリング・パイプライン。

パイプライン: ⓪リポジトリ解析 → ⓪変数辞書 → ①仕様書 → ②テスト仕様 → ③テストコード → ④実装 → ⑤テスト（fail→④、裁定は人）→ ⑥完了検証 → ⑦分析・改善（挙動保存）

## ドキュメント

| 文書 | 対象読者 |
|------|---------|
| [slides/index.html](slides/index.html) | 初めて使う人（セットアップ→⑦を手順どおりに進めるチュートリアル） |
| [QUICKREF.md](QUICKREF.md) | 作業中の操作者（コマンド即引き1枚） |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Skills とスクリプトの構成・区分け（層／固定と可変／作成者区分／操作の入口／skill 自体の保守） |
| [MANUAL.md](MANUAL.md) / MANUAL.html / MANUAL.pdf | 使う人・skill を触る人（構成・参照関係と、人が書く MD（規約・テンプレ・工程別プロンプト）の手引き）。HTML は画像込みの単一ファイルでそのまま配れる |
| [DESIGN.md](DESIGN.md) | skill の開発者・保守者（構造と設計判断） |
| [../docs/legacy-reverse/](../docs/legacy-reverse/) | 設計者・保守者・運用管理者（skill＋画面＋MCPサーバ＋運用の設計・仕様書。Quarto サイト。`quarto render docs/legacy-reverse` で HTML 化、合本 PDF は同梱の `pdf/`。再生成は `make_pdf.py`。章別に分けたいときは `--chapters`） |
| [references/workflow.md](references/workflow.md) / [schema.md](references/schema.md) | フェーズskill・スクリプト（規則とデータの正） |

## 構成

```
legacy-reverse/
  SKILL.md                 # 全体管理（状況表示・次アクション提案・セットアップ）
  skills/
    legacy-0-analyze/      # ⓪ 解析 → functions.json・骨子・WBS・規約・例外ポリシー
    legacy-0-dict/         # ⓪ 変数辞書（1変数=1語義を人が承認 → ①へ伝搬。①より先）
    legacy-1-spec/         # ① 仕様書（レガシーを読める唯一の役割。spec-gap改訂も担当）
    legacy-2-testspec/     # ② テスト仕様（①のみ入力。人の承認ゲート）
    legacy-3-testcode/     # ③ テストコード（②のみ入力。freeze でハッシュ固定）
    legacy-4-impl/         # ④ 実装（①のみ入力。スタブ禁止）
    legacy-5-test/         # ⑤ 実行・結果収集・トリアージ・ループ管理
    legacy-6-check/        # ⑥ 完了検証と最終レンダリング
    legacy-7-analyze/      # ⑦ 分析・改善（挙動保存）
  scripts/
    extract_fortran.py     # ⓪ Fortran機械抽出 → functions.json 生成/マージ（再実行=マージで再開安全）
    extract_c.py           # ⓪ C/C++機械抽出（同じ functions.json にマージ。Fortran↔C の呼出も突合）
    graph.py               # ⓪ コールグラフの導出層（reachable/callers/between/dead/cycles/summary。依存ゼロ）
    variables.py           # ⓪ 変数辞書エンジン（クラスタリング/根拠収集/検証/承認/伝搬）
    hazards.py             # ⓪ 例外ポリシー（0割等の検知結果 × EP登録簿の突合・質問キュー）
    pipeline.py            # 無人バッチドライバ + 対象選定（1関数=1 headlessプロセス。dict は変数チャンク単位。⭐優先）
    ledger.py              # 台帳: WBS生成/骨子生成（テンプレ駆動）/ハッシュ連鎖/blocked管理/⑥検証/フロー/dict-gate
    review_checks.py       # ①②の機械レビュー・一斉レビュー表・テンプレ契約チェック（ハルシネーション検知）
    check_skill.py         # skill 自身の整合性チェック（文書とスクリプトのドリフト検知。保守用）
    serve_site.py          # ローカル配信（GETのみ・閲覧専用。バッチ進捗の表示ページを内蔵）
    review_actions.py      # ①②の承認・修正依頼、⑤の裁定の CLI（機械レビューを再検証してから反映）
    render_site.py         # docs/ → HTMLサイト（差分レンダ・案内パネル焼き込み）
    tc_report_plugin.py    # pytest プラグイン（TCマーカー別の結果収集）
    collect_results.py     # ②と突合して結果報告書を自動生成（実装率・attempt・自動block）
    check_stubs.py         # ④のスタブ検出（空実装/NotImplementedError/TODO）
    quant_analyze.py       # ⑦の定量計測（cProfile+radon/ruff/bandit/pip-audit。LLM不使用）
  hooks/
    guard_tests.py         # PreToolUse: ④⑤中の tests/ 編集を拒否
    guard_json.py          # PostToolUse: 壊れた JSON をエージェントに差し戻す（正データ保護）
    settings-example.json  # 対象プロジェクトへの hook 登録例（上記2本）
  references/
    schema.md              # プロジェクト構成・functions.json/ledger.json/variables.json スキーマ・status遷移
    workflow.md            # 共通規則（情報遮断・ハッシュ連鎖・ISSUE・承認・辞書/フロー/例外・ループ）
    graph-dict-design.md   # グラフ層・変数辞書・フロー・例外ポリシーの設計の正
  assets/templates/        # 各成果物のテンプレートの**シード**（spec/test-spec は対象PJの docs/templates/ にコピーして人が編集）
  examples/                # 架空の COBOL 関数 CALC-TAX (F-0123) の記入例一式
```

## 導入（対象プロジェクト側）

1. このリポジトリの `legacy-reverse/` を対象プロジェクトの `.claude/skills/legacy-reverse` に配置
2. `legacy-reverse/skills/legacy-*` を `.claude/skills/` 直下にもコピー（skill発見のため）
3. `hooks/settings-example.json` を対象プロジェクトの `.claude/settings.json` にマージ
   （`hooks` = tests/ 保護と JSON 破損検出、`permissions.allow` = 無人バッチの headless が使うツール）
4. `.mcp.json` に `mcp-servers/legacy-reverse-mcp/server.py` を絶対パスで登録（推奨。元リポジトリを参照するので残しておく）
5. Quarto を入れる（HTML サイト生成に必要。`quarto --version` が通ればよい）
6. `ledger init-templates` で人が書くファイル一式（規約・業務知識・例外ポリシー・仕様書テンプレ・
   工程別プロンプト）の雛形を配置し、項目立て・書き方を
   プロジェクトに合わせて人が編集する（編集不要ならそのままでよい）
7. `/legacy-reverse` を実行してセットアップ確認 → `/legacy-0-analyze` から開始

合本PDF まで出す場合のみ、`quarto-typst-pdf/` も `.claude/skills/` に置く
（`pdf_book.py` が `legacy-reverse` の隣として qtpdf.py を探すため。HTML だけなら不要）。

## 確定事項

- レガシー言語: **機械抽出は Fortran と C/C++**（extract_fortran.py / extract_c.py）。
  他言語（COBOL・C# 等）も①以降は同じだが、⓪の関数列挙は AI と人で行う。
  新言語: Python＋pytest（将来 JS/Rust 拡張）
- レガシー実行環境なしが基本前提。②の期待値は 仕様🟢＋人間確認 で確定（実測は環境がある場合のみ）
- ⓪〜⑤のトリガは人。フェーズごとに skill を分ける
- ⑤結果報告書は `{func-id}_{YYYYMMDD-HHMMSS}.md` で毎回1から自動生成（履歴はファイルとして蓄積）
- ⑤の pass 条件は「実装率100%（②の全ケースが③に実装済み）かつ失敗0」
- PDF: 仕様書・テスト仕様書・テスト結果を種別ごとに個別出力（同一Markdownソースから Quarto で HTML も生成し WBS から導線）。④は Sphinx で HTML のみ
- ④⑤ループ上限は3回。到達で triage ISSUE 自動起票→人の裁定→`ledger unblock`→人が再トリガ、attempt リセット

## 原則

- 機械可読メタデータは YAML フロントマターに集約。WBS・⑥はフロントマター走査で自動生成（手編集禁止）
- ハッシュ連鎖 ①→②→③ で改訂の伝搬を機械検知（不一致＝要再生成）。
  変数辞書がある場合は dict-hash（辞書→①）が同じ役割をもう1段担う
- **語義と例外の扱いは①より先に人が確定させる**。変数辞書（1変数=1語義。既定では
  未承認の語義が残る関数の①を dict-gate が止める）と例外ポリシー（0割等の扱いを
  EP-xxx として登録。未決定のまま仕様化すると機械レビューがNG）
- ③は「②＋規約」のみ、④は「①＋規約」のみを入力（クリーンルーム。レガシー原文は読まない）
- ④のスタブ量産は禁止（check_stubs.py が検出し未完了扱い）。詰まったら spec-gap ISSUE →
  レガシーを読めるのは①改訂エージェントのみ→仕様更新→ハッシュ伝搬→④再開
- ④⑤中の tests/ 編集は hook で拒否。テスト側の疑義は ISSUE→人承認→②③再生成
- 仕様の各項目に Confidence（🟢🟡🔴）とレガシー行番号の根拠を必須付与（cc-rsg 流儀）
- ISSUE は全体通し番号・「仮説＋Yes/Noの問い」形式。人の回答は domain-knowledge.md に蓄積して再質問を防ぐ
  （domain-knowledge.md / conventions.md / exception-policy.md / docs/templates/ は
  **人だけが書くファイル**。AI は提案文の提示まで）
- **HTML サイトは閲覧専用**。実行は CLI（pipeline.py）とチャット、承認・裁定は
  チャット / ファイル記入 / CLI（review_actions.py）の3チャネル（すべて同格）
- **固変分離**: ワークフローは skill 共有（固定）、仕様書の項目立て・書き方は
  対象プロジェクトの docs/templates/（可変・人が著者）
- ⑦（legacy-7-analyze）: profile_run.py で計測→ NumPy/アルゴリズム/Rust(PyO3)化をエージェントが基準に沿って
  判断・提案→人の承認→**挙動保存で適用**（③テスト全pass維持が絶対条件。挙動変更は ISSUE→①②経由）。
  保守性は radon/ruff、セキュリティは bandit/pip-audit。中心文書は docs/analysis.md（OPT-/REF-/SEC- 施策台帳）

HTML は `scripts/render_site.py`（Mermaid を効かせるため .qmd 影コピーを経由する。
`quarto render docs` の直叩きは不可）、PDF は `scripts/pdf_book.py`＋quarto-typst-pdf skill。
④の詳細仕様は docstring→Sphinx。
閲覧は `scripts/serve_site.py`（127.0.0.1 のみ・プロジェクトごとに固定ポート・`--watch` で自動再レンダリング）、
レビュアーへの配布は `scripts/build_viewer.py`（サイト同梱の単体実行ファイル。相手に Python も Quarto も不要）。

`MANUAL.md` を直したら `MANUAL.html` と `MANUAL.pdf` も作り直す
（日本語フォントと絵文字フォントが入った環境で実行すること）:

```bash
python <skillsリポジトリ>/docs/legacy-reverse/make_manual.py          # HTML + PDF
python <skillsリポジトリ>/docs/legacy-reverse/make_manual.py --html   # HTML だけ（速い）
```

`MANUAL.html` は画像・CSS・JS をすべて埋め込んだ**単一ファイル**なので、
そのまま渡せば相手はダブルクリックで開ける（サーバ・ネットワーク不要）。
設計・仕様書の側は `docs/legacy-reverse/make_pdf.py` と
`quarto render docs/legacy-reverse` で再生成する。
