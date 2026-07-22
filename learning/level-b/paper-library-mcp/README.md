# レベルB-2: paper-library-mcp — ローカル文献ライブラリ MCP

> **ハンズオンは紙芝居版で**: [../../slides/b2-papers.html](../../slides/b2-papers.html) をブラウザで開く。
> この README は同内容のテキスト版(記録用)。

手元のフォルダに溜めた PDF(論文・技術資料・マニュアル)をキーワード検索できるようにする
MCP サーバ。外部 Web には一切出ない。学ぶことは2つ:

1. **入力がバイナリ (PDF)** — pypdf でテキスト抽出してから SQLite にインデックスする
2. **tools に加えて resources を使う** — MCP の2つ目の主要機能

## 1. tools と resources の使い分け

| | tools | resources |
| --- | --- | --- |
| 性格 | 呼び出す「操作・計算」 | 読み込む「データ・読み物」 |
| 呼び方 | LLM が判断して呼ぶ | URI で指定して読む(Claude Code では `@サーバ名:URI` で参照) |
| この章での例 | reindex / list_papers / search_papers | `paper://<ファイル名>`(本文全文) |

検索のように「引数を取って処理する」ものはツール、
本文全文のように「大きな読み物データ」はリソース、と分けるのが定石。
検索結果には全文を含めず「全文はリソースで」と誘導することで、
コンテキストに載る量を段階的に制御できる(Skill の段階的開示と同じ発想)。

## 2. 構成

```
~/paper-library/            ← PDF を置くだけ(場所は PAPER_LIBRARY_DIR で変更可)
    ├── smith2024_xxx.pdf
    ├── tanaka2023_yyy.pdf
    └── .index.db           ← reindex が作る SQLite インデックス
```

```
reindex (tool)
  PDF走査 → pypdf でタイトル・本文抽出 → SQLite へ
search_papers (tool)
  LIKE 検索 → ファイル名・タイトル・ヒット箇所の前後80字を返す
paper://{filename} (resource)
  本文テキスト全文
```

ポイント: **抽出はインデックス時に済ませる**。検索のたびに PDF を開かないので速く、
LLM に渡るのは常にテキストになっている。

## 3. コードの骨格

完成品: [server.py](server.py)。リソースはデコレータが違うだけ。

```python
@mcp.tool()
def search_papers(query: str) -> str:
    """キーワードで文献を全文検索し、ヒット箇所の前後を返す。..."""

@mcp.resource("paper://{filename}")
def read_paper(filename: str) -> str:
    """指定した PDF の本文テキスト全文。要約や精読はこれを読んでから行う。"""
```

`paper://{filename}` は**リソーステンプレート**。`{filename}` 部分が引数になる。

## 4. 動かす

```bash
pip install "mcp[cli]" pypdf
mkdir ~/paper-library      # ここに手持ちの PDF を数本入れる
claude mcp add paper-library -- python C:/work_space/skills/learning/level-b/paper-library-mcp/server.py
```

動作確認:

1. 「文献ライブラリをインデックスして」→ `reindex` → 件数が返る
2. 「◯◯について書いてある文献ある?」→ `search_papers` → ファイル名+スニペット
3. 「(ヒットした文献)を要約して」→ Claude が `paper://` リソースを読んで要約
   (`@paper-library:paper://ファイル名.pdf` と自分で指定してもよい)

## 5. 改造課題

- サブフォルダも走査する(`glob("**/*.pdf")`)
- 差分インデックス(追加されたファイルだけ処理する)にする
- SQLite の FTS5 を使って検索を高速化・スコア順にする
- 本文からの著者・発行年の抽出を足し、`search_papers` に年での絞り込みを付ける

## 6. 他ツールでの利用

レベルAと同じく `--http` 起動で他クライアントからも使える。
resources への対応度はクライアントによって差があるため、
リソース相当の内容を返す `get_paper_text(filename)` ツールを併設しておくと互換性が上がる
(tools はほぼすべてのクライアントが対応している)。

---

レベルB はここまで。次のレベルC では「業務を置き換える規模の本格スキル」の設計法を学ぶ。
このサーバは、レベルC の発展課題(調査報告パイプライン)で部品として再登場する。
