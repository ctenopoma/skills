---
title: "例外ポリシー登録簿"
date: last-modified
---

<!--
docs/exception-policy.md（対象プロジェクト側にコピーして使う）。
機械が検知した hazard（0割・SQRT/LOG の定義域・変数添字…）を「どう扱うか」の決定を
人が承認して積む台帳。hazards.py match がこの表と data/functions.json の hazards を
突合し、未決定を docs/exception-queue.md に質問として出す。

- 行の追記は `python hazards.py add-policy ...` で行う（EP-ID の自動採番・承認者/日付記録）。
  手で書いてもよいが、列の並びは変えないこと（素朴なパースで読んでいる）
- 「対象」列に書けるトークン: kind（div_by_var 等）/ 関数 F-0012 / 個別 H-0012-01。
  組み合わせ可（例: `div_by_var F-0012 H-0012-01`）
- 「適用範囲」は 全体既定 → 関数 → 個別 の順に個別が勝つ（同じ範囲なら下の行が勝つ）。
  実際の判定は「対象」列のトークンから決まるので、この列は人が読むためのラベル
- Fortran は 0割でも Inf を作って走り続けるが Python は停止する。
  **既定を決めずに①→④へ進むことはできない**（review_checks.py spec がNGにする）

決定の語彙（これ以外を書くとパース時にNG）:

| 決定 | 意味 |
|------|------|
| `detect_only` | 仕様に記載するだけ（コードでは検知せず、挙動もレガシーのまま） |
| `guard_raise` | ガードして例外送出（Python の例外として仕様化する） |
| `guard_value` | 代替値で継続（**どの値にするかを備考に必ず書く**） |
| `legacy_preserve` | レガシー挙動の再現（IEEE の Inf/NaN 等をそのまま流す） |
| `caller_guarantees` | 呼び出し元が保証（**根拠を備考に必ず書く**: SPEC-xxxx-nn や上流のチェック箇所） |

記入例（この2行はコメント内なのでパース対象外。実際の行は下の表に入れる）:

| EP-001 | div_by_var | 全体既定 | guard_raise | 0割は ZeroDivisionError を仕様化する | 山田 2026-08-08 |
| EP-002 | div_by_var F-0012 H-0012-01 | 個別 | caller_guarantees | Y は上流で >0 保証（SPEC-0011-03） | 山田 2026-08-08 |
-->

# 決定済みポリシー

| EP-ID | 対象 | 適用範囲 | 決定 | 備考 | 承認 |
|-------|------|---------|------|------|------|

# 運用メモ

- 未決定の一覧は [例外ポリシー 未決定キュー](exception-queue.md)（`hazards.py match` が生成）
- 決定を1つ登録すると同じ kind の全箇所に即座に効く。個別に変えたい箇所だけ
  `--hazard H-xxxx-nn` を付けて追加登録する（個別が全体既定に勝つ）
- ①仕様書の「例外・数値特異点」節は、ここに実在する EP-ID しか引用できない
  （捏造は review_checks.py spec がNGにする）
