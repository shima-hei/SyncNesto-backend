"""証拠提出・一ファイル進行・最終再認可。要件の最終合否は扱わない。"""

import math
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.core.exceptions import (
    AppError,
    BadRequestError,
    ConflictError,
    DuplicateResourceError,
    ForbiddenError,
    NotFoundError,
    SharedFileUnavailableError,
    TokenExpiredError,
    VersionConflictError,
)
from app.models.verification_evidence import VerificationEvidenceObject as Object
from app.models.verification_evidence import VerificationEvidenceSet as EvidenceSet
from app.repositories.requirement_agreement import RequirementAgreementRepository
from app.repositories.verification_evidence import EvidenceRepository
from app.schemas import verification_evidence as s
from app.services.artifact_release_cursor import decode_cursor, encode_cursor
from app.services.artifact_release_snapshot import digest
from app.services.audit_log import AuditLogService
from app.services.verification_evidence_access import EvidenceAccess
from app.services.verification_evidence_selection import EvidenceSelectionBuilder
from app.services.verification_evidence_storage import EvidenceStorage


def retry_db(operation):
    """Storage処理を含めず、新transactionのDB競合だけ最大3回。"""
    for attempt in range(3):
        try:
            return operation()
        except DBAPIError as error:
            code = getattr(error.orig, "pgcode", None)
            if code == "55P03":
                raise ConflictError() from None
            if code not in {"40001", "40P01"}:
                if (
                    code == "23505"
                    and getattr(
                        getattr(error.orig, "diag", None), "constraint_name", None
                    )
                    == "uq_verification_evidence_request"
                    and attempt < 2
                ):
                    continue
                raise
            if attempt == 2:
                raise ConflictError() from None
    raise AssertionError("unreachable")


class VerificationEvidenceService:
    def __init__(self):
        self.access = EvidenceAccess()
        self.repository = EvidenceRepository()
        self.builder = EvidenceSelectionBuilder()
        self.storage_factory = EvidenceStorage

    def audit(self, db, actor, row, event):
        AuditLogService().record(
            db,
            event_type="verification_evidence." + event,
            actor_user_id=actor.id,
            project_id=row.project_id,
            resource_type="verification_evidence",
            metadata={
                "set_id": str(row.id),
                "plan_id": str(row.plan_id),
                "plan_version": row.plan_version,
                "status": row.status,
            },
            commit=False,
        )

    def document(self, row):
        return any(f["kind"] == "document_attachment" for f in row.selection["files"])

    def operate(self, db, actor, row):
        self.access.require(
            db, actor, row.project_id, submit=True, document=self.document(row)
        )
        if row.created_by != actor.id and not self.access.can(
            db, actor, row.project_id, "requirement_verification:prepare"
        ):
            raise ForbiddenError()

    def can_operate(self, db, actor, row, submit=None, prepare=None):
        submit = (
            self.access.can(
                db, actor, row.project_id, "requirement_verification:submit"
            )
            if submit is None
            else submit
        )
        prepare = (
            self.access.can(
                db, actor, row.project_id, "requirement_verification:prepare"
            )
            if prepare is None
            else prepare
        )
        return submit and (row.created_by == actor.id or prepare)

    def summary(self, row, can=False, verified=0) -> dict[str, Any]:
        return dict(
            id=row.id,
            plan_version=row.plan_version,
            status=row.status,
            version=row.version,
            actor_name=row.actor_name,
            created_at=row.created_at,
            completed_at=row.completed_at,
            entry_count=len(row.selection["entries"]),
            file_count=len(row.selection["files"]),
            verified_files=len(row.selection["files"])
            if row.status == "ready"
            else verified,
            safe_error=row.safe_error,
            can_operate=can,
        )

    def preview(self, original, user, project_id, plan_id, data):
        def operation():
            with self.access.transaction(
                original, user, project_id, selection=data
            ) as (db, actor):
                self.access.require(
                    db,
                    actor,
                    project_id,
                    submit=True,
                    document=any(
                        f.kind == "document_attachment"
                        for e in data.entries
                        for f in e.files
                    ),
                )
                return s.EvidencePreview(
                    **self.repository.bounded(
                        self.builder.preview(
                            self.builder.build(db, project_id, plan_id, data)
                        )
                    )
                )

        return retry_db(operation)

    def page(self, original, user, project_id, plan_id, version, cursor, page_size):
        with self.access.transaction(original, user, project_id) as (db, actor):
            if not self.access.enabled():
                return s.EvidencePage(enabled=False, state="disabled")
            if not all(
                self.access.can(db, actor, project_id, code)
                for code in (
                    "requirement:read",
                    "test_plan:read",
                    "test_case:read",
                    "requirement_verification:read",
                )
            ):
                return s.EvidencePage(enabled=True, state="restricted")
            self.builder.plans.plan(db, project_id, plan_id)
            document = self.access.can(db, actor, project_id, "document:read")
            submit = self.access.can(
                db, actor, project_id, "requirement_verification:submit"
            )
            prepare = self.access.can(
                db, actor, project_id, "requirement_verification:prepare"
            )
            scope = uuid5(
                NAMESPACE_URL,
                f"evidence-list:{plan_id}:{version}:{actor.id}:{document}",
            )
            offset = decode_cursor(cursor, project_id, scope) if cursor else 0
            rows = self.repository.page(
                db, project_id, plan_id, version, offset, page_size, document
            )
            selected = (
                {
                    identity: count
                    for identity, count in db.execute(
                        select(Object.set_id, func.count())
                        .where(
                            Object.set_id.in_([r.id for r in rows]),
                            Object.selected.is_(True),
                        )
                        .group_by(Object.set_id)
                    ).all()
                }
                if rows
                else {}
            )
            result = dict(
                enabled=True,
                state="ready",
                can_submit=submit,
                document_access_limited=not document,
                items=[
                    self.summary(
                        r,
                        self.can_operate(db, actor, r, submit, prepare),
                        selected.get(r.id, 0),
                    )
                    for r in rows[:page_size]
                ],
                next_cursor=encode_cursor(project_id, scope, offset + page_size)
                if len(rows) > page_size
                else None,
            )
            return s.EvidencePage(**self.repository.bounded(result))

    def seal(self, db, row):
        objects = {
            o.slot: o
            for o in self.repository.objects(db, row.id)
            if o.selected and o.state == "verified"
        }
        if len(objects) != len(row.selection["files"]):
            return
        files = [
            dict(
                f,
                object_id=str(objects[f["slot"]].id),
                destination_version=objects[f["slot"]].destination_version,
                sha256=objects[f["slot"]].sha256,
                verified_at=objects[f["slot"]].verified_at.isoformat(),
            )
            for f in row.selection["files"]
        ]
        now = db.scalar(select(func.clock_timestamp()))
        row.manifest = dict(row.selection, files=files)
        row.manifest_digest = digest(row.manifest)
        row.status = "ready"
        row.completed_at = now
        row.lease_id = row.lease_expires_at = None
        row.safe_error = None

    def create(self, original, user, project_id, plan_id, data):
        chosen = s.EvidenceSelection(
            plan_version=data.plan_version, entries=data.entries
        )
        request_digest = digest(data.model_dump(mode="json"))

        def operation():
            with self.access.transaction(
                original,
                user,
                project_id,
                write=True,
                selection=chosen,
                plan_id=plan_id,
            ) as (db, actor):
                self.access.require(
                    db,
                    actor,
                    project_id,
                    submit=True,
                    document=any(
                        f.kind == "document_attachment"
                        for e in data.entries
                        for f in e.files
                    ),
                )
                db.execute(
                    text("SELECT pg_advisory_xact_lock(1398362935,:project)"),
                    {"project": project_id},
                )
                old = db.scalar(
                    select(EvidenceSet).where(
                        EvidenceSet.project_id == project_id,
                        EvidenceSet.plan_id == plan_id,
                        EvidenceSet.created_by == actor.id,
                        EvidenceSet.request_id == data.request_id,
                    )
                )
                if old:
                    if old.request_digest != request_digest:
                        raise DuplicateResourceError()
                    self.access.require(
                        db, actor, project_id, document=self.document(old)
                    )
                    return s.EvidenceSummary(
                        **self.summary(old, self.can_operate(db, actor, old))
                    ), False
                unfinished = list(
                    db.scalars(
                        select(EvidenceSet.created_by).where(
                            EvidenceSet.project_id == project_id,
                            EvidenceSet.status.in_(["preparing", "failed"]),
                        )
                    )
                )
                if len(unfinished) >= 4 or unfinished.count(actor.id) >= 2:
                    raise ConflictError()
                snapshot = self.builder.build(db, project_id, plan_id, chosen)
                if digest(snapshot) != data.expected_preview_digest:
                    raise VersionConflictError({"preview_required": True})
                row = EvidenceSet(
                    id=uuid4(),
                    project_id=project_id,
                    plan_id=plan_id,
                    plan_version=data.plan_version,
                    schema_version=1,
                    subject_digest=snapshot["subject_digest"],
                    checklist_digest=snapshot["checklist_digest"],
                    created_by=actor.id,
                    actor_name=actor.name,
                    request_id=data.request_id,
                    request_digest=request_digest,
                    preview_digest=digest(snapshot),
                    selection=snapshot,
                    status="preparing",
                    version=1,
                )
                db.add(row)
                db.flush()
                if not snapshot["files"]:
                    self.seal(db, row)
                    row.version += 1
                    db.flush()
                self.audit(db, actor, row, "created")
                return s.EvidenceSummary(
                    **self.summary(row, row.status != "ready")
                ), True

        return retry_db(operation)

    def comparison(self, db, row):
        plan = self.builder.plans.plan(db, row.project_id, row.plan_id)
        subjects = RequirementAgreementRepository().subjects(
            db, row.project_id, [plan.source_requirement_id]
        )
        current = subjects[0]["subject"] if subjects else None
        requirement = (
            "unavailable"
            if current is None
            else "same"
            if digest(current) == row.subject_digest
            else "changed"
        )
        try:
            latest = self.builder.build(
                db,
                row.project_id,
                row.plan_id,
                self.builder.input_from_snapshot(row.selection),
            )
            execution = "same" if digest(latest) == row.preview_digest else "changed"
        except (BadRequestError, ConflictError, NotFoundError):
            execution = "unavailable_or_changed"
        except AppError:
            execution = "not_evaluated"
        return dict(
            requirement=requirement,
            checklist="same" if plan.current_version == row.plan_version else "changed",
            sources=execution,
            ready_means="preserved_only",
        )

    def detail(self, original, user, project_id, plan_id, identity, cursor, page_size):
        with self.access.transaction(original, user, project_id) as (db, actor):
            self.access.require(db, actor, project_id)
            row = self.repository.get(db, project_id, plan_id, identity)
            self.access.require(db, actor, project_id, document=self.document(row))
            objects = self.repository.objects(db, row.id)
            scope = uuid5(NAMESPACE_URL, f"evidence-entries:{identity}:{actor.id}")
            offset = decode_cursor(cursor, project_id, scope) if cursor else 0
            entries = row.selection["entries"]
            files = row.manifest["files"] if row.manifest else row.selection["files"]
            result = dict(
                **self.summary(
                    row,
                    self.can_operate(db, actor, row),
                    sum(o.selected for o in objects),
                ),
                subject_digest=row.subject_digest,
                checklist_digest=row.checklist_digest,
                manifest_digest=row.manifest_digest,
                comparison=self.comparison(db, row),
                entries=[
                    dict(
                        id=e["id"],
                        row_id=e["row_id"],
                        row_description=e["row_description"],
                        kind=e["kind"],
                        use=e["use"],
                        result=e.get("execution", {}).get("status", e["result"]),
                    )
                    for e in entries[offset : offset + page_size]
                ],
                files=[
                    dict(self.builder.public_file(f), file_id=f.get("object_id"))
                    for f in files
                ],
                next_cursor=encode_cursor(project_id, scope, offset + page_size)
                if len(entries) > offset + page_size
                else None,
            )
            return s.EvidenceDetail(**self.repository.bounded(result))

    def entry(self, original, user, project_id, plan_id, identity, entry_id):
        with self.access.transaction(original, user, project_id) as (db, actor):
            self.access.require(db, actor, project_id)
            row = self.repository.get(db, project_id, plan_id, identity)
            self.access.require(db, actor, project_id, document=self.document(row))
            entry = next(
                (e for e in row.selection["entries"] if e["id"] == str(entry_id)), None
            )
            if entry is None:
                raise NotFoundError()
            result = dict(
                id=entry_id,
                plan_version=row.plan_version,
                actor_name=row.actor_name,
                received_at=row.created_at,
                entry=entry,
                comparison=self.comparison(db, row),
            )
            return s.EvidenceEntryDetail(**self.repository.bounded(result, 1310720))

    def read_selection(self, original, user, project_id, plan_id, identity):
        with self.access.transaction(original, user, project_id) as (db, actor):
            self.access.require(db, actor, project_id)
            row = self.repository.get(db, project_id, plan_id, identity)
            self.operate(db, actor, row)
            return self.builder.input_from_snapshot(row.selection)

    def claim(self, original, user, project_id, plan_id, identity, data, selection):
        def operation():
            with self.access.transaction(
                original,
                user,
                project_id,
                write=True,
                selection=selection,
                plan_id=plan_id,
            ) as (db, actor):
                row = self.repository.get(db, project_id, plan_id, identity, lock=True)
                self.operate(db, actor, row)
                if row.version != data.version:
                    raise VersionConflictError({"version": row.version})
                now = db.scalar(select(func.clock_timestamp()))
                assert now is not None
                if row.status not in {"preparing", "failed"} or (
                    row.lease_expires_at and row.lease_expires_at > now
                ):
                    raise ConflictError()
                if (
                    digest(self.builder.build(db, project_id, plan_id, selection))
                    != row.preview_digest
                ):
                    raise VersionConflictError({"preview_required": True})
                objects = self.repository.objects(db, row.id)
                selected_slots = {o.slot for o in objects if o.selected}
                for o in objects:
                    if not o.selected and o.state == "reserved":
                        o.state = "abandoned"
                file = next(
                    (
                        f
                        for f in row.selection["files"]
                        if f["slot"] not in selected_slots
                    ),
                    None,
                )
                if file is None:
                    raise ConflictError()
                row.status, row.safe_error = "preparing", None
                row.lease_id = uuid4()
                row.lease_expires_at = now + timedelta(seconds=90)
                row.version += 1
                db.flush()
                obj = Object(
                    id=uuid4(),
                    set_id=row.id,
                    slot=file["slot"],
                    attempt_id=row.lease_id,
                    storage_key="verification-evidence-v1/" + uuid4().hex,
                    state="reserved",
                    selected=False,
                )
                db.add(obj)
                db.flush()
                self.audit(db, actor, row, "copy_reserved")
                return dict(
                    object_id=obj.id,
                    key=obj.storage_key,
                    lease=row.lease_id,
                    file=dict(file),
                )

        return retry_db(operation)

    def finish(
        self, original, user, project_id, plan_id, identity, selection, claim, copied
    ):
        def operation():
            with self.access.transaction(
                original,
                user,
                project_id,
                write=True,
                selection=selection,
                plan_id=plan_id,
            ) as (db, actor):
                row = self.repository.get(db, project_id, plan_id, identity, lock=True)
                self.operate(db, actor, row)
                obj = db.get(Object, claim["object_id"])
                now = db.scalar(select(func.clock_timestamp()))
                assert now is not None
                if obj is None:
                    raise NotFoundError()
                if (
                    row.status != "preparing"
                    or row.lease_id != claim["lease"]
                    or row.lease_expires_at is None
                    or row.lease_expires_at <= now
                ):
                    raise ConflictError()
                valid = copied is not None
                if valid:
                    try:
                        valid = (
                            digest(
                                self.builder.build(db, project_id, plan_id, selection)
                            )
                            == row.preview_digest
                        )
                    except AppError:
                        valid = False
                if valid:
                    for name, value in copied.items():
                        setattr(obj, name, value)
                    obj.state, obj.selected, obj.verified_at = "verified", True, now
                    db.flush()
                    self.seal(db, row)
                    if row.status != "ready":
                        row.lease_id = row.lease_expires_at = None
                else:
                    obj.state, obj.selected = "abandoned", False
                    row.status = "failed"
                    row.safe_error = (
                        "copy_failed" if copied is None else "source_changed"
                    )
                    row.lease_id = row.lease_expires_at = None
                row.version += 1
                db.flush()
                self.audit(
                    db,
                    actor,
                    row,
                    "preserved" if row.status == "ready" else "copy_finished",
                )
                return s.EvidenceSummary(
                    **self.summary(
                        row,
                        self.can_operate(db, actor, row),
                        sum(o.selected for o in self.repository.objects(db, row.id)),
                    )
                )

        return retry_db(operation)

    def advance(self, original, user, project_id, plan_id, identity, data):
        selection = self.read_selection(original, user, project_id, plan_id, identity)
        claim = self.claim(
            original, user, project_id, plan_id, identity, data, selection
        )
        try:
            copied = self.storage_factory().copy(claim["file"], claim["key"])
        except (SharedFileUnavailableError, OSError):
            copied = None
        return self.finish(
            original, user, project_id, plan_id, identity, selection, claim, copied
        )

    def cancel(self, original, user, project_id, plan_id, identity, data):
        def operation():
            with self.access.transaction(original, user, project_id, write=True) as (
                db,
                actor,
            ):
                row = self.repository.get(db, project_id, plan_id, identity, lock=True)
                self.operate(db, actor, row)
                if row.version != data.version:
                    raise VersionConflictError({"version": row.version})
                if row.status not in {"preparing", "failed"}:
                    raise ConflictError()
                for obj in self.repository.objects(db, row.id):
                    obj.state, obj.selected = "abandoned", False
                db.flush()
                row.status, row.lease_id, row.lease_expires_at, row.safe_error = (
                    "cancelled",
                    None,
                    None,
                    None,
                )
                row.version += 1
                self.audit(db, actor, row, "cancelled")
                return s.EvidenceSummary(**self.summary(row, True))

        return retry_db(operation)

    def cleanup(self, original, user, project_id, plan_id, identity, data):
        def reserve():
            with self.access.transaction(original, user, project_id, write=True) as (
                db,
                actor,
            ):
                row = self.repository.get(db, project_id, plan_id, identity, lock=True)
                self.operate(db, actor, row)
                if row.version != data.version:
                    raise VersionConflictError({"version": row.version})
                now = db.scalar(select(func.clock_timestamp()))
                assert now is not None
                if row.lease_expires_at and row.lease_expires_at > now:
                    raise ConflictError()
                objects = list(
                    db.scalars(
                        select(Object)
                        .where(Object.set_id == row.id, Object.selected.is_(False))
                        .order_by(
                            Object.cleanup_checked_at.asc().nulls_first(), Object.id
                        )
                        .limit(2)
                    )
                )
                for obj in objects:
                    obj.state = "abandoned"
                db.flush()
                return [(o.id, o.storage_key) for o in objects]

        identities = retry_db(reserve)
        storage = self.storage_factory()
        outcomes = []
        for obj_id, key in identities:
            try:
                outcomes.append((obj_id, storage.cleanup(key)))
            except (BotoCoreError, ClientError, OSError):
                outcomes.append((obj_id, False))

        def finish():
            with self.access.transaction(original, user, project_id, write=True) as (
                db,
                actor,
            ):
                row = self.repository.get(db, project_id, plan_id, identity, lock=True)
                self.operate(db, actor, row)
                for obj_id, success in outcomes:
                    obj = db.get(Object, obj_id)
                    if obj and not obj.selected and obj.state == "abandoned":
                        obj.cleanup_checked_at = db.scalar(
                            select(func.clock_timestamp())
                        )
                        obj.cleanup_error = None if success else "retry_required"
                self.audit(db, actor, row, "cleanup_checked")
                return s.EvidenceSummary(
                    **self.summary(
                        row,
                        self.can_operate(db, actor, row),
                        sum(o.selected for o in self.repository.objects(db, row.id)),
                    )
                )

        return retry_db(finish)

    def download(self, original, user, project_id, plan_id, identity, file_id):
        def resolve(write=False) -> Any:
            with self.access.transaction(original, user, project_id, write=write) as (
                db,
                actor,
            ):
                self.access.require(db, actor, project_id)
                row = self.repository.get(db, project_id, plan_id, identity)
                self.access.require(db, actor, project_id, document=self.document(row))
                if row.status != "ready" or digest(row.manifest) != row.manifest_digest:
                    raise ConflictError()
                assert row.manifest is not None
                file = next(
                    (
                        f
                        for f in row.manifest["files"]
                        if f["object_id"] == str(file_id)
                    ),
                    None,
                )
                obj = db.get(Object, file_id)
                if (
                    file is None
                    or obj is None
                    or obj.set_id != row.id
                    or not obj.selected
                    or obj.state != "verified"
                    or obj.sha256 != file["sha256"]
                    or obj.destination_version != file["destination_version"]
                    or obj.byte_size != file["byte_size"]
                ):
                    raise NotFoundError()
                state = dict(
                    key=obj.storage_key,
                    version=obj.destination_version,
                    sha=obj.sha256,
                    size=obj.byte_size,
                    filename=file["filename"],
                )
                if not write:
                    return state
                if state != source:
                    raise ConflictError()
                exp = original.info.get("auth_token_exp")
                if (
                    not isinstance(exp, (int, float))
                    or isinstance(exp, bool)
                    or not math.isfinite(exp)
                ):
                    raise TokenExpiredError()
                session = db.info["auth_session"]
                deadline = min(
                    session.expires_at,
                    session.absolute_expires_at,
                    datetime.fromtimestamp(exp, UTC),
                )
                now = db.scalar(select(func.clock_timestamp()))
                assert now is not None
                if deadline <= now:
                    raise TokenExpiredError()
                result = storage.sign(
                    state["key"], state["version"], state["filename"], deadline, now
                )
                self.audit(db, actor, row, "download_issued")
                return s.EvidenceDownload.model_validate(result)

        source = retry_db(resolve)
        storage = self.storage_factory()
        storage.verify(source["key"], source["version"], source["sha"], source["size"])
        return retry_db(lambda: resolve(True))
