"""Bind requirement workflow guests to an explicit publication grant.

Revision ID: 3f6d1ab94702
Revises: f305b1acebc1
"""

import sqlalchemy as sa

from alembic import op

revision = "3f6d1ab94702"
down_revision = "f305b1acebc1"
branch_labels = None
depends_on = None

TABLE = "requirement_workflow_guest_bindings"


def upgrade() -> None:
    """既存の公開権限と依頼の組に対応を追加し、本文や回答は移動しない。"""
    op.create_table(
        TABLE,
        sa.Column("request_id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("publication_id", sa.Uuid(), nullable=False),
        sa.Column("target_node_id", sa.Uuid(), nullable=False),
        sa.Column("grant_id", sa.Uuid(), nullable=False),
        sa.Column("participation_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id", "user_id"],
            [
                "requirement_workflow_participants.request_id",
                "requirement_workflow_participants.user_id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "publication_id"],
            ["artifact_publications.project_id", "artifact_publications.id"],
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "grant_id"],
            [
                "artifact_publication_grants.project_id",
                "artifact_publication_grants.id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "participation_id"],
            [
                "project_guest_participations.project_id",
                "project_guest_participations.id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["publication_id", "target_node_id"],
            [
                "artifact_publication_nodes.publication_id",
                "artifact_publication_nodes.id",
            ],
        ),
    )
    op.create_index(
        "ix_requirement_workflow_guest_inbox", TABLE, ["user_id", "request_id"]
    )
    op.execute("""
        CREATE FUNCTION requirement_workflow_guest_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
              MESSAGE = 'guest workflow binding is immutable';
          END IF;
          IF NOT EXISTS (
            SELECT 1
            FROM requirement_workflow_requests r
            JOIN artifact_publications p ON p.id = NEW.publication_id
              AND p.project_id = r.project_id AND p.release_id = r.release_id
            JOIN artifact_publication_grants g ON g.id = NEW.grant_id
              AND g.project_id = r.project_id AND g.publication_id = p.id
              AND g.participation_id = NEW.participation_id
            JOIN project_guest_participations part ON part.id = g.participation_id
              AND part.project_id = r.project_id AND part.user_id = NEW.user_id
            WHERE r.id = NEW.request_id AND r.project_id = NEW.project_id
              AND p.source_manifest->'id_map'->>NEW.target_node_id::text
                = r.target_node_id::text
          ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
              MESSAGE = 'guest workflow binding scope mismatch';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER requirement_workflow_guest_guard
          BEFORE INSERT OR UPDATE OR DELETE ON requirement_workflow_guest_bindings
          FOR EACH ROW EXECUTE FUNCTION requirement_workflow_guest_guard();
    """)


def downgrade() -> None:
    """利用済みの共有範囲と権限の証跡がある場合は保持する。"""
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM requirement_workflow_guest_bindings) THEN
            RAISE EXCEPTION 'guest workflow bindings exist';
          END IF;
        END; $$;
        DROP TRIGGER requirement_workflow_guest_guard
          ON requirement_workflow_guest_bindings;
        DROP FUNCTION requirement_workflow_guest_guard();
    """)
    op.drop_table(TABLE)
