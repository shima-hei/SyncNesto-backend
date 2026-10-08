"""Cronの認可、組織指定、回収上限、多重実行と失敗後の再試行。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.core.config import settings
from app.db.session import session_local
from app.models.audit_log import AuditLog
from app.models.demo import DemoSession
from app.models.document import DocumentAttachment, ProjectDocument
from app.models.project import Project
from app.models.task import Task
from app.models.tenant import Tenant
from app.schemas.deleted_data_cleanup import DeletedDataCleanupResult
from app.services import deleted_data_cleanup as cleanup_module
from app.services import trash as trash_module
from app.services.deleted_data_cleanup import CLEANUP_LOCK, DeletedDataCleanupService
from app.services.storage import StorageService
from tests.fakes.storage import MemoryS3Client

PATH = "/internal/trash/cleanup"
SECRET = "cleanup-test-secret-" * 3
HEADERS = {"Authorization": f"Bearer {SECRET}"}


def configure(monkeypatch, tenant_ids="1", mode="dry_run"):
    """資格情報と対象はサーバー設定で固定する。"""
    monkeypatch.setattr(settings, "deleted_data_cleanup_mode", mode)
    monkeypatch.setattr(settings, "deleted_data_cleanup_tenant_ids", tenant_ids)
    monkeypatch.setattr(settings, "deleted_data_cleanup_limit", 20)
    monkeypatch.setattr(settings, "deleted_data_cleanup_budget_seconds", 20)
    monkeypatch.setattr(settings, "deleted_data_retention_days", 30)
    monkeypatch.setattr(settings, "demo_cron_secret", SECRET)
    monkeypatch.setattr(settings, "bff_shared_secret", "bff-test-secret-" * 3)


@pytest.mark.no_db
def test_cron_secret_is_required_before_any_database_work(client, monkeypatch):
    """Cookie・BFF共有キー・利用者指定のqueryでは回収を認可しない。"""
    configure(monkeypatch)
    calls = []
    monkeypatch.setattr(
        DeletedDataCleanupService,
        "run",
        lambda self: calls.append(True) or DeletedDataCleanupResult(mode="dry_run"),
    )
    client.cookies.set(settings.auth_cookie_name, "user-cookie")
    for headers in (
        {},
        {"Authorization": "Bearer wrong"},
        {
            "X-Syncnesto-BFF-Key": settings.bff_shared_secret,
            "Authorization": f"Bearer {settings.bff_shared_secret}",
        },
    ):
        assert client.get(PATH, headers=headers).status_code == 403
    assert not calls
    response = client.get(PATH, headers=HEADERS)
    assert response.status_code == 200 and calls == [True]
    assert response.headers["Cache-Control"] == "private, no-store"
    monkeypatch.setattr(settings, "deleted_data_cleanup_mode", "disabled")
    assert client.get(PATH, headers=HEADERS).status_code == 404
    configure(monkeypatch)
    monkeypatch.setattr(settings, "app_env", "demo")
    assert client.get(PATH, headers=HEADERS).status_code == 404
    assert PATH not in client.app.openapi()["paths"]


@pytest.mark.no_db
@pytest.mark.parametrize(
    "changes",
    [
        {"deleted_data_cleanup_mode": "Execute"},
        {"deleted_data_cleanup_tenant_ids": ""},
        {"deleted_data_cleanup_tenant_ids": "0,1"},
        {"deleted_data_cleanup_tenant_ids": "1,,2"},
        {"deleted_data_cleanup_tenant_ids": "2147483648"},
        {"deleted_data_cleanup_tenant_ids": ",".join(str(i) for i in range(1, 22))},
        {"deleted_data_cleanup_limit": 101},
        {"deleted_data_cleanup_budget_seconds": 41},
        {"deleted_data_retention_days": 0},
        {"demo_cron_secret": "short"},
        {"demo_cron_secret": "bff-secret" * 5},
        {"app_env": "demo"},
    ],
)
def test_unsafe_schedule_configuration_fails_closed(changes):
    """有効化には通常環境・有限保持・組織指定・専用秘密を必須にする。"""
    base = replace(
        settings,
        app_env="test",
        deleted_data_cleanup_mode="dry_run",
        deleted_data_cleanup_tenant_ids="1,2",
        deleted_data_cleanup_limit=20,
        deleted_data_cleanup_budget_seconds=20,
        deleted_data_retention_days=30,
        demo_cron_secret=SECRET,
        bff_shared_secret="bff-secret" * 5,
    )
    base.validate_cleanup()
    with pytest.raises(RuntimeError):
        replace(base, **changes).validate_cleanup()


@pytest.fixture
def data(db, client, monkeypatch):
    """通常2組織・対象外組織・デモに、期限前後の行を置く。"""
    now = datetime.now(UTC)
    tenants = [Tenant(name=f"組織{i}", slug=f"cleanup-{i}") for i in range(4)]
    db.add_all(tenants)
    db.flush()
    projects = [
        Project(tenant_id=t.id, project_code=f"C{i}", name="案件")
        for i, t in enumerate(tenants)
    ]
    db.add_all(projects)
    db.flush()
    old = [
        Task(
            project_id=p.id,
            task_code="OLD",
            title="SECRET-CONTENT",
            deleted_at=now - timedelta(days=31),
        )
        for p in projects
    ]
    fresh = Task(
        project_id=projects[0].id,
        task_code="FRESH",
        title="期限前",
        deleted_at=now - timedelta(days=29),
    )
    live = Task(project_id=projects[0].id, task_code="LIVE", title="有効")
    deleted_project = Project(
        tenant_id=tenants[0].id,
        project_code="DELETED",
        name="削除案件",
        deleted_at=now - timedelta(days=40),
    )
    db.add(deleted_project)
    db.flush()
    hidden = Task(
        project_id=deleted_project.id,
        task_code="HIDDEN",
        title="案件削除済み",
        deleted_at=now - timedelta(days=40),
    )
    db.add_all(
        [
            *old,
            fresh,
            live,
            hidden,
            DemoSession(
                tenant_id=tenants[3].id,
                created_at=now,
                expires_at=now,
                absolute_expires_at=now,
                cleanup_after=now,
            ),
        ]
    )
    db.commit()
    configure(
        monkeypatch, ",".join(str(t.id) for t in (tenants[0], tenants[1], tenants[3]))
    )
    s3 = MemoryS3Client()
    monkeypatch.setattr(
        cleanup_module, "StorageService", lambda **kw: StorageService(s3)
    )
    return tenants, projects, old, (fresh, live, hidden), s3


def test_dry_run_execute_and_repeated_delivery_preserve_scope(
    client, db, data, monkeypatch
):
    """queryの組織指定を無視し、対象確認と実行を監査に区別して記録する。"""
    tenants, _, old, keep, _ = data
    response = client.get(
        PATH, headers=HEADERS, params={"tenant_id": tenants[2].id, "execute": "true"}
    )
    assert response.status_code == 200
    assert response.json()["candidate_count"] == 2
    assert response.json()["purged_count"] == 0
    db.expire_all()
    assert all(db.get(Task, row.id) for row in [*old, *keep])
    audits = list(
        db.scalars(select(AuditLog).where(AuditLog.event_type == "trash.cleanup"))
    )
    assert {row.tenant_id for row in audits} == {t.id for t in tenants[:2]}
    assert all(row.extra_metadata["mode"] == "dry_run" for row in audits)
    assert "SECRET-CONTENT" not in response.text
    old_ids = [row.id for row in old]
    monkeypatch.setattr(settings, "deleted_data_cleanup_mode", "execute")
    response = client.get(PATH, headers=HEADERS)
    assert response.status_code == 200 and response.json()["purged_count"] == 2
    db.expire_all()
    assert all(db.get(Task, id) is None for id in old_ids[:2])
    assert all(db.get(Task, id) for id in old_ids[2:])
    assert all(db.get(Task, row.id) for row in keep)
    assert client.get(PATH, headers=HEADERS).json()["purged_count"] == 0
    purged_logs = list(
        db.scalars(select(AuditLog).where(AuditLog.event_type == "task.purged"))
    )
    assert {row.resource_id for row in purged_logs} == set(old_ids[:2])


def test_global_limit_and_transaction_lock_are_released(client, db, data, monkeypatch):
    """各組織20件ではなく全体上限を使い、commit後もジョブの競合を防ぐ。"""
    monkeypatch.setattr(settings, "deleted_data_cleanup_mode", "execute")
    monkeypatch.setattr(settings, "deleted_data_cleanup_limit", 1)
    with session_local() as lock_db:
        lock_db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": CLEANUP_LOCK}
        )
        response = client.get(PATH, headers=HEADERS)
        assert response.json()["status"] == "busy"
        assert response.json()["purged_count"] == 0
    response = client.get(PATH, headers=HEADERS)
    assert response.json()["purged_count"] == 1 and response.json()["has_more"]
    assert client.get(PATH, headers=HEADERS).json()["purged_count"] == 1


def add_document(db, project, user, s3, keys):
    """期限後文書のDB行と添付を同時に確認する。"""
    doc = ProjectDocument(
        project_id=project.id,
        title="文書",
        body="SECRET",
        created_by=user.id,
        updated_by=user.id,
        deleted_at=datetime.now(UTC) - timedelta(days=33),
    )
    db.add(doc)
    db.flush()
    for key in keys:
        db.add(
            DocumentAttachment(
                document_id=doc.id,
                filename="添付.txt",
                content_type="text/plain",
                byte_size=4,
                storage_key=key,
                uploaded_by=user.id,
            )
        )
        s3.put_object(Key=key, Body=b"data", ContentType="text/plain")
    db.commit()
    return doc.id


def test_storage_failure_is_sanitized_and_retried(
    client, db, data, monkeypatch, create_test_user, caplog
):
    """失敗した文書を残し、別の候補を回収し、再実行でファイルまで回収する。"""
    _, projects, _, _, s3 = data
    user = create_test_user()
    doc_id = add_document(db, projects[0], user, s3, ["cleanup/a.txt"])
    monkeypatch.setattr(settings, "deleted_data_cleanup_mode", "execute")
    original = s3.delete_object

    def fail(**kwargs):
        raise RuntimeError("SECRET-STORAGE-KEY")

    monkeypatch.setattr(s3, "delete_object", fail)
    response = client.get(PATH, headers=HEADERS)
    assert response.status_code == 503
    assert response.json()["failed_count"] == 1 and response.json()["purged_count"] == 2
    assert "SECRET-STORAGE-KEY" not in caplog.text + response.text
    db.expire_all()
    assert db.get(ProjectDocument, doc_id) and "cleanup/a.txt" in s3.objects
    monkeypatch.setattr(s3, "delete_object", original)
    assert client.get(PATH, headers=HEADERS).json()["purged_count"] == 1
    db.expire_all()
    assert db.get(ProjectDocument, doc_id) is None and not s3.objects


def test_time_budget_between_files_keeps_rows_for_next_run(
    client, db, data, monkeypatch, create_test_user
):
    """途中のファイルだけ回収済みでも、DBは残して次回に完了させる。"""
    _, projects, _, _, s3 = data
    doc_id = add_document(
        db, projects[0], create_test_user(), s3, ["cleanup/a.txt", "cleanup/b.txt"]
    )
    monkeypatch.setattr(settings, "deleted_data_cleanup_mode", "execute")
    clock = [0.0]
    monkeypatch.setattr(cleanup_module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(trash_module, "monotonic", lambda: clock[0])
    original = s3.delete_object

    def advance_clock(**kwargs):
        value = original(**kwargs)
        clock[0] = 30.0
        return value

    monkeypatch.setattr(s3, "delete_object", advance_clock)
    response = client.get(PATH, headers=HEADERS)
    assert response.status_code == 200 and response.json()["has_more"]
    assert response.json()["purged_count"] == 0
    db.expire_all()
    assert db.get(ProjectDocument, doc_id) is not None and len(s3.objects) == 1
    clock[0] = 0.0
    monkeypatch.setattr(s3, "delete_object", original)
    assert client.get(PATH, headers=HEADERS).json()["purged_count"] == 3
    db.expire_all()
    assert db.get(ProjectDocument, doc_id) is None and not s3.objects
