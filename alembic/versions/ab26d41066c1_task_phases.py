"""工程所属と工数根拠を既存値を保持して追加する。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from app.models.comments import db_comment

from alembic import op

revision = "ab26d41066c1"
down_revision = "fa14c7d2e658"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_phases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("project_id", "id", name="uq_project_phases_project_id"),
    )
    op.create_index("ix_project_phases_project_id", "project_phases", ["project_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_project_phases_name ON project_phases (project_id, lower(btrim(name)))"
    )
    op.create_table(
        "project_phase_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False
        ),
        sa.Column(
            "phase_id", sa.Integer(), sa.ForeignKey("project_phases.id"), nullable=False
        ),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("old_value", postgresql.JSONB()),
        sa.Column("new_value", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    for field in ("phase_id", "project_id"):
        op.create_index(
            f"ix_project_phase_events_{field}", "project_phase_events", [field]
        )
    op.add_column(
        "tasks",
        sa.Column(
            "phase_scope", sa.String(20), server_default="unclassified", nullable=False
        ),
    )
    op.add_column("tasks", sa.Column("phase_id", sa.Integer()))
    for metric in ("estimated", "actual"):
        op.add_column(
            "tasks",
            sa.Column(
                f"{metric}_basis", sa.String(20), server_default="own", nullable=False
            ),
        )
        op.execute(
            sa.text(
                f"UPDATE tasks p SET {metric}_basis='unreviewed' WHERE {metric}_minutes IS NOT NULL AND EXISTS (SELECT 1 FROM tasks c WHERE c.parent_task_id=p.id)"
            )
        )
    op.create_index("ix_tasks_phase_id", "tasks", ["phase_id"])
    op.create_foreign_key(
        "fk_tasks_project_phase",
        "tasks",
        "project_phases",
        ["project_id", "phase_id"],
        ["project_id", "id"],
    )
    op.create_check_constraint(
        "ck_tasks_phase_assignment",
        "tasks",
        "(phase_scope='phase' AND phase_id IS NOT NULL) OR (phase_scope IN ('unclassified','shared') AND phase_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_tasks_effort_basis",
        "tasks",
        "estimated_basis IN ('own','rollup','unreviewed') AND actual_basis IN ('own','rollup','unreviewed')",
    )
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname='syncnesto_app') THEN
      GRANT SELECT, INSERT, UPDATE, DELETE ON project_phases, project_phase_events TO syncnesto_app;
      GRANT USAGE, SELECT ON SEQUENCE project_phases_id_seq, project_phase_events_id_seq TO syncnesto_app;
    END IF; END $$;""")

    labels = {
        "project_phases": {
            "id": "工程ID",
            "project_id": "所有Project",
            "name": "工程名",
            "sort_order": "表示順",
            "version": "版",
            "archived_at": "アーカイブ日時",
            "created_by": "作成者",
            "updated_by": "更新者",
            "created_at": "作成日時",
            "updated_at": "更新日時",
        },
        "project_phase_events": {
            "id": "履歴ID",
            "project_id": "所有Project",
            "phase_id": "工程ID",
            "actor_id": "変更者",
            "old_value": "変更前の当時値",
            "new_value": "変更後の当時値",
            "created_at": "変更日時",
        },
        "tasks": {
            "phase_scope": "工程所属区分",
            "phase_id": "同一Projectの工程",
            "estimated_basis": "見積工数の根拠",
            "actual_basis": "実績工数の根拠",
        },
    }
    for table, columns in labels.items():
        for column, label in columns.items():
            op.alter_column(
                table, column, comment=db_comment(label, "工程所属と工数集計の契約")
            )
    op.create_table_comment(
        "project_phases", db_comment("工程", "Project内の工程とアーカイブ状態")
    )
    op.create_table_comment(
        "project_phase_events", db_comment("工程履歴", "当時の名前と状態を保持")
    )


def downgrade():
    used = op.get_bind().scalar(
        sa.text("""SELECT EXISTS(SELECT FROM project_phases)
      OR EXISTS(SELECT FROM project_phase_events)
      OR EXISTS(SELECT FROM tasks WHERE phase_scope <> 'unclassified'
        OR estimated_basis <> 'own' OR actual_basis <> 'own')""")
    )
    if used:
        raise RuntimeError("工程所属・工数根拠・原履歴を保持してください")
    op.drop_constraint("fk_tasks_project_phase", "tasks", type_="foreignkey")
    op.drop_constraint("ck_tasks_phase_assignment", "tasks", type_="check")
    op.drop_constraint("ck_tasks_effort_basis", "tasks", type_="check")
    op.drop_index("ix_tasks_phase_id", "tasks")
    for name in ("phase_scope", "phase_id", "estimated_basis", "actual_basis"):
        op.drop_column("tasks", name)
    op.drop_table("project_phase_events")
    op.drop_table("project_phases")
