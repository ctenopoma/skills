---
name: legacy-1-spec
description: レガシー移植パイプラインのフェーズ①。レガシー原文を読んで関数仕様書（機能詳細＋Confidence＋根拠）を書き上げる。spec-gap ISSUE を受けた仕様改訂もこのskill。「F-xxxx の仕様書を書いて」「仕様書を改訂して」で使う。
user-invocable: true
---

# legacy-1-spec — ① 関数仕様書作成

親skill legacy-reverse の references/workflow.md に従う。
**このskill（と⓪）だけがレガシー原文を読める。** tests/ と src/ は読まない。

引数: func-id（省略時は `ledger next` の提案に従う）。`ISSUE-xxx` を渡されたら改訂モード。

## 新規作成の手順

1. `docs/specs/<func-id>.md`（骨子, status: skeleton）と functions.json、
   `docs/domain-knowledge.md`、レガシー原文の該当範囲を読む
2. 骨子の空欄を充填する:
   - **概要**・**機能詳細**（`SPEC-<num>-NN` 見出し。条件→結果の形。
     各項目に **Confidence（🟢確認済/🟡推測/🔴仮定）と根拠 `file:lines`** を必須で付ける）
   - **数値計算・アルゴリズムは「④が仕様書だけで同じ結果を再現できる」精度で書く**：
     漸化式・端条件・丸め規則は言葉で要約せず式まで書く。要約した箇所は④で
     spec-gap ISSUE になって返ってくる（実例: FMM spline の端条件はレガシーの
     `c(1)=0` が「解が0」ではなく「方程式の右辺が0＝s‴=0」の意味だった）
   - **副作用・例外**（なければ「なし」と明記）
   - IO表の Confidence を実際に確認して更新（⓪の機械抽出が間違っていたら
     functions.json も直して `ledger skeletons --force` ではなく該当箇所のみ手修正）
   - cc-rsg が導入済みならその調査フェーズを流用してよい。出力は必ずこのテンプレ形式に合わせる
3. 🟡🔴 のうち、テストの期待値に影響しそうなものは ISSUE 起票
   （`ledger next-issue` で採番、**仮説＋Yes/Noの問い**の形式）
4. status を draft にし、**人へレビュー依頼**: 変更点サマリ＋🟡🔴一覧＋open ISSUE を提示
5. 人のOKが出たら status: reviewed に更新 → `ledger wbs`

## 改訂モード（spec-gap ISSUE 対応）

1. ISSUE の「何が決められないか」を読み、レガシー該当箇所を調査
2. 仕様書を根拠付きで更新（Confidence も更新）。ISSUE に反映内容を記録して applied に
3. 更新後 `ledger verify <func-id>` を実行し、②が stale になったことを確認して人に報告
   （テストに影響する変更なら ②の再生成→再承認が必要になる、と明示する）
4. `ledger wbs`

## 禁止

- 推測を🟢と書くこと。根拠行を示せないものは🟢にできない
- 人のOKなしで reviewed にすること
- tests/・src/ を読むこと
