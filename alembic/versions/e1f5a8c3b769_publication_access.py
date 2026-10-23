"""公開版の状態と本人限定の参加・許可・失効を追加する。"""

from pathlib import Path

from alembic import op
import sqlalchemy as sa

revision = "e1f5a8c3b769"
down_revision = "dae4b7c2e658"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """既存の準備版はpreparedのまま、許可を発行せず移行する。"""
    op.execute(Path(__file__).with_suffix(".sql").read_text())


def downgrade() -> None:
    """履歴が存在するDBの巻戻しを拒否する。"""
    db = op.get_bind()
    db.execute(
        sa.text(
            "LOCK TABLE artifact_publications,project_guest_participations,artifact_publication_grants,artifact_publication_events IN ACCESS EXCLUSIVE MODE"
        )
    )
    nonempty = db.execute(
        sa.text("""
        SELECT EXISTS(SELECT FROM project_guest_participations)
          OR EXISTS(SELECT FROM artifact_publication_grants)
          OR EXISTS(SELECT FROM artifact_publication_events)
          OR EXISTS(SELECT FROM artifact_publications WHERE publication_state<>'prepared' OR publication_version<>1)
    """)
    ).scalar_one()
    if nonempty:
        raise RuntimeError(
            "publication history must be retained; disable new access instead"
        )
    op.execute(
        Path(__file__).with_name("e1f5a8c3b769_publication_access_down.sql").read_text()
    )
