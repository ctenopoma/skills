"""pytest プラグイン: @pytest.mark.tc("F0123-TC-001") マーカー別に結果を収集する。

使い方（⑤skill が実行する。外部依存なし）:
    pytest tests/test_tax.py -p tc_report_plugin
出力: <rootdir>/.legacy-reverse/last-run.json
    [{"nodeid", "tc", "outcome", "duration", "longrepr"}, ...]
collect_results.py がこれと②のケース一覧を突合して結果報告書を生成する。
"""
import json
from pathlib import Path

import pytest

_RESULTS: dict = {}


def pytest_configure(config):
    config.addinivalue_line("markers", "tc(id): テスト仕様書のケースID")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    # call の結果を正とし、setup/teardown 失敗（error）も拾う
    if rep.when == "call" or (rep.when in ("setup", "teardown") and rep.outcome == "failed"):
        marker = item.get_closest_marker("tc")
        _RESULTS[item.nodeid] = {
            "nodeid": item.nodeid,
            "tc": marker.args[0] if marker and marker.args else None,
            "outcome": rep.outcome,  # passed / failed / skipped
            "duration": round(rep.duration, 3),
            "longrepr": str(rep.longrepr)[:2000] if rep.outcome == "failed" else "",
        }


def pytest_sessionfinish(session, exitstatus):
    out = Path(str(session.config.rootpath)) / ".legacy-reverse" / "last-run.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(list(_RESULTS.values()), ensure_ascii=False, indent=1),
                   encoding="utf-8")
