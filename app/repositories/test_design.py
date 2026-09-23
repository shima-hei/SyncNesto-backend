"""テスト設計のDBアクセス。commitはServiceが管理する。"""

from uuid import UUID

from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from app.models.test_design import (
    ExpectedValue,
    Factor,
    FactorLevel,
    PatternTable,
    TestCase,
    TestDesign,
    TestDesignColumn,
    TestDesignLayout,
    TestItem,
    TestItemPattern,
    TestPattern,
    TestPatternExpectedValue,
    TestPatternValue,
)
from app.models.user import User

ENTITY_MODELS = {
    "pattern_tables": PatternTable,
    "items": TestItem,
    "factors": Factor,
    "levels": FactorLevel,
    "patterns": TestPattern,
    "values": TestPatternValue,
    "links": TestItemPattern,
    "columns": TestDesignColumn,
    "expected_values": ExpectedValue,
    "expected_selections": TestPatternExpectedValue,
}


class TestDesignRepository:
    """設計書と独立した子エンティティを読み書きする。"""

    def design_counts(self, db: Session, project_id: int) -> dict[int, tuple[int, int]]:
        """有効な組み合わせ数を集計し、生成前のケース数を返す。"""
        tables = (
            select(TestPattern.table_id, func.count().label("n"))
            .where(TestPattern.enabled.is_(True))
            .group_by(TestPattern.table_id)
            .subquery()
        )
        legacy = (
            select(
                TestItemPattern.item_id,
                func.count().filter(TestPattern.enabled.is_(True)).label("n"),
            )
            .join(TestPattern, TestPattern.id == TestItemPattern.pattern_id)
            .group_by(TestItemPattern.item_id)
            .subquery()
        )
        rows = db.execute(
            select(
                TestItem.design_id,
                func.count(),
                func.sum(
                    case(
                        (
                            TestItem.pattern_table_id.is_not(None),
                            func.coalesce(tables.c.n, 0),
                        ),
                        else_=func.coalesce(legacy.c.n, 1),
                    )
                ),
            )
            .join(TestDesign, TestDesign.id == TestItem.design_id)
            .outerjoin(tables, tables.c.table_id == TestItem.pattern_table_id)
            .outerjoin(legacy, legacy.c.item_id == TestItem.id)
            .where(TestDesign.project_id == project_id, TestItem.is_spacer.is_(False))
            .group_by(TestItem.design_id)
        )
        return {design_id: (int(items), int(cases)) for design_id, items, cases in rows}

    def list_designs(self, db: Session, project_id: int) -> list[TestDesign]:
        """設計一覧を返す。"""
        return list(
            db.scalars(
                select(TestDesign)
                .where(TestDesign.project_id == project_id)
                .order_by(TestDesign.updated_at.desc())
            )
        )

    def get(
        self, db: Session, project_id: int, design_id: int, *, lock: bool = False
    ) -> TestDesign | None:
        """プロジェクトスコープで取得し、更新時は行をロックする。"""
        query = (
            select(TestDesign)
            .where(TestDesign.id == design_id, TestDesign.project_id == project_id)
            .execution_options(populate_existing=True)
        )
        if lock:
            query = query.with_for_update()
        return db.scalar(query)

    def graph(self, db: Session, design_id: int) -> dict:
        """一定回数のクエリで設計全体を取得する。"""
        result: dict = {
            name: list(
                db.scalars(
                    select(model)
                    .where(model.design_id == design_id)
                    .order_by(model.position, model.id)
                )
            )
            for name, model in ENTITY_MODELS.items()
        }
        layout = db.get(TestDesignLayout, design_id)
        result["layout"] = layout.data if layout else {}
        return result

    def replace_graph(self, db: Session, design_id: int, data: dict) -> None:
        """既存IDを維持し、依存関係順に差分を適用する。"""
        db.execute(
            delete(TestPatternValue).where(TestPatternValue.design_id == design_id)
        )
        db.execute(
            delete(TestPatternExpectedValue).where(
                TestPatternExpectedValue.design_id == design_id
            )
        )
        for name in (
            "links",
            "levels",
            "items",
            "patterns",
            "factors",
            "columns",
            "expected_values",
        ):
            model = ENTITY_MODELS[name]
            ids = [row["id"] for row in data[name]]
            db.execute(
                delete(model).where(model.design_id == design_id, model.id.not_in(ids))
            )
        for name in (
            "pattern_tables",
            "factors",
            "levels",
            "items",
            "patterns",
            "columns",
            "links",
            "values",
            "expected_values",
            "expected_selections",
        ):
            model = ENTITY_MODELS[name]
            existing = {
                row.id: row
                for row in db.scalars(select(model).where(model.design_id == design_id))
            }
            for values in data[name]:
                row = existing.get(values["id"])
                if row is None:
                    db.add(model(design_id=design_id, **values))
                else:
                    for key, value in values.items():
                        setattr(row, key, value)
            db.flush()
        db.execute(
            delete(PatternTable).where(
                PatternTable.design_id == design_id,
                PatternTable.id.not_in([row["id"] for row in data["pattern_tables"]]),
            )
        )
        layout = db.get(TestDesignLayout, design_id)
        if layout is None:
            db.add(TestDesignLayout(design_id=design_id, data=data["layout"]))
        else:
            layout.data = data["layout"]

    def cases(self, db: Session, design_id: int) -> list[TestCase]:
        """生成済みケースを取得する。"""
        return list(
            db.scalars(
                select(TestCase)
                .where(TestCase.design_id == design_id)
                .order_by(TestCase.position, TestCase.id)
            )
        )

    def executor_names(self, db: Session, user_ids: set[int]) -> dict[int, str]:
        """ケース一覧に表示する実行者名を一括取得する。"""
        if not user_ids:
            return {}
        return {
            user_id: name
            for user_id, name in db.execute(
                select(User.id, User.name).where(User.id.in_(user_ids))
            ).all()
        }

    def case(self, db: Session, design_id: int, case_id: UUID) -> TestCase | None:
        """設計内のケースを取得する。"""
        return db.scalar(
            select(TestCase)
            .where(TestCase.design_id == design_id, TestCase.id == case_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
