"""要件定義Repositoryを定義するモジュール。"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.requirement import (
    RequirementTargetComment,
)
from app.schemas.requirement import (
    RequirementTargetCommentCreate,
    RequirementTargetCommentUpdate,
)


class RequirementTargetCommentRepository:
    """RequirementTargetCommentテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        document_id: int,
        comment_in: RequirementTargetCommentCreate,
        author_id: int,
    ) -> RequirementTargetComment:
        """要件定義対象コメントを作成する。"""
        comment = RequirementTargetComment(
            document_id=document_id,
            target_type=comment_in.target_type,
            target_id=comment_in.target_id,
            target_anchor=comment_in.target_anchor,
            parent_comment_id=comment_in.parent_comment_id,
            body=comment_in.body,
            author_id=author_id,
        )
        db.add(comment)
        db.commit()
        db.refresh(comment)
        return comment

    def get_by_id(
        self,
        db: Session,
        comment_id: int,
    ) -> RequirementTargetComment | None:
        """idに一致する要件定義対象コメントを取得する。"""
        return (
            db.query(RequirementTargetComment)
            .filter(
                RequirementTargetComment.id == comment_id,
                RequirementTargetComment.deleted_at.is_(None),
            )
            .first()
        )

    def list_by_target(
        self,
        db: Session,
        *,
        document_id: int,
        target_type: str,
        target_id: int,
    ) -> list[RequirementTargetComment]:
        """対象に紐づくコメント一覧を取得する。"""
        return (
            db.query(RequirementTargetComment)
            .filter(
                RequirementTargetComment.document_id == document_id,
                RequirementTargetComment.target_type == target_type,
                RequirementTargetComment.target_id == target_id,
                RequirementTargetComment.deleted_at.is_(None),
            )
            .order_by(RequirementTargetComment.id)
            .all()
        )

    def list_by_document(
        self,
        db: Session,
        document_id: int,
    ) -> list[RequirementTargetComment]:
        """指定要件定義書のコメント一覧を取得する。"""
        return (
            db.query(RequirementTargetComment)
            .filter(
                RequirementTargetComment.document_id == document_id,
                RequirementTargetComment.deleted_at.is_(None),
            )
            .order_by(RequirementTargetComment.id)
            .all()
        )

    def update(
        self,
        db: Session,
        *,
        comment: RequirementTargetComment,
        comment_in: RequirementTargetCommentUpdate,
    ) -> RequirementTargetComment:
        """要件定義対象コメントを更新する。"""
        comment.body = comment_in.body
        comment.version += 1
        db.commit()
        db.refresh(comment)
        return comment

    def set_resolved(
        self,
        db: Session,
        *,
        comment: RequirementTargetComment,
        is_resolved: bool,
    ) -> RequirementTargetComment:
        """要件定義対象コメントの解決状態を更新する。"""
        comment.is_resolved = is_resolved
        comment.version += 1
        db.commit()
        db.refresh(comment)
        return comment

    def soft_delete(
        self,
        db: Session,
        *,
        comment: RequirementTargetComment,
    ) -> RequirementTargetComment:
        """要件定義対象コメントを論理削除する。"""
        comment.deleted_at = datetime.now(UTC)
        db.commit()
        db.refresh(comment)
        return comment
