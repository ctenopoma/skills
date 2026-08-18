#!/usr/bin/env python3
"""review_checks.py — 成果物の機械レビュー（ハルシネーション・省略の検知ゲート）。

LLM が書いた①仕様書・②テスト仕様書を、LLM を使わずに検証する。
「もっともらしいが根拠のない記述」「勝手な省略」を人のレビュー前に機械で落とすための関門。

report              ①draft の一斉レビュー表を docs/spec-review.md に生成
                    （バッチ実行後の人の一括レビュー用。概要・🟢🟡🔴内訳・機械レビュー・ISSUE）
spec <func-id>      ①のレビュー:
  - 根拠 `file:lines` の実在検証（ファイルが存在し、行範囲がファイル内に収まるか）
  - 🟢(VERIFIED) なのに有効な根拠引用がない項目の検出
  - 骨子プレースホルダの残存（= 記入の省略）・必須節の欠落・空の「副作用・例外」
  - フロントマター legacy.hash と原本の不一致（仕様書が古いソースに基づく）
  - ```{mermaid} の混入（render を落とす）
  - 例外・数値特異点: functions.json の hazards 全件が節に載っているか（検討漏れ）／
    引用 EP-ID が docs/exception-policy.md に実在するか（捏造）／
    ポリシー未決定の hazard を仕様化していないか（hazards が無い関数は素通り）
testspec <func-id>  ②のレビュー:
  - トレーサビリティ: ①の全🟢仕様項目にケースが1件以上あるか。マトリクスの参照先が実在するか
  - 各ケースに「対応仕様」「期待値の根拠」があるか。⚠未確定の数（approved には 0 が必要）
  - spec-hash の鮮度（①改訂の検知）
  - hazard 境界ケース: 決定が挙動に現れる hazard（guard_raise/guard_value/legacy_preserve）
    に hz_id を引用した TC があるか（detect_only/caller_guarantees・未決定は対象外）
all                 全関数を状態に応じて一括チェック

exit 0 = 問題なし / 1 = 問題あり。--json で機械可読出力（MCP からは import で直接呼ばれる）。
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

CITE = re.compile(r"([\w\-./\\]+\.\w{1,4})\s*[:：]\s*(\d+)(?:\s*[-–〜]\s*(\d+))?")
MERMAID_BLOCK = re.compile(r"(?ms)^[ \t]*```mermaid[ \t]*$(.*?)^[ \t]*```[ \t]*$")
MERMAID_TYPE = re.compile(
    r"^\s*(graph\b|flowchart\b|sequenceDiagram|classDiagram|stateDiagram|erDiagram"
    r"|journey|gantt|pie|mindmap|timeline|quadrantChart)")
# flowchart のノードラベル [] / {} の中に引用符なしの丸括弧 → mermaid が Syntax error になる典型
# （レガシー由来のラベル A[IARG(1)=0?] 等。A["IARG(1)=0?"] と引用符で囲めば正しい）
UNQUOTED_PAREN_SQ = re.compile(r"\[(?!\s*\")[^\]\n]*[()]")
UNQUOTED_PAREN_BR = re.compile(r"\{(?!\s*\")[^}\n]*[()]")


def check_mermaid_blocks(text: str) -> list:
    """```mermaid ブロックの、LLMがよくやる構文エラーを機械検知する。

    サイト側では図が「Syntax error in text」になるだけで承認前に気づきにくいので、
    機械レビューNGにして自己修正ループ（再実行ボタン・連続実行）に乗せる。
    完全なパースはしない——誤検知で承認を塞がないよう、確実に壊れる形だけ見る。
    """
    problems = []
    for m in MERMAID_BLOCK.finditer(text):
        lines = [l for l in m.group(1).splitlines()
                 if l.strip() and not l.strip().startswith("%%")]
        if not lines:
            continue
        head = lines[0].strip()
        if not MERMAID_TYPE.match(head):
            problems.append(f"mermaid: 1行目が図種別（flowchart 等）でない: {head[:50]}")
            continue
        if re.match(r"\s*(graph|flowchart)\b", head):
            for l in lines[1:]:
                if UNQUOTED_PAREN_SQ.search(l) or UNQUOTED_PAREN_BR.search(l):
                    problems.append(
                        "mermaid: ラベル内の () が引用符で囲まれていない"
                        f"（Syntax error で図が表示されない）: {l.strip()[:60]} "
                        '→ A["IARG(1)=0?"] のように "…" で囲む')
    return problems
SPEC_HEAD = re.compile(r"^##\s+(SPEC-[\w]+-\d+)", re.M)
CASE_HEAD = re.compile(r"^##\s+([\w]*-?TC-\d+)", re.M)
CONF = re.compile(r"Confidence[:：]?\s*\**\s*(🟢|🟡|🔴)")
PLACEHOLDER = re.compile(r"<!--\s*[①⓪]?で?充填|<!--\s*①で充填")
EVIDENCE_KINDS = ("仕様書🟢", "人間確認済み", "レガシー実測", "⚠未確定")


# レンダ時は全 draft ページからチェックが呼ばれ、引用ごとにレガシー原本を読み直すと
# 2000関数級で分単位になる。原本（レガシーソース）は事実上不変なので、stat
# （mtime+size）が変わるまで行数とハッシュを使い回す。stat 検証付きなので、
# 長寿命プロセス（serve_site.py）から呼ばれても古い値を返さない。
_FILE_CACHE: dict = {}          # {path: ((mtime_ns, size), {"lines": int, "sha8": str})}


def _file_cache_entry(path: Path) -> dict:
    st = path.stat()
    key, sig = str(path), (st.st_mtime_ns, st.st_size)
    hit = _FILE_CACHE.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    entry: dict = {}
    _FILE_CACHE[key] = (sig, entry)
    return entry


def sha8(path: Path) -> str:
    e = _file_cache_entry(path)
    if "sha8" not in e:
        e["sha8"] = hashlib.sha256(path.read_bytes()).hexdigest()[:8]
    return e["sha8"]


def _line_count(path: Path) -> int:
    e = _file_cache_entry(path)
    if "lines" not in e:
        e["lines"] = len(path.read_text(encoding="utf-8",
                                        errors="replace").splitlines())
    return e["lines"]


def _frontmatter(text: str) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ledger import parse_frontmatter
    return parse_frontmatter(text)


def _sections(text: str) -> list:
    """(見出しID or None, 見出し行, 本文) のリスト。## 単位。"""
    parts = re.split(r"(?m)^(##?\s+.*)$", text)
    out, i = [], 1
    while i < len(parts) - 1:
        head, body = parts[i], parts[i + 1]
        m = SPEC_HEAD.match(head) or CASE_HEAD.match(head)
        out.append((m.group(1) if m else None, head.strip(), body))
        i += 2
    return out


# ---------- 例外・数値特異点（hazards × 例外ポリシー） ----------

HAZ_SECTION = re.compile(r"(?m)^(#{1,4})\s*例外[・･]数値特異点\s*$")
RE_HZ_ID = re.compile(r"\bH-\d+-\d+\b")
RE_EP_ID = re.compile(r"\bEP-\d+\b")

_FUNCS_CACHE: dict = {}          # {path: ((mtime_ns, size), {func_id: エントリ})}


def _functions_index(rootp: Path) -> dict:
    """data/functions.json を {func_id: エントリ} で返す（stat 検証つきキャッシュ）。

    無い・壊れている場合は空 dict（＝hazard 検査はすべて素通り）。⓪より前の段階や
    hazards キーを持たない旧 functions.json でも従来どおり動く（後方互換）。
    """
    path = rootp / "data" / "functions.json"
    if not path.exists():
        return {}
    st = path.stat()
    key, sig = str(path), (st.st_mtime_ns, st.st_size)
    hit = _FUNCS_CACHE.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        idx = {f["func_id"]: f for f in data.get("functions", [])}
    except (json.JSONDecodeError, KeyError, TypeError):
        idx = {}
    _FUNCS_CACHE[key] = (sig, idx)
    return idx


_POLICY_CACHE: dict = {}         # {path: ((mtime_ns, size), 値)}（無いファイルは (None, 値)）


def _hazards_mod():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import hazards
    return hazards


def _ledger_mod():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ledger
    return ledger


def _cached_by_stat(path: Path, build):
    """レンダ時は全 draft ページから呼ばれるので、stat が変わるまで結果を使い回す。"""
    sig = None
    if path.exists():
        st = path.stat()
        sig = (st.st_mtime_ns, st.st_size)
    hit = _POLICY_CACHE.get(str(path))
    if hit and hit[0] == sig:
        return hit[1]
    val = build()
    _POLICY_CACHE[str(path)] = (sig, val)
    return val


# --- テンプレ由来の必須節（固変分離） ---------------------------------------
#
# 項目立て（節構成）はプロジェクト所有の docs/templates/*.md（無ければ同梱シード）が正。
# 必須節はテンプレの「# 見出し」から導出する。見出し行末に <!-- LR:OPTIONAL --> が
# 付いた節（例: 処理フロー）は任意節として必須から外す。
# 固定契約（機械が検証するアンカー: SPEC-ID・hazard 節・トレーサビリティ等）が
# テンプレから欠けている場合は「テンプレ不正」として全対象の問題に載せる。

def _template_required(rootp: Path, kind: str) -> dict:
    """{"heads": [必須節…], "problems": [テンプレ不正…], "path": str}。stat キャッシュ付き。"""
    L = _ledger_mod()
    tpath = L.template_path(rootp, kind)

    def build():
        if not tpath.exists():
            return {"heads": [], "problems": [f"テンプレが無い: {tpath}"], "path": str(tpath)}
        body = L.template_body(tpath)
        problems = []
        if kind == "spec":
            problems += [f"テンプレ不正（{tpath.name}）: {x}"
                         for x in L.spec_template_problems(body)]
        else:
            problems += [f"テンプレ不正（{tpath.name}）: 契約見出しがない: {h}"
                         for h in L.TESTSPEC_CONTRACT_HEADS
                         if not re.search(rf"(?m)^{re.escape(h)}\s*$", body)]
        marked = body.replace("<!-- LR:OPTIONAL -->", "@@LR_OPT@@")
        marked = re.sub(r"(?s)<!--.*?-->", "", marked)      # コメント内の見出しを拾わない
        heads = []
        for line in marked.splitlines():
            m = re.match(r"^(# [^#\s][^\n]*?)\s*$", line)
            if not m:
                continue
            if "@@LR_OPT@@" in m.group(1):
                continue                                     # 任意節（不要なら削除できる）
            heads.append(m.group(1).strip())
        return {"heads": heads, "problems": problems, "path": str(tpath)}

    return _cached_by_stat(tpath, build)


def _policy_eps(rootp: Path) -> set:
    """docs/exception-policy.md に実在する EP-ID の集合。"""
    hz = _hazards_mod()
    path = rootp / hz.POLICY_REL
    return _cached_by_stat(path, lambda: set(hz.parse_policy(rootp).get("ep_ids") or []))


def _hazard_map(rootp: Path) -> dict:
    """data/hazard-map.json（hazards.py match の突合結果）。無ければ空 dict。"""
    hz = _hazards_mod()
    path = rootp / hz.MAP_REL
    return _cached_by_stat(path, lambda: hz.load_hazard_map(rootp))


def _hazard_section(text: str):
    """「例外・数値特異点」節の本文（次の同レベル以上の見出しまで）。節が無ければ None。"""
    m = HAZ_SECTION.search(text)
    if not m:
        return None
    level = len(m.group(1))
    rest = text[m.end():]
    nxt = re.search(r"(?m)^#{1,%d}\s+\S" % level, rest)
    return rest[:nxt.start()] if nxt else rest


def _check_hazards(rootp: Path, func_id: str, text: str, res: dict) -> list:
    """①仕様書の「例外・数値特異点」節を hazards / 例外ポリシーと突合する。

    (a) hazard があるのに節が無い・該当 hz_id の行が無い → 検討漏れ
    (b) 引用 EP-ID が登録簿に実在しない → 捏造
    (c) ポリシー未決定（EP 未割当）の hazard を仕様化している → 決めてから書く
    hazards が1件も無い関数は何も見ない（従来どおり）。
    """
    f = _functions_index(rootp).get(func_id) or {}
    hazards = [h for h in (f.get("hazards") or []) if h.get("hz_id")]
    if not hazards:
        return []
    problems = []
    body = _hazard_section(text)
    if body is None:
        ids = ", ".join(h["hz_id"] for h in hazards[:5])
        more = " ほか" if len(hazards) > 5 else ""
        return [f"「例外・数値特異点」節がない（⓪が検知した hazard が {len(hazards)} 件ある: "
                f"{ids}{more}）＝例外の検討漏れ"]
    mentioned = set(RE_HZ_ID.findall(body))
    for h in hazards:
        if h["hz_id"] not in mentioned:
            problems.append(
                f"{h['hz_id']}（{h.get('kind', '')} {f.get('legacy', {}).get('file', '')}:"
                f"{h.get('line', '')} `{h.get('expr', '')}`）が"
                "「例外・数値特異点」節に無い＝検討漏れ")

    eps = _policy_eps(rootp)
    for ep in sorted(set(RE_EP_ID.findall(body))):
        if ep not in eps:
            problems.append(f"{ep} は docs/exception-policy.md に存在しない"
                            "（EP-ID の捏造。hazards.py add-policy で登録してから引用する）")

    hz_map = _hazard_map(rootp)
    if not hz_map:
        res["warnings"].append(
            "data/hazard-map.json が無いため EP 割当の検証を省略"
            "（`python hazards.py match --root .` を先に実行する）")
    else:
        for hid in sorted(mentioned):
            ent = hz_map.get(hid)
            if ent is not None and not ent.get("ep"):
                problems.append(
                    f"{hid} は例外ポリシー未決定（EP 未割当）のまま仕様化されている"
                    "（docs/exception-queue.md で決定 → hazards.py add-policy → 再突合）")
    return problems


#: 挙動に現れる決定（②に境界ケースが要る）。detect_only / caller_guarantees は
#: この関数のテストで観測できる挙動が無いため対象外
BEHAVIOR_DECISIONS = {"guard_raise", "guard_value", "legacy_preserve"}


def _check_testspec_hazards(rootp: Path, func_id: str, text: str, res: dict) -> list:
    """②テスト仕様を hazards × 例外ポリシーの決定と突合する。

    決定が挙動に現れる hazard（BEHAVIOR_DECISIONS）は、②本文に hz_id を引用した
    境界ケースが無ければ NG（hz_id は①の「例外・数値特異点」節に載っているので、
    ②は legacy を読まずに引用できる＝情報遮断と両立）。
    hazard が無い関数・hazard-map が無い場合は spec 側と同じく素通り/警告のみ。
    """
    f = _functions_index(rootp).get(func_id) or {}
    hazards = [h for h in (f.get("hazards") or []) if h.get("hz_id")]
    if not hazards:
        return []
    hz_map = _hazard_map(rootp)
    if not hz_map:
        res["warnings"].append(
            "data/hazard-map.json が無いため hazard 境界ケースの検証を省略"
            "（`python hazards.py match --root .` を先に実行する）")
        return []
    mentioned = set(RE_HZ_ID.findall(text))
    problems = []
    for h in hazards:
        ent = hz_map.get(h["hz_id"]) or {}
        dec = ent.get("decision")
        if dec in BEHAVIOR_DECISIONS and h["hz_id"] not in mentioned:
            problems.append(
                f"{h['hz_id']}（{h.get('kind', '')}・決定 {dec}）の境界ケースが無い"
                "（①の例外・数値特異点節の hz_id を引用した TC を追加する。"
                "0・0近傍・負値など決定した挙動が観測できる入力）")
    return problems


def _check_citations(root: Path, body: str) -> tuple:
    """本文中の file:lines 引用を検証。(有効数, 問題リスト)"""
    ok, problems = 0, []
    for m in CITE.finditer(body):
        rel, a, b = m.group(1), int(m.group(2)), m.group(3)
        b = int(b) if b else a
        p = root / rel
        if not p.exists():
            problems.append(f"引用先が存在しない: {rel}:{a}" + (f"-{b}" if b != a else ""))
            continue
        n = _line_count(p)
        if a < 1 or b < a or b > n:
            problems.append(f"引用行が範囲外: {rel}:{a}-{b}（実ファイルは {n} 行）")
        else:
            ok += 1
    return ok, problems


def check_spec(root: str, func_id: str) -> dict:
    rootp = Path(root).resolve()
    path = rootp / "docs" / "specs" / f"{func_id}.md"
    res = {"func_id": func_id, "target": "spec", "ok": False,
           "problems": [], "warnings": [], "confidence": {"🟢": 0, "🟡": 0, "🔴": 0}}
    if not path.exists():
        res["problems"].append(f"{path} がない")
        return res
    text = path.read_text(encoding="utf-8-sig")
    fm = _frontmatter(text)
    res["status"] = fm.get("status", "-")

    lf = fm.get("legacy", {}).get("file")
    lh = fm.get("legacy", {}).get("hash")
    if lf and (rootp / lf).exists() and lh and sha8(rootp / lf) != lh:
        res["warnings"].append(
            f"レガシー原本が仕様書作成後に変更されている（legacy.hash 不一致: {lf}）")

    if "```{mermaid}" in text:
        res["problems"].append("```{mermaid} が混入（render_site が落ちる。```mermaid に直す）")
    res["problems"] += check_mermaid_blocks(text)
    if PLACEHOLDER.search(text) and res["status"] != "skeleton":
        res["problems"].append("骨子プレースホルダ（<!-- ①で充填 -->）が残っている＝記入の省略")

    tinfo = _template_required(rootp, "spec")
    res["problems"] += tinfo["problems"]                 # テンプレ不正（人がテンプレを直す）
    for head in tinfo["heads"]:
        if head not in text:
            res["problems"].append(f"必須節がない: {head}（項目立ての正: {tinfo['path']}）")
    m = re.search(r"(?ms)^# 副作用・例外\s*$(.*?)(?=^# |\Z)", text)
    if m and not re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S).strip():
        res["problems"].append("「副作用・例外」が空欄（なければ「なし」と明記する規則）")
    if res["status"] != "skeleton":
        res["problems"] += _check_hazards(rootp, func_id, text, res)

    spec_items = [(sid, head, body) for sid, head, body in _sections(text)
                  if sid and sid.startswith("SPEC-")]
    if not spec_items and res["status"] != "skeleton":
        res["problems"].append("機能詳細に SPEC-xx 項目が1つもない")
    for sid, head, body in spec_items:
        mc = CONF.search(body)
        if not mc:
            res["problems"].append(f"{sid}: Confidence がない")
            continue
        res["confidence"][mc.group(1)] += 1
        nok, cite_problems = _check_citations(rootp, body)
        res["problems"] += [f"{sid}: {p}" for p in cite_problems]
        if mc.group(1) == "🟢" and nok == 0:
            res["problems"].append(
                f"{sid}: 🟢(確認済) なのに有効な根拠引用（file:lines）がない")

    res["ok"] = not res["problems"]
    return res


def check_testspec(root: str, func_id: str) -> dict:
    rootp = Path(root).resolve()
    ts_path = rootp / "docs" / "test-specs" / f"{func_id}.md"
    sp_path = rootp / "docs" / "specs" / f"{func_id}.md"
    res = {"func_id": func_id, "target": "testspec", "ok": False,
           "problems": [], "warnings": [], "pending": 0}
    if not ts_path.exists():
        res["problems"].append(f"{ts_path} がない")
        return res
    text = ts_path.read_text(encoding="utf-8-sig")
    fm = _frontmatter(text)
    res["status"] = fm.get("status", "-")

    if sp_path.exists() and fm.get("spec-hash") and fm["spec-hash"] != sha8(sp_path):
        res["problems"].append("spec-hash が①の現物と不一致（①改訂済み → 本書は要再生成）")
    if "```{mermaid}" in text:
        res["problems"].append("```{mermaid} が混入（render_site が落ちる）")
    res["problems"] += check_mermaid_blocks(text)

    tinfo = _template_required(rootp, "testspec")
    res["problems"] += tinfo["problems"]
    for head in tinfo["heads"]:
        if head not in text:
            res["problems"].append(f"必須節がない: {head}（項目立ての正: {tinfo['path']}）")

    # ①側の SPEC 項目と Confidence
    spec_conf = {}
    if sp_path.exists():
        for sid, head, body in _sections(sp_path.read_text(encoding="utf-8-sig")):
            if sid and sid.startswith("SPEC-"):
                mc = CONF.search(body)
                spec_conf[sid] = mc.group(1) if mc else "?"
    else:
        res["warnings"].append("①仕様書がないためトレーサビリティ検証は部分的")

    # ケース定義
    cases = {}
    for cid, head, body in _sections(text):
        if cid and "TC-" in cid:
            cases[cid] = body
            if "対応仕様" not in body:
                res["problems"].append(f"{cid}: 「対応仕様」行がない")
            mg = re.search(r"期待値の根拠\s*\|\s*(.*?)\s*\|", body)
            if not mg or not mg.group(1).strip():
                res["problems"].append(f"{cid}: 「期待値の根拠」が空欄")
            elif not any(k in mg.group(1) for k in EVIDENCE_KINDS):
                res["problems"].append(
                    f"{cid}: 期待値の根拠が規定外「{mg.group(1)}」"
                    f"（{' / '.join(EVIDENCE_KINDS)} のいずれかにする）")
    if not cases:
        res["problems"].append("テストケース（## xx-TC-xxx）が1つもない")
    res["pending"] = text.count("⚠未確定")
    if res["pending"] and res["status"] == "approved":
        res["problems"].append(f"approved なのに ⚠未確定 が {res['pending']} 件残っている")

    # トレーサビリティマトリクス
    matrix = {}
    mt = re.search(r"(?ms)^#\s*トレーサビリティマトリクス\s*$(.*?)(?=^# |\Z)", text)
    if not mt:
        res["problems"].append("トレーサビリティマトリクスの節がない")
    else:
        for row in re.finditer(r"^\|\s*(SPEC-[\w]+-\d+)\s*\|[^|]*\|([^|]*)\|", mt.group(1), re.M):
            tcs = [t.strip() for t in re.split(r"[,、/\s]+", row.group(2)) if "TC-" in t]
            matrix[row.group(1)] = tcs
        for sid, conf in spec_conf.items():
            if conf == "🟢" and not matrix.get(sid):
                res["problems"].append(f"{sid}: 🟢仕様項目なのにテストケースが割り当てられていない")
        for sid, tcs in matrix.items():
            if spec_conf and sid not in spec_conf:
                res["problems"].append(f"マトリクスの {sid} は①に存在しない（捏造の疑い）")
            for tc in tcs:
                if not any(c.endswith(tc) or tc.endswith(c) for c in cases):
                    res["problems"].append(f"マトリクスの {tc}（{sid}）に対応するケース定義がない")

    res["problems"] += _check_testspec_hazards(rootp, func_id, text, res)

    res["ok"] = not res["problems"]
    return res


def check_all(root: str) -> dict:
    rootp = Path(root).resolve()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ledger import Project
    p = Project(rootp)
    results, ng = [], 0
    for f in p.funcs():
        fid = f["func_id"]
        if (rootp / "docs" / "specs" / f"{fid}.md").exists():
            r = check_spec(root, fid)
            if r.get("status") != "skeleton":
                results.append(r)
                ng += 0 if r["ok"] else 1
        if (rootp / "docs" / "test-specs" / f"{fid}.md").exists():
            r = check_testspec(root, fid)
            results.append(r)
            ng += 0 if r["ok"] else 1
    return {"ok": ng == 0, "checked": len(results), "ng": ng,
            "results": [r for r in results if not r["ok"]]}


def make_report(root: str) -> dict:
    """①draft 仕様書の一斉レビュー表 docs/spec-review.md を生成する。

    バッチ実行（複数関数を連続で draft 化）の後、人がまとめてレビューするための一覧。
    「人がいま動ける行」を上に並べる（承認できる → ISSUEあり → AI修正待ち）。
    表は**閲覧専用**——承認・修正依頼はチャット（「全部OK」「F-xxxx は修正: 〜」）、
    review_actions.py（CLI）、または review-feedback.md への記入で行う。
    """
    rootp = Path(root).resolve()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ledger import Project
    p = Project(rootp)

    open_issues: dict = {}
    for ip in sorted((rootp / "docs" / "issues").glob("ISSUE-*.md")):
        ifm = _frontmatter(ip.read_text(encoding="utf-8-sig"))
        if ifm.get("status") == "open":
            open_issues.setdefault(ifm.get("func-id", ""), []).append(ip.stem)

    rows, ng_funcs = [], []
    for f in p.funcs():
        fid = f["func_id"]
        sp = rootp / "docs" / "specs" / f"{fid}.md"
        if not sp.exists():
            continue
        text = sp.read_text(encoding="utf-8-sig")
        fm = _frontmatter(text)
        if fm.get("status") != "draft":
            continue
        r = check_spec(root, fid)
        if not r["ok"]:
            ng_funcs.append(fid)
        mo = re.search(r"(?ms)^# 概要\s*$(.*?)(?=^# |\Z)", text)
        overview = ""
        if mo:
            body = re.sub(r"<!--.*?-->", "", mo.group(1), flags=re.S).strip()
            # 表のセルに入れるので | はエスケープ（表崩れ防止）
            first = body.splitlines()[0].replace("|", "\\|") if body else ""
            overview = first[:60] + ("…" if len(first) > 60 else "")
        c = r["confidence"]
        conf = " ".join(f"{c[k]}{k}" for k in ("🟢", "🟡", "🔴") if c[k]) or "—"
        # #review-<fid> は render_site.py が仕様書ページに埋め込む案内パネルの位置
        # （機械レビュー結果の全文と、返答方法の案内がそこにある）
        review_link = f"specs/{fid}.html#review-{fid}"
        mech = (f"[✅]({review_link})" if r["ok"]
               else f"[❌ {len(r['problems'])}件]({review_link})")
        iss_ids = open_issues.get(fid, [])
        iss = " ".join(f"[{i}](issues/{i}.md)" for i in iss_ids) or "—"
        name = f["new"].get("name", fid).replace("|", "\\|")
        if r["ok"]:
            act = '<span class="sr-can">承認できる</span>'
        else:
            act = '<span class="sr-wait">AI修正待ち</span>'
        # 並び順: 人がいま動ける行が先（0=承認できる, 1=承認できるがISSUEあり, 2=AI修正待ち）
        group = 2 if not r["ok"] else (1 if iss_ids else 0)
        rows.append((group, f"| [{name}](specs/{fid}.md) | {overview} "
                            f"| {conf} | {mech} | {iss} | {act} |"))
    rows.sort(key=lambda t: t[0])                    # 安定ソート（同グループ内は登録順のまま）

    # page-layout: full — 列の多い表を既定の本文幅に押し込むと1行が縦に伸びて
    # 「件数のわりに数行しか見えない」状態になるため、このページは全幅で使う
    lines = ["---", 'title: "① 仕様書 一斉レビュー"', "date: last-modified",
             "page-layout: full", "---", "",
             "<!-- review_checks.py report による自動生成。手編集禁止 -->", ""]
    if rows:
        lines += [
            f"レビュー待ち（draft）: **{len(rows)} 件**。概要と Confidence を見て、"
            "そのまま承認するか、関数名リンクで仕様書全文を確認してください。", "",
            "返答はチャット（「全部OK」／「F-xxxx は修正: 〜」）、CLI"
            "（`review_actions.py approve spec F-xxxx --by 名前`）、"
            "または docs/review-feedback.md への記入で（すべて同格）。"
            "❌ は AI が自己修正するまで承認できません。", "",
            "| 関数 | 概要 | Confidence | 機械レビュー | 未確定(ISSUE) | 状態 |",
            "|------|------|:---:|:---:|------|------|"] + [r for _, r in rows]
        lines += ["", "```{=html}", SPEC_REVIEW_PAGE_JS, "```"]
    else:
        lines.append("レビュー待ちの仕様書（draft）はありません 🎉")
    out = rootp / "docs" / "spec-review.md"
    out.parent.mkdir(parents=True, exist_ok=True)   # ⓪の骨子生成前に呼ばれても落ちない
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": not ng_funcs, "drafts": len(rows), "machine_ng": len(ng_funcs),
            "machine_ng_funcs": ng_funcs, "path": str(out)}


# 一斉レビュー表のページ内JS（Quarto の ```{=html} ブロックとして焼き込む）。
# フィルタチップ・検索・件数表示を表の直前に注入する**表示専用**のJS
# （サーバへの書き込みは行わない。承認はチャット / CLI / ファイル記入で）。
SPEC_REVIEW_PAGE_JS = """\
<style>
/* 列幅の固定（Quarto既定は内容比の自動配分で、概要列が関数名列を潰す）。
   余り幅はすべて概要列(2)に寄せ、関数名(1)は折り返さない */
#sr-table{table-layout:fixed;width:100%;min-width:58em}
.sr-scroll{overflow-x:auto}
#sr-table th:nth-child(1){width:8em}
#sr-table th:nth-child(3){width:6em}
#sr-table th:nth-child(4){width:6.5em}
#sr-table th:nth-child(5){width:7em}
#sr-table th:nth-child(6){width:12.5em}
#sr-table td{overflow-wrap:break-word;vertical-align:top}
#sr-table td:nth-child(1){white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#sr-table td:nth-child(3),#sr-table td:nth-child(4){white-space:nowrap}
.sr-can{color:#166534;font-weight:600;font-size:.9em}
.sr-wait{color:#9ca3af;font-size:.85em}
.sr-msg{display:block;font-size:.8em;color:#166534}
.sr-bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0}
.sr-chip{border:1px solid #9ca3af66;background:transparent;border-radius:999px;
         padding:2px 12px;font-size:.85em;cursor:pointer;color:inherit}
.sr-chip.on{background:#4f46e5;border-color:#4f46e5;color:#fff}
.sr-search{padding:5px 10px;border:1px solid #9ca3af;border-radius:6px;min-width:260px;
           background:transparent;color:inherit}
.sr-count{color:#9ca3af;font-size:.85em}
</style>
<script>
(function(){
  function setup(){
    var table = null;
    document.querySelectorAll("table").forEach(function(t){
      var h = t.querySelector("thead th, tr th");
      if(h && h.textContent.trim() === "関数") table = t;
    });
    if(!table) return;
    table.id = "sr-table";               // 列幅固定CSS（上の #sr-table）を効かせる
    var wrap = document.createElement("div");   // 画面が狭いときは表だけ横スクロール
    wrap.className = "sr-scroll";
    table.parentNode.insertBefore(wrap, table);
    wrap.appendChild(table);
    var rows = Array.prototype.slice.call(table.querySelectorAll("tbody tr"));
    rows.forEach(function(tr){
      tr.dataset.app = tr.querySelector(".sr-can") ? "1" : "";
      tr.dataset.iss = /ISSUE-/.test(tr.textContent) ? "1" : "";
    });
    var nApp = rows.filter(function(t){ return t.dataset.app === "1"; }).length;
    var nIss = rows.filter(function(t){ return t.dataset.iss === "1"; }).length;
    var bar = document.createElement("div");
    bar.className = "sr-bar";
    var cnt = document.createElement("span");
    cnt.className = "sr-count";
    var state = {mode: "all", q: ""};
    function apply(){
      var n = 0;
      rows.forEach(function(tr){
        var ok = true;
        if(state.mode === "app") ok = tr.dataset.app === "1";
        else if(state.mode === "iss") ok = tr.dataset.iss === "1";
        else if(state.mode === "wait") ok = tr.dataset.app !== "1";
        if(ok && state.q) ok = tr.textContent.toLowerCase().indexOf(state.q) >= 0;
        tr.style.display = ok ? "" : "none";
        if(ok) n++;
      });
      cnt.textContent = n + " / " + rows.length + " 件";
    }
    [["all", "すべて"], ["app", "承認できる " + nApp],
     ["iss", "ISSUEあり " + nIss], ["wait", "AI修正待ち " + (rows.length - nApp)]]
      .forEach(function(cdef){
        var b = document.createElement("button");
        b.className = "sr-chip" + (cdef[0] === "all" ? " on" : "");
        b.textContent = cdef[1];
        b.onclick = function(){
          bar.querySelectorAll(".sr-chip").forEach(function(x){ x.classList.remove("on"); });
          b.classList.add("on"); state.mode = cdef[0]; apply();
        };
        bar.appendChild(b);
      });
    var inp = document.createElement("input");
    inp.className = "sr-search"; inp.type = "search";
    inp.placeholder = "検索: 関数名・概要・ISSUE";
    inp.addEventListener("input", function(){
      state.q = inp.value.trim().toLowerCase(); apply();
    });
    bar.appendChild(inp);
    bar.appendChild(cnt);
    wrap.parentNode.insertBefore(bar, wrap);   // バーはスクロール枠の外（常に見える）
    apply();
  }
  if(document.readyState === "loading") document.addEventListener("DOMContentLoaded", setup);
  else setup();
})();
</script>"""


def _print_result(r: dict) -> None:
    mark = "OK" if r["ok"] else "NG"
    print(f"[{mark}] {r.get('func_id', '')} {r.get('target', '')} (status: {r.get('status', '-')})")
    for w in r.get("warnings", []):
        print(f"  警告: {w}")
    for pr in r.get("problems", []):
        print(f"  NG: {pr}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["spec", "testspec", "all", "report", "template"])
    ap.add_argument("func_id", nargs="?")
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.cmd == "template":
        # プロジェクトテンプレ（docs/templates/）の固定契約チェック。
        # 人がテンプレを編集したあと、骨子生成の前に確認する用
        rootp = Path(args.root).resolve()
        ng = 0
        out = {}
        for kind in ("spec", "testspec"):
            info = _template_required(rootp, kind)
            out[kind] = info
            ng += len(info["problems"])
            if not args.json:
                mark = "OK" if not info["problems"] else "NG"
                print(f"[{mark}] {kind}: {info['path']}")
                for pr in info["problems"]:
                    print(f"  NG: {pr}")
                print(f"  必須節: {' / '.join(info['heads']) or '（なし）'}")
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=1))
        sys.exit(0 if ng == 0 else 1)

    if args.cmd == "report":
        res = make_report(args.root)
        print(json.dumps(res, ensure_ascii=False) if args.json else
              f"drafts={res['drafts']} 機械NG={res['machine_ng']} → {res['path']}")
        sys.exit(0 if res["ok"] else 1)

    if args.cmd == "all":
        res = check_all(args.root)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=1))
        else:
            print(f"checked={res['checked']} NG={res['ng']}")
            for r in res["results"]:
                _print_result(r)
        sys.exit(0 if res["ok"] else 1)

    if not args.func_id:
        sys.exit("error: func_id が必要")
    res = (check_spec if args.cmd == "spec" else check_testspec)(args.root, args.func_id)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        _print_result(res)
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
