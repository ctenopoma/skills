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

手順1と2は `python <LR>/scripts/quant_analyze.py --root .` で一括実行でき、
`.legacy-reverse/quant.json`（機械可読）と `docs/quant.md`（サマリページ）に集約される
（WBSの「⑦分析を実行する」ボタンが使うのと同じもの。bench.py があれば自動で本命計測）。
既に quant.json がある場合は再計測せず、その実測値を候補の根拠に使ってよい。

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

### 3. 候補の洗い出し（docs/analysis.md に記入）

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

### 4. 項目確定（候補 → 施策票への昇格）

着手する候補を1つ選び、テンプレ `assets/templates/improvement.md` から
**施策票 `docs/improvements/<ID>.md` を起票**する。票には必ず:

- **目的**（なぜやるか・改善後の姿）
- **成功基準**（後から機械判定できる指標＋baseline実測値＋目標値。
  「⑤テスト全pass維持」は全施策共通の基準として必ず入れる）
- **改善仕様**（何をどう変えるか・挙動保存の根拠）

を書き、**人の承認（status: approved）を得る**。analysis.md は候補台帳として残し、
昇格した候補には票へのリンクを張る。`ledger wbs` で WBS「⑦改善イタレーション」表に載る。

### 5. イタレーション（1施策 = 1周の DevOps ループ）

approved の施策票1枚に対して:

1. **Do**: 適用する。**1施策 = 1コミット**（票の `commit:` にハッシュを記録）
2. **Check**: `pytest tests` 全pass確認 → `profile_run.py` 再計測・静的解析再走査 →
   票の成功基準表に「実測(after)」「判定」を記入
3. **Act**:
   - 全基準達成 → `achieved: true` / `status: verified`
   - 未達 → コミットを revert して `rolled-back`、または基準・仕様を見直して再イタレーション。
     学びは票の「振り返り」に残す。ドメイン知識として残すべきものは
     転記文を提案し、人が domain-knowledge.md に貼る（AI は書き込まない）
   - テストが落ちた＝挙動が変わった → **無条件で巻き戻し**。「テストが細かすぎる」と
     感じてもテストは触らない（ISSUEで裁定）
4. docstring・①仕様書に影響する変更（例: 関数分割）をしたら該当文書も更新し、
   `ledger sphinx-index`＋Sphinx再ビルド
5. 締め: `ledger wbs` → `render_site.py` → Sphinx。**次の施策票へ**（一度に1票ずつ）

これで「項目確定 → WBS → 目的 → 適用 → 検証 → 達成判定」が票1枚の上で完結し、
WBSからすべての施策の達成状況（✅/❌/—）がトレースできる。

## 禁止

- 人の承認前に施策を適用すること／複数施策をまとめて適用すること
- tests/ の編集（⑦でも不可侵）
- 計測せずに「たぶん速くなる」で最適化すること（before/afterが書けない施策は却下）
