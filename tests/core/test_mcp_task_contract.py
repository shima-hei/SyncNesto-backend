"""業務APIの拡張を公開MCP入力へ混ぜない。"""

import hashlib
import json

import pytest

from app.schemas.mcp_task_contract import TaskCreate, TaskUpdate

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize(
    ("model", "digest"),
    [
        (
            TaskCreate,
            "c18e461f5ac0ead4bb410e94d69577a0a01717dde8a00883edd92b7d65ca8808",
        ),
        (
            TaskUpdate,
            "af88fc6b4beffef15000ffa10f2baab113dbc8b863f64d8003d25e03bd54ad6e",
        ),
    ],
)
def test_public_task_input_contract_matches_pre_phase66_snapshot(model, digest):
    schema = json.dumps(model.model_json_schema(), sort_keys=True, ensure_ascii=False)
    assert hashlib.sha256(schema.encode()).hexdigest() == digest
