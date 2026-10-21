"""初期権限の変更・commit・採番が次のケースへ漏れないことを検証する。"""

from typing import cast

import pytest
from sqlalchemy import Table, delete, func, select, update

from app.models.rbac import Permission, Role, RolePermission
from app.models.tenant import Tenant


@pytest.mark.parametrize("repetition", [0, 1])
def test_seed_snapshot_and_sequences_survive_committed_mutation(
    db, rbac_seed_cache, repetition
):
    """両ケースとも同一初期値から始め、全権限行と採番を汚染して終える。"""
    for model in [Role, Permission, RolePermission]:
        table = cast(Table, model.__table__)
        actual = {row["id"]: dict(row) for row in db.execute(select(table)).mappings()}
        expected = {row["id"]: row for row in rbac_seed_cache[table.name]}
        assert actual == expected
    assert [(t.id, t.slug) for t in db.scalars(select(Tenant))] == [(1, "default")]
    max_role = db.scalar(select(func.max(Role.id)))
    max_permission = db.scalar(select(func.max(Permission.id)))
    max_link = db.scalar(select(func.max(RolePermission.id)))
    role = Role(key="verification_mutation", name="Verification", scope="project")
    permission = Permission(code="verification:mutated")
    db.add_all([role, permission])
    db.flush()
    assert role.id == max_role + 1
    assert permission.id == max_permission + 1
    db.execute(delete(RolePermission))
    link = RolePermission(role_id=role.id, permission_id=permission.id)
    db.add(link)
    db.flush()
    assert link.id == max_link + 1
    db.execute(update(Role).values(description="mutated"))
    db.execute(update(Permission).values(description="mutated"))
    db.execute(update(Tenant).values(slug="mutated"))
    db.add(Tenant(name="Extra", slug="extra"))
    db.commit()
