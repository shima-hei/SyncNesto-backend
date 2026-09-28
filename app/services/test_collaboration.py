"""テスト設計の要件追跡と対象コメントを管理する。"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import (
    BadRequestError,
    DuplicateResourceError,
    ForbiddenError,
    NotFoundError,
)
from app.models.test_design import (
    ExpectedValue,
    Factor,
    FactorLevel,
    PatternTable,
    RequirementTestItem,
    TestDesign,
    TestDesignColumn,
    TestDesignComment,
    TestDesignCommentChange,
    TestItem,
    TestPattern,
    TestPatternExpectedValue,
    TestPatternValue,
)
from app.repositories.test_collaboration import TestCollaborationRepository
from app.schemas.test_collaboration import (
    RequirementCoverageRead,
    RequirementTestItemCreate,
    RequirementTestItemRead,
    TestDesignCommentChangeRead,
    TestDesignCommentCreate,
    TestDesignCommentRead,
    TestDesignCommentUpdate,
)
from app.services.conflict import raise_if_version_conflict
from app.services.test_design import TestDesignService

TARGET_MODELS = {
    "test_item": TestItem,
    "pattern_table": PatternTable,
    "factor": Factor,
    "factor_level": FactorLevel,
    "combination": TestPattern,
    "expected_value": ExpectedValue,
}
ITEM_FIELDS = {
    "target_feature",
    "viewpoint",
    "content",
    "preconditions",
    "test_data",
    "steps",
    "expected_result",
    "notes",
    "pattern_table_id",
}


class TestCollaborationService:
    """所属と対象を検証し、関連と議論を独立して保存する。"""

    def __init__(self) -> None:
        """Repositoryと設計サービスを初期化する。"""
        self.repository = TestCollaborationRepository()
        self.designs = TestDesignService()

    def _link_read(self, rows: tuple) -> RequirementTestItemRead:
        """関連を双方向共通の表示情報へ変換する。"""
        link, requirement, item, design = rows
        return RequirementTestItemRead(
            id=link.id,
            requirement_id=requirement.id,
            document_id=requirement.document_id,
            requirement_code=requirement.requirement_code,
            requirement_title=requirement.title,
            item_id=item.id,
            item_code=item.code,
            item_content=item.content,
            design_id=design.id,
            design_name=design.name,
            item_deleted=item.deleted_at is not None or design.deleted_at is not None,
            created_at=link.created_at,
        )

    def list_design_links(
        self, db: Session, project_id: int, design_id: int
    ) -> list[RequirementTestItemRead]:
        """設計書の関連要件を取得する。"""
        self.designs.get(db, project_id, design_id)
        return [
            self._link_read(row)
            for row in self.repository.links_for_design(db, design_id)
        ]

    def list_requirement_links(
        self, db: Session, project_id: int, requirement_id: int
    ) -> list[RequirementTestItemRead]:
        """要件の関連テスト項目を取得する。"""
        if self.repository.requirement(db, project_id, requirement_id) is None:
            raise NotFoundError()
        return [
            self._link_read(row)
            for row in self.repository.links_for_requirement(db, requirement_id)
        ]

    def coverage(
        self, db: Session, project_id: int, document_id: int | None
    ) -> list[RequirementCoverageRead]:
        """関連の存在だけをカバレッジとして返す。"""
        self.designs.require_project(db, project_id)
        return [
            RequirementCoverageRead(
                requirement_id=rid, linked_item_count=count, has_tests=count > 0
            )
            for rid, count in self.repository.coverage(db, project_id, document_id)
        ]

    def create_link(
        self,
        db: Session,
        project_id: int,
        design_id: int,
        data: RequirementTestItemCreate,
        actor_id: int,
    ) -> RequirementTestItemRead:
        """同一プロジェクトの有効な要件と項目を関連付ける。"""
        self.designs.get(db, project_id, design_id)
        item = self.repository.item(db, design_id, data.item_id)
        requirement = self.repository.requirement(db, project_id, data.requirement_id)
        if (
            item is None
            or item.deleted_at is not None
            or item.is_spacer
            or requirement is None
        ):
            raise NotFoundError()
        if self.repository.link_by_pair(db, data.requirement_id, data.item_id):
            raise DuplicateResourceError()
        link = RequirementTestItem(
            requirement_id=requirement.id, item_id=item.id, created_by=actor_id
        )
        db.add(link)
        db.commit()
        return self._link_read(
            (link, requirement, item, self.designs.get(db, project_id, design_id))
        )

    def delete_link(
        self, db: Session, project_id: int, design_id: int, link_id: int
    ) -> None:
        """明示的に解除した関連だけを削除する。"""
        self.designs.get(db, project_id, design_id)
        link = self.repository.link(db, link_id)
        if link is None or self.repository.item(db, design_id, link.item_id) is None:
            raise NotFoundError()
        db.delete(link)
        db.commit()

    def _target_snapshot(
        self,
        db: Session,
        design_id: int,
        target_type: str,
        target_id: UUID | None,
        field: str | None,
    ) -> tuple[dict, bool]:
        """安定IDから対象の現在値と削除状態を取得する。"""
        if target_type == "design":
            if target_id is not None or field not in (None, "name", "description"):
                raise BadRequestError("コメント対象が不正です")
            design = db.get(TestDesign, design_id)
            if design is None:
                raise NotFoundError()
            value = getattr(design, field or "name")
            return {
                "label": design.name,
                "value": str(value or ""),
            }, design.deleted_at is not None
        model = TARGET_MODELS.get(target_type)
        if model is None or target_id is None:
            raise BadRequestError("コメント対象が不正です")
        row = db.scalar(
            select(model).where(model.id == target_id, model.design_id == design_id)
        )
        if row is None:
            raise NotFoundError()
        if target_type == "combination" and field and field.startswith("level:"):
            try:
                factor_id = UUID(field.split(":", 1)[1])
            except ValueError as exc:
                raise BadRequestError("対象因子が不正です") from exc
            factor = db.scalar(
                select(Factor).where(
                    Factor.id == factor_id,
                    Factor.design_id == design_id,
                    Factor.table_id == row.table_id,
                )
            )
            if factor is None:
                raise NotFoundError()
            selected = db.scalar(
                select(TestPatternValue).where(
                    TestPatternValue.pattern_id == target_id,
                    TestPatternValue.factor_id == factor_id,
                )
            )
            selected_level = (
                db.scalar(
                    select(FactorLevel).where(FactorLevel.id == selected.level_id)
                )
                if selected and selected.level_id
                else None
            )
            return {
                "label": f"{row.code} · {factor.name}",
                "value": (
                    f"{selected.level_id}:{selected_level.name}"
                    if selected and selected_level
                    else ""
                ),
            }, (
                row.deleted_at is not None
                or factor.deleted_at is not None
                or (
                    selected_level is not None and selected_level.deleted_at is not None
                )
            )
        if target_type == "combination" and field and field.startswith("expected:"):
            try:
                expected_id = UUID(field.split(":", 1)[1])
            except ValueError as exc:
                raise BadRequestError("対象期待値が不正です") from exc
            expected = db.scalar(
                select(ExpectedValue).where(
                    ExpectedValue.id == expected_id,
                    ExpectedValue.design_id == design_id,
                    ExpectedValue.table_id == row.table_id,
                )
            )
            if expected is None:
                raise NotFoundError()
            selected = db.scalar(
                select(TestPatternExpectedValue).where(
                    TestPatternExpectedValue.pattern_id == target_id,
                    TestPatternExpectedValue.expected_value_id == expected_id,
                )
            )
            return {
                "label": f"{row.code} · {expected.name}",
                "value": expected.name if selected else "",
            }, row.deleted_at is not None or expected.deleted_at is not None
        if target_type == "test_item":
            if (
                field is not None
                and field not in ITEM_FIELDS
                and not field.startswith("custom_")
            ):
                raise BadRequestError("対象フィールドが不正です")
            if field and field.startswith("custom_"):
                column = db.scalar(
                    select(TestDesignColumn).where(
                        TestDesignColumn.design_id == design_id,
                        TestDesignColumn.key == field,
                    )
                )
                if column is None:
                    raise NotFoundError()
            value = (
                row.custom_values.get(field, "")
                if field and field.startswith("custom_")
                else getattr(row, field or "content")
            )
            label = row.code
        else:
            if field not in (
                None,
                "name",
                "description",
                "notes",
                "code",
                "enabled",
            ) or (field and not hasattr(row, field)):
                raise BadRequestError("対象フィールドが不正です")
            value = getattr(
                row, field or ("code" if target_type == "combination" else "name")
            )
            label = getattr(row, "name", getattr(row, "code", ""))
        return {
            "label": str(label),
            "value": str(value or ""),
        }, row.deleted_at is not None

    def list_comment_changes(
        self, db: Session, project_id: int, design_id: int, comment_id: int
    ) -> list[TestDesignCommentChangeRead]:
        """所属する設計書のコメント変更履歴を返す。"""
        self.designs.get(db, project_id, design_id)
        if self.repository.comment(db, design_id, comment_id) is None:
            raise NotFoundError()
        return [
            TestDesignCommentChangeRead.model_validate(row)
            for row in self.repository.comment_changes(db, comment_id)
        ]

    def _comment_read(
        self, db: Session, comment: TestDesignComment, names: dict[int, str]
    ) -> TestDesignCommentRead:
        """コメント対象の現状を投稿時スナップショットと比較する。"""
        try:
            current, deleted = self._target_snapshot(
                db,
                comment.design_id,
                comment.target_type,
                comment.target_id,
                comment.field,
            )
            status = (
                "missing"
                if deleted
                else "changed"
                if current["value"] != comment.target_snapshot.get("value")
                else "current"
            )
        except NotFoundError:
            status = "missing"
        return TestDesignCommentRead(
            id=comment.id,
            design_id=comment.design_id,
            target_type=comment.target_type,
            target_id=comment.target_id,
            field=comment.field,
            target_snapshot=comment.target_snapshot,
            target_status=status,
            parent_comment_id=comment.parent_comment_id,
            body="削除されたコメント" if comment.deleted_at else comment.body,
            author_id=comment.author_id,
            author_name=names.get(comment.author_id),
            is_resolved=comment.is_resolved,
            version=comment.version,
            created_at=comment.created_at,
            updated_at=comment.updated_at,
            deleted_at=comment.deleted_at,
        )

    def list_comments(
        self, db: Session, project_id: int, design_id: int
    ) -> list[TestDesignCommentRead]:
        """設計書内のスレッドを対象削除後も返す。"""
        self.designs.get(db, project_id, design_id)
        comments = self.repository.comments(db, design_id)
        names = self.repository.user_names(db, {row.author_id for row in comments})
        return [self._comment_read(db, row, names) for row in comments]

    def create_comment(
        self,
        db: Session,
        project_id: int,
        design_id: int,
        data: TestDesignCommentCreate,
        actor_id: int,
    ) -> TestDesignCommentRead:
        """存在する対象へコメントまたは返信を投稿する。"""
        self.designs.get(db, project_id, design_id)
        snapshot, deleted = self._target_snapshot(
            db, design_id, data.target_type, data.target_id, data.field
        )
        if deleted:
            raise BadRequestError("削除済み対象には投稿できません")
        if data.parent_comment_id is not None:
            parent = self.repository.comment(db, design_id, data.parent_comment_id)
            if (
                parent is None
                or parent.deleted_at is not None
                or (parent.target_type, parent.target_id, parent.field)
                != (data.target_type, data.target_id, data.field)
            ):
                raise BadRequestError("返信先が同じ対象ではありません")
        comment = TestDesignComment(
            design_id=design_id,
            target_type=data.target_type,
            target_id=data.target_id,
            field=data.field,
            target_snapshot=snapshot,
            parent_comment_id=data.parent_comment_id,
            body=data.body,
            author_id=actor_id,
        )
        db.add(comment)
        db.flush()
        db.add(
            TestDesignCommentChange(
                comment_id=comment.id,
                actor_id=actor_id,
                action="created",
                old_value=None,
                new_value={"body": comment.body},
            )
        )
        db.commit()
        return self._comment_read(
            db, comment, self.repository.user_names(db, {actor_id})
        )

    def update_comment(
        self,
        db: Session,
        project_id: int,
        design_id: int,
        comment_id: int,
        data: TestDesignCommentUpdate,
        actor_id: int,
        can_moderate: bool,
        *,
        delete: bool = False,
    ) -> TestDesignCommentRead:
        """投稿者または管理者の編集・削除、あるいは解決状態を保存する。"""
        self.designs.get(db, project_id, design_id)
        comment = self.repository.comment(db, design_id, comment_id)
        if comment is None or comment.deleted_at is not None:
            raise NotFoundError()
        if (delete or data.body is not None) and not (
            can_moderate or comment.author_id == actor_id
        ):
            raise ForbiddenError()
        raise_if_version_conflict(
            current_version=comment.version,
            requested_version=data.version,
            current=self._comment_read(
                db, comment, self.repository.user_names(db, {comment.author_id})
            ).model_dump(mode="json"),
        )
        before = {
            "body": comment.body,
            "is_resolved": comment.is_resolved,
            "deleted_at": None,
        }
        if delete:
            comment.deleted_at = datetime.now(timezone.utc)
            action = "deleted"
        elif data.body is not None:
            comment.body = data.body
            action = "updated"
        elif data.is_resolved is not None:
            comment.is_resolved = data.is_resolved
            action = "resolved" if data.is_resolved else "reopened"
        else:
            raise BadRequestError("変更内容がありません")
        comment.version += 1
        db.add(
            TestDesignCommentChange(
                comment_id=comment.id,
                actor_id=actor_id,
                action=action,
                old_value=before,
                new_value={
                    "body": comment.body,
                    "is_resolved": comment.is_resolved,
                    "deleted_at": comment.deleted_at.isoformat()
                    if comment.deleted_at
                    else None,
                },
            )
        )
        db.commit()
        return self._comment_read(
            db, comment, self.repository.user_names(db, {comment.author_id})
        )
