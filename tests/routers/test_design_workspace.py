"""設計ワークスペースの共有正本・移行互換・認可の回帰確認。"""

from uuid import uuid4

from sqlalchemy import text

from tests.helpers.auth import authorize_as
from tests.routers.test_specifications import context as context
from tests.routers.test_specifications import create, update_data


def workspace(project):
    return f"/projects/{project.id}/design-workspace"


def diagram(client, project, kind, **params):
    response = client.get(
        f"{workspace(project)}/diagram", params={"kind": kind, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_graph_is_projection_and_layout_is_per_group(client, context):
    _, project, specs = context
    base = workspace(project)
    first, second = create(client, specs, "screen"), create(client, specs, "screen")
    response = client.post(
        f"{base}/collections", json={"kind": "group", "name": "貸出"}
    )
    assert response.status_code == 201, response.text
    group = response.json()
    sub = client.post(
        f"{base}/collections",
        json={
            "kind": "group",
            "name": "返却",
            "parent_id": group["id"],
        },
    ).json()
    assert (
        client.patch(
            f"{base}/collections/{group['id']}",
            json={
                "kind": "group",
                "name": "貸出",
                "parent_id": sub["id"],
                "version": group["version"],
            },
        ).status_code
        == 400
    )
    membership = client.put(
        f"{base}/memberships/{first['id']}",
        json={
            "version": first["version"],
            "group_ids": [group["id"]],
        },
    )
    assert membership.status_code == 200, membership.text
    first = membership.json()
    content = first["content"]
    transition = {
        "id": str(uuid4()),
        "target_id": second["id"],
        "name": "詳細へ",
        "condition": "検索結果あり",
    }
    content["transitions"] = [transition]
    updated = client.patch(f"{specs}/{first['id']}", json=update_data(first))
    assert updated.status_code == 200, updated.text
    assert diagram(client, project, "screen")["edges"] == [
        {
            "id": transition["id"],
            "source_id": first["id"],
            "target_id": second["id"],
            "name": "詳細へ",
            "kind": "transition",
            "condition": "検索結果あり",
        }
    ]
    for group_id, x in [(None, 32), (group["id"], 84)]:
        body = {
            "group_id": group_id,
            "placements": [
                {
                    "specification_id": first["id"],
                    "x": x,
                    "y": 10,
                    "hidden": not group_id,
                },
            ],
        }
        place = client.put(f"{base}/placements", json=body)
        assert place.status_code == 200, place.text
        assert client.put(f"{base}/placements", json=body).status_code == 409
    whole = diagram(client, project, "screen")
    scoped = diagram(client, project, "screen", group_id=group["id"])
    assert whole["nodes"][0]["placement"]["hidden"] is True
    assert scoped["nodes"][0]["placement"]["x"] == 84
    assert scoped["nodes"][0]["group_ids"] == [group["id"]]
    assert (
        client.get(f"{specs}/{first['id']}").json()["content"]["transitions"][0]["id"]
        == transition["id"]
    )


def test_design_project_permissions_and_references(
    client,
    context,
    create_test_project,
    create_test_user,
    assign_project_role,
):
    user, project, specs = context
    other = create_test_project()
    assign_project_role(user=user, project=other, role_key="project_admin")
    foreign = create(client, f"/projects/{other.id}/specifications", "screen")
    row = create(client, specs, "screen")
    row["content"]["transitions"] = [{"target_id": foreign["id"]}]
    assert (
        client.patch(f"{specs}/{row['id']}", json=update_data(row)).status_code == 400
    )
    reader = create_test_user(email="design-viewer@example.com")
    assign_project_role(user=reader, project=project, role_key="viewer")
    authorize_as(client, reader)
    assert client.get(f"{workspace(project)}/overview").status_code == 200
    assert (
        client.post(
            f"{workspace(project)}/collections", json={"kind": "group", "name": "組"}
        ).status_code
        == 403
    )
    assert client.get(f"{workspace(other)}/overview").status_code in {403, 404}


def test_openapi_import_selected_version_and_fixed_export(client, context):
    _, project, specs = context
    base = workspace(project)
    group = client.post(
        f"{base}/collections", json={"kind": "api_set", "name": "備品API"}
    ).json()
    document = {
        "openapi": "3.1.1",
        "info": {"title": "備品", "version": "1.0"},
        "paths": {
            "/equipment": {
                "get": {
                    "summary": "備品を取得",
                    "operationId": "listEquipment",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Equipment"}
                                },
                            },
                        }
                    },
                },
            }
        },
        "components": {
            "schemas": {
                "Equipment": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "maxLength": 100},
                    },
                }
            }
        },
    }
    inputs = {
        "collection_id": group["id"],
        "collection_version": 1,
        "document": document,
    }
    preview = client.post(f"{base}/openapi/import", json=inputs)
    assert preview.status_code == 200, preview.text
    assert preview.json()["changes"][0]["action"] == "add"
    result = client.post(
        f"{base}/openapi/import", json={**inputs, "selections": {"GET /equipment": 0}}
    )
    assert result.status_code == 200, result.text
    assert result.json()["applied"] == 1
    assert client.post(f"{base}/openapi/import", json=inputs).status_code == 409
    exported = client.get(
        f"{base}/openapi", params={"collection_id": group["id"]}
    ).json()
    assert exported["document"] == document

    design = diagram(client, project, "api")["nodes"][0]
    details = client.get(f"{specs}/{design['id']}").json()
    details["summary"] = "備品の検索に使う"
    edited = client.patch(f"{specs}/{design['id']}", json=update_data(details))
    assert edited.status_code == 200, edited.text
    approved = client.patch(
        f"{specs}/{design['id']}/status",
        json={"version": edited.json()["version"], "status": "approved"},
    )
    assert approved.status_code == 200, approved.text
    replacement = {
        **document,
        "components": {"schemas": {"Equipment": {"type": "object"}}},
    }
    assert (
        client.patch(
            f"{base}/collections/{group['id']}",
            json={
                "kind": "api_set",
                "name": "備品API",
                "version": 2,
                "configuration": {
                    key: value for key, value in replacement.items() if key != "paths"
                },
            },
        ).status_code
        == 200
    )
    fixed = client.get(
        f"{base}/openapi",
        params={"design_id": design["id"], "revision": approved.json()["version"]},
    )
    assert fixed.json()["document"] == document


def test_database_ddl_executes_in_disposable_database(client, context, db):
    _, project, specs = context
    name = "design_test_" + uuid4().hex[:10]
    first_id, second_id = str(uuid4()), str(uuid4())
    rows = []
    for table, identifier in [("equipment", first_id), ("loan", second_id)]:
        response = client.post(
            specs,
            json={
                "title": table,
                "content": {
                    "type": "database",
                    "namespace": name,
                    "physical_name": table,
                    "columns": [
                        {
                            "id": identifier,
                            "name": "ID",
                            "physical_name": "id",
                            "data_type": "bigint",
                            "nullable": False,
                        },
                        {
                            "name": "参照ID",
                            "physical_name": "related",
                            "data_type": "bigint",
                        },
                    ],
                    "constraints": [
                        {
                            "kind": "primary_key",
                            "name": f"{table}_pk",
                            "column_ids": [identifier],
                        }
                    ],
                },
            },
        )
        assert response.status_code == 201, response.text
        rows.append(response.json())
    for row, other, target_id in [
        (rows[0], rows[1], second_id),
        (rows[1], rows[0], first_id),
    ]:
        row["content"]["constraints"].append(
            {
                "kind": "foreign_key",
                "name": f"{row['title']}_fk",
                "column_ids": [row["content"]["columns"][1]["id"]],
                "target_id": other["id"],
                "target_column_ids": [target_id],
            }
        )
        result = client.patch(f"{specs}/{row['id']}", json=update_data(row))
        assert result.status_code == 200, result.text
    sql = client.get(f"{workspace(project)}/ddl").json()["sql"]
    assert sql.count("ALTER TABLE") == 2
    assert len(diagram(client, project, "database")["edges"]) == 2
    db.execute(text(sql))
    assert (
        db.scalar(
            text("SELECT count(*) FROM pg_tables WHERE schemaname=:namespace"),
            {"namespace": name},
        )
        == 2
    )


def test_mockup_is_transformed_before_preview(client, context):
    _, project, _ = context
    preview = client.post(
        f"{workspace(project)}/mockup-preview",
        json={
            "html": (
                "<h2>備品</h2><script>sample</script>"
                '<a href="https://example.invalid">文言</a><input onfocus="sample">'
                '<img src="https://example.invalid/image.png">'
            ),
            "css": (
                ".sample { background-image: "
                'url("https://example.invalid/sample.png"); }'
            ),
        },
    )
    assert preview.status_code == 200, preview.text
    document = preview.json()["document"]
    assert "<h2>備品</h2>" in document
    assert "<script" not in document and "onfocus" not in document
    assert "example.invalid" not in document
    assert "default-src 'none'" in document
    assert preview.json()["css_blocked"] is True


def test_change_impact_records_acknowledgement_without_editing_target(client, context):
    _, project, specs = context
    screen = create(client, specs, "screen")
    api = create(client, specs, "api")
    api["content"]["mappings"] = [
        {
            "source_path": "content/path",
            "target_id": screen["id"],
            "target_path": "content/route",
            "name": "連携先",
        }
    ]
    mapped = client.patch(f"{specs}/{api['id']}", json=update_data(api)).json()
    mapped["content"]["path"] = "/v2/login"
    changed = client.patch(f"{specs}/{api['id']}", json=update_data(mapped))
    assert changed.status_code == 200, changed.text
    url = f"{workspace(project)}/impact/{api['id']}"
    impact = client.get(url)
    assert impact.status_code == 200, impact.text
    analysis = impact.json()
    assert analysis["base_version"] == 2
    [item] = analysis["items"]
    assert item["target_id"] == str(screen["id"])
    assert item["level"] == "warning"
    decision = {
        "source_version": analysis["source_version"],
        "key": item["key"],
        "status": "confirmed",
    }
    assert client.put(url, json=decision).status_code == 200
    assert client.get(url).json()["items"][0]["status"] == "confirmed"
    assert client.put(url, json={**decision, "source_version": 2}).status_code == 409
    assert client.get(f"{specs}/{screen['id']}").json()["version"] == 1
