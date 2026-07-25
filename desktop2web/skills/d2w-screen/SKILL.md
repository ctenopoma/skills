---
name: d2w-screen
description: desktop2webの画面イタレーション。1画面=1票で、モック作成→人レビューの試行錯誤→デザイン確定→React実装→Playwright E2Eまでを回す。「S-001をやろう」「この画面のモックを作って」で使う。
user-invocable: true
---

# d2w-screen — 画面イタレーション（1画面 = 1票）

親skill desktop2web の references/ に従う。前提: Ⓐ方針書が approved。
引数: screen-id（省略時は `d2w next` の提案）。

## Plan: 票の起票（status: draft → mock-review）

1. `docs/screens/S-xxxx.md`（骨子は生成済み）を埋める:
   旧画面スクショ＋UX問題点 → 新画面の狙い（方針書の原則に照らす）→ **受け入れ基準**
   （Given/When/Then。画面の振る舞いに限定。ロジックの正しさは機能レーン⑤の担保に委ねる）
2. `uses` の機能に未完了があれば WBS 上「⏳機能待ち」になる。**モックまでは進んでよい**が、
   画面実装は全機能⑤passまで着手しない（legacy-1〜5 への発注を人に提案する）

## Do: モック → 人レビューのループ（軽量・承認不要）

1. `frontend/mocks/S-xxxx/v1.html`（静的HTML＋Tailwind CDN、規約参照）を作成
2. 静的サーバ＋ブラウザペインで人に見せる → 指摘を**票のモック履歴表に転記** → v2, v3…
3. 人が「これで確定」と言ったら **design-fixed**（承認ゲート）:
   frontマターの status / approved-by / mock-version を更新し、受け入れ基準を凍結

## Check: 実装と E2E

1. React 実装 `frontend/src/pages/S-xxxx/`（確定モックの構造・方針書のコンポーネントに従う。
   API は機能レーンの公開シグネチャのみ呼ぶ。**ロジックのフロント再実装は禁止**）
2. E2E `frontend/e2e/S-xxxx.spec.ts` を受け入れ基準 1行=1test で作成し、
   `ledger hash` で票の `e2e-frozen` に記録（freeze）
3. E2E 実行。fail 時のトリアージは workflow.md の (a)(b)(c)（(b)(c)は ISSUE→人承認）。
   3回で自動エスカレーション（機能レーンと同じ規律）

## Act

- 全 test pass → status: e2e-pass、票の実装・E2E記録を記入
- `d2w wbs` → quarto render → 次の画面へ（`d2w next`）
- design-fixed 後にデザイン変更が必要になったら ISSUE→人承認→mock-review へ差し戻し
  （版数は続きから。E2E再生成→再freeze）

## 禁止

- モック・実装でビジネスロジックを書くこと（必要になったら functions.json 追加起票＝ISSUE）
- design-fixed 前に React 実装へ進むこと／人のOKなしの design-fixed
- E2E freeze 後の frontend/e2e/ 編集（正規経路: ISSUE→承認→再生成）
