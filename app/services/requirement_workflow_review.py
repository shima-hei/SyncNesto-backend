"""依頼の固定内容に対する確認・指摘・回答の業務処理。"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    VersionConflictError,
)
from app.models.requirement_workflow import (
    RequirementReviewThread,
    RequirementReviewThreadEvent,
    RequirementWorkflowParticipant,
    RequirementWorkflowRequest,
)
from app.models.user import User
from app.repositories.requirement_review import RequirementReviewRepository
from app.schemas.requirement_workflow import (
    RequirementReviewConfirmationInput,
    RequirementReviewItemState,
    RequirementReviewProgress,
    RequirementReviewSubmitInput,
    RequirementReviewThreadActionInput,
    RequirementReviewThreadCreate,
    RequirementReviewThreadDetail,
    RequirementReviewThreadEventRead,
    RequirementReviewThreadList,
    RequirementReviewThreadRead,
    ReviewThreadAction,
)
from app.services.audit_log import AuditLogService
from app.services.requirement_workflow import RequirementWorkflowService

ANCHOR_FIELDS = {
    "requirement_document": {
        "title",
        "purpose",
        "project_goal",
        "project_scope",
        "project_completion_criteria",
    },
    "section": {"title", "content"},
    "requirement": {"title", "description", "rationale", "acceptance_criteria"},
}


def matches_fixed_quote(source, anchor):
    """固定本文のUnicodeコードポイント位置を原文のまま確認する。"""
    return (
        isinstance(source, str)
        and source[anchor.quote_start : anchor.quote_start + len(anchor.quote)]
        == anchor.quote
    )


class RequirementWorkflowReviewService:
    """固定版のレビューを、発行者・担当者と現在の権限に従って扱う。"""

    def __init__(self, workflow: RequirementWorkflowService) -> None:
        """依頼・版の認可を再利用する。"""
        self.workflow = workflow
        self.repository = RequirementReviewRepository()

    @contextmanager
    def _request(
        self,
        db: Session,
        user: User,
        project_id: int,
        request_id: UUID,
        *,
        lock: bool = False,
    ) -> Iterator[tuple[Session, User, RequirementWorkflowRequest]]:
        """案件、固定範囲、取引時点を検証し、変更時は依頼を行ロックする。"""
        try:
            with self.workflow.releases._transaction(db, user, project_id, None) as (
                work,
                actor,
            ):
                row = self.workflow.readable_content_request(
                    work, actor, project_id, request_id, lock=lock
                )
                if row.kind != "review":
                    raise NotFoundError()
                yield work, actor, row
        except DBAPIError as exc:
            if getattr(exc.orig, "pgcode", None) in {"40001", "40P01"}:
                raise ConflictError(
                    error_messages.REQUIREMENT_REVIEW_CONCURRENT
                ) from None
            raise

    def _editable_participant(
        self,
        db: Session,
        actor: User,
        row: RequirementWorkflowRequest,
        version: int,
    ) -> RequirementWorkflowParticipant:
        """依頼担当の本人だけが未提出時に結果を変更できる。"""
        participant = self.repository.participant(db, row.id, actor.id)
        if participant is None:
            raise ForbiddenError()
        if row.version != version:
            raise VersionConflictError({"version": row.version})
        if row.status != "requested" or participant.status != "pending":
            raise ConflictError(error_messages.REQUIREMENT_REVIEW_CLOSED)
        return participant

    def _progress(
        self,
        db: Session,
        actor: User,
        row: RequirementWorkflowRequest,
        ids: list[UUID],
    ) -> RequirementReviewProgress:
        """選択中の要件と全体の進捗をまとめて返す。"""
        participant = self.repository.participant(db, row.id, actor.id)
        total, confirmed, blocked = self.repository.counts(db, row, actor.id)
        return RequirementReviewProgress.model_validate(
            {
                "version": row.version,
                "can_respond": row.status == "requested"
                and participant is not None
                and participant.status == "pending",
                "can_discuss": self._can_discuss(db, actor, row),
                "participant_status": participant.status if participant else None,
                "requirement_count": total,
                "confirmed_count": confirmed,
                "changes_requested_count": blocked,
                "open_correction_count": self.repository.correction_count(db, row.id),
                "items": [
                    RequirementReviewItemState(
                        node_id=node_id,
                        status="changes_requested"
                        if needs_revision
                        else ("confirmed" if checked else "pending"),
                    )
                    for node_id, checked, needs_revision in self.repository.flags(
                        db, row, actor.id, ids
                    )
                ],
            }
        )

    def progress(
        self,
        db: Session,
        user: User,
        project_id: int,
        request_id: UUID,
        ids: list[UUID],
    ):
        """本人のレビュー確認状況を取得する。"""
        with self._request(db, user, project_id, request_id) as (work, actor, row):
            return self._progress(work, actor, row, ids)

    def set_confirmation(
        self,
        db: Session,
        user: User,
        project_id: int,
        request_id: UUID,
        node_id: UUID,
        data: RequirementReviewConfirmationInput,
    ) -> RequirementReviewProgress:
        """固定要件の確認・取消を保存する。"""
        with self._request(db, user, project_id, request_id, lock=True) as (
            work,
            actor,
            row,
        ):
            return self._set_confirmation(work, actor, row, node_id, data)

    def _set_confirmation(self, work, actor, row, node_id, data):
        """認可済みの固定範囲に対する本人確認を保存する。"""
        self._editable_participant(work, actor, row, data.version)
        flags = self.repository.flags(work, row, actor.id, [node_id])
        if not flags:
            raise NotFoundError()
        _, was_confirmed, blocked = flags[0]
        if data.confirmed and blocked:
            raise ConflictError(error_messages.REQUIREMENT_REVIEW_BLOCKED)
        if was_confirmed != data.confirmed:
            self.repository.set_confirmation(
                work, row, actor.id, node_id, data.confirmed
            )
            row.version += 1
            work.flush()
        return self._progress(work, actor, row, [node_id])

    def confirm_remaining(
        self, db: Session, user: User, project_id: int, request_id: UUID, version: int
    ):
        """未確認で修正待ちでない要件をまとめて確認済みにする。"""
        with self._request(db, user, project_id, request_id, lock=True) as (
            work,
            actor,
            row,
        ):
            self._editable_participant(work, actor, row, version)
            total, confirmed, blocked = self.repository.counts(work, row, actor.id)
            if total > confirmed + blocked:
                self.repository.confirm_remaining(work, row, actor.id)
                row.version += 1
                work.flush()
            return self._progress(work, actor, row, [])

    def submit(
        self,
        db: Session,
        user: User,
        project_id: int,
        request_id: UUID,
        data: RequirementReviewSubmitInput,
    ):
        """本人の回答を提出し、全担当者の提出時に依頼の結果を確定する。"""
        with self._request(db, user, project_id, request_id, lock=True) as (
            work,
            actor,
            row,
        ):
            self._submit(work, actor, row, data, project_id)
            return self.workflow._reads(work, [row])[0]

    def _submit(self, work, actor, row, data, project_id, *, tenant_id=None):
        """認可済みの担当者の提出と依頼全体の集計を確定する。"""
        participant = self._editable_participant(work, actor, row, data.version)
        total, confirmed, blocked = self.repository.counts(work, row, actor.id)
        if total > confirmed + blocked:
            if not data.confirm_remaining:
                raise ConflictError(error_messages.REQUIREMENT_REVIEW_INCOMPLETE)
            self.repository.confirm_remaining(work, row, actor.id)
        open_corrections = self.repository.correction_count(work, row.id)
        responded_at = datetime.now(UTC)
        participant.status = "changes_requested" if open_corrections else "confirmed"
        participant.response_note = data.note or None
        participant.responded_at = responded_at
        row.version += 1
        work.flush()

        states = self.repository.participant_statuses(work, row.id)
        if "pending" not in states:
            row.status = (
                "changes_requested" if "changes_requested" in states else "completed"
            )
            row.finalized_at = responded_at
        AuditLogService().record(
            work,
            event_type="requirement_workflow.review_submitted",
            actor_user_id=actor.id,
            tenant_id=tenant_id,
            project_id=project_id,
            resource_type="requirement_workflow",
            metadata={"request_id": str(row.id), "result": participant.status},
            commit=False,
        )
        work.flush()
        return participant

    def _can_manage(self, db: Session, actor: User, project_id: int) -> bool:
        """案件のレビューを管理できる既存権限かを確認する。"""
        authorization = self.workflow.releases.authorization
        return all(
            authorization.has_project_permission(
                db, user=actor, project_id=project_id, permission_code=code
            )
            for code in ("requirement:review", "requirement:update")
        )

    def _can_discuss(
        self, db: Session, actor: User, row: RequirementWorkflowRequest
    ) -> bool:
        """担当者・発行者・管理者だけが依頼内で会話できる。"""
        return row.status != "cancelled" and (
            actor.id == row.requester_id
            or self.repository.participant(db, row.id, actor.id) is not None
            or self._can_manage(db, actor, row.project_id)
        )

    def add_thread(
        self,
        db: Session,
        user: User,
        project_id: int,
        request_id: UUID,
        data: RequirementReviewThreadCreate,
    ) -> RequirementReviewThreadRead:
        """同じ依頼に含まれる本文のみにコメント・修正指摘を登録する。"""
        with self._request(db, user, project_id, request_id, lock=True) as (
            work,
            actor,
            row,
        ):
            if not self._can_discuss(work, actor, row):
                raise ForbiddenError()
            return self._store_thread(work, actor, row, data)

    def _store_thread(self, work, actor, row, data):
        """認可済み依頼と対象の照合を行い、本文に指摘を登録する。"""
        if row.version != data.version:
            raise VersionConflictError({"version": row.version})
        if data.kind == "correction" and row.status != "requested":
            raise ConflictError(error_messages.REQUIREMENT_REVIEW_CLOSED)
        node = self.repository.node(work, row, data.node_id)
        if node is None:
            raise NotFoundError()
        if data.anchor is not None:
            original = node.payload.get(data.anchor.field)
            if data.anchor.field not in ANCHOR_FIELDS[
                node.node_kind
            ] or not matches_fixed_quote(original, data.anchor):
                raise BadRequestError(error_messages.REQUIREMENT_REVIEW_INVALID_ANCHOR)
        thread = RequirementReviewThread(
            request_id=row.id,
            node_id=node.id,
            author_id=actor.id,
            assignee_id=row.requester_id if data.kind == "correction" else None,
            kind=data.kind,
            body=data.body,
            **(data.anchor.model_dump() if data.anchor is not None else {}),
        )
        work.add(thread)
        row.version += 1
        work.flush()
        if data.kind == "correction":
            self.repository.clear_blocked_pending_confirmations(work, row)
        return RequirementReviewThreadRead.model_validate(thread)

    def _available_actions(
        self,
        db: Session,
        actor: User,
        row: RequirementWorkflowRequest,
        thread: RequirementReviewThread,
    ) -> list[ReviewThreadAction]:
        """修正側・確認側・管理権限ごとの有効な状態遷移を返す。"""
        if not self._can_discuss(db, actor, row):
            return []
        actions: list[ReviewThreadAction] = ["reply"]
        author = actor.id == thread.author_id
        if thread.kind == "comment":
            if author:
                actions.append("resolve" if thread.status == "open" else "reopen")
            return actions
        manager = self._can_manage(db, actor, row.project_id)
        if thread.status == "open" and (actor.id == thread.assignee_id or manager):
            actions.append("start")
        if thread.status == "in_progress" and (
            actor.id == thread.assignee_id or manager
        ):
            actions.append("respond")
        if thread.status == "awaiting_confirmation" and author:
            actions.extend(["reopen", "resolve"])
        if thread.status == "resolved" and author:
            actions.append("reopen")
        if thread.status != "resolved" and manager:
            actions.append("force_resolve")
        return actions

    def _thread_detail(
        self,
        db: Session,
        actor: User,
        row: RequirementWorkflowRequest,
        thread: RequirementReviewThread,
        page: int,
        page_size: int,
    ) -> RequirementReviewThreadDetail:
        """指摘の履歴と、本人が利用できる操作を同じ応答で返す。"""
        events, total = self.repository.events(db, thread.id, page, page_size)
        return RequirementReviewThreadDetail(
            thread=RequirementReviewThreadRead.model_validate(thread),
            events=[
                RequirementReviewThreadEventRead.model_validate(event)
                for event in events
            ],
            event_count=total,
            available_actions=self._available_actions(db, actor, row, thread),
        )

    def thread(
        self,
        db: Session,
        user: User,
        project_id: int,
        request_id: UUID,
        thread_id: UUID,
        page: int,
        page_size: int,
    ) -> RequirementReviewThreadDetail:
        """同じ依頼に属する指摘の詳細と返信を取得する。"""
        with self._request(db, user, project_id, request_id) as (work, actor, row):
            thread = self.repository.thread(work, row.id, thread_id)
            if thread is None:
                raise NotFoundError()
            return self._thread_detail(work, actor, row, thread, page, page_size)

    def threads(
        self,
        db: Session,
        user: User,
        project_id: int,
        request_id: UUID,
        page: int,
        page_size: int,
        active_only: bool,
        node_id: UUID | None,
    ) -> RequirementReviewThreadList:
        """依頼のレビュー指摘・コメントをまとめて取得する。"""
        with self._request(db, user, project_id, request_id) as (work, _, row):
            if node_id is not None and self.repository.node(work, row, node_id) is None:
                raise NotFoundError()
            items, total = self.repository.threads(
                work, row.id, page, page_size, active_only=active_only, node_id=node_id
            )
            return RequirementReviewThreadList(
                items=[
                    RequirementReviewThreadRead.model_validate(item) for item in items
                ],
                total=total,
                page=page,
                page_size=page_size,
            )

    def act_on_thread(
        self,
        db: Session,
        user: User,
        project_id: int,
        request_id: UUID,
        thread_id: UUID,
        data: RequirementReviewThreadActionInput,
    ) -> RequirementReviewThreadRead:
        """本人による応答・再確認・例外クローズを理由付きで記録する。"""
        with self._request(db, user, project_id, request_id, lock=True) as (
            work,
            actor,
            row,
        ):
            thread = self.repository.thread(work, row.id, thread_id)
            if thread is None:
                raise NotFoundError()
            return self._act_on_thread(work, actor, row, thread, data)

    def _act_on_thread(self, work, actor, row, thread, data):
        """認可済みの指摘に対する状態遷移を保存する。"""
        if data.action not in self._available_actions(work, actor, row, thread):
            raise ForbiddenError()
        if thread.version != data.version:
            raise VersionConflictError(
                {"version": thread.version, "status": thread.status}
            )
        next_status = {
            "start": "in_progress",
            "respond": "awaiting_confirmation",
            "reopen": "open" if thread.kind == "comment" else "in_progress",
            "resolve": "resolved",
            "force_resolve": "resolved",
        }.get(data.action)
        if next_status is not None:
            thread.status = next_status
        thread.version += 1
        row.version += 1
        work.add(
            RequirementReviewThreadEvent(
                thread_id=thread.id,
                actor_id=actor.id,
                action=data.action,
                body=data.body or None,
            )
        )
        work.flush()
        if data.action == "reopen" and thread.kind == "correction":
            self.repository.clear_blocked_pending_confirmations(work, row)
        return RequirementReviewThreadRead.model_validate(thread)
