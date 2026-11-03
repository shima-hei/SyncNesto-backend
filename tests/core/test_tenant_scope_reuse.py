"""条件再利用後もSQLの組織・本人境界を各Sessionと操作で保持する。"""

from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, aliased

from app.db.tenant_scope import scope_orm_statement
from app.models.task import Task

pytestmark = pytest.mark.no_db


def scoped_sql(session, statement):
    """実行イベントと同じ条件を適用し、DBなしでSQLとパラメータを比較する。"""
    state = SimpleNamespace(
        session=session,
        statement=statement,
        is_select=statement.is_select,
        is_update=statement.is_update,
        is_delete=statement.is_delete,
    )
    scope_orm_statement(state)
    return str(
        state.statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def test_reuse_preserves_select_alias_update_delete_and_context_switches():
    """初回と再利用、別Session、本人/組織切替で条件が一致する。"""
    alias = aliased(Task)
    statements = [
        select(Task),
        select(alias).where(alias.priority == "high"),
        update(Task).where(Task.id == 7).values(title="変更"),
        delete(Task).where(Task.id == 7),
    ]
    with Session() as reused:
        previous = None
        for tenant, user in [(101, 201), (101, 202), (102, 202), (101, 201)]:
            reused.info.update(tenant_id=tenant, tenant_user_id=user)
            with Session(info=dict(reused.info, tenant_scope_options=None)) as fresh:
                for statement in statements:
                    first = scoped_sql(reused, statement)
                    assert first == scoped_sql(fresh, statement)
                    assert first == scoped_sql(reused, statement)
            current = scoped_sql(reused, select(Task))
            if previous is not None:
                assert current != previous
            previous = current
