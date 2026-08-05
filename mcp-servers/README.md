# mcp-servers

内製 MCP サーバ置き場(スキルではないので Claude Code のスキル読み込み対象外)。

**このブランチ(`legacy-reverse-dist`)には legacy-reverse-mcp だけを収録している**
(data-mcp / stats-mcp は `main` ブランチにある)。

| サーバ | 用途 | 依存 |
| --- | --- | --- |
| [legacy-reverse-mcp](legacy-reverse-mcp/) | レガシー移植パイプライン([legacy-reverse](../legacy-reverse/) skill)の機械操作を型付きツール化(26個)。登録方法は対象プロジェクトの `.mcp.json` 経由(サーバの README 参照) | `pip install "mcp[cli]"` |
