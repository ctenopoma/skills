# PlantUML 対応 — 導入手順とライセンス方針

Quarto は `.puml` を直接組版できない。そのため **`scripts/plantuml.py` で事前に
画像(PNG推奨)へビルドしてから、Markdown に `![...](figures/foo.png)` として
埋め込む**という経路を取る。qtpdf.py 側は Mermaid や Draw.io のように `.puml` を
特別扱いしない。この事前ビルドを親(呼び出し側)が担う。

## なぜ Amazon Corretto なのか

PlantUML は Java 製で、動作に JRE/JDK が要る。JDK には Oracle JDK・Temurin
(Eclipse Adoptium)・Amazon Corretto など複数の配布があるが、**ライセンス上
利用が許されているのは Amazon Corretto のみ**という制約がある(Oracle JDK は
商用利用でサブスクリプション契約が必要になりうる。Temurin 等は問題ないことが
多いが、ここでは判断を単純化するため Corretto に統一する)。

Corretto は Amazon が無償配布する OpenJDK ディストリビューションで、
GPLv2+CE(GNU General Public License v2 with Classpath Exception)で提供される。
このライセンスの下では実行ファイルの再配布・利用に追加コストは発生しない。

## 導入先(ユーザー領域・管理者権限不要)

Quarto を `~/.local/quarto` にポータブル展開しているのと同じ思想で、
Corretto は **`~/.local/corretto`** に zip 版を展開する。インストーラは使わない。

```
~/.local/corretto/
├── bin/
│   ├── java.exe        ← plantuml.py はこれを探す
│   └── javac.exe
├── lib/
└── ...
```

### 取得元

```
https://corretto.aws/downloads/latest/amazon-corretto-21-x64-windows-jdk.zip
```

`21` は LTS バージョン番号。Corretto は 8 / 11 / 17 / 21 / 25 などの LTS 系列を
配布している。PlantUML の実行だけが目的なら比較的枯れた LTS(21)で十分。
Linux 環境では `windows` を `linux` に、`.zip` を `.tar.gz` に読み替える。

このスキルで実際に検証したバージョン: **Corretto 21.0.11.10.1**
(`java -version` → `OpenJDK Runtime Environment Corretto-21.0.11.10.1`)

### 展開手順(手動で入れる場合)

1. 上記 URL から zip をダウンロードする(302 リダイレクトの先で ~200MB)。
2. zip の中身は `jdk21.0.11_10/` のような1階層のフォルダを直下に持つ。
   その **フォルダの中身**を `~/.local/corretto/` 直下に置く
   (`~/.local/corretto/bin/java.exe` という配置になるようにする。
   `~/.local/corretto/jdk21.0.11_10/bin/java.exe` のように一段深くしない)。
3. 確認:

   ```bash
   ~/.local/corretto/bin/java.exe -version
   # openjdk version "21.0.11" ...
   # OpenJDK Runtime Environment Corretto-21.0.11.10.1 ...
   ```

   出力に `Corretto` の文字列が入っていることを確認する。

PATH は変更しない。`plantuml.py` がこのパスを直接指定して呼ぶため、
PATH に登録しなくても動く(Quarto の導入方針と同じ)。

## plantuml.jar の導入

**スキル配下(`assets/` 等)には置かない。** 30MB近い巨大バイナリを skill の
git リポジトリにコミットしないため、Quarto のフォントと同じ扱いでユーザー領域
`~/.local/plantuml/` に置く。

### 取得元

```
https://github.com/plantuml/plantuml/releases/latest
```

アセット一覧の中の **`plantuml.jar`**(ライセンス別に分割されていない完全版)を使う。
これは JLaTeXMath(数式描画エンジン)を同梱したフル版で、`<math>...</math>` タグや
`@startmath` の数式描画に必要。`plantuml-mit-light-*.jar` のような軽量版は
数式描画に必要なクラスが欠けている可能性があるため使わない。

このスキルで実際に検証したバージョン: **PlantUML 1.2026.6**
(`java -jar plantuml.jar -version` で確認)

### 配置

```
~/.local/plantuml/plantuml.jar
```

### 確認

```bash
~/.local/corretto/bin/java.exe -jar ~/.local/plantuml/plantuml.jar -version
# PlantUML version 1.2026.6 / ...
# Installation seems OK. File generation OK
```

Graphviz(`dot`)が入っていなくても動く。PlantUML はレイアウトエンジンとして
Graphviz が無い場合に内蔵の Smetana(純 Java 実装)へ自動的にフォールバックする。
Java 以外の外部ツールを追加で入れる必要はない。

## ローカル完結であること

`java -jar plantuml.jar -pipe` はローカルプロセス内で完結してレンダリングする。
PlantUML の公式オンラインサーバ(`www.plantuml.com/plantuml`)は、ソース文字列を
URLエンコードして問い合わせる別経路(`!include http://...` や `-remoteurl` 等)を
明示的に使わない限り呼ばれない。`plantuml.py` はそれらのオプションを使わないため、
外部への送信は発生しない。

## 日本語フォントの注入

`scripts/plantuml.py` は次の2段構えで図中の日本語を本文フォント
(`Noto Sans CJK JP`)に揃える。

1. `.puml` の `@start...` 直後に `skinparam defaultFontName "Noto Sans CJK JP"`
   を自動で差し込む(既に指定があれば触らない)。
2. Java 起動オプション `-Dsun.java2d.fontpath=<フォントフォルダ>` で、
   OS にインストールしていないフォント(qtpdf の `fonts/` フォルダ)を
   Java に認識させる。

**注意点(検証で分かったこと)**:

- `sun.java2d.fontpath` は Java から見えるフォント検索パスを**追加ではなく
  上書き**する。指定フォルダの中身だけが `GraphicsEnvironment` から見える
  ようになる。Noto Sans/Serif CJK JP はラテン文字・記号も含む総合フォントなので
  実害はないが、フォルダには qtpdf の `fonts/` と同じフルセットを渡すこと。
- この上書きは Java の論理フォント(Serif / SansSerif / Monospaced 等)の
  解決には影響しない(OS 側のフォントファイルへの固定マッピングを使うため)。
  スキンパラメータで明示的にフォント名を指定していない要素(矢印・罫線等)は
  影響を受けない。
- JLaTeXMath(数式描画)は自前のTeXフォントリソースを使うため、
  `sun.java2d.fontpath` を絞っても数式の見た目には影響しない(検証済み)。

## PNG と SVG、どちらを使うか

`plantuml.py` は両方作れるが **PNG を既定・推奨とする**。

- **PNG**: レンダリング時点(Java側)でグリフがラスタに焼き込まれる。
  Typst 側のフォント解決に一切依存しないため、日本語が豆腐になるリスクが無い。
- **SVG**: `<text font-family="Noto Sans CJK JP">` という文字列参照のまま残る。
  Typst(内部で resvg 系エンジンを使う)がその名前のフォントを解決できないと
  文字化け・豆腐のリスクがある。これは `references/pitfalls.md` に書かれている
  draw.io SVG の注意点と同じ構造の問題。SVG の拡大耐性が必要な場合のみ選び、
  その際は Typst 側にも同じフォントを font-paths で渡しておくこと。

検証では、Mermaid 用の `filters/fit-images.lua`(`width: 25.17in` のような
自然サイズ指定を `100%` に矯正する処理)は PlantUML の PNG 出力には**適用されず、
かつ不要**だった。widthを指定しない `![...](foo.png)` は、983px 幅
(約26cm相当)のような横長図でもページ幅に自動的に収まることを確認済み
(Mermaid の場合と違い、Quarto が自然サイズを明示的に付与しないため)。

## Java が無い環境について

Java(Corretto)が用意できない・置けない環境では、**PlantUML だけが使えない**。
Mermaid や Draw.io など他の図はこれまでどおり Java 無しで qtpdf.py が処理できる
(Mermaid は Chromium 系ブラウザ、Draw.io は SVG をそのまま埋め込むだけなので
どちらも Java 非依存)。`scripts/plantuml.py doctor` を実行すると、不足している
ものと導入方法をその場で報告する。

## まとめ: 検証済みバージョンと配置

| 項目 | バージョン | 配置 | 取得元 |
| --- | --- | --- | --- |
| Amazon Corretto | 21.0.11.10.1 (LTS) | `~/.local/corretto/` | `https://corretto.aws/downloads/latest/amazon-corretto-21-x64-windows-jdk.zip` |
| plantuml.jar | 1.2026.6 | `~/.local/plantuml/plantuml.jar` | `https://github.com/plantuml/plantuml/releases/latest` (`plantuml.jar` アセット) |

どちらも zip/jar をそのまま置くだけで、インストーラ・管理者権限・PATH変更は
不要。git リポジトリにはコミットしない(`.gitignore` 相当の運用は各プロジェクト
側で行う。このスキル自身のリポジトリにはそもそも生成物・バイナリを置かない)。
