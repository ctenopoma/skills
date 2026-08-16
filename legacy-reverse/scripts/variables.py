#!/usr/bin/env python3
"""legacy-reverse 変数辞書エンジン（scripts/variables.py）。

対象プロジェクトのルートで実行する（--root で変更可。サブコマンドの後ろに置いてもよい）。
設計の正は references/graph-dict-design.md の「P2. 変数辞書」「P4. 追加開発の後発合流」。

原則:
- **機械が正、AIは意味づけ**。ノードの列挙・クラスタリング・根拠収集・ルーブリック判定・
  伝搬はすべてこのスクリプトが決定的に行う。LLM の仕事は data/interpretations.json に
  「desc / unit / 引用した ev_id」を書くことだけで、範囲逸脱は verify-interp が弾く
- **人の承認ゲート**。語義の確定は人（approve / revise、またはブラウザの辞書ウィジェット）
- **再 build は常にマージ**。var_id 不変・承認維持（evidence_hash と occurrence 集合で判定）

サブコマンド:
  build                      functions.json＋legacyソースから data/variables.json を生成/マージ
  verify-interp [--ids ...]  data/interpretations.json を機械検証して variables.json にマージ
  list-targets [--limit N]   status=unreviewed の解釈用コンテキストを JSON 出力（LLMバッチ用）
  approve V-0001,V-0002 --by 名前
  revise V-0001 --desc "..." [--unit "..."] --by 名前
  propagate                  approved の desc/unit を functions.json の IO/globals に機械転記
  page                       docs/variables.qmd を生成（自動生成・手編集禁止）
  conflicts                  reviewed 仕様書と辞書の矛盾候補 docs/dict-conflicts.md を生成

ノード＝(func_id, 変数名)。結合は機械的根拠のみ（Union-Find）:
  ① 同一 COMMON ブロックの同一位置  ② call_sites の実引数↔呼び先仮引数（位置対応）
  ③ EQUIVALENCE                     ④ 同一関数スコープ内の同名
**別関数の同名だけでは結合しない**（同名別義は別 var とし相互に name_collision を立てる）。
"""
import argparse
import datetime
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import graph  # 同ディレクトリ。functions.json のロード（エラーメッセージ）を共通化する

FIXED_EXTS = {".f", ".for", ".f77", ".ftn", ".fpp"}
FREE_EXTS = {".f90", ".f95", ".f03", ".f08"}

# 根拠の種別（設計 P2「根拠バンドル」）と、1変数あたりの収集上限
EV_KINDS = ("comment", "format_label", "data_init", "usage_expr", "common_pos")
EV_LIMITS = {"comment": 4, "format_label": 3, "data_init": 3, "usage_expr": 5, "common_pos": 99}
# rank A の根拠（ルーブリック）。common_pos / usage_expr は C 相当
RANK_A_KINDS = {"comment", "format_label", "data_init"}

TEXT_MAX = 160          # 根拠テキストの切り詰め
STATUS_VALUES = ("unreviewed", "interpreted", "approved")

# usage_expr から除外する宣言・制御系の文
RE_NON_EXPR = re.compile(
    r"^(integer|real|double\s*precision|complex|logical|character|byte|dimension|common|"
    r"equivalence|data|parameter|save|implicit|external|intrinsic|format|write|print|read|"
    r"subroutine|function|program|entry|end|return|use|include|namelist|block)\b", re.I)


# ---------- 基盤 ----------

def load_json(path: Path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as e:
            sys.exit(f"error: {path} が JSON として読めない（{e.lineno}行{e.colno}列: {e.msg}）")
    return default if default is not None else {}


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def today() -> str:
    return datetime.date.today().isoformat()


def norm(name) -> str:
    return (name or "").strip().upper()


def word_in(text: str, name: str) -> bool:
    """text 中に name が識別子として（前後が英数字・アンダースコアでなく）現れるか。"""
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])",
                     text, re.I) is not None


def clip(text: str) -> str:
    t = " ".join(text.split())
    return t if len(t) <= TEXT_MAX else t[:TEXT_MAX - 1] + "…"


class UnionFind:
    """素集合データ構造（経路圧縮のみ。数万ノード規模でも十分速い）。"""

    def __init__(self):
        self.parent = {}

    def add(self, x):
        self.parent.setdefault(x, x)
        return x

    def find(self, x):
        self.add(x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


# ---------- Fortran 軽量スキャナ ----------
# extract_fortran.py は触らない（M1a の所有物）ため、辞書に必要な最小限
# （継続行の連結・コメント除去・ラベル保持）だけを自前で持つ。

def _split_inline(line: str):
    """文字列リテラル外の ! で本体とコメントに分ける → (code, comment or None)。"""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "!":
            return line[:i], line[i + 1:].strip()
    return line, None


def _split_top(s: str, sep: str = ","):
    """括弧・文字列の外側の sep で分割する。"""
    out, depth, cur, quote = [], 0, [], None
    for ch in s:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            cur.append(ch)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == sep and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def _top_paren_groups(s: str):
    """最外側の括弧の中身を順に返す（EQUIVALENCE のグループ取り出し用）。"""
    groups, depth, start, quote = [], 0, None, None
    for i, ch in enumerate(s):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            continue
        if ch == "(":
            depth += 1
            if depth == 1:
                start = i + 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and start is not None:
                groups.append(s[start:i])
                start = None
    return groups


def _ident(entity: str):
    m = re.match(r"\s*([A-Za-z]\w*)", entity)
    return m.group(1).upper() if m else None


def _literals(text: str):
    """文字列リテラルの中身を取り出す（'' 形式のエスケープはそのまま）。"""
    return [m.group(1) or m.group(2) or ""
            for m in re.finditer(r"'((?:[^']|'')*)'|\"((?:[^\"]|\"\")*)\"", text)]


class SourceFile:
    """レガシー1ファイルのスキャン結果（文・コメント・同行コメント）。

    stmts は (開始物理行, ラベル or None, 文テキスト) の列。固定形式の継続行は連結し、
    73桁以降は落とす。コメントは evidence の材料なので捨てずに別に持つ。
    """

    def __init__(self, path: Path, rel: str):
        self.path = path
        self.rel = rel
        self.fixed = path.suffix.lower() not in FREE_EXTS
        self.lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        self.comments = {}      # 行番号 -> コメント本文（行全体がコメント）
        self.inline = {}        # 行番号 -> 同行コメント本文
        self.stmts = []
        self._scan_fixed() if self.fixed else self._scan_free()

    def _scan_fixed(self):
        for i, raw in enumerate(self.lines, 1):
            if not raw.strip():
                continue
            # 固定形式の1〜5桁はラベル欄（数字か空白のみ）。それ以外の文字はコメント標識
            if raw[0] not in " 0123456789\t":
                self.comments[i] = raw[1:].strip()
                continue
            if raw.lstrip().startswith("!"):
                self.comments[i] = raw.lstrip()[1:].strip()
                continue
            if "\t" in raw[:6]:                       # タブ形式（DEC 流儀）
                head, _, rest = raw.partition("\t")
                cont = bool(rest[:1].isdigit() and rest[0] != "0")
                label, body = head.strip(), (rest[1:] if cont else rest)
            else:
                label = raw[:5].strip()
                cont = len(raw) > 5 and raw[5] not in " 0"
                body = raw[6:72]
            code, cmt = _split_inline(body)
            if cmt:
                self.inline[i] = cmt
            if not code.strip():
                continue
            if cont and self.stmts:
                ln, lb, prev = self.stmts[-1]
                self.stmts[-1] = (ln, lb, prev + " " + code.strip())
            else:
                self.stmts.append((i, label or None, code.strip()))

    def _scan_free(self):
        pending, pend_ln = None, 0
        for i, raw in enumerate(self.lines, 1):
            s = raw.strip()
            if not s:
                continue
            if s.startswith("!"):
                self.comments[i] = s[1:].strip()
                continue
            code, cmt = _split_inline(raw)
            if cmt:
                self.inline[i] = cmt
            code = code.strip()
            if not code:
                continue
            if pending is not None:
                code = pending + " " + (code[1:].strip() if code.startswith("&") else code)
                ln, pending = pend_ln, None
            else:
                ln = i
            if code.endswith("&"):
                pending, pend_ln = code[:-1].rstrip(), ln
                continue
            for part in _split_top(code, ";"):
                p = part.strip()
                if not p:
                    continue
                m = re.match(r"^(\d+)\s+(.*)$", p)
                self.stmts.append((ln, m.group(1) if m else None,
                                   m.group(2).strip() if m else p))
        if pending:
            self.stmts.append((pend_ln, None, pending))


class Unit:
    """1関数（program unit）の宣言・COMMON・EQUIVALENCE・初期値・使用行。

    範囲は functions.json の legacy.lines（"120-240"）で切る。ソースを再パースして
    ユニット境界を求め直すことはしない（正データは functions.json）。
    """

    def __init__(self, func_id: str, src: SourceFile, start: int, end: int):
        self.func_id = func_id
        self.src = src
        self.stmts = [s for s in src.stmts if start <= s[0] <= end]
        self.decls = {}          # NAME -> (legacy_type, 宣言行)
        self.commons = []        # (block, [NAME...], 行)
        self.common_lines = defaultdict(list)   # NAME -> [行]
        self.equivalences = []   # ([NAME...], 行)
        self.inits = defaultdict(list)          # NAME -> [(行, 文)]
        self.formats = {}        # ラベル -> (行, 文)
        self._analyze()

    def _analyze(self):
        for ln, label, text in self.stmts:
            low = text.lower()
            if label and re.match(r"^format\s*\(", low):
                self.formats[label] = (ln, text)
            md = re.match(r"^(integer|real|double\s*precision|complex|logical|character|byte"
                          r"|dimension)\b(\s*\*\s*\w+)?(\s*\([^)]*\))?\s*(::)?\s*(.*)$",
                          text, re.I)
            if md and not re.search(r"\bfunction\b", low):
                typ = (md.group(1) + (md.group(2) or "")).replace(" ", "")
                for ent in _split_top(md.group(5) or ""):
                    nm = _ident(ent)
                    if nm and nm not in self.decls:
                        self.decls[nm] = (typ if typ.lower() != "dimension" else "", ln)
                continue
            if low.startswith("common"):
                rest = text[len("common"):].strip()
                blocks = re.findall(r"/\s*(\w*)\s*/([^/]*)", rest)
                if not blocks and rest:
                    blocks = [("", rest)]
                for bname, members in blocks:
                    mem = [_ident(x) for x in _split_top(members)]
                    mem = [m for m in mem if m]
                    self.commons.append((bname.upper() or "(無名)", mem, ln))
                    for m in mem:
                        self.common_lines[m].append(ln)
                continue
            if low.startswith("equivalence"):
                for grp in _top_paren_groups(text[len("equivalence"):]):
                    names = [n for n in (_ident(x) for x in _split_top(grp)) if n]
                    if len(names) >= 2:
                        self.equivalences.append((names, ln))
                continue
            if low.startswith("data"):
                # DATA A/1.0/, B/2.0/ → スラッシュで名前群と値群が交互に並ぶ
                parts = text[len("data"):].split("/")
                for i in range(0, len(parts) - 1, 2):
                    for ent in _split_top(parts[i]):
                        nm = _ident(ent)
                        if nm:
                            self.inits[nm].append((ln, text))
                continue
            if low.startswith("parameter"):
                for grp in _top_paren_groups(text[len("parameter"):]) or [text]:
                    for ent in _split_top(grp):
                        m = re.match(r"\s*([A-Za-z]\w*)\s*=", ent)
                        if m:
                            self.inits[m.group(1).upper()].append((ln, text))
                continue


# ---------- ノード収集とクラスタリング ----------

def _lines_range(f: dict, src: SourceFile):
    lines = (f.get("legacy") or {}).get("lines") or ""
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", str(lines))
    if m:
        return int(m.group(1)), int(m.group(2))
    return 1, len(src.lines) if src else 10 ** 9


def _formal_params(callee: dict):
    """呼び先の仮引数リスト（inputs → outputs の並び順。戻り値の擬似エントリは除く）。

    F77 には intent が無いため extract_fortran は仮引数を全て inputs に入れる。
    intent(out) だけが outputs に来るケースでは並び順が実引数とずれ得る（既知の制限）。
    """
    names, seen = [], set()
    ret = norm((callee.get("legacy") or {}).get("name"))
    for row in callee.get("inputs", []):
        nm = norm(row.get("name"))
        if nm and nm not in seen:
            seen.add(nm)
            names.append(nm)
    for row in callee.get("outputs", []):
        nm = norm(row.get("name"))
        if not nm or nm in seen:
            continue
        if row.get("desc") == "戻り値" or nm == ret:
            continue                       # 関数戻り値は仮引数ではない
        seen.add(nm)
        names.append(nm)
    return names


def _is_block_global(name: str) -> bool:
    """globals の擬似エントリ（COMMON ブロック行・USE 行）か。"""
    n = (name or "").strip().upper()
    return n.startswith("COMMON /") or n.startswith("COMMON//") or n.startswith("USE ")


class Builder:
    def __init__(self, root: Path, data: dict):
        self.root = Path(root)
        self.data = data
        self.warnings = []
        self.funcs = [f for f in data.get("functions", []) if not f.get("excluded")]
        self.fmap = {f["func_id"]: f for f in self.funcs}
        self.sources = {}     # rel -> SourceFile | None
        self.units = {}       # func_id -> Unit | None
        self.nodes = {}       # (fid, NAME) -> occurrence dict
        self.uf = UnionFind()
        self.links = []       # (a_key, b_key, kind, line)

    # --- ソース ---
    def source(self, rel: str):
        if rel not in self.sources:
            p = self.root / rel
            if not p.exists():
                self.warnings.append(f"レガシーソースが見つからない: {rel}（根拠収集をスキップ）")
                self.sources[rel] = None
            else:
                try:
                    self.sources[rel] = SourceFile(p, rel)
                except OSError as e:
                    self.warnings.append(f"{rel} を読めない: {e}")
                    self.sources[rel] = None
        return self.sources[rel]

    def unit(self, fid: str):
        if fid not in self.units:
            f = self.fmap[fid]
            rel = (f.get("legacy") or {}).get("file") or ""
            src = self.source(rel) if rel else None
            if src is None:
                self.units[fid] = None
            else:
                start, end = _lines_range(f, src)
                self.units[fid] = Unit(fid, src, start, end)
        return self.units[fid]

    # --- ノード ---
    def add_node(self, fid: str, name: str, role: str = None,
                 legacy_type: str = None, line: int = None):
        name = norm(name)
        if not name or fid not in self.fmap:
            return None
        key = (fid, name)
        occ = self.nodes.get(key)
        if occ is None:
            occ = {"func_id": fid, "name": name, "role": role or "local",
                   "legacy_type": legacy_type or "", "line": line}
            self.nodes[key] = occ
            self.uf.add(key)
        else:
            if role and role != occ["role"]:
                cur, new = occ["role"], role
                if {cur, new} == {"input", "output"}:
                    occ["role"] = "inout"
                elif cur == "local":
                    occ["role"] = new
            if legacy_type and not occ["legacy_type"]:
                occ["legacy_type"] = legacy_type
            if line and not occ["line"]:
                occ["line"] = line
        return key

    def collect_nodes(self):
        for f in self.funcs:
            fid = f["func_id"]
            u = self.unit(fid)
            for row in f.get("inputs", []):
                self.add_node(fid, row.get("name"), "input", row.get("legacy_type"))
            for row in f.get("outputs", []):
                self.add_node(fid, row.get("name"), "output", row.get("legacy_type"))
            for row in f.get("globals", []):
                if not _is_block_global(row.get("name")):
                    self.add_node(fid, row.get("name"), "global")
            if u is None:
                continue
            for block, members, ln in u.commons:
                for m in members:
                    self.add_node(fid, m, "global", u.decls.get(m, ("", None))[0], ln)
            for names, ln in u.equivalences:
                for m in names:
                    self.add_node(fid, m, None, u.decls.get(m, ("", None))[0], ln)
            # 宣言行・型の補完
            for key in [k for k in self.nodes if k[0] == fid]:
                typ, dln = u.decls.get(key[1], ("", None))
                occ = self.nodes[key]
                if typ and not occ["legacy_type"]:
                    occ["legacy_type"] = typ
                if dln and not occ["line"]:
                    occ["line"] = dln
        # call_sites の実引数（呼び出し元スコープの変数）
        for f in self.funcs:
            for site in f.get("call_sites", []) or []:
                for a in site.get("args", []) or []:
                    if a:
                        self.add_node(f["func_id"], a, None, None, site.get("line"))

    # --- 結合 ---
    def link(self, a, b, kind, line=None):
        if a is None or b is None or a == b:
            return
        self.uf.union(a, b)
        self.links.append((a, b, kind, line))

    def join_common(self):
        """① 同一 COMMON ブロックの同一位置。位置は同一ユニット内の COMMON 文を通し番号で数える。"""
        slots = defaultdict(list)   # (block, pos) -> [key]
        for f in self.funcs:
            fid = f["func_id"]
            u = self.unit(fid)
            if u is None:
                continue
            pos_by_block = defaultdict(int)
            for block, members, ln in u.commons:
                for m in members:
                    pos = pos_by_block[block]
                    pos_by_block[block] += 1
                    slots[(block, pos)].append((fid, m))
        for (block, pos), keys in sorted(slots.items()):
            uniq = list(dict.fromkeys(keys))
            for other in uniq[1:]:
                self.link(uniq[0], other, "common", None)
        self.common_slots = slots

    def join_call_bindings(self):
        """② call_sites の実引数 ↔ 呼び先仮引数（位置対応）。args[i] が null なら結線しない。"""
        for f in self.funcs:
            caller = f["func_id"]
            for site in f.get("call_sites", []) or []:
                callee_id = site.get("callee")
                callee = self.fmap.get(callee_id)
                if callee is None:
                    continue                     # 未解決の呼び出し名は結線しない
                formals = _formal_params(callee)
                for i, actual in enumerate(site.get("args", []) or []):
                    if not actual or i >= len(formals):
                        continue
                    a = (caller, norm(actual))
                    b = (callee_id, formals[i])
                    if a in self.nodes and b in self.nodes:
                        self.link(a, b, "call_binding", site.get("line"))

    def join_equivalence(self):
        """③ EQUIVALENCE。同一グループ内の変数は同一実体。"""
        for f in self.funcs:
            fid = f["func_id"]
            u = self.unit(fid)
            if u is None:
                continue
            for names, ln in u.equivalences:
                base = (fid, names[0])
                for n in names[1:]:
                    self.link(base, (fid, n), "equivalence", ln)

    # --- 根拠 ---
    def evidence_for(self, occ: dict):
        """1 occurrence 分の根拠を kind ごとに集める（ev_id は後段で採番）。"""
        fid, name = occ["func_id"], occ["name"]
        u = self.unit(fid)
        if u is None:
            return []
        src, rel = u.src, u.src.rel
        out = []

        def push(kind, line, text):
            t = clip(text)
            if t:
                out.append({"kind": kind, "file": rel, "line": line, "text": t})

        # common_pos: COMMON 名と位置
        for block, members, ln in u.commons:
            if name in members:
                pos = members.index(name)
                push("common_pos", ln, f"COMMON /{block}/ 位置{pos + 1}: {', '.join(members)}")

        # comment: 宣言行・COMMON 行の同行コメントと直上の連続コメント、
        #          および同一ユニット内で変数名に言及しているコメント行
        anchors = []
        dln = u.decls.get(name, ("", None))[1]
        if dln:
            anchors.append(dln)
        anchors += u.common_lines.get(name, [])
        seen_lines = set()
        for a in anchors:
            if a in src.inline and a not in seen_lines:
                seen_lines.add(a)
                push("comment", a, src.inline[a])
            k, taken = a - 1, 0
            while k >= 1 and k in src.comments and taken < 3:
                if k not in seen_lines and src.comments[k].strip():
                    seen_lines.add(k)
                    push("comment", k, src.comments[k])
                k -= 1
                taken += 1
        # 同一ユニット内で「NAME: 説明」形式の見出しコメント（宣言から離れていても拾う）。
        # 単に名前を含むだけのコメントは別変数の説明であることが多いので採らない
        lo, hi = (u.stmts[0][0], u.stmts[-1][0]) if u.stmts else (0, 0)
        head = re.compile(r"^\s*" + re.escape(name) + r"\s*[:：=＝-]", re.I)
        for ln in sorted(src.comments):
            if lo <= ln <= hi and ln not in seen_lines and head.match(src.comments[ln]):
                seen_lines.add(ln)
                push("comment", ln, src.comments[ln])

        # data_init: DATA / PARAMETER
        for ln, text in u.inits.get(name, []):
            push("data_init", ln, text)

        # format_label: WRITE/PRINT（変数と同文にあるもの）＋参照している FORMAT の文字列
        for ln, label, text in u.stmts:
            low = text.lower()
            if not re.match(r"^(write|print)\b", low) or not word_in(text, name):
                continue
            if _literals(text):
                push("format_label", ln, text)
            for lab in self._format_labels(text):
                if lab in u.formats:
                    fln, ftext = u.formats[lab]
                    if _literals(ftext):
                        push("format_label", fln, ftext)

        # usage_expr: 代入・式での使用行
        for ln, label, text in u.stmts:
            if RE_NON_EXPR.match(text) or not word_in(text, name):
                continue
            push("usage_expr", ln, text)
        return out

    @staticmethod
    def _format_labels(text: str):
        """WRITE(6,100) / WRITE(UNIT=6,FMT=100) / PRINT 100 から FORMAT ラベルを拾う。"""
        labs = []
        m = re.match(r"^\s*print\s+(\d+)", text, re.I)
        if m:
            labs.append(m.group(1))
        groups = _top_paren_groups(text)
        if groups:
            for item in _split_top(groups[0]):
                it = item.strip()
                mm = re.match(r"^(?:fmt\s*=\s*)?(\d+)$", it, re.I)
                if mm:
                    labs.append(mm.group(1))
        return labs

    # --- クラスタ組み立て ---
    def clusters(self):
        groups = defaultdict(list)
        for key in self.nodes:
            groups[self.uf.find(key)].append(key)
        link_by_root = defaultdict(list)
        for a, b, kind, line in self.links:
            link_by_root[self.uf.find(a)].append(
                {"kind": kind, "from": f"{a[0]}:{a[1]}", "to": f"{b[0]}:{b[1]}", "line": line})

        out = []
        for root, keys in groups.items():
            keys.sort()
            occs = [self.nodes[k] for k in keys]
            counts = Counter(k[1] for k in keys)
            # canonical: 出現数最多 → 長い名前（情報量が多い）→ 辞書順
            canonical = sorted(counts, key=lambda n: (-counts[n], -len(n), n))[0]
            evs = []
            for occ in occs:
                evs += self.evidence_for(occ)
            evs = self._dedup_limit(evs)
            links = sorted(link_by_root.get(root, []),
                           key=lambda l: (l["kind"], l["from"], l["to"]))
            out.append({
                "occ_keys": {f"{k[0]}:{k[1]}" for k in keys},
                "canonical_name": canonical,
                "aliases": sorted(counts),
                "occurrences": [dict(o) for o in occs],
                "links": links,
                "raw_evidence": evs,
            })
        out.sort(key=lambda c: (c["canonical_name"], sorted(c["occ_keys"])[0]))
        return out

    @staticmethod
    def _dedup_limit(evs):
        seen, kept, per_kind = set(), [], Counter()
        order = {k: i for i, k in enumerate(EV_KINDS)}
        for e in sorted(evs, key=lambda e: (order.get(e["kind"], 9), e["file"], e["line"] or 0)):
            sig = (e["kind"], e["file"], e["line"], e["text"])
            if sig in seen:
                continue
            if per_kind[e["kind"]] >= EV_LIMITS.get(e["kind"], 5):
                continue
            seen.add(sig)
            per_kind[e["kind"]] += 1
            kept.append(e)
        return kept

    def run(self):
        self.collect_nodes()
        self.join_common()
        self.join_call_bindings()
        self.join_equivalence()
        return self.clusters()


# ---------- マージ（P4: 後発合流） ----------

def _var_num(var_id: str) -> int:
    m = re.search(r"(\d+)$", var_id or "")
    return int(m.group(1)) if m else 0


def _evidence_with_ids(var_id: str, raw_evs):
    num = f"{_var_num(var_id):04d}"
    return [{"ev_id": f"E-{num}-{i:02d}", **e} for i, e in enumerate(raw_evs, 1)]


def _evidence_hash(evs) -> str:
    payload = json.dumps([[e["ev_id"], e["kind"], e["file"], e["line"], e["text"]] for e in evs],
                         ensure_ascii=False, sort_keys=True)
    return sha8(payload)


def merge_clusters(clusters, old_vars):
    """クラスタ群を既存 variables.json とマージして variables 配列を作る。

    var_id は occurrence 集合の対応付けで維持する（設計 P4）:
      - evidence_hash 不変        → status / desc / 承認情報を維持
      - 出現が増えただけ          → 維持 ＋ flags:["occurrence_added"]
      - クラスタの分裂・併合      → status を unreviewed に戻し flags:["cluster_changed"]
      - 出現同一で根拠が変わった  → status を unreviewed に戻し flags:["evidence_changed"]
    """
    old_by_id = {v.get("var_id"): v for v in old_vars if v.get("var_id")}
    old_occ = {vid: {f"{o['func_id']}:{o['name']}" for o in v.get("occurrences", [])}
               for vid, v in old_by_id.items()}
    owner = {}
    for vid, keys in old_occ.items():
        for k in keys:
            owner.setdefault(k, vid)

    # 1) 重なりの大きい順に var_id を引き継ぐ
    cand = []
    for j, c in enumerate(clusters):
        c["_old_counts"] = Counter(owner[k] for k in c["occ_keys"] if k in owner)
        for vid, n in c["_old_counts"].items():
            cand.append((n, j, vid))
    cand.sort(key=lambda t: (-t[0], t[1], t[2]))
    assigned, used = {}, set()
    for n, j, vid in cand:
        if j in assigned or vid in used:
            continue
        assigned[j] = vid
        used.add(vid)
    next_num = max([_var_num(v) for v in old_by_id] or [0]) + 1
    for j in range(len(clusters)):
        if j not in assigned:
            assigned[j] = f"V-{next_num:04d}"
            next_num += 1

    # 2) 旧 var の出現が今回どの新クラスタに散ったか（分裂検知）
    spread = defaultdict(set)
    for j, c in enumerate(clusters):
        for k in c["occ_keys"]:
            if k in owner:
                spread[owner[k]].add(j)

    out = []
    for j, c in enumerate(clusters):
        vid = assigned[j]
        old = old_by_id.get(vid)
        evs = _evidence_with_ids(vid, c["raw_evidence"])
        ehash = _evidence_hash(evs)
        v = {
            "var_id": vid,
            "canonical_name": c["canonical_name"],
            "aliases": c["aliases"],
            "desc": "", "unit": None,
            "status": "unreviewed",
            "rank": None,
            "confidence_basis": [],
            "occurrences": c["occurrences"],
            "links": c["links"],
            "evidence": evs,
            "evidence_hash": ehash,
            "approved_by": None, "approved_date": None,
            "flags": [],
        }
        if old is not None:
            merged = len([1 for n in c["_old_counts"].values() if n > 0]) > 1
            split = len(spread.get(vid, ())) > 1
            oldset, newset = old_occ.get(vid, set()), c["occ_keys"]
            keep = dict(desc=old.get("desc", ""), unit=old.get("unit"),
                        rank=old.get("rank"),
                        confidence_basis=old.get("confidence_basis", []))
            if merged or split or not (newset >= oldset or newset <= oldset):
                v.update(keep)
                v["status"] = "unreviewed"
                v["flags"].append("cluster_changed")
            elif newset == oldset and old.get("evidence_hash") == ehash:
                v.update(keep)
                v["status"] = old.get("status", "unreviewed")
                v["approved_by"] = old.get("approved_by")
                v["approved_date"] = old.get("approved_date")
            elif newset > oldset:
                v.update(keep)
                v["status"] = old.get("status", "unreviewed")
                v["approved_by"] = old.get("approved_by")
                v["approved_date"] = old.get("approved_date")
                v["flags"].append("occurrence_added")
            elif newset < oldset:
                v.update(keep)
                v["status"] = old.get("status", "unreviewed")
                v["approved_by"] = old.get("approved_by")
                v["approved_date"] = old.get("approved_date")
                v["flags"].append("occurrence_removed")
            else:                                   # 出現同一・根拠だけ変化
                v.update(keep)
                v["status"] = "unreviewed"
                v["flags"].append("evidence_changed")
        out.append(v)

    # 3) 同名別クラスタは相互に name_collision
    by_name = defaultdict(set)
    for i, v in enumerate(out):
        for a in v["aliases"]:
            by_name[a].add(i)
    for name, idxs in by_name.items():
        if len(idxs) > 1:
            for i in idxs:
                if "name_collision" not in out[i]["flags"]:
                    out[i]["flags"].append("name_collision")
    for v in out:
        v["flags"].sort()
    out.sort(key=lambda v: _var_num(v["var_id"]))
    return out


# ---------- サブコマンド: build ----------

def cmd_build(root: Path, args) -> None:
    data = graph.load_functions(root)
    b = Builder(root, data)
    clusters = b.run()
    vpath = root / "data" / "variables.json"
    old = load_json(vpath, {}).get("variables", [])
    variables = merge_clusters(clusters, old)
    save_json(vpath, {"variables": variables})

    for w in b.warnings:
        print(f"警告: {w}")
    rank = Counter(v["rank"] or "-" for v in variables)
    status = Counter(v["status"] for v in variables)
    collide = sum(1 for v in variables if "name_collision" in v["flags"])
    changed = sum(1 for v in variables if "cluster_changed" in v["flags"])
    added = sum(1 for v in variables if "occurrence_added" in v["flags"])
    print(f"変数 {len(variables)}件（既存 {len(old)}件からマージ） → {vpath}")
    print("  rank : " + (" ".join(f"{k}={rank[k]}" for k in ("A", "B", "C", "D", "-")
                                  if rank[k]) or "（なし）"))
    print("  status: " + (" ".join(f"{k}={status[k]}" for k in STATUS_VALUES
                                   if status[k]) or "（なし）"))
    print(f"  同名別義(name_collision): {collide}件 / "
          f"クラスタ変化: {changed}件 / 出現追加: {added}件")


# ---------- サブコマンド: verify-interp ----------

RE_TOKEN = re.compile(r"[0-9A-Za-z_一-龥ぁ-んァ-ヶーｦ-ﾟ]{2,}")


def desc_tokens(desc: str):
    """desc の主要語（2文字以上。ひらがなのみ・数字のみの語は助詞等なので落とす）。"""
    out = []
    for t in RE_TOKEN.findall(desc or ""):
        if re.fullmatch(r"[ぁ-ん]+", t) or re.fullmatch(r"[0-9]+", t):
            continue
        out.append(t)
    return out


def parse_ids(spec: str):
    """--ids の指定を展開する。"V-0001,V-0003" / "V-0001..V-0005" 形式。"""
    ids = []
    for tok in (spec or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        if ".." in tok:
            a, _, b = tok.partition("..")
            na, nb = _var_num(a.strip()), _var_num(b.strip())
            if na and nb and na <= nb:
                ids += [f"V-{n:04d}" for n in range(na, nb + 1)]
                continue
            sys.exit(f"error: --ids の範囲指定が不正: {tok}")
        ids.append(tok.upper())
    return list(dict.fromkeys(ids))


def decide_rank(var: dict, cited: list, dk_text: str, approved_by_id: dict):
    """rank は LLM の申告ではなく検証側が根拠種別から決める（設計 P2 のルーブリック）。"""
    ev_by_id = {e["ev_id"]: e for e in var.get("evidence", [])}
    kinds = {ev_by_id[c]["kind"] for c in cited if c in ev_by_id}
    if kinds & RANK_A_KINDS:
        return "A", "comment/format_label/data_init を引用"
    toks = desc_tokens(var.get("desc") or "")
    hit = [t for t in toks if dk_text and t in dk_text]
    if hit:
        return "B", f"domain-knowledge.md の語を引用（{', '.join(hit[:3])}）"
    for l in var.get("links", []):
        for side in ("from", "to"):
            other = approved_by_id.get(l.get(side))
            if other and other["var_id"] != var["var_id"]:
                otoks = desc_tokens(other.get("desc") or "")
                if any(t in (var.get("desc") or "") for t in otoks):
                    return "B", f"links で結ばれた承認済み変数 {other['var_id']} を引用"
    if kinds:
        return "C", "usage_expr / common_pos のみ"
    return "D", "引用なし"


def cmd_verify_interp(root: Path, args) -> None:
    vpath = root / "data" / "variables.json"
    store = load_json(vpath, None)
    if not store:
        sys.exit(f"error: {vpath} が無い（先に `variables.py build` を実行する）")
    variables = store.get("variables", [])
    by_id = {v["var_id"]: v for v in variables}

    ipath = root / "data" / "interpretations.json"
    interp = load_json(ipath, None)
    if interp is None:
        sys.exit(f"error: {ipath} が無い（LLM の解釈バッチ出力を置く）")
    if not isinstance(interp, dict):
        sys.exit(f"error: {ipath} は {{var_id: {{...}}}} 形式の辞書でなければならない")

    if args.ids:
        targets = parse_ids(args.ids)
        unknown = [i for i in targets if i not in by_id]
        if unknown:
            sys.exit(f"error: --ids に variables.json 未定義の var_id: {', '.join(unknown)}")
    else:
        targets = [v["var_id"] for v in variables if v["status"] == "unreviewed"]
    target_set = set(targets)

    ng = []
    missing = sorted(target_set - set(interp))
    extra = sorted(set(interp) - target_set)
    for m in missing:
        ng.append(f"欠落: {m} の解釈が interpretations.json に無い")
    for e in extra:
        ng.append(f"余剰: {e} は今回の対象外（対象は {len(target_set)}件）")

    dk_path = root / "docs" / "domain-knowledge.md"
    dk_text = dk_path.read_text(encoding="utf-8-sig") if dk_path.exists() else ""
    approved_by_id = {}
    for v in variables:
        if v["status"] == "approved":
            for o in v["occurrences"]:
                approved_by_id[f"{o['func_id']}:{o['name']}"] = v

    plan = []
    for vid in targets:
        ent = interp.get(vid)
        if ent is None:
            continue
        if not isinstance(ent, dict):
            ng.append(f"形式NG: {vid} はオブジェクトでない")
            continue
        desc = (ent.get("desc") or "").strip()
        if not desc:
            ng.append(f"形式NG: {vid} の desc が空")
            continue
        cited = ent.get("evidence_cited") or []
        if not isinstance(cited, list):
            ng.append(f"形式NG: {vid} の evidence_cited が配列でない")
            continue
        var = by_id[vid]
        own = {e["ev_id"] for e in var.get("evidence", [])}
        bogus = [c for c in cited if c not in own]
        if bogus:
            ng.append(f"引用NG: {vid} の evidence_cited {', '.join(bogus)} は"
                      f"この変数の根拠に存在しない（捏造・取り違え）")
            continue
        plan.append((vid, ent, desc, cited))

    if ng:
        print(f"検証NG {len(ng)}件（マージは行わない）:")
        for m in ng:
            print(f"  - {m}")
        sys.exit(1)

    ranks = Counter()
    applied, rejected = [], []
    for vid, ent, desc, cited in plan:
        var = by_id[vid]
        var_probe = dict(var)
        var_probe["desc"] = desc
        rank, why = decide_rank(var_probe, cited, dk_text, approved_by_id)
        ranks[rank] += 1
        if rank == "D":
            var["rank"] = "D"
            if not var.get("desc"):
                var["desc"] = "不明"
            var["status"] = "unreviewed"
            if "needs_human" not in var["flags"]:
                var["flags"].append("needs_human")
            rejected.append((vid, why))
            continue
        var["desc"] = desc
        unit = ent.get("unit")
        var["unit"] = unit if unit not in ("", "null") else None
        var["rank"] = rank
        var["confidence_basis"] = list(cited)
        var["status"] = "interpreted"
        if "needs_human" in var["flags"]:
            var["flags"].remove("needs_human")
        claim = (ent.get("rank_claim") or "").strip().upper()
        applied.append((vid, rank, why, claim))

    save_json(vpath, {"variables": variables})
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    dest = root / "data" / f"interpretations-applied-{stamp}.json"
    save_json(dest, interp)
    ipath.unlink()

    print(f"対象 {len(target_set)}件 / マージ {len(applied)}件 / 拒否(D) {len(rejected)}件")
    print("  rank: " + " ".join(f"{k}={ranks[k]}" for k in ("A", "B", "C", "D") if ranks[k]))
    for vid, rank, why, claim in applied:
        note = "" if claim in ("", rank) else f"（LLM申告 {claim} → 検証 {rank}）"
        print(f"  {vid} rank={rank} {why}{note}")
    for vid, why in rejected:
        print(f"  {vid} rank=D 拒否: {why} → desc は「不明」で人のキューに残す")
    print(f"  applied: {dest.name}（interpretations.json は消費した）")


# ---------- サブコマンド: list-targets ----------

def cmd_list_targets(root: Path, args) -> None:
    store = load_json(root / "data" / "variables.json", None)
    if not store:
        sys.exit("error: data/variables.json が無い（先に `variables.py build`）")
    data = graph.load_functions(root)
    fmap = {f["func_id"]: f for f in data.get("functions", [])}
    variables = store.get("variables", [])
    if args.ids:
        want = set(parse_ids(args.ids))
        pick = [v for v in variables if v["var_id"] in want]
    else:
        pick = [v for v in variables if v["status"] == "unreviewed"]
    pick = pick[:args.limit]

    out = []
    for v in pick:
        occs = []
        for o in v["occurrences"]:
            f = fmap.get(o["func_id"], {})
            occs.append({
                "func_id": o["func_id"],
                "legacy_name": (f.get("legacy") or {}).get("name", ""),
                "legacy_file": (f.get("legacy") or {}).get("file", ""),
                "name": o["name"], "role": o["role"],
                "legacy_type": o.get("legacy_type", ""), "line": o.get("line"),
            })
        out.append({
            "var_id": v["var_id"],
            "canonical_name": v["canonical_name"],
            "aliases": v["aliases"],
            "occurrences": occs,
            "links": v["links"],
            "evidence": v["evidence"],
            "flags": v["flags"],
        })
    print(json.dumps(out, ensure_ascii=False, indent=1))


# ---------- サブコマンド: approve / revise ----------

def _save_vars(root: Path, variables) -> None:
    save_json(root / "data" / "variables.json", {"variables": variables})


def _load_vars(root: Path):
    store = load_json(root / "data" / "variables.json", None)
    if not store:
        sys.exit("error: data/variables.json が無い（先に `variables.py build`）")
    return store.get("variables", [])


def cmd_approve(root: Path, args) -> None:
    variables = _load_vars(root)
    by_id = {v["var_id"]: v for v in variables}
    ids = parse_ids(args.ids)
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        sys.exit(f"error: 未定義の var_id: {', '.join(unknown)}")
    for vid in ids:
        v = by_id[vid]
        if not (v.get("desc") or "").strip() or v.get("desc") == "不明":
            sys.exit(f"error: {vid} は desc が未確定（`revise {vid} --desc ...` で確定させる）")
        v["status"] = "approved"
        v["approved_by"] = args.by
        v["approved_date"] = today()
    _save_vars(root, variables)
    print(f"承認 {len(ids)}件: {', '.join(ids)}（{args.by} / {today()}）")


def cmd_revise(root: Path, args) -> None:
    variables = _load_vars(root)
    by_id = {v["var_id"]: v for v in variables}
    vid = args.var_id.upper()
    if vid not in by_id:
        sys.exit(f"error: 未定義の var_id: {vid}")
    v = by_id[vid]
    v["desc"] = args.desc
    if args.unit is not None:
        v["unit"] = args.unit or None
    v["status"] = "approved"
    v["approved_by"] = args.by
    v["approved_date"] = today()
    if "needs_human" in v["flags"]:
        v["flags"].remove("needs_human")
    _save_vars(root, variables)
    unit = f"（{v['unit']}）" if v.get("unit") else ""
    print(f"修正＋承認 {vid}: {v['desc']}{unit}（{args.by} / {today()}）")


# ---------- サブコマンド: propagate ----------

def _dict_desc(v: dict) -> str:
    """転記の書式: "<desc> [V-0001]" / unit があれば "<desc>(<unit>) [V-0001]"。"""
    unit = v.get("unit")
    base = v.get("desc") or ""
    return f"{base}({unit}) [{v['var_id']}]" if unit else f"{base} [{v['var_id']}]"


def cmd_propagate(root: Path, args) -> None:
    variables = _load_vars(root)
    fpath = root / "data" / "functions.json"
    data = graph.load_functions(root)
    fmap = {f["func_id"]: f for f in data.get("functions", [])}
    n_io, n_glob, skipped = 0, 0, 0

    for v in variables:
        if v.get("status") != "approved" or not (v.get("desc") or "").strip():
            continue
        text = _dict_desc(v)
        ref = f"[{v['var_id']}]"
        for o in v["occurrences"]:
            f = fmap.get(o["func_id"])
            if f is None:
                continue
            hit = False
            for field in ("inputs", "outputs", "globals"):
                for row in f.get(field, []) or []:
                    if norm(row.get("name")) != o["name"]:
                        continue
                    hit = True
                    if row.get("desc") == text:
                        continue                      # 冪等
                    row["desc"] = text
                    n_io += 1
            if hit:
                continue
            # COMMON メンバーは globals がブロック単位の擬似エントリ（desc がメンバー一覧）
            # なので、そのブロック行に「メンバー名=意味 [V-xxxx]」を追記する
            placed = False
            for row in f.get("globals", []) or []:
                nm = (row.get("name") or "").strip().upper()
                if not nm.startswith("COMMON /"):
                    continue
                if not word_in(row.get("desc") or "", o["name"]):
                    continue
                if ref in (row.get("desc") or ""):
                    placed = True
                    continue                          # 冪等（同じ参照が既にある）
                row["desc"] = (row.get("desc") or "").rstrip() + f" ; {o['name']}={text}"
                n_glob += 1
                placed = True
            if not placed:
                skipped += 1

    save_json(fpath, data)
    print(f"転記: inputs/outputs/globals の desc {n_io}件 / COMMON ブロック注記 {n_glob}件"
          f"（転記先が見つからなかった出現 {skipped}件）")
    print("  ※ skeletons / WBS の再生成は呼び出し側（ledger.py）の仕事")


# ---------- サブコマンド: page ----------

RANK_ORDER = {None: 0, "": 0, "C": 1, "D": 2, "B": 3, "A": 4}


def _sort_key(v: dict):
    nfunc = len({o["func_id"] for o in v["occurrences"]})
    return (-nfunc, RANK_ORDER.get(v.get("rank"), 0), _var_num(v["var_id"]))


def _row(v: dict) -> str:
    names = v["canonical_name"]
    others = [a for a in v["aliases"] if a != v["canonical_name"]]
    if others:
        names += f"（{', '.join(others)}）"
    occ_fids = sorted({o["func_id"] for o in v["occurrences"]})
    links = ", ".join(f"[{fid}](specs/{fid}.md)" for fid in occ_fids)
    desc = (v.get("desc") or "").replace("|", "\\|") or "—"
    unit = (v.get("unit") or "—").replace("|", "\\|")
    flags = ", ".join(v.get("flags") or []) or "—"
    return (f"| [{v['var_id']}]{{#dict-{v['var_id']}}} | {names} | {desc} | {unit} | "
            f"{v.get('rank') or '—'} | {v['status']} | {links} | {flags} |")


def cmd_page(root: Path, args) -> None:
    variables = _load_vars(root)
    approved = [v for v in variables if v["status"] == "approved"]
    pending = [v for v in variables if v["status"] != "approved"]
    queue = [v for v in pending if RANK_ORDER.get(v.get("rank"), 0) <= 2]     # 未判定・C・D
    batch = [v for v in pending if RANK_ORDER.get(v.get("rank"), 0) >= 3]     # A・B
    for lst in (queue, batch, approved):
        lst.sort(key=_sort_key)

    head = ("| var_id | 名前（別名） | 意味 | 単位 | rank | status | 出現 | フラグ |\n"
            "|---|---|---|---|:---:|:---:|---|---|")
    out = ["---", 'title: "変数辞書"', "---", "",
           "<!--",
           "本書は data/variables.json から variables.py page が自動生成する。手編集禁止。",
           "並び順: 出現関数数 降順 × rank 昇順（C/D が先＝人の確認が必要なものが上）。",
           "各行の var_id には #dict-V-xxxx のアンカーがある（承認ウィジェットの埋め込み先）。",
           "-->", "",
           f"変数 {len(variables)}件（承認済み {len(approved)} / 確認待ち {len(queue)} / "
           f"一括承認候補 {len(batch)}）", ""]

    out += ["# 確認待ち（rank C/D・未判定）", "",
            "<!-- 根拠が弱く、人が1件ずつ desc/unit を確定させる対象 -->", ""]
    out += [head] + [_row(v) for v in queue] if queue else ["（なし）"]
    out += ["", "# 一括承認候補（rank A/B）", "",
            "<!-- 明示的な根拠（コメント・FORMAT・初期値・ドメイン知識）がある対象 -->", ""]
    out += [head] + [_row(v) for v in batch] if batch else ["（なし）"]
    out += ["", "# 承認済み", ""]
    out += [head] + [_row(v) for v in approved] if approved else ["（なし）"]

    collide = [v for v in variables if "name_collision" in v.get("flags", [])]
    if collide:
        out += ["", "# 同名別義（name_collision）", "",
                "<!-- 名前は同じだが機械的根拠では別実体と判定されたもの。併記して取り違えを防ぐ -->",
                "", "| 名前 | var_id | 意味 | 出現 |", "|---|---|---|---|"]
        for v in sorted(collide, key=lambda x: (x["canonical_name"], _var_num(x["var_id"]))):
            fids = ", ".join(sorted({o["func_id"] for o in v["occurrences"]}))
            out.append(f"| {v['canonical_name']} | {v['var_id']} | "
                       f"{(v.get('desc') or '—')} | {fids} |")
    out.append("")

    dest = root / "docs" / "variables.qmd"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(out), encoding="utf-8")
    print(f"生成: {dest}（確認待ち {len(queue)} / 一括承認候補 {len(batch)} / "
          f"承認済み {len(approved)}）")


# ---------- サブコマンド: conflicts ----------

def _frontmatter_status(text: str):
    m = re.search(r"^---\s*$(.*?)^---\s*$", text, re.M | re.S)
    if not m:
        return None
    ms = re.search(r"^status:\s*(\S+)", m.group(1), re.M)
    return ms.group(1).strip().strip('"').strip("'") if ms else None


def cmd_conflicts(root: Path, args) -> None:
    variables = _load_vars(root)
    approved = [v for v in variables if v["status"] == "approved"]
    specs_dir = root / "docs" / "specs"
    rows = []
    checked = 0
    for v in approved:
        for fid in sorted({o["func_id"] for o in v["occurrences"]}):
            p = specs_dir / f"{fid}.md"
            if not p.exists():
                continue
            text = p.read_text(encoding="utf-8-sig")
            if _frontmatter_status(text) != "reviewed":
                continue
            checked += 1
            names = {o["name"] for o in v["occurrences"] if o["func_id"] == fid}
            hits = []
            for i, line in enumerate(text.splitlines(), 1):
                if not line.lstrip().startswith("|"):
                    continue
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if any(norm(c) in names for c in cells):
                    hits.append((i, line.strip()))
            if not hits:
                rows.append((fid, v, "該当行なし", "—"))
                continue
            for ln, line in hits:
                if f"[{v['var_id']}]" not in line:
                    rows.append((fid, v, f"辞書参照なし（{ln}行）", line))
                elif (v.get("desc") or "") not in line:
                    rows.append((fid, v, f"記述不一致（{ln}行）", line))

    dest = root / "docs" / "dict-conflicts.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    out = ["---", 'title: "辞書と仕様書の矛盾候補"', "---", "",
           "<!--",
           "variables.py conflicts が自動生成する。手編集禁止。",
           "reviewed 済みの仕様書は自動修正しない（設計 P2）。ここは**機械が拾った候補の一覧**で、",
           "意味の矛盾かどうかは人が判断する。判定基準は「該当行に [V-xxxx] 参照が無い」または",
           "「辞書の desc 文字列が行に現れない」の2点のみ。",
           "-->", "",
           f"承認済み変数 {len(approved)}件 × reviewed 仕様書 {checked}組を突合 → "
           f"候補 {len(rows)}件", ""]
    if rows:
        out += ["| 仕様書 | 変数 | 辞書の記述 | 種別 | 仕様書の該当行 |",
                "|---|---|---|---|---|"]
        for fid, v, kind, line in rows:
            cell = line.replace("|", "\\|")
            out.append(f"| [{fid}](specs/{fid}.md) | {v['var_id']} {v['canonical_name']} | "
                       f"{_dict_desc(v)} | {kind} | {cell} |")
    else:
        out.append("矛盾候補なし。")
    out.append("")
    dest.write_text("\n".join(out), encoding="utf-8")
    print(f"生成: {dest}（突合 {checked}組 / 矛盾候補 {len(rows)}件）")


# ---------- CLI ----------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".", help="対象プロジェクトのルート")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", dest="root2", metavar="ROOT", default=None,
                        help="対象プロジェクトのルート（サブコマンドの後ろに置く場合）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("build", parents=[common],
                   help="functions.json＋legacyソースから data/variables.json を生成/マージ")

    s = sub.add_parser("verify-interp", parents=[common],
                       help="interpretations.json を機械検証して variables.json にマージ")
    s.add_argument("--ids", help="対象 var_id（例: V-0001,V-0003 / V-0001..V-0005）。"
                                 "省略時は status=unreviewed 全部")

    s = sub.add_parser("list-targets", parents=[common],
                       help="解釈バッチ用の最小コンテキストを JSON 出力")
    s.add_argument("--limit", type=int, default=30)
    s.add_argument("--ids", help="対象を明示指定（省略時は status=unreviewed）")

    s = sub.add_parser("approve", parents=[common], help="変数を承認（チャット承認用）")
    s.add_argument("ids", help="V-0001,V-0002 形式（範囲は V-0001..V-0005）")
    s.add_argument("--by", required=True, help="承認者名")

    s = sub.add_parser("revise", parents=[common], help="desc/unit を修正して同時に承認")
    s.add_argument("var_id")
    s.add_argument("--desc", required=True)
    s.add_argument("--unit")
    s.add_argument("--by", required=True, help="承認者名")

    sub.add_parser("propagate", parents=[common],
                   help="approved の desc/unit を functions.json に機械転記")
    sub.add_parser("page", parents=[common], help="docs/variables.qmd を生成")
    sub.add_parser("conflicts", parents=[common], help="docs/dict-conflicts.md を生成")

    args = ap.parse_args()
    root = Path(getattr(args, "root2", None) or args.root).resolve()
    {
        "build": cmd_build,
        "verify-interp": cmd_verify_interp,
        "list-targets": cmd_list_targets,
        "approve": cmd_approve,
        "revise": cmd_revise,
        "propagate": cmd_propagate,
        "page": cmd_page,
        "conflicts": cmd_conflicts,
    }[args.cmd](root, args)


if __name__ == "__main__":
    main()
