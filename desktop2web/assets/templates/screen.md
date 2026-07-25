---
title: "{{新画面の名前}}"
screen-id: "S-{{3桁連番}}"
legacy: ["L-xxx", "L-yyy"]      # 旧画面（N:M可）
uses: ["F-xxxx"]                 # 使用する機能（⓪の合算＋Ⓐの再編）
route: "/{{path}}"
status: draft        # draft → mock-review → design-fixed → implemented → e2e-pass
mock-version: 0
e2e: null            # null / pass / fail
e2e-frozen: null     # freeze 時の Playwright ファイル sha8
approved-by: null
approved-date: null
---

<!-- 1画面=1票=1イタレーション。d2w_ledger.py wbs が本フロントマターを走査する -->

# 旧画面の分析

<!-- 旧画面のスクリーンショット（⓪で採取）を貼る。何をする画面か、
     デスクトップ制約由来のUX問題点（workflow.md の棚卸し観点で）を列挙 -->

| 旧画面 | 役割 | UX問題点 |
|--------|------|---------|
| L-xxx {{FrmName}} | | |

# 新画面の狙い（Plan）

<!-- N:M再編の理由、Webでどう良くするか。方針書のデザインシステムに従う -->

# 受け入れ基準（E2Eの元ネタ。design-fixed 時に凍結）

<!-- Given/When/Then で書く。機能の正しさは機能レーン⑤が担保済みなので、
     ここは「画面としての振る舞い」（表示・操作・遷移・エラー表示）に限定する -->

| # | Given | When | Then |
|---|-------|------|------|
| 1 | | | |

# モック履歴

<!-- 試行錯誤の記録。versionを上げるたびに1行。指摘は要約でよいが必ず残す -->

| v | ファイル | レビュー指摘（要約） | 対応 |
|---|---------|--------------------|------|
| 1 | ../frontend/mocks/S-xxx/v1.html | | |

# 確定デザイン（design-fixed 時に記入）

- 確定版モック: v{{N}}
- 主要コンポーネント構成:
- 状態管理・API呼び出し（機能レーンのどのシグネチャを叩くか）:

# 実装・E2E記録

<!-- 実装コミット、E2E freeze ハッシュ、実行結果へのリンク -->
