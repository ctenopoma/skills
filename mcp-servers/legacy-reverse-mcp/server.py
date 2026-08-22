#!/usr/bin/env python3
"""legacy-reverse-mcp — レガシー移植パイプラインの機械操作を MCP ツールとして公開する。

skills（判断・手順）はそのまま、Bash経由で叩いていたスクリプト層を型付きツール化したもの。
実体は ../../legacy-reverse/scripts/ の各スクリプト（単体でも従来どおり使える）。

登録例（対象プロジェクトの .mcp.json）:
{
  "mcpServers": {
    "legacy-reverse": {
      "command": "python",
      "args": ["C:/work_space/skills/mcp-servers/legacy-reverse-mcp/server.py"]
    }
  }
}
"""
import os
import re
import subprocess
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

SCRIPTS = Path(__file__).resolve().parents[2] / "legacy-reverse" / "scripts"
sys.path.insert(0, str(SCRIPTS))
from ledger import Project, parse_frontmatter  # noqa: E402
import extract_c  # noqa: E402
import extract_fortran  # noqa: E402
import review_checks  # noqa: E402

mcp = FastMCP("legacy-reverse")


def _run_raw(cmd: list, cwd: str | None = None, env_add: dict | None = None):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_add:
        env.update(env_add)
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=cwd, env=env)


def _run(cmd: list, cwd: str | None = None, env_add: dict | None = None) -> dict:
    r = _run_raw(cmd, cwd, env_add)
    return {"ok": r.returncode == 0, "exit_code": r.returncode,
            "output": (r.stdout + r.stderr).strip()[-4000:]}


def _ledger(root: str, *args: str) -> dict:
    return _run([sys.executable, str(SCRIPTS / "ledger.py"), "--root", root, *args])


def _script(name: str, *args: str) -> dict:
    """scripts/<name> をそのまま実行する（sys.exit するスクリプトを安全に隔離する）。"""
    return _run([sys.executable, str(SCRIPTS / name), *args])


def _script_json(name: str, *args: str):
    """--json 系サブコマンドの stdout を JSON として返す（パース不能なら生出力）。"""
    import json as _json
    r = _run_raw([sys.executable, str(SCRIPTS / name), *args])
    try:
        return _json.loads(r.stdout)
    except ValueError:
        return {"ok": r.returncode == 0, "exit_code": r.returncode,
                "output": (r.stdout + r.stderr).strip()[-4000:]}


# ---------- 台帳（読み取り） ----------

@mcp.tool()
def pipeline_status(root: str = ".", func_id: str | None = None) -> list:
    """全関数（または指定関数）の①〜⑤の進捗・stale・blocked状態を返す。"""
    p = Project(Path(root).resolve())
    targets = [p.func(func_id)] if func_id else p.funcs()
    return [p.status_of(f) for f in targets]


@mcp.tool()
def next_action(root: str = ".") -> str:
    """トポロジカル順（葉から）で、次に着手すべき関数とフェーズを返す。"""
    return _ledger(root, "next")["output"]


@mcp.tool()
def next_actions(root: str = ".", limit: int = 20, phase: str | None = None,
                 skip_draft: bool = False, flow: str | None = None,
                 no_dict_gate: bool = False) -> str:
    """着手可能な関数を推奨順に最大 limit 件返す（fid<TAB>次フェーズ）。バッチ計画用。

    phase="1"〜"5" で対象フェーズを絞れる（例: ①のバッチ実行対象は phase="1"）。
    skip_draft=True で人のレビュー/承認待ち（①draft・②generated）を除外する
    ——バッチ全件モードの再開時はこれを使う（draft の二重書き直しを防ぐ）。
    flow（フロー名 or FL-01）で対象をそのフロー到達集合に限定する。
    既定では変数辞書に未承認の語義が残る関数の①を除外する（dict-gate）。
    no_dict_gate=True で解除できるが、原則は辞書の承認を先に進めること。
    """
    args = ["next", "--all", "--limit", str(limit)]
    if phase:
        args += ["--phase", phase]
    if skip_draft:
        args += ["--skip-draft"]
    if flow:
        args += ["--flow", flow]
    if no_dict_gate:
        args.append("--no-dict-gate")
    return _ledger(root, *args)["output"]


@mcp.tool()
def progress_summary(root: str = ".") -> dict:
    """進捗の要約（各フェーズの完了数・blocked・stale・改変・fail の一覧）を返す。

    2000関数規模でも全関数リストを読まずに状況把握と再開判断ができる。
    """
    import json as _json
    # _run() の「stdout+stderr 連結＋末尾4000字」を通すと、問題関数が数百件を超えた時に
    # JSON の先頭が切れてパース不能になる（大規模でだけ failed になる）。stdout を丸ごと読む。
    r = _run_raw([sys.executable, str(SCRIPTS / "ledger.py"), "--root", root,
                  "status", "--summary"])
    try:
        return _json.loads(r.stdout)
    except ValueError:
        return {"ok": False, "exit_code": r.returncode,
                "output": (r.stdout + r.stderr).strip()[-4000:]}


@mcp.tool()
def verify(root: str, func_id: str) -> dict:
    """ハッシュ連鎖（①→②、③改変、blocked）を検証する。ok=false なら理由が problems に入る。"""
    r = _ledger(root, "verify", func_id)
    return {"ok": r["ok"], "problems": [l for l in r["output"].splitlines() if l.startswith("NG")]}


@mcp.tool()
def next_issue_id(root: str = ".") -> str:
    """次に使う ISSUE 番号（全体通し）を返す。"""
    return _ledger(root, "next-issue")["output"]


# ---------- ⓪ 機械抽出 ----------

@mcp.tool()
def extract_functions(root: str = ".", legacy_dir: str = "legacy",
                      package: str | None = None, write: bool = True,
                      infer_calls: bool = True) -> dict:
    """⓪の機械抽出: Fortran ソースを静的解析し functions.json を生成/マージする。

    再実行は常にマージ（func_id 不変・手修正保持）なので途中再開しても安全。
    返り値に完全性突合の差分・推定呼び出し・未解決名の件数が入る。
    詳細は data/extract-report.json（監査ログ）を読むこと。
    """
    return extract_fortran.extract(root, legacy_dir, package,
                                   write=write, infer_calls=infer_calls)


@mcp.tool()
def extract_c_functions(root: str = ".", legacy_dir: str = "legacy",
                        package: str | None = None, write: bool = True) -> dict:
    """⓪の機械抽出（C/C++）: Fortran が依存する C/C++ ソースも同じ functions.json に載せる。

    extract_functions（Fortran）と同じマージ規約（func_id 不変・手修正保持）。
    Fortran↔C の呼び出し（アンダースコア規約含む）は自動でリンクされる——
    片方の時点で未解決だった呼び出し名は、もう片方の抽出が走った時点で解決される
    ので実行順は不問。監査ログは data/extract-report-c.json。
    """
    return extract_c.extract(root, legacy_dir, package, write=write)


# ---------- ⓪拡張: コールグラフ（graph.py。導出層・LLM不使用） ----------

@mcp.tool()
def graph_query(root: str = ".", cmd: str = "summary", fid: str | None = None,
                to: str | None = None, flow: str | None = None,
                transitive: bool = False) -> dict | list:
    """functions.json から構築したコールグラフに問い合わせる（保存しない導出層）。

    cmd:
      summary   … ノード/エッジ数・エントリ・到達率・dead件数・循環数（既定）
      dead      … どのエントリからも到達不能な関数（**exclude 候補の列挙のみ**。
                  自動除外はしない。除外は人の判断で exclude_function を使う）
      cycles    … SCC（Tarjan）でサイズ2以上の成分
      reachable … fid（カンマ区切りで複数可）または flow から到達可能な集合
      callers   … fid の呼び出し元（transitive=True で推移的な影響範囲）
      between   … fid → to の最短経路（BFS）
    """
    if cmd not in ("summary", "dead", "cycles", "reachable", "callers", "between"):
        return {"ok": False, "message": f"不明な cmd: {cmd}"}
    args = ["--root", root, cmd]
    if cmd == "reachable":
        args += [f.strip() for f in (fid or "").split(",") if f.strip()]
        if flow:
            args += ["--flow", flow]
    elif cmd == "callers":
        if not fid:
            return {"ok": False, "message": "callers には fid が必要"}
        args += [fid] + (["--transitive"] if transitive else [])
    elif cmd == "between":
        if not fid or not to:
            return {"ok": False, "message": "between には fid と to が必要"}
        args += [fid, to]
    if cmd != "summary":
        args.append("--json")
    return _script_json("graph.py", *args)


# ---------- ⓪拡張: 変数辞書（variables.py） ----------

@mcp.tool()
def dict_build(root: str = ".") -> dict:
    """変数辞書を構築/再構築する（data/variables.json）。①より先に行う。

    COMMON の同一位置・実引数↔仮引数・EQUIVALENCE・同一関数内の同名という
    機械的根拠だけで変数クラスタを作り、コメント/FORMAT/初期値/使用式を根拠として集める。
    **再実行は常にマージ**（var_id 不変・evidence_hash が同じなら承認と desc を維持）。
    """
    return _script("variables.py", "build", "--root", root)


@mcp.tool()
def dict_list_targets(root: str = ".", limit: int = 30, ids: str | None = None):
    """未解釈（status=unreviewed）の変数の根拠バンドルを JSON で返す。

    解釈の**唯一の入力**（legacy 全文は読まない）。各要素に var_id / 名前・別名 /
    出現（関数・役割・型・行）/ links / evidence（ev_id・kind・file・line・text）が入る。
    解釈結果は data/interpretations.json に書き、dict_verify_interp で検証・マージする。
    """
    args = ["list-targets", "--root", root, "--limit", str(limit)]
    if ids:
        args += ["--ids", ids]
    return _script_json("variables.py", *args)


@mcp.tool()
def dict_verify_interp(root: str = ".", ids: str | None = None) -> dict:
    """data/interpretations.json を機械検証して variables.json にマージする。

    検証: 対象 var_id との完全一致（欠落・余剰は全件差し戻し）／ev_id の実在（捏造検知）／
    desc が空でないこと。**rank は LLM の申告でなくここが根拠種別から決める**
    （D＝引用なしはマージせず、desc「不明」で人の確認キューに残す）。
    ok=false のときは output に NG 理由が並ぶので、直して再実行する。
    """
    args = ["verify-interp", "--root", root]
    if ids:
        args += ["--ids", ids]
    return _script("variables.py", *args)


@mcp.tool()
def dict_approve(root: str, by: str, ids: str | None = None, var_id: str | None = None,
                 desc: str | None = None, unit: str | None = None) -> dict:
    """変数の語義を承認する（**人のOKを得てから呼ぶこと**）。

    ids（"V-0001,V-0002" / "V-0001..V-0005"）でそのまま承認、
    var_id + desc で「修正して承認」（revise）。承認1回でクラスタの全出現に効く。
    desc が空・「不明」のままの変数は承認できない（先に修正する）。
    承認後は dict_propagate → generate_skeletons → render_site を続けて呼ぶ。
    """
    if var_id and desc is not None:
        args = ["revise", var_id, "--desc", desc, "--by", by, "--root", root]
        if unit is not None:
            args += ["--unit", unit]
        return _script("variables.py", *args)
    if not ids:
        return {"ok": False, "message": "ids（一括承認）か var_id+desc（修正して承認）が必要"}
    return _script("variables.py", "approve", ids, "--by", by, "--root", root)


@mcp.tool()
def dict_propagate(root: str = ".") -> dict:
    """承認済みの語義を functions.json の inputs/outputs/globals の desc へ機械転記する。

    書式は "<意味>(<単位>) [V-0001]"。①はこの [V-xxxx] を書き換えない（辞書が正）。
    転記後は generate_skeletons（骨子の IO 表と dict-hash の同期）→ generate_wbs → render_site。
    """
    return _script("variables.py", "propagate", "--root", root)


@mcp.tool()
def dict_page(root: str = ".") -> dict:
    """docs/variables.qmd（変数辞書ページ）を生成する。自動生成・手編集禁止。

    並び順は影響度（出現関数数）降順 × rank 昇順。未承認があれば render_site.py が
    承認方法の案内（閲覧専用）を焼き込み、ナビバーに「変数辞書」が自動で追加される。
    承認は dict_approve / dict_revise（または variables.py CLI）。
    """
    return _script("variables.py", "page", "--root", root)


@mcp.tool()
def dict_conflicts(root: str = ".") -> dict:
    """辞書と reviewed 済み仕様書の矛盾候補を docs/dict-conflicts.md に出す。

    reviewed 済みの仕様書は辞書の改訂で自動修正しない（人が判断する）ための一覧。
    WBS の「⚠辞書stale」と合わせて人に見せる。
    """
    return _script("variables.py", "conflicts", "--root", root)


# ---------- ⓪拡張: 例外ポリシー（hazards.py） ----------

@mcp.tool()
def hazard_status(root: str = ".") -> dict:
    """hazard（0割・SQRT/LOG の定義域・変数添字…）の総数・kind別・決定済み/未決定を返す。

    ok=true（未決定ゼロかつ登録簿に問題なし）が①へ進める条件。
    """
    return _script_json("hazards.py", "status", "--root", root, "--json")


@mcp.tool()
def hazard_match(root: str = ".") -> dict:
    """全 hazard を docs/exception-policy.md（EP登録簿）と突合する。

    data/hazard-map.json を更新し、未決定を docs/exception-queue.md に
    kind ごとに集約して「仮説＋選択肢」形式で書き出す。
    **未決定はそのキューを人に見せて決めてもらう**（AIが決めない）→ hazard_add_policy。
    """
    res = _script_json("hazards.py", "match", "--root", root, "--json")
    if isinstance(res, dict) and "map" in res:
        res = {k: v for k, v in res.items() if k != "map"}
        res["unmatched"] = res.get("unmatched", [])[:50]
    return res


@mcp.tool()
def hazard_add_policy(root: str, kind: str, decision: str, by: str,
                      func: str | None = None, hazard: str | None = None,
                      note: str = "") -> dict:
    """人が決めた例外ポリシーを EP-xxx として登録簿に追記する（**人のOKが前提**）。

    decision: detect_only / guard_raise / guard_value（値を note に明記）/
    legacy_preserve / caller_guarantees（根拠を note に明記）。
    func / hazard を付けると適用範囲が狭まり、全体既定より優先される。
    追記後は自動で再突合する（同じ kind の全箇所に即座に効く）。
    """
    args = ["add-policy", "--root", root, "--kind", kind, "--decision", decision,
            "--by", by, "--json"]
    if func:
        args += ["--func", func]
    if hazard:
        args += ["--hazard", hazard]
    if note:
        args += ["--note", note]
    return _script_json("hazards.py", *args)


# ---------- ⓪拡張: フロー（作業スコープ） ----------

@mcp.tool()
def flow_add(root: str, name: str, entry: str, desc: str = "") -> dict:
    """フロー（作業スコープ）を追加する。entry は "F-0000" / "F-0000,F-0006"。

    main が複数ある、または main 内の大分岐を別々に進めたいときだけ人に確認して定義する。
    到達集合は保存せず graph.py が毎回計算するので再抽出に自動追随する。
    定義すると WBS にフロー別進捗表が出て、next_actions / pipeline の --flow で絞れる。
    """
    args = ["flow", "add", name, "--entry", entry]
    if desc:
        args += ["--desc", desc]
    return _ledger(root, *args)


@mcp.tool()
def flow_list(root: str = ".") -> dict:
    """定義済みフローの一覧（flow_id・名前・entries・到達関数数・説明）。"""
    return _ledger(root, "flow", "list")


@mcp.tool()
def flow_remove(root: str, key: str) -> dict:
    """フローを削除する（フロー名 or flow_id を指定）。成果物には影響しない。"""
    return _ledger(root, "flow", "rm", key)


# ---------- 機械レビュー（ハルシネーション・省略の検知） ----------

@mcp.tool()
def review_spec(root: str, func_id: str) -> dict:
    """①仕様書の機械レビュー: 根拠 file:lines の実在、🟢なのに根拠なし、
    プレースホルダ残存（省略）、必須節欠落、原本ハッシュ鮮度を検証する。
    ①draft 後・人レビュー依頼前に必ず実行し、ok=true にしてから人に出す。"""
    return review_checks.check_spec(root, func_id)


@mcp.tool()
def review_testspec(root: str, func_id: str) -> dict:
    """②テスト仕様書の機械レビュー: トレーサビリティ（全🟢仕様項目にケースありか・
    参照先の実在）、期待値の根拠の規定準拠、⚠未確定件数、spec-hash 鮮度を検証する。
    approved 依頼前に必ず実行し、ok=true にしてから人に出す。"""
    return review_checks.check_testspec(root, func_id)


@mcp.tool()
def review_all(root: str = ".") -> dict:
    """全関数の①②を一括機械レビューする（skeleton は除外）。⑥前の総点検に使う。"""
    return review_checks.check_all(root)


@mcp.tool()
def spec_review_report(root: str = ".", kind: str = "") -> dict:
    """一斉レビュー表を生成する（①→docs/spec-review.md、②→docs/testspec-review.md）。

    バッチモード（複数関数を連続処理）の後、人がブラウザでまとめてレビューするための一覧。
    kind（spec / testspec）を省くと両方を作る。生成後に render_site で HTML 化して案内する。
    """
    if kind:
        return review_checks.make_report(root, kind)
    return review_checks.make_reports(root)


# ---------- 台帳（書き込み） ----------

@mcp.tool()
def add_function(root: str, name: str, file: str = "", lines: str = "",
                 module: str | None = None, new_name: str | None = None,
                 calls: str | None = None) -> dict:
    """人の指示で関数を後追い追加する（⓪の抽出漏れ・関数分割など）。

    manual フラグ付きで採番されるので、抽出の再実行で「ソースに無い」警告は出ない。
    追加後は functions.json の inputs/outputs/desc/signature を充填し、
    generate_skeletons → generate_wbs で①〜⑤の対象に載る。
    """
    args = ["add", name]
    if file:
        args += ["--file", file]
    if lines:
        args += ["--lines", lines]
    if module:
        args += ["--module", module]
    if new_name:
        args += ["--new-name", new_name]
    if calls:
        args += ["--calls", calls]
    return _ledger(root, *args)


@mcp.tool()
def exclude_function(root: str, func_id: str, reason: str = "") -> dict:
    """関数を移植対象から外す（デッドコード等の人の判断）。①〜⑥・WBSから除外される。

    functions.json から物理削除はしない（再抽出で別IDとして復活するため）。
    WBS の「対象外の関数」に理由つきで載り、include_function で復帰できる。
    """
    args = ["exclude", func_id]
    if reason:
        args += ["--reason", reason]
    return _ledger(root, *args)


@mcp.tool()
def include_function(root: str, func_id: str) -> dict:
    """対象外にした関数を①〜⑤の対象へ復帰させる。"""
    return _ledger(root, "include", func_id)


@mcp.tool()
def generate_wbs(root: str = ".") -> dict:
    """functions.json＋各成果物フロントマターから docs/index.qmd（WBS）を再生成する。"""
    return _ledger(root, "wbs")


@mcp.tool()
def generate_skeletons(root: str = ".", force: bool = False) -> dict:
    """functions.json から docs/specs/ の仕様書骨子を生成する（既存はスキップ）。"""
    args = ["skeletons"] + (["--force"] if force else [])
    return _ledger(root, *args)


@mcp.tool()
def set_test_file(root: str, func_id: str, path: str = "") -> dict:
    """③のテストファイルを functions.json の test_file に登録する。

    path 省略時は②のケースIDの @pytest.mark.tc を持つファイルから自動判定する。
    """
    args = ["set-test-file", func_id] + ([path] if path else [])
    return _ledger(root, *args)


@mcp.tool()
def freeze_tests(root: str, func_id: str, path: str = "") -> dict:
    """③完了時にテストコードのハッシュを台帳へ記録する（以降の改変を検知可能にする）。

    test_file 未設定なら path（省略時は自動判定）でその場で確定させる。
    """
    args = ["freeze-tests", func_id] + ([path] if path else [])
    return _ledger(root, *args)


@mcp.tool()
def block(root: str, func_id: str, issue_id: str) -> dict:
    """関数を裁定待ち(⛔)にする。"""
    return _ledger(root, "block", func_id, issue_id)


@mcp.tool()
def unblock(root: str, func_id: str) -> dict:
    """人の裁定反映後にブロックを解除し、attempt をリセットする。"""
    return _ledger(root, "unblock", func_id)


@mcp.tool()
def phase_start(root: str, phase: str, func_id: str) -> dict:
    """フェーズ開始を記録する（4/5 の間は hook が tests/ 編集を拒否する）。"""
    return _ledger(root, "phase-start", phase, func_id)


@mcp.tool()
def phase_end(root: str = ".") -> dict:
    """フェーズ終了を記録する（hook 解除）。"""
    return _ledger(root, "phase-end")


@mcp.tool()
def completion_check(root: str = ".") -> dict:
    """⑥完了検証を実行し docs/completion-check.md を生成する。ok=true が⑥pass。"""
    return _ledger(root, "check")


@mcp.tool()
def sphinx_index(root: str = ".") -> dict:
    """functions.json から docs-sphinx/index.rst（関数一覧＋トレース対応表）を再生成する。"""
    return _ledger(root, "sphinx-index")


# ---------- 実行系 ----------

@mcp.tool()
def run_tests(root: str, func_id: str) -> dict:
    """⑤を一括実行する: 事前verify → pytest（tcマーカー収集）→ 結果報告書の自動生成。

    返り値の result: pass / fail / blocked(要裁定) / mismatch(③のマーカー不整合) / verify_ng
    """
    rootp = Path(root).resolve()
    v = _ledger(root, "verify", func_id)
    if not v["ok"]:
        return {"result": "verify_ng", "detail": v["output"]}
    p = Project(rootp)
    tf = p.func(func_id).get("test_file")
    t = _run([sys.executable, "-m", "pytest", tf, "-q", "-p", "tc_report_plugin"],
             cwd=str(rootp), env_add={"PYTHONPATH": str(SCRIPTS)})
    c = _run([sys.executable, str(SCRIPTS / "collect_results.py"), func_id, "--root", root])
    result = {0: "pass", 1: "fail", 2: "blocked", 3: "mismatch"}.get(c["exit_code"], "error")
    out: dict = {"result": result, "detail": c["output"], "pytest_tail": t["output"][-1500:]}
    latest = p.latest_result(func_id)
    if latest:
        fm = parse_frontmatter(latest.read_text(encoding="utf-8-sig"))
        out.update({"report": str(latest), "rate": fm.get("tc-coverage", {}).get("rate"),
                    "attempt": fm.get("attempt"), "blocked_by": fm.get("blocked-by")})
    return out


@mcp.tool()
def check_stubs(root: str, module: str) -> dict:
    """④のスタブ検出（空実装・NotImplementedError・TODO/FIXME）。ok=true でスタブなし。"""
    return _run([sys.executable, str(SCRIPTS / "check_stubs.py"),
                 str(Path(root).resolve() / module)])


@mcp.tool()
def profile(root: str = ".", script: str | None = None) -> dict:
    """⑦の性能計測。cProfile で src/ 関数別に集計し docs/perf.md を生成する。

    script 未指定はテスト負荷のスモーク計測。ホットスポット判断は代表ワークロードで。
    """
    args = [sys.executable, str(SCRIPTS / "profile_run.py"), "--root", root]
    if script:
        args += ["--script", script]
    return _run(args)


@mcp.tool()
def render_site(root: str = ".", with_sphinx: bool = True, full: bool = False) -> dict:
    """docs/ を Quarto で HTML サイト化し、続けて Sphinx API を docs/_site/api に生成する。

    quarto を直接呼ばず render_site.py を通す（Mermaid を効かせるための影コピーを作る）。
    既定は差分レンダリング（変わったページのみ。2000関数でも数十秒）。
    full=True で全ページ再レンダ（サイト内検索の索引更新もこのときだけ）。
    """
    cmd = [sys.executable, str(SCRIPTS / "render_site.py"), "--root", root]
    if full:
        cmd.append("--full")
    r1 = _run(cmd)
    if not r1["ok"]:
        return {"ok": False, "step": "quarto", "output": r1["output"]}
    if with_sphinx and (Path(root).resolve() / "docs-sphinx").exists():
        r2 = _run([sys.executable, "-m", "sphinx", "-b", "html", "docs-sphinx",
                   "docs/_site/api", "-q"], cwd=str(Path(root).resolve()))
        if not r2["ok"]:
            return {"ok": False, "step": "sphinx", "output": r2["output"]}
    return {"ok": True, "site": str(Path(root).resolve() / "docs" / "_site")}


@mcp.tool()
def build_pdf(root: str, kind: str, title: str, output: str) -> dict:
    """種別ごとの合本PDFを生成する。kind: specs / test-specs / test-results。"""
    return _run([sys.executable, str(SCRIPTS / "pdf_book.py"), kind, "--root", root,
                 "--output", output, "--title", title])


@mcp.tool()
def build_viewer(root: str = ".", name: str | None = None, port: int | None = None,
                 render: bool = True, onedir: bool = False) -> dict:
    """WBS/仕様書サイトを同梱した単体実行ファイル（EXE）を dist/ に作る。

    レビュアーに渡すと、各自のローカルホストでブラウザが開く（相手に Python も Quarto も不要）。
    PyInstaller が必要（`pip install pyinstaller`）。1〜2分かかるので呼び出し側のタイムアウトに注意。
    配信そのもの（serve_site.py）は常駐プロセスなので MCP ツールにはしていない。
    """
    cmd = [sys.executable, str(SCRIPTS / "build_viewer.py"), "--root", root]
    if name:
        cmd += ["--name", name]
    if port is not None:
        cmd += ["--port", str(port)]
    if not render:
        cmd.append("--no-render")
    if onedir:
        cmd.append("--onedir")
    return _run(cmd)


if __name__ == "__main__":
    mcp.run()
