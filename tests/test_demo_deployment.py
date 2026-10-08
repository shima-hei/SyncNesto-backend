"""公開設定に秘密を混ぜず、通常環境へCronを増やさない。"""

import pytest

from scripts.configure_demo_deployment import deployment_config, main

pytestmark = pytest.mark.no_db


def test_demo_adds_only_daily_cron():
    base = {"functions": {"app/main.py": {"maxDuration": 60}}}
    assert deployment_config(base, "production", demo_mode=True)["crons"] == [
        {"path": "/internal/demo/cleanup", "schedule": "0 18 * * *"}
    ]
    assert deployment_config(base, "production") == base
    assert "crons" not in base


@pytest.mark.parametrize("app_env", ["development", "test", "demo", ""])
def test_unknown_environment_fails_closed(app_env):
    with pytest.raises(ValueError):
        deployment_config({}, app_env, demo_mode=True)


@pytest.mark.parametrize("mode", ["dry_run", "execute"])
def test_normal_cleanup_is_opt_in_and_does_not_keep_demo_cron(mode):
    """通常回収は明示した時だけ追加し、環境を跨ぐ古いCronは残さない。"""
    base = {"crons": [{"path": "/internal/demo/cleanup", "schedule": "0 18 * * *"}]}
    assert deployment_config(base, "production", mode)["crons"] == [
        {"path": "/internal/trash/cleanup", "schedule": "0 19 * * *"}
    ]
    assert "crons" not in deployment_config(base, "production")
    assert base["crons"][0]["path"] == "/internal/demo/cleanup"
    assert deployment_config(base, "production", mode, demo_mode=True)["crons"] == [
        {"path": "/internal/demo/cleanup", "schedule": "0 18 * * *"},
        {"path": "/internal/trash/cleanup", "schedule": "0 19 * * *"},
    ]
    with pytest.raises(ValueError):
        deployment_config(base, "production", "Execute")


@pytest.mark.parametrize("demo_mode", [False, True], ids=["normal", "demo"])
@pytest.mark.parametrize(
    "demo_connections", [False, True], ids=["without-demo-db", "with-demo-db"]
)
def test_pulled_demo_flag_selects_cron_without_copying_secrets(
    tmp_path, monkeypatch, demo_mode, demo_connections
):
    """実際のpull済みファイルを読み、productionのままCronだけを切り替える。"""
    import json

    import scripts.configure_demo_deployment as module

    (tmp_path / "scripts").mkdir()
    (tmp_path / ".vercel").mkdir()
    (tmp_path / "vercel.json").write_text("{}")
    (tmp_path / ".vercel/.env.production.local").write_text(
        f"APP_ENV=production\nDEMO_MODE={str(demo_mode).lower()}\nCRON_SECRET=private-fixture\n"
        + (
            "DEMO_DATA_ISOLATED=true\nDEMO_DATABASE_URL=private-demo-uri\n"
            if demo_connections
            else ""
        )
    )
    monkeypatch.setattr(
        module, "__file__", str(tmp_path / "scripts/configure_demo_deployment.py")
    )
    main()
    generated = (tmp_path / ".vercel/deploy-config.json").read_text()
    assert "private-fixture" not in generated and "CRON_SECRET" not in generated
    assert "private-demo-uri" not in generated
    config = json.loads(generated)
    if demo_mode or demo_connections:
        assert config["crons"][0]["path"] == "/internal/demo/cleanup"
    else:
        assert "crons" not in config
