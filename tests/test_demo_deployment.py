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


@pytest.mark.parametrize("mode", ["dry_run", "execute"])
def test_normal_cleanup_is_opt_in_and_does_not_keep_demo_cron(mode):
    """通常回収は明示した時だけ追加し、環境を跨ぐ古いCronは残さない。"""
    base = {"crons": [{"path": "/internal/demo/cleanup", "schedule": "0 18 * * *"}]}
    assert deployment_config(base, "production", mode)["crons"] == [
        {"path": "/internal/trash/cleanup", "schedule": "0 18 * * *"}
    ]
    assert "crons" not in deployment_config(base, "production")
    assert base["crons"][0]["path"] == "/internal/demo/cleanup"
    with pytest.raises(ValueError):
        deployment_config(base, "demo", mode)
    with pytest.raises(ValueError):
        deployment_config(base, "production", "Execute")
