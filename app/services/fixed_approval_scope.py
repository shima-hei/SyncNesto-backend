"""固定した要件本文の比較と、旧判断記録の証拠分類。DBを更新しない。"""

from typing import Any

from app.core.exceptions import FixedApprovalTooLargeError
from app.services.artifact_release_snapshot import canonical_bytes, digest

MAX_REVIEW_INPUT = 1024 * 1024
MAX_LEGACY_BYTES = 32 * 1024 * 1024


def requirement_subject(node: Any, details: list[Any]) -> dict:
    """release固有の配置を除き、同じ要件ID・版・本文・詳細だけを比較する。"""
    return {
        "policy": "req-subject-v1",
        "source_id": node.source_id,
        "source_version": node.source_version,
        "payload": node.payload,
        "details": [
            {
                "source_id": detail.source_id,
                "source_version": detail.source_version,
                "payload": detail.payload,
            }
            for detail in sorted(details, key=lambda item: item.position)
        ],
    }


def requirement_digest(node: Any, details: list[Any]) -> str:
    """親nodeや保存時点の違いで、同じ本文への合意を重複入力させない。"""
    return digest(requirement_subject(node, details))


def legacy_evidence(source: dict, logs: list[dict]) -> dict:
    """指定先を実操作者と推測せず、証明できた範囲を明示する。"""
    if (
        len(canonical_bytes(source)) > MAX_REVIEW_INPUT
        or any(len(canonical_bytes(log)) > MAX_REVIEW_INPUT for log in logs)
        or len(canonical_bytes({"source": source, "logs": logs})) > MAX_LEGACY_BYTES
    ):
        raise FixedApprovalTooLargeError()
    issues = ["SUBJECT_NOT_FIXED"]
    action = f"approval.{source['status']}"
    matched = [
        log
        for log in logs
        if log["action"] == action
        and isinstance(log.get("new_value"), dict)
        and log["new_value"].get("id") == source["id"]
        and log["new_value"].get("document_id") == source["document_id"]
        and log["new_value"].get("target_type") == source["target_type"]
        and log["new_value"].get("target_id") == source["target_id"]
    ]
    actor = None
    if len(matched) != 1:
        issues.append(
            "DECISION_LOG_MISSING" if not matched else "DECISION_LOG_AMBIGUOUS"
        )
    elif source["status"] in {"approved", "rejected"}:
        actor = matched[0].get("changed_by")
        if actor is None:
            issues.append("ACTOR_UNKNOWN")
        elif actor != source["approver_id"]:
            issues.append("ACTOR_DIFFERS_FROM_DESIGNEE")
    if source["requested_by"] == source["approver_id"]:
        issues.append("SELF_APPROVAL")
    if source["target_type"] not in {"document", "requirement_item"}:
        issues.append("LEGACY_TARGET_KIND")
    return {
        "classification": "history_only",
        "subject_verified": False,
        "actual_actor_id": actor,
        "issues": issues,
        "source_digest": digest({"source": source, "logs": logs}),
    }
