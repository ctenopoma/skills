# データスキーマ（desktop2web）

legacy-reverse の schema.md を継承し、画面レーン分を追加する。パスは対象プロジェクトのルート基準。

## プロジェクト構成

```
<project>/
  legacy/                 # WinForms 原文（読み取り専用。読めるのは⓪と①改訂のみ）
  backend/src/<pkg>/      # ④ Python 実装（機能レーン）
  backend/tests/          # ③ pytest（hook が④⑤中の編集を拒否）
  frontend/src/           # React + Tailwind（画面レーン）
  frontend/mocks/S-xxxx/  # 画面モック（v1.html, v2.html, ... 静的HTMLで試行錯誤）
  frontend/e2e/           # Playwright（design-fixed 後に freeze）
  docs/
    index.qmd             # WBS（d2w_ledger.py wbs で自動生成）
    policy.md             # Ⓐ 方針書（承認ゲートあり）
    crud.md               # CRUDマトリクス（自動生成）
    screens/S-xxxx.md     # 画面票
    specs/ test-specs/ test-results/ issues/  # 機能レーン（legacy-reverse と同一）
    conventions.md  domain-knowledge.md  completion-check.md
  data/
    functions.json        # 機能（legacy-reverse 形式＋crud 拡張）
    screens.json          # 画面（下記）
    schema.json           # DB（下記）
    ledger.json           # 機能レーンの台帳（legacy-reverse）
```

## data/screens.json（⓪が生成。旧画面の記録＋Ⓐで新画面マップ確定）

```json
{
  "legacy_screens": [
    { "id": "L-001", "form": "FrmOrderList", "file": "legacy/FrmOrderList.cs",
      "kind": "form|dialog|usercontrol", "shows": ["L-002"],
      "handlers": [ { "event": "btnSearch.Click", "calls": ["F-0012"] } ] }
  ],
  "screens": [
    { "screen_id": "S-001", "title": "注文一覧・詳細",
      "legacy": ["L-001", "L-002"],
      "uses": ["F-0012", "F-0031"],
      "route": "/orders" }
  ]
}
```

- `legacy_screens` は⓪の解析結果（事実）。`screens` はⒶの新画面マップ（設計、N:M可）
- `uses` は legacy 側 handlers.calls の合算に、Ⓐでの再編（統合・分割）を反映したもの

## data/functions.json（legacy-reverse 形式＋拡張）

- 追加フィールド `crud`: 機能が触るテーブルと操作。例 `"crud": { "T_ORDER": "R", "T_ORDER_ITEM": "CRU" }`
- 追加フィールド `origin`: `"logic"`（ロジック層由来）| `"ui-embedded"`（イベントハンドラ直書きから⓪で昇格）

## data/schema.json

```json
{ "tables": [ { "name": "T_ORDER", "columns": [ { "name": "ORDER_ID", "type": "int", "pk": true } ],
               "source": "legacy/db/create_order.sql:12" } ] }
```

## docs/screens/S-xxxx.md（画面票）フロントマター

```yaml
screen-id: "S-001"
title: "注文一覧・詳細"
legacy: ["L-001", "L-002"]
uses: ["F-0012", "F-0031"]
route: "/orders"
status: draft            # draft → mock-review → design-fixed → implemented → e2e-pass
mock-version: 0          # モックの現行版数
e2e: null                # null / pass / fail
e2e-frozen: null         # freeze 時の Playwright ファイルの sha8
approved-by: null        # design-fixed の承認者
```

## 状態遷移とWBS✅条件

| 対象 | 遷移 | ✅条件 |
|---|---|---|
| Ⓐ 方針書 | draft → approved | approved |
| 画面票 | draft → mock-review → design-fixed → implemented → e2e-pass | e2e-pass |
| 機能 | legacy-reverse と同一（①〜⑤） | 同一 |
| ⑥ | — | 全画面 e2e-pass ＋ 全機能⑤pass ＋ CRUD網羅（下記） |

CRUD網羅 = schema.json の全テーブルについて、legacy の CRUD 合算と新実装側で参照される
機能の CRUD 合算が一致すること（消えた操作・増えた操作を⑥で検出する）。
