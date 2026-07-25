# desktop2web

C# WinForms デスクトップアプリを Web アプリ（React + Tailwind / Python バックエンド）へ
派生開発する skill 群。**画面は再設計、機能・DB は鏡移し**の2レーン構成。

```
⓪ d2w-0-analyze  画面・機能・DB・CRUD の全量解析
Ⓐ d2w-policy     技術方針・新画面マップ(N:M)・デザインシステム → 人が承認（全体で1回）
画面レーン        d2w-screen: 1画面=1票=1イタレーション（モック→人レビュー→確定→実装→E2E）
機能・DBレーン    legacy-reverse ①〜⑤ をそのまま流用（C#→Python の conventions 差し替え）
⑥ d2w-6-check    完了検証（全画面 design-fixed＋E2E pass、全機能⑤pass、CRUD網羅）
⑦ legacy-7-analyze 流用（性能・保守性・セキュリティ、挙動保存）
```

## 確定事項

- 対象: WinForms（Designer.cs＋イベントハンドラ＋ADO.NET/ORM を⓪で解析）
- バックエンド: Python（機能の鏡移し先。legacy-reverse フル適用）
- フロントエンド: React + TypeScript + Tailwind CSS（Vite）。UXはデスクトップ制約から解放して再設計
- E2E: Playwright。**画面ごと**に、画面票の受け入れ基準から起こす
- 新画面は旧画面と **N:M 対応**（ウィザード統合・タブ分割などUX都合の再編を許す）
- イタレーション単位は**画面**。画面が使う機能はレーン間で「発注」（プル型スケジューリング）

## 原則（legacy-reverse から継承）

- 機械可読メタデータはフロントマターに集約。WBS・CRUDマトリクスは台帳走査で自動生成
- 承認ゲート: Ⓐ方針書 / 画面票の design-fixed / 機能レーンの①②⑤裁定 は人
- モックの試行錯誤（v1→v2→…）は軽量に回し、**確定だけを承認ゲートにする**。
  版とレビュー指摘は画面票に残す（デザインのトレーサビリティ）
- UIコードに埋まったビジネスロジックは⓪で機能に昇格させて鏡移しレーンへ
  （画面レーンはロジックを1行も発明しない）
- E2Eテストは design-fixed 後に freeze。以後の変更は ISSUE→人承認（tests/ hook 併用）

## 構成

- `skills/` — d2w-0-analyze / d2w-policy / d2w-screen / d2w-6-check
- `scripts/d2w_ledger.py` — screens.json 走査、画面WBS＋CRUDマトリクス生成（legacy-reverse の ledger を import）
- `assets/templates/` — screen.md（画面票）/ policy.md（方針書）/ conventions.md（C#→Python・React規約）
- `references/` — schema.md（screens.json 等のスキーマ）/ workflow.md（2レーン共通規則）

機能レーンのテンプレート・スクリプト・hook・MCP は `../legacy-reverse` を参照する（複製しない）。
