# Mermaid 記法パターン集

Markdown のコードブロック(```mermaid)に書く。GitHub・多くの Wiki・エディタが
そのまま描画する。日本語ラベルは `"..."` で囲むと記号混じりでも安全。

## フローチャート(処理の流れ)

```mermaid
flowchart LR
    A["データ受領"] --> B["品質チェック"]
    B --> C{"欠損あり?"}
    C -- はい --> D["補完方針を決定"]
    C -- いいえ --> E["分布の確認"]
    D --> E
```

- 向き: `LR`(左→右)/ `TD`(上→下)。横に長い流れは LR、階層は TD
- 形: `[四角]` 処理 / `{ひし形}` 分岐 / `([丸角])` 開始・終了 / `[(円筒)]` データ

## 構成図(subgraph で包含を表す)

```mermaid
flowchart LR
    subgraph PC["利用者の PC"]
        CC["Claude Code"]
    end
    subgraph SV["社内サーバ"]
        GW["LiteLLM Proxy"]
        M["MCP サーバ"]
    end
    CC --> GW --> M
```

## シーケンス図(登場者間のやり取り)

```mermaid
sequenceDiagram
    participant U as 利用者
    participant C as Claude
    participant S as MCP サーバ
    U->>C: 分析して
    C->>S: profile(table)
    S-->>C: 列ごとの統計
    C-->>U: 所見の報告
```

- `->>` 実線(呼び出し)/ `-->>` 破線(応答)

## 状態遷移図

```mermaid
stateDiagram-v2
    [*] --> 受付
    受付 --> 実行中: 開始
    実行中 --> 完了: 成功
    実行中 --> 失敗: エラー
    失敗 --> 受付: リトライ
    完了 --> [*]
```

## ガントチャート

```mermaid
gantt
    dateFormat YYYY-MM-DD
    title 実験計画
    section 準備
    装置調整      :a1, 2026-08-01, 5d
    section 測定
    条件A         :after a1, 3d
    条件B         :3d
```

## 強調(使いすぎない)

```mermaid
flowchart LR
    A[通常] --> B[重要]
    style B fill:#eef2ff,stroke:#4f46e5,stroke-width:2px
```
