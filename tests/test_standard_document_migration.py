"""空の台帳だけ戻せ、使用済みの再送記録は失わない。"""

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command
from app.models.requirement_document_creation import RequirementDocumentCreationReceipt


def test_empty_receipt_migration_round_trip(db):
    db.commit()
    config = Config("alembic.ini")
    command.downgrade(config, "f9b3c6e1d547")
    assert (
        "requirement_document_creation_receipts"
        not in inspect(db.bind).get_table_names()
    )
    command.upgrade(config, "head")
    constraints = inspect(db.bind).get_unique_constraints(
        "requirement_document_creation_receipts"
    )
    assert any(
        row["column_names"] == ["project_id", "actor_id", "request_key"]
        for row in constraints
    )


def test_used_receipt_prevents_downgrade(db, create_test_user, create_test_project):
    from uuid import uuid4

    actor = create_test_user()
    project = create_test_project()
    db.add(
        RequirementDocumentCreationReceipt(
            project_id=project.id,
            actor_id=actor.id,
            request_key=uuid4(),
            request_hash="a" * 64,
        )
    )
    db.commit()
    with pytest.raises(RuntimeError, match="再送記録"):
        command.downgrade(Config("alembic.ini"), "f9b3c6e1d547")
    assert (
        db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        == "fa14c7d2e658"
    )
    assert db.query(RequirementDocumentCreationReceipt).count() == 1
