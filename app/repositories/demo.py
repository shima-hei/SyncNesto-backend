"""デモの原子的な発行制限と、組織の所有経路を使う回収処理。"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, func, or_, select, text, tuple_, update
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.tenant_scope import OWNERS, ownership_filter
from app.models.demo import DemoOwnedUser, DemoSession, DemoStartBudget

BUSINESS_TABLES = set(OWNERS) | {"projects", "drafts", "comment_mentions"}


class DemoRepository:
    """DBロックで発行・更新・失効・回収の競合を直列化する。"""

    def lock_admission(self, db: Session) -> None:
        """複数Functionでも同時に全体上限を通過させない。"""
        db.execute(text("SELECT pg_advisory_xact_lock(731892604)"))

    def lock(self, db: Session, demo_id: UUID) -> DemoSession | None:
        """更新直前にも状態を再取得する。"""
        return db.scalar(
            select(DemoSession)
            .where(DemoSession.id == demo_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def by_session(self, db: Session, session_id: UUID) -> DemoSession | None:
        """署名済みsidと唯一のデモを対応させる。"""
        return db.scalar(
            select(DemoSession).where(DemoSession.session_id == session_id)
        )

    def owned_ids(self, db: Session, demo_id: UUID) -> list[int]:
        """明示的に一時発行したIdentityだけを返す。"""
        return list(
            db.scalars(
                select(DemoOwnedUser.user_id).where(DemoOwnedUser.demo_id == demo_id)
            )
        )

    def unfinished_count(self, db: Session) -> int:
        """失効後の回収待ちも資源上限に含める。"""
        return (
            db.scalar(
                select(func.count())
                .select_from(DemoSession)
                .where(DemoSession.status != "cleaned")
            )
            or 0
        )

    def consume_start(self, db: Session, digest: str, now: datetime) -> bool:
        """時間枠に最大3回。呼び出し元の発行ロック内で使用する。"""
        window = int(now.timestamp()) // 3600 * 3600
        db.execute(delete(DemoStartBudget).where(DemoStartBudget.window_start < window))
        row = db.get(DemoStartBudget, (digest, window))
        if row is None:
            db.add(DemoStartBudget(key=digest, window_start=window, count=1))
        elif row.count >= 3:
            return False
        else:
            row.count += 1
        return True

    def business_count(self, db: Session, tenant_id: int) -> int:
        """論理削除や版履歴も容量に含める。"""
        return sum(
            db.connection().scalar(
                select(func.count())
                .select_from(Base.metadata.tables[name])
                .where(ownership_filter(name, tenant_id))
            )
            or 0
            for name in BUSINESS_TABLES
        )

    def delete_business_data(self, db: Session, demo: DemoSession) -> None:
        """先に全対象PKを確定し、子から削除して通常組織を保存する。"""
        if demo.tenant_id is None:
            return
        tenant_id = demo.tenant_id
        users = self.owned_ids(db, demo.id)
        emails = list(
            db.connection().scalars(
                select(Base.metadata.tables["users"].c.email).where(
                    Base.metadata.tables["users"].c.id.in_(users)
                )
            )
        )
        targets: dict[str, list[tuple]] = {}
        for table in Base.metadata.sorted_tables:
            name = table.name
            if name.startswith("demo_") or name in {
                "roles",
                "permissions",
                "role_permissions",
                "request_limits",
            }:
                continue
            if name == "users":
                condition = table.c.id.in_(users)
            elif name == "tenants":
                condition = table.c.id == tenant_id
            elif name == "login_attempts":
                condition = table.c.email.in_(emails)
            elif name in BUSINESS_TABLES or name in {"notifications", "audit_logs"}:
                condition = ownership_filter(name, tenant_id)
                if name == "audit_logs":
                    condition = or_(
                        condition,
                        table.c.actor_user_id.in_(users),
                        table.c.target_user_id.in_(users),
                    )
            elif name in {"tenant_members", "account_actions"}:
                condition = table.c.tenant_id == tenant_id
            elif name in {"sessions", "user_roles"}:
                condition = table.c.user_id.in_(users)
            else:
                # 新しいモデル追加時、所有経路の定義忘れを削除漏れにしない。
                raise RuntimeError(f"Demo cleanup ownership missing: {name}")
            keys = list(table.primary_key.columns)
            targets[name] = list(
                db.connection().execute(select(*keys).where(condition)).tuples()
            )
        demo.tenant_id = demo.user_id = demo.session_id = None
        db.flush()
        db.execute(delete(DemoOwnedUser).where(DemoOwnedUser.demo_id == demo.id))
        for table in reversed(Base.metadata.sorted_tables):
            identities = targets.get(table.name, [])
            if not identities:
                continue
            condition = tuple_(*table.primary_key.columns).in_(identities)
            # 同じ表への親・返信・作成者参照を削除前に解除する。
            self_refs = {
                c.name: None
                for c in table.c
                if c.nullable and any(f.column.table == table for f in c.foreign_keys)
            }
            if self_refs:
                db.connection().execute(
                    update(table).where(condition).values(**self_refs)
                )
            db.connection().execute(delete(table).where(condition))

    def due_ids(self, db: Session, now: datetime, limit: int = 10) -> list[UUID]:
        """期限切れと再試行可能な回収待ちを一回分に制限する。"""
        return list(
            db.scalars(
                select(DemoSession.id)
                .where(
                    or_(
                        (DemoSession.status == "active")
                        & (DemoSession.expires_at <= now),
                        (DemoSession.status == "cleanup_pending")
                        & (DemoSession.cleanup_after <= now),
                    )
                )
                .order_by(DemoSession.cleanup_after)
                .limit(limit)
            )
        )

    def prune_receipts(self, db: Session) -> None:
        """内容のない完了台帳も24時間で廃棄する。"""
        from datetime import timedelta

        db.execute(
            delete(DemoSession).where(
                DemoSession.status == "cleaned",
                DemoSession.cleaned_at < datetime.now(UTC) - timedelta(hours=24),
            )
        )
        db.commit()
