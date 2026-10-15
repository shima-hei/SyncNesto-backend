"""設計の編集・レビュー・関連付けの業務ルール。"""

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    SpecificationReviewFieldsError,
    SpecificationUnresolvedCommentsError,
)
from app.models.specification import (
    Specification,
    SpecificationComment,
    SpecificationLink,
)
from app.models.user import User
from app.repositories.specification import TARGETS, SpecificationRepository
from app.schemas.specification import (
    SpecificationCommentCreate,
    SpecificationCommentRead,
    SpecificationCreate,
    SpecificationLinkCreate,
    SpecificationLinkRead,
    SpecificationList,
    SpecificationListItem,
    SpecificationRead,
    SpecificationTarget,
    SpecificationUpdate,
)
from app.services.audit_log import AuditLogService
from app.services.authorization import AuthorizationService
from app.services.conflict import raise_if_version_conflict

TARGET_PERMISSIONS = {
    "requirement": "requirement:read",
    "task": "task:read",
    "test_item": "test_plan:read",
    "specification": "document:read",
    "document": "document:read",
}


def field_value(row: Specification, path: str) -> str | None:
    """項目UUIDを使ったパスを、現在の表示値へ解決する。"""
    if path in {"title", "summary"}:
        return getattr(row, path)
    parts = path.split("/")
    if parts[0] != "content":
        return None
    value = row.content
    for part in parts[1:]:
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list):
            value = next((item for item in value if str(item.get("id")) == part), None)
            if value is None:
                return None
        else:
            return None
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return None
    return (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, sort_keys=True)
    )


def anchor_digest(row: Specification, path: str) -> str | None:
    value = field_value(row, path)
    return sha256(value.encode("utf-8")).hexdigest() if value is not None else None


class SpecificationService:
    """Project認可はRouter、関連先ごとの認可は本Serviceで判定する。"""

    def __init__(self):
        self.repository = SpecificationRepository()
        self.authorization = AuthorizationService()

    def get(
        self, db: Session, project_id: int, specification_id: int, *, lock: bool = False
    ):
        row = self.repository.get(db, project_id, specification_id, lock=lock)
        if row is None:
            raise NotFoundError(error_messages.SPECIFICATION_NOT_FOUND)
        return row

    def check_version(self, row: Specification, version: int):
        raise_if_version_conflict(
            current_version=row.version,
            requested_version=version,
            current=SpecificationRead.model_validate(row).model_dump(mode="json"),
        )

    def audit(self, db: Session, row: Specification, user_id: int, action: str):
        AuditLogService().record(
            db,
            event_type=f"specification.{action}",
            actor_user_id=user_id,
            project_id=row.project_id,
            resource_type="specification",
            resource_id=row.id,
            metadata={"version": row.version, "kind": row.kind},
        )

    def revision(
        self, db: Session, project_id: int, specification_id: int, number: int
    ):
        row = self.get(db, project_id, specification_id)
        revision = self.repository.revision_number(db, row.id, number)
        if revision is None:
            raise NotFoundError(error_messages.SPECIFICATION_REVISION_NOT_FOUND)
        return revision

    def list(
        self,
        db: Session,
        project_id: int,
        kind: str | None,
        q: str,
        page: int,
        page_size: int,
    ):
        rows, total = self.repository.list(db, project_id, kind, q, page, page_size)
        return SpecificationList(
            items=[SpecificationListItem.model_validate(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    def create(
        self, db: Session, project_id: int, data: SpecificationCreate, user: User
    ):
        row = Specification(
            project_id=project_id,
            kind=data.content.type,
            title=data.title,
            summary=data.summary,
            content=data.content.model_dump(mode="json"),
            created_by=user.id,
            updated_by=user.id,
            status="draft",
            version=1,
        )
        db.add(row)
        self.repository.revision(db, row, user.id)
        db.commit()
        self.audit(db, row, user.id, "created")
        return row

    def update(
        self,
        db: Session,
        project_id: int,
        specification_id: int,
        data: SpecificationUpdate,
        user: User,
    ):
        row = self.get(db, project_id, specification_id, lock=True)
        self.check_version(row, data.version)
        if row.kind != data.content.type:
            raise BadRequestError(error_messages.SPECIFICATION_KIND_IMMUTABLE)
        row.title, row.summary, row.content = (
            data.title,
            data.summary,
            data.content.model_dump(mode="json"),
        )
        # 承認済みの内容を変更した場合は、再確認のため下書きに戻す。
        if row.status == "approved":
            row.status = "draft"
        row.version += 1
        row.updated_by = user.id
        self.repository.revision(db, row, user.id)
        db.commit()
        self.audit(db, row, user.id, "updated")
        return row

    def status(
        self,
        db: Session,
        project_id: int,
        specification_id: int,
        version: int,
        status: str,
        user: User,
    ):
        row = self.get(db, project_id, specification_id, lock=True)
        self.check_version(row, version)
        if status in {
            "approved",
            "changes_requested",
        } and not self.authorization.has_project_permission(
            db, user=user, project_id=project_id, permission_code="requirement:review"
        ):
            raise ForbiddenError()
        if status == "approved" and self.repository.has_unresolved_comments(db, row.id):
            raise SpecificationUnresolvedCommentsError()
        if status in {"in_review", "approved"}:
            content = row.content
            if (
                not row.summary.strip()
                or not content.get("path" if row.kind == "api" else "route", "").strip()
            ):
                raise SpecificationReviewFieldsError()
        row.status, row.version, row.updated_by = status, row.version + 1, user.id
        self.repository.revision(db, row, user.id)
        db.commit()
        self.audit(db, row, user.id, "status_changed")
        return row

    def delete(
        self,
        db: Session,
        project_id: int,
        specification_id: int,
        version: int,
        user: User,
    ):
        row = self.get(db, project_id, specification_id, lock=True)
        self.check_version(row, version)
        row.deleted_at = datetime.now(UTC)
        row.version += 1
        db.commit()
        self.audit(db, row, user.id, "deleted")

    def comments(
        self,
        db: Session,
        project_id: int,
        specification_id: int,
        page: int = 1,
        page_size: int = 100,
    ):
        row = self.get(db, project_id, specification_id)
        comments = self.repository.comments(db, row.id, page, page_size)
        authors = {
            author_id: name
            for author_id, name in db.execute(
                select(User.id, User.name).where(
                    User.id.in_({comment.author_id for comment in comments})
                )
            ).all()
        }
        return [
            SpecificationCommentRead.model_validate(comment).model_copy(
                update={
                    "anchor_changed": anchor_digest(row, comment.field_path)
                    != comment.field_digest,
                    "author_name": authors.get(comment.author_id, "メンバー"),
                }
            )
            for comment in comments
        ]

    def comment(
        self,
        db: Session,
        project_id: int,
        specification_id: int,
        data: SpecificationCommentCreate,
        user: User,
    ):
        row = self.get(db, project_id, specification_id, lock=True)
        self.check_version(row, data.specification_version)
        snapshot = field_value(row, data.field_path)
        if snapshot is None or not data.body.strip():
            raise BadRequestError(error_messages.SPECIFICATION_COMMENT_TARGET_INVALID)
        if self.repository.comment_count(db, row.id) >= 500:
            raise BadRequestError(error_messages.SPECIFICATION_COMMENT_LIMIT)
        comment = SpecificationComment(
            specification_id=row.id,
            author_id=user.id,
            field_path=data.field_path,
            field_snapshot=snapshot[:800],
            field_digest=sha256(snapshot.encode("utf-8")).hexdigest(),
            specification_version=row.version,
            body=data.body.strip(),
        )
        db.add(comment)
        db.commit()
        self.audit(db, row, user.id, "commented")
        return SpecificationCommentRead.model_validate(comment)

    def resolve(
        self,
        db: Session,
        project_id: int,
        specification_id: int,
        comment_id: int,
        version: int,
        resolved: bool,
        user: User,
    ):
        row = self.get(db, project_id, specification_id, lock=True)
        comment = self.repository.comment(db, row.id, comment_id)
        if comment is None:
            raise NotFoundError(error_messages.SPECIFICATION_COMMENT_NOT_FOUND)
        raise_if_version_conflict(
            current_version=comment.version,
            requested_version=version,
            current=SpecificationCommentRead.model_validate(comment).model_dump(
                mode="json"
            ),
        )
        comment.is_resolved, comment.version = resolved, comment.version + 1
        db.commit()
        self.audit(
            db, row, user.id, "comment_resolved" if resolved else "comment_reopened"
        )
        return SpecificationCommentRead.model_validate(comment).model_copy(
            update={
                "anchor_changed": anchor_digest(row, comment.field_path)
                != comment.field_digest
            }
        )

    def can_read_target(self, db: Session, user: User, project_id: int, kind: str):
        return self.authorization.has_project_permission(
            db,
            user=user,
            project_id=project_id,
            permission_code=TARGET_PERMISSIONS[kind],
        )

    def candidates(self, db: Session, project_id: int, kind: str, q: str, user: User):
        if not self.can_read_target(db, user, project_id, kind):
            return []
        return [
            SpecificationLinkRead(
                target_type=cast(SpecificationTarget, kind),
                target_id=str(row["id"]),
                title=row["title"] or "名称未設定",
                container_id=row["container_id"],
            )
            for row in self.repository.candidates(db, project_id, kind, q)
        ]

    def links(self, db: Session, project_id: int, specification_id: int, user: User):
        row = self.get(db, project_id, specification_id)
        links = self.repository.links(db, row.id)
        result = []
        for kind, (model, column, *_) in TARGETS.items():
            selected = [link for link in links if getattr(link, column) is not None]
            if not selected or not self.can_read_target(db, user, project_id, kind):
                continue
            targets = {
                str(item["id"]): item
                for item in db.execute(
                    self.repository.target_query(project_id, kind).where(
                        model.id.in_([getattr(link, column) for link in selected])
                    )
                ).mappings()
            }
            for link in selected:
                target_id = str(getattr(link, column))
                target = targets.get(target_id)
                if target:
                    result.append(
                        SpecificationLinkRead(
                            id=link.id,
                            target_type=cast(SpecificationTarget, kind),
                            target_id=target_id,
                            title=target["title"] or "名称未設定",
                            container_id=target["container_id"],
                        )
                    )
        return sorted(result, key=lambda item: item.id or 0)

    def add_link(
        self,
        db: Session,
        project_id: int,
        specification_id: int,
        data: SpecificationLinkCreate,
        user: User,
    ):
        row = self.get(db, project_id, specification_id, lock=True)
        if not self.can_read_target(db, user, project_id, data.target_type):
            raise ForbiddenError()
        target = self.repository.target(
            db, project_id, data.target_type, data.target_id
        )
        if target is None:
            raise NotFoundError(error_messages.SPECIFICATION_TARGET_NOT_FOUND)
        if data.target_type == "specification" and target["id"] == row.id:
            raise BadRequestError(error_messages.SPECIFICATION_SELF_LINK)
        column = TARGETS[data.target_type][1]
        links = self.repository.links(db, row.id)
        if not any(str(getattr(link, column)) == str(target["id"]) for link in links):
            if len(links) >= 200:
                raise BadRequestError(error_messages.SPECIFICATION_LINK_LIMIT)
            db.add(SpecificationLink(specification_id=row.id, **{column: target["id"]}))
            db.commit()
            self.audit(db, row, user.id, "linked")
        return self.links(db, project_id, specification_id, user)

    def delete_link(
        self,
        db: Session,
        project_id: int,
        specification_id: int,
        link_id: int,
        user: User,
    ):
        row = self.get(db, project_id, specification_id, lock=True)
        link = next(
            (item for item in self.repository.links(db, row.id) if item.id == link_id),
            None,
        )
        if link is None:
            raise NotFoundError(error_messages.SPECIFICATION_LINK_NOT_FOUND)
        db.delete(link)
        db.commit()
        self.audit(db, row, user.id, "unlinked")

    def related(
        self, db: Session, project_id: int, kind: str, target_id: str, user: User
    ):
        if not self.can_read_target(db, user, project_id, kind):
            raise ForbiddenError()
        if self.repository.target(db, project_id, kind, target_id) is None:
            raise NotFoundError(error_messages.SPECIFICATION_TARGET_NOT_FOUND)
        return [
            SpecificationListItem.model_validate(row)
            for row in self.repository.related(db, project_id, kind, target_id)
        ]
