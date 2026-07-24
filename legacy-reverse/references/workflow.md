# 共通ワークフロー規則（全フェーズskillが従う）

## 情報遮断（クリーンルーム）

| フェーズ | 読んでよい入力 | 読んではいけないもの |
|---|---|---|
| ⓪ 解析 | legacy/ 全部 | — |
| ① 仕様書 | legacy/ 該当関数、functions.json、domain-knowledge.md | tests/、src/ |
| ② テスト仕様 | ①(reviewed)、conventions.md、domain-knowledge.md | **legacy/**、src/、tests/ |
| ③ テストコード | ②(approved)、conventions.md | **legacy/**、**①**、src/ |
| ④ 実装 | ①(reviewed)、conventions.md | **legacy/**、**②**、**tests/** |
| ⑤ テスト | 結果＋①②（トリアージ判断用）、src/（(a)修正時） | legacy/、tests/ の編集 |

- 「読んではいけない」に触れたくなったら、それは仕様の穴。ISSUE を起票して停止する
- レガシー原文を読める役割は ⓪ と ①（改訂含む）だけ

## ハッシュ連鎖

- ②のフロントマター `spec-hash` に生成時点の①のハッシュ、ledger.json に③freeze時のハッシュを記録
- `python <LR>/scripts/ledger.py verify <func-id>` で連鎖を検証。不一致＝上流が改訂された＝下流は「要再生成」
- ハッシュは `ledger.py hash <path>`（sha256 先頭8桁）

## ISSUE 運用

- 採番は全体通し。`docs/issues/` の最大番号+1（`ledger.py next-issue` が返す）
- 必ず「仮説＋Yes/Noで答えられる問い」の形式（templates/issue.md）
- 人の回答が付いたら status: answered → 成果物へ反映して applied。ドメイン知識なら domain-knowledge.md へ転記
- kind: spec-gap(④が詰まった) / domain / legacy-bug / triage(⑤ループ上限) / other

## 人の承認ゲート

対象: ①の reviewed 化、②の approved 化、⑤トリアージの (b)(c)、ループ上限後の再開。

1. skill が承認用サマリ（変更点・チェックリスト）をチャットに提示する
2. 人がチャットで OK / 修正指示を返す
3. OK なら skill がフロントマター（status, approved-by, approved-date）を更新する

勝手に approved にしない。承認待ちで turn を終えるのは正しい動作。

## ④⑤ループと再開

- attempt は「最後の裁定以降の⑤実行回数」。上限3
- 3回目 fail → triage ISSUE 自動起票、ledger に blocked_by 記録、WBS ⛔。④⑤skillは blocked 中の実行を拒否
- 人が ISSUE を裁定 → 反映(applied) → `ledger.py unblock <func-id>` → 人が⑤（または②③の再生成）を再トリガ。attempt は1から

## スタブ禁止（④）

- `NotImplementedError`・本体が pass/... だけの関数・TODO/FIXME は `check_stubs.py` が検出し、④は未完了扱い
- 実装しきれない場合は spec-gap ISSUE を起票して停止 → ①改訂（レガシー確認・根拠付き）→ ハッシュ伝搬 → ④再開

## 出力・レンダリング（フェーズごとに必ずHTML更新）

- 各フェーズの最後に必ず実行する（人がブラウザで途中経過を常に確認できる状態を保つ）:
  1. `ledger.py wbs` で WBS を再生成
  2. `quarto render docs` で HTML サイトを更新（`docs/_quarto.yml` はテンプレから⓪で配置済み）
- Quarto 未導入なら quarto-typst-pdf skill の `qtpdf.py install` でポータブル導入
  （`~/.local/quarto/bin/quarto`。PATH 登録不要）
- 閲覧は `docs/_site/` を静的サーバで配信（例: `python -m http.server 8765 --directory docs/_site`）
- 成果物フロントマターに独自キーを足すときは Quarto 予約キーと衝突させない（coverage → tc-coverage の前例）
- PDF は 仕様書/テスト仕様書/テスト結果 の種別ごとに個別（quarto-typst-pdf skill）、④は Sphinx で HTML のみ
