"""履歴と独立した通知ポリシー、既読管理、対象の表示可否。"""

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import NotFoundError
from app.models.notification import Notification, NotificationType
from app.models.notification import NotificationTargetType as Target
from app.models.project import Project
from app.models.requirement import (
    Requirement,
    RequirementComment,
    RequirementDocument,
    RequirementTargetComment,
)
from app.models.task import Task, TaskComment
from app.models.test_design import TestDesign, TestDesignComment
from app.models.user import User
from app.repositories.comment_mention import CommentMentionRepository
from app.repositories.notification import NotificationRepository
from app.schemas.notification import (
    NotificationContext,
    NotificationListResponse,
    NotificationRead,
    NotificationSnapshot,
)
from app.services.authorization import AuthorizationService

TARGET_PERMISSIONS = {
    Target.TASK: "task:read",
    Target.TASK_COMMENT: "task:read",
    Target.REQUIREMENT: "requirement:read",
    Target.OPEN_ISSUE: "requirement:read",
    Target.REQUIREMENT_COMMENT: "requirement:read",
    Target.REQUIREMENT_TARGET_COMMENT: "requirement:read",
    Target.TEST_DESIGN_COMMENT: "test_plan:read",
}
Comment = (
    RequirementComment | RequirementTargetComment | TaskComment | TestDesignComment
)


class NotificationService:
    """業務Serviceが確定した差分だけを受け取り、commitは呼び出し元に委ねる。"""

    def __init__(self) -> None:
        """共通Repositoryを初期化する。"""
        self.repository = NotificationRepository()

    def emit(
        self,
        db: Session,
        *,
        recipient_ids: set[int],
        actor_id: int | None,
        project_id: int,
        kind: NotificationType,
        target_type: Target,
        target_id: int,
        version: int,
        target_title: str,
        context: dict,
        excerpt: str | None = None,
    ) -> None:
        """有効な所属と閲覧権限を持つ宛先へイベントを一度だけ保存する。"""
        recipient_ids = recipient_ids - {actor_id}
        if not recipient_ids:
            return
        eligible = (
            CommentMentionRepository()
            .eligible_users(db, project_id, TARGET_PERMISSIONS[target_type])
            .filter(User.id.in_(recipient_ids))
        )
        actor = db.get(User, actor_id) if actor_id is not None else None
        project = db.get(Project, project_id)
        snapshot = {
            "actor_name": actor.name if actor else "ユーザー",
            "project_name": project.name if project else "プロジェクト",
            "target_title": target_title,
            "excerpt": excerpt[:240] if excerpt else None,
        }
        for recipient in eligible:
            self.repository.create_once(
                db,
                recipient_user_id=recipient.id,
                type=kind.value,
                actor_user_id=actor_id,
                project_id=project_id,
                target_type=target_type.value,
                target_id=str(target_id),
                event_key=f"{target_type.value}:{target_id}:v{version}:{kind.value}",
                snapshot=snapshot,
                context=context,
            )

    def assignment_changed(
        self,
        db: Session,
        *,
        project_id: int,
        actor_id: int | None,
        target_type: Target,
        target_id: int,
        version: int,
        previous_user_id: int | None,
        recipient_id: int | None,
        title: str,
        document_id: int | None = None,
    ) -> None:
        """A→A、解除、自分への割り当てを除外する。"""
        if recipient_id is None or recipient_id == previous_user_id:
            return
        self.emit(
            db,
            recipient_ids={recipient_id},
            actor_id=actor_id,
            project_id=project_id,
            kind=NotificationType.ASSIGNED,
            target_type=target_type,
            target_id=target_id,
            version=version,
            target_title=title,
            context={"document_id": document_id},
        )

    def mentions_changed(
        self,
        db: Session,
        *,
        project_id: int,
        actor_id: int | None,
        comment: Comment,
        previous_user_ids: set[int] | None = None,
    ) -> None:
        """保存済み関連の新規宛先だけ通知し、本文の名前から推測しない。"""
        recipients = {target.user_id for target in comment.mention_targets} - (
            previous_user_ids or set()
        )
        if not recipients or recipients == {actor_id}:
            return
        context: dict = {}
        if isinstance(comment, TaskComment):
            kind = Target.TASK_COMMENT
            task = db.get(Task, comment.task_id)
            title = task.title if task else "タスク"
            context = {"task_id": comment.task_id}
        elif isinstance(comment, RequirementComment):
            kind = Target.REQUIREMENT_COMMENT
            requirement = db.get(Requirement, comment.requirement_id)
            title = requirement.title if requirement else "要件"
            context = {
                "requirement_id": comment.requirement_id,
                "document_id": requirement.document_id if requirement else None,
            }
        elif isinstance(comment, RequirementTargetComment):
            kind = Target.REQUIREMENT_TARGET_COMMENT
            document = db.get(RequirementDocument, comment.document_id)
            title = document.title if document else "要件定義書"
            context = {
                "document_id": comment.document_id,
                "subject_type": comment.target_type,
                "subject_id": str(comment.target_id),
                "requirement_id": comment.target_id
                if comment.target_type == "requirement_item"
                else None,
            }
        else:
            kind = Target.TEST_DESIGN_COMMENT
            design = db.get(TestDesign, comment.design_id)
            title = design.name if design else "テスト設計書"
            context = {
                "design_id": comment.design_id,
                "subject_type": comment.target_type,
                "subject_id": str(comment.target_id) if comment.target_id else None,
            }
        db.flush()
        self.emit(
            db,
            recipient_ids=recipients,
            actor_id=actor_id,
            project_id=project_id,
            kind=NotificationType.MENTIONED,
            target_type=kind,
            target_id=comment.id,
            version=getattr(comment, "version", 1),
            target_title=title,
            context=context,
            excerpt=comment.comment
            if isinstance(comment, RequirementComment)
            else comment.body,
        )

    def present(
        self, db: Session, user: User, items: list[Notification]
    ) -> list[NotificationRead]:
        """名前変更や対象削除があっても、過去の表示を再現する。"""
        available = self.repository.available_targets(db, items)
        permissions = {}
        result = []
        for item in items:
            permission = TARGET_PERMISSIONS[Target(item.target_type)]
            key = (item.project_id, permission)
            if key not in permissions:
                permissions[key] = (
                    item.project_id is not None
                    and AuthorizationService().has_project_permission(
                        db,
                        user=user,
                        project_id=item.project_id,
                        permission_code=permission,
                    )
                )
            status = "available"
            if not permissions[key]:
                status = "forbidden"
            elif (item.target_type, item.target_id) not in available:
                status = "deleted"
            readable = permissions[key]
            result.append(
                NotificationRead(
                    id=item.id,
                    type=NotificationType(item.type),
                    actor_user_id=item.actor_user_id if readable else None,
                    project_id=item.project_id if readable else None,
                    target_type=Target(item.target_type),
                    target_id=item.target_id if readable else "0",
                    snapshot=NotificationSnapshot.model_validate(item.snapshot)
                    if readable
                    else NotificationSnapshot(
                        actor_name="ユーザー",
                        project_name="閲覧できないプロジェクト",
                        target_title="閲覧できない通知",
                    ),
                    context=NotificationContext.model_validate(item.context)
                    if readable
                    else NotificationContext(),
                    target_status=status,
                    is_read=item.read_at is not None,
                    read_at=item.read_at,
                    created_at=item.created_at,
                )
            )
        return result

    def list(
        self,
        db: Session,
        user: User,
        *,
        page: int,
        page_size: int,
        unread_only: bool,
        project_id: int | None,
    ) -> NotificationListResponse:
        """宛先ユーザーは認証済みユーザーから決定する。"""
        items, total = self.repository.list_paginated(
            db,
            user.id,
            page=page,
            page_size=page_size,
            unread_only=unread_only,
            project_id=project_id,
        )
        return NotificationListResponse(
            items=self.present(db, user, items),
            total=total,
            page=page,
            page_size=page_size,
        )

    def mark_read(
        self, db: Session, user: User, notification_id: int
    ) -> NotificationRead:
        """他人の通知には404を返し、再実行では既読日時を変えない。"""
        item = self.repository.get(db, user.id, notification_id)
        if item is None:
            raise NotFoundError(error_messages.NOTIFICATION_NOT_FOUND)
        self.repository.mark_read(db, user.id, notification_id)
        db.refresh(item)
        return self.present(db, user, [item])[0]

    def unread_count(self, db: Session, user: User, project_id: int | None) -> int:
        """本人の未読件数を返す。"""
        return self.repository.unread_count(db, user.id, project_id)

    def mark_all_read(self, db: Session, user: User, project_id: int | None) -> int:
        """本人の通知だけを一括既読にする。"""
        return self.repository.mark_all_read(db, user.id, project_id)
