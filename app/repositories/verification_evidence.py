"""限定した実行・選択添付と版固定証拠だけを取得する。"""

from fastapi.encoders import jsonable_encoder
from sqlalchemy import String, Text, cast, func, select

from app.core.exceptions import (
    ArtifactReleaseTooLargeError,
    BadRequestError,
    NotFoundError,
)
from app.models.document import DocumentAttachment, ProjectDocument
from app.models.test_design import TestCase, TestDesign, TestEvidence, TestExecution
from app.models.user import User
from app.models.verification_evidence import VerificationEvidenceObject as Object
from app.models.verification_evidence import VerificationEvidenceSet as EvidenceSet
from app.services.artifact_release_snapshot import canonical_bytes


class EvidenceRepository:
    def get(self, db, project_id, plan_id, identity, *, lock=False):
        query = select(EvidenceSet).where(
            EvidenceSet.project_id == project_id,
            EvidenceSet.plan_id == plan_id,
            EvidenceSet.id == identity,
        )
        row = db.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise NotFoundError()
        return row

    def objects(self, db, set_id):
        return list(
            db.scalars(
                select(Object)
                .where(Object.set_id == set_id)
                .order_by(Object.slot, Object.id)
            )
        )

    def executions(self, db, project_id, ids):
        if not ids:
            return {}
        conditions = (
            TestExecution.id.in_(ids),
            TestDesign.project_id == project_id,
            TestDesign.deleted_at.is_(None),
        )
        base = (
            select(TestExecution)
            .join(TestCase, TestCase.id == TestExecution.case_id)
            .join(TestDesign, TestDesign.id == TestCase.design_id)
            .where(*conditions)
        )
        sizes = db.execute(
            base.with_only_columns(
                TestExecution.id,
                func.octet_length(cast(TestExecution.source, Text)),
                func.octet_length(TestExecution.actual_result)
                + func.octet_length(TestExecution.notes),
            )
        ).all()
        if (
            any(s is None or s > 1048576 or b > 131072 for _, s, b in sizes)
            or sum(s + b for _, s, b in sizes) > 8 * 1048576
        ):
            raise ArtifactReleaseTooLargeError()
        latest = (
            select(func.max(TestExecution.run_number))
            .where(TestExecution.case_id == TestCase.id)
            .correlate(TestCase)
            .scalar_subquery()
        )
        rows = db.execute(
            base.add_columns(
                TestCase.design_id,
                func.coalesce(
                    TestCase.source_key, cast(TestCase.item_pattern_id, String)
                ),
                User.name,
                latest,
            ).outerjoin(User, User.id == TestExecution.executed_by)
        ).all()
        if len(rows) != len(set(ids)):
            raise NotFoundError()
        result = {}
        for e, design_id, key, name, last in rows:
            if e.run_number != last or e.status not in {
                "passed",
                "failed",
                "blocked",
                "not_applicable",
                "in_progress",
            }:
                raise BadRequestError()
            result[str(e.id)] = dict(
                id=str(e.id),
                case_id=str(e.case_id),
                design_id=design_id,
                source_key=key,
                run_number=e.run_number,
                status=e.status,
                source=e.source,
                actual_result=e.actual_result,
                notes=e.notes,
                executed_by=e.executed_by,
                executed_by_name=name,
                executed_at=e.executed_at.isoformat() if e.executed_at else None,
                requirement_version_recorded=False,
            )
        return result

    def files(self, db, project_id, selection):
        requested = {(f.kind, f.id) for e in selection.entries for f in e.files}
        result = {}
        for kind, model in (
            ("test_evidence", TestEvidence),
            ("document_attachment", DocumentAttachment),
        ):
            ids = [identity for k, identity in requested if k == kind]
            if not ids:
                continue
            if kind == "test_evidence":
                query = (
                    select(model, TestDesign.id)
                    .join(TestExecution, TestExecution.id == TestEvidence.execution_id)
                    .join(TestCase, TestCase.id == TestExecution.case_id)
                    .join(TestDesign, TestDesign.id == TestCase.design_id)
                    .where(
                        TestDesign.project_id == project_id,
                        TestDesign.deleted_at.is_(None),
                    )
                )
            else:
                query = (
                    select(model, ProjectDocument.id)
                    .join(
                        ProjectDocument,
                        ProjectDocument.id == DocumentAttachment.document_id,
                    )
                    .where(
                        ProjectDocument.project_id == project_id,
                        ProjectDocument.deleted_at.is_(None),
                    )
                )
            for row, parent in db.execute(
                query.where(model.id.in_(ids), model.deleted_at.is_(None))
            ).all():
                if not 0 < row.byte_size <= 20 * 1048576:
                    raise ArtifactReleaseTooLargeError()
                result[(kind, row.id)] = dict(
                    kind=kind,
                    id=str(row.id),
                    parent_id=parent,
                    execution_id=str(row.execution_id)
                    if kind == "test_evidence"
                    else None,
                    filename=row.filename,
                    content_type=row.content_type,
                    byte_size=row.byte_size,
                    storage_key=row.storage_key,
                )
        if set(result) != requested:
            raise NotFoundError()
        if sum(r["byte_size"] for r in result.values()) > 100 * 1048576:
            raise ArtifactReleaseTooLargeError()
        for e in selection.entries:
            for f in e.files:
                if f.kind == "test_evidence" and result[(f.kind, f.id)][
                    "execution_id"
                ] != str(e.execution_id):
                    raise BadRequestError()
        return [
            dict(row, slot=slot)
            for slot, (_, row) in enumerate(
                sorted(result.items(), key=lambda x: (x[0][0], str(x[0][1])))
            )
        ]

    def page(self, db, project_id, plan_id, version, offset, limit, document):
        query = select(EvidenceSet).where(
            EvidenceSet.project_id == project_id, EvidenceSet.plan_id == plan_id
        )
        if version:
            query = query.where(EvidenceSet.plan_version == version)
        if not document:
            query = query.where(
                ~func.jsonb_path_exists(
                    EvidenceSet.selection,
                    '$.files[*] ? (@.kind == "document_attachment")',
                )
            )
        return list(
            db.scalars(
                query.order_by(EvidenceSet.created_at, EvidenceSet.id)
                .offset(offset)
                .limit(limit + 1)
            )
        )

    @staticmethod
    def bounded(value, maximum=512 * 1024):
        if len(canonical_bytes(jsonable_encoder(value))) > maximum:
            raise ArtifactReleaseTooLargeError()
        return value
