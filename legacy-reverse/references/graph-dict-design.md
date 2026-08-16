# ⓪拡張 設計 — グラフ層・変数辞書・フロー・例外ポリシー

DESIGN.md 本体に統合するまでの間、本改修（M1〜M6）の設計の正はこのファイル。
既存原則（DESIGN.md §1）を全て継承する。特に:

- **functions.json が唯一の正データ**。グラフ・辞書ページ・フロー到達集合はすべて導出物
- **機械が正、AIは意味づけ**。列挙・クラスタリング・検証・伝搬は決定的スクリプト。
  LLM の仕事は「解釈の記入」だけで、範囲逸脱は機械検証で弾く
- **人の承認ゲート**。辞書の語義確定・例外ポリシーの決定は人。AIは仮説＋根拠で聞く
- **再抽出は常にマージ**（func_id / var_id 不変・手修正保持・承認保持）

## 決定事項（2026-08-08 相談で確定）

| 論点 | 決定 |
|---|---|
| グラフの持ち方 | functions.json から毎回構築する導出層。外部エクスポート口は作らない（この開発範囲で閉じる）。エンジンは**純標準ライブラリ**（BFS/Tarjan。依存追加なし） |
| 辞書と①の順序 | **既定は「変数承認 → ①仕様化」**（dict-gate ON）。`--no-dict-gate` で切替可。既に reviewed の仕様書は触らず、辞書と矛盾したら矛盾レポートのみ |
| 例外処理 | 0割は一例。**汎用の例外ポリシー機構**: 機械検知 → ポリシー登録簿（EP-xxx）と突合 → 未定義は人に質問 → 決定を登録して同種の全箇所に再利用。Python は例外で停止するため「未決定のまま進む」選択肢は無い（①の機械レビューで NG） |
| 単位・COMMON別名 | 辞書エントリに unit / aliases を持たせる（M2 に含める） |
| LLM のモデル | pipeline の kind ごとにモデル指定可（`claude -p --model`）。辞書解釈は sonnet、①仕様書は既定モデル |

---

## P1. call_sites と graph.py（M1）

### functions.json への追加フィールド（関数エントリ）

```json
"call_sites": [
  { "callee": "F-0087", "name": "CALCTAX", "line": 152,
    "args": ["WK_AMT", "RATE", null] }
]
```

- `args`: 実引数のうち**単純な変数名はそのまま**（大文字・ソース表記）、式・リテラル・
  配列要素参照（`X(I)` は `X` に落とす: 配列全体も同一実体）は変数名に還元できなければ `null`
- `callee` は既存 `_lookup_call` で解決した func_id。未解決なら省略し `name` のみ残す
- `call sites` は**ソースから完全に導出される**ため、マージ時は常に上書き（手修正対象外）
- 既存 `calls`（func_id 集合）の生成ロジックは変えない。call_sites は付加情報
- function 参照（call 文でない `Y = CALCTAX(X)` 形式）も inferred_calls と同様に
  引数を取れる場合は call_site として記録する（`"inferred": true` を付ける）

### scripts/graph.py（新規・依存ゼロ・LLM不使用）

functions.json（と flows）を読み、隣接リストを構築して答える CLI。

| コマンド | 出力 |
|---|---|
| `graph.py reachable <fid...>` | 到達可能集合（fid・名前・深さ）。`--json` 可 |
| `graph.py callers <fid> [--transitive]` | 逆方向（影響範囲） |
| `graph.py between <fid> <fid>` | 経路（BFS 最短＋件数） |
| `graph.py dead` | どのフローのエントリ（フロー未定義時は F-0000）からも到達不能な関数。excluded は除外し「exclude 候補」として理由候補つきで列挙。**自動 exclude はしない** |
| `graph.py cycles` | SCC（Tarjan）でサイズ2以上の強連結成分 |
| `graph.py summary` | ノード/エッジ数・エントリ・到達率・dead 件数（数行 JSON） |

- Python ライブラリとしても使う（`load_graph(root) -> {fid: set(callee)}` 等）。
  ledger.py / variables.py / pipeline.py がフロー絞り込み・伝搬で import する
- excluded 関数はノードとして残すが、reachable/dead の既定では通過可・対象外を区別表示

## P2. 変数辞書（M2）

### data/variables.json（variables.py build が生成・マージ）

```json
{
  "variables": [
    {
      "var_id": "V-0001",
      "canonical_name": "RATE",
      "aliases": ["RATE", "R8TBL"],
      "desc": "", "unit": null,
      "status": "unreviewed | interpreted | approved",
      "rank": "A | B | C | D",
      "confidence_basis": ["E-0001-02"],
      "occurrences": [
        { "func_id": "F-0001", "name": "RATE", "role": "input",
          "legacy_type": "REAL*8", "line": 120 }
      ],
      "links": [
        { "kind": "call_binding", "from": "F-0001:WK_R", "to": "F-0087:RATE", "line": 152 }
      ],
      "evidence": [
        { "ev_id": "E-0001-01", "kind": "comment", "file": "legacy/tax.f",
          "line": 118, "text": "C  RATE: ANNUAL TAX RATE" }
      ],
      "evidence_hash": "a1b2c3d4",
      "approved_by": null, "approved_date": null,
      "flags": []
    }
  ]
}
```

### クラスタリング（Union-Find・機械的根拠のみ）

ノード = `(func_id, 変数名)`。**別関数の同名は結合しない**。結合ルール:

1. 同一 COMMON ブロックの同一位置（別名でも同一実体 → aliases に集約）
2. call_sites の実引数 ↔ 呼び先仮引数（位置対応）
3. EQUIVALENCE
4. 同一関数スコープ内の同名

名前が同じで別クラスタになったものは相互に `flags: ["name_collision"]` を立て、
辞書ページで「同名別義」として併記する。

### 根拠バンドル（機械収集）

kind: `comment`（宣言行の近傍・同行コメント）/ `format_label`（WRITE/PRINT+FORMAT の
文字列リテラル）/ `data_init`（DATA・PARAMETER 初期値）/ `usage_expr`（代入・式での
使用行、上限N件）/ `common_pos`（COMMON名と位置）。ev_id は var 内連番。
`evidence_hash` = evidence 配列の正規化 sha256 先頭8桁（承認維持判定に使う）。

### LLM 解釈の契約（interpretations.json）

LLM（pipeline kind: `dict`、既定モデル sonnet）は variables.json を**編集しない**。
`data/interpretations.json` に書く:

```json
{ "V-0001": { "desc": "年間税率", "unit": "無次元(比率)",
              "rank_claim": "A", "evidence_cited": ["E-0001-01"], "notes": "" } }
```

`variables.py verify-interp` が機械検証してからマージ（status: interpreted）:

- 指示された var_id 集合と完全一致（欠落・余剰は NG）
- evidence_cited が実在し、その var のものである
- **rank は LLM の申告でなく検証側が根拠種別から決定**:
  - A: comment / format_label / data_init を引用し、desc がその内容と矛盾しない
  - B: domain-knowledge.md の項目、または links で結ばれた approved 済み変数を引用
  - C: usage_expr / 命名慣習のみ
  - D: 引用なし → マージ拒否（desc は「不明」でキュー直行）
- rank A/B → 一括承認候補、C/D → 人の確認キュー

### 人の承認導線（辞書ページ）

- `variables.py page` が `docs/variables.qmd` を生成（自動生成・手編集禁止）。
  render_site.py がウィジェットを埋め込み、serve_site.py に `POST /dict-action` を追加
  （既存 `/review-action` と同じ 127.0.0.1 限定・Host/Origin 検証・FROZEN 無効化）
- 並び順: **影響度（出現関数数）降順 × rank 昇順（C/D が先）**。rank・status・
  名前でフィルタ。A/B はチェックボックス一括承認、C/D は1件ずつ desc/unit を
  修正入力して承認（修正入力は approved と同時に desc を確定させる）
- 承認1回でクラスタ全出現に効く。approved_by / approved_date を記録
- チャット承認も同格（従来どおり）

### 伝搬とゲート

- `variables.py propagate`: approved の desc/unit を functions.json の
  inputs/outputs/globals の desc へ機械転記（`[V-0001]` 参照つき）→ skeletons/WBS 再生成。
  IO 表に辞書リンク列を足す
- **dict-gate（既定 ON）**: `ledger next --phase 1` / pipeline の①対象選定で、
  その関数の変数に approved でないものが残る関数を除外（除外理由を表示）。
  `--no-dict-gate` で解除。**既に spec が draft/reviewed の関数はゲート対象外**
- **dict-hash 連鎖**: ①生成時に使用した (var_id, desc) 集合のハッシュを spec
  フロントマター `dict-hash` に記録。承認後に語義が改訂されたら `ledger verify` /
  WBS が ⚠ 表示。reviewed 済み仕様書は自動修正せず
  `variables.py conflicts` が矛盾レポート（docs/dict-conflicts.md）を出すだけ

## P3. フロー（M3）

functions.json トップレベルに追加:

```json
"flows": [ { "flow_id": "FL-01", "name": "月次バッチ", "entries": ["F-0000"], "desc": "" } ]
```

- `ledger flow add <name> --entry F-xxxx[,F-yyyy]` / `flow rm` / `flow list`。
  main が複数ならそれぞれ、main 内の大分岐は「分岐先の代表サブルーチン」をエントリ指定
- 到達集合は graph.py がその場で計算（保存しない＝再抽出に自動追随）
- `ledger next --flow <name>`・`pipeline.py run/spec --flow <name>`・WBS にフロー別進捗表。
  **人が「このフローだけ作業」と指定できる**
- 関数は複数フローに属し得るため、成果物は従来どおり関数単位。①仕様書の
  フロントマター/冒頭に所属フローを自動記載（文脈付与のみ）
- flows 未定義時の既定エントリは F-0000（現行挙動と互換）

## P4. 追加開発の後発合流（M5）

- 再抽出 → `variables.py build` 再実行。マージ規則:
  - var_id 不変（クラスタの代表 occurrence で対応付け）
  - `evidence_hash` 不変 → 承認・desc 維持
  - 出現が増えただけ → 承認維持＋ `flags: ["occurrence_added"]`（辞書ページで任意再確認）
  - クラスタの分裂・併合 → status を unreviewed に戻し `flags: ["cluster_changed"]`
    （意味の同一性の前提が崩れた時だけ人に戻す）
- call_sites / hazards は常に再生成。フロー到達集合は導出なので自動追随
- 新規関数は dict-gate に従い「変数承認 → ①」の順で合流

## P5. 例外ポリシー機構（M4）— 0割は一例

### 検知（機械・拡張可能な検出器テーブル）

extract_fortran.py に hazard スキャンを追加。関数エントリに:

```json
"hazards": [
  { "hz_id": "H-0012-01", "kind": "div_by_var", "line": 152,
    "expr": "X / Y", "vars": ["Y"] }
]
```

初期 kind: `div_by_var`（変数を含む分母）/ `sqrt_arg` / `log_arg` /
`array_index_var`（変数添字）。検出器は kind→正規表現/走査関数のテーブルで追加可能。
hz_id は関数内連番。ソース導出なのでマージ時は常に上書き。

### ポリシー登録簿 docs/exception-policy.md（人が承認する規約）

```markdown
| EP-ID | 対象 | 適用範囲 | 決定 | 備考 | 承認 |
| EP-001 | div_by_var | 全体既定 | guard_raise | 0割は ZeroDivisionError を仕様化 | 承認者/日付 |
| EP-002 | div_by_var F-0012 H-0012-01 | 個別 | caller_guarantees | Y は上流で >0 保証（SPEC-…参照） | 〃 |
```

決定の語彙: `detect_only`（仕様に記載のみ）/ `guard_raise`（ガードして例外送出）/
`guard_value`（代替値。値を備考に明記）/ `legacy_preserve`（IEEE Inf/NaN 等の再現）/
`caller_guarantees`（呼び出し元保証。根拠必須）。適用範囲は 全体既定 → 関数 → 個別 hazard
の順に個別が勝つ。**Fortran は続行し得るが Python は停止するため、既定を決めずに
④へ進むことはできない。**

### 突合と質問キュー（機械）

- `hazards.py match`（または variables.py に同居）: 全 hazard に EP を割り当て、
  未マッチを `docs/exception-queue.md` に「仮説＋選択肢」形式で列挙
  （kind ごとに集約: 「div_by_var が N 箇所。既定をどうしますか: guard_raise /
  detect_only / 式ごとに個別判断」）。人の回答（チャット or ウィジェット）を
  EP 登録 → 再突合で同種全箇所に反映
- ①テンプレに「例外・数値特異点」節を追加: hazard × 適用 EP × 仕様記述の表。
  review_checks.py spec に「hazards 全件が言及され、引用 EP が実在するか」を追加
  （検討漏れ・EP 捏造の機械検知）
- ②はこの節から境界ケース（0・0近傍・負値）を導出。review_checks.py testspec で網羅突合

## モデル階層（pipeline / browser_run）

- KINDS の各エントリに `model`（省略時は現行どおり既定モデル）。
  `claude -p --model <id>` を付けて起動。CLI `--model` で一括上書き可
- 既定: `dict`（辞書解釈）= sonnet。①〜⑤ = 指定なし（従来）

## マイルストーンとファイル所有（サブエージェント分担の境界）

| M | 内容 | 触るファイル |
|---|---|---|
| M1a | call_sites 抽出 | scripts/extract_fortran.py |
| M1b | graph.py 新規 | scripts/graph.py |
| M2a | variables.py（build/verify-interp/propagate/page/conflicts） | scripts/variables.py |
| M2b | 辞書ウィジェット・/dict-action | scripts/render_site.py, serve_site.py, review_actions.py |
| M2c | dict-gate・dict-hash・IO表辞書列・pipeline kind dict＋model | scripts/ledger.py, pipeline.py, browser_run.py |
| M3 | flows・--flow・WBS フロー表 | scripts/ledger.py, graph.py, pipeline.py |
| M4 | hazards 検知・EP 突合・テンプレ・review_checks | scripts/extract_fortran.py, hazards.py(新), review_checks.py, assets/templates/ |
| M5 | 辞書マージの承認維持・conflicts | scripts/variables.py, ledger.py |
| M6 | skill 文書更新（legacy-0-analyze・legacy-0-dict 新設・workflow.md・schema.md・DESIGN.md 統合・MCP ツール追加） | skills/, references/, mcp-servers/ |

検証方針: 各 M は scratchpad に小さな Fortran フィクスチャ（COMMON・CALL・EQUIVALENCE・
0割を含む 3〜4 サブルーチン）を作って抽出→ build → 突合まで通し、期待 JSON と突合する
スモークを付ける（tests/ は対象プロジェクト用語なので、skill 側は scripts/selftest/ に置く）。
