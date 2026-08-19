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
import review_actions  # noqa: E402  承認待ち状態の判定（KINDS・blocked）に使う
import pipeline  # noqa: E402  Project ロードキャッシュ（_project）を共有する
from ledger import parse_frontmatter  # noqa: E402

MERMAID_FENCE = re.compile(r"^(\s*)```mermaid\s*$", re.MULTILINE)
MD_LINK = re.compile(r"\]\((?!https?://)([^)\s]+?)\.md(#[^)]*)?\)")
DOC_SUFFIXES = {".md", ".qmd"}
SCRIPTS = Path(__file__).resolve().parent          # 案内パネルに載せるコマンドのパス


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


def inject_notice(text: str, notice: str) -> str:
    """フロントマターの直後（本文の先頭）に案内パネルを挿む。

    ページを開いた瞬間に見える位置＝一番上。notice は ```{=html} ... ``` の
    生ブロック（下の _notice が作る）。
    """
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == "---")
        end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return notice + "\n\n" + text
    lines[end + 1:end + 1] = ["", notice, ""]
    return "\n".join(lines)


# --- 閲覧専用の案内パネル ---------------------------------------------------
#
# HTML サイトは**見せるだけ**（実行・承認・裁定の操作は持たない）。
# その代わり、人の対応が要るページには「いま何待ちで、どう返答するか」を
# 静的な案内パネルとして焼き込む。返答チャネルは3つで、すべて同格:
#   チャット / ファイル記入（ISSUE回答欄・review-feedback.md）/ CLI（review_actions.py 等）

def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _notice(title: str, body_html: str, color: str = "#4f46e5", bg: str = "#f5f5ff",
            dom_id: str = "") -> str:
    id_attr = f' id="{dom_id}"' if dom_id else ""
    return f"""```{{=html}}
<div class="lr-notice"{id_attr}
     style="border:2px solid {color};border-radius:10px;padding:14px 18px;margin:0 0 22px;
            background:{bg};font-family:system-ui,sans-serif">
  <div style="font-weight:700;margin-bottom:8px">{title}</div>
  {body_html}
</div>
```"""


def _cmd(text: str) -> str:
    return (f'<pre style="white-space:pre-wrap;background:#37415115;padding:8px 12px;'
            f'border-radius:6px;font-size:.85rem;margin:4px 0">{_esc(text)}</pre>')


def _channels_html(chat: str, file_note: str, cli: str) -> str:
    """3チャネル（チャット / ファイル記入 / CLI）の返答方法。どれでも同格。"""
    return (
        '<div style="font-size:.9rem;margin-top:8px">返答はどれでも（すべて同格）:'
        "<ul style='margin:4px 0 0'>"
        f"<li><b>チャット</b>: {chat}</li>"
        f"<li><b>ファイル記入</b>: {file_note}</li>"
        f"<li><b>CLI</b>:{_cmd(cli)}</li>"
        "</ul></div>")


def review_notice_html(root: str, kind: str, fid: str) -> str | None:
    """①draft / ②generated（人の承認待ち）ページの案内パネル。対象外は None。"""
    cfg = review_actions.KINDS[kind]
    p = Path(root).resolve() / "docs" / cfg["dir"] / f"{fid}.md"
    if not p.exists():
        return None
    fm = parse_frontmatter(p.read_text(encoding="utf-8-sig"))
    if fm.get("status") != cfg["pending_status"]:
        return None
    r = cfg["check"](root, fid)
    label = "①仕様書" if kind == "spec" else "②テスト仕様書"

    if r["ok"]:
        review_box = ('<p style="color:#166534;margin:0">✅ 機械レビュー: 問題なし。'
                      "内容を確認して承認してください。</p>")
    else:
        items = "".join(f"<li>{_esc(x)}</li>" for x in r["problems"])
        review_box = (
            '<div style="background:#fee2e2;border-radius:8px;padding:10px 14px;color:#991b1b">'
            f"<b>❌ 機械レビューNG（{len(r['problems'])}件）— 解消されるまで承認できません"
            f"（承認要求はどの入口でも拒否されます）</b>"
            f"<ul style='margin:6px 0 0'>{items}</ul></div>"
            f'<p style="font-size:.9rem;margin:6px 0 0">AI に自己修正させる: '
            f"チャットで <code>/legacy-{'1-spec' if kind == 'spec' else '2-testspec'} {fid}</code>、"
            f"または <code>pipeline.py run --root .</code>（修正依頼・機械NGは自動で再実行対象になる）</p>")

    warn_box = ""
    if r.get("warnings"):
        warn_box = ("<p style='color:#854d0e;margin:6px 0 0'>⚠ "
                    + "<br>".join(_esc(w) for w in r["warnings"]) + "</p>")

    channels = _channels_html(
        chat=f"「{fid} OK」で承認、「{fid} 修正: 〜」で修正依頼",
        file_note="docs/review-feedback.md に修正依頼を記入（状態: pending。書式はファイル冒頭）",
        cli=(f"python {SCRIPTS / 'review_actions.py'} approve {kind} {fid} --by <名前> --root <プロジェクト>\n"
             f"python {SCRIPTS / 'review_actions.py'} request-changes {kind} {fid}"
             " --by <名前> --comment \"…\" --root <プロジェクト>"))
    # WBS・一斉レビュー表のリンク（…#review-<fid>）の着地点になる
    return _notice(f"{label}レビュー — {fid}（承認待ち: {fm.get('status')}）",
                   review_box + warn_box + channels, dom_id=f"review-{fid}")


def adjudicate_notice_html(root: str, fid: str) -> str | None:
    """⑤で blocked（裁定待ち）の関数の①ページに出す案内パネル。blocked でなければ None。"""
    iss = review_actions.blocked_issue(root, fid)
    if not iss:
        return None
    rootp = Path(root).resolve()
    ip = rootp / "docs" / "issues" / f"{iss}.md"
    title, question, answered = "", "", False
    if ip.exists():
        text = ip.read_text(encoding="utf-8-sig")
        ifm = parse_frontmatter(text)
        title = ifm.get("title") or ""
        answered = ifm.get("status") == "answered"
        m = re.search(r"(?ms)^#\s*質問（人への問い）\s*$(.*?)(?=^# |\Z)", text)
        if m:
            question = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S).strip()
    q_box = (f'<pre style="white-space:pre-wrap;background:#78350f14;padding:10px 14px;'
             f'border-radius:8px;font-size:.9rem;margin:8px 0">{_esc(question)}</pre>'
             if question else "")
    note = ('<p style="color:#166534;margin:0">この ISSUE は回答済み（answered）です。'
            "裁定の完了（unblock）だけが残っています。</p>" if answered else "")
    channels = _channels_html(
        chat=f"「{iss} は〜が正」のように回答（AI が記入・unblock・再テストまで行う）",
        file_note=f"docs/issues/{iss}.md の「回答（人が記入）」欄に書く（次の skill 起動時に反映）",
        cli=(f"python {SCRIPTS / 'review_actions.py'} adjudicate {fid} --issue {iss}"
             " --by <名前> --comment \"回答…\" --root <プロジェクト>"))
    return _notice(
        f'⛔ ⑤裁定待ち — {fid}（<a href="../issues/{iss}.html">{iss}</a>'
        + ("：" + _esc(title) if title else "") + "）",
        note + q_box + channels
        + '<p style="margin:8px 0 0;color:#854d0e;font-size:.85rem">'
          "裁定が完了すると ⑤ の再実行で回答が反映されます。</p>",
        color="#b45309", bg="#fffbeb")


def dict_notice_html(root: str) -> str | None:
    """docs/variables.qmd（変数辞書）に出す承認方法の案内。未承認が無ければ None。"""
    p = Path(root).resolve() / "data" / "variables.json"
    if not p.exists():
        return None
    try:
        variables_ = (json.loads(p.read_text(encoding="utf-8-sig")) or {}).get("variables") or []
    except (OSError, ValueError):
        return None
    pending = [v for v in variables_ if v.get("status") != "approved"]
    if not pending:
        return None
    need_desc = [v for v in pending
                 if v.get("rank") == "D" or not (v.get("desc") or "").strip()
                 or (v.get("desc") or "").strip() == "不明"]
    body = (
        f'<p style="margin:0 0 6px">未承認 <b>{len(pending)}</b> 件'
        f"（うち意味の記入が必要 {len(need_desc)} 件）。"
        "承認1回でクラスタ全出現（別名・COMMON・呼び出し結線）に効きます。</p>"
        + _channels_html(
            chat="「V-0001,V-0002 を承認」「V-0003 の意味は〜に直して承認」",
            file_note="なし（辞書の正は data/variables.json。手編集はしない）",
            cli=(f"python {SCRIPTS / 'variables.py'} approve V-0001,V-0002 --by <名前> --root <プロジェクト>\n"
                 f"python {SCRIPTS / 'variables.py'} revise V-0003 --desc \"意味\" [--unit \"単位\"]"
                 " --by <名前> --root <プロジェクト>"))
        + '<p style="margin:6px 0 0;font-size:.85rem;color:#3730a3">'
          "承認後は propagate → skeletons → ページ再生成まで /legacy-0-dict（または CLI 手順）が行います。</p>")
    return _notice(f"変数辞書の承認 — 未承認 {len(pending)} 件", body)


def index_notice_html(root: str) -> str | None:
    """WBSトップ（docs/index.qmd）の案内。⑤全passで⑥、⑥passで⑦の実行方法を出す。"""
    rootp = Path(root).resolve()
    if not (rootp / "data" / "functions.json").exists():
        return None
    p = pipeline._project(rootp)
    funcs = p.funcs()
    if not funcs or not all(p.status_of(f)["test_ok"] for f in funcs):
        return None                              # ①〜⑤が全部終わるまで⑥の案内は出さない
    cc = rootp / "docs" / "completion-check.md"
    cc_status = (parse_frontmatter(cc.read_text(encoding="utf-8-sig")).get("status")
                 if cc.exists() else None)
    if cc_status == "pass":
        body = ('<p style="color:#166534;margin:0">⑥完了検証: ✅ pass 🎉</p>'
                '<p style="margin:6px 0 0">次は ⑦分析・改善（チャットで '
                "<code>/legacy-7-analyze</code>。計測 → 施策候補の提案 → 人の承認 → 挙動保存で適用）。</p>")
    else:
        head = ('<p style="color:#991b1b;margin:0 0 6px">前回の⑥: ❌ fail'
                '（<a href="completion-check.html">不備一覧</a>）</p>' if cc_status == "fail"
                else '<p style="margin:0 0 6px">全関数の⑤が完了しています。⑥完了検証を実行してください。</p>')
        body = head + _cmd(f"python {SCRIPTS / 'ledger.py'} check --root <プロジェクト>") \
            + ('<p style="margin:6px 0 0;font-size:.9rem">チャットなら <code>/legacy-6-check</code>'
               "（検証 → 最終レンダリングまで行う）。</p>")
    return _notice("⑥完了検証", body, color="#0891b2", bg="#ecfeff")


NOTICE_DIR_KIND = {"specs": "spec", "test-specs": "testspec"}


def maybe_inject_notice(project_root: Path, rel: Path, body: str) -> str:
    """人の対応が要るページに閲覧専用の案内パネルを焼き込む。

    対象外（reviewed/approved 済み・skeleton 未着手など）はそのまま body を返す。
    render 全体をここで落とさないよう例外は握りつぶす。
    """
    try:
        if rel == Path("variables.qmd"):
            notice = dict_notice_html(str(project_root))
        elif rel == Path("index.qmd"):
            notice = index_notice_html(str(project_root))
        else:
            parts = rel.parts
            if len(parts) != 2 or parts[0] not in NOTICE_DIR_KIND:
                return body
            kind = NOTICE_DIR_KIND[parts[0]]
            fid = Path(parts[1]).stem
            notice = review_notice_html(str(project_root), kind, fid)
            if not notice and kind == "spec":
                # ⑤裁定待ち（blocked）なら ISSUE と回答方法を案内する
                notice = adjudicate_notice_html(str(project_root), fid)
    except Exception as e:                          # noqa: BLE001 — render を止めない
        print(f"note: {rel} の案内パネル生成に失敗（{e}）。パネルなしで続行")
        return body
    return inject_notice(body, notice) if notice else body


def transform_yml(text: str, has_variables: bool = False) -> str:
    text = re.sub(r"\.md\b", ".qmd", text)          # render グロブと navbar href
    if has_variables and "variables.qmd" not in text:
        # 変数辞書（P2）は docs/variables.qmd が生成されたときだけナビバーに出す。
        # ledger.py は並行編集の対象なので触らず、影コピー側で救済する
        # （テンプレ側にエントリが入った世代では二重に足さない）
        text = re.sub(
            r"(?m)^(\s*)- href: index\.qmd\s*\n\s*text:[^\n]*$",
            lambda m: m.group(0) + f"\n{m.group(1)}- href: variables.qmd"
                                   f"\n{m.group(1)}  text: 変数辞書",
            text, count=1)
    if "wbs/*.qmd" not in text:                     # 旧テンプレ救済: WBS分割ページを render 対象に
        text = re.sub(r"(?m)^(\s*)- \"\*\.qmd\"\s*$",
                      "\\1- \"*.qmd\"\n\\1- \"wbs/*.qmd\"", text, count=1)
    if "pipeline.html" not in text:                 # 旧テンプレ救済: バッチ状況リンクを navbar に追加
        def add_after(pattern: str, s: str) -> str:
            return re.sub(
                pattern,
                lambda m: m.group(0) + f"\n{m.group(1)}- href: pipeline.html"
                                       f"\n{m.group(1)}  text: バッチ状況",
                s, count=1)
        # 新しめのテンプレは API リンクの後ろへ。それが無い世代（apiエントリ追加前の
        # プロジェクト）は WBS（index）エントリの後ろへ挿す——どの世代でもリンクが出るように
        new = add_after(r"(?m)^(\s*)- href: api/index\.html\s*\n\s*text:[^\n]*$", text)
        if new == text:
            new = add_after(r"(?m)^(\s*)- href: index\.qmd\s*\n\s*text:[^\n]*$", text)
        text = new
    if "manual.html" not in text:                   # 旧テンプレ救済: マニュアルリンクを navbar に追加
        text = re.sub(
            r"(?m)^(\s*)- href: pipeline\.html\s*\n\s*text:[^\n]*$",
            lambda m: m.group(0) + f"\n{m.group(1)}- href: manual.html"
                                   f"\n{m.group(1)}  text: マニュアル",
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
        if rel.parts and rel.parts[0] in ("templates", "prompts"):
            continue                                 # プロジェクト所有の設定（テンプレ・工程別
            #                                          プロンプト調整）はサイトには載せない
        dst = work / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        elif src.suffix in DOC_SUFFIXES:
            dst.parent.mkdir(parents=True, exist_ok=True)
            body = transform_doc(src.read_text(encoding="utf-8-sig"))
            body = maybe_inject_notice(docs.parent, rel, body)
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
    yml_text = transform_yml(yml.read_text(encoding="utf-8-sig"),
                             has_variables=(docs / "variables.qmd").exists())
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
    import serve_site                            # 隣のファイル（コンソール保護とページ定義を共有）
    serve_site.use_utf8_console()                # ウィジェット生成の note が cp932 で落ちないように
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
    (site / "pipeline.html").write_text(serve_site.PIPELINE_PAGE,
                                        encoding="utf-8", newline="\n")
    # ナビバーの「マニュアル」の実体（skill 同梱の自己完結 HTML をコピー）。
    # サイトに入れておくことで EXE 配布でもレビュアーがマニュアルを読める
    manual = Path(__file__).resolve().parent.parent / "MANUAL.html"
    if manual.exists():
        shutil.copy2(manual, site / "manual.html")
    (site / "lr-widgets.js").unlink(missing_ok=True)   # 旧ウィジェットJSの残骸を掃除
    print(f"wrote {site}: {done_msg}")


if __name__ == "__main__":
    main()
