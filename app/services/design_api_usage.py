"""仕様セット内で共通コンポーネントを参照する操作を探す。"""

from uuid import UUID

from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import JSONPATH
from sqlalchemy.orm import Session

from app.models.specification import Specification
from app.schemas.design_workspace import (
    OpenApiComponentKind,
    OpenApiUsageItem,
    OpenApiUsageRead,
)
from app.services.design_impact_shared import used_components
from app.services.design_workspace import collection


def usage(
    db: Session,
    project_id: int,
    collection_id: UUID,
    category: OpenApiComponentKind,
    name: str,
    page: int,
    page_size: int,
) -> OpenApiUsageRead:
    """API本体の代わりに参照・パス・版だけを取得する。"""
    target = collection(db, project_id, collection_id, "api_set")
    config = target.configuration
    design = Specification
    operation = design.content["operation"]
    query = (
        select(
            design.id,
            design.title,
            design.version,
            design.content["path"].as_string().label("path"),
            design.content["method"].as_string().label("method"),
            operation["security"].label("security"),
            func.jsonb_path_query_array(
                operation, cast('lax $.**."$ref"', JSONPATH)
            ).label("refs"),
            func.jsonb_path_query_array(
                operation, cast('lax $.**."$dynamicRef"', JSONPATH)
            ).label("dynamic_refs"),
        )
        .where(
            design.project_id == project_id,
            design.kind == "api",
            design.deleted_at.is_(None),
            design.content["collection_id"].as_string() == str(collection_id),
        )
        .order_by(design.id)
    )
    result = OpenApiUsageRead(items=[], total=0, page=page, page_size=page_size)
    offset = (page - 1) * page_size
    for item in db.execute(query).yield_per(200):
        refs = {
            ref
            for ref in [*(item.refs or []), *(item.dynamic_refs or [])]
            if isinstance(ref, str)
        }
        used = used_components(
            config, {"references": [{"$ref": ref} for ref in refs]}, item.path or ""
        )
        security = (
            item.security if item.security is not None else config.get("security", [])
        )
        security_match = (
            category == "securitySchemes"
            and isinstance(security, list)
            and any(
                isinstance(requirement, dict) and name in requirement
                for requirement in security
            )
        )
        if (category, name) not in used and not security_match:
            continue
        if offset <= result.total < offset + page_size:
            result.items.append(
                OpenApiUsageItem(
                    id=item.id,
                    title=item.title,
                    version=item.version,
                    path=item.path or "",
                    method=item.method,
                )
            )
        result.total += 1
    return result
