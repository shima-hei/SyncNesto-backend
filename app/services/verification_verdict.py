"""指定本人の判断と、保存結果とは別の現在有効性。"""

import time
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import Text, cast, func, select, text
from sqlalchemy.exc import DBAPIError

from app.core import error_messages as messages
from app.core.exceptions import (
    AppError,
    BadRequestError,
    ConflictError,
    DuplicateResourceError,
    ForbiddenError,
    SharedFileUnavailableError,
    VersionConflictError,
)
from app.models.verification_verdict import VerificationEvent as Event
from app.models.verification_verdict import VerificationRequest as Request
from app.repositories.verification_verdict import VerdictRepository
from app.schemas import verification_verdict as s
from app.services.artifact_release_cursor import decode_cursor, encode_cursor
from app.services.artifact_release_snapshot import digest
from app.services.audit_log import AuditLogService
from app.services.verification_evidence_storage import EvidenceStorage
from app.services.verification_verdict_access import VerdictAccess
from app.services.verification_verdict_basis import (
    VerdictBasis,
    public_entry,
    public_file,
)


def retry(operation):
    """DB処理だけ再試行する。Storage IOはこの関数の外。"""
    for attempt in range(3):
        try:
            return operation()
        except DBAPIError as error:
            code = getattr(error.orig, "pgcode", None)
            if code in {"40001", "40P01"} and attempt < 2:
                continue
            if code in {"40001", "40P01", "23505", "23514", "55P03"}:
                raise ConflictError() from None
            raise
    raise AssertionError("unreachable")


class VerificationVerdictService:
    def __init__(self):
        self.access = VerdictAccess()
        self.repo = VerdictRepository()
        self.builder = VerdictBasis()
        self.storage_factory = EvidenceStorage

    @staticmethod
    def document(basis):
        return any(f["kind"] == "document_attachment" for f in basis["files"])

    def allowed(self, db, actor, row, **kwargs):
        self.access.require(
            db, actor, row.project_id, document=self.document(row.basis), **kwargs
        )

    def judge(self, db, actor, row):
        self.allowed(db, actor, row, decide=True)
        if actor.id != row.judge_id:
            raise ForbiddenError()
        current = self.access.qualified(
            db, row.project_id, user_id=actor.id, document=self.document(row.basis)
        )[0]
        if {k: v for k, v in current.items() if k != "name"} != {
            k: v for k, v in row.basis["judge_qualification"].items() if k != "name"
        }:
            raise ConflictError(messages.VERDICT_JUDGE_CHANGED)

    @staticmethod
    def summary(row):
        return s.VerdictSummary(
            **{k: getattr(row, k) for k in s.VerdictSummary.model_fields}
        )

    def event(self, db, actor, row, operation_id, operation_digest, reason):
        db.flush()
        db.add(
            Event(
                id=uuid4(),
                request_id=row.id,
                version=row.version,
                event=row.status,
                actor_id=actor.id,
                actor_name=actor.name,
                reason=reason,
                operation_id=operation_id,
                operation_digest=operation_digest,
                snapshot=dict(
                    basis_digest=row.basis_digest,
                    result=row.result,
                    decision=row.decision_snapshot,
                ),
            )
        )
        AuditLogService().record(
            db,
            event_type="requirement_verdict." + row.status,
            actor_user_id=actor.id,
            project_id=row.project_id,
            resource_type="requirement_verification_request",
            metadata={
                "request_id": str(row.id),
                "version": row.version,
                "result": row.result,
            },
            commit=False,
        )
        db.flush()

    def prepared(self, db, actor, project_id, data):
        self.access.require(db, actor, project_id, prepare=True)
        plan, basis, comparison = self.builder.build(db, project_id, data)
        document = self.document(basis)
        self.access.require(db, actor, project_id, document=document)
        judge = self.access.qualified(
            db, project_id, user_id=data.judge_id, document=document
        )[0]
        basis["judge_qualification"] = judge
        self.repo.bounded(basis, 1900000)
        return plan, basis, comparison, judge

    @staticmethod
    def preview_value(data, basis):
        return digest(
            dict(
                selection=data.model_dump(
                    mode="json", exclude={"operation_id", "expected_preview_digest"}
                ),
                basis=basis,
            )
        )

    def preview(self, original, user, project_id, data):
        def operation():
            with self.access.transaction(original, user, project_id) as (db, actor):
                plan, basis, comparison, judge = self.prepared(
                    db, actor, project_id, data
                )
                return s.VerdictPreview(
                    preview_digest=self.preview_value(data, basis),
                    plan_id=plan.id,
                    plan_version=data.plan_version,
                    source_requirement_id=plan.source_requirement_id,
                    subject_digest=plan.subject_digest,
                    checklist_digest=basis["checklist_digest"],
                    judge_id=judge["id"],
                    judge_name=judge["name"],
                    readiness=basis["readiness"],
                    comparison=comparison,
                    entries=[public_entry(e) for e in basis["entries"]],
                    file_count=len(basis["files"]),
                )

        return retry(operation)

    def candidates(self, original, user, project_id):
        with self.access.transaction(original, user, project_id) as (db, actor):
            self.access.require(db, actor, project_id, prepare=True)
            rows = self.access.qualified(db, project_id)
            if len(rows) > 1000:
                from app.core.exceptions import ArtifactReleaseTooLargeError

                raise ArtifactReleaseTooLargeError()
            return s.VerdictCandidates(
                items=[
                    s.VerdictCandidate(
                        id=r["id"], name=r["name"], is_self=r["id"] == actor.id
                    )
                    for r in rows
                ]
            )

    def create(self, original, user, project_id, data):
        operation_digest = digest(data.model_dump(mode="json"))

        def operation():
            with self.access.transaction(
                original, user, project_id, write=True, plan_id=data.plan_id
            ) as (db, actor):
                self.access.require(db, actor, project_id, prepare=True)
                db.execute(
                    text("SELECT pg_advisory_xact_lock(1398362936,:project)"),
                    {"project": project_id},
                )
                previous = db.scalar(
                    select(Request).where(
                        Request.project_id == project_id,
                        Request.requested_by == actor.id,
                        Request.request_id == data.operation_id,
                    )
                )
                if previous:
                    self.allowed(db, actor, previous)
                    if previous.request_digest != operation_digest:
                        raise DuplicateResourceError()
                    return self.summary(previous), False
                self.access.lock_judge(db, project_id, data.judge_id)
                plan, basis, _, judge = self.prepared(db, actor, project_id, data)
                if data.expected_preview_digest != self.preview_value(data, basis):
                    raise VersionConflictError({"preview_required": True})
                chain = list(
                    db.scalars(
                        select(Request)
                        .where(
                            Request.project_id == project_id,
                            Request.source_requirement_id == plan.source_requirement_id,
                        )
                        .with_for_update()
                    )
                )
                successors = {
                    r.previous_request_id for r in chain if r.previous_request_id
                }
                terminal = [r for r in chain if r.id not in successors]
                if chain:
                    if (
                        len(terminal) != 1
                        or terminal[0].id != data.previous_request_id
                        or terminal[0].status == "requested"
                        or not data.reason.strip()
                    ):
                        raise ConflictError(messages.VERDICT_CHAIN_REQUIRED)
                    actual = (
                        terminal[0].id
                        if terminal[0].result
                        else terminal[0].reviewed_decision_id
                    )
                    if data.reviewed_decision_id != actual:
                        raise ConflictError(messages.VERDICT_CHAIN_REQUIRED)
                elif data.previous_request_id or data.reviewed_decision_id:
                    raise BadRequestError(messages.VERDICT_CHAIN_REQUIRED)
                row = Request(
                    id=uuid4(),
                    project_id=project_id,
                    source_requirement_id=plan.source_requirement_id,
                    plan_id=plan.id,
                    plan_version=data.plan_version,
                    subject_digest=plan.subject_digest,
                    checklist_digest=basis["checklist_digest"],
                    basis=basis,
                    basis_digest=digest(basis),
                    requested_by=actor.id,
                    requester_name=actor.name,
                    judge_id=judge["id"],
                    judge_name=judge["name"],
                    previous_request_id=data.previous_request_id,
                    reviewed_decision_id=data.reviewed_decision_id,
                    reason=data.reason,
                    request_id=data.operation_id,
                    request_digest=operation_digest,
                    status="requested",
                    version=1,
                    review_version=0,
                )
                db.add(row)
                self.event(
                    db, actor, row, data.operation_id, operation_digest, data.reason
                )
                db.refresh(row)
                return self.summary(row), True

        return retry(operation)

    @staticmethod
    def validity(row, comparison):
        if row.status == "cancelled":
            return "cancelled"
        if row.status == "withdrawn":
            return "withdrawn"
        if any(
            comparison.get(k) in {"not_evaluated", "unavailable"}
            for k in ("requirement", "checklist", "sources", "adverse", "files_source")
        ):
            return "not_evaluated"
        if (
            any(
                comparison.get(k) != "same"
                for k in ("requirement", "checklist", "sources")
            )
            or comparison.get("adverse") == "found"
        ):
            return "requires_reassessment"
        return "pending" if row.status == "requested" else row.result

    def detail_value(self, db, actor, row):
        self.allowed(db, actor, row)
        comparison = self.builder.compare_many(db, row.project_id, [row])[str(row.id)]
        comparison.pop("observed", None)
        can_judge = False
        qualification = "current"
        if row.judge_id == actor.id:
            try:
                self.judge(db, actor, row)
                can_judge = True
            except (ForbiddenError, ConflictError):
                qualification = "changed"
        comparison["judge_qualification"] = qualification
        review = row.review or {}
        expires = (
            datetime.fromisoformat(review["expires_at"])
            if review.get("expires_at")
            else None
        )
        current_review = (
            can_judge
            and review.get("session_id") == str(db.info["auth_session"].id)
            and expires is not None
            and expires > datetime.now(UTC)
        )
        plan = self.builder.plans.plan(db, row.project_id, row.plan_id)
        subject = self.builder.plans.fixed(
            db,
            row.project_id,
            plan.release_id,
            plan.target_node_id,
        )
        return s.VerdictDetail(
            **self.summary(row).model_dump(),
            fixed_subject=subject,
            checklist=row.basis["checklist"],
            basis_digest=row.basis_digest,
            reason=row.reason,
            readiness=row.basis["readiness"],
            comparison=comparison,
            validity=self.validity(row, comparison),
            entries=[public_entry(e) for e in row.basis["entries"]],
            files=[public_file(f) for f in row.basis["files"]],
            review_version=row.review_version,
            review_expires_at=expires if current_review else None,
            verified_files=len(review.get("verified", {})) if current_review else 0,
            review_error=review.get("error") if current_review else None,
            decision_reason=row.decision_reason,
            can_review=can_judge and row.status == "requested",
            can_cancel=row.status == "requested"
            and self.access.can(
                db, actor, row.project_id, "requirement_verification:prepare"
            ),
            can_withdraw=can_judge and row.status == "decided",
        )

    def detail(self, original, user, project_id, identity):
        with self.access.transaction(original, user, project_id) as (db, actor):
            self.access.require(db, actor, project_id)
            return self.detail_value(db, actor, self.repo.get(db, project_id, identity))

    def page(self, original, user, project_id, requirement_id, cursor, page_size):
        with self.access.transaction(original, user, project_id) as (db, actor):
            if not self.access.enabled():
                return s.VerdictPage(enabled=False, state="disabled")
            try:
                self.access.require(db, actor, project_id)
            except ForbiddenError:
                return s.VerdictPage(enabled=True, state="restricted")
            scope = uuid5(
                NAMESPACE_URL, f"verdict-page:{actor.id}:{project_id}:{requirement_id}"
            )
            offset = decode_cursor(cursor, project_id, scope) if cursor else 0
            rows = self.repo.pages(db, project_id, requirement_id, offset, page_size)
            document = self.access.can(db, actor, project_id, "document:read")
            return s.VerdictPage(
                enabled=True,
                state="ready",
                can_prepare=self.access.can(
                    db, actor, project_id, "requirement_verification:prepare"
                ),
                items=[
                    self.summary(r)
                    for r in rows[:page_size]
                    if document or not self.document(r.basis)
                ],
                next_cursor=encode_cursor(project_id, scope, offset + page_size)
                if len(rows) > page_size
                else None,
            )

    def events(self, original, user, project_id, identity, cursor, page_size):
        with self.access.transaction(original, user, project_id) as (db, actor):
            row = self.repo.get(db, project_id, identity)
            self.allowed(db, actor, row)
            scope = uuid5(NAMESPACE_URL, f"verdict-events:{actor.id}:{identity}")
            after = decode_cursor(cursor, project_id, scope) if cursor else 0
            rows = list(
                db.scalars(
                    select(Event)
                    .where(Event.request_id == identity, Event.version > after)
                    .order_by(Event.version)
                    .limit(page_size + 1)
                )
            )
            items = [
                s.VerdictEventRead(
                    **{k: getattr(r, k) for k in s.VerdictEventRead.model_fields}
                )
                for r in rows[:page_size]
            ]
            self.repo.bounded([r.model_dump(mode="json") for r in items], 8 * 1048576)
            return s.VerdictEvents(
                items=items,
                next_cursor=encode_cursor(
                    project_id, scope, rows[page_size - 1].version
                )
                if len(rows) > page_size
                else None,
            )

    def _review_valid(self, db, row, data):
        review = row.review or {}
        now = db.scalar(select(func.clock_timestamp()))
        if (
            row.status != "requested"
            or row.version != data.version
            or row.review_version != data.review_version
            or review.get("session_id") != str(db.info["auth_session"].id)
            or review.get("actor_id") != row.judge_id
            or review.get("basis_digest") != row.basis_digest
            or not review.get("expires_at")
            or datetime.fromisoformat(review["expires_at"]) <= now
        ):
            raise ConflictError(messages.VERDICT_REVIEW_EXPIRED)
        return review, now

    def review(self, original, user, project_id, identity, data):
        def operation():
            with self.access.transaction(original, user, project_id, write=True) as (
                db,
                actor,
            ):
                row = self.repo.get(db, project_id, identity, lock=True)
                self.judge(db, actor, row)
                if (
                    row.version != data.version
                    or row.review_version != data.review_version
                    or row.status != "requested"
                ):
                    raise VersionConflictError(
                        {"version": row.version, "review_version": row.review_version}
                    )
                now = db.execute(select(func.clock_timestamp())).scalar_one()
                if row.lease_id and row.lease_expires_at and row.lease_expires_at > now:
                    raise ConflictError()
                old = row.review or {}
                reusable = (
                    old.get("session_id") == str(db.info["auth_session"].id)
                    and old.get("expires_at")
                    and datetime.fromisoformat(old["expires_at"]) > now
                )
                if not reusable:
                    row.review = dict(
                        actor_id=actor.id,
                        session_id=str(db.info["auth_session"].id),
                        version=row.version,
                        basis_digest=row.basis_digest,
                        expires_at=(now + timedelta(minutes=15)).isoformat(),
                        verified={},
                        error=None,
                    )
                    row.review_version += 1
                    row.lease_id = row.lease_expires_at = None
                    db.flush()
                return self.detail_value(db, actor, row)

        return retry(operation)

    def _claim(self, original, user, project_id, identity, data, *, decision=False):
        def operation():
            with self.access.transaction(original, user, project_id, write=True) as (
                db,
                actor,
            ):
                row = self.repo.get(db, project_id, identity, lock=True)
                self.judge(db, actor, row)
                review, now = self._review_valid(db, row, data)
                if row.lease_id and row.lease_expires_at > now:
                    raise ConflictError()
                remaining = [
                    f
                    for f in row.basis["files"]
                    if f["object_id"] not in review["verified"]
                ]
                if decision and remaining:
                    raise ConflictError(messages.VERDICT_NOT_READY)
                if not decision and not remaining:
                    return None
                row.lease_id, row.lease_expires_at = (
                    uuid4(),
                    now + timedelta(seconds=90),
                )
                row.review_version += 1
                db.flush()
                return dict(
                    id=row.id,
                    plan_id=row.plan_id,
                    version=row.version,
                    review_version=row.review_version,
                    lease=row.lease_id,
                    basis_digest=row.basis_digest,
                    files=row.basis["files"] if decision else remaining[:1],
                )

        return retry(operation)

    def _finish_claim(self, original, user, project_id, claim, callback):
        def operation():
            with self.access.transaction(
                original,
                user,
                project_id,
                write=True,
                plan_id=claim["plan_id"],
                selection=claim,
            ) as (db, actor):
                row = self.repo.get(db, project_id, claim["id"], lock=True)
                self.judge(db, actor, row)
                data = s.VerdictReview(
                    version=claim["version"], review_version=claim["review_version"]
                )
                review, now = self._review_valid(db, row, data)
                if (
                    row.lease_id != claim["lease"]
                    or row.lease_expires_at <= now
                    or row.basis_digest != claim["basis_digest"]
                ):
                    raise ConflictError(messages.VERDICT_REVIEW_EXPIRED)
                return callback(db, actor, row, review, now)

        try:
            return retry(operation)
        except AppError:
            # 確定直前の競合・不足では、採用せず自分のleaseだけを解放する。
            # 認可自体が失効した場合は再書込せず90秒の期限に任せる。
            try:
                with self.access.transaction(
                    original, user, project_id, write=True
                ) as (db, actor):
                    row = self.repo.get(db, project_id, claim["id"], lock=True)
                    self.judge(db, actor, row)
                    if row.status == "requested" and row.lease_id == claim["lease"]:
                        row.lease_id = row.lease_expires_at = None
                        row.review_version += 1
                        db.flush()
            except (AppError, DBAPIError):
                pass
            raise

    def advance(self, original, user, project_id, identity, data):
        claim = self._claim(original, user, project_id, identity, data)
        if claim is None:
            return self.detail(original, user, project_id, identity)
        file = claim["files"][0]
        error = None
        checked = datetime.now(UTC).isoformat()
        try:
            self.storage_factory().verify(
                file["preserved_key"],
                file["destination_version"],
                file["sha256"],
                file["byte_size"],
                deadline=time.monotonic() + 25,
            )
        except (SharedFileUnavailableError, BotoCoreError, ClientError, OSError):
            error = "file_unavailable"

        def finish(db, actor, row, review, now):
            comparison = self.builder.compare_many(db, project_id, [row])[str(row.id)]
            if any(
                comparison.get(k) != "same" for k in ("checklist", "sources", "runs")
            ):
                raise ConflictError(messages.VERDICT_BASIS_CHANGED)
            verified = dict(review["verified"])
            if error is None:
                verified[file["object_id"]] = dict(
                    checked_at=checked,
                    version=file["destination_version"],
                    sha256=file["sha256"],
                )
            row.review = dict(review, verified=verified, error=error)
            row.review_version += 1
            row.lease_id = row.lease_expires_at = None
            db.flush()
            return self.detail_value(db, actor, row)

        return self._finish_claim(original, user, project_id, claim, finish)

    def _prior_operation(self, db, row, operation_id, operation_digest):
        previous = db.scalar(
            select(Event).where(
                Event.request_id == row.id, Event.operation_id == operation_id
            )
        )
        if previous:
            if previous.operation_digest != operation_digest:
                raise DuplicateResourceError()
            return True
        return False

    def decide(self, original, user, project_id, identity, data):
        operation_digest = digest(data.model_dump(mode="json"))
        with self.access.transaction(original, user, project_id) as (db, actor):
            row = self.repo.get(db, project_id, identity)
            self.judge(db, actor, row)
            if self._prior_operation(db, row, data.operation_id, operation_digest):
                return self.detail_value(db, actor, row)
            plan_id = row.plan_id

        def save(db, actor, row, review, now):
            if not data.reason.strip():
                raise BadRequestError(messages.VERDICT_NOT_READY)
            comparison = self.builder.compare_many(db, project_id, [row])[str(row.id)]
            readiness = row.basis["readiness"]
            row_ids = {r["id"] for r in row.basis["checklist"]["rows"]}
            if data.result == "failed":
                if not data.unresolved_rows or not {
                    str(r) for r in data.unresolved_rows
                }.issubset(row_ids):
                    raise BadRequestError(messages.VERDICT_NOT_READY)
            else:
                key = (
                    "passed_eligible"
                    if data.result == "passed"
                    else "not_applicable_eligible"
                )
                if (
                    not readiness[key]
                    or any(
                        comparison.get(k) != "same"
                        for k in ("checklist", "sources", "runs", "files_source")
                    )
                    or comparison.get("adverse") != "none"
                ):
                    raise ConflictError(messages.VERDICT_NOT_READY)
                if comparison.get("requirement") not in {"same", "changed"} or (
                    comparison["requirement"] == "changed"
                    and not data.acknowledge_requirement_difference
                ):
                    raise ConflictError(messages.VERDICT_BASIS_CHANGED)
                if set(readiness["not_applicable_rows"]) != {
                    str(r) for r in data.confirmed_not_applicable_rows
                }:
                    raise BadRequestError(messages.VERDICT_NOT_READY)
            row.result, row.decided_by, row.decision_reason, row.decided_at = (
                data.result,
                actor.id,
                data.reason,
                now,
            )
            observed = comparison.pop("observed", [])
            row.decision_snapshot = dict(
                readiness=readiness,
                comparison=comparison,
                observed=observed,
                file_proofs=review.get("verified", {}),
                file_error=review.get("error"),
                files_last_checked_at=now.isoformat()
                if data.result != "failed"
                else None,
                confirmed_not_applicable_rows=[
                    str(r) for r in data.confirmed_not_applicable_rows
                ],
                unresolved_rows=[str(r) for r in data.unresolved_rows],
                requirement_difference_acknowledged=data.acknowledge_requirement_difference,
            )
            self.repo.bounded(row.decision_snapshot, 1900000)
            row.status, row.version = "decided", row.version + 1
            row.lease_id = row.lease_expires_at = None
            self.event(db, actor, row, data.operation_id, operation_digest, data.reason)
            return self.detail_value(db, actor, row)

        if data.result == "failed":

            def negative():
                with self.access.transaction(
                    original, user, project_id, write=True, plan_id=plan_id
                ) as (db, actor):
                    row = self.repo.get(db, project_id, identity, lock=True)
                    self.judge(db, actor, row)
                    if self._prior_operation(
                        db, row, data.operation_id, operation_digest
                    ):
                        return self.detail_value(db, actor, row)
                    review, now = self._review_valid(db, row, data)
                    if row.lease_id and row.lease_expires_at > now:
                        raise ConflictError()
                    return save(db, actor, row, review, now)

            return retry(negative)
        claim = self._claim(original, user, project_id, identity, data, decision=True)
        assert claim is not None
        storage = self.storage_factory() if claim["files"] else None
        deadline = time.monotonic() + 25
        try:
            for file in claim["files"]:
                assert storage is not None
                if time.monotonic() >= deadline:
                    raise SharedFileUnavailableError()
                response = storage.destination.head_object(
                    Bucket=storage.bucket,
                    Key=file["preserved_key"],
                    VersionId=file["destination_version"],
                )
                if (
                    response.get("VersionId") != file["destination_version"]
                    or response.get("ContentLength") != file["byte_size"]
                ):
                    raise SharedFileUnavailableError()
        except (SharedFileUnavailableError, BotoCoreError, ClientError, OSError):

            def unavailable(db, actor, row, review, now):
                row.review = dict(review, verified={}, error="file_unavailable")
                row.review_version += 1
                row.lease_id = row.lease_expires_at = None
                db.flush()
                return None

            self._finish_claim(original, user, project_id, claim, unavailable)
            raise SharedFileUnavailableError() from None
        return self._finish_claim(original, user, project_id, claim, save)

    def transition(self, original, user, project_id, identity, data, *, withdraw=False):
        operation_digest = digest(
            dict(
                data.model_dump(mode="json"),
                action="withdraw" if withdraw else "cancel",
            )
        )

        def operation():
            with self.access.transaction(original, user, project_id, write=True) as (
                db,
                actor,
            ):
                row = self.repo.get(db, project_id, identity, lock=True)
                if withdraw:
                    self.judge(db, actor, row)
                else:
                    self.allowed(db, actor, row, prepare=True)
                if self._prior_operation(db, row, data.operation_id, operation_digest):
                    return self.detail_value(db, actor, row)
                if row.version != data.version or row.status != (
                    "decided" if withdraw else "requested"
                ):
                    raise VersionConflictError(
                        {"version": row.version, "status": row.status}
                    )
                if not data.reason.strip():
                    raise BadRequestError()
                row.status = "withdrawn" if withdraw else "cancelled"
                row.version += 1
                row.lease_id = row.lease_expires_at = None
                self.event(
                    db, actor, row, data.operation_id, operation_digest, data.reason
                )
                return self.detail_value(db, actor, row)

        return retry(operation)

    def verdicts(self, original, user, project_id, ids):
        with self.access.transaction(original, user, project_id) as (db, actor):
            if not self.access.enabled():
                return s.RequirementVerdicts(enabled=False, state="disabled")
            try:
                self.access.require(db, actor, project_id)
            except ForbiddenError:
                return s.RequirementVerdicts(enabled=True, state="restricted")
            # 過去全basisを読まず、明示系列の終端だけを取得する。
            child = Request.__table__.alias("successor")
            query = (
                select(Request)
                .where(
                    Request.project_id == project_id,
                    Request.source_requirement_id.in_(ids),
                    ~select(child.c.id)
                    .where(child.c.previous_request_id == Request.id)
                    .exists(),
                )
                .limit(101)
            )
            sizes = list(
                db.scalars(
                    query.with_only_columns(
                        func.octet_length(cast(Request.basis, Text))
                        + func.coalesce(
                            func.octet_length(cast(Request.decision_snapshot, Text)), 0
                        )
                    )
                )
            )
            if len(sizes) > 100 or sum(sizes) > 8 * 1048576:
                return s.RequirementVerdicts(
                    enabled=True,
                    state="ready",
                    items=[
                        s.RequirementVerdict(requirement_id=i, state="not_evaluated")
                        for i in ids
                    ],
                )
            rows = list(db.scalars(query))
            self.repo.bounded([r.basis for r in rows], 8 * 1048576)
            document = self.access.can(db, actor, project_id, "document:read")
            allowed = [r for r in rows if document or not self.document(r.basis)]
            comparisons = self.builder.compare_many(db, project_id, allowed)
            items = []
            for identity in ids:
                matches = [r for r in rows if r.source_requirement_id == identity]
                if len(matches) != 1:
                    items.append(
                        s.RequirementVerdict(
                            requirement_id=identity,
                            state="conflict" if matches else "not_judged",
                        )
                    )
                    continue
                row = matches[0]
                if row not in allowed:
                    items.append(
                        s.RequirementVerdict(
                            requirement_id=identity, state="restricted"
                        )
                    )
                    continue
                comparison = comparisons[str(row.id)]
                comparison.pop("observed", None)
                items.append(
                    s.RequirementVerdict(
                        requirement_id=identity,
                        state=self.validity(row, comparison),
                        result=row.result,
                        request_id=row.id,
                        plan_version=row.plan_version,
                        subject_digest=row.subject_digest,
                        comparison=comparison,
                        files_last_checked_at=(row.decision_snapshot or {}).get(
                            "files_last_checked_at"
                        ),
                        files_rechecked=False,
                    )
                )
            return s.RequirementVerdicts(enabled=True, state="ready", items=items)
