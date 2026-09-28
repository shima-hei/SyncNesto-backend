"""構造化テスト設計の認可・整合性・競合・ケース保持を検証する。"""

import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from tests.helpers.auth import authorize_as


def graph(design):
    """多対多の小さな設計データを作る。"""
    item1, item2, factor, level, pattern1, pattern2 = [str(uuid4()) for _ in range(6)]
    return {
        **{key: design[key] for key in ("name", "description", "version")},
        "items": [
            {"id": item1, "code": "T001", "content": "ログイン"},
            {"id": item2, "code": "T002"},
        ],
        "factors": [{"id": factor, "name": "ユーザー"}],
        "levels": [{"id": level, "factor_id": factor, "name": "管理者"}],
        "patterns": [
            {"id": pattern1, "code": "P001"},
            {"id": pattern2, "code": "P002"},
        ],
        "values": [
            {
                "id": str(uuid4()),
                "pattern_id": pattern1,
                "factor_id": factor,
                "level_id": level,
            }
        ],
        "links": [
            {"id": str(uuid4()), "item_id": i, "pattern_id": p}
            for i, p in [(item1, pattern1), (item1, pattern2), (item2, pattern1)]
        ],
        "columns": [],
        "layout": {"cells": {}, "widths": {}, "heights": {}},
    }


@pytest.fixture
def design_context(client, create_test_user, create_test_project, assign_project_role):
    """プロジェクト管理者と設計書を用意する。"""
    user = create_test_user(email="design@example.com")
    project = create_test_project(project_code="DESIGN", name="設計")
    assign_project_role(user=user, project=project, role_key="project_admin")
    authorize_as(client, user)
    root = f"/projects/{project.id}/test-designs"
    response = client.post(root, json={"name": "ログイン設計"})
    assert response.status_code == 201, response.text
    design = response.json()
    return root, f"{root}/{design['id']}", design, user, project


def test_graph_roundtrip_conflict_and_case_preservation(client, design_context):
    """多対多、冪等生成、設計変更の影響、削除後の結果保持を確認する。"""
    _, url, design, _, _ = design_context
    data = graph(design)
    response = client.put(url, json=data)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["version"] == 2
    conflict = client.put(url, json=data)
    assert conflict.status_code == 409
    assert conflict.json()["current"]["version"] == 2
    cases = client.post(url + "/cases/generate", json={"version": 2}).json()
    assert len(cases) == 3
    assert len(client.post(url + "/cases/generate", json={"version": 2}).json()) == 3
    case = next(c for c in cases if c["source"]["item"]["code"] == "T001")
    assert case["acknowledged_source"] is None
    updated = client.patch(
        url + f"/cases/{case['id']}",
        json={
            "version": 1,
            "status": "passed",
            "actual_result": "成功",
            "notes": "記録",
        },
    )
    assert updated.status_code == 200, updated.text
    data["version"] = 2
    data["items"][0]["content"] = "変更後"
    assert client.put(url, json=data).status_code == 200
    changed = client.get(url + "/cases").json()
    old = next(c for c in changed if c["id"] == case["id"])
    assert old["stale"] is True
    assert old["source"]["item"]["content"] == "変更後"
    assert old["acknowledged_source"]["item"]["content"] == "ログイン"
    assert old["actual_result"] == "成功"
    assert old["notes"] == "記録"
    refreshed = client.post(url + f"/cases/{case['id']}/refresh", json={"version": 3})
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["actual_result"] == "成功"
    assert refreshed.json()["stale"] is False
    assert refreshed.json()["acknowledged_source"] is None
    data["version"] = 3
    data["items"] = []
    data["links"] = []
    assert client.put(url, json=data).status_code == 200
    archived = client.get(url + "/cases").json()
    assert len(archived) == 3
    assert all(not c["active"] for c in archived)
    assert next(c for c in archived if c["id"] == case["id"])["status"] == "passed"


def test_case_result_records_executor_and_not_applicable(client, design_context):
    """対象外の判断者と日時を保存し、設計確認後も維持する。"""
    _, url, design, user, _ = design_context
    data = graph(design)
    assert client.put(url, json=data).status_code == 200
    case = next(
        row
        for row in client.get(url + "/cases").json()
        if row["source"]["item"]["code"] == "T001"
    )
    assert case["executed_by"] is None
    assert case["executed_at"] is None
    result = client.patch(
        url + f"/cases/{case['id']}",
        json={
            "version": case["version"],
            "status": "not_applicable",
            "actual_result": "",
            "notes": "今回のリリースでは対象外",
        },
    )
    assert result.status_code == 200, result.text
    saved = result.json()
    assert saved["status"] == "not_applicable"
    assert saved["executed_by"] == user.id
    assert saved["executed_by_name"] == user.name
    assert saved["executed_at"]
    listed = next(
        row for row in client.get(url + "/cases").json() if row["id"] == case["id"]
    )
    assert listed["executed_at"] == saved["executed_at"]
    data["version"] = 2
    data["items"][0]["content"] = "変更後"
    assert client.put(url, json=data).status_code == 200
    refreshed = client.post(
        url + f"/cases/{case['id']}/refresh", json={"version": saved["version"] + 1}
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["status"] == "not_applicable"
    assert refreshed.json()["executed_at"] == saved["executed_at"]


def test_save_syncs_cases_and_preserves_explicit_spacers(client, design_context):
    """保存だけでケースを生成し、区切り行は項目数・ケース数から除外する。"""
    root, url, design, _, _ = design_context
    data = graph(design)
    data["items"] = [
        {
            "id": str(uuid4()),
            "code": "T001",
            "target_feature": "ログイン画面",
            "content": "表示",
            "position": 0,
        },
        {"id": str(uuid4()), "code": str(uuid4()), "is_spacer": True, "position": 1},
        {
            "id": str(uuid4()),
            "code": "T002",
            "target_feature": "設定画面",
            "content": "保存",
            "position": 2,
        },
    ]
    data["factors"] = []
    data["levels"] = []
    data["patterns"] = []
    data["values"] = []
    data["links"] = []
    saved = client.put(url, json=data)
    assert saved.status_code == 200, saved.text
    assert [item["is_spacer"] for item in saved.json()["items"]] == [False, True, False]
    summary = client.get(root).json()[0]
    assert (summary["item_count"], summary["expanded_case_count"]) == (2, 2)
    cases = client.get(url + "/cases").json()
    assert len(cases) == 2
    assert {case["source"]["item"]["target_feature"] for case in cases} == {
        "ログイン画面",
        "設定画面",
    }
    first = cases[0]
    result = client.patch(
        url + f"/cases/{first['id']}",
        json={
            "version": first["version"],
            "status": "passed",
            "actual_result": "成功",
            "notes": "記録",
        },
    )
    assert result.status_code == 200, result.text
    data["version"] = 2
    data["items"][0]["content"] = "表示を確認"
    assert client.put(url, json=data).status_code == 200
    changed = next(
        case for case in client.get(url + "/cases").json() if case["id"] == first["id"]
    )
    assert changed["source"]["item"]["content"] == "表示を確認"
    assert changed["stale"] is True
    assert (changed["status"], changed["actual_result"], changed["notes"]) == (
        "passed",
        "成功",
        "記録",
    )


def test_new_combination_is_created_on_design_save(client, design_context):
    """組み合わせの追加時に実行者による生成操作を要求しない。"""
    _, url, design, _, _ = design_context
    data = graph(design)
    assert client.put(url, json=data).status_code == 200
    assert len(client.get(url + "/cases").json()) == 3
    data["version"] = 2
    data["links"].append(
        {
            "id": str(uuid4()),
            "item_id": data["items"][1]["id"],
            "pattern_id": data["patterns"][1]["id"],
        }
    )
    assert client.put(url, json=data).status_code == 200
    cases = client.get(url + "/cases").json()
    assert len(cases) == 4
    assert all(case["active"] for case in cases)


def test_item_renumber_keeps_case_result_without_false_impact(client, design_context):
    """途中挿入による表示番号の変更だけでは、既存ケースを影響ありにしない。"""
    _, url, design, _, _ = design_context
    data = graph(design)
    assert client.put(url, json=data).status_code == 200
    original = next(
        case
        for case in client.get(url + "/cases").json()
        if case["source"]["item"]["code"] == "T002"
    )
    assert (
        client.patch(
            url + f"/cases/{original['id']}",
            json={
                "version": original["version"],
                "status": "passed",
                "actual_result": "確認済み",
            },
        ).status_code
        == 200
    )
    data["version"] = 2
    data["items"][1]["code"] = "T003"
    data["items"][1]["position"] = 2
    data["items"].insert(
        1, {"id": str(uuid4()), "code": "T002", "position": 1, "content": "新規項目"}
    )
    assert client.put(url, json=data).status_code == 200
    cases = client.get(url + "/cases").json()
    assert len(cases) == 4
    retained = next(case for case in cases if case["id"] == original["id"])
    assert retained["source"]["item"]["code"] == "T003"
    assert retained["acknowledged_source"] is None
    assert retained["stale"] is False
    assert retained["status"] == "passed"
    assert retained["actual_result"] == "確認済み"
    data["version"] = 3
    data["items"][2]["content"] = "既存項目の変更"
    assert client.put(url, json=data).status_code == 200
    changed = next(
        case
        for case in client.get(url + "/cases").json()
        if case["id"] == original["id"]
    )
    assert changed["stale"] is True
    assert changed["acknowledged_source"]["item"]["code"] == "T003"


def test_case_change_keeps_last_acknowledged_design(client, design_context):
    """連続した設計変更でも前回確認済みの内容を維持する。"""
    _, url, design, _, _ = design_context
    data = graph(design)
    assert client.put(url, json=data).status_code == 200
    case = next(
        row
        for row in client.get(url + "/cases").json()
        if row["source"]["item"]["code"] == "T001"
    )
    data["version"] = 2
    data["items"][0]["steps"] = "必要項目を入力する"
    assert client.put(url, json=data).status_code == 200
    data["version"] = 3
    data["items"][0]["steps"] = "必要項目を入力し、ボタンを押す"
    assert client.put(url, json=data).status_code == 200
    changed = next(
        row for row in client.get(url + "/cases").json() if row["id"] == case["id"]
    )
    assert changed["stale"] is True
    assert changed["acknowledged_source"]["item"]["steps"] == ""
    assert changed["source"]["item"]["steps"] == "必要項目を入力し、ボタンを押す"
    refreshed = client.post(
        url + f"/cases/{case['id']}/refresh", json={"version": changed["version"]}
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["acknowledged_source"] is None
    data["version"] = 4
    data["items"][0]["steps"] = "入力内容を見直し、ボタンを押す"
    assert client.put(url, json=data).status_code == 200
    changed_again = next(
        row for row in client.get(url + "/cases").json() if row["id"] == case["id"]
    )
    assert changed_again["acknowledged_source"]["item"]["steps"] == (
        "必要項目を入力し、ボタンを押す"
    )


def test_legacy_stale_case_reports_missing_previous_source(client, design_context, db):
    """移行前に変更されたケースの旧値を推測せず、欠損を維持する。"""
    _, url, design, _, _ = design_context
    data = graph(design)
    assert client.put(url, json=data).status_code == 200
    case = next(
        row
        for row in client.get(url + "/cases").json()
        if row["source"]["item"]["code"] == "T001"
    )
    data["version"] = 2
    data["items"][0]["content"] = "一度目の変更"
    assert client.put(url, json=data).status_code == 200
    db.execute(
        text(
            "UPDATE test_cases SET acknowledged_source = NULL "
            "WHERE id = CAST(:id AS uuid)"
        ),
        {"id": case["id"]},
    )
    db.commit()
    data["version"] = 3
    data["items"][0]["content"] = "二度目の変更"
    assert client.put(url, json=data).status_code == 200
    changed = next(
        row for row in client.get(url + "/cases").json() if row["id"] == case["id"]
    )
    assert changed["stale"] is True
    assert changed["acknowledged_source"] is None


def test_legacy_current_case_captures_source_before_change(client, design_context, db):
    """旧ケースが未変更なら次の設計保存時に変更前の内容を記録する。"""
    _, url, design, _, _ = design_context
    data = graph(design)
    assert client.put(url, json=data).status_code == 200
    case = next(
        row
        for row in client.get(url + "/cases").json()
        if row["source"]["item"]["code"] == "T001"
    )
    db.execute(
        text(
            "UPDATE test_cases SET acknowledged_source = NULL "
            "WHERE id = CAST(:id AS uuid)"
        ),
        {"id": case["id"]},
    )
    db.commit()
    data["version"] = 2
    data["items"][0]["content"] = "変更後"
    assert client.put(url, json=data).status_code == 200
    changed = next(
        row for row in client.get(url + "/cases").json() if row["id"] == case["id"]
    )
    assert changed["acknowledged_source"]["item"]["content"] == "ログイン"
    assert changed["source"]["item"]["content"] == "変更後"


def test_expected_value_roundtrip_and_impact(client, design_context):
    """共有パターンの期待値変更が利用ケース全件に伝播する。"""
    _, url, design, _, _ = design_context
    data = graph(design)
    expected_id = str(uuid4())
    data["expected_values"] = [{"id": expected_id, "name": "ログイン成功"}]
    data["expected_selections"] = [
        {
            "id": str(uuid4()),
            "pattern_id": data["patterns"][0]["id"],
            "expected_value_id": expected_id,
        }
    ]
    response = client.put(url, json=data)
    assert response.status_code == 200, response.text
    assert response.json()["expected_values"][0]["name"] == "ログイン成功"
    cases = client.post(url + "/cases/generate", json={"version": 2}).json()
    assert sum("expected_value" in c["source"] for c in cases) == 2
    data["version"] = 2
    data["expected_values"][0]["name"] = "ホームへ移動"
    assert client.put(url, json=data).status_code == 200
    assert sum(c["stale"] for c in client.get(url + "/cases").json()) == 2
    data["version"] = 3
    data["expected_selections"][0]["expected_value_id"] = str(uuid4())
    assert client.put(url, json=data).status_code == 422
    assert client.get(url).json()["version"] == 3
    data["expected_selections"] = []
    data["expected_values"] = []
    assert client.put(url, json=data).status_code == 200
    assert len(client.get(url + "/cases").json()) == 3


def test_multiple_expected_values(client, design_context):
    """複数期待値を保存・展開し、重複参照を拒否する。"""
    _, url, design, _, _ = design_context
    data = graph(design)
    data["expected_values"] = [
        {"id": str(uuid4()), "name": name} for name in ["成功", "履歴"]
    ]
    data["expected_selections"] = [
        {
            "id": str(uuid4()),
            "pattern_id": data["patterns"][0]["id"],
            "expected_value_id": row["id"],
        }
        for row in data["expected_values"]
    ]
    response = client.put(url, json=data)
    assert response.status_code == 200, response.text
    assert len(response.json()["expected_selections"]) == 2
    cases = client.post(url + "/cases/generate", json={"version": 2}).json()
    expanded = [c for c in cases if "expected_values" in c["source"]]
    assert len(expanded) == 2
    assert {v["name"] for v in expanded[0]["source"]["expected_values"]} == {
        "成功",
        "履歴",
    }
    data["version"] = 2
    data["expected_selections"].reverse()
    assert client.put(url, json=data).status_code == 200
    assert not any(c["stale"] for c in client.get(url + "/cases").json())
    data["version"] = 3
    data["expected_selections"].append(
        {**data["expected_selections"][0], "id": str(uuid4())}
    )
    assert client.put(url, json=data).status_code == 422
    data["expected_selections"] = data["expected_selections"][:1]
    assert client.put(url, json=data).status_code == 200
    assert sum(c["stale"] for c in client.get(url + "/cases").json()) == 2


def test_independent_tables_and_optional_case_expansion(client, design_context):
    """独立した表の参照と単独ケース・展開ケースを混在させる。"""
    _, url, design, _, _ = design_context
    data = graph(design)
    t1, t2, item3 = [str(uuid4()) for _ in range(3)]
    data["pattern_tables"] = [
        {"id": t1, "name": "ログイン"},
        {"id": t2, "name": "権限"},
    ]
    data["links"] = []
    data["items"][0]["pattern_table_id"] = t1
    data["items"].append({"id": item3, "code": "T003", "pattern_table_id": t1})
    data["factors"][0]["table_id"] = t1
    for p in data["patterns"]:
        p["table_id"] = t1
    data["patterns"].append({"id": str(uuid4()), "code": "P001", "table_id": t2})
    response = client.put(url, json=data)
    assert response.status_code == 200, response.text
    assert len(client.get(url + "/cases").json()) == 5
    cases = client.post(url + "/cases/generate", json={"version": 2}).json()
    assert len(cases) == 5
    summary = client.get(url.rsplit("/", 1)[0]).json()[0]
    assert summary["item_count"] == 3
    assert summary["expanded_case_count"] == 5
    assert sum(c["source"]["pattern"] is None for c in cases) == 1
    assert len(client.post(url + "/cases/generate", json={"version": 2}).json()) == 5
    standalone = next(c for c in cases if c["source"]["pattern"] is None)
    assert (
        client.patch(
            url + f"/cases/{standalone['id']}",
            json={"version": 1, "status": "passed", "actual_result": "成功"},
        ).status_code
        == 200
    )
    data["version"] = 2
    data["items"][1]["content"] = "単独項目の変更"
    assert client.put(url, json=data).status_code == 200
    refreshed = client.post(
        url + f"/cases/{standalone['id']}/refresh", json={"version": 3}
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["actual_result"] == "成功"
    data["version"] = 3
    data["items"][0]["pattern_table_id"] = None
    assert client.put(url, json=data).status_code == 200
    expanded = client.post(url + "/cases/generate", json={"version": 4}).json()
    assert sum(c["active"] for c in expanded) == 4
    assert len(expanded) == 6
    assert client.get(url.rsplit("/", 1)[0]).json()[0]["expanded_case_count"] == 4
    data["version"] = 4
    data["values"][0]["pattern_id"] = data["patterns"][-1]["id"]
    assert client.put(url, json=data).status_code == 422
    assert client.get(url).json()["version"] == 4


def test_legacy_matrix_migration_preserves_results(client, design_context, db):
    """旧スキーマのケースを移行し、結果と識別子を維持する。"""
    from app.db.session import engine

    _, url, design, _, _ = design_context
    data = graph(design)
    assert client.put(url, json=data).status_code == 200
    cases = client.post(url + "/cases/generate", json={"version": 2}).json()
    case = cases[0]
    assert (
        client.patch(
            url + f"/cases/{case['id']}",
            json={"version": 1, "status": "passed", "actual_result": "移行前の結果"},
        ).status_code
        == 200
    )
    db.commit()
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/8d9e0f1a2b3c_pattern_tables.py"
    )
    spec = importlib.util.spec_from_file_location("pattern_table_migration", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE test_cases SET item_pattern_id=source_key::uuid, "
                "source_key=NULL"
            )
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            migration.upgrade()
        # 旧migrationを単独で再実行したため、後続migrationの列を復元する。
        connection.execute(
            text("ALTER TABLE test_pattern_tables ADD COLUMN deleted_at TIMESTAMPTZ")
        )
    migrated = client.get(url).json()
    assert len(migrated["pattern_tables"]) == 1
    assert all(item["pattern_table_id"] for item in migrated["items"])
    assert not migrated["links"]
    assert migrated["version"] == 3
    regenerated = client.post(url + "/cases/generate", json={"version": 3}).json()
    assert len(regenerated) == 4
    retained = next(c for c in regenerated if c["id"] == case["id"])
    assert retained["actual_result"] == "移行前の結果"
    assert retained["active"] is True


def test_combination_label_migration(client, design_context, db):
    """旧自動名だけを短縮し、既存コードとの衝突を回避する。"""
    from app.db.session import engine

    _, url, design, _, _ = design_context
    data = graph(design)
    data["patterns"][1]["code"] = "組み合わせ001"
    data["patterns"].append({"id": str(uuid4()), "code": "独自の名前"})
    assert client.put(url, json=data).status_code == 200
    db.commit()
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/9e0f1a2b3c4d_multiple_pattern_expectations.py"
    )
    spec = importlib.util.spec_from_file_location("expectations_migration", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            migration.upgrade()
    updated = client.get(url).json()
    assert {p["code"] for p in updated["patterns"]} == {"P001", "P002", "独自の名前"}
    assert updated["version"] == 3


def test_invalid_graph_rolls_back(client, design_context):
    """不正参照を保存せず、別因子の水準も拒否する。"""
    _, url, design, _, _ = design_context
    data = graph(design)
    data["values"][0]["level_id"] = str(uuid4())
    assert client.put(url, json=data).status_code == 422
    assert client.get(url).json()["version"] == 1
    data = graph(design)
    other_factor = str(uuid4())
    data["factors"].append({"id": other_factor, "name": "別因子"})
    data["levels"][0]["factor_id"] = other_factor
    assert client.put(url, json=data).status_code == 422


def test_other_design_ids_cannot_be_stolen(client, design_context):
    """別設計の既存IDを投入しても既存データを書き換えない。"""
    root, url, design, _, _ = design_context
    data = graph(design)
    assert client.put(url, json=data).status_code == 200
    other = client.post(root, json={"name": "別設計"}).json()
    data["name"] = "乗っ取り"
    rejected = client.put(f"{root}/{other['id']}", json=data)
    assert rejected.status_code == 409, rejected.text
    assert client.get(url).json()["name"] == "ログイン設計"
    assert client.get(f"{root}/{other['id']}").json()["items"] == []


@pytest.mark.parametrize("role", ["viewer", "member", "manager"])
def test_role_permissions(
    client, design_context, create_test_user, assign_project_role, role
):
    """設計編集とケース生成の権限を分ける。"""
    _, url, design, _, project = design_context
    user = create_test_user(email=f"{role}@example.com")
    assign_project_role(user=user, project=project, role_key=role)
    authorize_as(client, user)
    assert client.get(url).status_code == 200
    result = client.put(url, json=graph(design))
    assert result.status_code == (403 if role == "viewer" else 200)
    version = 1 if role == "viewer" else 2
    generated = client.post(url + "/cases/generate", json={"version": version})
    assert generated.status_code == (200 if role == "manager" else 403)
    assert client.delete(url, params={"version": version}).status_code == 403


def test_project_scope(
    client, design_context, create_test_user, create_test_project, assign_project_role
):
    """他プロジェクトへのパス差し替えと非所属アクセスを拒否する。"""
    _, url, design, user, _ = design_context
    other = create_test_project(project_code="OTHER", name="別")
    assign_project_role(user=user, project=other, role_key="project_admin")
    assert (
        client.get(f"/projects/{other.id}/test-designs/{design['id']}").status_code
        == 404
    )
    outsider = create_test_user(email="outside@example.com")
    authorize_as(client, outsider)
    assert client.get(url).status_code == 403


def test_concurrent_updates_reject_one_writer(client, design_context):
    """同じversionで並行保存した場合、片方だけが成功する。"""
    _, url, design, _, _ = design_context
    data = graph(design)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.put(url, json=data), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert client.get(url).json()["version"] == 2


def test_deletion_disabled_patterns_and_case_conflict(client, design_context):
    """無効化・再有効化とケースの排他制御、設計削除を検証する。"""
    _, url, design, _, _ = design_context
    data = graph(design)
    data["patterns"][0]["enabled"] = False
    assert client.put(url, json=data).status_code == 200
    cases = client.post(url + "/cases/generate", json={"version": 2}).json()
    assert len(cases) == 1
    case = cases[0]
    update = {"version": 1, "status": "failed", "actual_result": "記録"}
    assert client.patch(url + f"/cases/{case['id']}", json=update).status_code == 200
    assert client.patch(url + f"/cases/{case['id']}", json=update).status_code == 409
    data["version"] = 2
    data["patterns"][0]["enabled"] = True
    assert client.put(url, json=data).status_code == 200
    assert len(client.post(url + "/cases/generate", json={"version": 3}).json()) == 3
    assert client.delete(url, params={"version": 2}).status_code == 409
    assert client.delete(url, params={"version": 3}).status_code == 204
    assert client.get(url + "/cases").status_code == 404
