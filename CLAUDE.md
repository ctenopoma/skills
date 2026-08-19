# このリポジトリで作業するときのルール

skill（`legacy-reverse/`）とその設計・仕様書（`docs/legacy-reverse/`）を保守するリポジトリ。
**成果物ではなく「成果物を作る道具」を触っている**ので、文書とスクリプトの食い違いが
そのまま利用者の事故になる。次のループを必ず回すこと。

## 1. skill を直したら機械レビュー（必須ゲート）

スクリプト・SKILL.md・references・MANUAL・QUICKREF・設計書のどれかを編集したら:

```bash
python legacy-reverse/scripts/check_skill.py
```

- 検知するもの: 実在しないファイル参照（削除・改名の取りこぼし）／実在しない
  サブコマンド・オプション（argparse とのドリフト）／SKILL.md の frontmatter 不備
- **NG が出たら、各指摘の `hint` に従って直し、exit 0 になるまで繰り返す**。
  `--json` で機械可読（file / line / kind / message / hint）
- 直す方向の原則は **「スクリプトの argparse が正、文書を合わせる」**。
  文書に書いた機能のほうが正しい（実装が足りない）なら、スクリプト側を実装する
- **NG が残る状態で「直しました」と報告しない**（①仕様書の機械レビューと同じ扱い）

## 2. セルフテスト

```bash
python -m pytest legacy-reverse/scripts/selftest -q
```

`test_skill_docs.py` が上のチェックを回帰テストとして持っている（＝チェックを
忘れても pytest で落ちる）。既知の失敗は `test_no_dict_backward_compat`（改修前から）。

## 3. 生成物の作り直し

| 直したもの | 作り直すコマンド |
|---|---|
| `legacy-reverse/MANUAL.md` | `python docs/legacy-reverse/make_manual.py`（HTML＋PDF。`--html` で速く） |
| `docs/legacy-reverse/*.qmd` | `quarto render docs/legacy-reverse` と `python docs/legacy-reverse/make_pdf.py` |

日本語フォント・絵文字フォントのある環境で実行すること。

## 4. 設計の前提（壊さない）

`legacy-reverse/ARCHITECTURE.md` が構成と区分けの正。特に次の3つは**確定した方針**なので、
戻す変更を提案しない:

- **HTML サイトは閲覧専用**。実行・承認・裁定のボタンや POST API を復活させない
  （操作の入口はチャットと CLI。画面は状態と返答方法の案内だけ）
- **①〜⑦の成果物以外の MD は人だけが書く**（conventions.md / domain-knowledge.md /
  exception-policy.md / docs/templates/ / ISSUE 回答欄 / review-feedback.md）。
  skill の手順に「AI がこれらに書き込む」を書かない（提案文の提示まで）
- **固変分離**: ワークフローは skill 共有（固定）、仕様書の項目立て・書き方は
  対象プロジェクトの `docs/templates/`（可変・人が著者）。固定契約
  （LR マーカー・契約見出し）だけは機械が守る

## 5. 指摘が繰り返されたら機械化する

利用者からの指摘は射程で経路が分かれる（`references/workflow.md`「エージェントへの
フィードバックの経路」）。**同じ指摘が3回来て、機械で判定できる形なら
`review_checks.py`（成果物）か `check_skill.py`（skill 自身）に検査を足す**。
プロンプトの言い回しを増やすより、ゲートにするほうが確実に効く。
