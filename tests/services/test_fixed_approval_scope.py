"""移行時に証拠がない本文・判断者を生成しないことを確認する。"""

from copy import deepcopy
from types import SimpleNamespace

from app.services.fixed_approval_scope import legacy_evidence, requirement_digest


def test_subject_preserves_ids_versions_details_and_ignores_container():
    """別releaseの同じ本文と、同名の別REQを区別する。"""
    node = SimpleNamespace(source_id=7, source_version=2, payload={"title": "目標"})
    detail = SimpleNamespace(
        source_id=8,
        source_version=1,
        position=0,
        payload={"detail_json": {"value": None}},
    )
    first = requirement_digest(node, [detail])
    node.parent_id, node.position = "other-release", 99
    assert requirement_digest(node, [detail]) == first
    node.source_id = 9
    assert requirement_digest(node, [detail]) != first
    node.source_id = 7
    detail.payload["detail_json"]["value"] = ""
    assert requirement_digest(node, [detail]) != first


def test_legacy_actual_actor_is_never_inferred_from_approver():
    """不一致や欠落があっても旧記録の状態・指定先を書き換えない。"""
    source = dict(
        id=1,
        document_id=2,
        target_type="requirement_item",
        target_id=3,
        status="approved",
        approver_id=4,
        requested_by=5,
    )
    original = deepcopy(source)
    log = dict(action="approval.approved", new_value=deepcopy(source), changed_by=6)
    result = legacy_evidence(source, [log])
    assert result["actual_actor_id"] == 6
    assert "ACTOR_DIFFERS_FROM_DESIGNEE" in result["issues"]
    assert not result["subject_verified"]
    assert source == original
    assert legacy_evidence(source, [])["actual_actor_id"] is None
    assert "DECISION_LOG_AMBIGUOUS" in legacy_evidence(source, [log, log])["issues"]


def test_legacy_matching_actor_is_not_proof_of_fixed_subject():
    """当時の本文参照がなければ承認済み固定版には変換しない。"""
    source = dict(
        id=1,
        document_id=2,
        target_type="section",
        target_id=3,
        status="rejected",
        approver_id=4,
        requested_by=5,
    )
    log = dict(action="approval.rejected", new_value=deepcopy(source), changed_by=4)
    result = legacy_evidence(source, [log])
    assert result["actual_actor_id"] == 4
    assert result["classification"] == "history_only"
    assert "SUBJECT_NOT_FIXED" in result["issues"]
    assert "LEGACY_TARGET_KIND" in result["issues"]
