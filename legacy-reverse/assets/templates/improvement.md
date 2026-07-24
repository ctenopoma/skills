---
title: "{{改善の要約を一行で}}"
item-id: "{{OPT|REF|SEC}}-{{3桁連番}}"
kind: refactor          # opt(性能) / refactor(保守性) / security
func-id: "{{関連func-id。複数可}}"
status: proposed        # proposed → approved → applied → verified ／ rejected ／ rolled-back
achieved: null          # true / false。verified 時に成功基準の判定で確定する
approved-by: null
approved-date: null
commit: null            # 適用コミットのハッシュ（1施策=1コミット）
---

<!--
⑦の施策票（1施策=1ファイル=1イタレーション）。ledger.py wbs が本フロントマターを
走査して WBS「⑦改善イタレーション」表に載せる。
ライフサイクル: Plan(起票→人の承認) → Do(適用) → Check(検証記録) → Act(達成判定/差し戻し)
-->

# 目的（Plan）

<!-- なぜやるか。何が問題で、改善後にどうなっていてほしいか。1〜3行 -->

# 成功基準（検証可能な形で定義する）

<!--
「目的が達成されたか」を後から機械的に判定できる形で書く。
baseline は起票時点の実測値（perf.json / radon / bandit の出力から転記）。
挙動保存（⑤テスト全pass）は全施策共通の基準として必ず1行入れる。
-->

| # | 指標 | baseline | 目標 | 実測(after) | 判定 |
|---|------|---------|------|------------|:---:|
| 1 | （例: radon CC get_rate） | B(8) | A(4)以下 | | |
| 2 | ⑤テスト | 全pass | 全pass維持 | | |

# 改善仕様（Do の設計）

<!-- 何をどう変えるか。挙動保存の根拠（外部から見た入出力・副作用が変わらない理由）を必ず書く -->

# 検証記録（Check）

<!-- 適用コミット・再計測/再走査の実行ログ・成功基準表の「実測(after)」「判定」を埋める -->

# 振り返り（Act）

<!--
- 達成 → achieved: true / status: verified に更新
- 未達 → 差し戻し: コミットをrevertして rolled-back、または基準・仕様を見直して再イタレーション。
  学びを domain-knowledge.md か次の施策票に残す
-->
