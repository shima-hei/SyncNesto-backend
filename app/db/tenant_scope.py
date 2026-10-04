"""検証済みContextに基づくORMの取得・更新と参照先の組織境界。"""

from functools import lru_cache

from sqlalchemy import and_, event, inspect, or_, select, tuple_
from sqlalchemy.orm import Session, with_loader_criteria

from app.core.exceptions import ForbiddenError
from app.db.base import Base

# 所有関係は一箇所で定義する。認証Identityへの参照は所有関係に含めない。
OWNERS = {
    "project_members": [("project_id", "projects", "id")],
    "requirement_documents": [("project_id", "projects", "id")],
    "tasks": [("project_id", "projects", "id")],
    "boards": [("project_id", "projects", "id")],
    "milestones": [("project_id", "projects", "id")],
    "task_change_logs": [("project_id", "projects", "id")],
    "test_designs": [("project_id", "projects", "id")],
    "board_columns": [("board_id", "boards", "id")],
    "task_comments": [("task_id", "tasks", "id")],
    "task_dependencies": [
        ("predecessor_task_id", "tasks", "id"),
        ("successor_task_id", "tasks", "id"),
    ],
    "requirement_task_relations": [
        ("requirement_id", "requirements", "id"),
        ("task_id", "tasks", "id"),
    ],
    "requirement_test_items": [
        ("requirement_id", "requirements", "id"),
        ("item_id", "test_items", "id"),
    ],
    "test_case_issues": [("case_id", "test_cases", "id"), ("task_id", "tasks", "id")],
    "test_executions": [("case_id", "test_cases", "id")],
    "test_evidence": [("execution_id", "test_executions", "id")],
    "test_design_comment_changes": [("comment_id", "test_design_comments", "id")],
}
for name in (
    "requirement_sections",
    "requirements",
    "requirement_open_issues",
    "requirement_approvals",
    "requirement_change_logs",
    "requirement_target_comments",
    "requirement_relations",
):
    OWNERS[name] = [("document_id", "requirement_documents", "id")]
for name in (
    "requirement_revisions",
    "requirement_details",
    "requirement_links",
    "requirement_reviews",
    "requirement_comments",
):
    OWNERS[name] = [("requirement_id", "requirements", "id")]
for name in (
    "test_pattern_tables",
    "test_items",
    "test_factors",
    "test_factor_levels",
    "test_patterns",
    "test_pattern_values",
    "test_expected_values",
    "test_pattern_expected_values",
    "test_item_patterns",
    "test_design_columns",
    "test_design_layouts",
    "test_cases",
    "test_design_comments",
):
    OWNERS[name] = [("design_id", "test_designs", "id")]
MENTION_OWNERS = {
    "requirement_comment_id": "requirement_comments",
    "requirement_target_comment_id": "requirement_target_comments",
    "task_comment_id": "task_comments",
    "test_design_comment_id": "test_design_comments",
}


def ownership_filter(name: str, tenant_id: int, user_id: int | None = None):
    """テーブルから組織までの所有経路をSQL式で返す。"""
    table = Base.metadata.tables[name]
    if name == "projects":
        condition = table.c.tenant_id == tenant_id
        if user_id is not None:
            members = Base.metadata.tables["project_members"]
            condition = and_(
                condition,
                table.c.id.in_(
                    select(members.c.project_id).where(
                        members.c.user_id == user_id, members.c.deleted_at.is_(None)
                    )
                ),
            )
        return condition
    if name in {"notifications", "drafts", "audit_logs"}:
        condition = table.c.tenant_id == tenant_id
        if user_id is not None and name != "audit_logs":
            owner = "recipient_user_id" if name == "notifications" else "owner_user_id"
            condition = and_(condition, table.c[owner] == user_id)
        return condition
    parents = OWNERS.get(name, [])
    if name == "comment_mentions":
        return or_(
            *(
                table.c[column].in_(
                    select(Base.metadata.tables[parent].c.id).where(
                        ownership_filter(parent, tenant_id, user_id)
                    )
                )
                for column, parent in MENTION_OWNERS.items()
            )
        )
    return and_(
        *(
            table.c[column].in_(
                select(Base.metadata.tables[parent].c[key]).where(
                    ownership_filter(parent, tenant_id, user_id)
                )
            )
            for column, parent, key in parents
        )
    )


@lru_cache(maxsize=1)
def scoped_models() -> tuple[type, ...]:
    """全モデル登録後に組織配下モデルを確定する。"""
    names = set(OWNERS) | {
        "projects",
        "notifications",
        "drafts",
        "audit_logs",
        "comment_mentions",
    }
    return tuple(
        mapper.class_
        for mapper in Base.registry.mappers
        if mapper.class_.__tablename__ in names
    )


@event.listens_for(Session, "do_orm_execute")
def scope_orm_statement(state) -> None:
    """取得とORM一括更新にも同じ組織条件を適用する。"""
    tenant_id = state.session.info.get("tenant_id")
    if tenant_id is None or not (state.is_select or state.is_update or state.is_delete):
        return
    user_id = state.session.info.get("tenant_user_id")
    options = []
    for model in scoped_models():
        name = model.__tablename__
        # 組織管理の案件一覧には組織権限を使い、内容はProject所属を要求する。
        scoped_user = (
            None if name in {"projects", "project_members", "audit_logs"} else user_id
        )
        options.append(
            with_loader_criteria(
                model,
                ownership_filter(name, tenant_id, scoped_user),
                include_aliases=True,
            )
        )
    state.statement = state.statement.options(*options)


@event.listens_for(Session, "before_flush")
def validate_tenant_writes(db: Session, flush_context, instances) -> None:
    """所属の書き換えと別組織への参照をcommit前に拒否する。"""
    tenant_id = db.info.get("tenant_id")
    if tenant_id is None:
        return
    models = scoped_models()
    references: dict[tuple[str, str], set] = {}
    existing: dict[str, set[tuple]] = {}
    identity_references: set[int] = set()
    for row in db.new | db.dirty | db.deleted:
        if not isinstance(row, models):
            continue
        name = row.__tablename__
        identity = inspect(row).identity
        if identity is not None:
            existing.setdefault(name, set()).add(identity)
        if hasattr(row, "tenant_id"):
            previous = inspect(row).attrs.tenant_id.history.deleted
            if previous and previous[0] != tenant_id:
                raise ForbiddenError()
            if row.tenant_id is None and row in db.new:
                row.tenant_id = tenant_id
            if row.tenant_id != tenant_id:
                raise ForbiddenError()
        # 参照先の所属を、ORMのidentity mapや取得済みデータに依存せず確認する。
        for column in row.__table__.c:
            value = getattr(row, column.name)
            if value is None:
                continue
            for fk in column.foreign_keys:
                parent = fk.column.table.name
                if parent == "users" and (
                    row in db.new
                    or inspect(row).attrs[column.name].history.has_changes()
                ):
                    # 共通Identityへの新しい業務参照も、現在組織内に限定する。
                    identity_references.add(value)
                if parent not in OWNERS and parent not in {
                    "projects",
                    "comment_mentions",
                }:
                    continue
                if parent == name and value == getattr(row, "id", None):
                    continue
                pending = next(
                    (
                        p
                        for p in db.new
                        if p.__table__.name == parent
                        and getattr(p, fk.column.name) == value
                    ),
                    None,
                )
                if pending is not None:
                    continue
                references.setdefault((parent, fk.column.name), set()).add(value)
    for name, identities in existing.items():
        table = Base.metadata.tables[name]
        keys = list(table.primary_key.columns)
        allowed = set(
            db.connection()
            .execute(
                select(*keys).where(
                    tuple_(*keys).in_(identities), ownership_filter(name, tenant_id)
                )
            )
            .tuples()
        )
        if allowed != identities:
            raise ForbiddenError()
    for (parent, key), values in references.items():
        column = Base.metadata.tables[parent].c[key]
        allowed = set(
            db.connection()
            .execute(
                select(column).where(
                    column.in_(values), ownership_filter(parent, tenant_id)
                )
            )
            .scalars()
        )
        if allowed != values:
            raise ForbiddenError()
    if identity_references:
        users = Base.metadata.tables["users"]
        memberships = Base.metadata.tables["tenant_members"]
        allowed_identities = set(
            db.connection()
            .execute(
                select(users.c.id)
                .join(memberships, memberships.c.user_id == users.c.id)
                .where(
                    users.c.id.in_(identity_references),
                    users.c.is_active.is_(True),
                    users.c.deleted_at.is_(None),
                    memberships.c.tenant_id == tenant_id,
                    memberships.c.status == "active",
                )
            )
            .scalars()
        )
        if allowed_identities != identity_references:
            raise ForbiddenError()
