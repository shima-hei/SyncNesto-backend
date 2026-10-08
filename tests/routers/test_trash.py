"""ごみ箱の権限、削除世代、期限、所有経路と失敗時の回収再試行。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.document import DocumentAttachment, DocumentRevision, ProjectDocument
from app.models.project import Project
from app.models.requirement import Requirement
from app.models.task import Task
from app.models.test_design import TestCaseIssue as CaseIssueModel
from app.models.test_design import TestDesign as DesignModel
from app.models.test_design import TestEvidence as EvidenceModel
from app.models.test_design import TestExecution as ExecutionModel
from app.repositories.trash import RESOURCES
from app.services.storage import StorageService
from app.services.trash import TrashService
from tests.fakes.storage import MemoryS3Client
from tests.helpers.auth import authorize_as
from tests.routers.test_test_designs import graph


@pytest.fixture
def context(client, db, create_test_user, create_test_project, assign_project_role):
    """同一Projectの管理者と削除された文書を用意する。"""
    user = create_test_user()
    project = create_test_project()
    assign_project_role(user=user, project=project, role_key="project_admin")
    authorize_as(client, user)
    base = f"/projects/{project.id}"
    doc = client.post(
        base + "/documents", json={"title": "復元対象", "body": "機密本文"}
    ).json()
    assert (
        client.delete(
            base + f"/documents/{doc['id']}", params={"version": 1}
        ).status_code
        == 204
    )
    return user, project, base, doc["id"]


def item(client, base, kind="document", identity=None):
    """APIの照合値をそのまま取り出す。"""
    response = client.get(base + "/trash", params={"kind": kind})
    assert response.status_code == 200, response.text
    return next(
        row
        for row in response.json()["items"]
        if identity is None or row["id"] == str(identity)
    )


def restore(client, base, row):
    """一覧の削除世代と版を送る。"""
    return client.post(
        base + f"/trash/{row['kind']}/{row['id']}/restore",
        json={"deleted_at": row["deleted_at"], "version": row["version"]},
    )


def test_restore_preserves_body_history_and_rejects_old_generation(client, context, db):
    """復元・再削除を古い一覧の操作で上書きしない。"""
    user, project, base, identity = context
    old = item(client, base)
    assert old["version"] == 2
    assert "body" not in old and "storage_key" not in old
    assert client.get(base + "/trash").json()["retention_days"] == 30
    assert restore(client, base, old).status_code == 204
    doc = client.get(base + f"/documents/{identity}").json()
    assert doc["body"] == "機密本文" and doc["version"] == 3
    assert (
        db.scalar(
            select(DocumentRevision.number)
            .where(DocumentRevision.document_id == identity)
            .order_by(DocumentRevision.number.desc())
            .limit(1)
        )
        == 3
    )
    assert restore(client, base, old).status_code == 404
    assert (
        client.delete(
            base + f"/documents/{identity}", params={"version": 3}
        ).status_code
        == 204
    )
    stale = restore(client, base, old)
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT"
    assert "機密本文" not in stale.text
    assert restore(client, base, item(client, base)).status_code == 204


@pytest.mark.parametrize("days,allowed", [(29, True), (30, False), (31, False)])
def test_retention_boundary(client, context, db, days, allowed):
    """保持期間後は、回収コマンド未実行でも復元できない。"""
    _, _, base, identity = context
    db.get(ProjectDocument, identity).deleted_at = datetime.now(UTC) - timedelta(
        days=days
    )
    db.commit()
    row = item(client, base)
    assert row["can_restore"] is allowed
    assert restore(client, base, row).status_code == (204 if allowed else 409)


def test_unlimited_retention(client, context, db, monkeypatch):
    """0日設定は期限なしとし、回収候補も作らない。"""
    _, project, base, identity = context
    monkeypatch.setattr(settings, "deleted_data_retention_days", 0)
    db.get(ProjectDocument, identity).deleted_at = datetime.now(UTC) - timedelta(
        days=4000
    )
    db.commit()
    assert item(client, base)["expires_at"] is None
    assert TrashService().due(db, project.tenant_id, 20, datetime.now(UTC)) == []
    assert restore(client, base, item(client, base)).status_code == 204


@pytest.mark.parametrize(
    "role,visible",
    [("viewer", False), ("member", False), ("manager", False), ("project_admin", True)],
)
def test_deleted_document_permissions(
    client, context, create_test_user, assign_project_role, role, visible
):
    """読めるだけの利用者には削除済み本文の存在も見せない。"""
    _, project, base, _ = context
    row = item(client, base)
    user = create_test_user(email="trash-other@example.com")
    assign_project_role(user=user, project=project, role_key=role)
    authorize_as(client, user)
    assert (
        bool(client.get(base + "/trash", params={"kind": "document"}).json()["total"])
        is visible
    )
    assert restore(client, base, row).status_code == (204 if visible else 403)


def test_parent_restore_keeps_individually_deleted_children(
    client, context, db, create_test_requirement_document, create_test_requirement
):
    """親の復元で、以前に個別削除した要件を復活させない。"""
    _, project, base, _ = context
    doc = create_test_requirement_document(project=project)
    live = create_test_requirement(document=doc)
    deleted = create_test_requirement(document=doc)
    doc.deleted_at = deleted.deleted_at = datetime.now(UTC)
    db.commit()
    child = item(client, base, "requirement", deleted.id)
    assert not child["can_restore"]
    assert restore(client, base, child).status_code == 409
    assert (
        restore(
            client, base, item(client, base, "requirement_document", doc.id)
        ).status_code
        == 204
    )
    db.expire_all()
    assert db.get(Requirement, live.id).deleted_at is None
    assert db.get(Requirement, deleted.id).deleted_at is not None
    assert (
        restore(client, base, item(client, base, "requirement", deleted.id)).status_code
        == 204
    )


def test_project_isolation_and_deleted_project(
    client, context, db, create_test_project, create_test_user, assign_project_role
):
    """別案件、非所属、削除案件への復元を拒否する。"""
    _, project, base, _ = context
    row = item(client, base)
    other = create_test_project()
    assert restore(client, f"/projects/{other.id}", row).status_code == 403
    visitor = create_test_user(email="trash-visitor@example.com")
    assign_project_role(user=visitor, project=other, role_key="project_admin")
    authorize_as(client, visitor)
    assert client.get(base + "/trash").status_code == 403
    assert restore(client, f"/projects/{other.id}", row).status_code == 404
    project.deleted_at = datetime.now(UTC)
    db.commit()
    assert client.get(base + "/trash").status_code == 403


def test_attachment_purge_failure_retry_and_live_parent(client, context, db):
    """ファイル削除失敗では台帳を残し、期限後の再試行でのみ回収する。"""
    user, project, base, identity = context
    s3 = MemoryS3Client()
    s3.put_object(Key="trash/test.txt", Body=b"data", ContentType="text/plain")
    attachment = DocumentAttachment(
        document_id=identity,
        filename="test.txt",
        content_type="text/plain",
        byte_size=4,
        storage_key="trash/test.txt",
        uploaded_by=user.id,
    )
    db.add(attachment)
    doc = db.get(ProjectDocument, identity)
    doc.deleted_at = datetime.now(UTC) - timedelta(days=31)
    db.commit()
    service = TrashService(StorageService(s3))
    attachment_id = attachment.id
    target = service.due(db, project.tenant_id, 20, datetime.now(UTC))[0]
    assert target["kind"] == "document"
    original = s3.delete_object

    def fail(**kwargs):
        raise RuntimeError("S3 unavailable")

    s3.delete_object = fail
    with pytest.raises(RuntimeError):
        service.purge_one(db, target, datetime.now(UTC))
    db.rollback()
    assert (
        db.get(ProjectDocument, identity) is not None and "trash/test.txt" in s3.objects
    )
    s3.delete_object = original
    assert service.purge_one(db, target, datetime.now(UTC))
    db.expire_all()
    assert db.get(ProjectDocument, identity) is None
    assert db.get(DocumentAttachment, attachment_id) is None
    assert "trash/test.txt" not in s3.objects
    assert not service.purge_one(db, target, datetime.now(UTC))


@pytest.mark.parametrize("kind", list(RESOURCES))
def test_purge_each_kind_retains_related_business_data(
    client,
    context,
    db,
    kind,
    create_test_requirement_document,
    create_test_requirement_section,
    create_test_requirement,
    create_test_task,
):
    """各種類の所有行だけ回収し、別の業務資源を維持する。"""
    user, project, base, identity = context
    reqdoc = create_test_requirement_document(project=project)
    section = create_test_requirement_section(document=reqdoc)
    requirement = create_test_requirement(document=reqdoc)
    requirement.section_id = section.id
    task = create_test_task(project=project)
    child = create_test_task(project=project)
    child.parent_task_id = task.id
    design = DesignModel(
        project_id=project.id, name="test", created_by=user.id, updated_by=user.id
    )
    attachment = DocumentAttachment(
        document_id=identity,
        filename="test.txt",
        content_type="text/plain",
        byte_size=4,
        storage_key="trash/kind.txt",
        uploaded_by=user.id,
    )
    db.add_all([design, attachment])
    db.commit()
    rows = {
        "requirement_document": reqdoc,
        "requirement_section": section,
        "requirement": requirement,
        "task": task,
        "test_design": design,
        "document": db.get(ProjectDocument, identity),
        "document_attachment": attachment,
    }
    row = rows[kind]
    rid = row.id
    row.deleted_at = datetime.now(UTC) - timedelta(days=31)
    db.commit()
    service = TrashService(StorageService(MemoryS3Client()))
    target = {"kind": kind, "project_id": project.id, "id": str(rid)}
    assert service.purge_one(db, target, datetime.now(UTC))
    db.expire_all()
    assert db.get(RESOURCES[kind].model, rid) is None
    assert db.get(Project, project.id) is not None
    assert db.get(Task, child.id) is not None
    if kind == "task":
        assert db.get(Task, child.id).parent_task_id is None
    if kind == "requirement_section":
        assert db.get(Requirement, requirement.id).section_id is None
    if kind == "document_attachment":
        assert db.get(ProjectDocument, identity) is not None


def test_literal_search_paging_and_request_validation(client, context):
    """ごみ箱検索もLIKEメタ文字をリテラルで扱い、範囲外入力を拒否する。"""
    _, _, base, _ = context
    assert client.get(base + "/trash", params={"q": "%"}).json()["total"] == 0
    for params in (
        {"page": 501},
        {"page_size": 51},
        {"q": "a" * 201},
        {"kind": "user"},
    ):
        assert client.get(base + "/trash", params=params).status_code == 422
    row = item(client, base)
    for identity in ("-1", "0", "2147483648", "abc"):
        invalid = {**row, "id": identity}
        assert restore(client, base, invalid).status_code == 404
    assert (
        client.post(
            base + "/trash/document/1/restore",
            json={"deleted_at": "2026-01-01T00:00:00"},
        ).status_code
        == 422
    )


def test_concurrent_restore_and_stale_cleanup(client, context, db):
    """二重復元は一回だけ成功し、回収候補で生存行を消さない。"""
    _, project, base, identity = context
    row = item(client, base)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: restore(client, base, row), range(2)))
    assert sorted(r.status_code for r in responses) == [204, 404]
    assert not TrashService().purge_one(
        db,
        {"kind": "document", "project_id": project.id, "id": str(identity)},
        datetime.now(UTC),
    )


def test_test_design_graph_evidence_and_related_task_purge(
    client, context, db, create_test_task
):
    """実際のグラフ・ケース・実行・証跡を回収し、不具合タスクを残す。"""
    user, project, base, _ = context
    design = client.post(base + "/test-designs", json={"name": "回収する設計"}).json()
    url = base + f"/test-designs/{design['id']}"
    assert client.put(url, json=graph(design)).status_code == 200
    cases = client.post(url + "/cases/generate", json={"version": 2}).json()
    execution = ExecutionModel(
        case_id=cases[0]["id"], run_number=1, status="ng", source=cases[0]["source"]
    )
    db.add(execution)
    db.flush()
    evidence = EvidenceModel(
        execution_id=execution.id,
        storage_key="trash/evidence.png",
        filename="result.png",
        content_type="image/png",
        byte_size=4,
        uploaded_by=user.id,
    )
    db.add(evidence)
    db.flush()
    task = create_test_task(project=project)
    task_id = task.id
    db.add(
        CaseIssueModel(
            case_id=cases[0]["id"],
            task_id=task_id,
            origin_execution_id=execution.id,
            created_by=user.id,
        )
    )
    db.get(DesignModel, design["id"]).deleted_at = datetime.now(UTC) - timedelta(
        days=31
    )
    db.commit()
    evidence_id = evidence.id
    s3 = MemoryS3Client()
    s3.put_object(Key="trash/evidence.png", Body=b"test", ContentType="image/png")
    assert TrashService(StorageService(s3)).purge_one(
        db,
        {"kind": "test_design", "id": str(design["id"]), "project_id": project.id},
        datetime.now(UTC),
    )
    db.expire_all()
    assert db.get(DesignModel, design["id"]) is None
    assert db.get(EvidenceModel, evidence_id) is None
    assert db.get(Task, task_id) is not None
    assert "trash/evidence.png" not in s3.objects


def test_demo_restore_and_expiry_keep_session_cleanup(
    client, db, monkeypatch, demo_settings
):
    """デモの復元を一時組織に限定し、30日保持へ延長しない。"""
    from app.models.demo import DemoSession
    from tests.routers.test_demo import start

    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "frontend_public_url", "http://testserver")
    monkeypatch.setattr(settings, "demo_cron_secret", "c" * 48)
    status = start(client)
    project = db.scalar(select(Project).where(Project.tenant_id == status["tenant_id"]))
    base = f"/projects/{project.id}"
    doc = client.post(base + "/documents", json={"title": "一時データ"}).json()
    assert (
        client.delete(
            base + f"/documents/{doc['id']}", params={"version": 1}
        ).status_code
        == 204
    )
    row = item(client, base)
    assert TrashService().due(db, status["tenant_id"], 20, datetime.now(UTC)) == []
    assert restore(client, base, row).status_code == 204
    db.get(ProjectDocument, doc["id"]).deleted_at = datetime.now(UTC) - timedelta(
        days=31
    )
    db.commit()
    assert TrashService().due(db, status["tenant_id"], 20, datetime.now(UTC)) == []
    demo = db.scalar(
        select(DemoSession).where(DemoSession.tenant_id == status["tenant_id"])
    )
    demo.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert client.get(base + "/trash").status_code == 401


@pytest.mark.parametrize("operation", ["read", "delete", "update"])
def test_custom_permission_changes_are_applied_at_restore(
    client, context, db, operation
):
    """表示後の資源別権限変更を復元APIでも再確認する。"""
    from app.models.rbac import Permission, Role, RolePermission

    _, _, base, _ = context
    row = item(client, base)
    grant = db.scalar(
        select(RolePermission)
        .join(Role, Role.id == RolePermission.role_id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(
            Role.key == "project_admin",
            Role.scope == "project",
            Permission.code == f"document:{operation}",
        )
    )
    db.delete(grant)
    db.commit()
    assert restore(client, base, row).status_code == 403
    response = client.get(base + "/trash", params={"kind": "document"}).json()
    if operation == "update":
        assert response["total"] == 1 and not response["items"][0]["can_restore"]
    else:
        assert response["total"] == 0


def test_trash_query_count_does_not_grow_with_rows(client, context, db):
    """権限照会はリクエスト内で共有し、行ごとのDB呼び出しを増やさない。"""
    from sqlalchemy import event

    from app.db.session import engine

    user, project, base, _ = context
    statements = []

    def record(*args):
        statements.append(args[2])

    event.listen(engine, "before_cursor_execute", record)
    try:
        assert client.get(base + "/trash").status_code == 200
        small = len(statements)
        for i in range(12):
            db.add(
                ProjectDocument(
                    project_id=project.id,
                    title=f"追加{i}",
                    body="",
                    created_by=user.id,
                    updated_by=user.id,
                    deleted_at=datetime.now(UTC),
                )
            )
        db.commit()
        statements.clear()
        response = client.get(base + "/trash")
        assert response.json()["total"] == 13
        assert len(statements) == small
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_cleanup_tenant_scope_and_dry_run(client, context, db):
    """指定組織だけを候補とし、標準のdry-runでは期限後の行も消さない。"""
    from app.models.tenant import Tenant
    from scripts.cleanup_deleted_data import cleanup_deleted_data

    _, project, _, identity = context
    other_tenant = Tenant(name="別組織", slug="trash-other")
    db.add(other_tenant)
    db.flush()
    other_project = Project(
        tenant_id=other_tenant.id, project_code="OTHER", name="別組織の案件"
    )
    db.add(other_project)
    db.flush()
    other_task = Task(
        project_id=other_project.id,
        task_code="SECRET",
        title="他組織の削除データ",
        deleted_at=datetime.now(UTC) - timedelta(days=31),
    )
    db.add(other_task)
    db.get(ProjectDocument, identity).deleted_at = datetime.now(UTC) - timedelta(
        days=31
    )
    db.commit()
    targets = TrashService().due(db, project.tenant_id, 20, datetime.now(UTC))
    assert len(targets) == 1 and targets[0]["id"] == str(identity)
    assert cleanup_deleted_data(tenant_id=project.tenant_id) == 1
    db.expire_all()
    assert db.get(ProjectDocument, identity) is not None
    assert db.get(Task, other_task.id) is not None


@pytest.mark.parametrize("role,allowed", [("viewer", False), ("member", True)])
def test_restore_attachment_uses_edit_permission_and_reopens_download(
    client,
    context,
    db,
    monkeypatch,
    create_test_user,
    assign_project_role,
    role,
    allowed,
):
    """文書添付は既存の編集権限で戻せ、ダウンロード経路も再開する。"""
    from app.routers import documents

    _, project, base, identity = context
    assert restore(client, base, item(client, base)).status_code == 204
    s3 = MemoryS3Client()
    monkeypatch.setattr(documents.attachments, "storage", StorageService(s3))
    url = base + f"/documents/{identity}/attachments"
    response = client.post(url, files={"file": ("test.txt", b"test", "text/plain")})
    assert response.status_code == 201, response.text
    attachment_id = response.json()["id"]
    assert client.delete(url + f"/{attachment_id}").status_code == 204
    deleted = item(client, base, "document_attachment", attachment_id)
    assert deleted["version"] is None
    user = create_test_user(email="trash-attachment@example.com")
    assign_project_role(user=user, project=project, role_key=role)
    authorize_as(client, user)
    assert restore(client, base, deleted).status_code == (204 if allowed else 403)
    assert client.get(url + f"/{attachment_id}/download").status_code == (
        200 if allowed else 404
    )
