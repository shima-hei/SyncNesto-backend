"""横断概要の集計と履歴API。"""

from collections.abc import Callable
from datetime import date, timedelta

from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.project import Project, ProjectMember
from app.models.requirement import (
    Requirement,
    RequirementChangeLog,
    RequirementDocument,
)
from app.models.task import Task, TaskChangeLog
from app.models.test_design import (
    RequirementTestItem,
)
from app.models.test_design import (
    TestCase as CaseModel,
)
from app.models.test_design import (
    TestDesign as DesignModel,
)
from app.models.test_design import (
    TestExecution as ExecutionModel,
)
from app.models.test_design import (
    TestItem as ItemModel,
)
from app.models.user import User
from app.routers.projects import overview_service
from app.services.test_design import TestDesignService as DesignService
from app.services.test_design_cases import case_sources, source_hash
from tests.helpers.auth import authorize_as


def test_overview_counts_current_entities_and_shows_project_activity(
    client: TestClient,
    db: Session,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
    create_test_requirement_document: Callable[..., RequirementDocument],
    create_test_requirement: Callable[..., Requirement],
    create_test_task: Callable[..., Task],
) -> None:
    """履歴実行数は現在のケース数に加えず、他案件は混入させない。"""
    user = create_test_user(email="overview@example.com")
    project = create_test_project(project_code="OVERVIEW")
    other = create_test_project(project_code="OTHER")
    assign_project_role(user=user, project=project, role_key="viewer")
    document = create_test_requirement_document(project=project)
    requirement = create_test_requirement(document=document, requirement_code="REQ-1")
    covered_requirement = create_test_requirement(
        document=document, requirement_code="REQ-2"
    )
    create_test_requirement(document=create_test_requirement_document(project=other))
    late = create_test_task(project=project, task_code="TASK-1", title="Late")
    late.due_date = date.today() - timedelta(days=3)
    bug = create_test_task(project=project, task_code="BUG-1", title="Issue")
    bug.task_type = "bug"
    create_test_task(project=other)
    db.add(
        RequirementChangeLog(
            document_id=document.id,
            target_type="requirement_item",
            target_id=requirement.id,
            action="updated",
            changed_by=user.id,
        )
    )
    db.add(
        TaskChangeLog(
            project_id=project.id,
            target_type="task",
            target_id=late.id,
            action="updated",
            changed_by=user.id,
        )
    )
    db.add(
        TaskChangeLog(
            project_id=project.id,
            target_type="task",
            target_id=bug.id,
            action="created",
            changed_by=user.id,
        )
    )

    design = DesignModel(
        project_id=project.id, name="Design", created_by=user.id, updated_by=user.id
    )
    db.add(design)
    db.flush()
    item = ItemModel(design_id=design.id, position=0, code="T001", content="Login")
    db.add(item)
    db.flush()
    db.add(
        AuditLog(
            tenant_id=project.tenant_id,
            event_type="test_design.updated",
            actor_user_id=user.id,
            project_id=project.id,
            resource_type="test_design",
            resource_id=design.id,
            extra_metadata={},
        )
    )
    db.add(
        RequirementTestItem(
            requirement_id=covered_requirement.id,
            item_id=item.id,
            created_by=user.id,
        )
    )
    db.commit()
    source = case_sources(DesignService().read(db, design))[str(item.id)]
    case = CaseModel(
        design_id=design.id,
        position=0,
        source_key=str(item.id),
        source=source,
        source_hash=source_hash(source),
        status="failed",
    )
    db.add(case)
    db.add(
        CaseModel(
            design_id=design.id,
            position=1,
            source_key="retired-item",
            source=source,
            source_hash=source_hash(source),
            status="passed",
        )
    )
    db.flush()
    db.add_all(
        [
            ExecutionModel(
                case_id=case.id,
                run_number=n,
                status="failed" if n == 2 else "passed",
                source=source,
                executed_by=user.id,
            )
            for n in (1, 2)
        ]
    )
    db.commit()
    authorize_as(client, user)

    response = client.get(f"/projects/{project.id}/overview")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["requirements"] == {"total": 2, "covered": 1, "uncovered": 1}
    assert data["tasks"] == {"total": 1, "done": 0, "overdue": 1, "blocked": 0}
    assert data["issues"] == {"total": 1, "open": 1}
    assert data["tests"] == {
        "total": 1,
        "executed": 1,
        "passed": 0,
        "failed": 1,
        "not_run": 0,
        "failed_without_issue": 1,
    }
    assert [item["kind"] for item in data["attention"]] == [
        "task_overdue",
        "test_failed_no_issue",
        "issue_open",
        "requirement_uncovered",
    ]
    kinds = {activity["kind"] for activity in data["activities"]}
    assert {"requirement", "task", "issue", "test_design", "test_execution"} <= kinds
    assert all(activity["actor_name"] == user.name for activity in data["activities"])

    history = client.get(f"/projects/{project.id}/activities?page_size=2")
    assert history.status_code == 200, history.text
    assert len(history.json()["items"]) == 2
    assert history.json()["has_more"] is True


def test_overview_requires_membership(
    client: TestClient,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
) -> None:
    """非参加者へ横断情報を公開しない。"""
    user = create_test_user(email="outsider-overview@example.com")
    project = create_test_project()
    authorize_as(client, user)
    assert client.get(f"/projects/{project.id}/overview").status_code == 403
    assert client.get(f"/projects/{project.id}/activities").status_code == 403


def test_overview_omits_domain_without_read_permission(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    create_test_user: Callable[..., User],
    create_test_project: Callable[..., Project],
    assign_project_role: Callable[..., ProjectMember],
) -> None:
    """個別ドメインを閲覧できない場合、ゼロ件と誤表示せず非表示にする。"""
    user = create_test_user(email="limited-overview@example.com")
    project = create_test_project()
    assign_project_role(user=user, project=project, role_key="viewer")
    authorize_as(client, user)
    monkeypatch.setattr(
        overview_service,
        "_can",
        lambda _db, _user, _project_id, code: code != "test_case:read",
    )
    response = client.get(f"/projects/{project.id}/overview")
    assert response.status_code == 200, response.text
    assert response.json()["tests"] is None
    assert response.json()["tasks"]["total"] == 0
