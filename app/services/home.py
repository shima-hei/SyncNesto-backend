"""ログインユーザーの個人ワークスペースを構成する。"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.home import HomeRepository
from app.schemas.home import HomeProjectsRead, HomeTasksRead
from app.services.authorization import AuthorizationService


def home_today(timezone: str, now: datetime | None = None) -> date:
    """UTCの現在時刻を表示と同じタイムゾーンの暦日に変換する。"""
    return (now or datetime.now(UTC)).astimezone(ZoneInfo(timezone)).date()


class HomeService:
    """権限確認と表示契約の構築を担う読み取り専用Service。"""

    def __init__(self) -> None:
        """既存の認可Serviceと専用の集計Repositoryを使用する。"""
        self.authorization = AuthorizationService()
        self.repository = HomeRepository()

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
