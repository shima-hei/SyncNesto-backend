"""提出・版・権限・DB封印を実PostgreSQLで検証する。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from app.models.document import DocumentAttachment, ProjectDocument
from app.models.rbac import Permission, RolePermission
from app.models.verification_evidence import VerificationEvidenceSet as EvidenceSet
from app.routers.verification_evidence import service
from tests.helpers.auth import authorize_as
from tests.routers.test_requirement_verifications import context as context
from tests.routers.test_requirement_verifications import (
    manual_row,
    update,
)
from tests.routers.test_requirement_verifications import (
    prepared as prepared,
)


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "requirement_verification_preparation_enabled", True)
    monkeypatch.setattr(settings, "requirement_verification_evidence_enabled", True)


@pytest.fixture
def evidence(client, prepared):
    c = prepared
    row = manual_row()
    response, _ = update(client, c, [row])
    assert response.status_code == 200, response.text
    c["plan"] = response.json()
    c["evidence"] = c["detail"] + "/evidence-sets"
    c["selection"] = dict(
        plan_version=2,
        entries=[
            dict(
                id=str(uuid4()),
                row_id=row["id"],
                kind="manual",
                reason="固定した要件本文と照合した記録",
                checked_content="戻った後の入力保持",
                actual_result="入力が残った",
                result="passed",
                reported_at=datetime.now(UTC).isoformat(),
            )
        ],
    )
    return c


def create(client, c, selection=None):
    selection = selection or c["selection"]
    preview = client.post(c["evidence"] + "/preview", json=selection)
    assert preview.status_code == 200, preview.text
    data = dict(
        selection,
        request_id=str(uuid4()),
        expected_preview_digest=preview.json()["preview_digest"],
    )
    response = client.post(c["evidence"], json=data)
    assert response.status_code == 201, response.text
    return response.json(), data


def attach(db, c):
    doc = ProjectDocument(
        project_id=c["project"].id,
        title="確認記録",
        body="",
        created_by=c["user"].id,
        updated_by=c["user"].id,
    )
    db.add(doc)
    db.flush()
    file = DocumentAttachment(
        document_id=doc.id,
        filename="evidence.txt",
        content_type="text/plain",
        byte_size=5,
        storage_key="isolated/" + uuid4().hex,
        uploaded_by=c["user"].id,
    )
    db.add(file)
    db.commit()
    c["selection"]["entries"][0]["files"] = [
        dict(kind="document_attachment", id=str(file.id))
    ]
    return file


def test_text_ready_preview_no_write_idempotency_and_detail(client, evidence, db):
    c = evidence
    preview = client.post(c["evidence"] + "/preview", json=c["selection"])
    assert (
        preview.status_code == 200
        and db.scalar(select(func.count()).select_from(EvidenceSet)) == 0
    )
    row, data = create(client, c)
    assert row["status"] == "ready" and row["plan_version"] == 2
    same = client.post(c["evidence"], json=data)
    assert same.status_code == 200 and same.json()["id"] == row["id"]
    assert (
        client.post(
            c["evidence"], json={**data, "expected_preview_digest": "0" * 64}
        ).status_code
        == 409
    )
    detail = client.get(c["evidence"] + "/" + row["id"])
    assert detail.status_code == 200, detail.text
    assert detail.headers["cache-control"] == "private, no-store"
    assert detail.json()["comparison"]["ready_means"] == "preserved_only"
    entry = client.get(
        c["evidence"]
        + "/"
        + row["id"]
        + "/entries/"
        + c["selection"]["entries"][0]["id"]
    )
    assert (
        entry.status_code == 200
        and entry.json()["entry"]["actual_result"] == "入力が残った"
    )
    assert entry.json()["actor_name"] == c["user"].name
    assert "storage_key" not in detail.text and "lease" not in detail.text


@pytest.mark.parametrize(
    "change",
    ["empty", "wrong_row", "no_reason", "on_behalf", "missing_result", "duplicate"],
)
def test_invalid_entries_are_not_saved(client, evidence, change):
    c = evidence
    import copy

    data = copy.deepcopy(c["selection"])
    entry = data["entries"][0]
    if change == "empty":
        data["entries"] = []
    elif change == "wrong_row":
        entry["row_id"] = str(uuid4())
    elif change == "no_reason":
        entry["reason"] = "  "
    elif change == "on_behalf":
        entry["performed_by"] = 999
    elif change == "missing_result":
        entry["actual_result"] = ""
    elif change == "duplicate":
        data["entries"] *= 2
    assert client.post(c["evidence"] + "/preview", json=data).status_code in {400, 422}


def test_na_is_explicit_and_saved_revision_does_not_follow_current(client, evidence):
    c = evidence
    row_id = c["selection"]["entries"][0]["row_id"]
    c["selection"]["entries"] = [
        dict(
            id=str(uuid4()),
            row_id=row_id,
            kind="not_applicable",
            reason="対象端末がないため範囲外と確認",
        )
    ]
    saved, _ = create(client, c)
    response, _ = update(
        client, c, [manual_row(id=row_id, description="更新後の確認表")], version=2
    )
    assert response.status_code == 200
    detail = client.get(c["evidence"] + "/" + saved["id"]).json()
    assert (
        detail["plan_version"] == 2 and detail["comparison"]["checklist"] == "changed"
    )
    assert detail["entries"][0]["result"] is None
    for action in ["cancel", "advance"]:
        assert (
            client.post(
                c["evidence"] + "/" + saved["id"] + "/" + action,
                json={"version": saved["version"]},
            ).status_code
            == 409
        )


@pytest.mark.parametrize(
    "role,submit", [("member", True), ("viewer", False), ("manager", True)]
)
def test_project_roles(
    client, evidence, create_test_user, assign_project_role, role, submit
):
    c = evidence
    user = create_test_user(email=f"{role}@example.test")
    assign_project_role(user=user, project=c["project"], role_key=role)
    authorize_as(client, user)
    page = client.get(c["evidence"])
    assert page.status_code == 200 and page.json()["can_submit"] == submit
    response = client.post(c["evidence"] + "/preview", json=c["selection"])
    assert response.status_code == (200 if submit else 403)


def test_flags_and_public_environment(client, evidence, monkeypatch):
    c = evidence
    row, _ = create(client, c)
    monkeypatch.setattr(settings, "requirement_verification_evidence_enabled", False)
    assert client.get(c["evidence"]).json()["state"] == "disabled"
    assert client.get(c["evidence"] + "/" + row["id"]).status_code == 404
    monkeypatch.setattr(settings, "requirement_verification_evidence_enabled", True)
    monkeypatch.setattr(settings, "app_env", "production")
    assert client.get(c["evidence"]).status_code == 403


def test_unfinished_limit_cancel_and_audit_rollback(client, evidence, db, monkeypatch):
    c = evidence
    attach(db, c)
    rows = [create(client, c)[0] for _ in range(2)]
    preview = client.post(c["evidence"] + "/preview", json=c["selection"]).json()
    response = client.post(
        c["evidence"],
        json=dict(
            c["selection"],
            request_id=str(uuid4()),
            expected_preview_digest=preview["preview_digest"],
        ),
    )
    assert response.status_code == 409
    url = c["evidence"] + "/" + rows[0]["id"]
    assert (
        client.post(url + "/cancel", json={"version": rows[0]["version"]}).json()[
            "status"
        ]
        == "cancelled"
    )
    from app.services.audit_log import AuditLogService

    def fail(*args, **kwargs):
        raise RuntimeError("isolated audit failure")

    monkeypatch.setattr(AuditLogService, "record", fail)
    with pytest.raises(RuntimeError):
        create(client, c)
    assert db.scalar(select(func.count()).select_from(EvidenceSet)) == 2


def test_document_permission_filters_sql_and_detail(client, evidence, db):
    c = evidence
    text_set, _ = create(client, c)
    attach(db, c)
    with_file, _ = create(client, c)
    permission = db.scalar(
        select(Permission.id).where(Permission.code == "document:read")
    )
    db.execute(delete(RolePermission).where(RolePermission.permission_id == permission))
    db.commit()
    page = client.get(c["evidence"]).json()
    assert page["document_access_limited"] is True
    assert [r["id"] for r in page["items"]] == [text_set["id"]]
    assert client.get(c["evidence"] + "/" + with_file["id"]).status_code == 403


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM requirement_verification_evidence_sets WHERE id=:id",
        "UPDATE requirement_verification_evidence_sets "
        "SET actor_name='changed' WHERE id=:id",
        "UPDATE requirement_verification_evidence_sets "
        "SET status='cancelled',version=version+1 WHERE id=:id",
    ],
)
def test_ready_database_immutable(client, evidence, db, sql):
    row, _ = create(client, evidence)
    with pytest.raises(DBAPIError), db.get_bind().engine.begin() as connection:
        connection.execute(text(sql), {"id": UUID(row["id"])})


def test_copy_reservation_failure_retry_success_and_download(
    client, evidence, db, monkeypatch
):
    c = evidence
    attach(db, c)
    saved, _ = create(client, c)
    url = c["evidence"] + "/" + saved["id"]

    class Storage:
        failed = True

        def copy(self, file, key):
            from app.core.exceptions import SharedFileUnavailableError

            if self.failed:
                raise SharedFileUnavailableError()
            return dict(
                destination_version="isolated-version-1",
                sha256="a" * 64,
                byte_size=file["byte_size"],
            )

        def verify(self, *args):
            return None

        def sign(self, *args):
            return dict(
                url="http://127.0.0.1/isolated",
                expires_in=59,
                expires_at=datetime.now(UTC) + timedelta(seconds=59),
            )

    storage = Storage()
    monkeypatch.setattr(service, "storage_factory", lambda: storage)
    failed = client.post(url + "/advance", json={"version": saved["version"]})
    assert failed.status_code == 200 and failed.json()["status"] == "failed", (
        failed.text
    )
    storage.failed = False
    ready = client.post(url + "/advance", json={"version": failed.json()["version"]})
    assert ready.status_code == 200 and ready.json()["status"] == "ready", ready.text
    detail = client.get(url)
    assert detail.status_code == 200, detail.text
    file_id = detail.json()["files"][0]["file_id"]
    assert detail.json()["files"][0]["sha256"] == "a" * 64
    download = client.post(url + "/files/" + file_id + "/download")
    assert download.status_code == 200 and download.json()["expires_in"] <= 60, (
        download.text
    )


def test_execution_requires_current_latest_and_explicit_reconfirmation(
    client, evidence, db
):
    from app.models.test_design import RequirementTestItem
    from tests.routers.test_test_designs import graph

    c = evidence
    root = f"/projects/{c['project'].id}/test-designs"
    design = client.post(root, json={"name": "証拠のテスト"}).json()
    url = root + f"/{design['id']}"
    data = graph(design)
    assert client.put(url, json=data).status_code == 200
    item_id = data["items"][0]["id"]
    db.add(
        RequirementTestItem(
            requirement_id=c["req"].id, item_id=UUID(item_id), created_by=c["user"].id
        )
    )
    db.commit()
    row = manual_row(
        method="test", targets=[{"item_id": item_id, "pattern_scope": "all_enabled"}]
    )
    response, _ = update(client, c, [row], version=2)
    assert response.status_code == 200
    case = next(
        x
        for x in client.get(url + "/cases").json()
        if x["source"]["item"]["id"] == item_id
    )
    assert (
        client.patch(
            url + f"/cases/{case['id']}",
            json={
                "version": case["version"],
                "status": "passed",
                "actual_result": "元の実結果",
            },
        ).status_code
        == 200
    )
    candidate = next(
        x
        for x in client.get(
            c["detail"] + "/execution-candidates", params={"row_id": row["id"]}
        ).json()["items"]
        if x["case_id"] == case["id"]
    )
    c["selection"] = dict(
        plan_version=3,
        entries=[
            dict(
                id=str(uuid4()),
                row_id=row["id"],
                kind="execution",
                execution_id=candidate["execution_id"],
                reconfirmed=True,
                reason="今この固定要件と対応を再確認",
            )
        ],
    )
    saved, _ = create(client, c)
    record = client.get(
        c["evidence"]
        + "/"
        + saved["id"]
        + "/entries/"
        + c["selection"]["entries"][0]["id"]
    ).json()
    assert record["entry"]["execution"]["actual_result"] == "元の実結果"
    assert record["entry"]["execution"]["requirement_version_recorded"] is False
    data["version"] = 2
    data["items"][0]["content"] = "変更後のテスト内容"
    assert client.put(url, json=data).status_code == 200
    assert (
        client.post(c["evidence"] + "/preview", json=c["selection"]).status_code == 409
    )
    c["selection"]["entries"][0]["use"] = "historical_record"
    assert (
        client.post(c["evidence"] + "/preview", json=c["selection"]).status_code == 200
    )
    latest_case = next(
        x for x in client.get(url + "/cases").json() if x["id"] == case["id"]
    )
    assert (
        client.patch(
            url + f"/cases/{case['id']}",
            json={"version": latest_case["version"], "status": "failed"},
        ).status_code
        == 200
    )
    assert (
        client.post(c["evidence"] + "/preview", json=c["selection"]).status_code == 400
    )
    assert client.get(c["evidence"] + "/" + saved["id"]).json()["status"] == "ready"


@pytest.mark.parametrize(
    "mutation,expected",
    [("deleted", 200), ("permission", 403), ("cancelled", 409), ("lease", 409)],
)
def test_copy_does_not_seal_after_concurrent_changes(
    client, evidence, db, monkeypatch, mutation, expected
):
    c = evidence
    file = attach(db, c)
    saved, _ = create(client, c)
    url = c["evidence"] + "/" + saved["id"]

    class Storage:
        def copy(self, source, key):
            if mutation == "deleted":
                file.deleted_at = datetime.now(UTC)
                db.commit()
            elif mutation == "permission":
                permission = db.scalar(
                    select(Permission.id).where(
                        Permission.code == "requirement_verification:submit"
                    )
                )
                db.execute(
                    delete(RolePermission).where(
                        RolePermission.permission_id == permission
                    )
                )
                db.commit()
            elif mutation == "cancelled":
                current = client.get(url).json()
                assert (
                    client.post(
                        url + "/cancel", json={"version": current["version"]}
                    ).status_code
                    == 200
                )
            else:
                db.execute(
                    text(
                        "UPDATE requirement_verification_evidence_sets "
                        "SET version=version+1,lease_expires_at="
                        "clock_timestamp()-interval '1 second' WHERE id=:id"
                    ),
                    {"id": saved["id"]},
                )
                db.commit()
            return dict(destination_version="old-attempt", sha256="a" * 64, byte_size=5)

    monkeypatch.setattr(service, "storage_factory", Storage)
    response = client.post(url + "/advance", json={"version": saved["version"]})
    assert response.status_code == expected, response.text
    db.expire_all()
    assert db.get(EvidenceSet, UUID(saved["id"])).status != "ready"


def test_unfinished_owner_boundary_and_other_manager_resume(
    client, evidence, db, create_test_user, assign_project_role, monkeypatch
):
    c = evidence
    attach(db, c)
    saved, _ = create(client, c)
    url = c["evidence"] + "/" + saved["id"]
    member = create_test_user(email="other-member@example.test")
    assign_project_role(user=member, project=c["project"], role_key="member")
    authorize_as(client, member)
    assert (
        client.post(url + "/cancel", json={"version": saved["version"]}).status_code
        == 403
    )
    manager = create_test_user(email="other-manager@example.test")
    assign_project_role(user=manager, project=c["project"], role_key="manager")
    authorize_as(client, manager)
    cancelled = client.post(url + "/cancel", json={"version": saved["version"]})
    assert (
        cancelled.status_code == 200
        and cancelled.json()["actor_name"] == c["user"].name
    )
