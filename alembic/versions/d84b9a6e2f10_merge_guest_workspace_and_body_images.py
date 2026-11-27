"""ゲストワークスペースと本文画像の移行履歴を統合する。"""

revision = "d84b9a6e2f10"
down_revision = ("72f1c6ef8b15", "b2a8ed7f3c16")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """双方の移行後に履歴を一つにする。"""


def downgrade() -> None:
    """統合前の二つの履歴へ戻す。"""
