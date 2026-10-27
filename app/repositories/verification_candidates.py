"""必要な項目・組合せ・直近実行だけをSQLで絞る。"""

from sqlalchemy import (
    String,
    Text,
    and_,
    case,
    cast,
    exists,
    func,
    literal,
    or_,
    select,
    union_all,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from app.models.test_design import (
    ExpectedValue,
    Factor,
    FactorLevel,
    PatternTable,
    RequirementTestItem,
    TestCase,
    TestDesign,
    TestDesignColumn,
    TestExecution,
    TestItem,
    TestItemPattern,
    TestPattern,
    TestPatternExpectedValue,
    TestPatternValue,
)
from app.schemas import test_design as schemas


class VerificationCandidateRepository:
    def linked(self, db, project_id, requirement_id):
        return (
            db.execute(
                select(
                    TestItem.id,
                    TestItem.design_id,
                    TestItem.code,
                    func.left(TestItem.content, 200).label("title"),
                    TestItem.pattern_table_id,
                    TestDesign.version,
                )
                .join(RequirementTestItem, RequirementTestItem.item_id == TestItem.id)
                .join(TestDesign, TestDesign.id == TestItem.design_id)
                .where(
                    RequirementTestItem.requirement_id == requirement_id,
                    TestDesign.project_id == project_id,
                    TestDesign.deleted_at.is_(None),
                    TestItem.deleted_at.is_(None),
                    TestItem.is_spacer.is_(False),
                )
                .order_by(TestItem.id)
                .limit(1001)
                .with_for_update(read=True, of=TestDesign)
            )
            .mappings()
            .all()
        )

    def pairs(self, db, project_id, requirement_id, targets, offset, limit):
        item, pattern, link = TestItem, TestPattern, TestItemPattern
        base = (
            select(
                item.id.label("item_id"),
                item.design_id,
                func.left(item.content, 200).label("item_title"),
            )
            .join(RequirementTestItem, RequirementTestItem.item_id == item.id)
            .join(TestDesign, TestDesign.id == item.design_id)
            .where(
                RequirementTestItem.requirement_id == requirement_id,
                TestDesign.project_id == project_id,
                TestDesign.deleted_at.is_(None),
                item.deleted_at.is_(None),
                item.is_spacer.is_(False),
                item.id.in_([t.item_id for t in targets]),
            )
        )

        def selection():
            return or_(
                *[
                    and_(item.id == t.item_id, pattern.id.in_(t.pattern_ids))
                    if t.pattern_scope == "selected"
                    else item.id == t.item_id
                    for t in targets
                ]
            )

        table_pairs = (
            base.add_columns(
                pattern.id.label("pattern_id"),
                pattern.code.label("pattern_name"),
                pattern.enabled.label("active"),
                (cast(item.id, String) + ":" + cast(pattern.id, String)).label(
                    "source_key"
                ),
            )
            .join(
                pattern,
                and_(
                    pattern.table_id == item.pattern_table_id,
                    pattern.design_id == item.design_id,
                    pattern.deleted_at.is_(None),
                ),
            )
            .where(item.pattern_table_id.is_not(None), selection())
        )
        legacy_pairs = (
            base.add_columns(
                pattern.id.label("pattern_id"),
                pattern.code.label("pattern_name"),
                pattern.enabled.label("active"),
                cast(link.id, String).label("source_key"),
            )
            .join(link, link.item_id == item.id)
            .join(
                pattern,
                and_(pattern.id == link.pattern_id, pattern.deleted_at.is_(None)),
            )
            .where(item.pattern_table_id.is_(None), selection())
        )
        standalone = base.add_columns(
            cast(literal(None), PGUUID).label("pattern_id"),
            cast(literal(None), String).label("pattern_name"),
            literal(True).label("active"),
            cast(item.id, String).label("source_key"),
        ).where(
            item.pattern_table_id.is_(None),
            ~exists(select(link.id).where(link.item_id == item.id)),
            item.id.in_(
                [t.item_id for t in targets if t.pattern_scope == "all_enabled"]
            ),
        )
        pairs = union_all(table_pairs, legacy_pairs, standalone).subquery()
        return (
            db.execute(
                select(pairs)
                .order_by(pairs.c.item_id, pairs.c.source_key)
                .offset(offset)
                .limit(limit + 1)
            )
            .mappings()
            .all()
        )

    def material(self, db, pairs):
        """全設計/全履歴を読み込まず、素材のbyte/行予算を先に調べる。"""
        ids = {p["item_id"] for p in pairs}
        patterns = {p["pattern_id"] for p in pairs if p["pattern_id"]}
        designs = {p["design_id"] for p in pairs}
        tables = (
            select(TestItem.pattern_table_id)
            .where(TestItem.id.in_(ids))
            .union(select(TestPattern.table_id).where(TestPattern.id.in_(patterns)))
            .subquery()
        )
        table_ids = select(tables.c.pattern_table_id)
        factor_ids = select(Factor.id).where(
            Factor.design_id.in_(designs),
            Factor.table_id.in_(table_ids) | Factor.table_id.is_(None),
            Factor.deleted_at.is_(None),
        )
        value_levels = select(TestPatternValue.level_id).where(
            TestPatternValue.pattern_id.in_(patterns)
        )
        selected_expected = select(TestPatternExpectedValue.expected_value_id).where(
            TestPatternExpectedValue.pattern_id.in_(patterns)
        )
        specs = [
            ("items", TestItem, schemas.TestItemInput, TestItem.id.in_(ids)),
            (
                "patterns",
                TestPattern,
                schemas.TestPatternInput,
                TestPattern.id.in_(patterns),
            ),
            (
                "pattern_tables",
                PatternTable,
                schemas.PatternTableInput,
                PatternTable.id.in_(table_ids),
            ),
            ("factors", Factor, schemas.FactorInput, Factor.id.in_(factor_ids)),
            (
                "levels",
                FactorLevel,
                schemas.FactorLevelInput,
                FactorLevel.id.in_(value_levels),
            ),
            (
                "values",
                TestPatternValue,
                schemas.TestPatternValueInput,
                TestPatternValue.pattern_id.in_(patterns),
            ),
            (
                "expected_values",
                ExpectedValue,
                schemas.ExpectedValueInput,
                ExpectedValue.id.in_(selected_expected),
            ),
            (
                "expected_selections",
                TestPatternExpectedValue,
                schemas.TestPatternExpectedValueInput,
                TestPatternExpectedValue.pattern_id.in_(patterns),
            ),
            (
                "links",
                TestItemPattern,
                schemas.TestItemPatternInput,
                and_(
                    TestItemPattern.item_id.in_(ids),
                    TestItemPattern.pattern_id.in_(patterns),
                ),
            ),
            (
                "columns",
                TestDesignColumn,
                schemas.TestDesignColumnInput,
                TestDesignColumn.design_id.in_(designs),
            ),
        ]
        queries = []
        for kind, model, schema, condition in specs:
            fields = [
                part
                for name in schema.model_fields
                for part in (name, getattr(model, name))
            ]
            query = select(
                literal(kind).label("kind"),
                model.design_id.label("design_id"),
                func.jsonb_build_object(*fields).label("data"),
            ).where(condition)
            if hasattr(model, "deleted_at"):
                query = query.where(model.deleted_at.is_(None))
            queries.append(query)
        material = union_all(*queries).cte("verification_material")
        count, size = db.execute(
            select(
                func.count()
                + func.coalesce(
                    func.sum(
                        case(
                            (
                                material.c.kind == "items",
                                12
                                + func.jsonb_array_length(
                                    func.jsonb_path_query_array(
                                        material.c.data, "$.custom_values.keyvalue()"
                                    )
                                ),
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(func.octet_length(cast(material.c.data, Text))), 0
                ),
            ).select_from(material)
        ).one()
        if count > 10000 or size > 8 * 1024 * 1024:
            return None
        return db.execute(select(material)).mappings().all(), size

    def latest(self, db, pairs, byte_budget):
        """各ケースのindex付きrun_number末尾だけを引く。"""
        keys = [
            and_(
                TestCase.design_id == p["design_id"],
                or_(
                    TestCase.source_key == p["source_key"],
                    and_(
                        TestCase.source_key.is_(None),
                        cast(TestCase.item_pattern_id, String) == p["source_key"],
                    ),
                ),
            )
            for p in pairs
        ]
        latest = (
            select(
                TestExecution.id.label("execution_id"),
                TestExecution.status,
                TestExecution.executed_at,
                func.octet_length(cast(TestExecution.source, Text)).label(
                    "source_bytes"
                ),
                case(
                    (
                        func.octet_length(cast(TestExecution.source, Text)) <= 131072,
                        TestExecution.source,
                    ),
                    else_=None,
                ).label("source"),
            )
            .where(TestExecution.case_id == TestCase.id)
            .order_by(TestExecution.run_number.desc())
            .limit(1)
            .lateral()
        )
        return (
            db.execute(
                select(
                    TestCase.id.label("case_id"),
                    TestCase.design_id,
                    func.coalesce(
                        TestCase.source_key, cast(TestCase.item_pattern_id, String)
                    ).label("source_key"),
                    latest.c.execution_id,
                    latest.c.status,
                    latest.c.executed_at,
                    (
                        func.coalesce(func.sum(latest.c.source_bytes).over(), 0)
                        > byte_budget
                    ).label("over_budget"),
                    case(
                        (
                            func.coalesce(func.sum(latest.c.source_bytes).over(), 0)
                            <= byte_budget,
                            latest.c.source,
                        ),
                        else_=None,
                    ).label("source"),
                )
                .outerjoin(latest, literal(True))
                .where(or_(*keys))
                .limit(51)
            )
            .mappings()
            .all()
        )
