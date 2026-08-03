# Step 2: MCP サーバ — OSS の利用と内製開発

> **ハンズオンは紙芝居版で**: [../../slides/a2-mcp.html](../../slides/a2-mcp.html) をブラウザで開く。
> この README は同内容のテキスト版(記録用)。

MCP (Model Context Protocol) は、LLM アプリに「道具(ツール)」や「データ(リソース)」を
生やすための標準プロトコル。この章では OSS サーバをそのまま使うところから始めて、
自分で ToDo サーバを書き、最後にそれを HTTP 化する。

## 1. 仕組みの最小理解

登場人物は3つ。

```
ホスト(Claude Code 等)
  └─ クライアント ──(プロトコル)── MCP サーバ ──> ツール / リソースを提供
```

- **サーバ**: ツール(呼べる関数)やリソース(読めるデータ)を提供するプログラム
- **クライアント/ホスト**: Claude Code、Claude Desktop、OpenWebUI など。
  サーバに「どんなツールがある?」と聞き、LLM の判断でツールを呼ぶ

接続方式(transport)は実質2つ:

| transport | 仕組み | 用途 |
| --- | --- | --- |
| **STDIO** | クライアントがサーバをサブプロセスとして起動し、標準入出力で会話 | ローカル完結。いちばん簡単 |
| **Streamable HTTP** | サーバは独立した HTTP サーバとして動き、クライアントが URL に接続 | 複数クライアントから共有、リモート配置 |

> 旧方式の **SSE** transport もまだ動くが、仕様上は非推奨 (deprecated) で
> Streamable HTTP が後継。これから作るなら Streamable HTTP を選ぶ。
> どちらも localhost 間の通信ならプロキシ環境の影響を受けない。

## 2. ハンズオン1: OSS サーバを使う(filesystem)

まずは公式 OSS の filesystem サーバを Claude Code に繋ぐ。npm があれば導入は1コマンド。

```bash
claude mcp add filesystem -- npx -y @modelcontextprotocol/server-filesystem ~/daily-logs
```

意味: 「`filesystem` という名前で、`npx -y @modelcontextprotocol/server-filesystem ~/daily-logs` を
STDIO サーバとして登録する」。最後の引数はこのサーバに許可するディレクトリ。

確認:

```bash
claude mcp list
```

Claude Code を起動し `/mcp` と打つと接続状態とツール一覧が見える。
「daily-logs にあるファイルを一覧して」と頼めば、filesystem サーバの
`list_directory` ツールが呼ばれる(ツール呼び出しの承認プロンプトが出る)。

### OSS サーバの探し方・取り方

- 公式カタログ: GitHub の `modelcontextprotocol/servers` リポジトリ
- npm / pip で配布されているものは `npx` / `uvx` で直接起動できる
- 配布されていないものも `git clone` してソースから起動すればよい
  (登録コマンドの `--` 以降を `python path/to/server.py` 等にするだけ)

### チームで共有するなら .mcp.json

`claude mcp add` は既定では個人設定に入る。プロジェクトのリポジトリで共有したい場合は
プロジェクト直下に `.mcp.json` を置く:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "./data"]
    }
  }
}
```

## 3. ハンズオン2: ToDo サーバを自分で書く(STDIO)

ここが本題。**完成品は見ずに、自分で書いて動かす**(紙芝居版に写経用コードと仕様を掲載)。

1. **写経(1本目)**: 作業フォルダに `my-todo/server.py` を作り、FastMCP の骨格+
   `add_task`(JSON ファイルに追記して結果を返す)を写経する。
   `pip install "mcp[cli]"` →
   `claude mcp add todo -- python (my-todo/server.py の絶対パス)` → 再起動 →
   「タスク追加して: 〜」で動作確認
2. **仕様から自作(2本)**: コードを見ずに実装する
   - `list_tasks(status="open")` — "open" / "done" / "all" で絞って一覧を文字列で返す。0件ならその旨
   - `complete_task(task_id: int)` — status を "done" にして保存。**見つからない ID でも
     エラーにせず「見つからない」と返す**
   - 再起動(STDIO はコード変更のたびに必要)→「一覧見せて」「1番終わった」「99番終わった」で確認
3. **答え合わせ**: 完成例 [todo-mcp/server.py](todo-mcp/server.py) と見比べる。観点:
   docstring の粒度(「いつ使うか」まで書くか)/ 登録日・完了日の+α設計 /
   _load・_save の関数化。`/mcp` で自分の docstring がそのまま説明文になっていることも確認

押さえるポイント: `@mcp.tool()` を付けた関数がそのままツールになる。
**docstring と型ヒントがそのままツールの説明・引数定義として LLM に渡る**ので、
docstring は人間向けコメントではなく「LLM がいつどう使うかの説明」を書く。

## 4. ハンズオン3: 自分のサーバを HTTP 化する

STDIO と HTTP の差が「起動方法の差」でしかないことを、自分のコードで体験する。
my-todo/server.py の末尾を起動引数で切り替わるように書き換える(`import sys` を追加し、
`"--http" in sys.argv` なら `mcp.run(transport="streamable-http")`、それ以外は `mcp.run()`)。

ターミナル1(サーバを起動しっぱなしにする):

```bash
python (my-todo/server.py の絶対パス) --http
```

ターミナル2(HTTP サーバとして登録し直す):

```bash
claude mcp add --transport http todo-http http://127.0.0.1:8000/mcp
```

Claude Code から同じようにタスク操作ができれば成功。データファイルは共通なので
STDIO 版で入れたタスクがそのまま見える。**サーバのコード(ツール定義)は1行も
変わっていない**ことがポイント。transport はあくまで配管で、サーバの本体は
ツール定義のほう。

> STDIO 版は「クライアントがサーバを都度起動する」ので登録だけで動くが、
> HTTP 版は「自分でサーバを立てておく」必要がある、という運用の違いも体感できる。

## 5. 改造課題

- `add_task` に期限 (`due: str = ""`) を足し、`list_tasks` で期限順に並べる
- `delete_task` ツールを足す
- 保存先を JSON から SQLite に変える(ツール定義側は無変更で済むことを確認)

## 6. 他ツールでの利用

同じ server.py がそのまま他のクライアントでも使える。これが「標準プロトコル」の意味。

- **Claude Desktop**: 設定ファイル `claude_desktop_config.json` に追記

  ```json
  {
    "mcpServers": {
      "todo": {
        "command": "python",
        "args": ["C:/work_space/skills/learning/level-a/02-mcp/todo-mcp/server.py"]
      }
    }
  }
  ```

- **OpenWebUI**: ネイティブの MCP 対応(管理画面の External Tools に
  Streamable HTTP の URL `http://127.0.0.1:8000/mcp` を登録)、もしくは
  `mcpo` で MCP サーバを OpenAPI サーバに変換して繋ぐ方法がある。
  いずれも HTTP 化したサーバ(ハンズオン3)がそのまま使える
- **Cursor / その他 MCP 対応ツール**: それぞれの設定ファイルに
  同様の command / url を書くだけ

---

次: [Step 3: Skills × MCP 連携](../03-skills-x-mcp/README.md)
