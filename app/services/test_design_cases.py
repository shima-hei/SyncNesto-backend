"""ケース生成時のスナップショットと影響判定。"""

import hashlib
import json

from app.models.test_design import TestCase
from app.schemas.test_design import TestCaseRead, TestDesignRead


def case_sources(design: TestDesignRead) -> dict[str, dict]:
    """参照を展開し、表示順・書式以外の変更を検知できる形にする。"""
    items = {row.id: row for row in design.items}
    patterns = {row.id: row for row in design.patterns}
    levels = {row.id: row for row in design.levels}
    values = {(row.pattern_id, row.factor_id): row.level_id for row in design.values}
    result = {}
    expected = {row.id: row for row in design.expected_values}
    selections = {}
    for row in design.expected_selections:
        selections.setdefault(row.pattern_id, []).append(
            expected[row.expected_value_id]
        )
    tables = {row.id: row for row in design.pattern_tables}
    targets = []
    legacy_items = {link.item_id for link in design.links}
    for item in design.items:
        if item.is_spacer:
            continue
        if item.pattern_table_id:
            targets.extend(
                (f"{item.id}:{p.id}", item, p)
                for p in design.patterns
                if p.table_id == item.pattern_table_id and p.enabled
            )
        elif item.id not in legacy_items:
            targets.append((str(item.id), item, None))
    targets.extend(
        (str(link.id), items[link.item_id], patterns[link.pattern_id])
        for link in design.links
        if not items[link.item_id].is_spacer
        and not items[link.item_id].pattern_table_id
        and patterns[link.pattern_id].enabled
    )
    for key, item, pattern in targets:
        item_snapshot = item.model_dump(mode="json", exclude={"position", "is_spacer"})
        # 追加前のスナップショットと互換にし、空の新列だけで影響ありにしない。
        if not item.target_feature:
            item_snapshot.pop("target_feature")
        result[key] = {
            "item": item_snapshot,
            "pattern": pattern.model_dump(mode="json", exclude={"position"})
            if pattern
            else None,
            "columns": {row.key: row.label for row in design.columns},
            "values": [
                {
                    "factor_id": str(factor.id),
                    "factor": factor.name,
                    "level": levels[level_id].name if level_id else None,
                    "level_id": str(level_id) if level_id else None,
                }
                for factor in sorted(design.factors, key=lambda row: str(row.id))
                if pattern and factor.table_id == pattern.table_id
                for level_id in [values.get((pattern.id, factor.id))]
            ],
        }
        if item.pattern_table_id:
            result[key]["pattern_table"] = tables[item.pattern_table_id].model_dump(
                mode="json", exclude={"position"}
            )
        if pattern and pattern.id in selections:
            selected = sorted(selections[pattern.id], key=lambda row: str(row.id))
            # 単一期待値の既存スナップショットとハッシュ互換を維持する。
            if len(selected) == 1:
                result[key]["expected_value"] = selected[0].model_dump(
                    mode="json", exclude={"position"}
                )
            else:
                result[key]["expected_values"] = [
                    row.model_dump(mode="json", exclude={"position"})
                    for row in selected
                ]
    return result


def source_hash(source: dict) -> str:
    """内容比較用の安定したハッシュ値を返す。"""
    return hashlib.sha256(
        json.dumps(source, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def source_without_item_code(source: dict) -> dict:
    """表示用の項目番号だけを除いて設計内容を比較する。"""
    return {
        **source,
        "item": {key: value for key, value in source["item"].items() if key != "code"},
    }


def case_key(case: TestCase) -> str:
    """旧ケースの識別子も保持して比較する。"""
    return case.source_key or str(case.item_pattern_id)


def present_case(
    case: TestCase, sources: dict[str, dict], executor_name: str | None = None
) -> TestCaseRead:
    """元データの変更・削除・無効化をケースごとに示す。"""
    source = sources.get(case_key(case))
    stale = source is None or source_hash(source) != case.source_hash
    return TestCaseRead.model_validate(
        dict(
            id=case.id,
            design_id=case.design_id,
            item_pattern_id=case.item_pattern_id,
            source=case.source,
            acknowledged_source=case.acknowledged_source
            if source is not None and stale
            else None,
            status=case.status,
            actual_result=case.actual_result,
            notes=case.notes,
            executed_by=case.executed_by,
            executed_by_name=executor_name,
            executed_at=case.executed_at,
            version=case.version,
            active=source is not None,
            stale=stale,
        )
    )
