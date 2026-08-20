#!/usr/bin/env python3
"""無人バッチ（pipeline.py）の CLI 契約のセルフテスト（pytest不要・素の assert）。

`python test_pipeline_args.py` で実行する。検証範囲:

1. **--skip-permissions が既定 ON** であること（全サブコマンド）。headless は許可
   プロンプトに答えられず、未許可のツール呼び出しは黙って拒否される。既定が
   「聞く」だと「応答はあるのにファイルが更新されない」失敗を工程ごとに繰り返す
   ため、既定を倒した。安全装置は hooks（guard_tests.py / guard_json.py）が担う
2. `--no-skip-permissions` で明示的に外せること（許可で止めたい環境向け）
3. permission_args() が上の状態をそのまま起動引数に落とすこと
4. **実行中の生存表示**。headless の1プロセスは数分〜30分かかる（dict は1チャンク
   =変数40件で1プロセス）。無音だと「固まった／応答がない」としか見えないため、
   run_claude が heartbeat 間隔で経過を出すこと
"""
import contextlib
import io
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))
import pipeline                                                    # noqa: E402

# 実行系のサブコマンド（priority は claude を起動しないので対象外）
DRIVER_CMDS = ["spec", "run", "testspec", "testcode", "impl", "test", "dict"]


def test_skip_permissions_defaults_on():
    ap = pipeline.build_parser()
    for cmd in DRIVER_CMDS:
        args = ap.parse_args([cmd])
        assert args.skip_permissions is True, \
            f"{cmd}: --skip-permissions が既定 ON でない（headless が許可待ちで詰まる）"
    print("OK  --skip-permissions は全サブコマンドで既定 ON")


def test_skip_permissions_opt_out():
    ap = pipeline.build_parser()
    for cmd in DRIVER_CMDS:
        args = ap.parse_args([cmd, "--no-skip-permissions"])
        assert args.skip_permissions is False, f"{cmd}: --no-skip-permissions が効かない"
    print("OK  --no-skip-permissions で外せる")


def test_permission_args():
    ap = pipeline.build_parser()
    on = pipeline.permission_args(ap.parse_args(["dict"]))
    assert on == ["--dangerously-skip-permissions"], on
    off = pipeline.permission_args(ap.parse_args(["dict", "--no-skip-permissions"]))
    assert off == [], off
    print("OK  permission_args() の組み立て")


def test_heartbeat_prints_while_running():
    """待っている間、コンソールに経過が出ること（無音区間を作らない）。"""
    slow = [sys.executable, "-c", "import time; time.sleep(2.2); print('{}')"]
    buf = io.StringIO()
    t0 = time.time()
    with contextlib.redirect_stdout(buf):
        r = pipeline.run_claude(slow, "x", Path("."), 5, [], 30,
                                heartbeat=1, label="dict:V-0001〜V-0040")
    out = buf.getvalue()
    assert r.get("exit_code") == 0, r
    assert out.count("実行中") >= 2, f"生存表示が出ていない（{time.time() - t0:.1f}s）: {out!r}"
    assert "dict:V-0001〜V-0040" in out, out

    # heartbeat=0（既定）なら何も出さない＝従来と同じ
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        pipeline.run_claude([sys.executable, "-c", "print('{}')"], "x", Path("."), 5, [], 30)
    assert buf.getvalue() == "", buf.getvalue()
    print("OK  実行中の生存表示（heartbeat）")


def main() -> int:
    tests = [test_skip_permissions_defaults_on, test_skip_permissions_opt_out,
             test_permission_args, test_heartbeat_prints_while_running]
    for t in tests:
        t()
    print(f"\nPASS: {len(tests)}件すべて成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())
