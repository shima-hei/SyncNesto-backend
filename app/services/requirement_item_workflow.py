"""個別要件について作成、レビュー確認、正式承認を独立に表示する。"""

from collections import defaultdict

from app.repositories.requirement_item_workflow import RequirementItemWorkflowRepository
from app.schemas.requirement_workflow import RequirementItemWorkflowState
from app.services.artifact_release import SOURCE_PERMISSIONS
from app.services.artifact_release_snapshot import requirement_snapshot_payload
from app.services.requirement_baseline_changes import REVIEW_IGNORED_FIELDS


def _changed_fields(
    requirement, section_id, current_details, node, previous_section_id, fixed_details
):
    """同じ要件の固定本文と現在値を、版番号や運用上の変更から独立に比較する。"""
    current = requirement_snapshot_payload(requirement)
    fields = sorted(
        field
        for field in current.keys() - REVIEW_IGNORED_FIELDS
        if current[field] != node.payload.get(field)
    )
    if section_id != previous_section_id:
        fields.append("parent")
    if current_details.get(requirement.id, {}) != fixed_details.get(node.id, {}):
        fields.append("details")
    return fields


class RequirementItemWorkflowService:
    """現在の要件を、閲覧できる固定内容の証跡と照合する。"""

    def __init__(self, releases):
        self.releases = releases
        self.repository = RequirementItemWorkflowRepository()

    def read(
        self, original, user, project_id, ids
    ) -> list[RequirementItemWorkflowState]:
        """現在の閲覧権限と同じスナップショット内で、表示中の要件を集計する。"""
        with self.releases._transaction(original, user, project_id, None) as (
            db,
            actor,
        ):
            self.releases._permission(db, actor, project_id, "requirement:read")
            return self._read(db, actor, project_id, ids)

    def _read(self, db, actor, project_id, ids):
        repo = self.repository
        items = repo.current_items(db, project_id, ids)
        if not items:
            return []
        ids = [requirement.id for requirement, _ in items]
        documents = {requirement.document_id for requirement, _ in items}
        reviews = repo.latest_reviews(db, project_id, ids)
        baselines = repo.baselines(db, project_id, documents)
        allowed = {
            code
            for code in {
                "artifact_release:read",
                *(f"{kind}:read" for kind in SOURCE_PERMISSIONS.values()),
            }
            if self.releases.authorization.has_project_permission(
                db,
                user=actor,
                project_id=project_id,
                permission_code=code,
            )
        }
        assigned = repo.assigned(
            db, actor.id, {row[0].id for row in [*reviews, *baselines]}
        )

        def visible(request, kinds):
            return (
                request.id in assigned
                or request.requester_id == actor.id
                or "artifact_release:read" in allowed
            ) and all(f"{SOURCE_PERMISSIONS[kind]}:read" in allowed for kind in kinds)

        latest_reviews = {
            node.source_id: (req, node, kinds, section)
            for req, node, kinds, section in reviews
        }
        latest_baselines = {req.document_id: (req, kinds) for req, kinds in baselines}
        visible_reviews = [
            (req, node) for req, node, kinds, _ in reviews if visible(req, kinds)
        ]
        baseline_items = repo.baseline_items(
            db, {req.release_id for req, kinds in baselines if visible(req, kinds)}, ids
        )
        approved_items = {
            (node.release_id, node.source_id): (node, section)
            for node, section in baseline_items
        }
        counts, confirmed, blocked = repo.review_evidence(
            db,
            {req.id for req, _ in visible_reviews},
            {node.id for _, node in visible_reviews},
            {
                identity
                for req, node in visible_reviews
                for identity in (req.target_node_id, node.parent_id, node.id)
                if identity is not None
            },
        )
        current_details = defaultdict(dict)
        fixed_details = defaultdict(dict)
        for detail in repo.current_details(db, ids):
            current_details[detail.requirement_id][detail.id] = {
                "detail_type": detail.detail_type,
                "detail_json": detail.detail_json,
            }
        fixed_nodes = {node.id for _, node in visible_reviews} | {
            node.id for node, _ in baseline_items
        }
        for detail in repo.fixed_details(db, fixed_nodes):
            fixed_details[detail.parent_id][detail.source_id] = detail.payload

        states = []
        for requirement, section_id in items:
            state = RequirementItemWorkflowState(
                requirement_id=requirement.id,
                authoring="draft"
                if requirement.status == "draft"
                else "excluded"
                if requirement.status == "deprecated"
                else "defined",
                review="not_reviewed",
                approval="not_approved",
            )
            if evidence := latest_reviews.get(requirement.id):
                request, node, kinds, previous_section = evidence
                if not visible(request, kinds):
                    state.review = "restricted"
                else:
                    state.review_request_id = request.id
                    state.review_item_node_id = node.id
                    if any(
                        (request.id, identity) in blocked
                        for identity in (
                            request.target_node_id,
                            node.parent_id,
                            node.id,
                        )
                    ):
                        state.review = "changes_requested"
                    elif _changed_fields(
                        requirement,
                        section_id,
                        current_details,
                        node,
                        previous_section,
                        fixed_details,
                    ):
                        state.review = "changed"
                    elif (
                        counts.get(request.id, 0) > 0
                        and confirmed.get((request.id, node.id)) == counts[request.id]
                    ):
                        state.review = "confirmed"
            if evidence := latest_baselines.get(requirement.document_id):
                baseline, kinds = evidence
                if not visible(baseline, kinds):
                    state.approval = "restricted"
                elif item := approved_items.get((baseline.release_id, requirement.id)):
                    node, previous_section = item
                    state.approved_request_id = baseline.id
                    state.approved_version_label = f"A-{baseline.display_number:03d}"
                    state.changed_fields = _changed_fields(
                        requirement,
                        section_id,
                        current_details,
                        node,
                        previous_section,
                        fixed_details,
                    )
                    state.approval = "changed" if state.changed_fields else "approved"
            states.append(state)
        return states
