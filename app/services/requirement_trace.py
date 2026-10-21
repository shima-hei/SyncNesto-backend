"""要件の追跡を、本人/tenant/分野の読取権限内に制限する。"""

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError
from app.repositories.requirement_trace import EXCERPT_LIMIT, RequirementTraceRepository
from app.schemas.requirement_trace import (
    RequirementTraceDetail,
    RequirementTracePage,
    RequirementTraceRow,
    TraceCapabilities,
    TraceFacet,
    TraceTarget,
)
from app.services.project_objective import ProjectObjectiveService

FACET_PERMISSIONS = {
    "designs": "document:read",
    "tasks": "task:read",
    "test_items": "test_plan:read",
}


class RequirementTraceService:
    """関連なし・権限外・未集計を区別し、業務データを更新しない。"""

    def __init__(self):
        """認可は既存の業務Project認可を使う。"""
        self.access = ProjectObjectiveService()
        self.repository = RequirementTraceRepository()

    def capabilities(self, db, user, project_id):
        """入口を見られることと判断権限を区別する。"""
        return TraceCapabilities(
            read_approvals=self.access.can(
                db, user, project_id, "artifact_release:read"
            )
        )

    def page(self, db, user, project_id, page, page_size, document_id, q, status):
        """全行の本文/履歴や行ごとのAPIを呼ばない。"""
        base = dict(page=page, page_size=page_size)
        if db.info.get("demo_id") or db.info.get("data_realm") == "demo":
            return RequirementTracePage(enabled=False, state="disabled", **base)
        self.access.access(db, user, project_id)
        if not settings.project_traceability_enabled:
            return RequirementTracePage(enabled=False, state="disabled", **base)
        if not self.access.can(db, user, project_id, "requirement:read"):
            return RequirementTracePage(enabled=True, state="restricted", **base)
        rows, total = self.repository.rows(
            db, project_id, page, page_size, document_id, q, status
        )
        ids = [r["id"] for r in rows]
        facets = {}
        for kind, permission in FACET_PERMISSIONS.items():
            allowed = self.access.can(db, user, project_id, permission)
            counts = (
                self.repository.counts(db, project_id, ids, kind) if allowed else {}
            )
            facets[kind] = {
                rid: TraceFacet(state="ready", count=counts.get(rid, 0))
                if allowed
                else TraceFacet(state="restricted")
                for rid in ids
            }
        return RequirementTracePage(
            enabled=True,
            state="ready",
            total=total,
            items=[
                RequirementTraceRow(
                    **r, **{kind: values[r["id"]] for kind, values in facets.items()}
                )
                for r in rows
            ],
            capabilities=self.capabilities(db, user, project_id),
            **base,
        )

    def detail(self, db, user, project_id, requirement_id):
        """1件の正本の抜粋と同案件の関連を返す。"""
        self.access.access(db, user, project_id)
        if not self.access.can(db, user, project_id, "requirement:read"):
            raise ForbiddenError()
        if not settings.project_traceability_enabled:
            raise NotFoundError()
        row = self.repository.detail(db, project_id, requirement_id)
        if row is None:
            raise NotFoundError()
        result = dict(row)
        for field in ("description", "acceptance_criteria"):
            value = result[field]
            result[field + "_truncated"] = (
                value is not None and len(value) > EXCERPT_LIMIT
            )
            result[field] = value[:EXCERPT_LIMIT] if value is not None else None
        for kind, permission in FACET_PERMISSIONS.items():
            if not self.access.can(db, user, project_id, permission):
                result[kind] = TraceFacet(state="restricted")
                continue
            count = self.repository.counts(db, project_id, [requirement_id], kind).get(
                requirement_id, 0
            )
            targets = self.repository.targets(db, project_id, requirement_id, kind)
            result[kind] = TraceFacet(
                state="ready",
                count=count,
                has_more=count > len(targets),
                items=[
                    TraceTarget(
                        id=str(r["target_id"]),
                        **{
                            k: v
                            for k, v in r.items()
                            if k not in {"requirement_id", "target_id"}
                        },
                    )
                    for r in targets
                ],
            )
        return RequirementTraceDetail(
            **result, capabilities=self.capabilities(db, user, project_id)
        )
