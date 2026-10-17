"""固定版2表と、既存Project管理roleへの限定permissionを追加する。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f6b0d3e8c214"
down_revision = "e5a9c2f7b103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """旧業務行を変更せず、固定版とDB側の封印を追加する。"""
    op.create_table(
        "artifact_releases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False
        ),
        sa.Column("source_kind", sa.String(30), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("source_refs", postgresql.JSONB(), nullable=False),
        sa.Column("source_kinds", postgresql.JSONB(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("projection_version", sa.String(50), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("node_count", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("sealed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint(
            "project_id", "created_by", "request_id", name="uq_artifact_release_request"
        ),
        sa.CheckConstraint(
            "source_kind IN ('requirement','requirement_document','specification')",
            name="ck_artifact_release_source_kind",
        ),
        comment="成果物固定版: 本文と構成の不変な保存単位",
    )
    op.create_index(
        "ix_artifact_releases_project_id", "artifact_releases", ["project_id"]
    )
    op.create_index(
        "ix_artifact_release_source",
        "artifact_releases",
        ["project_id", "source_kind", "source_id"],
    )
    op.create_table(
        "artifact_release_nodes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "release_id",
            sa.Uuid(),
            sa.ForeignKey("artifact_releases.id"),
            nullable=False,
        ),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("node_kind", sa.String(30), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "release_id", "id", name="uq_artifact_release_node_identity"
        ),
        sa.UniqueConstraint(
            "release_id", "ordinal", name="uq_artifact_release_node_ordinal"
        ),
        sa.ForeignKeyConstraint(
            ["release_id", "parent_id"],
            ["artifact_release_nodes.release_id", "artifact_release_nodes.id"],
            name="fk_artifact_release_node_parent",
        ),
        sa.CheckConstraint("id <> parent_id", name="ck_artifact_release_node_not_self"),
        comment="固定版本文: 同じ固定版に属する本文と表示順",
    )
    op.execute("""
        CREATE FUNCTION guard_artifact_release_header() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.sealed THEN
              RAISE EXCEPTION 'sealed artifact release is immutable' USING ERRCODE='55000';
            END IF;
            RETURN OLD;
          END IF;
          IF OLD.sealed OR NOT NEW.sealed OR
             (to_jsonb(NEW) - 'sealed') <> (to_jsonb(OLD) - 'sealed') THEN
            RAISE EXCEPTION 'sealed artifact release is immutable' USING ERRCODE='55000';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER artifact_release_header_immutable BEFORE UPDATE OR DELETE
        ON artifact_releases FOR EACH ROW EXECUTE FUNCTION guard_artifact_release_header();
        CREATE FUNCTION guard_artifact_release_node() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE locked_sealed boolean;
        DECLARE target uuid;
        BEGIN
          IF TG_OP = 'UPDATE' AND (NEW.release_id <> OLD.release_id OR NEW.id <> OLD.id) THEN
            RAISE EXCEPTION 'artifact node cannot move' USING ERRCODE='55000';
          END IF;
          IF TG_OP = 'DELETE' THEN target := OLD.release_id;
          ELSE target := NEW.release_id; END IF;
          SELECT sealed INTO locked_sealed FROM artifact_releases WHERE id=target FOR UPDATE;
          IF NOT FOUND OR locked_sealed THEN
            RAISE EXCEPTION 'sealed artifact nodes are immutable' USING ERRCODE='55000';
          END IF;
          IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END $$;
        CREATE TRIGGER artifact_release_node_immutable BEFORE INSERT OR UPDATE OR DELETE
        ON artifact_release_nodes FOR EACH ROW EXECUTE FUNCTION guard_artifact_release_node();
    """)
    op.execute("""
        INSERT INTO permissions (code, description) VALUES
          ('artifact_release:create', '成果物の固定版を作成する'),
          ('artifact_release:read', '成果物の固定版を閲覧する')
        ON CONFLICT (code) DO NOTHING;
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.scope='project' AND r.key IN ('project_admin','manager')
          AND p.code IN ('artifact_release:create','artifact_release:read')
        ON CONFLICT DO NOTHING;
    """)


def downgrade() -> None:
    """固定版がある場合は破壊的なdowngradeを拒否する。"""
    connection = op.get_bind()
    if connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM artifact_releases)")
    ).scalar():
        raise RuntimeError(
            "固定版を保全するため、データのあるdowngradeは実行できません"
        )
    op.execute("""
        DELETE FROM role_permissions WHERE permission_id IN
          (SELECT id FROM permissions WHERE code IN ('artifact_release:create','artifact_release:read'));
        DELETE FROM permissions WHERE code IN ('artifact_release:create','artifact_release:read');
    """)
    op.drop_table("artifact_release_nodes")
    op.drop_table("artifact_releases")
    op.execute("DROP FUNCTION guard_artifact_release_node()")
    op.execute("DROP FUNCTION guard_artifact_release_header()")
