"""ログインユーザーの個人ワークスペースを構成する。"""

from collections import defaultdict
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.user import User, UserType
from app.repositories.home import HomeRepository
from app.repositories.home_requirement_work import (
    HomeRequirementWorkRepository,
    ProjectAccess,
)
from app.schemas.home import (
    HomeProjectsRead,
    HomeRequirementWorkItem,
    HomeRequirementWorkRead,
    HomeTasksRead,
)
from app.services.artifact_release import SOURCE_PERMISSIONS
from app.services.authorization import AuthorizationService
from app.services.requirement_formal_approval import APPROVER_PERMISSIONS


def home_today(timezone: str, now: datetime | None = None) -> date:
    """UTCの現在時刻を表示と同じタイムゾーンの暦日に変換する。"""
    return (now or datetime.now(UTC)).astimezone(ZoneInfo(timezone)).date()


class HomeService:
    """権限確認と表示契約の構築を担う読み取り専用Service。"""

    def __init__(self) -> None:
        """既存の認可Serviceと専用の集計Repositoryを使用する。"""
        self.authorization = AuthorizationService()
        self.repository = HomeRepository()
        self.requirements = HomeRequirementWorkRepository()

    def permissions(self, db: Session, user: User) -> dict[str, bool]:
        """system権限を一度ずつ確認し、所属内の通常RBACと合成する。"""
        return {
            code: self.authorization.has_system_permission(
                db, user=user, permission_code=code
            )
            for code in ("project:read", "task:read")
        }

    def tasks(
        self, db: Session, user: User, timezone: str, limit: int
    ) -> HomeTasksRead:
        """今日の作業だけを返し、通知や案件の取得とは独立させる。"""
        today = home_today(timezone)
        summary, items = self.repository.work(
            db, user.id, self.permissions(db, user), today, limit
        )
        return HomeTasksRead.model_validate(
            dict(today=today, timezone=timezone, summary=summary, items=items)
        )

    def projects(
        self, db: Session, user: User, timezone: str, limit: int
    ) -> HomeProjectsRead:
        """参加案件とタスク状況を返し、通知状態は変更しない。"""
        today = home_today(timezone)
        total, items = self.repository.projects(
            db, user.id, self.permissions(db, user), today, limit
        )
        return HomeProjectsRead.model_validate(
            dict(today=today, timezone=timezone, total=total, items=items)
        )

    def requirement_work(
        self, db: Session, user: User, page: int, page_size: int
    ) -> HomeRequirementWorkRead:
        """アクティブな組織の、本人の依頼・修正指摘だけを返す。"""
        result = HomeRequirementWorkRead(
            total=0, items=[], page=page, page_size=page_size
        )
        tenant_id = db.info.get("tenant_id")
        if (
            tenant_id is None
            or db.info.get("tenant_user_id") != user.id
            or db.info.get("demo_id") is not None
            or db.info.get("data_realm") == "demo"
            or db.info.get("mcp_connection_id") is not None
            or user.user_type != UserType.INTERNAL.value
            or user.password_change_required
        ):
            return result
        projects: dict[ProjectAccess, list[int]] = defaultdict(list)
        for project_id in self.requirements.candidate_projects(db, tenant_id, user.id):

            def permits(code: str, scope: int = project_id) -> bool:
                return self.authorization.has_project_permission(
                    db, user=user, project_id=scope, permission_code=code
                )

            if not permits("requirement:read"):
                continue
            can_read_fixed = permits("artifact_release:read")
            access = ProjectAccess(
                tuple(
                    kind
                    for kind, prefix in SOURCE_PERMISSIONS.items()
                    if permits(f"{prefix}:read")
                ),
                all(permits(code) for code in APPROVER_PERMISSIONS),
                can_read_fixed
                and permits("requirement:review")
                and permits("requirement:update"),
            )
            projects[access].append(project_id)
        total, rows = self.requirements.work(db, user.id, projects, page, page_size)
        result.total = total
        for row in rows:
            prefix = "A" if row.kind == "formal_approval" else "R"
            result.items.append(
                HomeRequirementWorkItem.model_validate(
                    dict(row, version_label=f"{prefix}-{row.display_number:03d}")
                )
            )
        return result
