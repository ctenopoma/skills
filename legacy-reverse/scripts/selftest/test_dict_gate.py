#!/usr/bin/env python3
"""M2c（dict-gate / dict-hash 連鎖 / pipeline の辞書バッチ・モデル指定）のセルフテスト。

`python test_dict_gate.py` で走る（pytest 不要・素の assert）。
scripts/selftest/fixture_vars/ を毎回テンポラリにコピーし、variables.py build で
本物の変数辞書を作ってから検証する。claude は一切起動しない
（pipeline は --dry-run と run_claude の差し替えでドライに検証する）。

検証対象:
  (a) dict-gate: `ledger next` の除外と `--no-dict-gate` での解除
  (b) 免除: spec が draft / reviewed の関数はゲート対象外
  (c) 後方互換: variables.json が無いプロジェクトの next / wbs 出力が
      **改修前（git HEAD）の ledger.py の出力とバイト一致**すること
  (d) dict-hash: 骨子への記録（①未着手の骨子は作り直しで現在値になる）と、
      承認後に desc を変えたときの dict_stale 検出
      （verify のメッセージと exit / WBS の ⚠ 表示）
  (e) 旧骨子（dict-hash 行が無い spec）は stale 扱いにしない
  (f) pipeline._decide_kind のゲートと `--no-dict-gate` での解除
      （判定は ledger 側の共通実装を参照していること）
  (g) run_one が余分な起動引数を足さないこと（モデルは選ばない）と、
      応答ログのファイル名の正規化
"""
import atexit
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixture_vars"
LEDGER_PY = SCRIPTS_DIR / "ledger.py"
VARIABLES_PY = SCRIPTS_DIR / "variables.py"

sys.path.insert(0, str(SCRIPTS_DIR))
import ledger        # noqa: E402
import pipeline      # noqa: E402

_TMPDIRS = []
# 後方互換比較用に HEAD 版 ledger.py を置く場所（テスト中だけ存在）
_HEAD_COPY = SCRIPTS_DIR / "_ledger_head_selftest.py"


# ---------- 基盤 ----------

def make_project(with_dict: bool = True) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="lr-dictgate-"))
    _TMPDIRS.append(tmp)
    root = tmp / "proj"
    shutil.copytree(FIXTURE, root)
    if with_dict:
        assert run_vars(root, "build")[0] == 0
    return root


def _env() -> dict:
    # PYTHONPATH: 比較用に temp へ書き出した旧 ledger.py（_old_ledger）も
    # 兄弟モジュール（graph.py 等）を import できるようにする。
    # これが無いと旧コピーが ModuleNotFoundError で落ち、
    # 後方互換の比較そのものが成立しない
    return {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(SCRIPTS_DIR)}


def run_ledger(root: Path, *args, script: Path = None):
    r = subprocess.run([sys.executable, str(script or LEDGER_PY), "--root", str(root), *args],
                       capture_output=True, text=True, encoding="utf-8", env=_env())
    return r.returncode, r.stdout, r.stderr


def run_vars(root: Path, *args):
    r = subprocess.run([sys.executable, str(VARIABLES_PY), *args, "--root", str(root)],
                       capture_output=True, text=True, encoding="utf-8", env=_env())
    return r.returncode, r.stdout, r.stderr


def load_vars(root: Path) -> list:
    return json.loads((root / "data" / "variables.json").read_text(encoding="utf-8"))["variables"]


def vars_of(root: Path, fid: str) -> list:
    return [v["var_id"] for v in load_vars(root)
            if any(o["func_id"] == fid for o in v["occurrences"])]


def approve(root: Path, vid: str, desc: str) -> None:
    """desc を確定して同時に承認する（人の承認と同じ経路＝variables.py revise）。"""
    rc, out, err = run_vars(root, "revise", vid, "--desc", desc, "--by", "テスト太郎")
    assert rc == 0, out + err


def set_spec_status(root: Path, fid: str, status: str) -> None:
    """①の結果を模して spec のフロントマター status を書き換える（他は触らない）。"""
    p = root / "docs" / "specs" / f"{fid}.md"
    text = p.read_text(encoding="utf-8-sig")
    p.write_text(text.replace("status: skeleton", f"status: {status}", 1), encoding="utf-8")


def spec_frontmatter(root: Path, fid: str) -> dict:
    return ledger.parse_frontmatter(
        (root / "docs" / "specs" / f"{fid}.md").read_text(encoding="utf-8-sig"))


def next_fids(out: str) -> set:
    return {line.split("\t")[0] for line in out.strip().splitlines() if "\t" in line}


# ---------- (a) dict-gate の除外と --no-dict-gate ----------

def test_dict_gate_excludes_and_can_be_disabled():
    root = make_project()
    assert run_ledger(root, "skeletons")[0] == 0

    rc, out, err = run_ledger(root, "next", "--all")
    assert rc == 0, err
    # 全変数が unreviewed なので、①が次フェーズの関数（F-0000/F-0002/F-0003）は全部落ちる。
    # F-0001 は spec が reviewed 済みで次フェーズが②なので、ゲートとは無関係に残る
    assert next_fids(out) == {"F-0001"}, out
    # 理由は stderr（--all の fid<TAB>phase を汚さない）
    assert "dict-gate: F-0000 は未承認の変数" in err, err
    assert "--no-dict-gate" in err
    for line in out.splitlines():
        assert "dict-gate" not in line, "機械可読出力に理由が混ざっている"

    rc, out2, err2 = run_ledger(root, "next", "--all", "--no-dict-gate")
    assert rc == 0, err2
    assert next_fids(out2) == {"F-0000", "F-0001", "F-0002", "F-0003"}, out2
    assert "dict-gate" not in err2

    # 単発（--all なし）は人間可読なので stdout に理由を出す
    rc, out3, err3 = run_ledger(root, "next", "--phase", "1")
    assert rc == 0, err3
    assert "dict-gate: F-0000 は未承認の変数" in out3, out3
    assert "対象なし（dict-gate で 3 関数を除外" in out3, out3

    # 承認が済んだ関数だけがゲートを通る（F-0003 の変数を全部承認）
    for vid in vars_of(root, "F-0003"):
        approve(root, vid, f"{vid} の意味")
    rc, out4, err4 = run_ledger(root, "next", "--all")
    assert rc == 0, err4
    assert next_fids(out4) == {"F-0001", "F-0003"}, out4
    print("OK  (a) dict-gate の除外 / --no-dict-gate / 承認済み関数の通過")


def test_dict_gate_blockers_api():
    root = make_project()
    p = ledger.Project(root)
    assert p.has_dict()
    blockers = p.dict_gate_blockers("F-0003")
    assert blockers == vars_of(root, "F-0003") == ["V-0003", "V-0007"], blockers

    approve(root, "V-0003", "処理件数")
    p2 = ledger.Project(root)
    assert p2.dict_gate_blockers("F-0003") == ["V-0007"]
    approve(root, "V-0007", "作業バッファ")
    p3 = ledger.Project(root)
    assert p3.dict_gate_blockers("F-0003") == []
    print("OK  (a) Project.dict_gate_blockers（承認に応じて減る）")


# ---------- (b) draft / reviewed の免除 ----------

def test_gate_exempts_draft_and_reviewed():
    root = make_project()
    assert run_ledger(root, "skeletons")[0] == 0
    set_spec_status(root, "F-0002", "draft")     # ①は書けたが承認待ち

    p = ledger.Project(root)
    # F-0002 は未承認の変数を持つが、spec が draft なのでゲート対象外
    assert vars_of(root, "F-0002"), "前提: F-0002 に変数がある"
    assert p.dict_gate_blockers("F-0002") == []
    # F-0001（reviewed）も対象外
    assert vars_of(root, "F-0001"), "前提: F-0001 に変数がある"
    assert p.dict_gate_blockers("F-0001") == []
    # skeleton のままの F-0000 は掛かる
    assert p.dict_gate_blockers("F-0000")

    rc, out, err = run_ledger(root, "next", "--all")
    assert rc == 0, err
    assert next_fids(out) == {"F-0001", "F-0002"}, out
    assert "F-0002" not in err
    print("OK  (b) 免除: spec が draft / reviewed の関数はゲート対象外")


# ---------- (c) variables.json 無しの後方互換（改修前の出力とバイト一致） ----------

def _old_ledger() -> "Path | None":
    """git HEAD の ledger.py を scripts/ の中に一時ファイルとして書き出す。

    temp ディレクトリではなく scripts/ に置くのは、ledger.py が
    兄弟モジュール（graph.py）と `__file__` 起点の assets/templates を
    参照するため（temp から実行すると ModuleNotFoundError や
    テンプレ不在で落ち、後方互換の比較そのものが成立しない）。
    後始末は atexit で行う（途中で落ちてもリポジトリに残さない）。
    """
    try:
        prefix = subprocess.run(["git", "-C", str(SCRIPTS_DIR), "rev-parse", "--show-prefix"],
                                capture_output=True, text=True, check=True).stdout.strip()
        blob = subprocess.run(["git", "-C", str(SCRIPTS_DIR), "show", f"HEAD:{prefix}ledger.py"],
                              capture_output=True, text=True, encoding="utf-8", check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    _HEAD_COPY.write_text(blob, encoding="utf-8")
    atexit.register(_rm_head_copy)
    return _HEAD_COPY


def _rm_head_copy() -> None:
    try:
        _HEAD_COPY.unlink()
    except OSError:
        pass


def test_no_dict_backward_compat():
    old = _old_ledger()
    root_new = make_project(with_dict=False)
    root_old = make_project(with_dict=False)
    assert not (root_new / "data" / "variables.json").exists()

    for root, script in ((root_new, None), (root_old, old)):
        if script is None or old is not None:
            assert run_ledger(root, "skeletons", script=script)[0] == 0
            assert run_ledger(root, "wbs", script=script)[0] == 0

    rc, out_new, err_new = run_ledger(root_new, "next", "--all")
    assert rc == 0, err_new
    assert "dict-gate" not in out_new + err_new
    wbs_new = (root_new / "docs" / "index.qmd").read_text(encoding="utf-8")
    assert "辞書" not in wbs_new
    skel_new = (root_new / "docs" / "specs" / "F-0000.md").read_text(encoding="utf-8")
    assert "dict-hash" not in skel_new, "辞書が無いプロジェクトに dict-hash を書いてはいけない"

    if old is None:
        print("WARN (c) git が使えないため改修前との突合はスキップ（辞書痕跡なしのみ確認）")
        return
    rc, out_old, _ = run_ledger(root_old, "next", "--all", script=old)
    assert rc == 0
    assert out_new == out_old, "next --all の出力が改修前と一致しない"
    wbs_old = (root_old / "docs" / "index.qmd").read_text(encoding="utf-8")
    assert wbs_new == wbs_old, "WBS 出力が改修前と一致しない"

    # 骨子は M4（例外・数値特異点の節）が未コミットのため HEAD と差がある。
    # ここで要求するのは「M2c が何も足していないこと」なので、追加行に辞書由来の
    # ものが混ざっていないことを見る
    import difflib
    skel_old = (root_old / "docs" / "specs" / "F-0000.md").read_text(encoding="utf-8")
    added = [l[1:] for l in difflib.ndiff(skel_old.splitlines(), skel_new.splitlines())
             if l.startswith("+ ")]
    assert not any("dict" in l or "辞書" in l for l in added), added

    # status --json も既存キーを壊していない。dict_stale は辞書機能で
    # 増えたキーなので両辺から落として比べる（機能が HEAD に入る前は
    # 旧側に無く、入った後は両方にある―どちらでも成立させる）
    _, sj_new, _ = run_ledger(root_new, "status", "--json")
    _, sj_old, _ = run_ledger(root_old, "status", "--json", script=old)
    a, b = json.loads(sj_new), json.loads(sj_old)

    def _wo_dict(rows: list) -> list:
        return [{k: v for k, v in row.items() if k != "dict_stale"} for row in rows]

    assert _wo_dict(a) == _wo_dict(b)
    assert all(s["dict_stale"] is False for s in a)
    print("OK  (c) variables.json 無しは next / wbs / skeletons / status が改修前と完全一致")


# ---------- (d) dict-hash の記録と stale 検出 ----------

def test_dict_hash_recorded_and_synced():
    root = make_project()
    assert run_ledger(root, "skeletons")[0] == 0
    # 承認ゼロの時点では "" で記録される（「辞書は見たが確定語義なし」の印）
    assert spec_frontmatter(root, "F-0003").get("dict-hash") in ("", {}), \
        spec_frontmatter(root, "F-0003")
    text_before = (root / "docs" / "specs" / "F-0003.md").read_text(encoding="utf-8")
    assert 'dict-hash: ""' in text_before

    # reviewed 済みの F-0001 は骨子生成の対象外なので、dict-hash は書かれない
    f1 = (root / "docs" / "specs" / "F-0001.md").read_text(encoding="utf-8")
    assert "dict-hash" not in f1

    for vid in vars_of(root, "F-0003"):
        approve(root, vid, f"{vid} の意味")
    rc, out, err = run_ledger(root, "skeletons")
    assert rc == 0, err
    # ①未着手（status: skeleton）の骨子は機械生成ブロックごと作り直される
    # ＝dict-hash も IO 表の転記も現在値になる
    assert "作り直し" in out, out
    h = spec_frontmatter(root, "F-0003")["dict-hash"]
    assert isinstance(h, str) and len(h) == 8, h
    # ①未着手（skeleton）の骨子だけが同期対象。reviewed の F-0001 は不変
    assert (root / "docs" / "specs" / "F-0001.md").read_text(encoding="utf-8") == f1

    p = ledger.Project(root)
    assert p.dict_hash("F-0003") == h
    assert p.status_of(p.func("F-0003"))["dict_stale"] is False
    print("OK  (d) dict-hash の記録（承認ゼロは \"\"）と①未着手骨子への同期")


def test_dict_stale_detected_after_revision():
    root = make_project()
    assert run_ledger(root, "skeletons")[0] == 0
    for vid in vars_of(root, "F-0003"):
        approve(root, vid, f"{vid} の意味")
    assert run_ledger(root, "skeletons")[0] == 0
    set_spec_status(root, "F-0003", "draft")     # ①が仕様書を書いた状態
    recorded = spec_frontmatter(root, "F-0003")["dict-hash"]

    p = ledger.Project(root)
    assert p.status_of(p.func("F-0003"))["dict_stale"] is False
    rc, out, err = run_ledger(root, "verify", "F-0003")
    assert rc == 0 and "verify: OK" in out, out + err

    # 承認後に語義を改訂 → dict-hash が変わる
    approve(root, "V-0003", "処理件数（改訂）")
    p2 = ledger.Project(root)
    assert p2.dict_hash("F-0003") != recorded
    assert p2.status_of(p2.func("F-0003"))["dict_stale"] is True
    # 他の関数（承認済み変数を持たない）は巻き込まない
    assert p2.status_of(p2.func("F-0000"))["dict_stale"] is False

    rc, out, err = run_ledger(root, "verify", "F-0003")
    assert rc == 1, out + err
    assert "NG: 辞書の語義が①生成後に改訂された → ①要再確認" in out, out
    assert "verify: NG" in out

    assert run_ledger(root, "wbs")[0] == 0
    wbs = (root / "docs" / "index.qmd").read_text(encoding="utf-8")
    assert "| ⚠ 辞書stale |" in wbs, wbs[:2000]
    assert "辞書の語義が①生成後に改訂 → ①要再確認" in wbs
    assert "辞書⚠" in wbs, "関数一覧の①列にも ⚠ が出る"
    print("OK  (d) dict_stale の検出（verify の NG と exit 1 / WBS の ⚠）")


# ---------- (e) 旧骨子（dict-hash 無し）は stale にしない ----------

def test_old_spec_without_dict_hash_is_not_stale():
    root = make_project()
    assert run_ledger(root, "skeletons")[0] == 0
    # 改修前に作られた仕様書を模して dict-hash 行を落とす
    sp = root / "docs" / "specs" / "F-0003.md"
    sp.write_text("\n".join(l for l in sp.read_text(encoding="utf-8-sig").splitlines()
                            if not l.startswith("dict-hash:")), encoding="utf-8")
    set_spec_status(root, "F-0003", "draft")
    for vid in vars_of(root, "F-0003"):
        approve(root, vid, f"{vid} の意味")

    p = ledger.Project(root)
    assert p.dict_hash("F-0003") != ""
    assert p.status_of(p.func("F-0003"))["dict_stale"] is False, \
        "dict-hash を持たない旧骨子を stale 扱いしてはいけない"
    rc, out, err = run_ledger(root, "verify", "F-0003")
    assert rc == 0 and "verify: OK" in out, out + err
    # 骨子の再同期対象にもならない（status が draft のため）
    assert "dict-hash" not in sp.read_text(encoding="utf-8")
    assert run_ledger(root, "skeletons")[0] == 0
    assert "dict-hash" not in sp.read_text(encoding="utf-8")
    print("OK  (e) 旧骨子（dict-hash 無し）は stale 扱いにしない")


# ---------- (f) pipeline._decide_kind のゲート ----------

def test_decide_kind_gate():
    root = make_project()
    assert run_ledger(root, "skeletons")[0] == 0
    pipeline._PROJECT_CACHE.clear()

    assert pipeline._decide_kind(root, "F-0003") is None, "未承認の変数が残る間は①を出さない"
    for vid in vars_of(root, "F-0003"):
        approve(root, vid, f"{vid} の意味")
    pipeline._PROJECT_CACHE.clear()
    assert pipeline._decide_kind(root, "F-0003") == "spec"

    # 判定は ledger 側の共通実装（Project.dict_gate_blockers）に委ねている
    orig = ledger.Project.dict_gate_blockers
    try:
        ledger.Project.dict_gate_blockers = lambda self, fid, spec_status=None: ["V-9999"]
        pipeline._PROJECT_CACHE.clear()
        assert pipeline._decide_kind(root, "F-0003") is None
    finally:
        ledger.Project.dict_gate_blockers = orig
        pipeline._PROJECT_CACHE.clear()

    # draft は免除（人の承認待ちなので None のままだが、
    # 再実行モードでは①が出る＝ゲートに掛かっていない）
    set_spec_status(root, "F-0000", "draft")
    pipeline._PROJECT_CACHE.clear()
    p = ledger.Project(root)
    assert p.dict_gate_blockers("F-0000") == []
    print("OK  (f) _decide_kind の dict-gate（ledger 共通実装を参照）")


def test_decide_kind_no_dict_gate():
    """`pipeline.py spec|run --no-dict-gate` でゲートを解除できる。

    辞書の整理を後回しにして①を先に進める運用（フラグはその1回の実行にだけ効き、
    辞書の status は変えない）。`ledger next --no-dict-gate` と同じ意味。
    """
    root = make_project()
    assert run_ledger(root, "skeletons")[0] == 0
    pipeline._PROJECT_CACHE.clear()

    assert pipeline._decide_kind(root, "F-0003") is None, "既定はゲート ON"
    assert pipeline._decide_kind(root, "F-0003", dict_gate=False) == "spec"

    # CLI にフラグが生えていて、既定は ON（＝ゲートが効く）
    ap = pipeline.build_parser()
    for cmd in ("spec", "run"):
        assert ap.parse_args([cmd]).dict_gate is True, cmd
        assert ap.parse_args([cmd, "--no-dict-gate"]).dict_gate is False, cmd
    print("OK  (f) pipeline --no-dict-gate でゲートを解除できる")


def test_batch_queue_shows_dict_gate():
    """バッチ処理画面（/batch-queue）に dict-gate の関数が「人待ち」として出る。

    黙って落とすと画面が「残タスクなし」に見え、①が語義の承認待ちで
    止まっていることを人が見失う（CLI を --no-dict-gate で回していると、
    実行されているのに画面に出ない、という食い違いにもなる）。
    """
    root = make_project()
    assert run_ledger(root, "skeletons")[0] == 0
    pipeline._PROJECT_CACHE.clear()

    d = pipeline.batch_queue(str(root))
    gated = [e for e in d["items"] if e["kind"] == "dict-gate"]
    assert gated, d["items"]
    assert all(e["auto"] is False and e["unapproved"] > 0 for e in gated), gated
    assert d["counts"].get("human", 0) >= len(gated), d["counts"]

    # 承認すれば自動実行待ち（①の作成）へ移る
    fid = gated[0]["func_id"]
    for vid in vars_of(root, fid):
        approve(root, vid, f"{vid} の意味")
    pipeline._PROJECT_CACHE.clear()
    d2 = pipeline.batch_queue(str(root))
    row = next(e for e in d2["items"] if e["func_id"] == fid)
    assert row["kind"] == "spec" and row["auto"] is True, row
    print("OK  (f) batch_queue が dict-gate を保留行として見せる")


def test_decide_kind_without_dict():
    root = make_project(with_dict=False)
    assert run_ledger(root, "skeletons")[0] == 0
    pipeline._PROJECT_CACHE.clear()
    for fid in ("F-0000", "F-0002", "F-0003"):
        assert pipeline._decide_kind(root, fid) == "spec", fid
    print("OK  (f) 辞書が無いプロジェクトの _decide_kind は従来どおり")


# ---------- (g) run_one の起動引数とログ名 ----------


class _StubStatus:
    """run_one が呼ぶ RunStatus のインタフェースだけを持つスタブ。"""

    def __init__(self):
        self.results = []

    def current(self, fid, attempt):
        pass

    def waiting(self, *a, **kw):
        pass

    def result(self, fid, ok, why, kind, sec, cost_total, r, attempt, problems=None, phase=None):
        self.results.append((fid, ok, phase))


def test_run_one_no_model_argument():
    root = make_project(with_dict=False)
    calls = []
    orig = pipeline.run_claude
    try:
        def fake(claude_cmd, prompt, root_, max_turns, extra, timeout, cancel_event=None,
                 **kw):                       # heartbeat / label は受け流す
            calls.append({"prompt": prompt, "extra": list(extra)})
            return {"ok": True, "cost_usd": 0.0, "tail": "", "err": "",
                    "stdout": "{}", "stderr": "", "exit_code": 0}
        pipeline.run_claude = fake

        st = _StubStatus()
        ok, why, r, cost, waited = pipeline.run_one(
            "dict:V-0001〜V-0002", ["claude"], ["--dangerously-skip-permissions"], root,
            "変数 {fid} を解釈する。例: {{\"V-0001\": {{\"desc\": \"…\"}}}}",
            50, 60, 0, 60, 900, 21600, st, 0.0, 0,
            verify_fn=lambda root_, fid: (True, "", []), phase="dict")
        # モデルは選ばない。渡した extra 以外の引数を勝手に足さないこと
        assert ok and calls[-1]["extra"] == ["--dangerously-skip-permissions"]
        assert "{fid}" not in calls[-1]["prompt"]
        assert '"V-0001": {"desc"' in calls[-1]["prompt"], "波括弧のエスケープが崩れている"
        assert "dict:V-0001〜V-0002" in calls[-1]["prompt"]

        ok, *_ = pipeline.run_one(
            "F-0001", ["claude"], [], root, "/legacy-1-spec {fid}", 50, 60, 0, 60, 900,
            21600, st, 0.0, 0, verify_fn=lambda root_, fid: (True, "", []), phase="spec")
        assert ok and calls[-1]["extra"] == []
        assert calls[-1]["prompt"] == "/legacy-1-spec F-0001"
    finally:
        pipeline.run_claude = orig

    # 識別子に ':' を含んでもエージェントログが書ける（Windows のファイル名制約）
    logs = sorted(p.name for p in (root / ".legacy-reverse" / "agent-logs").glob("*.txt"))
    assert logs == ["F-0001.txt", "dict_V-0001_V-0002.txt"], logs
    print("OK  (g) run_one は --model を付けない ＋ ログ名の正規化")


def main() -> None:
    tests = [
        test_dict_gate_excludes_and_can_be_disabled,
        test_dict_gate_blockers_api,
        test_gate_exempts_draft_and_reviewed,
        test_no_dict_backward_compat,
        test_dict_hash_recorded_and_synced,
        test_dict_stale_detected_after_revision,
        test_old_spec_without_dict_hash_is_not_stale,
        test_decide_kind_gate,
        test_decide_kind_no_dict_gate,
        test_batch_queue_shows_dict_gate,
        test_decide_kind_without_dict,
        test_run_one_no_model_argument,
    ]
    try:
        for t in tests:
            t()
        print(f"\nPASS: {len(tests)}件すべて成功")
    finally:
        for d in _TMPDIRS:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
