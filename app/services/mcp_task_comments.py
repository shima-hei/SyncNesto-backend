"""開発用MCPの作業コメント。既存Serviceの検証・履歴・通知を保持する。"""

from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.user import User, UserType
from app.presenters.task import TaskPresenter
from app.schemas import mcp_development as s
from app.services.task import TaskService
from app.services.task_comment import TaskCommentService


class McpTaskCommentService:
    """同意Projectの認可は外側で、対象の現所属は毎回ここで確認する。"""

    def __init__(self):
        """既存の作業・コメント処理を再利用する。"""
        self.tasks = TaskService()
        self.comments = TaskCommentService()
        self.presenter = TaskPresenter()

    def task(self, db: Session, project_id: int, task_id: int):
        """指定Projectに属さない作業は、取得済みIDでも受け付けない。"""
        task = self.tasks.get_task(db, task_id)
        if task.project_id != project_id:
            raise NotFoundError()
        return task

    def normal_user(self, db: Session, user: User) -> None:
        """通常のinternal接続だけを受け付け、閲覧専用接続の権限を広げない。"""
        if (
            user.user_type != UserType.INTERNAL.value
            or db.info.get("demo_id")
            or db.info.get("data_realm") == "demo"
        ):
            raise ForbiddenError()

    def validate_create(
        self, db: Session, user: User, data: s.TaskCommentCreateInput
    ) -> None:
        """receipt返却前にも作業・返信先・メンションの失効を確認する。"""
        self.normal_user(db, user)
        self.task(db, data.project_id, data.task_id)
        self.comments.validate_create(db, data.task_id, data.data)

    def validate_replay(
        self, db: Session, data: s.TaskCommentCreateInput, result: dict
    ) -> None:
        """作成されたコメント自体の削除後も、旧receiptのIDを返さない。"""
        comment = self.comments.get_comment(db, result["id"])
        if comment.task_id != data.task_id:
            raise NotFoundError()

    def read(self, db: Session, user: User, data: s.TaskCommentListInput) -> dict:
        """全件の本文をロードせず、必要な頁だけ新しい順で返す。"""
        self.normal_user(db, user)
        self.task(db, data.project_id, data.task_id)
        rows, total = self.comments.comment_repository.list_page_by_task(
            db, data.task_id, data.page, data.page_size
        )
        comments = self.presenter.build_task_comment_responses(
            rows, users_by_id=self.comments.get_task_comment_users_by_id(db, rows)
        )
        items = []
        for row in comments:
            body = row.body[:8000]
            utf16_length = len(body.encode("utf-16-le")) // 2
            mentions = [
                mention for mention in row.mentions if mention.end <= utf16_length
            ]
            items.append(
                s.DevelopmentTaskCommentRead.model_validate(
                    {
                        **row.model_dump(),
                        "body": body,
                        "body_length": len(row.body),
                        "body_truncated": len(row.body) > 8000,
                        "mentions": mentions,
                        "mentions_truncated": len(mentions) != len(row.mentions),
                    }
                )
            )
        return s.TaskCommentPageRead(
            items=items, total=total, page=data.page, page_size=data.page_size
        ).model_dump(mode="json")

    def write(self, db: Session, user: User, data: s.TaskCommentCreateInput) -> dict:
        """外側transactionがreceipt・監査と一緒に確定または取消する。"""
        self.normal_user(db, user)
        self.task(db, data.project_id, data.task_id)
        row = self.comments.create_comment(
            db, task_id=data.task_id, comment_in=data.data, actor_id=user.id
        )
        return s.TaskCommentCreatedRead.model_validate(
            row, from_attributes=True
        ).model_dump(mode="json")
