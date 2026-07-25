---
name: d2w-0-analyze
description: desktop2webのフェーズ⓪。WinFormsアプリを解析して画面一覧（screens.json）・機能一覧（functions.json）・DBスキーマ（schema.json）・CRUDマトリクスを作る。「WinFormsを解析して」「Web化の土台を作って」で使う。
user-invocable: true
---

# d2w-0-analyze — ⓪ WinForms 全量解析

親skill desktop2web の references/ に従う。`d2w` = `scripts/d2w_ledger.py`。

## 手順

### 1. ヒアリング

対象ソリューションの場所、DB の種類と DDL/接続コードの所在、新パッケージ名。
`assets/templates/conventions.md` を埋めて docs/conventions.md に確定（人のOK）。

### 2. 画面の解析 → screens.json (legacy_screens)

- `Form`/`UserControl` 派生クラスと `.Designer.cs` を列挙 → 画面・部品インベントリ
- `Show()`/`ShowDialog()` 呼び出し → 画面遷移グラフ
- イベントハンドラ（`+=` 購読と Designer の関連付け）→ 呼び出すロジックを追跡し
  `handlers[].calls` に記録
- **各画面のスクリーンショット（または Designer から再構成したレイアウト図）を
  `docs/screenshots/L-xxx.png` に採取**（画面票のレビューで必ず使う）

### 3. 機能の解析 → functions.json

- ロジック層のクラス・メソッドを legacy-reverse 形式で抽出（`origin: logic`）
- **イベントハンドラ直書きのビジネスロジック**（計算・検証・絞り込み）は関数として
  切り出して登録（`origin: ui-embedded`）。UI参照は引数に置き換えた形でシグネチャを設計
- 各機能の `crud` フィールドに 触るテーブル×操作 を記録（SQL文字列・ORM呼び出しから）

### 4. DBの解析 → schema.json

DDL・ORMマッピング・SQL文字列からテーブル・列・制約を抽出。出典（file:line）を必ず付ける。

### 5. 検証と生成

- 完全性チェック: Form 数・public メソッド数の機械カウントと突合。不明は ISSUE
- `d2w crud` を生成し、**どの機能からも触られないテーブル／どの画面からも呼ばれない機能**を
  洗い出す（死蔵か抽出漏れかを ISSUE で人に確認）
- 機能レーン用に `ledger skeletons`（legacy-reverse）、`d2w wbs`、quarto render

### 6. 報告

画面数・機能数（ui-embedded の比率も）・テーブル数・CRUD の検出事項を報告し、
`/d2w-policy` へ誘導する。

## 禁止

- 新画面の設計をここでやること（それはⒶ）。⓪は事実の記録に徹する
- 確信のない情報を書くこと（不明は ISSUE）
