---
title: "legacy-reverse 利用マニュアル"
subtitle: "レガシーコードのリバースエンジニアリング・パイプライン — 人の操作ガイド"
author: "legacy-reverse project"
date: 2026-07-25
lang: ja
---

# このパイプラインは何か

レガシーコード（Fortran / C# など）を Python へ**仕様ベースで移植**するための、
Claude Code skill 群です。関数を1つずつ、次の流れで進めます。

> ⓪リポジトリ解析 → ①関数仕様書 → ②テスト仕様書 → ③テストコード → ④実装
> → ⑤テスト → ⑥完了検証 → ⑦分析・改善

設計の柱は4つあります。

1. **クリーンルーム分業** — ②③はレガシー原文を見ない。③は「②＋規約」だけ、
   ④は「①＋規約」だけを入力に作られる。独立に作ったテストと実装を⑤で突き合わせる
   ことで、仕様書の穴が機械的に炙り出される。
2. **ハッシュ連鎖** — ①→②→③の各成果物は上流のハッシュを記録している。
   上流が改訂されると下流が自動的に「要再生成（stale⚠）」になり、伝搬漏れが起きない。
3. **人の承認ゲート** — 仕様の確定・テスト仕様の承認・裁定は必ず人が行う。
   AIは「仮説＋Yes/Noの問い」の形で判断材料を出し、勝手に確定しない。
4. **挙動保存の改善（⑦）** — 移植完了後の高速化・リファクタリングは、
   ③のテスト資産を安全網に「テスト全pass維持」を絶対条件として行う。

**あなた（人）の仕事は「トリガを引く」「質問に答える」「承認する」「裁定する」の4つ**です。
コードや文書を直接書く必要は基本的にありません（書いてもよい場所は後述）。

![WBS（進捗のホーム画面）。進捗サマリ・あなたへの質問（Open ISSUES）・関数一覧・⑦改善イタレーションが1画面に集約され、各✅から成果物へ移動できる](assets/manual/wbs.png)

# セットアップ

## 1. skill の配置

対象プロジェクトに skills リポジトリから次をコピーします。

```bash
cp -r skills/legacy-reverse        <project>/.claude/skills/
cp -r skills/legacy-reverse/skills/* <project>/.claude/skills/
```

## 2. hook の登録（必須）

`legacy-reverse/hooks/settings-example.json` の内容を `<project>/.claude/settings.json` に
マージします。これは④⑤フェーズ中の `tests/` 編集を機械的に拒否する安全装置で、
「AIがテストを書き換えて通す」事故をプロンプトではなく仕組みで防ぎます。

## 3. MCP サーバの登録（推奨）

`<project>/.mcp.json` に次を追加すると、台帳操作・テスト実行などの機械処理が
型付きツールになり、許可プロンプトが減って安定します（未登録でも動作は同じ）。

```json
{
  "mcpServers": {
    "legacy-reverse": {
      "command": "python",
      "args": ["<skillsリポジトリ>/mcp-servers/legacy-reverse-mcp/server.py"]
    }
  }
}
```

## 4. ツール類

- **Quarto**（HTML/PDF出力）: 未導入なら quarto-typst-pdf skill の
  `qtpdf.py install` でポータブル導入（管理者権限不要）
- **Chromium 系ブラウザ**（Chrome/Edge 等）: PDF に Mermaid 図を焼き込むのに使います。
  HTML だけなら不要（`qtpdf.py doctor` で有無を確認できます）
- **Python パッケージ**: `pip install pytest sphinx sphinx-rtd-theme`
  （⑦では追加で `radon ruff bandit pip-audit`）

## 5. 開始

Claude Code で `/legacy-reverse` を実行すると、セットアップ状態を確認して
次の一手を案内します。新規なら `/legacy-0-analyze` へ。

# フェーズ別ガイド — 各フェーズで人がやること

すべてのフェーズは**人がスラッシュコマンドで起動**します。AIが勝手に次のフェーズへ
進むことはありません。

| フェーズ | コマンド | 人がやること |
|---------|---------|-------------|
| ⓪ 解析 | `/legacy-0-analyze` | レガシーの場所・言語・新パッケージ名を答える。型対応表・規約を一緒に確定 |
| ① 仕様書 | `/legacy-1-spec F-xxxx` | レビューして OK を出す（reviewed 化）。🟡🔴項目の質問に答える |
| ② テスト仕様 | `/legacy-2-testspec F-xxxx` | ケース一覧と質問を見て承認（approved 化）。期待値の不明点に答える |
| ③ テストコード | `/legacy-3-testcode F-xxxx` | 基本は見守り（完了時に freeze される） |
| ④ 実装 | `/legacy-4-impl F-xxxx` | 基本は見守り。spec-gap ISSUE が来たら①改訂を指示 |
| ⑤ テスト | `/legacy-5-test F-xxxx` | fail 時の裁定（後述）。pass ならWBSで✅を確認 |
| ⑥ 完了検証 | `/legacy-6-check` | 全関数完了後に1回。不備リストが出たら該当フェーズへ差し戻し |
| ⑦ 分析・改善 | `/legacy-7-analyze` | 代表ワークロードを教える。施策票を承認する |

「次にどの関数をやるべきか」は WBS の推奨着手順（依存の葉から）に従います。
迷ったら `/legacy-reverse` に聞けば `next` を提案します。

## ①②の承認とは何をすることか

AIは承認を求めるとき、**変更点サマリ・Confidence（🟢確認済/🟡推測/🔴仮定）の内訳・
未回答の質問一覧**をチャットに提示します。あなたは:

- 内容に問題なければ「OK」と返す → AIがフロントマターの status を更新する
- 質問には **Yes / No / 修正内容** で答える（AIは必ず仮説を添えて聞いてくるので、
  白紙から考える必要はない）
- 答えられない質問は「保留」でよい。ISSUE として残り、WBS の最上部に出続ける

![①関数仕様書。機械抽出したIO表と、機能詳細の各項目に Confidence（🟢🟡🔴）とレガシー行番号の根拠が付く。あなたはこれをレビューして OK を出す](assets/manual/spec.png)

ISSUE は必ず「仮説＋Yes/Noの問い」の形で来ます。白紙の質問は来ません。

![ISSUE の例。AIの仮説に Yes/No/修正 で答えるだけでよい。回答はドメイン知識集に転記され、同じ質問は二度と来ない](assets/manual/issue.png)

## ⑤で fail したときの裁定

テストが落ちたとき、原因は3種類あります。**(a) だけはAIが自走**し、(b)(c) は
あなたの承認が要ります。

| 分類 | 意味 | あなたの操作 |
|------|------|-------------|
| (a) 実装バグ | 実装が①を満たしていない | 何もしない（AIが④修正→⑤再実行を回す） |
| (b) テストコードバグ | ③が②とズレている | ISSUE の証拠を見て承認 → ③再生成を指示 |
| (c) 仕様が誤り | ②①自体が間違い | ISSUE で裁定 → ①改訂を指示（伝搬は自動検知） |

(a) のループは**3回で自動停止**し、triage ISSUE が起票されて WBS に ⛔ が出ます。
このときの再開手順:

1. ISSUE を読んで (a)/(b)/(c) を裁定し、回答欄に記入（またはチャットで回答）
2. AIが反映（applied）したら `unblock`（AIに「F-xxxx をアンブロックして」でよい）
3. `/legacy-5-test F-xxxx` を再トリガ（試行回数は1からやり直し）

![⑤テスト結果報告書。実装率（②のケースが③に全部実装されたか）と、ケースごとの内容・分類・結果が1枚で分かる。内容列は②の該当ケース定義へのリンク](assets/manual/test-result.png)

# 日常の関わり方 — ブラウザと直接入力

## WBS サイト

進捗はすべて HTML サイトで確認します（各フェーズの最後に自動更新されます）。

```bash
python -m http.server 8765 --directory docs/_site
```

- **トップ = WBS**: 進捗サマリ、コールグラフ図（緑=完了/黄=着手中/灰=未着手/赤=判断待ち。
  ノードをクリックで仕様書へ）、Open ISSUE（あなたへの質問が常に最上部）、
  関数一覧（各✅から成果物へリンク）、⑦改善イタレーションの達成状況
- ナビバー: ドメイン知識 / 規約 / ⑥完了検証 / ⑦分析 / 新コード詳細(API)
- API ページ（Sphinx）: 関数一覧 → 個別ページ。`Spec:` 行で仕様書へ、
  トレース対応表で func-id ↔ 新関数 ↔ レガシー名 ↔ 仕様書が相互に辿れる

![新コード詳細(API)。docstring から自動生成され、関数一覧→個別ページ、トレース対応表から仕様書へ渡れる](assets/manual/api.png)

## あなたが直接書いてよいファイル

| ファイル | 手編集 | 説明 |
|---------|:---:|------|
| `docs/domain-knowledge.md`・`docs/conventions.md` | ⭕ | あなたが著者。業務知識・規約はここへ |
| ISSUE の「回答（人が記入）」欄 | ⭕ | じっくり書きたい裁定はファイルで |
| `docs/specs/`（仕様書） | △ | 編集可。ハッシュ連鎖が下流を stale にして再確認が走る（設計どおり） |
| WBS・テスト結果・完了検証レポート | ❌ | 自動生成。次の再生成で消える |

**ファイルに直接書いた内容は、次にどのフェーズを起動したときにAIが自動で拾います**
（全skillは起動時に「回答済みISSUE・規約変更」をスキャンしてから本処理に入ります）。
チャットで「DKに追加して: ○○」と言うだけでも構いません。

# ⑦ 分析・改善の回し方

⑥が pass した後、`/legacy-7-analyze` で開始します。1施策 = 1票 = 1イタレーションの
DevOps ループです。

1. **計測・走査**: AIが profile / radon / bandit を実行し、候補を `analysis.md` に列挙。
   このとき**代表ワークロード（実データ規模の入力）を聞かれるので用意する**。
   単体テスト負荷だけではホットスポットを断定しません
2. **項目確定**: 着手する候補が施策票（`docs/improvements/OPT-001.md` など）に昇格。
   票には目的・**検証可能な成功基準（baseline→目標）**・改善仕様が書かれる。
   **あなたはこの票を承認する**
3. **イタレーション**: AIが適用（1施策=1コミット）→ テスト全pass確認 → 再計測 →
   票に実測と判定を記録。全基準達成なら achieved ✅、未達なら巻き戻して振り返り
4. WBS の「⑦改善イタレーション」表で全施策の達成状況（✅/❌/—）がトレースできる

Rust(PyO3) 化のような大きな施策も、AIが4段階基準（CPUバウンドか → NumPyで足りないか →
純粋関数か → 保守コスト明記）で根拠付きの提案を出すので、票の承認で判断してください。

![⑦施策票の例（REF-001）。目的→成功基準（baseline→目標→実測→判定）→検証記録→振り返りが1枚に揃い、目的が達成されたかが後から機械的に追える](assets/manual/improvement.png)

# トラブルシューティング

| 症状 | 意味 | 対処 |
|------|------|------|
| WBS に ⛔ ISSUE-xxx | ④⑤ループが上限到達、裁定待ち | ISSUE に回答 → unblock → ⑤再トリガ |
| WBS に stale⚠ | 上流（①）が改訂され②が古い | `/legacy-2-testspec` で再生成→再承認 |
| WBS に ⚠改変 | freeze 後にテストコードが変わった | 意図した変更なら③で再freeze。心当たりがなければ調査 |
| tests/ 編集が拒否された | hook が正常動作している | テスト側の疑義は ISSUE 経由で（正規経路） |
| 再レンダリングが失敗する | 配信サーバが _site をロック | サーバの cwd を _site の外にして `--directory` 指定 |
| 図がソースのまま表示される | `quarto render docs` を直接叩いた | `render_site.py` で出し直す（Mermaid は影コピー経由でのみ描画される） |
| WBS の関数名が何行にも折れる | `docs/wbs.css` が無い／index.qmd を手編集した | `ledger wbs` で作り直す。CSS はテンプレ（assets/templates/wbs.css）から docs/ にコピー |
| PDF だけ図が出ない | Mermaid の画像化に Chromium 系ブラウザが要る | `qtpdf.py doctor` で確認。HTML 側はブラウザが描くので影響なし |

# PDF 出力

種別ごとの合本 PDF は次で生成します（フォールバック含め自動処理）。

```bash
python <LR>/scripts/pdf_book.py specs        --root . --output pdf/関数仕様書.pdf     --title 関数仕様書
python <LR>/scripts/pdf_book.py test-specs   --root . --output pdf/テスト仕様書.pdf   --title テスト仕様書
python <LR>/scripts/pdf_book.py test-results --root . --output pdf/テスト結果報告書.pdf --title テスト結果報告書
```

生成後は `qtpdf.py check <pdf>` で豆腐・はみ出しの機械チェックができます。

# 付録: 成果物とデータの全体像

```
docs/
  index.qmd            WBS（自動生成・手編集禁止）
  conventions.md       プロジェクト規約（⓪で確定・人が編集可）
  domain-knowledge.md  裁定の蓄積（人が編集可）
  specs/F-xxxx.md          ① 関数仕様書（skeleton→draft→reviewed）
  test-specs/F-xxxx.md     ② テスト仕様書（generated→approved）
  test-results/F-xxxx_日時.md  ⑤ 結果報告書（毎回新規・自動生成）
  issues/ISSUE-xxx.md      質問と裁定（回答欄はあなたが記入可）
  improvements/XXX-nnn.md  ⑦ 施策票（proposed→approved→applied→verified）
  completion-check.md      ⑥ 完了検証（自動生成）
data/
  functions.json       ⓪の解析結果（正データ）
  ledger.json          ハッシュ・ブロック状態（スクリプト専用）
src/ , tests/          ④実装 / ③テスト（④⑤中は hook が tests/ を保護）
```

ステータスの意味: ✅=完了 / ▲=作業中（draft等） / ☐=未着手 / ⛔=裁定待ち / ⚠=要再確認
