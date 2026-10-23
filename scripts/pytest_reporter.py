"""pytestの完全な実行記録を、AIの画面出力とは分離して保存する。"""

import json
import time
from pathlib import Path

import pytest


def pytest_addoption(parser):
    """既存テストの契約を変えない出力・選択オプション。"""
    parser.addoption("--verification-report")
    parser.addoption("--verification-select", action="append", default=[])
    parser.addoption("--verification-shard")


def pytest_configure(config):
    """実行ごとに状態を分離する。"""
    config._verification = {
        "started": time.perf_counter(),
        "all_collected": [],
        "selected": [],
        "reports": [],
        "collection_errors": [],
    }


def pytest_collection_modifyitems(session, config, items):
    """差分選択にはno_dbを常に含め、CI分割はファイル単位にする。"""
    state = config._verification
    state["all_collected"] = [item.nodeid for item in items]
    selected = set(config.getoption("--verification-select"))
    shard = config.getoption("--verification-shard")
    files = sorted({item.nodeid.split("::")[0] for item in items})
    allowed = None
    if shard:
        index, total = map(int, shard.split("/"))
        if not 1 <= index <= total or total > 8:
            raise pytest.UsageError("shard must be index/total, total <= 8")
        allowed = set(files[index - 1 :: total])
    keep, dropped = [], []
    for item in items:
        path = item.nodeid.split("::")[0]
        related = not selected or path in selected or item.get_closest_marker("no_db")
        (keep if related and (allowed is None or path in allowed) else dropped).append(
            item
        )
    items[:] = keep
    if dropped:
        config.hook.pytest_deselected(items=dropped)


def pytest_collection_finish(session):
    """組込み-m選択も反映した最終集合を保存する。"""
    session.config._verification["selected"] = [item.nodeid for item in session.items]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """setup/call/teardownの失敗・時間を欠落なく記録する。"""
    outcome = yield
    report = outcome.get_result()
    item.config._verification["reports"].append(
        {
            "id": report.nodeid,
            "phase": report.when,
            "outcome": report.outcome,
            "seconds": report.duration,
            "xfail": hasattr(report, "wasxfail"),
            "error": report.longreprtext if report.failed else "",
        }
    )


def pytest_sessionfinish(session, exitstatus):
    """完了通知がない記録はrunnerが成功扱いしない。"""
    state = session.config._verification
    state["wall_seconds"] = time.perf_counter() - state.pop("started")
    state["exit_code"] = int(exitstatus)
    state["finished"] = True
    target = session.config.getoption("--verification-report")
    if target:
        Path(target).write_text(json.dumps(state, ensure_ascii=False, indent=2))
