"""要件定義対象コメントサービスを定義するモジュール。"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import NotFoundError
from app.models.requirement import (
    Requirement,
    RequirementDocument,
    RequirementOpenIssue,
    RequirementSection,
    RequirementTargetComment,
)
from app.repositories.requirement import (
    RequirementDocumentRepository,
    RequirementOpenIssueRepository,
    RequirementRepository,
    RequirementSectionRepository,
    RequirementTargetCommentRepository,
)
from app.repositories.user import UserRepository
from app.schemas.change_log import ChangeLogUserRead
from app.schemas.requirement import (
    RequirementTargetCommentCreate,
    RequirementTargetCommentRead,
    RequirementTargetCommentStateUpdate,
    RequirementTargetCommentUpdate,
)
from app.services.conflict import raise_if_version_conflict
from app.services.requirement_change_log import (
    RequirementChangeLogAction,
    RequirementChangeLogService,
    RequirementChangeLogTargetType,
)
from app.services.response_user import build_response_users_by_id


class RequirementCommentTargetType:
    """要件定義対象コメントの対象種別定数。"""

    DOCUMENT = "document"
    OPEN_ISSUE = "open_issue"
    REQUIREMENT_ITEM = "requirement_item"
    SECTION = "section"


@dataclass(frozen=True)
class CommentTarget:
    """コメント対象の解決結果。"""

    document_id: int
    target_type: str
    target_id: int


class RequirementTargetCommentService:
    """要件定義対象コメントに関するビジネスロジックを提供する。"""

    def __init__(
        self,
        repository: RequirementTargetCommentRepository | None = None,
        document_repository: RequirementDocumentRepository | None = None,
        section_repository: RequirementSectionRepository | None = None,
        requirement_repository: RequirementRepository | None = None,
        open_issue_repository: RequirementOpenIssueRepository | None = None,
        change_log_service: RequirementChangeLogService | None = None,
        user_repository: UserRepository | None = None,
    ) -> None:
        """RequirementTargetCommentServiceを初期化する。

        Args:
            repository: 要件定義対象コメントRepository。
            document_repository: 要件定義書Repository。
            section_repository: 要件定義セクションRepository。
            requirement_repository: 要件Repository。
            open_issue_repository: 未決事項Repository。
            change_log_service: 要件定義変更履歴サービス。
            user_repository: ユーザーRepository。
        """
        self.repository = repository or RequirementTargetCommentRepository()
        self.document_repository = (
            document_repository or RequirementDocumentRepository()
        )
        self.section_repository = section_repository or RequirementSectionRepository()
        self.requirement_repository = requirement_repository or RequirementRepository()
        self.open_issue_repository = (
            open_issue_repository or RequirementOpenIssueRepository()
        )
        self.change_log_service = change_log_service or RequirementChangeLogService()
        self.user_repository = user_repository or UserRepository()

    def create_comment(
        self,
        db: Session,
        *,
        project_id: int,
        comment_in: RequirementTargetCommentCreate,
        author_id: int,
    ) -> RequirementTargetComment:
        """要件定義対象コメントを作成する。

        Args:
            db: DBセッション。
            project_id: 作成対象のプロジェクトID。
            comment_in: コメント作成入力値。
            author_id: 投稿ユーザーID。

        Returns:
            作成された要件定義対象コメント。
        """
        target = self._resolve_target(
            db,
            project_id=project_id,
            target_type=comment_in.target_type,
            target_id=comment_in.target_id,
        )
        if comment_in.parent_comment_id is not None:
            self._get_parent_comment_for_target(
                db,
                parent_comment_id=comment_in.parent_comment_id,
                target=target,
            )
        comment = self.repository.create(
            db,
            document_id=target.document_id,
            comment_in=comment_in,
            author_id=author_id,
        )
        self._record_change_log(
            db,
            comment=comment,
            action=RequirementChangeLogAction.COMMENT_CREATED,
            new_value=self._build_comment_snapshot(comment),
            changed_by=author_id,
        )
        return comment

    def list_comments(
        self,
        db: Session,
        *,
        project_id: int,
        target_type: str,
        target_id: int,
    ) -> list[RequirementTargetComment]:
        """対象に紐づくコメント一覧を取得する。"""
        target = self._resolve_target(
            db,
            project_id=project_id,
            target_type=target_type,
            target_id=target_id,
        )
        return self.repository.list_by_target(
            db,
            document_id=target.document_id,
            target_type=target.target_type,
            target_id=target.target_id,
        )

    def list_comment_reads(
        self,
        db: Session,
        *,
        project_id: int,
        target_type: str,
        target_id: int,
    ) -> list[RequirementTargetCommentRead]:
        """対象に紐づくコメント一覧レスポンスを取得する。"""
        comments = self.list_comments(
            db,
            project_id=project_id,
            target_type=target_type,
            target_id=target_id,
        )
        return self.build_comment_reads(db, comments)

    def build_comment_read(
        self,
        db: Session,
        comment: RequirementTargetComment,
    ) -> RequirementTargetCommentRead:
        """要件定義対象コメントレスポンスを作成する。"""
        return self.build_comment_reads(db, [comment])[0]

    def build_comment_reads(
        self,
        db: Session,
        comments: list[RequirementTargetComment],
    ) -> list[RequirementTargetCommentRead]:
        """要件定義対象コメントモデル一覧からレスポンス一覧を作成する。"""
        users_by_id = build_response_users_by_id(
            db,
            self.user_repository,
            [comment.author_id for comment in comments],
        )
        return [
            self._build_target_comment_read(comment, users_by_id=users_by_id)
            for comment in comments
        ]

    def update_comment(
        self,
        db: Session,
        *,
        project_id: int,
        comment_id: int,
        comment_in: RequirementTargetCommentUpdate,
        actor_id: int,
    ) -> RequirementTargetComment:
        """要件定義対象コメントを更新する。"""
        comment = self.get_comment(db, project_id=project_id, comment_id=comment_id)
        raise_if_version_conflict(
            current_version=comment.version,
            requested_version=comment_in.version,
            current=RequirementTargetCommentRead.model_validate(comment).model_dump(),
        )
        before_value = self._build_comment_snapshot(comment)
        updated_comment = self.repository.update(
            db,
            comment=comment,
            comment_in=comment_in,
        )
        self._record_change_log(
            db,
            comment=updated_comment,
            action=RequirementChangeLogAction.COMMENT_UPDATED,
            old_value=before_value,
            new_value=self._build_comment_snapshot(updated_comment),
            reason=comment_in.reason,
            changed_by=actor_id,
        )
        return updated_comment

    def resolve_comment(
        self,
        db: Session,
        *,
        project_id: int,
        comment_id: int,
        state_in: RequirementTargetCommentStateUpdate,
        actor_id: int,
    ) -> RequirementTargetComment:
        """要件定義対象コメントを解決済みにする。"""
        return self._set_resolved(
            db,
            project_id=project_id,
            comment_id=comment_id,
            state_in=state_in,
            actor_id=actor_id,
            is_resolved=True,
            action=RequirementChangeLogAction.COMMENT_RESOLVED,
        )

    def reopen_comment(
        self,
        db: Session,
        *,
        project_id: int,
        comment_id: int,
        state_in: RequirementTargetCommentStateUpdate,
        actor_id: int,
    ) -> RequirementTargetComment:
        """要件定義対象コメントを未解決に戻す。"""
        return self._set_resolved(
            db,
            project_id=project_id,
            comment_id=comment_id,
            state_in=state_in,
            actor_id=actor_id,
            is_resolved=False,
            action=RequirementChangeLogAction.COMMENT_REOPENED,
        )

    def delete_comment(
        self,
        db: Session,
        *,
        project_id: int,
        comment_id: int,
        actor_id: int,
    ) -> None:
        """要件定義対象コメントを論理削除する。"""
        comment = self.get_comment(db, project_id=project_id, comment_id=comment_id)
        before_value = self._build_comment_snapshot(comment)
        deleted_comment = self.repository.soft_delete(db, comment=comment)
        self._record_change_log(
            db,
            comment=deleted_comment,
            action=RequirementChangeLogAction.COMMENT_DELETED,
            old_value=before_value,
            changed_by=actor_id,
        )

    def get_comment(
        self,
        db: Session,
        *,
        project_id: int,
        comment_id: int,
    ) -> RequirementTargetComment:
        """要件定義対象コメントを取得する。"""
        comment = self.repository.get_by_id(db, comment_id)
        if comment is None:
            raise NotFoundError(error_messages.REQUIREMENT_TARGET_COMMENT_NOT_FOUND)
        self._resolve_target(
            db,
            project_id=project_id,
            target_type=comment.target_type,
            target_id=comment.target_id,
        )
        return comment

    def _set_resolved(
        self,
        db: Session,
        *,
        project_id: int,
        comment_id: int,
        state_in: RequirementTargetCommentStateUpdate,
        actor_id: int,
        is_resolved: bool,
        action: str,
    ) -> RequirementTargetComment:
        """要件定義対象コメントの解決状態を更新する。"""
        comment = self.get_comment(db, project_id=project_id, comment_id=comment_id)
        raise_if_version_conflict(
            current_version=comment.version,
            requested_version=state_in.version,
            current=RequirementTargetCommentRead.model_validate(comment).model_dump(),
        )
        before_value = self._build_comment_snapshot(comment)
        updated_comment = self.repository.set_resolved(
            db,
            comment=comment,
            is_resolved=is_resolved,
        )
        self._record_change_log(
            db,
            comment=updated_comment,
            action=action,
            old_value=before_value,
            new_value=self._build_comment_snapshot(updated_comment),
            reason=state_in.reason,
            changed_by=actor_id,
        )
        return updated_comment

    def _resolve_target(
        self,
        db: Session,
        *,
        project_id: int,
        target_type: str,
        target_id: int,
    ) -> CommentTarget:
        """コメント対象を取得し、プロジェクト所属を確認する。"""
        if target_type == RequirementCommentTargetType.DOCUMENT:
            document = self._get_document_in_project(
                db,
                project_id=project_id,
                document_id=target_id,
            )
            return CommentTarget(document.id, target_type, target_id)
        if target_type == RequirementCommentTargetType.SECTION:
            section = self._get_section_in_project(
                db,
                project_id=project_id,
                section_id=target_id,
            )
            return CommentTarget(section.document_id, target_type, target_id)
        if target_type == RequirementCommentTargetType.REQUIREMENT_ITEM:
            requirement = self._get_requirement_in_project(
                db,
                project_id=project_id,
                requirement_id=target_id,
            )
            return CommentTarget(requirement.document_id, target_type, target_id)
        if target_type == RequirementCommentTargetType.OPEN_ISSUE:
            issue = self._get_open_issue_in_project(
                db,
                project_id=project_id,
                issue_id=target_id,
            )
            return CommentTarget(issue.document_id, target_type, target_id)
        raise NotFoundError(error_messages.REQUIREMENT_COMMENT_TARGET_NOT_FOUND)

    def _get_parent_comment_for_target(
        self,
        db: Session,
        *,
        parent_comment_id: int,
        target: CommentTarget,
    ) -> RequirementTargetComment:
        """対象に紐づく親コメントを取得する。"""
        parent = self.repository.get_by_id(db, parent_comment_id)
        if (
            parent is None
            or parent.document_id != target.document_id
            or parent.target_type != target.target_type
            or parent.target_id != target.target_id
        ):
            raise NotFoundError(error_messages.REQUIREMENT_TARGET_COMMENT_NOT_FOUND)
        return parent

    def _get_document_in_project(
        self,
        db: Session,
        *,
        project_id: int,
        document_id: int,
    ) -> RequirementDocument:
        """プロジェクト内の要件定義書を取得する。"""
        document = self.document_repository.get_by_id(db, document_id)
        if document is None or document.project_id != project_id:
            raise NotFoundError(error_messages.REQUIREMENT_COMMENT_TARGET_NOT_FOUND)
        return document

    def _get_section_in_project(
        self,
        db: Session,
        *,
        project_id: int,
        section_id: int,
    ) -> RequirementSection:
        """プロジェクト内のセクションを取得する。"""
        section = self.section_repository.get_by_id(db, section_id)
        if section is None:
            raise NotFoundError(error_messages.REQUIREMENT_COMMENT_TARGET_NOT_FOUND)
        self._get_document_in_project(
            db,
            project_id=project_id,
            document_id=section.document_id,
        )
        return section

    def _get_requirement_in_project(
        self,
        db: Session,
        *,
        project_id: int,
        requirement_id: int,
    ) -> Requirement:
        """プロジェクト内の要件を取得する。"""
        requirement = self.requirement_repository.get_by_id(db, requirement_id)
        if requirement is None:
            raise NotFoundError(error_messages.REQUIREMENT_COMMENT_TARGET_NOT_FOUND)
        self._get_document_in_project(
            db,
            project_id=project_id,
            document_id=requirement.document_id,
        )
        return requirement

    def _get_open_issue_in_project(
        self,
        db: Session,
        *,
        project_id: int,
        issue_id: int,
    ) -> RequirementOpenIssue:
        """プロジェクト内の未決事項を取得する。"""
        issue = self.open_issue_repository.get_by_id(db, issue_id)
        if issue is None:
            raise NotFoundError(error_messages.REQUIREMENT_COMMENT_TARGET_NOT_FOUND)
        self._get_document_in_project(
            db,
            project_id=project_id,
            document_id=issue.document_id,
        )
        return issue

    def _record_change_log(
        self,
        db: Session,
        *,
        comment: RequirementTargetComment,
        action: str,
        old_value: dict | None = None,
        new_value: dict | None = None,
        reason: str | None = None,
        changed_by: int | None = None,
    ) -> None:
        """コメント操作の変更履歴を記録する。"""
        self.change_log_service.record(
            db,
            document_id=comment.document_id,
            target_type=RequirementChangeLogTargetType.COMMENT,
            target_id=comment.id,
            action=action,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
            changed_by=changed_by,
        )

    def _build_comment_snapshot(
        self,
        comment: RequirementTargetComment,
    ) -> dict[str, object]:
        """変更履歴に保存するコメントスナップショットを作成する。"""
        return RequirementTargetCommentRead.model_validate(comment).model_dump(
            mode="json",
            exclude={"author"},
        )

    def _build_target_comment_read(
        self,
        comment: RequirementTargetComment,
        *,
        users_by_id: dict[int, ChangeLogUserRead],
    ) -> RequirementTargetCommentRead:
        """要件定義対象コメントレスポンスを作成する。"""
        return RequirementTargetCommentRead.model_validate(comment).model_copy(
            update={"author": users_by_id.get(comment.author_id)},
        )
