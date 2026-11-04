"""要求・組織・書き込み境界で読み取りキャッシュを分離する。"""

import pytest
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.schema_cache import model_json_schema
from app.db.read_cache import cached_read, read_cache

pytestmark = pytest.mark.no_db


def test_read_cache_context_lifetime_and_nested_scope():
    """キャッシュのない要求・別組織・別本人・入れ子へ値を持ち出さない。"""
    calls = []

    def load():
        calls.append(len(calls) + 1)
        return calls[-1]

    with Session(info={"tenant_id": 1, "tenant_user_id": 2}) as db:
        assert cached_read(db, "test", 1, load) == 1
        with read_cache(db):
            assert cached_read(db, "test", 1, load) == 2
            assert cached_read(db, "test", 1, load) == 2
            with read_cache(db):
                assert cached_read(db, "test", 1, load) == 3
            assert cached_read(db, "test", 1, load) == 2
            db.info["tenant_user_id"] = 3
            assert cached_read(db, "test", 1, load) == 4
            db.info["tenant_id"] = 4
            assert cached_read(db, "test", 1, load) == 5
            db.info["data_realm"] = "demo"
            assert cached_read(db, "test", 1, load) == 6
        assert "operation_read_cache" not in db.info
        with read_cache(db):
            assert cached_read(db, "test", 1, load) == 7


def test_transaction_end_and_exception_discard_values():
    """同じSessionでもcommit後・例外後は取得し直す。"""
    with Session() as db, read_cache(db):
        with db.begin():
            assert cached_read(db, "test", 1, lambda: "first") == "first"
        assert cached_read(db, "test", 1, lambda: "second") == "second"
        with pytest.raises(ValueError), read_cache(db):
            cached_read(db, "test", 1, lambda: "nested")
            raise ValueError()
        assert cached_read(db, "test", 1, lambda: "unused") == "second"
        with read_cache(db), db.begin():
            cached_read(db, "test", 1, lambda: "inner")
        assert (
            cached_read(db, "test", 1, lambda: "after-nested-commit")
            == "after-nested-commit"
        )


def test_schema_copies_do_not_share_nested_mutations():
    """一つのツール応答の変更が次の利用者へ漏れない。"""

    class Input(BaseModel):
        values: list[str]

    expected = Input.model_json_schema()
    first = model_json_schema(Input)
    first["properties"]["values"]["items"]["type"] = "integer"
    assert model_json_schema(Input) == expected
