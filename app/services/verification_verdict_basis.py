"""固定根拠の構成、全対象の準備条件、現在内容との独立した照合。"""

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import select, text

from app.core import error_messages as messages
from app.core.exceptions import (
    AppError,
    ArtifactReleaseTooLargeError,
    BadRequestError,
    ConflictError,
)
from app.models.requirement_verification import RequirementVerificationPlan as Plan
from app.repositories.requirement_agreement import RequirementAgreementRepository
from app.repositories.requirement_verification import RequirementVerificationRepository
from app.repositories.verification_candidates import VerificationCandidateRepository
from app.repositories.verification_verdict import VerdictRepository
from app.schemas import test_design as ts
from app.services.artifact_release_snapshot import digest
from app.services.requirement_verification import MATERIAL_SCHEMAS
from app.services.test_design_cases import case_sources, source_without_item_code


def semantic(source):
    if (
        not isinstance(source, dict)
        or not isinstance(source.get("item"), dict)
        or not isinstance(source.get("columns"), dict)
        or not isinstance(source.get("values"), list)
        or "pattern" not in source
    ):
        return None
    return digest(source_without_item_code(source))


def public_entry(entry):
    return {k: v for k, v in entry.items() if k not in {"file_slots"}}


def public_file(file):
    return {
        k: v
        for k, v in file.items()
        if k
        not in {
            "storage_key",
            "preserved_key",
            "destination_version",
            "parent_id",
            "execution_id",
            "slot",
        }
    }


@dataclass
class ObservationBatch:
    values: dict[str, list[dict[str, Any]]]
    bytes_used: int


class VerdictBasis:
    def __init__(self):
        self.repo = VerdictRepository()
        self.plans = RequirementVerificationRepository()
        self.candidates = VerificationCandidateRepository()

    def observe(self, db, project_id, scopes):
        """複数要件も同じSQL回数で対象内容・実行末尾を読み取る。"""
        pairs = self.repo.inventory(db, project_id, scopes)
        material = self.candidates.material(db, pairs) if pairs else ([], 0)
        if material is None:
            raise ArtifactReleaseTooLargeError()
        material, material_bytes = material
        sources = {}
        try:
            for design in {p["design_id"] for p in pairs}:
                subset = SimpleNamespace(
                    **{
                        kind: [
                            schema.model_validate(r["data"])
                            for r in material
                            if r["kind"] == kind and r["design_id"] == design
                        ]
                        for kind, schema in MATERIAL_SCHEMAS.items()
                    }
                )
                sources.update(
                    {
                        (design, key): semantic(value)
                        for key, value in case_sources(
                            cast(ts.TestDesignRead, subset)
                        ).items()
                    }
                )
        except (ValidationError, KeyError, TypeError, ValueError):
            raise ConflictError(messages.VERDICT_SOURCE_INVALID) from None
        runs = self.repo.runs(db, pairs, 8 * 1048576 - material_bytes)
        latest = {(r["design_id"], r["source_key"]): r for r in runs}
        if len(latest) != len(runs):
            raise ConflictError(messages.VERDICT_SOURCE_INVALID)
        result = {str(scope): [] for scope, _, _ in scopes}
        for pair in pairs:
            r = latest.get((pair["design_id"], pair["source_key"]))
            result[pair["scope"]].append(
                jsonable_encoder(
                    dict(
                        **{k: v for k, v in pair.items() if k != "scope"},
                        source_digest=sources.get(
                            (pair["design_id"], pair["source_key"])
                        ),
                        case_id=r["case_id"] if r else None,
                        execution_id=r["execution_id"] if r else None,
                        run_number=(r["run_number"] or 0) if r else 0,
                        status=r["status"] if r else "not_run",
                        execution_source_digest=semantic(r["source"])
                        if r and r["source"]
                        else None,
                        not_applicable_reason=r["notes"]
                        if r and r["status"] == "not_applicable"
                        else "",
                    )
                )
            )
        return ObservationBatch(
            result, material_bytes + max((r["total"] or 0 for r in runs), default=0)
        )

    @staticmethod
    def inventory(rows):
        return [
            {
                k: v
                for k, v in r.items()
                if k
                not in {
                    "scope",
                    "execution_id",
                    "run_number",
                    "status",
                    "execution_source_digest",
                    "not_applicable_reason",
                }
            }
            for r in rows
        ]

    def manual_conflicts(self, db, project_id, plan_id, plan_version):
        rows = (
            db.execute(
                text("""
          SELECT e->>'row_id' AS row_id,count(*) OVER() AS count FROM
          requirement_verification_evidence_sets s
          CROSS JOIN LATERAL jsonb_array_elements(s.selection->'entries') e
          WHERE s.project_id=:project AND s.plan_id=:plan AND
          s.plan_version=:version AND s.status='ready'
            AND e->>'kind'='manual' AND e->>'result' IN ('failed','blocked') LIMIT 5001
        """),
                {"project": project_id, "plan": plan_id, "version": plan_version},
            )
            .mappings()
            .all()
        )
        if len(rows) > 5000:
            raise ArtifactReleaseTooLargeError()
        return sorted({r["row_id"] for r in rows})

    def readiness(self, checklist, entries, observations, conflicts):
        rows = checklist["rows"]
        coverage = checklist.get("coverage")
        coverage_valid = bool(
            rows and coverage and coverage.get("rows_digest") == digest(rows)
        )
        states, actual_pass = [], False
        for row in rows:
            chosen = [e for e in entries if e["row_id"] == row["id"]]
            inv = [p for p in observations if p["row_id"] == row["id"]]
            issues = []
            relevant = [e for e in chosen if e.get("use") == "current_candidate"]
            na = [
                e
                for e in relevant
                if e["kind"] == "not_applicable" and e["reason"].strip()
            ]
            if row["id"] in conflicts or any(
                p["status"] in {"failed", "blocked"} for p in inv
            ):
                issues.append("adverse_evidence")
            if na:
                state = "not_applicable"
                if len(na) != 1 or len(relevant) != 1:
                    issues.append("conflicting_evidence")
            elif row["method"] == "test":
                if not inv or not row.get("targets"):
                    issues.append("no_targets")
                # 選択patternが削除/無効の場合に空集合へ化けないよう確認する。
                for target in row.get("targets", []):
                    found = {
                        p["pattern_id"]
                        for p in inv
                        if p["item_id"] == target["item_id"]
                    }
                    if not found or (
                        target["pattern_scope"] == "selected"
                        and not set(target["pattern_ids"]).issubset(found)
                    ):
                        issues.append("missing_target")
                for p in inv:
                    matches = [
                        e
                        for e in relevant
                        if e["kind"] == "execution"
                        and e.get("execution", {}).get("case_id") == p["case_id"]
                    ]
                    if len(matches) != 1:
                        issues.append("missing_or_conflicting_execution")
                        continue
                    e = matches[0]
                    ex = e["execution"]
                    if (
                        not p["active"]
                        or not p["source_digest"]
                        or p["execution_source_digest"] != p["source_digest"]
                        or ex["id"] != p["execution_id"]
                        or ex["source_digest"] != p["source_digest"]
                    ):
                        issues.append("execution_changed")
                    if p["status"] == "passed":
                        actual_pass = True
                    elif (
                        p["status"] != "not_applicable"
                        or not (p["not_applicable_reason"] or "").strip()
                    ):
                        issues.append("unresolved_execution")
                state = (
                    "unresolved"
                    if issues
                    else "not_applicable"
                    if inv and all(p["status"] == "not_applicable" for p in inv)
                    else "satisfied"
                )
            else:
                manual = [e for e in relevant if e["kind"] == "manual"]
                if (
                    row["method"] != "manual"
                    or len(manual) != 1
                    or manual[0].get("result") != "passed"
                ):
                    issues.append("missing_or_unresolved_confirmation")
                else:
                    actual_pass = True
                state = "satisfied" if not issues else "unresolved"
            if issues:
                state = "unresolved"
            states.append(
                dict(
                    row_id=row["id"],
                    description=row["description"],
                    state=state,
                    issues=sorted(set(issues)),
                )
            )
        complete = (
            bool(rows)
            and coverage_valid
            and all(r["state"] in {"satisfied", "not_applicable"} for r in states)
        )
        return dict(
            coverage_confirmed=coverage_valid,
            rows=states,
            passed_eligible=complete and actual_pass,
            not_applicable_eligible=complete
            and all(r["state"] == "not_applicable" for r in states),
            not_applicable_rows=[
                r["row_id"] for r in states if r["state"] == "not_applicable"
            ],
            unresolved_rows=[r["row_id"] for r in states if r["state"] == "unresolved"],
        )

    def build(
        self, db, project_id, data
    ) -> tuple[Plan, dict[str, Any], dict[str, str]]:
        self.repo.bounded(data.model_dump(mode="json"), 512 * 1024)
        plan = self.plans.plan(db, project_id, data.plan_id)
        if data.plan_version != plan.current_version:
            raise ConflictError(messages.VERDICT_BASIS_CHANGED)
        revision = self.plans.revision(db, plan.id, data.plan_version)
        checklist = revision["checklist"]
        row_ids = {r["id"] for r in checklist["rows"]}
        selected = self.repo.selected(db, project_id, data)
        entries, files = [], {}
        for saved in selected:
            if saved["status"] != "ready" or (
                saved["plan_id"],
                saved["plan_version"],
                saved["subject_digest"],
                saved["checklist_digest"],
            ) != (
                plan.id,
                data.plan_version,
                plan.subject_digest,
                revision["checklist_digest"],
            ):
                raise BadRequestError(messages.VERDICT_BASIS_CHANGED)
            entry = dict(saved["entry"])
            if entry["row_id"] not in row_ids:
                raise BadRequestError(messages.VERDICT_SOURCE_INVALID)
            if len(saved["files"]) != len(entry["file_slots"]):
                raise ConflictError(messages.VERDICT_SOURCE_INVALID)
            if entry.get("execution"):
                ex = dict(entry["execution"])
                ex["source_digest"] = semantic(ex.pop("source"))
                entry["execution"] = ex
            entry.update(
                set_id=str(saved["set_id"]),
                manifest_digest=saved["manifest_digest"],
                submitted_by=saved["actor_name"],
                submitted_at=saved["created_at"].isoformat(),
                file_ids=[f["object_id"] for f in saved["files"]],
            )
            entries.append(entry)
            for file in saved["files"]:
                files[file["object_id"]] = {**file, "set_id": str(saved["set_id"])}
        if (
            len(files) > 20
            or any(not 0 < f["byte_size"] <= 20 * 1048576 for f in files.values())
            or sum(f["byte_size"] for f in files.values()) > 100 * 1048576
        ):
            raise ArtifactReleaseTooLargeError()
        observed = self.observe(
            db,
            project_id,
            [(str(plan.id), plan.source_requirement_id, checklist["rows"])],
        ).values[str(plan.id)]
        conflicts = self.manual_conflicts(db, project_id, plan.id, data.plan_version)
        readiness = self.readiness(checklist, entries, observed, conflicts)
        if (
            not checklist.get("coverage")
            or checklist["coverage"].get("subject_digest") != plan.subject_digest
        ):
            readiness["coverage_confirmed"] = False
            readiness["passed_eligible"] = readiness["not_applicable_eligible"] = False
        basis: dict[str, Any] = dict(
            plan_id=str(plan.id),
            plan_version=data.plan_version,
            source_requirement_id=plan.source_requirement_id,
            subject_digest=plan.subject_digest,
            checklist_digest=revision["checklist_digest"],
            checklist=checklist,
            entries=sorted(entries, key=lambda e: (e["set_id"], e["id"])),
            files=sorted(files.values(), key=lambda f: f["object_id"]),
            observed=observed,
            inventory_digest=digest(self.inventory(observed)),
            readiness=readiness,
            manual_conflicts=conflicts,
        )
        self.repo.bounded(basis, 1900000)
        current = RequirementAgreementRepository().subjects(
            db, project_id, [plan.source_requirement_id]
        )
        subject = current[0]["subject"] if current else None
        comparison = dict(
            requirement="unavailable"
            if subject is None
            else "same"
            if digest(subject) == plan.subject_digest
            else "changed",
            checklist="same",
            sources="same",
        )
        return plan, basis, comparison

    def compare_many(self, db, project_id, rows):
        if not rows:
            return {}
        result: dict[str, dict[str, Any]] = {
            str(r.id): dict(
                requirement="not_evaluated",
                checklist="not_evaluated",
                sources="not_evaluated",
                adverse="not_evaluated",
            )
            for r in rows
        }
        try:
            plans = {
                str(p.id): p
                for p in db.scalars(
                    select(Plan).where(
                        Plan.project_id == project_id,
                        Plan.id.in_({r.plan_id for r in rows}),
                    )
                )
            }
            subjects = {
                s["id"]: s["subject"]
                for s in RequirementAgreementRepository().subjects(
                    db, project_id, list({r.source_requirement_id for r in rows})
                )
            }
            observed_batch = self.observe(
                db,
                project_id,
                [
                    (str(r.id), r.source_requirement_id, r.basis["checklist"]["rows"])
                    for r in rows
                ],
            )
            observations = observed_batch.values
            marks = [
                {
                    "case_id": p["case_id"],
                    "run_number": p["run_number"],
                    "scope": str(r.id),
                }
                for r in rows
                for p in (r.decision_snapshot or {}).get("observed", [])
                if p["case_id"]
            ]
            marks = list(
                {(m["case_id"], m["run_number"], m["scope"]): m for m in marks}.values()
            )
            late = self.repo.adverse(db, marks, 8 * 1048576 - observed_batch.bytes_used)
            bad = set()
            for run in late:
                if run["status"] in {"failed", "blocked"}:
                    original = next(r for r in rows if str(r.id) == run["scope"])
                    if any(
                        p["case_id"] == str(run["case_id"])
                        and p["source_digest"] == semantic(run["source"])
                        for p in original.decision_snapshot["observed"]
                    ):
                        bad.add(run["scope"])
            # 手動の後着NGも同じ確認表版の範囲で一括検索する。
            manual = (
                db.execute(
                    text("""
              WITH wanted AS (SELECT * FROM jsonb_to_recordset(CAST(:wanted AS
              jsonb)) AS w(scope text,plan_id uuid,plan_version int))
              SELECT w.scope,e->>'row_id' AS row_id FROM wanted w JOIN
              requirement_verification_evidence_sets s ON s.plan_id=w.plan_id AND
              s.plan_version=w.plan_version
              CROSS JOIN LATERAL jsonb_array_elements(s.selection->'entries') e
              WHERE s.project_id=:project AND s.status='ready' AND
              e->>'kind'='manual' AND e->>'result' IN ('failed','blocked') LIMIT 5001
            """),
                    {
                        "project": project_id,
                        "wanted": json.dumps(
                            [
                                dict(
                                    scope=str(r.id),
                                    plan_id=str(r.plan_id),
                                    plan_version=r.plan_version,
                                )
                                for r in rows
                            ]
                        ),
                    },
                )
                .mappings()
                .all()
            )
            if len(manual) > 5000:
                raise ArtifactReleaseTooLargeError()
            bad.update(m["scope"] for m in manual)
            source_files = self.repo.source_files(
                db, project_id, [f for r in rows for f in r.basis["files"]]
            )
            for r in rows:
                current = subjects.get(r.source_requirement_id)
                observed = observations[str(r.id)]
                result[str(r.id)] = dict(
                    files_source="same"
                    if all(
                        (f["kind"], f["id"]) in source_files for f in r.basis["files"]
                    )
                    else "unavailable",
                    current_requirement_version=current.get("source_version")
                    if current
                    else None,
                    file_count=len(r.basis["files"]),
                    requirement="unavailable"
                    if current is None
                    else "same"
                    if digest(current) == r.subject_digest
                    else "changed",
                    checklist="same"
                    if str(r.plan_id) in plans
                    and plans[str(r.plan_id)].current_version == r.plan_version
                    else "changed",
                    sources="same"
                    if digest(self.inventory(observed)) == r.basis["inventory_digest"]
                    else "changed",
                    runs="same"
                    if digest(observed) == digest(r.basis["observed"])
                    else "changed",
                    adverse="found" if str(r.id) in bad else "none",
                    observed=observed,
                )
        except (AppError, KeyError, TypeError, ValueError):
            # 明確な上限/欠損を現在合格に補完しない。
            pass
        return result
