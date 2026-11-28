"""OpenAPIとDB設計の出力、および選択したOpenAPI操作の取込。"""

from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError
from app.models.specification import Specification, SpecificationRevision
from app.models.user import User
from app.schemas.design_content import DatabaseSpecification
from app.schemas.design_workspace import (
    FormatExport,
    OpenApiChange,
    OpenApiImport,
    OpenApiResult,
)
from app.schemas.specification import (
    ApiSpecification,
    SpecificationCreate,
    SpecificationRead,
)
from app.services.conflict import raise_if_version_conflict
from app.services.design_ddl import generate_ddl
from app.services.design_openapi import check_document, legacy_operation, operations
from app.services.design_workspace import collection, lock_project
from app.services.specification import SpecificationService


def format_rows(db: Session, project_id: int, kind: str, collection_id: UUID | None):
    """対象セット・モデルの設計だけを読み出す。"""
    return list(
        db.scalars(
            select(Specification)
            .where(
                Specification.project_id == project_id,
                Specification.kind == kind,
                Specification.deleted_at.is_(None),
                Specification.content["collection_id"].as_string() == str(collection_id)
                if collection_id is not None
                else Specification.content["collection_id"].as_string().is_(None),
            )
            .order_by(Specification.id)
            .limit(501)
        )
    )


def import_specification(
    db: Session, project_id: int, data: OpenApiImport, user: User
) -> OpenApiResult:
    """差分を再計算し、明示選択した操作だけを一括で下書きへ反映する。"""
    parsed = operations(data.document)
    if data.selections is not None:
        lock_project(db, project_id)
    target = collection(db, project_id, data.collection_id, "api_set")
    raise_if_version_conflict(
        current_version=target.version,
        requested_version=data.collection_version,
        current={"version": target.version, "name": target.name},
    )
    rows = format_rows(db, project_id, "api", data.collection_id)
    if len(rows) > 500:
        raise BadRequestError("仕様セットのAPI操作が500件を超えています")
    legacy = {row.id: ApiSpecification.model_validate(row.content) for row in rows}
    by_path = {
        (content.method, content.path): row
        for row in rows
        if (content := legacy[row.id]).path and content.method
    }
    by_name = {
        content.operation["operationId"]: row
        for row in rows
        if (content := legacy[row.id]).operation
        and isinstance(content.operation.get("operationId"), str)
    }
    candidates = {}
    report: list[OpenApiChange] = []
    seen: set[int] = set()
    for method, path, operation in parsed:
        key = f"{method} {path}"
        name = operation.get("summary") or operation.get("operationId") or key
        if not isinstance(name, str):
            raise BadRequestError("OpenAPI summaryは文字列にしてください")
        named = by_name.get(operation.get("operationId"))
        matched = by_path.get((method, path))
        if named is not None and matched is not None and named.id != matched.id:
            raise BadRequestError(
                "operationIdとメソッド・パスが別の既存APIに一致します"
            )
        row = named or matched
        if row is not None and row.id in seen:
            raise BadRequestError("複数の操作が同一の既存APIに一致します")
        candidates[key] = (method, path, operation, row)
        if row is not None:
            seen.add(row.id)
        unchanged = (
            row is not None
            and legacy[row.id].operation == operation
            and (method, path) == (legacy[row.id].method, legacy[row.id].path)
        )
        report.append(
            OpenApiChange(
                key=key,
                action="same" if unchanged else "edit" if row is not None else "add",
                title=name,
                specification_id=row.id if row is not None else None,
                version=row.version if row is not None else 0,
            )
        )
    for row in rows:
        if row.id not in seen and legacy[row.id].path and legacy[row.id].method:
            report.append(
                OpenApiChange(
                    key=f"{legacy[row.id].method} {legacy[row.id].path}",
                    action="retain",
                    title=row.title,
                    specification_id=row.id,
                    version=row.version,
                )
            )
    if data.selections is None:
        return OpenApiResult(changes=report)
    if not data.selections.keys() <= candidates.keys():
        raise BadRequestError("取込候補を確認し直してください")
    service = SpecificationService()
    updated: list[Specification] = []
    for key, expected in data.selections.items():
        method, path, operation, candidate = candidates[key]
        if candidate is None:
            raise_if_version_conflict(
                current_version=0, requested_version=expected, current={}
            )
            content = ApiSpecification.model_validate(
                {
                    "collection_id": target.id,
                    "method": method,
                    "path": path,
                    "operation": operation,
                }
            )
            title = (
                str(
                    operation.get("summary") or operation.get("operationId") or key
                ).strip()[:200]
                or key[:200]
            )
            checked = SpecificationCreate(title=title, content=content)
            row = Specification(
                project_id=project_id,
                kind="api",
                title=checked.title,
                summary="",
                content=content.model_dump(mode="json"),
                status="draft",
                version=1,
                created_by=user.id,
                updated_by=user.id,
            )
            db.add(row)
        else:
            row = service.get(db, project_id, candidate.id, lock=True)
            service.check_version(row, expected)
            content = ApiSpecification.model_validate(
                {
                    **row.content,
                    "method": method,
                    "path": path,
                    "operation": operation,
                }
            )
            SpecificationCreate(title=row.title, summary=row.summary, content=content)
            row.content = content.model_dump(mode="json")
            row.version += 1
            row.status = "draft"
            row.updated_by = user.id
        service.repository.revision(db, row, user.id)
        updated.append(row)
    target.configuration = {
        key: value for key, value in data.document.items() if key != "paths"
    }
    from app.schemas.design_workspace import CollectionInput

    CollectionInput(
        kind="api_set", name=target.name, configuration=target.configuration
    )
    target.version += 1
    db.commit()
    for row in updated:
        service.audit(db, row, user.id, "openapi_imported")
    return OpenApiResult(changes=report, applied=len(updated))


def export_openapi(
    db: Session,
    project_id: int,
    collection_id: UUID | None,
    design_id: int | None,
    revision: int | None,
) -> FormatExport:
    """下書きの仕様セット、または指定された1操作の固定版を出力する。"""
    if revision is not None and design_id is None:
        raise BadRequestError("固定版の出力には設計IDを指定してください")
    rows: list[tuple[str, dict]] = []
    document: dict = {}
    specifications = SpecificationService()
    if design_id is not None:
        if revision is not None:
            fixed = specifications.revision(
                db, project_id, design_id, revision
            ).snapshot
            rows.append((fixed["title"], fixed["content"]))
            document = dict(fixed.get("collection", {}).get("configuration", {}))
        else:
            row = specifications.get(db, project_id, design_id)
            rows.append((row.title, row.content))
            if row.content.get("collection_id"):
                collection_id = UUID(row.content["collection_id"])
    else:
        loaded = format_rows(db, project_id, "api", collection_id)
        if len(loaded) > 500:
            raise BadRequestError("一度に出力できるAPI操作は500件までです")
        rows.extend((row.title, row.content) for row in loaded)
    if collection_id is not None and revision is None:
        target = collection(db, project_id, collection_id, "api_set")
        document = dict(target.configuration)
    document.setdefault("openapi", "3.1.1")
    document.setdefault("info", {"title": "API設計", "version": "0.1.0"})
    document["paths"] = {}
    warnings = []
    for title, raw in rows:
        if raw["type"] != "api":
            raise BadRequestError("API設計を指定してください")
        content = ApiSpecification.model_validate(raw)
        if not content.path.strip() or content.method is None:
            warnings.append(f"{title}: メソッドまたはパスが未確定のため出力から除外")
            continue
        path = document["paths"].setdefault(content.path, {})
        method = content.method.lower()
        if method in path:
            raise BadRequestError(
                f"メソッドとパスが重複しています（{content.method} {content.path}）"
            )
        path[method] = {
            **(
                content.operation
                if content.operation is not None
                else legacy_operation(content)
            )
        }
        path[method].setdefault("summary", title)
    check_document(document)
    return FormatExport(document=document, warnings=warnings)


def export_ddl(
    db: Session,
    project_id: int,
    collection_id: UUID | None,
    design_id: int | None,
    revision: int | None,
) -> FormatExport:
    """DBモデルの現在値、または指定されたテーブルの固定版を出力する。"""
    if revision is not None:
        if design_id is None:
            raise BadRequestError("固定版の出力には設計IDを指定してください")
        snapshot = (
            SpecificationService()
            .revision(db, project_id, design_id, revision)
            .snapshot
        )
        data = snapshot["content"]
        if data["type"] != "database":
            raise BadRequestError("DB設計を指定してください")
        fixed_table = DatabaseSpecification.model_validate(data)
        referenced_ids = {
            item.target_id
            for item in fixed_table.constraints
            if item.kind == "foreign_key"
        }
        pins = [
            (item["id"], item["version"])
            for item in snapshot.get("dependencies", [])
            if item["kind"] == "database" and item["id"] in referenced_ids
        ]
        external_tables = {}
        if pins:
            stored = SpecificationRevision
            external_tables = {
                identifier: DatabaseSpecification.model_validate(content)
                for identifier, content in db.execute(
                    select(stored.specification_id, stored.snapshot["content"])
                    .join(Specification, Specification.id == stored.specification_id)
                    .where(
                        Specification.project_id == project_id,
                        tuple_(stored.specification_id, stored.number).in_(pins),
                    )
                )
            }
        sql, warnings = generate_ddl(
            {design_id: fixed_table}, external_tables=external_tables
        )
    else:
        if design_id is not None:
            row = SpecificationService().get(db, project_id, design_id)
            data = SpecificationRead.model_validate(row).content
            if not isinstance(data, DatabaseSpecification):
                raise BadRequestError("DB設計を指定してください")
            collection_id = data.collection_id
        if collection_id is not None:
            target = collection(db, project_id, collection_id, "db_model")
            if target.configuration.get("product", "PostgreSQL") != "PostgreSQL":
                raise BadRequestError("DDLの出力対象はPostgreSQLです")
        rows = format_rows(db, project_id, "database", collection_id)
        if len(rows) > 500:
            raise BadRequestError("一度に出力できるテーブルは500件までです")
        sql, warnings = generate_ddl(
            {row.id: DatabaseSpecification.model_validate(row.content) for row in rows}
        )
    return FormatExport(sql=sql, warnings=warnings)
