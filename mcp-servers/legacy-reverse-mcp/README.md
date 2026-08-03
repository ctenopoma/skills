# legacy-reverse-mcp

レガシー移植パイプライン（../../legacy-reverse）の**機械的操作**を MCP ツール化したサーバ。
判断・手順は従来どおり skills（legacy-0〜7）が担い、本サーバは決定的な処理だけを提供する。
実体は `legacy-reverse/scripts/` の各スクリプト（単体でも従来どおり動く。二重管理なし）。

## 登録（対象プロジェクトの .mcp.json）

```json
{
  "mcpServers": {
    "legacy-reverse": {
      "command": "python",
      "args": ["C:/work_space/skills/mcp-servers/legacy-reverse-mcp/server.py"]
    }
  }
}
```

全ツールが `root`（対象プロジェクトのルート、既定 "."）を取る。

## ツール一覧（24）

| 分類 | ツール | 対応スクリプト |
|------|--------|---------------|
| ⓪機械抽出 | **extract_functions**（Fortran静的解析→functions.json 生成/マージ。再実行=マージで func_id 不変） | extract_fortran.py |
| 機械レビュー | **review_spec / review_testspec / review_all**（根拠引用の実在・省略・トレーサビリティのハルシネーション検知ゲート） | review_checks.py |
| 台帳(読) | pipeline_status / next_action / next_actions / progress_summary / verify / next_issue_id | ledger.py |
| 台帳(書) | generate_wbs / generate_skeletons / freeze_tests / block / unblock / phase_start / phase_end / completion_check / sphinx_index | ledger.py |
| 実行系 | **run_tests**（verify→pytest→報告書生成の⑤一括） | tc_report_plugin.py + collect_results.py |
| 実行系 | check_stubs / profile | check_stubs.py / profile_run.py |
| 出力系 | render_site（Quarto→Sphinxの正順を内包） / build_pdf | quarto / sphinx / pdf_book.py |

大規模（2000関数級）での再開は `progress_summary`（数行の要約）と
`next_actions`（着手可能リスト）から。全関数リストをコンテキストに読み込まない。

## 設計メモ

- 戻り値は構造化（dict/list）。`pipeline_status` は WBS と同じ判定ロジックの生データを返す
- `run_tests` の result: `pass / fail / blocked(要裁定) / mismatch(③マーカー不整合) / verify_ng`
- hook（tests/ 編集拒否）は MCP 化しない。ツール呼び出しの強制ガードは hook の役割のまま
