"""現在内容への判断を読取専用で分類し、申請/判断の既存正本を変更しない。"""

from datetime import UTC, datetime
from typing import cast

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError
from app.repositories.requirement_agreement import RequirementAgreementRepository
from app.schemas.requirement_agreement import (
    AgreementState,
    RequirementAgreementPage,
    RequirementAgreementResult,
    RequirementAgreementRow,
)
from app.services.artifact_release_snapshot import digest
from app.services.project_objective import ProjectObjectiveService

REQUIRED_READS = ("artifact_release:read", "document:read", "test_plan:read")


class RequirementAgreementService:
    """読取不能を合意なしへ補完せず、現在内容の二重読取も確認する。"""

    def __init__(self):
        """既存の社内所属認可と、限定された集約Repositoryを使う。"""
        self.access = ProjectObjectiveService()
        self.repository = RequirementAgreementRepository()

    def read(self, db, user, project_id, ids):
        """表示pageの要件だけをまとめて比較する。"""
        now = datetime.now(UTC)
        if db.info.get("demo_id") or db.info.get("data_realm") == "demo":
            return RequirementAgreementPage(
                enabled=False, state="disabled", evaluated_at=now
            )
        self.access.access(db, user, project_id)
        if not self.access.can(db, user, project_id, "requirement:read"):
            raise ForbiddenError()
        if not (
            settings.project_traceability_enabled
            and settings.project_requirement_agreements_enabled
        ):
            return RequirementAgreementPage(
                enabled=False, state="disabled", evaluated_at=now
            )
        if not all(
            self.access.can(db, user, project_id, code) for code in REQUIRED_READS
        ):
            return RequirementAgreementPage(
                enabled=True, state="restricted", evaluated_at=now
            )
        ids = sorted(set(ids))
        before = self.repository.subjects(db, project_id, ids)
        if {row["id"] for row in before} != set(ids):
            raise NotFoundError()
        hashes = {
            row["id"]: digest(row["subject"])
            for row in before
            if row["subject"] is not None
        }
        evidence = self.repository.evidence(db, project_id, hashes) if hashes else []
        legacy = self.repository.legacy_ids(db, project_id, ids)
        after = {
            row["id"]: row for row in self.repository.subjects(db, project_id, ids)
        }
        results = []
        for row in before:
            rid = row["id"]
            reason = "size_limit" if rid not in hashes else None
            other = after.get(rid)
            if not reason and (
                other is None
                or other["subject"] is None
                or digest(other["subject"]) != hashes[rid]
            ):
                reason = "concurrent_change"
            sides = []
            for external in (False, True):
                histories = [
                    r for r in evidence if r["rid"] == rid and r["external"] == external
                ]
                sides.append(
                    self.classify(histories, rid in legacy and not external, reason)
                )
            results.append(
                RequirementAgreementRow(
                    requirement_id=rid,
                    version=row["version"],
                    internal=sides[0],
                    external=sides[1],
                )
            )
        return RequirementAgreementPage(
            enabled=True, state="ready", evaluated_at=now, items=results
        )

    def classify(self, rows, legacy, unavailable):
        """相反と同内容取消を区別し、旧版や例外文書から合意を作らない。"""
        if unavailable or any(not r["valid"] for r in rows):
            return RequirementAgreementResult(
                state="not_evaluated", reason=unavailable or "invalid_evidence"
            )
        same = {r["status"]: r for r in rows if r["same"]}
        choice = None
        if "approved" in same and "rejected" in same:
            state = "conflicting"
        else:
            state = "unagreed"
            for status, label in (
                ("approved", "agreed"),
                ("rejected", "rejected"),
                ("requested", "pending"),
                ("cancelled", "cancelled"),
            ):
                if status in same:
                    state, choice = label, same[status]
                    break
        if state != "unagreed":
            return RequirementAgreementResult(
                state=cast(AgreementState, state),
                reason="current_subject",
                approval_id=choice["approval_id"] if choice else None,
                event_id=choice["event_id"] if choice else None,
            )
        return RequirementAgreementResult(
            state="changed" if rows else "legacy_unverified" if legacy else "unagreed",
            reason="other_subject" if rows else "legacy" if legacy else "no_record",
        )
