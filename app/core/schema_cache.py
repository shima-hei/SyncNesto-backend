"""利用者の情報を含まない固定モデルのJSON Schemaだけを再利用する。"""

import json
from functools import lru_cache
from typing import Any

from pydantic import BaseModel


@lru_cache(maxsize=128)
def _schema_json(model: type[BaseModel]) -> str:
    """共有キャッシュを呼び出し元が変更できない文字列で保持する。"""
    return json.dumps(model.model_json_schema())


def model_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """毎回独立した辞書を返し、別要求への変更の混入を防ぐ。"""
    return json.loads(_schema_json(model))
