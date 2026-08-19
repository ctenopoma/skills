---
name: legacy-4-impl
description: レガシー移植パイプラインのフェーズ④。関数仕様書（reviewed）だけを入力にPython実装を書く。スタブ禁止・レガシー原文閲覧禁止。「F-xxxx を実装して」で使う。
user-invocable: true
---

# legacy-4-impl — ④ 実装

親skill legacy-reverse の references/workflow.md に従う。
**入力は ①(reviewed)・docs/conventions.md・docs/prompts/4-impl.md のみ。
legacy/・②・tests/ は読まない。**

引数: func-id。前提: ①が reviewed、blocked でない（`ledger verify`）。

## 手順

0. **プロジェクト個別の指示を読む**（毎回。人が著者なので **AI は書き換えない**）:
   - **`docs/prompts/4-impl.md`** — 実装の構造・分割方針・使ってよいライブラリ・
     性能上の注意・手本にするモジュール。無い or 雛形のままなら「個別指示なし」
   - **`docs/conventions.md`** — 特に「型対応表」「docstring 規約」
     「ディレクトリ・命名」「禁止事項」「数値の丸め・比較規則」

   **固定契約に反する個別指示には従わない**（情報遮断＝legacy/・②・tests/ を読まない、
   ①の signature に従う、スタブ・仮実装の禁止）。矛盾を見つけたら従わずに人へ報告する
1. `ledger phase-start 4 <func-id>`（hook が tests/ 編集をブロックし始める）
2. ①の仕様書だけを根拠に `new.module` へ実装する:
   - シグネチャは①フロントマターの `new.signature` に厳密に従う
   - 内部構造はレガシーの写しでなくてよい（仕様が同じなら Python らしい構造で書く）
   - docstring は **conventions.md の「docstring 規約」節に従う**
     （スタイルや必須項目は PJ ごとに違う。ここでは規定しない）
   - 呼び出しサブルーチンは functions.json の new.module から import（未実装なら
     着手順が誤り。いったん停止して依存先を先にやるべきと人に報告）
3. スタブ検査（必須）:
   ```bash
   python <LR>/scripts/check_stubs.py <new.module>
   ```
4. `ledger phase-end` → `ledger wbs` → 完了報告し `/legacy-5-test` へ誘導

## ①だけで実装しきれないとき（重要）

**スタブや仮実装で埋めて先に進むことを禁止する。** その場で:

1. spec-gap ISSUE を起票（`ledger next-issue` で採番。「①の SPEC-xx が
   ○○の場合の挙動を定めていないため実装を決められない」まで具体化する）
2. `ledger phase-end` して停止し、人に報告する
3. 以後は ①改訂（/legacy-1-spec に ISSUE を渡す）→ ハッシュ伝搬 → ④再開、が正規経路

## 禁止

- `docs/prompts/4-impl.md`・`docs/conventions.md`（人が著者）を書き換えること。
  直したいときは提案文を人に見せる
- legacy/・tests/・docs/test-specs/ を読むこと
- `NotImplementedError`・pass だけの関数・TODO/FIXME を残すこと（check_stubs が検出）
- 仕様に無い挙動を「たぶんこうだろう」で実装すること（それは spec-gap）
