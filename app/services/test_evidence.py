"""実行履歴に属する非公開エビデンスを管理する。"""

from datetime import datetime, timezone
from pathlib import PurePath
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.test_design import TestEvidence, TestExecution
from app.repositories.test_collaboration import TestCollaborationRepository
from app.schemas.test_collaboration import TestEvidenceRead, TestExecutionRead
from app.services.storage import StorageService
from app.services.test_design import TestDesignService

ALLOWED_TYPES = {
    "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": (".jpg", b"\xff\xd8\xff"),
    "image/webp": (".webp", b"RIFF"),
    "application/pdf": (".pdf", b"%PDF-"),
    "text/plain": (".txt", None),
    "application/json": (".json", None),
    "application/zip": (".zip", b"PK\x03\x04"),
}
MAX_BYTES = 20 * 1024 * 1024
MAX_FILES_PER_EXECUTION = 20


class TestEvidenceService:
    """ケース・実行の所属を確認してファイルを操作する。"""

    def __init__(self, storage: StorageService | None = None) -> None:
        """RepositoryとS3操作を初期化する。"""
        self.repository = TestCollaborationRepository()
        self.designs = TestDesignService()
        self.storage = storage or StorageService()

    def _case_execution(
        self,
        db: Session,
        project_id: int,
        design_id: int,
        case_id: UUID,
        execution_id: UUID | None = None,
    ) -> tuple[object, TestExecution | None]:
        """プロジェクト内のケースと任意の実行を検証する。"""
        self.designs.get(db, project_id, design_id)
        case = self.repository.case(db, design_id, case_id)
        if case is None:
            raise NotFoundError()
        execution = (
            self.repository.execution(db, case_id, execution_id)
            if execution_id
            else None
        )
        if execution_id and execution is None:
            raise NotFoundError()
        return case, execution

    def list_executions(
        self, db: Session, project_id: int, design_id: int, case_id: UUID
    ) -> list[TestExecutionRead]:
        """実行履歴と証跡件数を返す。"""
        self._case_execution(db, project_id, design_id, case_id)
        rows = self.repository.executions(db, case_id)
        names = self.repository.user_names(
            db, {row.executed_by for row in rows if row.executed_by is not None}
        )
        return [
            TestExecutionRead(
                id=row.id,
                case_id=row.case_id,
                run_number=row.run_number,
                status=row.status,
                actual_result=row.actual_result,
                notes=row.notes,
                source=row.source,
                executed_by=row.executed_by,
                executed_by_name=names.get(row.executed_by)
                if row.executed_by
                else None,
                executed_at=row.executed_at,
                evidence_count=len(self.repository.evidence(db, row.id)),
            )
            for row in rows
        ]

    def list_evidence(
        self,
        db: Session,
        project_id: int,
        design_id: int,
        case_id: UUID,
        execution_id: UUID,
    ) -> list[TestEvidenceRead]:
        """指定実行の証跡一覧を返す。"""
        self._case_execution(db, project_id, design_id, case_id, execution_id)
        return [
            TestEvidenceRead.model_validate(row)
            for row in self.repository.evidence(db, execution_id)
        ]

    def _validate_file(
        self, filename: str, content_type: str, content: bytes
    ) -> tuple[str, str]:
        """証跡用の形式、容量、ファイル名を検証する。"""
        filename = PurePath(filename.replace("\\", "/")).name.strip()
        if (
            not filename
            or len(filename) > 255
            or not content
            or len(content) > MAX_BYTES
        ):
            raise BadRequestError("エビデンスの名前またはサイズが不正です")
        spec = ALLOWED_TYPES.get(content_type)
        if spec is None:
            raise BadRequestError("このファイル形式は添付できません")
        extension, signature = spec
        if not filename.lower().endswith(
            (extension, ".jpeg" if extension == ".jpg" else extension)
        ):
            raise BadRequestError("ファイル名と形式が一致しません")
        if signature and not content.startswith(signature):
            raise BadRequestError("ファイル内容と形式が一致しません")
        if content_type == "image/webp" and content[8:12] != b"WEBP":
            raise BadRequestError("ファイル内容と形式が一致しません")
        if content_type in ("text/plain", "application/json"):
            try:
                content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise BadRequestError("テキストはUTF-8で保存してください") from exc
        return filename, extension

    def upload(
        self,
        db: Session,
        project_id: int,
        design_id: int,
        case_id: UUID,
        execution_id: UUID,
        filename: str,
        content_type: str,
        content: bytes,
        actor_id: int,
    ) -> TestEvidenceRead:
        """実行に対して検証済みファイルを登録する。"""
        self._case_execution(db, project_id, design_id, case_id, execution_id)
        existing = self.repository.evidence(db, execution_id)
        if len(existing) >= MAX_FILES_PER_EXECUTION:
            raise BadRequestError("1回の実行に添付できるファイルは20件までです")
        filename, extension = self._validate_file(filename, content_type, content)
        evidence_id = uuid4()
        key = (
            f"projects/{project_id}/test-cases/{case_id}/executions/"
            f"{execution_id}/{evidence_id}{extension}"
        )
        self.storage.upload_private_object(
            key=key, content=content, content_type=content_type
        )
        evidence = TestEvidence(
            id=evidence_id,
            execution_id=execution_id,
            storage_key=key,
            filename=filename,
            content_type=content_type,
            byte_size=len(content),
            uploaded_by=actor_id,
        )
        db.add(evidence)
        try:
            db.commit()
        except Exception:
            db.rollback()
            self.storage.delete_object(key)
            raise
        return TestEvidenceRead.model_validate(evidence)

    def get_evidence(
        self,
        db: Session,
        project_id: int,
        design_id: int,
        case_id: UUID,
        execution_id: UUID,
        evidence_id: UUID,
    ) -> TestEvidence:
        """所属と有効性を確認して証跡を取得する。"""
        self._case_execution(db, project_id, design_id, case_id, execution_id)
        evidence = self.repository.evidence_by_id(db, execution_id, evidence_id)
        if evidence is None:
            raise NotFoundError()
        return evidence

    def download_url(
        self,
        db: Session,
        project_id: int,
        design_id: int,
        case_id: UUID,
        execution_id: UUID,
        evidence_id: UUID,
    ) -> str:
        """認可済み証跡の短期URLを返す。"""
        evidence = self.get_evidence(
            db, project_id, design_id, case_id, execution_id, evidence_id
        )
        return self.storage.private_object_url(evidence.storage_key)

    def delete(
        self,
        db: Session,
        project_id: int,
        design_id: int,
        case_id: UUID,
        execution_id: UUID,
        evidence_id: UUID,
    ) -> None:
        """明示的な削除要求で証跡を論理削除する。"""
        evidence = self.get_evidence(
            db, project_id, design_id, case_id, execution_id, evidence_id
        )
        evidence.deleted_at = datetime.now(timezone.utc)
        db.commit()
