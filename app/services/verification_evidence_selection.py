"""保存版への採用候補をまとめて読み、previewと封印で同じ比較を行う。"""

from types import SimpleNamespace
from typing import cast

from pydantic import ValidationError

from app.core.exceptions import (
    ArtifactReleaseTooLargeError,
    BadRequestError,
    ConflictError,
    NotFoundError,
)
from app.repositories.requirement_verification import RequirementVerificationRepository
from app.repositories.verification_candidates import VerificationCandidateRepository
from app.repositories.verification_evidence import EvidenceRepository
from app.schemas import test_design as ts
from app.schemas.requirement_verification import VerificationRow
from app.services.artifact_release_snapshot import canonical_bytes, digest
from app.services.requirement_verification import MATERIAL_SCHEMAS
from app.services.test_design_cases import case_sources, source_without_item_code


class EvidenceSelectionBuilder:
    def __init__(self):
        self.plans = RequirementVerificationRepository()
        self.candidates = VerificationCandidateRepository()
        self.repository = EvidenceRepository()

    def build(self, db, project_id, plan_id, data):
        if len(canonical_bytes(data.model_dump(mode="json"))) > 512 * 1024:
            raise ArtifactReleaseTooLargeError()
        plan = self.plans.plan(db, project_id, plan_id)
        if data.plan_version > plan.current_version:
            raise NotFoundError()
        revision = self.plans.revision(db, plan_id, data.plan_version)
        rows = {
            r["id"]: VerificationRow.model_validate(r)
            for r in revision["checklist"]["rows"]
        }
        if not rows or any(str(e.row_id) not in rows for e in data.entries):
            raise BadRequestError()
        selected_rows = {str(e.row_id): rows[str(e.row_id)] for e in data.entries}
        targets = {}
        for row in selected_rows.values():
            for target in row.targets:
                previous = targets.get(target.item_id)
                if previous and (
                    previous.pattern_scope == "all_enabled"
                    or target.pattern_scope == "all_enabled"
                ):
                    targets[target.item_id] = target.model_copy(
                        update={"pattern_scope": "all_enabled", "pattern_ids": []}
                    )
                elif previous:
                    targets[target.item_id] = target.model_copy(
                        update={
                            "pattern_ids": sorted(
                                set(previous.pattern_ids + target.pattern_ids)
                            )
                        }
                    )
                else:
                    targets[target.item_id] = target
        pairs = (
            self.candidates.pairs(
                db,
                project_id,
                plan.source_requirement_id,
                list(targets.values()),
                0,
                1000,
            )
            if targets
            else []
        )
        if len(pairs) > 1000:
            raise ArtifactReleaseTooLargeError()
        inventory = [
            dict(
                item_id=str(p["item_id"]),
                pattern_id=str(p["pattern_id"]) if p["pattern_id"] else None,
                design_id=p["design_id"],
                source_key=p["source_key"],
                active=p["active"],
            )
            for p in pairs
        ]
        executions = self.repository.executions(
            db, project_id, [e.execution_id for e in data.entries if e.execution_id]
        )
        sources = {}
        if executions:
            material = self.candidates.material(db, pairs)
            if material is None:
                raise ArtifactReleaseTooLargeError()
            material, _ = material
            try:
                for design_id in {p["design_id"] for p in pairs}:
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
            except (ValidationError, KeyError, ValueError, TypeError):
                raise BadRequestError() from None
        files = self.repository.files(db, project_id, data)
        entries = []
        for e in data.entries:
            row = rows[str(e.row_id)]
            if e.kind != "not_applicable" and row.method != (
                "test" if e.kind == "execution" else "manual"
            ):
                raise BadRequestError()
            saved = e.model_dump(mode="json")
            saved["row_description"] = row.description
            saved["file_slots"] = [
                f["slot"]
                for f in files
                if any(
                    f["kind"] == selected.kind and f["id"] == str(selected.id)
                    for selected in e.files
                )
            ]
            if e.kind == "execution":
                execution = executions[str(e.execution_id)]
                pair = next(
                    (
                        p
                        for p in pairs
                        if (p["design_id"], p["source_key"])
                        == (execution["design_id"], execution["source_key"])
                    ),
                    None,
                )
                if pair is None or not any(
                    t.item_id == pair["item_id"]
                    and (
                        t.pattern_scope == "all_enabled"
                        or pair["pattern_id"] in t.pattern_ids
                    )
                    for t in row.targets
                ):
                    raise BadRequestError()
                current = sources.get((execution["design_id"], execution["source_key"]))
                old = execution["source"]
                comparable = (
                    isinstance(old, dict)
                    and isinstance(old.get("item"), dict)
                    and isinstance(old.get("columns"), dict)
                    and isinstance(old.get("values"), list)
                    and "pattern" in old
                    and current is not None
                )
                same = (
                    comparable
                    and isinstance(current, dict)
                    and source_without_item_code(old)
                    == source_without_item_code(current)
                )
                if e.use == "current_candidate" and (not pair["active"] or not same):
                    raise ConflictError()
                saved.update(
                    execution=execution,
                    comparison_at_submission="same"
                    if same and pair["active"]
                    else "changed",
                    current_source_digest=digest(source_without_item_code(current))
                    if current
                    else None,
                )
            entries.append(saved)
        snapshot = dict(
            plan_version=data.plan_version,
            subject_digest=plan.subject_digest,
            checklist_digest=revision["checklist_digest"],
            entries=entries,
            files=files,
            inventory=inventory,
        )
        if len(canonical_bytes(snapshot)) > 10 * 1048576:
            raise ArtifactReleaseTooLargeError()
        return snapshot

    @staticmethod
    def public_file(file):
        return {
            k: v
            for k, v in file.items()
            if k
            not in {
                "storage_key",
                "parent_id",
                "execution_id",
                "destination_version",
                "object_id",
            }
        }

    def preview(self, snapshot):
        return dict(
            preview_digest=digest(snapshot),
            plan_version=snapshot["plan_version"],
            subject_digest=snapshot["subject_digest"],
            checklist_digest=snapshot["checklist_digest"],
            entries=[
                dict(
                    id=e["id"],
                    row_id=e["row_id"],
                    kind=e["kind"],
                    row_description=e["row_description"],
                    reason=e["reason"],
                    use=e["use"],
                    result=e.get("execution", {}).get("status", e["result"]),
                    actual_result=e.get("execution", {}).get(
                        "actual_result", e["actual_result"]
                    ),
                    executed_by_name=e.get("execution", {}).get("executed_by_name"),
                    executed_at=e.get("execution", {}).get("executed_at"),
                    reported_at=e["reported_at"],
                )
                for e in snapshot["entries"]
            ],
            files=[self.public_file(f) for f in snapshot["files"]],
            total_bytes=sum(f["byte_size"] for f in snapshot["files"]),
            warnings=[
                "保全完了は要件の合格ではありません。",
                "ファイルのhashは保全時の同一性です。実行当時のbytesを証明しません。",
                "今回確認した対応は、実行当時に要件版が確認済みだったことを意味しません。",
            ],
        )

    @staticmethod
    def input_from_snapshot(snapshot):
        from app.schemas.verification_evidence import EvidenceEntry, EvidenceSelection

        return EvidenceSelection(
            plan_version=snapshot["plan_version"],
            entries=[
                EvidenceEntry.model_validate(
                    {k: v for k, v in e.items() if k in EvidenceEntry.model_fields}
                )
                for e in snapshot["entries"]
            ],
        )
