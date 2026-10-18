"""固定版の対象へテスト設計を追加する。"""

import sqlalchemy as sa

from alembic import op

revision = "b8d2f5a0c436"
down_revision = "a7c1e4f9b325"
branch_labels = None
depends_on = None


def _constraint(kinds: str) -> None:
    """旧行を書き換えず対象種別のCHECKだけを交換する。"""
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_constraint(
        "ck_artifact_release_source_kind", "artifact_releases", type_="check"
    )
    op.create_check_constraint(
        "ck_artifact_release_source_kind",
        "artifact_releases",
        f"source_kind IN ({kinds})",
    )


def upgrade() -> None:
    """既存の権限・データ・封印triggerを維持する。"""
    _constraint("'requirement','requirement_document','specification','test_design'")


def downgrade() -> None:
    """従来kindの根に含めたgraphも含め、データがある場合は戻さない。"""
    bind = op.get_bind()
    bind.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    bind.execute(
        sa.text(
            "LOCK TABLE artifact_releases, artifact_release_nodes IN ACCESS EXCLUSIVE MODE"
        )
    )
    exists = bind.scalar(
        sa.text("""
        SELECT EXISTS (
            SELECT 1 FROM artifact_releases WHERE source_kind = 'test_design'
              OR source_kinds ? 'test_design'
              OR EXISTS (
                SELECT 1 FROM jsonb_array_elements(source_refs) ref
                WHERE ref->>'kind' = 'test_design')
            UNION ALL
            SELECT 1 FROM artifact_release_nodes
            WHERE node_kind IN ('test_design', 'test_design_part')
        )
    """)
    )
    if exists:
        raise RuntimeError(
            "テスト設計を含む固定版を保持しているためdowngradeできません"
        )
    _constraint("'requirement','requirement_document','specification'")
