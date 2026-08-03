# legacy-reverse

レガシーコード（Fortran / C# 等）を Python へ仕様ベースで移植するリバースエンジニアリング・パイプライン。

パイプライン: ⓪リポジトリ解析 → ①仕様書 → ②テスト仕様 → ③テストコード → ④実装 → ⑤テスト（fail→④、裁定は人）→ ⑥完了検証 → ⑦分析・改善（挙動保存）

## ドキュメント

| 文書 | 対象読者 |
|------|---------|
| [slides/index.html](slides/index.html) | 初見の操作者（⓪→⑦を進めながら覚えるチュートリアル） |
| [QUICKREF.md](QUICKREF.md) | 作業中の操作者（コマンド即引き1枚） |
| [MANUAL.md](MANUAL.md) / MANUAL.pdf | 操作者（背景と操作の意味・トラブル対処） |
| [DESIGN.md](DESIGN.md) | skill の開発者・保守者（構造と設計判断） |
| [references/workflow.md](references/workflow.md) / [schema.md](references/schema.md) | フェーズskill・スクリプト（規則とデータの正） |

## 構成

```
legacy-reverse/
  SKILL.md                 # 全体管理（状況表示・次アクション提案・セットアップ）
  skills/
    legacy-0-analyze/      # ⓪ 解析 → functions.json・骨子・WBS・規約
    legacy-1-spec/         # ① 仕様書（レガシーを読める唯一の役割。spec-gap改訂も担当）
    legacy-2-testspec/     # ② テスト仕様（①のみ入力。人の承認ゲート）
    legacy-3-testcode/     # ③ テストコード（②のみ入力。freeze でハッシュ固定）
    legacy-4-impl/         # ④ 実装（①のみ入力。スタブ禁止）
    legacy-5-test/         # ⑤ 実行・結果収集・トリアージ・ループ管理
    legacy-6-check/        # ⑥ 完了検証と最終レンダリング
    legacy-7-analyze/      # ⑦ 分析・改善（挙動保存）
  scripts/
    extract_fortran.py     # ⓪ Fortran機械抽出 → functions.json 生成/マージ（再実行=マージで再開安全）
    ledger.py              # 台帳: WBS生成/骨子生成/ハッシュ連鎖/blocked管理/⑥検証
    review_checks.py       # ①②の機械レビュー（引用実在・省略・トレーサビリティのハルシネーション検知）
    tc_report_plugin.py    # pytest プラグイン（TCマーカー別の結果収集）
    collect_results.py     # ②と突合して結果報告書を自動生成（実装率・attempt・自動block）
    check_stubs.py         # ④のスタブ検出（空実装/NotImplementedError/TODO）
  hooks/
    guard_tests.py         # PreToolUse: ④⑤中の tests/ 編集を拒否
    settings-example.json  # 対象プロジェクトへの hook 登録例
  references/
    schema.md              # プロジェクト構成・functions.json/ledger.json スキーマ・status遷移
    workflow.md            # 共通規則（情報遮断・ハッシュ連鎖・ISSUE・承認・ループ）
  assets/templates/        # 各成果物のテンプレート（フロントマターが台帳の正データ)
  examples/                # 架空の COBOL 関数 CALC-TAX (F-0123) の記入例一式
```

## 導入（対象プロジェクト側）

1. このリポジトリの `legacy-reverse/` を対象プロジェクトの `.claude/skills/legacy-reverse` に配置
2. `legacy-reverse/skills/legacy-*` を `.claude/skills/` 直下にもコピー（skill発見のため）
3. `hooks/settings-example.json` を対象プロジェクトの `.claude/settings.json` にマージ
4. `/legacy-reverse` を実行してセットアップ確認 → `/legacy-0-analyze` から開始

## 確定事項

- レガシー言語: Fortran / C# など（⓪の解析は言語ごとに都度対応）。新言語: Python＋pytest（将来 JS/Rust 拡張）
- レガシー実行環境なしが基本前提。②の期待値は 仕様🟢＋人間確認 で確定（実測は環境がある場合のみ）
- ⓪〜⑤のトリガは人。フェーズごとに skill を分ける
- ⑤結果報告書は `{func-id}_{YYYYMMDD-HHMMSS}.md` で毎回1から自動生成（履歴はファイルとして蓄積）
- ⑤の pass 条件は「実装率100%（②の全ケースが③に実装済み）かつ失敗0」
- PDF: 仕様書・テスト仕様書・テスト結果を種別ごとに個別出力（同一Markdownソースから Quarto で HTML も生成し WBS から導線）。④は Sphinx で HTML のみ
- ④⑤ループ上限は3回。到達で triage ISSUE 自動起票→人の裁定→`ledger unblock`→人が再トリガ、attempt リセット

## 原則

- 機械可読メタデータは YAML フロントマターに集約。WBS・⑥はフロントマター走査で自動生成（手編集禁止）
- ハッシュ連鎖 ①→②→③ で改訂の伝搬を機械検知（不一致＝要再生成）
- ③は「②＋規約」のみ、④は「①＋規約」のみを入力（クリーンルーム。レガシー原文は読まない）
- ④のスタブ量産は禁止（check_stubs.py が検出し未完了扱い）。詰まったら spec-gap ISSUE →
  レガシーを読めるのは①改訂エージェントのみ→仕様更新→ハッシュ伝搬→④再開
- ④⑤中の tests/ 編集は hook で拒否。テスト側の疑義は ISSUE→人承認→②③再生成
- 仕様の各項目に Confidence（🟢🟡🔴）とレガシー行番号の根拠を必須付与（cc-rsg 流儀）
- ISSUE は全体通し番号・「仮説＋Yes/Noの問い」形式。人の回答は domain-knowledge.md に蓄積して再質問を防ぐ
- ⑦（legacy-7-analyze）: profile_run.py で計測→ NumPy/アルゴリズム/Rust(PyO3)化をエージェントが基準に沿って
  判断・提案→人の承認→**挙動保存で適用**（③テスト全pass維持が絶対条件。挙動変更は ISSUE→①②経由）。
  保守性は radon/ruff、セキュリティは bandit/pip-audit。中心文書は docs/analysis.md（OPT-/REF-/SEC- 施策台帳）

HTML は `scripts/render_site.py`（Mermaid を効かせるため .qmd 影コピーを経由する。
`quarto render docs` の直叩きは不可）、PDF は `scripts/pdf_book.py`＋quarto-typst-pdf skill。
④の詳細仕様は docstring→Sphinx。
