"""runnerの誤成功・資格情報継承・影響判定の取りこぼしを防ぐ。"""

import json

import pytest

from scripts import verification as v
from scripts.check_verification import check

pytestmark = pytest.mark.no_db


def test_summary_requires_finished_exit_zero_and_teardown():
    report = {
        "finished": True,
        "all_collected": ["a"],
        "selected": ["a"],
        "reports": [
            {"id": "a", "phase": "setup", "outcome": "passed", "seconds": 0},
            {"id": "a", "phase": "call", "outcome": "passed", "seconds": 1},
            {"id": "a", "phase": "teardown", "outcome": "passed", "seconds": 0},
        ],
    }
    assert v.summarize(report, 0)["status"] == "passed"
    assert v.summarize(report, 0, True)["status"] == "failed"
    assert v.summarize(report, 2)["status"] == "failed"
    assert v.summarize({}, 0)["status"] == "failed"
    report["reports"].pop()
    assert v.summarize(report, 0)["status"] == "failed"
    report["reports"].append(
        {"id": "a", "phase": "teardown", "outcome": "passed", "seconds": 0}
    )
    report["reports"].pop(1)
    assert v.summarize(report, 0)["status"] == "failed"


@pytest.mark.parametrize(
    "path",
    [
        "app/core/security.py",
        "app/db/session.py",
        "app/models/task.py",
        "tests/conftest.py",
        "uv.lock",
        "app/services/new_domain.py",
        "alembic/versions/new.py",
    ],
)
def test_unmapped_or_shared_changes_require_full(monkeypatch, path):
    monkeypatch.setattr(v, "changed_paths", lambda _: [path])
    targets, reason = v.select("changed", "HEAD")
    assert targets == [] and reason.startswith("full:")


def test_changed_notifications_include_related_and_no_db(monkeypatch):
    monkeypatch.setattr(v, "changed_paths", lambda _: ["app/routers/notifications.py"])
    targets, reason = v.select("changed", "HEAD")
    assert "tests/routers/test_notifications.py" in targets
    assert "tests/routers/test_fixed_approval_notifications.py" in targets
    assert reason == "related + no_db"


def test_environment_does_not_inherit_real_credentials(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret@production/live")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "real-secret")
    env = v.safe_environment()
    assert "DATABASE_URL" not in env and "SMTP_PASSWORD" not in env
    assert env["AWS_SECRET_ACCESS_KEY"] == "test"
    assert env["PYTHON_DOTENV_DISABLED"] == env["SYNCNESTO_VERIFY"] == "1"


@pytest.mark.parametrize(
    "damage",
    [None, "missing", "overlap", "universe", "duplicate", "failed", "unfinished"],
)
def test_ci_gate_requires_every_case_exactly_once(tmp_path, damage):
    for number, selected in [(1, ["a"]), (2, ["b"])]:
        folder = tmp_path / str(number)
        folder.mkdir()
        result = {
            "status": "passed",
            "complete": True,
            "scope": {"shard": f"{number}/2"},
        }
        report = {"all_collected": ["a", "b"], "selected": selected}
        if number == 2:
            if damage == "missing":
                continue
            if damage == "overlap":
                report["selected"] = ["a", "b"]
            if damage == "universe":
                report["all_collected"] = ["b"]
            if damage == "duplicate":
                result["scope"]["shard"] = "1/2"
            if damage == "failed":
                result["status"] = "failed"
            if damage == "unfinished":
                result["complete"] = False
        (folder / "result.json").write_text(json.dumps(result))
        (folder / "report.json").write_text(json.dumps(report))
    if damage:
        with pytest.raises(ValueError):
            check(tmp_path, 2)
    else:
        assert check(tmp_path, 2) == 2


def test_failed_setup_and_teardown_are_never_hidden():
    report = {
        "finished": True,
        "selected": ["a"],
        "reports": [
            {
                "id": "a",
                "phase": phase,
                "outcome": "failed",
                "seconds": 0,
                "error": phase,
            }
            for phase in ["setup", "teardown"]
        ],
    }
    result = v.summarize(report, 1)
    assert result["status"] == "failed"
    assert {f["phase"] for f in result["failures"]} == {"setup", "teardown"}


def test_content_fingerprint_changes_for_uncommitted_deleted_and_added_files(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(v, "ROOT", tmp_path)
    monkeypatch.setattr(v, "git", lambda *args: "source.py\0new.py")
    source = tmp_path / "source.py"
    source.write_text("original")
    original = v.fingerprint({"profile": "full"})
    assert v.fingerprint({"profile": "full"}) == original
    source.write_text("edited")
    edited = v.fingerprint({"profile": "full"})
    assert edited != original
    source.unlink()
    deleted = v.fingerprint({"profile": "full"})
    assert deleted != edited
    (tmp_path / "new.py").write_text("new test")
    assert v.fingerprint({"profile": "full"}) != deleted
