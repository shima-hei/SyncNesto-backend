"""テスト設計の構造化データと表示情報を分離するモデル。"""

from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class TestDesign(Base):
    """プロジェクト配下のテスト設計書。"""

    __tablename__ = "test_designs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DesignEntity:
    """設計内の安定IDと表示順。"""

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    design_id: Mapped[int] = mapped_column(
        ForeignKey("test_designs.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)


class PatternTable(DesignEntity, Base):
    """用途ごとに独立したパターン表。"""

    __tablename__ = "test_pattern_tables"
    name: Mapped[str] = mapped_column(String(200))


class TableEntity:
    """因子・組み合わせ・期待値の所属表。"""

    table_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("test_pattern_tables.id", ondelete="CASCADE"), index=True
    )


class TestItem(DesignEntity, Base):
    """テスト項目。固定項目と拡張列の値を保持する。"""

    __tablename__ = "test_items"
    __table_args__ = (UniqueConstraint("design_id", "id"),)
    code: Mapped[str] = mapped_column(String(100))
    target_feature: Mapped[str] = mapped_column(Text, default="")
    is_spacer: Mapped[bool] = mapped_column(Boolean, default=False)
    viewpoint: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default="")
    preconditions: Mapped[str] = mapped_column(Text, default="")
    test_data: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[str] = mapped_column(Text, default="")
    expected_result: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    custom_values: Mapped[dict] = mapped_column(JSONB, default=dict)
    pattern_table_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("test_pattern_tables.id", ondelete="SET NULL"), index=True
    )


class Factor(TableEntity, DesignEntity, Base):
    """組み合わせを構成する因子。"""

    __tablename__ = "test_factors"
    __table_args__ = (UniqueConstraint("design_id", "id"),)
    name: Mapped[str] = mapped_column(String(200))


class FactorLevel(DesignEntity, Base):
    """因子に属する水準。"""

    __tablename__ = "test_factor_levels"
    __table_args__ = (
        UniqueConstraint("design_id", "factor_id", "id"),
        ForeignKeyConstraint(
            ["design_id", "factor_id"],
            ["test_factors.design_id", "test_factors.id"],
            ondelete="CASCADE",
        ),
    )
    factor_id: Mapped[UUID] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(String(200))


class TestPattern(TableEntity, DesignEntity, Base):
    """再利用可能な組み合わせ。"""

    __tablename__ = "test_patterns"
    __table_args__ = (UniqueConstraint("design_id", "id"),)
    code: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class TestPatternValue(DesignEntity, Base):
    """パターン内で選択した水準。nullは対象外を表す。"""

    __tablename__ = "test_pattern_values"
    __table_args__ = (
        UniqueConstraint("design_id", "pattern_id", "factor_id"),
        ForeignKeyConstraint(
            ["design_id", "pattern_id"],
            ["test_patterns.design_id", "test_patterns.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["design_id", "factor_id"],
            ["test_factors.design_id", "test_factors.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["design_id", "factor_id", "level_id"],
            [
                "test_factor_levels.design_id",
                "test_factor_levels.factor_id",
                "test_factor_levels.id",
            ],
        ),
    )
    pattern_id: Mapped[UUID] = mapped_column(Uuid)
    factor_id: Mapped[UUID] = mapped_column(Uuid)
    level_id: Mapped[UUID | None] = mapped_column(Uuid)


class ExpectedValue(TableEntity, DesignEntity, Base):
    """因子とは独立した期待値の候補。"""

    __tablename__ = "test_expected_values"
    __table_args__ = (UniqueConstraint("design_id", "id"),)
    name: Mapped[str] = mapped_column(Text)


class TestPatternExpectedValue(DesignEntity, Base):
    """パターンが選択した期待値。"""

    __tablename__ = "test_pattern_expected_values"
    __table_args__ = (
        UniqueConstraint(
            "design_id",
            "pattern_id",
            "expected_value_id",
            name="uq_pattern_expected_selection",
        ),
        ForeignKeyConstraint(
            ["design_id", "pattern_id"],
            ["test_patterns.design_id", "test_patterns.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["design_id", "expected_value_id"],
            ["test_expected_values.design_id", "test_expected_values.id"],
            ondelete="CASCADE",
        ),
    )
    pattern_id: Mapped[UUID] = mapped_column(Uuid)
    expected_value_id: Mapped[UUID] = mapped_column(Uuid)


class TestItemPattern(DesignEntity, Base):
    """項目とパターンの多対多関連。"""

    __tablename__ = "test_item_patterns"
    __table_args__ = (
        UniqueConstraint("design_id", "item_id", "pattern_id"),
        ForeignKeyConstraint(
            ["design_id", "item_id"],
            ["test_items.design_id", "test_items.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["design_id", "pattern_id"],
            ["test_patterns.design_id", "test_patterns.id"],
            ondelete="CASCADE",
        ),
    )
    item_id: Mapped[UUID] = mapped_column(Uuid)
    pattern_id: Mapped[UUID] = mapped_column(Uuid)


class TestDesignColumn(DesignEntity, Base):
    """任意列の定義。値は項目側で管理する。"""

    __tablename__ = "test_design_columns"
    key: Mapped[str] = mapped_column(String(100))
    label: Mapped[str] = mapped_column(String(200))


class TestDesignLayout(Base):
    """セル書式と寸法。業務データとは別テーブルで保存する。"""

    __tablename__ = "test_design_layouts"
    design_id: Mapped[int] = mapped_column(
        ForeignKey("test_designs.id", ondelete="CASCADE"), primary_key=True
    )
    data: Mapped[dict] = mapped_column(JSONB, default=dict)


class TestCase(DesignEntity, Base):
    """生成時の内容と個別の実行結果を持つテストケース。"""

    __tablename__ = "test_cases"
    __table_args__ = (UniqueConstraint("design_id", "source_key"),)
    source_key: Mapped[str | None] = mapped_column(String(100))
    item_pattern_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("test_item_patterns.id", ondelete="SET NULL"), unique=True
    )
    source: Mapped[dict] = mapped_column(JSONB)
    acknowledged_source: Mapped[dict | None] = mapped_column(JSONB)
    source_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="not_run")
    actual_result: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    executed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)


for _model in (
    TestDesign,
    PatternTable,
    TestItem,
    Factor,
    FactorLevel,
    TestPattern,
    TestPatternValue,
    TestItemPattern,
    TestDesignColumn,
    TestDesignLayout,
    TestCase,
    ExpectedValue,
    TestPatternExpectedValue,
):
    cast(Table, _model.__table__).comment = db_comment(
        _model.__name__, _model.__doc__ or ""
    )
