"""外部参照を取得せずに設計データとOpenAPIを変換する。"""

import re
from typing import Any

from app.core.exceptions import BadRequestError
from app.schemas.design_content import json_size, validate_document_tree
from app.schemas.specification import ApiSpecification

METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
PATH_KEY = re.compile(r"^/[^?#\s]*$")
TEMPLATE = re.compile(r"\{([^{}]+)\}")


def validate_operation(path: str, operation: dict) -> None:
    """操作とパス変数の整合性を確認する。"""
    if not PATH_KEY.fullmatch(path):
        raise BadRequestError(
            "APIパスはスラッシュから始め、クエリや空白を含めないでください"
        )
    parameters = operation.get("parameters", [])
    responses = operation.get("responses")
    if not isinstance(responses, dict) or not responses:
        raise BadRequestError("OpenAPIレスポンスを1件以上設定してください")
    if not isinstance(parameters, list):
        raise BadRequestError("OpenAPI parametersは配列にしてください")
    used = set()
    variables = set()
    for parameter in parameters:
        if not isinstance(parameter, dict):
            raise BadRequestError("OpenAPIパラメータはオブジェクトにしてください")
        if "$ref" in parameter:
            continue
        name, location = parameter.get("name"), parameter.get("in")
        if not isinstance(name, str) or location not in {
            "path",
            "query",
            "header",
            "cookie",
        }:
            raise BadRequestError("OpenAPIパラメータに名前と配置場所を指定してください")
        if (name, location) in used:
            raise BadRequestError("OpenAPIパラメータ名が重複しています")
        used.add((name, location))
        if location == "path":
            if parameter.get("required") is not True:
                raise BadRequestError("パスパラメータは必須にしてください")
            variables.add(name)
    if variables != set(TEMPLATE.findall(path)) and not any(
        "$ref" in record for record in parameters
    ):
        raise BadRequestError("APIパスとパスパラメータが一致しません")


def legacy_operation(content: ApiSpecification) -> dict:
    """旧設計の説明・入出力を保持し、出力可能な範囲をOpenAPIへ写す。"""
    types = {"string", "integer", "number", "boolean", "object", "array", "null"}

    def schema(field):
        return {
            "type": field.data_type if field.data_type in types else "string",
            "description": field.description or field.validation,
        }

    parameters = [
        {
            "name": field.name,
            "in": field.location,
            "required": field.required or field.location == "path",
            "schema": schema(field),
        }
        for field in content.request_fields
        if field.name and field.location in {"path", "query", "header", "cookie"}
    ]
    known = {parameter["name"] for parameter in parameters if parameter["in"] == "path"}
    parameters.extend(
        {"name": name, "in": "path", "required": True, "schema": {"type": "string"}}
        for name in TEMPLATE.findall(content.path)
        if name not in known
    )
    responses: dict[str, dict[str, Any]] = {
        rule.name: {"description": rule.description or rule.destination or rule.name}
        for rule in content.responses
        if re.fullmatch(r"([1-5][0-9]{2}|[1-5]XX|default)", rule.name)
    } or {"200": {"description": "正常応答"}}
    if content.response_fields:
        response = next(
            (response for code, response in responses.items() if code.startswith("2")),
            None,
        )
        if response is not None:
            response["content"] = {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            field.name: schema(field)
                            for field in content.response_fields
                            if field.name
                        },
                    }
                }
            }
    operation: dict[str, Any] = {"parameters": parameters, "responses": responses}
    fields = [
        field
        for field in content.request_fields
        if field.name and field.location == "body"
    ]
    if fields:
        operation["requestBody"] = {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {field.name: schema(field) for field in fields},
                        "required": [field.name for field in fields if field.required],
                    }
                }
            }
        }
    return operation


def check_document(document: dict) -> None:
    """3.1系の文書形・ローカルJSONポインタ・サイズを検証する。"""
    try:
        validate_document_tree(document)
        size = json_size(document)
    except (ValueError, TypeError) as error:
        raise BadRequestError(
            "OpenAPI定義の階層・要素数が上限を超えています"
        ) from error
    if size > 600_000:
        raise BadRequestError("OpenAPIファイルは600KB以内にしてください")
    if not re.fullmatch(r"3\.1\.\d+", str(document.get("openapi", ""))):
        raise BadRequestError("OpenAPI 3.1系を指定してください")
    info = document.get("info", {})
    if not isinstance(info, dict) or not all(
        isinstance(info.get(key), str) and info[key].strip()
        for key in ("title", "version")
    ):
        raise BadRequestError("OpenAPI infoにtitleとversionを指定してください")
    paths = document.get("paths", {})
    if not isinstance(paths, dict):
        raise BadRequestError("OpenAPI pathsはオブジェクトにしてください")
    stack = [document]
    while stack:
        value = stack.pop()
        if isinstance(value, list):
            stack.extend(value)
        if not isinstance(value, dict):
            continue
        for ref_key in ("$ref", "$dynamicRef"):
            if ref_key not in value:
                continue
            pointer = value[ref_key]
            if not isinstance(pointer, str) or not pointer.startswith("#/"):
                raise BadRequestError("外部参照・アンカー参照は取り込み対象外です")
            target = document
            try:
                for part in pointer[2:].split("/"):
                    key = part.replace("~1", "/").replace("~0", "~")
                    target = (
                        target[int(key)] if isinstance(target, list) else target[key]
                    )
            except (ValueError, IndexError, TypeError, KeyError) as error:
                raise BadRequestError("解決できないローカル参照があります") from error
        stack.extend(value.values())


def operations(document: dict) -> list[tuple[str, str, dict]]:
    """パス項目を分解しても、表現できない定義を黙って落とさない。"""
    check_document(document)
    result = []
    ids: set[str] = set()
    paths: set[tuple[str, str]] = set()
    for path, definitions in document.get("paths", {}).items():
        if not isinstance(definitions, dict):
            raise BadRequestError("OpenAPI pathsの各項目はオブジェクトにしてください")
        unsupported = set(definitions) - METHODS
        if unsupported:
            raise BadRequestError(
                "パス項目共通の定義はAPI操作に展開してから取り込んでください"
            )
        for method, operation in definitions.items():
            if not isinstance(operation, dict):
                raise BadRequestError("OpenAPI操作はオブジェクトにしてください")
            validate_operation(path, operation)
            operation_id = operation.get("operationId")
            if operation_id is not None:
                if (
                    not isinstance(operation_id, str)
                    or not operation_id
                    or operation_id in ids
                ):
                    raise BadRequestError(
                        "OpenAPI operationIdを重複なく指定してください"
                    )
                ids.add(operation_id)
            key = (method, TEMPLATE.sub("{}", path))
            if key in paths:
                raise BadRequestError("同じ構造のAPIパスが重複しています")
            paths.add(key)
            result.append((method.upper(), path, operation))
    if len(result) > 100:
        raise BadRequestError("一度に取り込めるAPI操作は100件までです")
    return result
