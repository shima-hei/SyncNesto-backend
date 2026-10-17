"""添付保全の構成と、PUT前から残す実体台帳を追加する。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a7c1e4f9b325"
down_revision = "f6b0d3e8c214"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """旧業務行・権限・元添付の保存先は変更しない。"""
    op.create_table(
        "artifact_preservations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "release_id",
            sa.Uuid(),
            sa.ForeignKey("artifact_releases.id"),
            nullable=False,
        ),
        sa.Column(
            "created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("plan_digest", sa.String(64), nullable=False),
        sa.Column("selection", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(), nullable=True),
        sa.Column("manifest_digest", sa.String(64), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("lease_id", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "release_id",
            "created_by",
            "request_id",
            name="uq_artifact_preservation_request",
        ),
        sa.CheckConstraint(
            "status IN ('preparing','ready','failed','cancelled')",
            name="ck_artifact_preservation_status",
        ),
        sa.CheckConstraint(
            "jsonb_array_length(selection) BETWEEN 1 AND 20",
            name="ck_artifact_preservation_selection",
        ),
        comment="添付保全構成: 固定本文と選択した添付実体の対応と準備状態",
    )
    op.create_index(
        "ix_artifact_preservations_release_id", "artifact_preservations", ["release_id"]
    )
    op.create_table(
        "artifact_preservation_objects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "preservation_id",
            sa.Uuid(),
            sa.ForeignKey("artifact_preservations.id"),
            nullable=False,
        ),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("lease_id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.String(1000), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleanup_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleanup_error", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "slot BETWEEN 0 AND 19", name="ck_artifact_preservation_slot"
        ),
        sa.CheckConstraint(
            "state IN ('reserved','verified','abandoned')",
            name="ck_artifact_preservation_object_state",
        ),
        sa.CheckConstraint(
            "NOT selected OR (state='verified' AND content_digest IS NOT NULL AND byte_size > 0)",
            name="ck_artifact_preservation_verified",
        ),
        comment="添付保全オブジェクト台帳: 書込み予約・検証・採用と非採用実体の回収履歴",
    )
    op.create_index(
        "ix_artifact_preservation_objects_preservation_id",
        "artifact_preservation_objects",
        ["preservation_id"],
    )
    op.create_index(
        "uq_artifact_preservation_selected",
        "artifact_preservation_objects",
        ["preservation_id", "slot"],
        unique=True,
        postgresql_where=sa.text("selected"),
    )
    op.execute("""
        CREATE FUNCTION guard_artifact_preservation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'preservation ledger must be retained' USING ERRCODE='55000'; END IF;
          IF TG_OP='INSERT' THEN
            IF NEW.status<>'preparing' OR NEW.manifest IS NOT NULL OR NEW.manifest_digest IS NOT NULL THEN
              RAISE EXCEPTION 'preservation must start preparing' USING ERRCODE='55000';
            END IF;
            RETURN NEW;
          END IF;
          IF OLD.status='ready' OR (OLD.status='cancelled' AND NEW.status<>'cancelled') OR
             (to_jsonb(NEW)-ARRAY['status','manifest','manifest_digest','completed_at','error_code','lease_id','lease_expires_at']) <>
             (to_jsonb(OLD)-ARRAY['status','manifest','manifest_digest','completed_at','error_code','lease_id','lease_expires_at']) THEN
            RAISE EXCEPTION 'preservation identity and ready manifest are immutable' USING ERRCODE='55000';
          END IF;
          IF NEW.status='ready' AND (
             NEW.manifest IS NULL OR NEW.manifest_digest IS NULL OR NEW.completed_at IS NULL OR
             jsonb_array_length(NEW.manifest->'files') <> jsonb_array_length(NEW.selection) OR
             (SELECT count(*) FROM artifact_preservation_objects WHERE preservation_id=NEW.id AND selected AND state='verified') <> jsonb_array_length(NEW.selection)
          ) THEN RAISE EXCEPTION 'preservation is incomplete' USING ERRCODE='55000'; END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER artifact_preservation_guard BEFORE INSERT OR UPDATE OR DELETE ON artifact_preservations
        FOR EACH ROW EXECUTE FUNCTION guard_artifact_preservation();

        CREATE FUNCTION guard_artifact_preservation_object() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_state text;
        DECLARE parent_lease uuid;
        BEGIN
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'object ledger must be retained' USING ERRCODE='55000'; END IF;
          SELECT status,lease_id INTO parent_state,parent_lease FROM artifact_preservations WHERE id=NEW.preservation_id FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'preservation parent is missing' USING ERRCODE='55000'; END IF;
          IF TG_OP='INSERT' THEN
            IF parent_state<>'preparing' OR NEW.state<>'reserved' OR NEW.selected OR NEW.lease_id<>parent_lease THEN
              RAISE EXCEPTION 'object must start reserved' USING ERRCODE='55000';
            END IF;
            RETURN NEW;
          END IF;
          IF (to_jsonb(NEW)-ARRAY['state','selected','content_digest','byte_size','verified_at','cleanup_checked_at','cleanup_error']) <>
             (to_jsonb(OLD)-ARRAY['state','selected','content_digest','byte_size','verified_at','cleanup_checked_at','cleanup_error']) OR
             (OLD.selected AND (parent_state='ready' OR
              (to_jsonb(NEW)-ARRAY['cleanup_checked_at','cleanup_error'])<>(to_jsonb(OLD)-ARRAY['cleanup_checked_at','cleanup_error']))) OR
             (NOT OLD.selected AND NEW.selected AND (OLD.state='abandoned' OR parent_state<>'preparing' OR NEW.lease_id<>parent_lease)) THEN
            RAISE EXCEPTION 'preserved object is immutable or lease is stale' USING ERRCODE='55000';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER artifact_preservation_object_guard BEFORE INSERT OR UPDATE OR DELETE ON artifact_preservation_objects
        FOR EACH ROW EXECUTE FUNCTION guard_artifact_preservation_object();
    """)


def downgrade() -> None:
    """台帳を含めて保全情報があるdowngradeを拒否する。"""
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS(SELECT 1 FROM artifact_preservations)"))
        .scalar()
    ):
        raise RuntimeError(
            "添付保全情報を保護するため、データのあるdowngradeは実行できません"
        )
    op.drop_table("artifact_preservation_objects")
    op.drop_table("artifact_preservations")
    op.execute("DROP FUNCTION guard_artifact_preservation_object()")
    op.execute("DROP FUNCTION guard_artifact_preservation()")
