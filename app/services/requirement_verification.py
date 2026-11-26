"""確認表の準備だけを保存する。承認・実行結果・最終合否は更新しない。"""

import json
from types import SimpleNamespace
from typing import cast
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from app.core import error_messages
from app.core.exceptions import (
    ArtifactReleaseTooLargeError,
    BadRequestError,
    ConflictError,
    DuplicateResourceError,
    NotFoundError,
    VersionConflictError,
)
from app.models.artifact_release import ArtifactRelease, ArtifactReleaseNode
from app.models.requirement import Requirement
from app.models.requirement_verification import RequirementVerificationPlan as Plan
from app.models.requirement_verification import (
    RequirementVerificationRevision as Revision,
)
from app.models.requirement_workflow import RequirementWorkflowRequest
from app.repositories.requirement_agreement import RequirementAgreementRepository
from app.repositories.requirement_verification import RequirementVerificationRepository
from app.repositories.requirement_workflow import RequirementWorkflowRepository
from app.repositories.verification_candidates import VerificationCandidateRepository
from app.schemas import requirement_verification as s
from app.schemas import test_design as ts
from app.schemas.artifact_release import (
    ArtifactCreateRequest,
    ArtifactPreviewRequest,
    ArtifactSource,
)
from app.services.artifact_release import SOURCE_PERMISSIONS, ArtifactReleaseService
from app.services.artifact_release_cursor import decode_cursor, encode_cursor
from app.services.artifact_release_snapshot import canonical_bytes, digest
from app.services.audit_log import AuditLogService
from app.services.test_design_cases import case_sources, source_without_item_code

READS = (
    "project:read",
    "requirement:read",
    "test_plan:read",
    "test_case:read",
    "requirement_verification:read",
)
MATERIAL_SCHEMAS = dict(
    items=ts.TestItemInput,
    patterns=ts.TestPatternInput,
    pattern_tables=ts.PatternTableInput,
    factors=ts.FactorInput,
    levels=ts.FactorLevelInput,
    values=ts.TestPatternValueInput,
    expected_values=ts.ExpectedValueInput,
    expected_selections=ts.TestPatternExpectedValueInput,
    links=ts.TestItemPatternInput,
    columns=ts.TestDesignColumnInput,
)


class RequirementVerificationService:
    def __init__(self):
        self.access = ArtifactReleaseService()
        self.repository = RequirementVerificationRepository()
        self.candidates = VerificationCandidateRepository()

    def can(self, db, user, project_id, permission):
        return self.access.authorization.has_project_permission(
            db, user=user, project_id=project_id, permission_code=permission
        )

    def allowed(self, db, user, project_id, *, prepare=False):
        for permission in READS + (
            ("requirement_verification:prepare",) if prepare else ()
        ):
            self.access._permission(db, user, project_id, permission)

    def scope(self, project_id, *parts):
        return uuid5(
            NAMESPACE_URL,
            "verification-v1:" + str(project_id) + ":" + ":".join(map(str, parts)),
        )

    def page(self, original, user, project_id, requirement_id, cursor, page_size):
        with self.access._transaction(original, user, project_id, None) as (db, actor):
            for code in ("project:read", "requirement_verification:read"):
                self.access._permission(db, actor, project_id, code)
            if not all(self.can(db, actor, project_id, code) for code in READS):
                return s.VerificationList(enabled=True, state="restricted")
            scope = self.scope(project_id, "list", requirement_id, actor.id)
            offset = decode_cursor(cursor, project_id, scope) if cursor else 0
            rows = self.repository.summaries(
                db, project_id, requirement_id, offset, page_size
            )
            return s.VerificationList(
                enabled=True,
                state="ready",
                can_prepare=self.can(
                    db, actor, project_id, "requirement_verification:prepare"
                ),
                items=[
                    s.VerificationSummary(**self.summary(row, title))
                    for row, title in rows[:page_size]
                ],
                next_cursor=encode_cursor(project_id, scope, offset + page_size)
                if len(rows) > page_size
                else None,
            )

    def summary(self, row, title):
        return dict(
            id=row.id,
            source_requirement_id=row.source_requirement_id,
            title=title or "名称未設定",
            current_version=row.current_version,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def detail(self, db, actor, project_id, row, *, saved_version=None):
        subject = self.repository.fixed(
            db, project_id, row.release_id, row.target_node_id
        )
        current = RequirementAgreementRepository().subjects(
            db, project_id, [row.source_requirement_id]
        )
        comparison = "unavailable"
        if current and current[0]["subject"] is not None:
            comparison = (
                "same"
                if digest(current[0]["subject"]) == row.subject_digest
                else "changed"
            )
        version = saved_version or row.current_version
        approved_version = db.scalar(
            select(RequirementWorkflowRequest.display_number)
            .where(
                RequirementWorkflowRequest.project_id == project_id,
                RequirementWorkflowRequest.release_id == row.release_id,
                RequirementWorkflowRequest.kind == "formal_approval",
                RequirementWorkflowRequest.status == "approved",
            )
            .order_by(RequirementWorkflowRequest.display_number.desc())
            .limit(1)
        )
        return s.VerificationDetail(
            **{
                **self.summary(row, subject["payload"].get("title")),
                "current_version": version,
            },
            subject_digest=row.subject_digest,
            target_version_label=f"正式承認済み A-{approved_version:03d}"
            if approved_version is not None
            else None,
            fixed_subject=subject,
            comparison=comparison,
            can_prepare=self.can(
                db, actor, project_id, "requirement_verification:prepare"
            ),
            revision=s.VerificationRevisionRead(
                **self.repository.revision(db, row.id, version)
            ),
        )

    def get(self, original, user, project_id, plan_id):
        with self.access._transaction(original, user, project_id, None) as (db, actor):
            self.allowed(db, actor, project_id)
            return self.detail(
                db, actor, project_id, self.repository.plan(db, project_id, plan_id)
            )

    def retry(self, operation):
        for attempt in range(3):
            try:
                return operation()
            except DBAPIError as exc:
                code = getattr(exc.orig, "pgcode", None)
                constraint = getattr(
                    getattr(exc.orig, "diag", None), "constraint_name", None
                )
                if code not in {"40001", "40P01"} and not (
                    code == "23505"
                    and constraint
                    in {
                        "uq_verification_plan_request",
                        "uq_verification_revision_operation",
                        "uq_artifact_release_request",
                    }
                ):
                    raise
                if attempt == 2:
                    raise ConflictError(error_messages.VERIFICATION_RETRY) from None
        raise AssertionError("unreachable")

    def audit(self, db, actor, project_id, row, event):
        AuditLogService().record(
            db,
            event_type="requirement_verification." + event,
            actor_user_id=actor.id,
            project_id=project_id,
            resource_type="requirement_verification",
            metadata={"plan_id": str(row.id), "version": row.current_version},
            commit=False,
        )

    def preparation_targets(self, db, actor, project_id, requirement_id):
        subjects = RequirementAgreementRepository().subjects(
            db, project_id, [requirement_id]
        )
        if not subjects:
            raise NotFoundError()
        current = subjects[0]["subject"]
        if current is None:
            raise ArtifactReleaseTooLargeError()

        def target(subject, kind, label, approval_id=None):
            payload = subject["payload"]
            return s.VerificationPreparationTarget(
                kind=kind,
                version=subject["source_version"],
                label=label,
                title=payload.get("title") or "名称未設定",
                description=payload.get("description") or "",
                acceptance_criteria=payload.get("acceptance_criteria") or "",
                subject_digest=digest(subject),
                approval_id=approval_id,
            )

        results: list[
            tuple[
                s.VerificationPreparationTarget,
                ArtifactRelease | None,
                ArtifactReleaseNode | None,
            ]
        ] = [(target(current, "current", "現在の保存内容"), None, None)]
        document_id = db.scalar(
            select(Requirement.document_id).where(Requirement.id == requirement_id)
        )
        baseline = RequirementWorkflowRepository().approval_for_document(
            db, project_id, document_id, "approved"
        )
        if baseline is None:
            return results
        release = self.access.repository.get(db, project_id, baseline.release_id)
        if release is None or not all(
            self.can(db, actor, project_id, f"{SOURCE_PERMISSIONS[kind]}:read")
            for kind in release.source_kinds
        ):
            return results
        node = db.scalar(
            select(ArtifactReleaseNode).where(
                ArtifactReleaseNode.release_id == release.id,
                ArtifactReleaseNode.node_kind == "requirement",
                ArtifactReleaseNode.source_id == requirement_id,
            )
        )
        if node is None:
            return results
        fixed = self.repository.fixed(db, project_id, release.id, node.id)
        results.insert(
            0,
            (
                target(
                    fixed,
                    "approved",
                    f"正式承認済み A-{baseline.display_number:03d}",
                    baseline.id,
                ),
                release,
                node,
            ),
        )
        return results

    def require_start_permissions(self, db, actor, project_id):
        self.allowed(db, actor, project_id, prepare=True)
        for code in (
            "artifact_release:create",
            "artifact_release:read",
            "requirement:update",
        ):
            self.access._permission(db, actor, project_id, code)

    def preparation(self, original, user, project_id, requirement_id):
        with self.access._transaction(original, user, project_id, None) as (db, actor):
            self.require_start_permissions(db, actor, project_id)
            options = self.preparation_targets(db, actor, project_id, requirement_id)
            return s.VerificationPreparation(
                targets=[option[0] for option in options],
                test_items=[
                    s.VerificationTargetOption(
                        item_id=row.id, code=row.code, title=row.title or "名称未設定"
                    )
                    for row in self.candidates.linked(db, project_id, requirement_id)
                ],
            )

    def start(self, original, user, project_id, data):
        request_digest = digest(data.model_dump(mode="json"))

        def save():
            with self.access._transaction(original, user, project_id, None) as (
                db,
                actor,
            ):
                self.require_start_permissions(db, actor, project_id)
                old = self.previous_plan(
                    db, actor, project_id, data.request_id, request_digest
                )
                if old:
                    return self.detail(db, actor, project_id, old), False
                choices = self.preparation_targets(
                    db, actor, project_id, data.source_requirement_id
                )
                selected = next(
                    (
                        option
                        for option in choices
                        if option[0].approval_id == data.approval_id
                    ),
                    None,
                )
                if (
                    not selected
                    or selected[0].subject_digest != data.expected_subject_digest
                ):
                    raise VersionConflictError(
                        {
                            "subject_digest": selected[0].subject_digest
                            if selected
                            else None
                        }
                    )
                target, release, node = selected
                if release is None:
                    source = ArtifactPreviewRequest(
                        source=ArtifactSource(
                            kind="requirement",
                            id=data.source_requirement_id,
                            version=target.version,
                        )
                    )
                    snapshot = self.access.capture_snapshot(
                        db, actor, project_id, source
                    )
                    release = self.access.save_snapshot(
                        db,
                        actor,
                        project_id,
                        ArtifactCreateRequest(
                            **source.model_dump(),
                            request_id=uuid5(data.request_id, "verification-target"),
                            expected_scope_digest=snapshot.scope_digest,
                        ),
                        snapshot,
                    )
                    node = db.scalar(
                        select(ArtifactReleaseNode).where(
                            ArtifactReleaseNode.release_id == release.id,
                            ArtifactReleaseNode.node_kind == "requirement",
                            ArtifactReleaseNode.source_id == data.source_requirement_id,
                        )
                    )
                assert node is not None
                subject = self.repository.fixed(db, project_id, release.id, node.id)
                if digest(subject) != target.subject_digest:
                    raise VersionConflictError({"subject_digest": digest(subject)})
                row = self.save_initial_plan(
                    db,
                    actor,
                    project_id,
                    data.source_requirement_id,
                    release.id,
                    node.id,
                    subject,
                    data.request_id,
                    request_digest,
                    data.checklist,
                )
                return self.detail(db, actor, project_id, row), True

        return self.retry(save)

    def previous_plan(self, db, actor, project_id, request_id, request_digest):
        old = db.scalar(
            select(Plan).where(
                Plan.project_id == project_id,
                Plan.created_by == actor.id,
                Plan.request_id == request_id,
            )
        )
        if old and old.request_digest != request_digest:
            raise DuplicateResourceError()
        return old

    def save_initial_plan(
        self,
        db,
        actor,
        project_id,
        requirement_id,
        release_id,
        node_id,
        subject,
        request_id,
        request_digest,
        prepared_checklist=None,
    ):
        row = Plan(
            id=uuid4(),
            project_id=project_id,
            source_requirement_id=requirement_id,
            release_id=release_id,
            target_node_id=node_id,
            subject_digest=digest(subject),
            current_version=1,
            created_by=actor.id,
            updated_by=actor.id,
            request_id=request_id,
            request_digest=request_digest,
        )
        checklist = (
            self.validate(
                db,
                project_id,
                row,
                subject,
                SimpleNamespace(checklist=prepared_checklist, confirm_coverage=True),
            )
            if prepared_checklist is not None
            else {"rows": [], "coverage": None}
        )
        self.stamp_coverage(db, actor, checklist)
        row = self.repository.insert_header(db, row)
        db.add(
            Revision(
                plan_id=row.id,
                version=1,
                checklist=checklist,
                checklist_digest=digest(checklist),
                actor_id=actor.id,
                operation_id=request_id,
                operation_digest=request_digest,
            )
        )
        self.audit(db, actor, project_id, row, "created")
        db.flush()
        db.refresh(row)
        return row

    def create(self, original, user, project_id, data):
        request_digest = digest(data.model_dump(mode="json"))

        def save():
            with self.access._transaction(original, user, project_id, None) as (
                db,
                actor,
            ):
                self.require_start_permissions(db, actor, project_id)
                old = self.previous_plan(
                    db, actor, project_id, data.request_id, request_digest
                )
                if old:
                    return self.detail(db, actor, project_id, old), False
                release = db.scalar(
                    select(ArtifactRelease).where(
                        ArtifactRelease.id == data.release_id,
                        ArtifactRelease.project_id == project_id,
                        ArtifactRelease.sealed.is_(True),
                    )
                )
                if release is None:
                    raise NotFoundError()
                if release.content_digest != data.expected_release_digest:
                    raise VersionConflictError(
                        {"content_digest": release.content_digest}
                    )
                subject = self.repository.fixed(
                    db, project_id, release.id, data.target_node_id
                )
                if subject["source_id"] != data.source_requirement_id:
                    raise BadRequestError(error_messages.VERIFICATION_TARGET_INVALID)
                row = self.save_initial_plan(
                    db,
                    actor,
                    project_id,
                    data.source_requirement_id,
                    release.id,
                    data.target_node_id,
                    subject,
                    data.request_id,
                    request_digest,
                )
                result = self.detail(db, actor, project_id, row)
            return result, True

        return self.retry(save)

    def validate(self, db, project_id, row, subject, data):
        checklist = data.checklist.model_dump(mode="json")
        # PostgreSQL jsonb::textの空白分も見込み、DB上限を超える前に413にする。
        if len(json.dumps(checklist, ensure_ascii=False).encode()) > 256 * 1024:
            raise ArtifactReleaseTooLargeError()
        linked = {
            r["id"]: r
            for r in self.candidates.linked(db, project_id, row.source_requirement_id)
        }
        complete = bool(data.checklist.rows)
        for condition in data.checklist.rows:
            ref = condition.reference
            valid_reference = ref.field == "whole"
            if ref.field == "whole" and (ref.start is not None or ref.text):
                raise BadRequestError(error_messages.VERIFICATION_QUOTE_INVALID)
            if ref.field != "whole" and ref.text:
                text = subject["payload"].get(ref.field) or ""
                if (
                    ref.start is None
                    or text[ref.start : ref.start + len(ref.text)] != ref.text
                ):
                    raise BadRequestError(error_messages.VERIFICATION_QUOTE_INVALID)
                valid_reference = True
            if len({t.item_id for t in condition.targets}) != len(condition.targets):
                raise BadRequestError(error_messages.VERIFICATION_TARGET_INVALID)
            for target in condition.targets:
                if (
                    target.item_id not in linked
                    or len(set(target.pattern_ids)) != len(target.pattern_ids)
                    or (target.pattern_scope == "all_enabled" and target.pattern_ids)
                ):
                    raise BadRequestError(error_messages.VERIFICATION_TARGET_INVALID)
            method_complete = (
                condition.method == "test"
                and bool(condition.targets)
                and all(
                    t.pattern_scope == "all_enabled" or t.pattern_ids
                    for t in condition.targets
                )
            )
            method_complete = (
                method_complete
                or (
                    condition.method == "manual" and bool(condition.manual_plan.strip())
                )
                or (
                    condition.method == "not_applicable"
                    and bool(condition.not_applicable_reason.strip())
                )
            )
            complete = (
                complete
                and valid_reference
                and bool(condition.description.strip())
                and bool(method_complete)
            )
        if data.confirm_coverage and not complete:
            raise BadRequestError(error_messages.VERIFICATION_INCOMPLETE)
        selected = [
            t
            for r in data.checklist.rows
            for t in r.targets
            if t.pattern_scope == "selected" and t.pattern_ids
        ]
        if selected:
            pairs = self.candidates.pairs(
                db, project_id, row.source_requirement_id, selected, 0, 1000
            )
            found = {(p["item_id"], p["pattern_id"]) for p in pairs if p["active"]}
            if any(
                (t.item_id, p) not in found for t in selected for p in t.pattern_ids
            ):
                raise BadRequestError(error_messages.VERIFICATION_TARGET_INVALID)
        checklist["coverage"] = None
        if data.confirm_coverage:
            checklist["coverage"] = {
                "subject_digest": row.subject_digest,
                "rows_digest": digest(checklist["rows"]),
            }
        return checklist

    def stamp_coverage(self, db, actor, checklist):
        if checklist["coverage"]:
            checklist["coverage"].update(
                actor_id=actor.id,
                confirmed_at=db.execute(select(func.transaction_timestamp()))
                .scalar_one()
                .isoformat(),
            )
        if len(json.dumps(checklist, ensure_ascii=False).encode()) > 256 * 1024:
            raise ArtifactReleaseTooLargeError()

    def update(self, original, user, project_id, plan_id, data):
        request_digest = digest(data.model_dump(mode="json"))

        def save():
            with self.access._transaction(original, user, project_id, None) as (
                db,
                actor,
            ):
                self.allowed(db, actor, project_id, prepare=True)
                row = self.repository.plan(db, project_id, plan_id, lock=True)
                previous = db.scalar(
                    select(Revision).where(
                        Revision.plan_id == row.id,
                        Revision.operation_id == data.operation_id,
                    )
                )
                if previous:
                    if previous.operation_digest != request_digest:
                        raise DuplicateResourceError()
                    return self.detail(
                        db, actor, project_id, row, saved_version=previous.version
                    )
                if row.current_version != data.version:
                    revision = self.repository.revision(db, row.id, row.current_version)
                    raise VersionConflictError(
                        {
                            "version": row.current_version,
                            "checklist_digest": revision["checklist_digest"],
                        }
                    )
                subject = self.repository.fixed(
                    db, project_id, row.release_id, row.target_node_id
                )
                checklist = self.validate(db, project_id, row, subject, data)
                self.stamp_coverage(db, actor, checklist)
                db.add(
                    Revision(
                        plan_id=row.id,
                        version=row.current_version + 1,
                        checklist=checklist,
                        checklist_digest=digest(checklist),
                        actor_id=actor.id,
                        operation_id=data.operation_id,
                        operation_digest=request_digest,
                    )
                )
                # revisionのguardは旧ヘッダー版を検証するため、先に追記をflushする。
                db.flush()
                row.current_version += 1
                row.updated_by = actor.id
                self.audit(db, actor, project_id, row, "saved")
                db.flush()
                db.refresh(row)
                result = self.detail(db, actor, project_id, row)
            return result

        return self.retry(save)

    def revisions(self, original, user, project_id, plan_id, cursor, page_size):
        with self.access._transaction(original, user, project_id, None) as (db, actor):
            self.allowed(db, actor, project_id)
            plan = self.repository.plan(db, project_id, plan_id)
            scope = self.scope(project_id, plan.id, plan.current_version, "history")
            after = decode_cursor(cursor, project_id, scope) if cursor else 0
            rows = self.repository.history(db, plan.id, after, page_size)
            return s.VerificationRevisionPage(
                items=[
                    s.VerificationRevisionRead(
                        version=r.version,
                        checklist=r.checklist,
                        checklist_digest=r.checklist_digest,
                        actor_name=name,
                        created_at=r.created_at,
                    )
                    for r, name in rows[:page_size]
                ],
                next_cursor=encode_cursor(
                    project_id, scope, rows[page_size - 1][0].version
                )
                if len(rows) > page_size
                else None,
            )

    def execution_candidates(
        self,
        original,
        user,
        project_id,
        plan_id,
        row_id,
        cursor,
        page_size,
        browse_patterns=False,
    ):
        with self.access._transaction(original, user, project_id, None) as (db, actor):
            self.allowed(db, actor, project_id)
            plan = self.repository.plan(db, project_id, plan_id)
            revision = self.repository.revision(db, plan.id, plan.current_version)
            linked = self.candidates.linked(db, project_id, plan.source_requirement_id)
            if len(linked) > 1000:
                return s.VerificationCandidatePage(
                    items=[], state="not_evaluated", reason="target_limit"
                )
            available = [
                {
                    "item_id": str(r["id"]),
                    "design_id": r["design_id"],
                    "code": r["code"],
                    "title": r["title"],
                }
                for r in linked
            ]
            if len(canonical_bytes(available)) > 256 * 1024:
                return s.VerificationCandidatePage(
                    items=[], state="not_evaluated", reason="response_limit"
                )
            if row_id is None:
                return s.VerificationCandidatePage(
                    items=[], available_targets=available
                )
            condition = next(
                (r for r in revision["checklist"]["rows"] if r["id"] == str(row_id)),
                None,
            )
            if condition is None:
                raise NotFoundError()
            row = s.VerificationRow.model_validate(condition)
            if row.method != "test" or not row.targets:
                return s.VerificationCandidatePage(
                    items=[], available_targets=available
                )
            if not {t.item_id for t in row.targets}.issubset({r["id"] for r in linked}):
                return s.VerificationCandidatePage(
                    items=[],
                    state="not_evaluated",
                    reason="target_unavailable",
                    available_targets=available,
                )
            scope = self.scope(
                project_id,
                plan.id,
                plan.current_version,
                row_id,
                browse_patterns,
                digest(
                    [
                        [
                            str(r["id"]),
                            r["design_id"],
                            r["version"],
                            str(r["pattern_table_id"]),
                        ]
                        for r in linked
                    ]
                ),
            )
            offset = decode_cursor(cursor, project_id, scope) if cursor else 0
            pairs = self.candidates.pairs(
                db,
                project_id,
                plan.source_requirement_id,
                [
                    t.model_copy(
                        update={"pattern_scope": "all_enabled", "pattern_ids": []}
                    )
                    for t in row.targets
                ]
                if browse_patterns
                else row.targets,
                offset,
                page_size,
            )
            page = pairs[:page_size]
            if not page:
                return s.VerificationCandidatePage(
                    items=[],
                    state="not_evaluated",
                    reason="no_current_cases",
                    available_targets=available,
                )
            material = self.candidates.material(db, page)
            if material is None:
                return s.VerificationCandidatePage(
                    items=[],
                    state="not_evaluated",
                    reason="material_limit",
                    available_targets=available,
                )
            material, material_bytes = material
            sources = {}
            invalid = False
            try:
                for design_id in {p["design_id"] for p in page}:
                    subset = SimpleNamespace(
                        **{
                            kind: [
                                schema.model_validate(r["data"])
                                for r in material
                                if r["kind"] == kind and r["design_id"] == design_id
                            ]
                            for kind, schema in MATERIAL_SCHEMAS.items()
                        }
                    )
                    sources.update(
                        {
                            (design_id, key): value
                            for key, value in case_sources(
                                cast(ts.TestDesignRead, subset)
                            ).items()
                        }
                    )
            except (ValidationError, KeyError, TypeError, ValueError):
                invalid = True
            latest_rows = self.candidates.latest(
                db, page, 8 * 1024 * 1024 - material_bytes
            )
            if any(r["over_budget"] for r in latest_rows):
                return s.VerificationCandidatePage(
                    items=[],
                    state="not_evaluated",
                    reason="material_limit",
                    available_targets=available,
                )
            if any(
                r["status"] is not None
                and r["status"]
                not in {
                    "not_run",
                    "in_progress",
                    "passed",
                    "failed",
                    "blocked",
                    "not_applicable",
                }
                for r in latest_rows
            ):
                return s.VerificationCandidatePage(
                    items=[],
                    state="not_evaluated",
                    reason="invalid_execution_state",
                    available_targets=available,
                )
            latest = {(r["design_id"], r["source_key"]): r for r in latest_rows}
            items = []
            for pair in page:
                key = pair["design_id"], pair["source_key"]
                execution = latest.get(key)
                state = (
                    "inactive"
                    if not pair["active"]
                    else "not_generated"
                    if execution is None
                    else "not_run"
                    if execution["execution_id"] is None
                    else execution["status"]
                )
                comparison, reason = "not_evaluated", "not_run"
                if invalid or (pair["active"] and key not in sources):
                    reason = "invalid_material"
                elif execution and execution["execution_id"]:
                    try:
                        old = execution["source"]
                        current = sources[key]
                        if not isinstance(old, dict) or not isinstance(
                            old.get("item"), dict
                        ):
                            raise ValueError()
                        if (
                            not {"item", "pattern", "columns", "values"}.issubset(old)
                            or not isinstance(old["columns"], dict)
                            or not isinstance(old["values"], list)
                        ):
                            raise ValueError()
                        comparison = (
                            "same"
                            if source_without_item_code(old)
                            == source_without_item_code(current)
                            else "changed"
                        )
                        reason = "requirement_version_not_recorded"
                    except (KeyError, TypeError, ValueError):
                        reason = "invalid_execution_source"
                items.append(
                    s.VerificationCandidate(
                        selected=pair["active"]
                        and any(
                            t.item_id == pair["item_id"]
                            and (
                                t.pattern_scope == "all_enabled"
                                or pair["pattern_id"] in t.pattern_ids
                            )
                            for t in row.targets
                        ),
                        item_id=pair["item_id"],
                        item_title=pair["item_title"],
                        design_id=pair["design_id"],
                        pattern_id=pair["pattern_id"],
                        pattern_name=pair["pattern_name"],
                        case_id=execution["case_id"] if execution else None,
                        state=state,
                        execution_id=execution["execution_id"] if execution else None,
                        executed_at=execution["executed_at"] if execution else None,
                        comparison=comparison,
                        reason=reason,
                    )
                )
            result = s.VerificationCandidatePage(
                items=items,
                available_targets=available,
                next_cursor=encode_cursor(project_id, scope, offset + page_size)
                if len(pairs) > page_size
                else None,
            )
            if len(canonical_bytes(result.model_dump(mode="json"))) > 512 * 1024:
                return s.VerificationCandidatePage(
                    items=[], state="not_evaluated", reason="response_limit"
                )
            return result
