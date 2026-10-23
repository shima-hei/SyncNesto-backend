"""旧linksと独立パターン表の現在内容・実行内容の比較回帰。"""

import json
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event

from app.db.session import engine
from app.models.test_design import RequirementTestItem, TestItem
from tests.routers.test_artifact_releases import context as context
from tests.routers.test_requirement_verifications import enabled as enabled
from tests.routers.test_requirement_verifications import manual_row, update
from tests.routers.test_requirement_verifications import prepared as prepared
from tests.routers.test_test_designs import graph


@pytest.mark.parametrize("mode", ["legacy", "table"])
def test_compare_execution_after_save_and_renumber(client, prepared, db, mode):
    c = prepared
    root = f"/projects/{c['project'].id}/test-designs"
    design = client.post(root, json={"name": "確認候補"}).json()
    url = root + f"/{design['id']}"
    data = graph(design)
    if mode == "table":
        table = str(uuid4())
        data["pattern_tables"] = [{"id": table, "name": "独立した表"}]
        data["items"][0]["pattern_table_id"] = table
        for p in data["patterns"]:
            p["table_id"] = table
        data["factors"][0]["table_id"] = table
        data["links"] = []
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
    assert update(client, c, [row])[0].status_code == 200
    endpoint = c["detail"] + "/execution-candidates"

    def read():
        response = client.get(endpoint, params={"row_id": row["id"]})
        assert response.status_code == 200, response.text
        return response.json()

    assert len(read()["items"]) == 2
    cases = client.get(url + "/cases").json()
    case = next(r for r in cases if r["source"]["item"]["id"] == item_id)
    result = client.patch(
        url + f"/cases/{case['id']}",
        json={"version": case["version"], "status": "passed"},
    )
    assert result.status_code == 200, result.text
    current = next(r for r in read()["items"] if r["case_id"] == case["id"])
    assert current["state"] == "passed" and current["comparison"] == "same"
    assert current["requirement_version_recorded"] is False
    data["version"] = 2
    data["items"][0]["code"] = "T900"
    assert client.put(url, json=data).status_code == 200
    current = next(r for r in read()["items"] if r["case_id"] == case["id"])
    assert current["comparison"] == "same"
    data["version"] = 3
    data["items"][0]["content"] = "変更後の試験内容"
    assert client.put(url, json=data).status_code == 200
    current = next(r for r in read()["items"] if r["case_id"] == case["id"])
    assert current["state"] == "passed" and current["comparison"] == "changed"
    assert client.get(endpoint, params={"row_id": str(uuid4())}).status_code == 404
    # 直近のNG/保留/対象外を、古いOKより優先して表示する。
    for status in ("failed", "blocked", "not_applicable"):
        latest_case = next(
            r for r in client.get(url + "/cases").json() if r["id"] == case["id"]
        )
        assert (
            client.patch(
                url + f"/cases/{case['id']}",
                json={"version": latest_case["version"], "status": status},
            ).status_code
            == 200
        )
        assert (
            next(r for r in read()["items"] if r["case_id"] == case["id"])["state"]
            == status
        )
    if mode == "legacy":
        for index in range(2, 50):
            pattern = str(uuid4())
            data["patterns"].append({"id": pattern, "code": f"P{index + 1:03}"})
            data["links"].append(
                {"id": str(uuid4()), "item_id": item_id, "pattern_id": pattern}
            )
        data["version"] = 4
        assert client.put(url, json=data).status_code == 200
        measurements = []
        for count in (10, 50):
            queries = []

            def capture(conn, cursor, statement, parameters, context, executemany):
                queries.append(statement)

            event.listen(engine, "before_cursor_execute", capture)
            started = perf_counter()
            try:
                result = client.get(
                    endpoint, params={"row_id": row["id"], "page_size": count}
                )
            finally:
                event.remove(engine, "before_cursor_execute", capture)
            assert result.status_code == 200 and len(result.json()["items"]) == count, (
                result.text
            )
            business = sum(
                any(
                    name in q
                    for name in (
                        "requirement_verification",
                        "requirement_test_items",
                        "test_cases",
                        "verification_material",
                    )
                )
                for q in queries
            )
            measurements.append(
                {
                    "cases": count,
                    "sql_total": len(queries),
                    "sql_business": business,
                    "response_bytes": len(result.content),
                    "seconds": round(perf_counter() - started, 4),
                }
            )
            assert business <= 15
        assert measurements[0]["sql_total"] == measurements[1]["sql_total"]
        Path(".verify/verification-performance.json").write_text(
            json.dumps(measurements, indent=2)
        )
    item = db.get(TestItem, UUID(item_id))
    item.content = "x" * (8 * 1024 * 1024 + 1)
    db.commit()
    limited = read()
    assert limited["state"] == "not_evaluated" and limited["reason"] == "material_limit"
    assert limited["items"] == []
