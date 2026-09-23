"""設計書の原子的保存、競合制御、ケースの独立管理。"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import error_messages
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.test_design import TestCase, TestDesign, TestExecution
from app.repositories.project import ProjectRepository
from app.repositories.test_collaboration import TestCollaborationRepository
from app.repositories.test_design import TestDesignRepository
from app.schemas.test_design import (
    TestCaseRead,
    TestCaseUpdate,
    TestDesignCreate,
    TestDesignRead,
    TestDesignSummary,
    TestDesignUpdate,
)
from app.services.audit_log import AuditLogService
from app.services.conflict import (
    raise_duplicate_after_rollback,
    raise_if_version_conflict,
)
from app.services.test_design_cases import (
    case_key,
    case_sources,
    present_case,
    source_hash,
    source_without_item_code,
)


class TestDesignService:
    """所属確認とトランザクション境界を管理する。"""

    def __init__(self) -> None:
        """Repositoryを初期化する。"""
        self.repository = TestDesignRepository()

    def require_project(self, db: Session, project_id: int) -> None:
        """削除済みプロジェクトを除外する。"""
        if ProjectRepository().get_by_id(db, project_id) is None:
            raise NotFoundError()

    def get(
        self, db: Session, project_id: int, design_id: int, *, lock: bool = False
    ) -> TestDesign:
        """プロジェクト所属を検証して設計を返す。"""
        self.require_project(db, project_id)
        design = self.repository.get(db, project_id, design_id, lock=lock)
        if design is None:
            raise NotFoundError()
        return design

    def read(self, db: Session, design: TestDesign) -> TestDesignRead:
        """構造化データをAPI形式へ変換する。"""
        return TestDesignRead(
            **TestDesignSummary.model_validate(design).model_dump(),
            **self.repository.graph(db, design.id),
        )

    def list_designs(self, db: Session, project_id: int) -> list[TestDesignSummary]:
        """プロジェクト内の一覧を返す。"""
        self.require_project(db, project_id)
        counts = self.repository.design_counts(db, project_id)
        return [
            TestDesignSummary.model_validate(row).model_copy(
                update={
                    "item_count": counts.get(row.id, (0, 0))[0],
                    "expanded_case_count": counts.get(row.id, (0, 0))[1],
                }
            )
            for row in self.repository.list_designs(db, project_id)
        ]

    def audit(
        self, db: Session, design: TestDesign, actor_id: int, action: str
    ) -> None:
        """入力本文を含めず重要操作を記録する。"""
        AuditLogService().record(
            db,
            event_type=f"test_design.{action}",
            actor_user_id=actor_id,
            project_id=design.project_id,
            resource_type="test_design",
            resource_id=design.id,
        )

    def create(
        self, db: Session, project_id: int, data: TestDesignCreate, actor_id: int
    ) -> TestDesignRead:
        """空の設計書を作成する。"""
        self.require_project(db, project_id)
        design = TestDesign(
            project_id=project_id,
            created_by=actor_id,
            updated_by=actor_id,
            **data.model_dump(),
        )
        db.add(design)
        db.commit()
        self.audit(db, design, actor_id, "created")
        return self.read(db, design)

    def check_version(self, db: Session, design: TestDesign, version: int) -> None:
        """最新の設計全体を伴う競合エラーを返す。"""
        if design.version != version:
            raise_if_version_conflict(
                current_version=design.version,
                requested_version=version,
                current=self.read(db, design).model_dump(mode="json"),
            )

    def update(
        self,
        db: Session,
        project_id: int,
        design_id: int,
        data: TestDesignUpdate,
        actor_id: int,
    ) -> TestDesignRead:
        """ロック後にversionを検証し、一括編集を原子的に保存する。"""
        design = self.get(db, project_id, design_id, lock=True)
        self.check_version(db, design, data.version)
        old = self.read(db, design)
        old_links = {row.id: (row.item_id, row.pattern_id) for row in old.links}
        if any(
            row.id in old_links and old_links[row.id] != (row.item_id, row.pattern_id)
            for row in data.links
        ):
            raise BadRequestError(error_messages.TEST_DESIGN_LINK_IMMUTABLE)
        try:
            self.repository.replace_graph(db, design.id, data.model_dump())
            design.name = data.name
            design.description = data.description
            design.version += 1
            design.updated_by = actor_id
            db.flush()
            self._sync_cases(db, design)
            db.commit()
        except IntegrityError as exc:
            raise_duplicate_after_rollback(
                db, error_messages.TEST_DESIGN_INVALID_ID, exc
            )
        self.audit(db, design, actor_id, "updated")
        return self.read(db, design)

    def _sync_cases(self, db: Session, design: TestDesign) -> None:
        """保存した設計をケースへ反映し、実行情報と変更影響の基準は保持する。"""
        sources = case_sources(self.read(db, design))
        cases = self.repository.cases(db, design.id)
        existing = {case_key(row): row for row in cases}
        for position, (key, source) in enumerate(sources.items()):
            row = existing.get(key)
            if row is None:
                db.add(
                    TestCase(
                        design_id=design.id,
                        source_key=key,
                        source=source,
                        acknowledged_source=source,
                        source_hash=source_hash(source),
                        position=len(cases) + position,
                    )
                )
            elif row.source != source:
                was_acknowledged = source_hash(row.source) == row.source_hash
                if row.acknowledged_source is None and was_acknowledged:
                    # 旧ケースでも、この更新前なら確認済みの内容を復元できる。
                    row.acknowledged_source = row.source
                if was_acknowledged and source_without_item_code(
                    row.source
                ) == source_without_item_code(source):
                    row.source_hash = source_hash(source)
                    row.acknowledged_source = source
                row.source = source
                row.version += 1

    def delete(
        self, db: Session, project_id: int, design_id: int, version: int, actor_id: int
    ) -> None:
        """設計書を一覧から除外し、ケースと議論の履歴は保持する。"""
        design = self.get(db, project_id, design_id, lock=True)
        self.check_version(db, design, version)
        design.deleted_at = datetime.now(timezone.utc)
        design.updated_by = actor_id
        design.version += 1
        db.commit()
        self.audit(db, design, actor_id, "deleted")

    def list_cases(
        self, db: Session, project_id: int, design_id: int
    ) -> list[TestCaseRead]:
        """変更影響付きのケース一覧を返す。"""
        design = self.get(db, project_id, design_id)
        sources = case_sources(self.read(db, design))
        cases = self.repository.cases(db, design_id)
        names = self.repository.executor_names(
            db, {row.executed_by for row in cases if row.executed_by is not None}
        )
        return [
            present_case(
                row,
                sources,
                names.get(row.executed_by) if row.executed_by is not None else None,
            )
            for row in cases
        ]

    def generate(
        self, db: Session, project_id: int, design_id: int, version: int, actor_id: int
    ) -> list[TestCaseRead]:
        """有効な紐付けから未生成ケースだけを追加する。"""
        design = self.get(db, project_id, design_id, lock=True)
        self.check_version(db, design, version)
        sources = case_sources(self.read(db, design))
        cases = self.repository.cases(db, design_id)
        existing = {case_key(row) for row in cases}
        for link_id, source in sources.items():
            if link_id not in existing:
                case = TestCase(
                    design_id=design_id,
                    source_key=link_id,
                    source=source,
                    acknowledged_source=source,
                    source_hash=source_hash(source),
                    position=len(cases),
                )
                db.add(case)
                cases.append(case)
        db.commit()
        self.audit(db, design, actor_id, "cases_generated")
        names = self.repository.executor_names(
            db, {row.executed_by for row in cases if row.executed_by is not None}
        )
        return [
            present_case(
                row,
                sources,
                names.get(row.executed_by) if row.executed_by is not None else None,
            )
            for row in cases
        ]

    def update_case(
        self,
        db: Session,
        project_id: int,
        design_id: int,
        case_id: UUID,
        data: TestCaseUpdate,
        actor_id: int,
    ) -> TestCaseRead:
        """元の設計を変更せず個別の結果を保存する。"""
        design = self.get(db, project_id, design_id, lock=True)
        case = self.repository.case(db, design_id, case_id)
        if case is None:
            raise NotFoundError()
        sources = case_sources(self.read(db, design))
        executor_names = self.repository.executor_names(
            db, {case.executed_by} if case.executed_by is not None else set()
        )
        raise_if_version_conflict(
            current_version=case.version,
            requested_version=data.version,
            current=present_case(
                case,
                sources,
                executor_names.get(case.executed_by)
                if case.executed_by is not None
                else None,
            ).model_dump(mode="json"),
        )
        case.status, case.actual_result, case.notes = (
            data.status,
            data.actual_result,
            data.notes,
        )
        case.executed_by = actor_id
        case.executed_at = datetime.now(timezone.utc)
        history = TestCollaborationRepository().executions(db, case.id)
        db.add(
            TestExecution(
                case_id=case.id,
                run_number=history[0].run_number + 1 if history else 1,
                status=data.status,
                actual_result=data.actual_result,
                notes=data.notes,
                source=case.source,
                executed_by=actor_id,
                executed_at=case.executed_at,
            )
        )
        case.version += 1
        db.commit()
        self.audit(db, design, actor_id, "case_updated")
        names = self.repository.executor_names(db, {actor_id})
        return present_case(case, sources, names.get(actor_id))

    def refresh_case(
        self,
        db: Session,
        project_id: int,
        design_id: int,
        case_id: UUID,
        version: int,
        actor_id: int,
    ) -> TestCaseRead:
        """明示操作でケースの設計内容を更新し、既存結果は保持する。"""
        design = self.get(db, project_id, design_id, lock=True)
        case = self.repository.case(db, design_id, case_id)
        if case is None:
            raise NotFoundError()
        sources = case_sources(self.read(db, design))
        executor_names = self.repository.executor_names(
            db, {case.executed_by} if case.executed_by is not None else set()
        )
        raise_if_version_conflict(
            current_version=case.version,
            requested_version=version,
            current=present_case(
                case,
                sources,
                executor_names.get(case.executed_by)
                if case.executed_by is not None
                else None,
            ).model_dump(mode="json"),
        )
        key = case_key(case)
        if key not in sources:
            raise BadRequestError(error_messages.TEST_DESIGN_SOURCE_INACTIVE)
        case.source = sources[key]
        case.acknowledged_source = case.source
        case.source_hash = source_hash(case.source)
        case.version += 1
        db.commit()
        self.audit(db, design, actor_id, "case_refreshed")
        return present_case(
            case,
            sources,
            executor_names.get(case.executed_by)
            if case.executed_by is not None
            else None,
        )
