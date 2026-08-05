#!/usr/bin/env python3
"""docs/ を HTML サイト（WBSがトップ）にレンダリングする。Mermaid 図つき。

`quarto render docs` を直接叩かずにこのスクリプトを通すのは、Quarto が
「Mermaid は .qmd でしか描けない」制約を持つため（Quarto 1.10 で実機確認済み）:

  - `.md` に ```{mermaid} → *サイト全体の render が失敗する*
    （"You must use the .qmd extension for documents with executable code."）
  - `.md` に ```mermaid（GitHub流）→ render は通るが <pre class="mermaid"> が出るだけで
    mermaid.js が読み込まれず、図にならずソースが素のまま表示される
  - `.qmd` に ```{mermaid} → 図になる

成果物は人が読む・他ツールが扱う都合で `.md` のままにしておきたいので、
レンダリング直前に docs/_sitework/ へ `.qmd` の影コピーを作り、そこで render する
（qtpdf.py が PDF でやっている shadow と同じ考え方）。出力先は従来どおり docs/_site/。

影コピーで行う変換:
  - ```mermaid → ```{mermaid}
  - 相対リンクの .md → .qmd（Quarto がサイト内リンクを .html に張り替えられるように）
  - _quarto.yml の render グロブ・navbar href の .md → .qmd、output-dir を ../_site へ

レンダリングは**差分**が既定。前回の成果（docs/_site/.render-manifest.json に
変換後ソースのハッシュを記録）と比べ、変わったページだけ quarto render する。
2000関数級では全体レンダが1時間級になるため、フェーズ末の更新（WBS＋数ページ）を
数十秒に抑えるのが目的。_quarto.yml / wbs.css が変わった時・_site が無い時・
変更ページが多い時（1ファイルずつの起動コストより全体レンダが速い規模）は
自動で全体レンダに切り替わる。--full で強制全体レンダ。
注意: 差分レンダではサイト内検索の索引（search.json）は更新されない。
検索に新ページを載せたくなったら --full を一度実行する。

使い方:
  python render_site.py --root .            # → docs/_site/index.html（差分）
  python render_site.py --root . --full     # 全ページ再レンダリング
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import review_actions  # noqa: E402  仕様書ページへの承認ウィジェット埋め込みに使う
import browser_run  # noqa: E402  ①②未着手ページへの「実行する」ボタン埋め込みに使う（試作）

MERMAID_FENCE = re.compile(r"^(\s*)```mermaid\s*$", re.MULTILINE)
MD_LINK = re.compile(r"\]\((?!https?://)([^)\s]+?)\.md(#[^)]*)?\)")
DOC_SUFFIXES = {".md", ".qmd"}


def find_quarto() -> str:
    q = shutil.which("quarto")
    if q:
        return q
    for cand in (Path.home() / ".local/quarto/bin/quarto.exe",
                 Path.home() / ".local/quarto/bin/quarto"):
        if cand.exists():
            return str(cand)
    sys.exit("error: quarto が見つからない（quarto-typst-pdf skill の qtpdf.py install で導入）")


def transform_doc(text: str) -> str:
    text = MERMAID_FENCE.sub(r"\1```{mermaid}", text)
    return MD_LINK.sub(lambda m: f"]({m.group(1)}.qmd{m.group(2) or ''})", text)


def inject_review_widget(text: str, widget: str) -> str:
    """フロントマターの直後（本文の先頭）に承認ウィジェットを挿む。

    ページを開いた瞬間に見える位置＝一番上。widget は ```{=html} ... ``` の
    生ブロック（review_actions.widget_html が作る）。
    """
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == "---")
        end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return widget + "\n\n" + text
    lines[end + 1:end + 1] = ["", widget, ""]
    return "\n".join(lines)


WIDGET_DIR_KIND = {"specs": "spec", "test-specs": "testspec"}


def maybe_inject_review_widget(project_root: Path, rel: Path, body: str) -> str:
    """docs/specs|test-specs/<fid>.md が承認待ちなら承認ウィジェットを、
    docs/index.qmd（WBSトップ）なら⑥完了検証ボタンを埋めて返す。

    承認待ちでない（reviewed/approved 済み・skeleton 未着手）場合や、
    functions.json に該当が無い（骨子生成前・手動追加直後の一時ファイル等）場合は
    そのまま body を返す——render 全体をここで落とさないよう例外は握りつぶす。
    """
    if rel == Path("index.qmd"):
        try:
            widget = browser_run.check_widget_html(str(project_root))
        except Exception as e:                       # noqa: BLE001 — render を止めない
            print(f"note: {rel} の⑥実行ウィジェット生成に失敗（{e}）。ウィジェットなしで続行")
            return body
        return inject_review_widget(body, widget) if widget else body

    parts = rel.parts
    if len(parts) != 2 or parts[0] not in WIDGET_DIR_KIND:
        return body
    kind = WIDGET_DIR_KIND[parts[0]]
    fid = Path(parts[1]).stem
    try:
        widget = review_actions.widget_html(str(project_root), kind, fid)
        if not widget and kind == "spec":
            # 承認待ちでなければ、①〜⑤未着手の実行トリガーを試す（試作機能）
            widget = browser_run.trigger_widget_html(str(project_root), fid)
    except Exception as e:                          # noqa: BLE001 — render を止めない
        print(f"note: {rel} の承認ウィジェット生成に失敗（{e}）。ウィジェットなしで続行")
        return body
    return inject_review_widget(body, widget) if widget else body


def transform_yml(text: str) -> str:
    text = re.sub(r"\.md\b", ".qmd", text)          # render グロブと navbar href
    if "wbs/*.qmd" not in text:                     # 旧テンプレ救済: WBS分割ページを render 対象に
        text = re.sub(r"(?m)^(\s*)- \"\*\.qmd\"\s*$",
                      "\\1- \"*.qmd\"\n\\1- \"wbs/*.qmd\"", text, count=1)
    if "pipeline.html" not in text:                 # 旧テンプレ救済: バッチ状況リンクを navbar に追加
        text = re.sub(
            r"(?m)^(\s*)- href: api/index\.html\s*\n\s*text:[^\n]*$",
            lambda m: m.group(0) + f"\n{m.group(1)}- href: pipeline.html"
                                   f"\n{m.group(1)}  text: バッチ状況",
            text, count=1)
    if re.search(r"(?m)^\s*output-dir:", text):
        return re.sub(r"(?m)^(\s*)output-dir:.*$", r"\1output-dir: ../_site", text)
    return re.sub(r"(?m)^(\s*)type: website\s*$", r"\1type: website\n\1output-dir: ../_site", text)


def build_shadow(docs: Path, work: Path) -> int:
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    n = 0
    for src in sorted(docs.rglob("*")):
        rel = src.relative_to(docs)
        if any(part.startswith(("_", ".")) for part in rel.parts):
            continue                                 # _site / _sitework / _pdfwork / .quarto
        dst = work / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        elif src.suffix in DOC_SUFFIXES:
            dst.parent.mkdir(parents=True, exist_ok=True)
            body = transform_doc(src.read_text(encoding="utf-8-sig"))
            body = maybe_inject_review_widget(docs.parent, rel, body)
            dst.with_suffix(".qmd").write_text(body, encoding="utf-8", newline="\n")
            n += 1
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)                   # 画像などのリソース
    css = docs / "wbs.css"
    if not css.exists():                             # ⓪より前に作られたプロジェクト救済
        tmpl = Path(__file__).resolve().parent.parent / "assets" / "templates" / "wbs.css"
        if tmpl.exists():
            shutil.copy2(tmpl, work / "wbs.css")
            print(f"note: docs/wbs.css が無いのでテンプレを使った（`cp {tmpl} {css}` で常設できる）")
    yml = docs / "_quarto.yml"
    if not yml.exists():
        sys.exit(f"error: {yml} がない（assets/templates/_quarto.yml を docs/ に配置する）")
    yml_text = transform_yml(yml.read_text(encoding="utf-8-sig"))
    (work / "_quarto.yml").write_text(yml_text, encoding="utf-8", newline="\n")
    n += make_placeholders(work, yml_text)
    return n


# ナビバーが参照するがまだ生成されていないページ（⓪の時点では⑥⑦等が無い）。
# リンク切れにせず「いつできるか」を書いたプレースホルダを影コピー側にだけ置く。
PLACEHOLDER_NOTE = {
    "domain-knowledge": "人の裁定・業務知識がここに蓄積されます（ISSUE の回答が転記されます）。",
    "conventions": "プロジェクト規約。⓪（/legacy-0-analyze）で人と確定します。",
    "completion-check": "⑥完了検証（/legacy-6-check）を実行すると自動生成されます。",
    "analysis": "⑦分析（/legacy-7-analyze）で生成されます。",
}


def make_placeholders(work: Path, yml_text: str) -> int:
    made = 0
    for href in re.findall(r"href:\s*([\w\-./]+\.qmd)", yml_text):
        dst = work / href
        if dst.exists():
            continue
        note = PLACEHOLDER_NOTE.get(Path(href).stem, "該当フェーズの実行時に生成されます。")
        title = Path(href).stem
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(
            f'---\ntitle: "{title}"\n---\n\n'
            "::: {.callout-note}\n"
            f"このページはまだ作成されていません。{note}\n"
            ":::\n", encoding="utf-8", newline="\n")
        made += 1
    return made


API_PLACEHOLDER = """<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">
<title>新コード詳細(API)</title>
<style>body{font-family:sans-serif;max-width:640px;margin:80px auto;color:#334155;line-height:1.8}
a{color:#4f46e5}</style></head><body>
<h1>新コード詳細(API) はまだ生成されていません</h1>
<p>④実装の docstring から Sphinx で生成されます。④が進んでから
<code>python -m sphinx -b html docs-sphinx docs/_site/api</code>
（render_site.py の後に実行）で作成されます。</p>
<p><a href="../index.html">← WBS へ戻る</a></p></body></html>
"""


def page_hashes(work: Path) -> dict:
    """影コピー内の全ページ（変換後 .qmd）のハッシュ。差分検出の基準。"""
    return {p.relative_to(work).as_posix():
            hashlib.sha256(p.read_bytes()).hexdigest()[:12]
            for p in sorted(work.rglob("*.qmd"))}


def config_hash(work: Path) -> str:
    """テーマ・ナビバー・CSS が変わったら全ページ作り直し（見た目が全ページに効くため）。"""
    h = hashlib.sha256((work / "_quarto.yml").read_bytes())
    css = work / "wbs.css"
    if css.exists():
        h.update(css.read_bytes())
    return h.hexdigest()[:12]


def plan_render(site: Path, pages: dict, conf: str, force_full: bool):
    """(全体レンダか, 変更ページ, 削除ページ) を決める。"""
    manifest_p = site / ".render-manifest.json"
    if force_full or not (site / "index.html").exists() or not manifest_p.exists():
        return True, [], []
    try:
        old = json.loads(manifest_p.read_text(encoding="utf-8"))
    except ValueError:
        return True, [], []
    if old.get("config") != conf:
        print("note: _quarto.yml / wbs.css が変わったので全体レンダリングする")
        return True, [], []
    old_pages = old.get("pages", {})
    changed = [r for r, h in pages.items() if old_pages.get(r) != h]
    removed = [r for r in old_pages if r not in pages]
    # 1ファイルずつは quarto の起動コストが載る。変更が全体の2割を超えたら全体レンダが速い
    if len(changed) > max(20, len(pages) // 5):
        print(f"note: 変更 {len(changed)}/{len(pages)} ページ → 全体レンダリングに切り替え")
        return True, [], []
    return False, changed, removed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="対象プロジェクトのルート")
    ap.add_argument("--full", action="store_true", help="差分を無視して全ページ再レンダリング")
    ap.add_argument("--keep-work", action="store_true", help="影コピー(_sitework)を残す（調査用）")
    args = ap.parse_args()

    docs = Path(args.root).resolve() / "docs"
    if not docs.is_dir():
        sys.exit(f"error: {docs} がない")
    site = docs / "_site"
    work = docs / "_sitework"
    n = build_shadow(docs, work)
    pages = page_hashes(work)
    conf = config_hash(work)
    quarto = find_quarto()

    full, changed, removed = plan_render(site, pages, conf, args.full)
    ok = True
    if full:
        ok = subprocess.run([quarto, "render", str(work)]).returncode == 0
        done_msg = f"{n} ページ（全体）"
    else:
        for i, rel in enumerate(changed, 1):
            print(f"render [{i}/{len(changed)}] {rel}")
            r = subprocess.run([quarto, "render", str(work / rel)],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace")
            if r.returncode != 0:
                print(r.stdout + r.stderr)
                ok = False
                break
        for rel in removed:                      # 消えた成果物の残骸ページを掃除
            (site / rel).with_suffix(".html").unlink(missing_ok=True)
        done_msg = (f"{len(changed)} ページ（差分。全 {n} ページ中、削除 {len(removed)}）"
                    if changed or removed else "変更なし（0 ページ）")

    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    if not ok:
        sys.exit("error: quarto render 失敗")
    (site / ".render-manifest.json").write_text(
        json.dumps({"config": conf, "pages": pages}), encoding="utf-8")
    api_index = site / "api" / "index.html"
    if not api_index.exists():                   # ④前でもナビバーの API リンクを切らさない
        api_index.parent.mkdir(parents=True, exist_ok=True)
        api_index.write_text(API_PLACEHOLDER, encoding="utf-8", newline="\n")
    # ナビバーの「バッチ状況」の実体。serve_site.py 配信時は同内容のライブページが
    # ルートで優先されるが、素の http.server や EXE 同梱スナップショットでも 404 にしない
    import serve_site                            # 隣のファイル（ページ定義を共有）
    (site / "pipeline.html").write_text(serve_site.PIPELINE_PAGE,
                                        encoding="utf-8", newline="\n")
    print(f"wrote {site}: {done_msg}")


if __name__ == "__main__":
    main()
