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


def test_wireframe_part_revisions_preserve_approved_screen_layout(client, context):
    _, project, specs = context
    library = f"{workspace(project)}/wireframe-parts"
    layer_id = str(uuid4())
    content = {
        "width": 320,
        "height": 80,
        "layers": [{"id": layer_id, "kind": "heading", "name": "受付"}],
    }
    response = client.post(library, json={"name": "ヘッダー", "content": content})
    assert response.status_code == 201, response.text
    original = response.json()
    part_url = f"{library}/{original['part_id']}"
    screens = [create(client, specs, "screen") for _ in range(2)]
    approved = []
    for screen in screens:
        data = update_data(screen)
        root, layer, field = [str(uuid4()) for _ in range(3)]
        data["content"]["fields"] = [{"id": field, "name": "ローカル項目"}]
        data["content"]["artboards"] = [
            {
                "components": [
                    {
                        "id": root,
                        "kind": "container",
                        "part_pin": {
                            "part_id": original["part_id"],
                            "version": 1,
                            "layer_ids": {layer_id: layer},
                        },
                    },
                    {
                        "id": layer,
                        "parent_id": root,
                        "kind": "heading",
                        "name": "受付",
                        "field_id": field,
                    },
                ]
            }
        ]
        url = f"{specs}/{screen['id']}"
        saved = client.patch(url, json=data)
        assert saved.status_code == 200, saved.text
        fixed = client.patch(
            f"{url}/status",
            json={"version": saved.json()["version"], "status": "approved"},
        )
        assert fixed.status_code == 200, fixed.text
        approved.append(fixed.json())

    content["layers"][0]["name"] = "総合受付"
    changed = {"version": 1, "name": "共通ヘッダー", "content": content}
    updated = client.patch(part_url, json=changed)
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    conflict = client.patch(part_url, json=changed)
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["current"]["version"] == 2
    assert client.get(part_url, params={"version": 1}).json() == original
    assert client.get(library).json() == [
        {"part_id": original["part_id"], "name": "共通ヘッダー", "version": 2}
    ]

    first, second = approved
    newer = client.get(f"{workspace(project)}/impact/{second['id']}").json()["items"]
    assert any(
        item["level"] == "review" and "新版 2" in item["detail"] for item in newer
    )
    first_url = f"{specs}/{first['id']}"
    data = update_data(first)
    root, layer = data["content"]["artboards"][0]["components"]
    field_id = layer["field_id"]
    root["part_pin"]["version"] = 2
    layer["name"] = "総合受付"
    refreshed = client.patch(first_url, json=data)
    assert refreshed.status_code == 200, refreshed.text
    first_now = refreshed.json()
    assert first_now["status"] == "draft"
    assert first_now["content"]["artboards"][0]["components"][1]["field_id"] == field_id
    for screen in approved:
        url = f"{specs}/{screen['id']}/revisions/{screen['version']}"
        fixed_layers = client.get(url).json()["snapshot"]["content"]["artboards"][0][
            "components"
        ]
        assert fixed_layers[0]["part_pin"]["version"] == 1
        assert fixed_layers[1]["name"] == "受付"
    other_now = client.get(f"{specs}/{second['id']}").json()
    assert other_now["content"] == second["content"]


def test_wireframe_parts_enforce_project_and_write_permissions(
    client, context, create_test_project, create_test_user, assign_project_role
):
    user, project, _ = context
    library = f"{workspace(project)}/wireframe-parts"
    other = create_test_project()
    assign_project_role(user=user, project=other, role_key="project_admin")
    payload = {
        "name": "検索フォーム",
        "content": {"width": 140, "height": 60, "layers": [{"kind": "input"}]},
    }
    original = client.post(library, json=payload).json()
    part_url = f"{library}/{original['part_id']}"
    foreign = f"{workspace(other)}/wireframe-parts/{original['part_id']}"
    assert client.get(foreign).status_code == 404
    assert client.patch(foreign, json={**payload, "version": 1}).status_code == 404
    screen = client.post(
        f"/projects/{other.id}/specifications",
        json={
            "title": "別案件の画面",
            "content": {
                "type": "screen",
                "artboards": [
                    {
                        "components": [
                            {
                                "kind": "container",
                                "part_pin": {
                                    "part_id": original["part_id"],
                                    "version": 1,
                                    "layer_ids": {},
                                },
                            }
                        ]
                    }
                ],
            },
        },
    )
    assert screen.status_code == 400, screen.text
    payload["content"]["layers"][0]["field_id"] = str(uuid4())
    assert client.post(library, json=payload).status_code == 422
    del payload["content"]["layers"][0]["field_id"]

    viewer = create_test_user(email="wireframe-viewer@example.com")
    assign_project_role(user=viewer, project=project, role_key="viewer")
    authorize_as(client, viewer)
    assert client.get(part_url).status_code == 200
    assert client.post(library, json=payload).status_code == 403
    assert client.patch(part_url, json={**payload, "version": 1}).status_code == 403


def test_batch_review_and_approval_apply_only_to_locked_target_versions(
    client, context, create_test_user, assign_project_role
):
    requester, project, specs = context
    reviewer = create_test_user(email="design-reviewer@example.com")
    assign_project_role(user=reviewer, project=project, role_key="project_admin")
    base = f"{workspace(project)}/review-requests"
    targets = [create(client, specs, "screen"), create(client, specs, "screen")]
    assert [member["id"] for member in client.get(base + "/candidates").json()] == [
        reviewer.id
    ]

    def submit(kind, rows):
        payload = {
            "request_id": str(uuid4()),
            "kind": kind,
            "reviewer_id": reviewer.id,
            "targets": [
                {
                    "specification_id": row.get("specification_id", row.get("id")),
                    "version": row["version"],
                }
                for row in rows
            ],
        }
        response = client.post(base, json=payload)
        assert response.status_code == 201, response.text
        assert client.post(base, json=payload).json()["id"] == response.json()["id"]
        return response.json()

    review = submit("review", targets)
    assert [target["version"] for target in review["targets"]] == [2, 2]
    decision = f"{base}/{review['id']}/decision"
    assert client.post(decision, json={"status": "reviewed"}).status_code == 403
    authorize_as(client, reviewer)
    assert client.get(base).json()[0]["assigned_to_me"] is True
    assert client.post(decision, json={"status": "approved"}).status_code == 400
    reply = client.post(decision, json={"status": "reviewed"})
    assert reply.status_code == 200, reply.text
    assert reply.json()["status"] == "reviewed"
    assert client.get(base).json() == []
    authorize_as(client, requester)

    approval = submit("approval", review["targets"])
    first, second = [client.get(f"{specs}/{row['id']}").json() for row in targets]
    changed = client.patch(
        f"{specs}/{first['id']}", json={**update_data(first), "title": "次の下書き"}
    )
    assert changed.status_code == 200, changed.text
    authorize_as(client, reviewer)
    decision = f"{base}/{approval['id']}/decision"
    assert client.post(decision, json={"status": "approved"}).status_code == 409
    assert client.get(f"{specs}/{second['id']}").json()["status"] == "in_review"
    fixed = client.get(f"{specs}/{first['id']}/revisions/2").json()["snapshot"]
    assert fixed["title"] == first["title"]
    authorize_as(client, requester)
    assert (
        client.post(decision, json={"status": "cancelled"}).json()["status"]
        == "cancelled"
    )
    repeat = submit("approval", [second])
    authorize_as(client, reviewer)
    reply = client.post(f"{base}/{repeat['id']}/decision", json={"status": "approved"})
    assert reply.status_code == 200, reply.text
    assert client.get(f"{specs}/{second['id']}").json()["status"] == "approved"


def test_screen_list_batches_match_versions_and_do_not_duplicate_on_retry(
    client, context, create_test_user
):
    actor, project, specifications = context
    base = f"{workspace(project)}/screen-list"
    group_response = client.post(
        f"{workspace(project)}/collections", json={"kind": "group", "name": "受付"}
    )
    assert group_response.status_code == 201, group_response.text
    group = group_response.json()
    original = create(client, specifications, "screen")
    api = create(client, specifications, "api")
    details = update_data(original)
    details["content"]["actions"] = [
        {"steps": [{"kind": "api", "specification_id": api["id"]}]}
    ]
    edited = client.patch(f"{specifications}/{original['id']}", json=details)
    assert edited.status_code == 200, edited.text
    original = edited.json()
    payload = {
        "request_id": str(uuid4()),
        "edits": [
            {
                "id": original["id"],
                "version": original["version"],
                "route": "/new",
                "screen_kind": "list",
                "assignee_id": actor.id,
            },
            {
                "title": "新規の画面",
                "screen_kind": "drawer",
                "group_ids": [group["id"]],
            },
        ],
    }
    response = client.post(base + "/edits", json=payload)
    assert response.status_code == 200, response.text
    first, second = response.json()
    assert client.post(base + "/edits", json=payload).json() == [first, second]
    data = client.get(
        base, params={"assignee_id": actor.id, "screen_kind": "list", "q": "/new"}
    )
    assert data.status_code == 200, data.text
    (row,) = data.json()["items"]
    assert row["id"] == first["id"]
    assert row["assignee"]["id"] == actor.id
    assert row["related_apis"][0]["id"] == api["id"]
    assert (
        client.get(base, params={"group_id": group["id"]}).json()["items"][0]["id"]
        == second["id"]
    )
    assert client.get(base).json()["total"] == 2

    stale = {
        "request_id": str(uuid4()),
        "edits": [{"title": "保存しない行"}, payload["edits"][0]],
    }
    assert client.post(base + "/edits", json=stale).status_code == 409
    outsider = create_test_user(email="screen-assignee-outsider@example.com")
    invalid = {
        "request_id": str(uuid4()),
        "edits": [{"title": "保存しない担当者", "assignee_id": outsider.id}],
    }
    assert client.post(base + "/edits", json=invalid).status_code == 400
    assert client.get(base).json()["total"] == 2


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
    usage = client.get(
        f"{base}/collections/{group['id']}/api-usage",
        params={"category": "schemas", "name": "Equipment", "page_size": 1},
    )
    assert usage.status_code == 200, usage.text
    assert usage.json()["total"] == 1 and usage.json()["items"][0]["id"] == design["id"]
    unused = client.get(
        f"{base}/collections/{group['id']}/api-usage",
        params={"category": "schemas", "name": "Unused"},
    )
    assert unused.json()["items"] == []
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
    affected = client.get(f"{base}/impact/{design['id']}").json()
    assert affected["source_version"] == approved.json()["version"]
    assert any(
        item["level"] == "review" and "schemas / Equipment" in item["detail"]
        for item in affected["items"]
    )


def test_openapi_preserves_path_item_and_checks_inherited_parameters(client, context):
    _, project, specs = context
    base = workspace(project)
    group = client.post(
        f"{base}/collections", json={"kind": "api_set", "name": "契約"}
    ).json()
    path = "/equipment/{id}"
    parameter = {
        "name": "id",
        "in": "path",
        "required": True,
        "schema": {"type": "integer"},
    }
    document = {
        "openapi": "3.1.1",
        "info": {"title": "契約", "version": "1.0"},
        "paths": {
            "x-category": ["設備"],
            "/reserved": {},
            path: {
                "summary": "備品",
                "description": "共通の説明",
                "servers": [{"url": "https://test.example.invalid"}],
                "parameters": [{"$ref": "#/components/parameters/Identifier"}],
                "x-owner": "設備管理",
                "get": {
                    "summary": "備品取得",
                    "responses": {"200": {"description": "OK"}},
                },
                "delete": {
                    "summary": "備品削除",
                    "responses": {"204": {"description": "Removed"}},
                    "parameters": [{**parameter, "description": "削除対象"}],
                },
            },
        },
        "components": {"parameters": {"Identifier": parameter}},
    }
    payload = {
        "collection_id": group["id"],
        "collection_version": 1,
        "document": document,
    }
    selected = {f"GET {path}": 0, f"DELETE {path}": 0}
    imported = client.post(
        f"{base}/openapi/import", json={**payload, "selections": selected}
    )
    assert imported.status_code == 200, imported.text
    assert (
        client.get(f"{base}/openapi", params={"collection_id": group["id"]}).json()[
            "document"
        ]
        == document
    )
    operations = diagram(client, project, "api")["nodes"]
    design_id = next(row["id"] for row in operations if row["subtype"] == "GET")
    design_url = f"{specs}/{design_id}"
    row = client.get(design_url).json()
    edited = client.patch(
        design_url, json={**update_data(row), "summary": "備品を読む"}
    )
    assert edited.status_code == 200, edited.text
    approved = client.patch(
        design_url + "/status", json={"version": 2, "status": "approved"}
    )
    assert approved.status_code == 200, approved.text
    fixed = client.get(
        f"{base}/openapi", params={"design_id": design_id, "revision": 3}
    )
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["document"]["paths"][path] == {
        key: value for key, value in document["paths"][path].items() if key != "delete"
    }
    assert "parameters" not in fixed.json()["document"]["paths"][path]["get"]
    document["components"]["parameters"]["Identifier"]["name"] = "incorrect"
    assert (
        client.post(
            f"{base}/openapi/import", json={**payload, "collection_version": 2}
        ).status_code
        == 400
    )


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


def test_review_pins_design_dependencies_and_fixed_ddl_uses_them(client, context):
    _, project, specs = context
    parent_col, child_col = str(uuid4()), str(uuid4())
    parent = client.post(
        specs,
        json={
            "title": "元の親テーブル",
            "content": {
                "type": "database",
                "physical_name": "parent_table",
                "columns": [{"id": parent_col, "physical_name": "original_id"}],
            },
        },
    ).json()
    child = client.post(
        specs,
        json={
            "title": "参照元テーブル",
            "summary": "親を参照する",
            "content": {
                "type": "database",
                "physical_name": "child_table",
                "columns": [{"id": child_col, "physical_name": "parent_id"}],
                "constraints": [
                    {
                        "kind": "foreign_key",
                        "name": "child_parent_fk",
                        "target_id": parent["id"],
                        "column_ids": [child_col],
                        "target_column_ids": [parent_col],
                    }
                ],
            },
        },
    )
    assert child.status_code == 201, child.text
    child_url, parent_url = f"{specs}/{child.json()['id']}", f"{specs}/{parent['id']}"
    placements = f"{workspace(project)}/placements"
    initial_place = {"specification_id": parent["id"], "x": 32, "y": 64}
    assert (
        client.put(placements, json={"placements": [initial_place]}).status_code == 200
    )
    review = client.patch(
        child_url + "/status", json={"version": 1, "status": "in_review"}
    )
    assert review.status_code == 200, review.text
    parent["content"]["physical_name"] = "changed_parent"
    parent["content"]["columns"][0]["physical_name"] = "changed_id"
    assert client.patch(parent_url, json=update_data(parent)).status_code == 200
    assert (
        client.put(
            placements, json={"placements": [{**initial_place, "x": 256, "version": 1}]}
        ).status_code
        == 200
    )
    approved = client.patch(
        child_url + "/status", json={"version": 2, "status": "approved"}
    )
    assert approved.status_code == 200, approved.text
    snapshot = client.get(child_url + "/revisions/3").json()["snapshot"]
    [pinned] = snapshot["dependencies"]
    assert pinned["version"] == 1 and pinned["title"] == "元の親テーブル"
    assert pinned["placements"] == [{"view": "all", "x": 32, "y": 64, "hidden": False}]
    assert (
        snapshot["dependencies"]
        == client.get(child_url + "/revisions/2").json()["snapshot"]["dependencies"]
    )
    exported = client.get(
        f"{workspace(project)}/ddl",
        params={"design_id": child.json()["id"], "revision": 3},
    )
    assert exported.status_code == 200, exported.text
    assert exported.json()["warnings"] == []
    ddl = exported.json()["sql"]
    assert 'REFERENCES "public"."parent_table" ("original_id")' in ddl
    assert 'CREATE TABLE "public"."parent_table"' not in ddl


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


def test_impact_identifies_reverse_mapping_with_unchanged_item_name(client, context):
    _, project, specs = context
    column, schema_id = str(uuid4()), str(uuid4())
    table = client.post(
        specs,
        json={
            "title": "備品",
            "content": {
                "type": "database",
                "physical_name": "equipment",
                "columns": [
                    {
                        "id": column,
                        "name": "名称",
                        "physical_name": "name",
                        "data_type": "varchar",
                        "length": 100,
                    }
                ],
            },
        },
    ).json()
    api = client.post(
        specs,
        json={
            "title": "備品登録",
            "content": {
                "type": "api",
                "method": "POST",
                "path": "/equipment",
                "operation": {
                    "responses": {"201": {"description": "OK"}},
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {
                                            "x-syncnesto-id": schema_id,
                                            "type": "string",
                                            "maxLength": 100,
                                        }
                                    },
                                },
                            }
                        }
                    },
                },
                "mappings": [
                    {
                        "name": "備品名の保存",
                        "source_path": (
                            "content/operation/requestBody/content/application~1json/"
                            f"schema/properties/{schema_id}/type"
                        ),
                        "target_id": table["id"],
                        "target_path": f"content/columns/{column}/physical_name",
                    }
                ],
            },
        },
    )
    assert api.status_code == 201, api.text
    table["content"]["columns"][0]["length"] = 50
    changed = client.patch(f"{specs}/{table['id']}", json=update_data(table))
    assert changed.status_code == 200, changed.text
    impact = client.get(f"{workspace(project)}/impact/{table['id']}")
    assert impact.status_code == 200, impact.text
    [item] = impact.json()["items"]
    assert item["level"] == "error" and "最大文字数" in item["detail"]
    assert item["target_id"] == str(api.json()["id"]) and item["target_version"] == 1
    assert client.get(f"{specs}/{api.json()['id']}").json()["version"] == 1


def test_draft_api_requires_an_explicit_method_for_export(client, context):
    _, project, specs = context
    draft = client.post(
        specs,
        json={"title": "メソッド未定", "content": {"type": "api", "method": None}},
    )
    assert draft.status_code == 201, draft.text
    row = draft.json()
    assert row["content"]["method"] is None
    row["content"]["path"] = "/loans"
    edit = client.patch(f"{specs}/{row['id']}", json=update_data(row))
    assert edit.status_code == 200, edit.text
    output = client.get(f"{workspace(project)}/openapi").json()
    assert output["document"]["paths"] == {}
    assert "メソッド" in output["warnings"][0]
