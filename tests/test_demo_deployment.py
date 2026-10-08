"""公開設定に秘密を混ぜず、通常環境へCronを増やさない。"""

import pytest

from scripts.configure_demo_deployment import deployment_config

pytestmark = pytest.mark.no_db


def test_demo_adds_only_daily_cron():
    base = {"functions": {"app/main.py": {"maxDuration": 60}}}
    assert deployment_config(base, "demo")["crons"] == [
        {"path": "/internal/demo/cleanup", "schedule": "0 18 * * *"}
    ]
    assert deployment_config(base, "production") == base
    assert "crons" not in base


def test_unknown_environment_fails_closed():
    with pytest.raises(ValueError):
        deployment_config({}, "development")
