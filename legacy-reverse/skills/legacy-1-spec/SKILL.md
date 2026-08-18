---
name: legacy-1-spec
description: レガシー移植パイプラインのフェーズ①。レガシー原文を読んで関数仕様書（機能詳細＋Confidence＋根拠）を書き上げる。spec-gap ISSUE を受けた仕様改訂、複数関数のバッチ実行（まとめてdraft化→一斉レビュー）もこのskill。「F-xxxx の仕様書を書いて」「仕様書をまとめて10件進めて」「仕様書を改訂して」で使う。
user-invocable: true
---

# legacy-1-spec — ① 関数仕様書作成

親skill legacy-reverse の references/workflow.md に従う。
**このskill（と⓪）だけがレガシー原文を読める。** tests/ と src/ は読まない。

引数とモード:

| 引数 | モード |
|------|--------|
| func-id 1つ | 単発（下記手順を最後まで。レビュー依頼もその場で） |
| 「まとめて」「N件」「全部」等 | **バッチ**（複数関数を draft まで連続処理 → 一斉レビュー） |
| `ISSUE-xxx` | 改訂 |
| 既に draft/reviewed の func-id | **再実行（書き直し）** |

func-id 省略時は `ledger next` の提案に従う。

## 新規作成の手順

0. **起動時スキャン**: `docs/review-feedback.md` に対象func-idの「状態: pending」が
   ないか確認（人が修正依頼を記入している場合がある。直接記入でも
   `review_actions.py request-changes` 経由でも同じ形式）。
   あれば内容を反映してから「状態: applied」に書き換える
1. `docs/specs/<func-id>.md`（骨子, status: skeleton）と functions.json、
   `docs/domain-knowledge.md`、レガシー原文の該当範囲を読む。
   **項目立てと書き方の正はプロジェクトの `docs/templates/spec.md`（人が著者）**——
   骨子の節構成と記入ガイドコメントはそこから来ている。ガイドに従って充填し、
   充填し終えたガイドコメントは消す（残すと機械レビューが「省略」として NG にする）
2. 骨子の空欄を充填する:
   - **概要**・**機能詳細**（`SPEC-<num>-NN` 見出し。条件→結果の形。
     各項目に **Confidence（🟢確認済/🟡推測/🔴仮定）と根拠 `file:lines`** を必須で付ける）
   - **数値計算・アルゴリズムは「④が仕様書だけで同じ結果を再現できる」精度で書く**：
     漸化式・端条件・丸め規則は言葉で要約せず式まで書く。要約した箇所は④で
     spec-gap ISSUE になって返ってくる（実例: FMM spline の端条件はレガシーの
     `c(1)=0` が「解が0」ではなく「方程式の右辺が0＝s‴=0」の意味だった）
   - **処理フロー**（分岐が3本以上ある関数のみ）: GitHub 流の ```mermaid で flowchart を書き、
     ノードに `SPEC-<num>-NN` を添えて機能詳細と対応づける。図で本文を省略しない
     （図は読む順の地図であって、④が再現する根拠は機能詳細の式のほう）。
     ```{mermaid} と書くとサイト全体の render が落ちるので使わない。
     ラベルに丸括弧等の記号を含めるときは A["IARG(1)=0?"] のように必ず "…" で囲む
     （囲まないと Syntax error で図が出ない。機械レビューがNGにする）
   - **副作用・例外**（なければ「なし」と明記）
   - **例外・数値特異点**（`# 副作用・例外` 直下の節。下記「記入義務」）
   - IO表の Confidence を実際に確認して更新（⓪の機械抽出が間違っていたら
     functions.json も直して `ledger skeletons --force` ではなく該当箇所のみ手修正）。
     **desc にある `[V-xxxx]` は変数辞書からの機械転記なので書き換えない**
     （辞書が正。意味が違うと思ったら人に報告し、辞書側を `variables.py revise` で直す）
   - cc-rsg が導入済みならその調査フェーズを流用してよい。出力は必ずこのテンプレ形式に合わせる
3. 🟡🔴 のうち、テストの期待値に影響しそうなものは ISSUE 起票
   （`ledger next-issue` で採番、**仮説＋Yes/Noの問い**の形式）
4. **機械レビュー（必須ゲート）**: status を draft にしたら
   ```bash
   python <LR>/scripts/review_checks.py spec <func-id> --root .
   ```
   （MCP なら `review_spec`）を実行し、**NG ゼロにしてから**人に出す。
   検知対象: 根拠 `file:lines` の実在（存在しない行の引用＝ハルシネーション）、
   🟢なのに根拠なし、プレースホルダ残存（＝記入の省略）、必須節欠落、原本ハッシュ不一致、
   hazard の検討漏れ・EP-ID の捏造・ポリシー未決定のまま仕様化
5. **人へレビュー依頼**（単発モードのみ）: 変更点サマリ＋🟡🔴一覧＋open ISSUE を提示
   （人は CLI `review_actions.py approve/request-changes` で直接返してもよい。
   その場合は反映まで CLI 側で完結する）
6. 人のOKが出たら `review_actions.py approve spec <func-id> --by <名前>` 相当で
   status: reviewed に反映（機械レビューの再検証込み）→ `ledger wbs`
   （CLI で承認済みの場合この更新は不要）

## 「例外・数値特異点」節の記入義務

Fortran は 0割でも Inf を作って走り続けるが、**Python は例外で停止する**。
検討漏れがそのまま④の事故になるため、この節は機械レビューで突合される。

- `data/functions.json` のその関数の `hazards` を **1件も落とさず**表に書く
  （hz_id・種別・箇所・適用EP・仕様記述）。**1件も無い関数は「該当なし」と1行書く**
  （空欄は「検討を省略した」と区別できないため）
- 「適用EP」は `docs/exception-policy.md` に**実在する EP-ID だけ**。捏造は NG
- **未決定の hazard は仕様化できない**。書く前に止まって人に決めてもらう:
  ```bash
  python <LR>/scripts/hazards.py match --root .   # → docs/exception-queue.md
  ```
  キューを人に見せ、決定（`detect_only` / `guard_raise` / `guard_value` /
  `legacy_preserve` / `caller_guarantees`）を聞き、**人が**
  `hazards.py add-policy --kind <k> --decision <語彙> --by <名前>` を実行して
  登録してから書く（exception-policy.md は人だけが書くファイル。AI はコマンド例の
  提示まで）。**AIが勝手に決めない**
- 「仕様記述」は決定を新実装の言葉にしたもの。`guard_raise` なら送出する例外と条件、
  `guard_value` なら代替値、`caller_guarantees` なら保証の根拠（SPEC-ID・上流の関数）
- ②はこの節から境界ケース（0・0近傍・負値・添字の下限上限）を導出する

## フロントマターの扱い

`dict-hash` と `flows` は `ledger skeletons` が骨子生成時に書く機械管理のキー。
**①は値を書き換えず、行ごとそのまま残す**（テンプレから書き直すときも同じ）。

- `dict-hash` を書き換えると辞書の改訂検知（WBS の「⚠辞書stale」）が壊れる
- `flows` は所属フローの文脈情報。機械判定には使わない

## バッチモード（複数関数を連続処理 → 一斉レビュー。全件・2000件規模対応）

「まとめて進めて」「次の10件」「全件」等で起動。**執筆は機械的に連続、承認だけ後で
まとめて人**、という分業。1件ごとに人を待たない。進捗の正は status（ファイル）に
あるので、**どこで止まっても同じ手順で再開できる**。

> **全件（数百件〜）を頼まれたら**: 会話内ループはコンテキスト上限があるため、
> `scripts/pipeline.py`（無人ドライバ。1関数=1 headless プロセスでトークン制約なし）
> を人に案内すること。会話内バッチは「数十件を対話しながら」の用途に向く。

1. **再開チェック（毎回最初に）**: `spec_review_report`（`review_checks.py report`）を実行し、
   `machine_ng_funcs` が空でなければ**先にそれらを書き直す**
   （前回中断が draft 書きかけで起きた場合、ここで機械的に検出される）。
   `docs/review-feedback.md` に「状態: pending」があれば同様に先に反映する
2. 対象を機械的に選定:
   ```bash
   ledger next --all --phase 1 --skip-draft --limit <チャンク数>
   ```
   `--skip-draft` が **draft（レビュー待ち）を除外**するので、再開しても二重に
   書き直さない。blocked も自動除外
3. 各関数に「新規作成の手順」1〜4 を適用する。**手順4の機械レビューは1件ごとに
   NG ゼロまで自己修正**してから次の関数へ。status は draft のまま置く
   （個別のレビュー依頼はしない。ISSUE 起票は通常どおり行う）
4. **チャンクの区切りごと**（10〜20件目安）に `ledger wbs` → report を再生成。
   全件モードは「手順2が空を返すまで手順2〜4を繰り返す」。
   コンテキストが長くなっても、状態はファイルにあるので続行・再開に影響しない
5. 対象が尽きたら（または人に見せる区切りで）:
   ```bash
   python <LR>/scripts/review_checks.py report --root .   # docs/spec-review.md を生成
   ledger wbs
   python <LR>/scripts/render_site.py --root .
   ```
   **人へ一斉レビュー依頼**: 件数・🟢🟡🔴の合計・起票した ISSUE 数を報告し、
   「① 仕様書 一斉レビュー」表（閲覧サイト。WBS の要対応からもリンクされる）へ誘導する。
   返答はチャット（「全部OK」「F-xxxx は修正: 〜」）・review-feedback.md 記入・CLI のどれでもよい。
   なお全件を待たず、**溜まった draft を人が随時レビューして reviewed 化してよい**
   （レビュー済みは次の report から自動で消える）
6. 人の回答を反映:
   - 「全部OK」→ 全件 reviewed 化
   - 「F-xxxx は修正: 〜」→ その関数だけ再実行（書き直し）して再提示、他は reviewed 化
   - 反映後 report 再生成 → `ledger wbs` → render

## 再実行（書き直し）

同じ func-id で再度呼ばれたら書き直しモード。気に入らない仕様書は何度でも作り直せる。

- **draft の書き直し**: 自由に上書きしてよい。人から修正指示があればそれを反映し、
  なければ全面的に書き直す。既存の ISSUE 参照は維持し、内容が変わって不要になった
  ISSUE は人に確認して closed に
- **reviewed の書き直し**: ハッシュ連鎖で②が stale になり再承認が必要になる。
  **その旨を伝えて人の了承を得てから**書き直す（了承なしで reviewed を上書きしない）
- どちらも書き直し後は必ず機械レビュー（手順4）→ 単発なら手順5-6、バッチ中なら次へ

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
- 「例外・数値特異点」節で hazard を省略すること、実在しない EP-ID を書くこと、
  未決定のポリシーを自分で決めて仕様化すること
- IO 表の `[V-xxxx]`（辞書からの転記）を書き換えること
- フロントマターの `dict-hash` / `flows` を書き換え・削除すること
