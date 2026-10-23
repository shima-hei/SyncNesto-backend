"""公開準備版2表と内部管理者向けの2権限を追加する。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "dae4b7c2e658"
down_revision = "c9e3f6a1d547"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """元本文・判断を変更せず、封印した準備版だけを保存する。"""
    op.create_table(
        "artifact_publications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False
        ),
        sa.Column(
            "release_id",
            sa.Uuid(),
            sa.ForeignKey("artifact_releases.id"),
            nullable=False,
        ),
        sa.Column(
            "preservation_id", sa.Uuid(), sa.ForeignKey("artifact_preservations.id")
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("projection_version", sa.String(50), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("source_kinds", postgresql.JSONB(), nullable=False),
        sa.Column("source_manifest", postgresql.JSONB(), nullable=False),
        sa.Column("source_manifest_digest", sa.String(64), nullable=False),
        sa.Column("file_manifest", postgresql.JSONB(), nullable=False),
        sa.Column("public_digest", sa.String(64), nullable=False),
        sa.Column("body_digest", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("node_count", sa.Integer(), nullable=False),
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
        sa.Column("sealed", sa.Boolean(), nullable=False, server_default="false"),
        sa.UniqueConstraint(
            "project_id",
            "created_by",
            "request_id",
            name="uq_artifact_publication_request",
        ),
        sa.CheckConstraint(
            "schema_version = 1 AND projection_version = 'public-preview-v1'",
            name="ck_artifact_publication_schema",
        ),
        sa.CheckConstraint(
            "node_count BETWEEN 1 AND 50000 AND byte_size BETWEEN 1 AND 33554432",
            name="ck_artifact_publication_capacity",
        ),
        comment="公開準備版: 内部固定版から作る不変な未公開の表現",
    )
    op.create_index(
        "ix_artifact_publications_project_id", "artifact_publications", ["project_id"]
    )
    op.create_index(
        "ix_artifact_publications_release_id", "artifact_publications", ["release_id"]
    )
    op.create_table(
        "artifact_publication_nodes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "publication_id",
            sa.Uuid(),
            sa.ForeignKey("artifact_publications.id"),
            nullable=False,
        ),
        sa.Column("parent_id", sa.Uuid()),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("node_kind", sa.String(30), nullable=False),
        sa.Column("source_version", sa.Integer()),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "publication_id", "id", name="uq_artifact_publication_node_identity"
        ),
        sa.UniqueConstraint(
            "publication_id", "ordinal", name="uq_artifact_publication_node_ordinal"
        ),
        sa.ForeignKeyConstraint(
            ["publication_id", "parent_id"],
            [
                "artifact_publication_nodes.publication_id",
                "artifact_publication_nodes.id",
            ],
            name="fk_artifact_publication_node_parent",
        ),
        sa.CheckConstraint(
            "id <> parent_id AND ordinal >= 0 AND position >= 0",
            name="ck_artifact_publication_node_order",
        ),
        sa.CheckConstraint(
            "node_kind IN ('requirement_document','section','requirement','requirement_detail','specification','test_design','test_design_part')",
            name="ck_artifact_publication_node_kind",
        ),
        comment="公開準備版本文: 公開用表現の本文と順序",
    )
    op.create_index(
        "ix_artifact_publication_nodes_publication_id",
        "artifact_publication_nodes",
        ["publication_id"],
    )
    op.execute("""
      CREATE FUNCTION guard_artifact_publication_header() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE rel artifact_releases; pre artifact_preservations;
      BEGIN
        IF TG_OP='DELETE' THEN
          RAISE EXCEPTION 'publication must be retained' USING ERRCODE='55000';
        END IF;
        IF TG_OP='UPDATE' AND (OLD.sealed OR NOT NEW.sealed OR
            (to_jsonb(NEW)-'sealed')<>(to_jsonb(OLD)-'sealed')) THEN
          RAISE EXCEPTION 'publication is immutable' USING ERRCODE='55000';
        END IF;
        IF TG_OP='INSERT' AND NEW.sealed THEN
          RAISE EXCEPTION 'publication must be completed before sealing' USING ERRCODE='55000';
        END IF;
        SELECT * INTO rel FROM artifact_releases WHERE id=NEW.release_id;
        IF NOT FOUND OR NOT rel.sealed OR rel.project_id<>NEW.project_id OR
           rel.content_digest<>NEW.body_digest OR NEW.source_kinds<>rel.source_kinds OR
           (NEW.source_manifest->>'release_id') IS DISTINCT FROM rel.id::text OR
           (NEW.source_manifest->>'body_digest') IS DISTINCT FROM rel.content_digest OR
           (NEW.source_manifest->'sources') IS DISTINCT FROM rel.source_refs OR
           (NEW.source_manifest->>'preservation_id') IS DISTINCT FROM NEW.preservation_id::text THEN
          RAISE EXCEPTION 'publication source does not match' USING ERRCODE='23514';
        END IF;
        IF NEW.preservation_id IS NOT NULL THEN
          SELECT * INTO pre FROM artifact_preservations WHERE id=NEW.preservation_id;
          IF NOT FOUND OR pre.release_id<>rel.id OR pre.status<>'ready' OR
             (NEW.source_manifest->>'preservation_digest') IS DISTINCT FROM pre.manifest_digest OR
             jsonb_array_length(NEW.file_manifest)<>jsonb_array_length(pre.manifest->'files') OR EXISTS(
               SELECT 1 FROM jsonb_array_elements(NEW.file_manifest) WITH ORDINALITY f(item,pos)
               WHERE (f.item-'id') IS DISTINCT FROM
                 ((pre.manifest->'files'->(f.pos::integer-1))-
                   ARRAY['document_id','attachment_id','document_version'])
             ) THEN
            RAISE EXCEPTION 'publication preservation does not match' USING ERRCODE='23514';
          END IF;
        ELSIF jsonb_array_length(NEW.file_manifest)<>0 THEN
          RAISE EXCEPTION 'publication files require preservation' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER artifact_publication_header_guard BEFORE INSERT OR UPDATE OR DELETE ON artifact_publications
        FOR EACH ROW EXECUTE FUNCTION guard_artifact_publication_header();
      CREATE FUNCTION guard_artifact_publication_node() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE target uuid; locked_sealed boolean;
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'publication nodes are immutable' USING ERRCODE='55000';
        END IF;
        target := NEW.publication_id;
        SELECT sealed INTO locked_sealed FROM artifact_publications WHERE id=target FOR UPDATE;
        IF NOT FOUND OR locked_sealed THEN
          RAISE EXCEPTION 'publication nodes are sealed' USING ERRCODE='55000';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER artifact_publication_node_guard BEFORE INSERT OR UPDATE OR DELETE ON artifact_publication_nodes
        FOR EACH ROW EXECUTE FUNCTION guard_artifact_publication_node();
      CREATE FUNCTION check_artifact_publication_complete() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE pub artifact_publications; n integer; lo integer; hi integer;
      BEGIN
        SELECT * INTO pub FROM artifact_publications WHERE id=NEW.id;
        SELECT count(*), min(ordinal), max(ordinal) INTO n,lo,hi FROM artifact_publication_nodes WHERE publication_id=pub.id;
        IF NOT pub.sealed OR n<>pub.node_count OR lo<>0 OR hi<>n-1 OR NOT EXISTS(
          SELECT 1 FROM artifact_publication_nodes WHERE publication_id=pub.id AND parent_id IS NULL
        ) OR EXISTS(
          SELECT 1 FROM artifact_publication_nodes child JOIN artifact_publication_nodes parent
          ON child.parent_id=parent.id WHERE child.publication_id=pub.id AND parent.ordinal>=child.ordinal
        ) THEN
          RAISE EXCEPTION 'publication is incomplete' USING ERRCODE='23514';
        END IF;
        RETURN NULL;
      END $$;
      CREATE CONSTRAINT TRIGGER artifact_publication_complete AFTER INSERT OR UPDATE ON artifact_publications
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_artifact_publication_complete();
      INSERT INTO permissions(code,description) VALUES
        ('artifact_publication:manage','成果物の公開準備版を生成する'),
        ('artifact_publication:read','成果物の公開準備版を内部で確認する') ON CONFLICT(code) DO NOTHING;
      INSERT INTO role_permissions(role_id,permission_id)
        SELECT r.id,p.id FROM roles r CROSS JOIN permissions p
        WHERE r.scope='project' AND r.key IN ('project_admin','manager')
        AND p.code IN ('artifact_publication:manage','artifact_publication:read') ON CONFLICT DO NOTHING;
    """)


def downgrade() -> None:
    """データのある準備版を消さず、空DBだけを元へ戻す。"""
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE artifact_publications,artifact_publication_nodes IN ACCESS EXCLUSIVE MODE"
        )
    )
    if connection.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM artifact_publications)")):
        raise RuntimeError(
            "公開準備版を保持するため、データのあるdowngradeは実行できません"
        )
    op.drop_table("artifact_publication_nodes")
    op.drop_table("artifact_publications")
    for name in (
        "check_artifact_publication_complete",
        "guard_artifact_publication_node",
        "guard_artifact_publication_header",
    ):
        op.execute(f"DROP FUNCTION {name}()")
    op.execute("""
      DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions
        WHERE code IN ('artifact_publication:manage','artifact_publication:read'));
      DELETE FROM permissions WHERE code IN ('artifact_publication:manage','artifact_publication:read');
    """)
