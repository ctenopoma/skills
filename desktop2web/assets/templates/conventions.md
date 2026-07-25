# プロジェクト規約（desktop2web / 機能レーン③④と画面レーンの入力に必ず含める）

## 対象

| 項目 | 値 |
|------|----|
| レガシー | C# WinForms |
| バックエンド | Python 3.12+ / pytest |
| フロントエンド | React + TypeScript + Tailwind CSS（Vite）/ Playwright |

## 型対応表（C# → Python）

| C# | Python | 注意 |
|----|--------|------|
| decimal | decimal.Decimal | 金額。float にしない |
| double / float | float | |
| int / long | int | |
| string | str | null 許容は `str \| None` |
| bool | bool | |
| DateTime | datetime.datetime | Kind(UTC/Local) の扱いを①で明記 |
| DataTable / DataRow | list[dict] または dataclass のリスト | ①で列構造を確定 |
| null | None | C# の null 伝播（?.）は明示的な None チェックに |
| out / ref 引数 | 戻り値タプル | |
| イベントハンドラ内ロジック | 純関数に昇格（origin: ui-embedded） | UIへの参照は引数化 |
| 例外 (throw) | 対応する Python 例外 | ①に例外表を書く |

## ディレクトリ・命名

- バックエンド: `backend/src/<pkg>/`、テスト: `backend/tests/test_*.py`（tc マーカー必須）
- フロントエンド: `frontend/src/pages/<ScreenId>/`、共通部品: `frontend/src/components/`
- モック: `frontend/mocks/S-xxxx/v<N>.html`（静的HTML＋Tailwind CDN。試行錯誤専用、実装に流用しない）
- E2E: `frontend/e2e/S-xxxx.spec.ts`（1画面1ファイル。design-fixed 後に freeze）

## モック規約

- 依存は Tailwind CDN のみ（ビルド不要でブラウザに出せること）
- ダミーデータは⓪で採取した実画面の値に寄せる（レビューのリアリティのため）
- インタラクション表現は最小限のバニラJSでよい（本実装で書き直す前提）

## E2E 規約（Playwright）

- 画面票の受け入れ基準 1行 = 1 test。`test('AC-1: ...')` と番号を対応させる
- バックエンドはテスト用DBで実サービスを起動（機能は⑤で担保済みなのでモックしない）
- セレクタは `data-testid` を正とする（Tailwind クラスで拾わない）

## 禁止事項（legacy-reverse から継承＋画面レーン分）

- 機能レーン③④中に legacy/ を読むこと。④⑤中の backend/tests/ 編集（hook）
- **画面レーン（モック・画面実装）でビジネスロジックを書くこと**
  （バリデーション規則・計算・絞り込み条件は機能レーンの API を呼ぶ。
  仮実装したくなったら functions.json への追加起票＝⓪の抽出漏れとして ISSUE）
- design-fixed 後の無断デザイン変更（ISSUE→承認→mock-review 差し戻しが正規経路）
- E2E freeze 後の frontend/e2e/ 編集（機能レーンの tests/ と同じ扱い）
