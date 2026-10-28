"""旧値と原履歴を残す前進migrationの検証。"""

import pytest
from alembic.config import Config
from sqlalchemy import text

from alembic import command


def test_legacy_parent_values_are_not_assumed_to_be_own_effort(db, create_test_project):
    project = create_test_project()
    db.commit()
    config = Config("alembic.ini")
    command.downgrade(config, "fa14c7d2e658")
    parent = db.execute(
        text("""INSERT INTO tasks(project_id,task_code,title,task_type,status,
      priority,progress_percent,estimated_minutes,actual_minutes,sort_order,version)
      VALUES(:project,'LEGACY-P','親','other','backlog','medium',20,300,30,4,7)
      RETURNING id"""),
        {"project": project.id},
    ).scalar_one()
    db.execute(
        text("""INSERT INTO tasks(project_id,parent_task_id,task_code,title,
      task_type,status,priority,progress_percent,estimated_minutes,
      sort_order,version,deleted_at)
      VALUES(:project,:parent,'LEGACY-C','削除済み子','other','backlog','medium',0,100,0,3,now())"""),
        {"project": project.id, "parent": parent},
    )
    db.commit()
    command.upgrade(config, "head")
    row = (
        db.execute(text("SELECT * FROM tasks WHERE id=:id"), {"id": parent})
        .mappings()
        .one()
    )
    assert (
        row["estimated_minutes"],
        row["actual_minutes"],
        row["version"],
        row["sort_order"],
        row["progress_percent"],
    ) == (300, 30, 7, 4, 20)
    assert row["estimated_basis"] == row["actual_basis"] == "unreviewed"
    assert row["phase_scope"] == "unclassified" and row["phase_id"] is None
    db.commit()
    with pytest.raises(RuntimeError, match="工数根拠"):
        command.downgrade(config, "fa14c7d2e658")
