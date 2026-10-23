"""従来承認の証拠を保存し、未判断依頼だけを原子的に切り替える。"""

from datetime import datetime
from uuid import uuid4, uuid5

from sqlalchemy import select

from app.core.exceptions import (
    DuplicateResourceError,
    FixedApprovalMigratedError,
    FixedApprovalScopeConflictError,
    FixedApprovalTooLargeError,
    NotFoundError,
)
from app.models.fixed_approval import FixedApprovalRequest, LegacyApprovalImport
from app.schemas.fixed_approval import LegacyApprovalImportRead, LegacyApprovalPreview
from app.services.artifact_release_snapshot import MAX_PAGE_BYTES, digest
from app.services.fixed_approval import FixedApprovalService
from app.services.fixed_approval_cursor import decode_cursor, encode_cursor
from app.services.fixed_approval_scope import legacy_evidence


def record_json(row):
    """証拠の原値をJSON保存できる形式へ変換する。"""
    return {
        column.key: (
            value.isoformat()
            if isinstance(value := getattr(row, column.key), datetime)
            else value
        )
        for column in row.__table__.columns
    }


class FixedApprovalMigrationService:
    """旧行と旧判断を保持し、新方式で過去の本文を推測生成しない。"""

    def __init__(self):
        """通常の承認処理と同じ認可・transactionを利用する。"""
        self.approvals = FixedApprovalService()
        self.repository = self.approvals.repository

    def prepare(self, db, actor, project_id, data, *, lock=False):
        """元の行・対応ログ・新しい固定内容を同じ読取で確認する。"""
        self.approvals.check_input(data)
        row = self.repository.legacy(db, project_id, data.legacy_approval_id, lock=lock)
        if row is None:
            raise NotFoundError()
        logs = [record_json(item) for item in self.repository.legacy_logs(db, row)]
        source = record_json(row)
        evidence = legacy_evidence(source, logs)
        new_preview, bases = None, []
        if data.mode == "switch":
            if data.new_request.legacy_import_id is not None:
                raise FixedApprovalScopeConflictError()
            previous = db.scalar(
                select(LegacyApprovalImport).where(
                    LegacyApprovalImport.project_id == project_id,
                    LegacyApprovalImport.legacy_approval_id == row.id,
                    LegacyApprovalImport.mode == "switch",
                )
            )
            if previous:
                raise FixedApprovalMigratedError()
            if row.status != "requested" or row.target_type not in {
                "requirement_item",
                "document",
            }:
                raise FixedApprovalScopeConflictError()
            new_preview, bases = self.approvals.scope(
                db, actor, project_id, data.new_request
            )
            expected = (
                "requirement"
                if row.target_type == "requirement_item"
                else "requirement_document"
            )
            if (
                new_preview.target_kind != expected
                or new_preview.source_id != row.target_id
            ):
                raise FixedApprovalScopeConflictError()
        result = LegacyApprovalPreview(
            legacy_approval_id=row.id,
            source=source,
            evidence=evidence,
            preview_digest=digest(
                {
                    "actor": actor.id,
                    "project": project_id,
                    "mode": data.mode,
                    "source": evidence["source_digest"],
                    "new": new_preview.preview_digest if new_preview else None,
                }
            ),
            new_request=new_preview,
        )
        if len(result.model_dump_json().encode()) > MAX_PAGE_BYTES:
            raise FixedApprovalTooLargeError()
        return result, logs, bases

    def preview(self, original, user, project_id, data):
        """原値を書き換えず移行の適否を確認する。"""
        with self.approvals.access(original, user, project_id, write=True) as (
            db,
            actor,
        ):
            return self.prepare(db, actor, project_id, data)[0]

    def read(self, db, actor, project_id, row, cursor=None):
        """取込時点の全証拠を、署名cursorとbyte上限で分割する。"""
        kinds, document_read = self.approvals.allowed(db, actor, project_id)
        migrated = db.scalar(
            self.repository.visible(project_id, kinds, document_read=document_read)
            .where(
                FixedApprovalRequest.legacy_import_id == row.id,
            )
            .order_by(
                FixedApprovalRequest.created_at.desc(), FixedApprovalRequest.id.desc()
            )
            .limit(1)
        )
        offset = decode_cursor(cursor, project_id, str(row.id), row.source_digest)
        result = LegacyApprovalImportRead(
            id=row.id,
            legacy_approval_id=row.legacy_approval_id,
            mode=row.mode,
            source=row.source,
            evidence=row.evidence,
            created_by=row.created_by,
            created_at=row.created_at,
            migrated_to=migrated.id if migrated else None,
            logs=[],
            total_logs=len(row.logs),
            next_cursor=None,
        )
        for log in row.logs[offset : offset + 100]:
            proposed = result.model_copy(update={"logs": [*result.logs, log]})
            if len(proposed.model_dump_json().encode()) > MAX_PAGE_BYTES - 2048:
                if not result.logs:
                    raise FixedApprovalTooLargeError()
                break
            result = proposed
        after = offset + len(result.logs)
        if after < len(row.logs):
            result.next_cursor = encode_cursor(
                project_id, str(row.id), row.source_digest, after
            )
        if len(result.model_dump_json().encode()) > MAX_PAGE_BYTES:
            raise FixedApprovalTooLargeError()
        return result

    def get(self, original, user, project_id, import_id, cursor):
        """旧本文を完全削除した後も、取込済み証拠を現在権限で読む。"""
        with self.approvals.access(original, user, project_id) as (db, actor):
            row = self.repository.imported(db, project_id, import_id)
            if row is None:
                raise NotFoundError()
            return self.read(db, actor, project_id, row, cursor)

    def create(self, original, user, project_id, data):
        """archiveとswitchを区別し、履歴・依頼・ガード対応を一括保存する。"""
        self.approvals.check_input(data)

        def operation():
            with self.approvals.access(original, user, project_id, write=True) as (
                db,
                actor,
            ):
                request_hash = digest(data.model_dump(mode="json"))
                previous = db.scalar(
                    select(LegacyApprovalImport).where(
                        LegacyApprovalImport.project_id == project_id,
                        LegacyApprovalImport.created_by == actor.id,
                        LegacyApprovalImport.request_id == data.request_id,
                    )
                )
                if previous:
                    if previous.request_digest != request_hash:
                        raise DuplicateResourceError()
                    if previous.mode == "switch":
                        native = db.scalar(
                            select(FixedApprovalRequest).where(
                                FixedApprovalRequest.legacy_import_id == previous.id
                            )
                        )
                        if native is None:
                            raise FixedApprovalScopeConflictError()
                        self.approvals.release(
                            db,
                            actor,
                            project_id,
                            native.release_id,
                            native.preservation_id,
                            write=True,
                        )
                    return self.read(db, actor, project_id, previous), False
                preview, logs, bases = self.prepare(
                    db, actor, project_id, data, lock=True
                )
                if preview.preview_digest != data.expected_preview_digest:
                    raise FixedApprovalScopeConflictError()
                existing = db.scalar(
                    select(LegacyApprovalImport).where(
                        LegacyApprovalImport.project_id == project_id,
                        LegacyApprovalImport.legacy_approval_id
                        == data.legacy_approval_id,
                        LegacyApprovalImport.source_digest
                        == preview.evidence["source_digest"],
                        LegacyApprovalImport.mode == data.mode,
                    )
                )
                if existing:
                    return self.read(db, actor, project_id, existing), False
                row = LegacyApprovalImport(
                    id=uuid4(),
                    project_id=project_id,
                    legacy_approval_id=data.legacy_approval_id,
                    source=preview.source,
                    logs=logs,
                    source_digest=preview.evidence["source_digest"],
                    evidence=preview.evidence,
                    mode=data.mode,
                    created_by=actor.id,
                    request_id=data.request_id,
                    request_digest=request_hash,
                )
                db.add(row)
                db.flush()
                if preview.new_request is not None:
                    self.approvals.save(
                        db,
                        actor,
                        project_id,
                        data.new_request,
                        preview.new_request,
                        bases,
                        request_id=uuid5(row.id, "legacy-switch"),
                        request_digest=request_hash,
                        import_id=row.id,
                    )
                self.approvals.audit(
                    db, actor, project_id, row.id, f"legacy_{data.mode}"
                )
                db.flush()
                return self.read(db, actor, project_id, row), True

        return self.approvals.retry(operation)
