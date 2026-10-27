"""API routers."""

from fastapi import Depends, FastAPI

from app.core.tenant import get_current_tenant
from app.routers import (
    account_actions,
    artifact_preservations,
    artifact_publication_access,
    artifact_publications,
    artifact_releases,
    audit_logs,
    auth,
    deleted_data_cleanup,
    demo,
    documents,
    drafts,
    fixed_approvals,
    guest_fixed_approvals,
    guest_invitations,
    health,
    home,
    mcp,
    mfa,
    notification_email,
    notifications,
    project_objectives,
    projects,
    requirement_agreements,
    requirement_trace,
    requirement_verifications,
    requirements,
    search,
    shared_notifications,
    specifications,
    system_management,
    tasks,
    tenants,
    test_collaboration,
    test_designs,
    test_issues,
    trash,
    users,
    verification_evidence,
    verification_verdicts,
)


def register_routers(app: FastAPI) -> None:
    """アプリケーションのルーターを登録する。

    Args:
        app: ルーターを登録するFastAPIアプリケーション。
    """
    app.include_router(auth.router)
    app.include_router(notification_email.router)
    app.include_router(shared_notifications.router)
    app.include_router(guest_fixed_approvals.shared_router)
    app.include_router(artifact_publication_access.shared_router)
    app.include_router(guest_invitations.recipient_router)
    app.include_router(mfa.router)
    app.include_router(mfa.operator_router)
    app.include_router(mcp.router)
    app.include_router(demo.router)
    app.include_router(deleted_data_cleanup.router)
    app.include_router(account_actions.router)
    app.include_router(drafts.router, dependencies=[Depends(get_current_tenant)])
    app.include_router(health.router)
    for router in (
        artifact_publication_access.router,
        artifact_publications.router,
        artifact_preservations.router,
        artifact_releases.router,
        fixed_approvals.router,
        guest_fixed_approvals.router,
        guest_invitations.router,
        home.router,
        search.router,
        trash.router,
        notifications.router,
        projects.router,
        project_objectives.router,
        requirement_trace.router,
        requirement_agreements.router,
        requirement_verifications.router,
        verification_evidence.router,
        verification_verdicts.router,
        documents.router,
        specifications.router,
        requirements.router,
        tasks.router,
        test_designs.router,
        test_collaboration.router,
        test_issues.router,
    ):
        app.include_router(router, dependencies=[Depends(get_current_tenant)])
    app.include_router(users.router)
    app.include_router(tenants.router)
    app.include_router(system_management.router)
    app.include_router(audit_logs.router)
