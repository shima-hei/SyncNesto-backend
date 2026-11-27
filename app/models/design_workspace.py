"""案件の機能グループ・設計セット・図上の表示位置。"""

from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DesignCollection(Base):
    """グループ、API仕様セット、DBモデルの案件単位の定義。"""

    __tablename__ = "design_collections"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "id", name="uq_design_collections_project_id_id"
        ),
        ForeignKeyConstraint(
            ["project_id", "parent_id"],
            ["design_collections.project_id", "design_collections.id"],
            name="fk_design_collections_parent_project",
        ),
        CheckConstraint(
            "kind IN ('group', 'api_set', 'db_model')",
            name="ck_design_collections_kind",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    kind: Mapped[str] = mapped_column(String(20))
    parent_id: Mapped[UUID | None]
    name: Mapped[str] = mapped_column(String(200))
    configuration: Mapped[dict] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(default=1)


class DesignMembership(Base):
    """設計対象と機能グループの多対多所属。"""

    __tablename__ = "design_memberships"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "group_id"],
            ["design_collections.project_id", "design_collections.id"],
            name="fk_design_memberships_group_project",
        ),
        ForeignKeyConstraint(
            ["project_id", "specification_id"],
            ["specifications.project_id", "specifications.id"],
            name="fk_design_memberships_specification_project",
            ondelete="CASCADE",
        ),
    )

    project_id: Mapped[int] = mapped_column(primary_key=True)
    specification_id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[UUID] = mapped_column(primary_key=True)


class DesignPlacement(Base):
    """同一設計の配置は図ごとに保持する。"""

    __tablename__ = "design_placements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "specification_id"],
            ["specifications.project_id", "specifications.id"],
            name="fk_design_placements_specification_project",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "group_id"],
            ["design_collections.project_id", "design_collections.id"],
            name="fk_design_placements_group_project",
        ),
        CheckConstraint("x >= 0 AND y >= 0", name="ck_design_placements_coordinates"),
    )

    project_id: Mapped[int] = mapped_column(primary_key=True)
    specification_id: Mapped[int] = mapped_column(primary_key=True)
    view_key: Mapped[str] = mapped_column(String(36), primary_key=True)
    group_id: Mapped[UUID | None]
    x: Mapped[int]
    y: Mapped[int]
    hidden: Mapped[bool] = mapped_column(Boolean)
    version: Mapped[int] = mapped_column(default=1)


class DesignImpactDecision(Base):
    """設計版ごとに影響確認の結果を保持する。"""

    __tablename__ = "design_impact_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "specification_id"],
            ["specifications.project_id", "specifications.id"],
            name="fk_design_impact_specification_project",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('confirmed', 'needs_action')", name="ck_design_impact_status"
        ),
    )
    project_id: Mapped[int] = mapped_column(primary_key=True)
    specification_id: Mapped[int] = mapped_column(primary_key=True)
    source_version: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(20))
