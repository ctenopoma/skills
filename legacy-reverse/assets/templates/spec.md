---
title: "関数仕様書: {{new_name}}"
func-id: "{{func_id}}"
status: skeleton        # skeleton(0で生成) → draft(①でLLM充填) → reviewed(人が確認)
dict-hash: "{{dict_hash}}"  # 変数辞書の語義ハッシュ。ledger skeletons が刻む。①は書き換えずそのまま残す
                        # （書き換えると辞書改訂の検知が壊れる。辞書が無いプロジェクトではこの行ごと出ない）
reviewed-by: null       # 承認者（チャット承認 / review_actions.py approve で自動記入）
reviewed-date: null
legacy:
  file: "{{legacy_file}}"
  name: "{{legacy_name}}"
  lines: "{{legacy_lines}}"
  hash: "{{legacy_hash}}"     # レガシー該当箇所のハッシュ。原本の変更検知用
new:
  module: "{{new_module}}"
  signature: "{{new_signature}}"   # ③④の共通契約。0で型対応表に従って決定する
---

<!-- LR:TEMPLATE-NOTE
━━━ このテンプレはプロジェクトの所有物（人が編集する） ━━━━━━━━━━━━━━━━━━━━━
項目立て（節の構成・順序）と各節の書き方ガイド（HTMLコメント）は、プロジェクトに
合わせて自由に変更してよい。`ledger skeletons` がこの本文を骨子に写し、
review_checks が「# 見出し」を必須節として検証する。

ただし次の**固定契約**（機械が生成・検証するアンカー）だけは削除・改名できない:
  - 置換マーカー3つ: <LR:IO-TABLES> <LR:CALLS-TABLE> <LR:HAZARD-TABLE>（HTMLコメント形式）
  - 契約見出し: # 機能詳細 / # 副作用・例外 / ## 例外・数値特異点 / # 未確定事項
上のフロントマターは機械が生成する（ここに書いてあるのは参考表示。本文には写らない）。
プレースホルダ {{func_id}} {{func_num}} {{func_num_lower}} {{new_name}} {{legacy_file}} は
本文中でも骨子生成時に置換される。
見出し行末に <LR:OPTIONAL>（HTMLコメント形式）を付けた節は任意節（機械レビューの必須節から外れる）。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-->

# 概要

<!-- ①で充填: この関数の目的を1〜3行で -->

# 処理フロー <!-- LR:OPTIONAL -->

<!--
①で充填する任意節: 分岐が3本以上ある場合のみ図にする（表や箇条書きで足りるものは
書かない）。不要なら節ごと削除。図は GitHub 流の mermaid フェンス（行頭 ```mermaid）で
書く。波括弧付きフェンス（{mermaid} 形式）はサイト全体の render を落とすので使わない。
ラベルに丸括弧などの記号を含める場合は A["IARG(1)=0?"] のように必ず "…" で囲む
（囲まないと mermaid が Syntax error になり図が表示されない。機械レビューでNGになる）。
ノードには機能詳細の見出しID（SPEC-{{func_num}}-01 等）を添えて本文と対応づけること。
記入例は skill 同梱の examples/specs/F-0123.md を参照。
-->

# インタフェース

<!-- IO表は機械が functions.json から生成する（変数辞書の [V-xxxx] 転記込み）。
     ①は Confidence と説明を実際に確認して更新する。[V-xxxx] は書き換えない -->

<!-- LR:IO-TABLES -->

<!-- LR:CALLS-TABLE -->

# 機能詳細

<!--
①で充填。機能1つにつき見出し1つ。見出しIDは SPEC-{{func_num}}-NN 形式で固定（②が参照する）。
記述は「条件 → 結果」の形。各項目に必ず Confidence と根拠（レガシーの file:lines）を付ける。
  🟢 VERIFIED: コードから確認済み / 🟡 INFERRED: 文脈からの推測 / 🔴 ASSUMED: 仮定
数値計算・アルゴリズムは「④が仕様書だけで同じ結果を再現できる」精度で、式まで書く。

書式例（先頭の空白は外して使う。コメント内で ## を行頭に置くと機械レビューが
実項目と誤認するため字下げしてある）:

  ## SPEC-{{func_num}}-01: 機能名 {#spec-{{func_num_lower}}-01}

  挙動の記述（条件 → 結果）。

  **Confidence: 🟢** — 根拠: `{{legacy_file}}:120-135`
-->

# 副作用・例外

<!-- ①で充填。画面出力・ログ・DB・異常終了条件など。なければ「なし」と明記（空欄と区別する） -->

## 例外・数値特異点

<!--
①で充填。data/functions.json の hazards（⓪の機械検知: 0割・SQRT/LOG の定義域・変数添字…）を
**1件も落とさず**この表に書く。行が足りない・hz_id が違うと機械レビューでNGになる。

- 「適用EP」は docs/exception-policy.md に実在する EP-ID だけを書く（捏造はNG）。
  まだ決まっていない hazard は書けない ——
  `python hazards.py match --root .` → docs/exception-queue.md を人に見せて決めてもらい、
  人が `hazards.py add-policy` で登録してから①を書く（未決定のまま仕様化するとNG）
- 「仕様記述」は決定を新実装の言葉にしたもの。guard_raise なら送出する例外と条件、
  guard_value なら代替値、caller_guarantees なら保証の根拠（SPEC-ID や上流の関数）
- ②はこの節から境界ケース（0・0近傍・負値・添字の下限上限）を導出する
- **hazards が1件も無い関数は、表を空にせず「該当なし」と1行書く**（検討の省略と区別するため）
-->

<!-- LR:HAZARD-TABLE -->

# 未確定事項

| ISSUE | 内容 | 状態 |
|-------|------|------|
| | | |
