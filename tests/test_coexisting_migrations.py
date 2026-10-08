"""公開前migrationも通常とデモの接続を取り違えない。"""

import pytest

from scripts import migrate_production as migration

pytestmark = pytest.mark.no_db
NORMAL = "postgresql://syncnesto_owner:test@ep-normal.neon.tech/syncnesto?sslmode=verify-full"
DEMO = (
    "postgresql://syncnesto_owner:test@ep-demo.neon.tech/syncnesto?sslmode=verify-full"
)


def test_normal_target_is_preserved_and_demo_is_added_when_configured():
    env = {"MIGRATION_DATABASE_URL": NORMAL, "DEMO_MIGRATION_DATABASE_URL": DEMO}
    assert migration.migration_targets(env, {}) == [("normal", NORMAL)]
    assert migration.migration_targets(env, {"DEMO_MODE": "true"}) == [
        ("normal", NORMAL),
        ("demo", DEMO),
    ]
    assert migration.migration_targets(
        env, {"DEMO_MODE": "false", "DEMO_DATA_ISOLATED": "true"}
    ) == [("normal", NORMAL), ("demo", DEMO)]


@pytest.mark.parametrize(
    "demo",
    [
        "",
        NORMAL,
        DEMO.replace("verify-full", "require"),
        DEMO.replace("ep-demo", "ep-demo-pooler"),
        DEMO.replace("syncnesto_owner", "syncnesto_app"),
    ],
)
def test_invalid_demo_target_stops_before_any_migration(demo, monkeypatch):
    monkeypatch.setenv("MIGRATION_DATABASE_URL", NORMAL)
    monkeypatch.setenv("DEMO_MIGRATION_DATABASE_URL", demo)
    monkeypatch.setattr(migration, "dotenv_values", lambda _: {"DEMO_MODE": "true"})
    monkeypatch.setattr(
        migration, "migrate", lambda _: pytest.fail("Must validate both targets first")
    )
    assert migration.main() == 2
