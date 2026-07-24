---
name: legacy-7-analyze
description: レガシー移植パイプラインのフェーズ⑦。性能計測→高速化（NumPy/アルゴリズム/Rust化の判断）、静的解析による保守性リファクタリング、セキュリティ強化を、テスト資産を安全網に挙動保存で行う。「性能分析して」「高速化して」「⑦をやって」で使う。
user-invocable: true
---

# legacy-7-analyze — ⑦ 分析・改善

親skill legacy-reverse の references/workflow.md に従う。前提: ⑥が pass していること。

**大原則: ⑦のすべての変更は挙動保存。** ③のテストスイート（freeze済み・全pass）が安全網であり、
どの施策を適用しても `pytest tests` が全passのまま。挙動を変えたくなったら
ISSUE→①②経由（このパイプラインの通常ルート）に戻す。tests/ の編集は引き続き禁止。

中心文書は `docs/analysis.md`（テンプレ: assets/templates/analysis.md）。
施策は OPT-/REF-/SEC- のID付きで提案し、**人の承認（approved）を得たものだけ適用**する。

## 手順

### 1. 計測

```bash
python <LR>/scripts/profile_run.py --root .                    # スモーク（テスト負荷）
python <LR>/scripts/profile_run.py --root . --script bench.py  # 代表ワークロード（本命）
```

- **代表ワークロードの有無をまず人に確認する**（実データ規模の入力・繰り返し回数）。
  無ければ一緒に `bench.py` を作る。単体テスト負荷だけでホットスポットを断定しない
- 出力: `docs/perf.md`（レポート）と `.legacy-reverse/perf.json`（before/after比較用）

### 2. 静的解析・セキュリティ走査

```bash
python -m pip install radon ruff bandit pip-audit   # 未導入なら
python -m radon cc src -s -a     # 循環的複雑度（C以上を列挙）
python -m radon mi src -s        # 保守性指標（B以下を列挙）
python -m ruff check src
python -m bandit -r src -q
python -m pip_audit
```

### 3. 施策提案（docs/analysis.md に記入）

- **性能(OPT-)**: ホットスポットごとに NumPy化 / アルゴリズム / キャッシュ / rust-pyo3 を検討。
  **Rust化の判断基準**（この順で検討し、根拠を提案欄に明記する）:
  1. 全体比が大きいCPUバウンドか（I/Oバウンドは対象外）
  2. NumPy等のベクトル化・アルゴリズム改善で足りないか（足りるならそちらが先）
  3. 純粋関数か（グローバル状態・ファイルI/Oを跨ぐなら計算部を切り出してから）
  4. 満たして初めて提案。maturin/PyO3 のビルド環境という保守コストを必ずリスク欄に書く
- **保守性(REF-)**: 複雑度C以上・MI低スコアのモジュールに分割・命名・重複排除を提案
- **セキュリティ(SEC-)**: bandit/pip-audit の検出＋レガシー移植特有の観点
  （外部ファイル入力のパス検証・サイズ上限・不正フォーマット時の挙動、エラーのフラグ渡しの見直し）
- 各施策に期待効果・リスクを書き、**優先順位を付けて人に提示**する

### 4. 承認 → 適用 → 検証（施策ごとに小さく回す）

1. 人が approved にした施策だけ着手。1施策 = 1コミット
2. 適用後、必ず: `pytest tests` 全pass維持 → `profile_run.py` 再計測 → 実施ログに before/after 記録
3. docstring・①仕様書に影響する変更（例: 関数分割）をしたら該当文書も更新し、
   `ledger sphinx-index`＋Sphinx再ビルド
4. 適用の途中で挙動差が出た（テストが落ちた）ら **その施策を巻き戻す**。
   「テストの方が細かすぎる」と感じてもテストは触らない（ISSUEで裁定）

### 5. 仕上げ

- analysis.md の status を done に、WBSナビバーの「⑦分析」リンクから読めることを確認
- `ledger wbs` → `quarto render docs` → Sphinx（workflow.md の順序）

## 禁止

- 人の承認前に施策を適用すること／複数施策をまとめて適用すること
- tests/ の編集（⑦でも不可侵）
- 計測せずに「たぶん速くなる」で最適化すること（before/afterが書けない施策は却下）
