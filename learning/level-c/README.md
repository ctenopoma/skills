# レベルC: 本格スキル開発 — 触りながら学ぶ

> **ハンズオンは紙芝居版で**: [C-1: html-craft](../slides/c1-skill-dev.html) /
> [C-2: データ分析支援セット](../slides/c2-ds-toolkit.html) をブラウザで開く。
> この README は同内容のテキスト版(記録用)。

レベルA・Bで部品(手順書と道具)の作り方は身についた。レベルCでは、リポジトリに
同梱された「業務を置き換える規模」の実物2セットを **使う → 中身を読む → 改造する →
自分用に設計する** の順で触る。

## C-1: html-craft(ツール導入型スキル)

スライド / ダッシュボード / デモを自己完結HTML 1ファイルで作るスキル。
PowerPoint・Excelスクショ共有・口頭説明の置き換え。

### ハンズオン

1. **使う(slides)**: 「レベルAで学んだことを5枚のスライドにして」
   - 確認: 1ファイル生成 / ブラウザでめくれる / `grep -c "https://\|cdn"` が 0(自己完結)
2. **使う(PDF化)**: 「いまのスライドをPDFにして」
   - 観察: Claude が変換コードを書かず `scripts/to_pdf.py` を実行する(定型処理はスクリプトへ)
3. **使う(dashboard / demo)**: plot-mcp の sample CSV をダッシュボード化、移動平均のデモ作成
   - 確認: `const DATA` の埋め込み / スライダで即時再描画
4. **読む(SKILL.md)**: 3モードが言い方だけで切り替わった仕掛けを探す
   - 答え: **発動は description(全モードのトリガー語)、分岐は本文のモード判定表**
5. **読む(references / scripts)**: slides.md の部品表と自分の成果物を突き合わせる。
   `new_page.py` を直接実行して CSS のインライン展開を確認。`to_pdf.py` の
   Edge 対策コメントを読む(**道具のクセは一度だけ解いてコードに焼く**)
6. **改造**: references/slides.md に「2カラム比較」部品を追記 → 再起動 →
   「STDIOとHTTPの比較スライドを1枚作って」で新部品が使われるか確認
7. **仕上げワーク**: 自分の業務からひとつ選び、置き場所マップ
   (SKILL.md / references / assets / scripts に何を置くか)を埋めてから
   Claude に SKILL.md 初版を作らせる

### ふりかえり(置き場所の判断基準)

| 置き場所 | 判断基準 | 実物 |
| --- | --- | --- |
| SKILL.md | 毎回必要な判断と手順の骨格 | モード判定表・自己完結ルール |
| references/ | 状況別の詳細知識。パターン集として書く | slides.md の部品表 |
| assets/ | 実証済みの部品(ゼロから書かせない) | deck.css(教材の紙芝居と同じもの) |
| scripts/ | 毎回同じで、LLM にやらせると不安定な処理 | new_page.py / to_pdf.py |

大型スライド(15枚超)は references/slides.md の「分担執筆」手順
(親がアウトラインと一貫性、サブが並列執筆)を使う。

## C-2: データ分析支援セット(部品分担型)

| 部品 | 種類 | 担当 |
| --- | --- | --- |
| [eda-workflow](../../eda-workflow/) | スキル | EDA の手順と判断(指揮者)。references に品質チェックリスト・図の選び方・問い→手法の選定フロー |
| [diagram](../../diagram/) | スキル | ポンチ絵・概念図(Mermaid / SVG) |
| [data-mcp](../../mcp-servers/data-mcp/) | MCP | DuckDB でデータ処理。大きいデータをコンテキストに入れない設計 |
| [stats-mcp](../../mcp-servers/stats-mcp/) | MCP | 検定・相関・ベースラインモデル。LLM に計算させない設計 |

### ハンズオン

0. **セットアップ**: `claude mcp add data / stats / plot` → 再起動 → `/mcp` で接続確認
1. **一巡を観察**: 「`mcp-servers/data-mcp/sample/experiment.csv` を分析して」
   - 観察: load→profile の順で進む / temp の欠損12件を検出する /
     **会話に生データ(400行)が一度も現れない**
   - 答え合わせ: 400行5列 / dose と yield_pct に r ≈ 0.85 / temp はほぼ無相関
2. **深掘り質問**: 「groupごとに差は?」→ ANOVA(p ≈ 8.8e-13、B群が高い)/
   「予測できそう?」→ quick_fit(R² ≈ 0.62、dose重要度 ≈ 0.88)/
   「category と group に関連は?」→ **わざと関連なしの組(p ≈ 0.67)**。
   有意でない結果を無理なく報告できているかを見る
3. **ポンチ絵**: 「ここまでの分析の流れをポンチ絵にして。Markdownに貼れる形式で」
   - 観察: 描く前に箱と矢印の箇条書きで合意を求めてくるか / Mermaid が選ばれるか
4. **ログで設計原則を確認**: ①生データがコンテキストに出ていない
   ②数値はすべて stats-mcp の返り値(暗算ゼロ)③手法選定は references/methods.md
5. **改造(道具と手順書をセットで更新)**: plot-mcp の `plot_csv` に `kind="box"`
   (箱ひげ図)を追加 → charts.md の表にも1行追加 → 再起動 →
   「groupごとの yield_pct の分布を箱ひげ図で」→ B群の箱が上にずれることを確認

### 発展課題

- `compare_groups` に効果量(Cohen's d)を追加
- 所見まとめ → html-craft dashboard の流れを定型化して手順6に追記
- 自分の分野の手法・判断基準を methods.md に追記(references をチームの知識置き場に)
- 自分の実データで一巡し、手順書の不足を見つけて直す

## まとめ: 2つのケーススタディの対比

| | C-1: html-craft | C-2: データ分析支援セット |
| --- | --- | --- |
| 型 | ツール導入型(1スキルで完結) | 部品分担型(スキル×MCPの編成) |
| スキルの役割 | 作り方の知識+スクリプト | 順序・判断・選定フロー(指揮者) |
| MCP の役割 | — | データ処理・統計計算・描画(実働部隊) |
| 育て方 | references に部品を足す | 道具と references をセットで更新 |

設計原則: ①大きいデータはコンテキストに入れない ②決定的な計算は LLM にさせない
③手順と知識は references に集約してチームの資産として育てる。

カリキュラム全体のまとめ: **A** = Skill=手順書 / MCP=道具箱 / 連携=名前を書くだけ。
**B** = 入出力はテキストに限らない。tools と resources。**C** = 本格スキル=知識と処理の
置き場所設計。成果物(ToDoサーバ、plot/data/stats-mcp、html-craft、eda-workflow、diagram)は
すべてそのまま業務で使える。
