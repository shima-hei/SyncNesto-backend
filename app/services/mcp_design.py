"""設計のMCP操作をHTTP APIと同じサービスに接続する。"""

from copy import deepcopy
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, NotFoundError, VersionConflictError
from app.models.user import User
from app.schemas.design_workspace import CollectionRead
from app.schemas.mcp_design import (
    DesignArtifactInput,
    DesignArtifactPatchBatch,
    DesignItemsInput,
)
from app.schemas.specification import (
    SpecificationListItem,
    SpecificationRead,
    SpecificationUpdate,
)
from app.services.design_formats import export_ddl, export_openapi
from app.services.design_impact import analyze_impact, save_impact
from app.services.design_review_request import DesignReviewService
from app.services.design_workspace import DesignWorkspaceService, collection
from app.services.specification import SpecificationService

SERVICE = SpecificationService()
WORKSPACE = DesignWorkspaceService()

SECTIONS = {
    "screen": {
        "fields",
        "actions",
        "states",
        "transitions",
        "artboards",
        "components",
        "mockups",
        "mappings",
    },
    "api": {
        "request_fields",
        "response_fields",
        "responses",
        "processing_steps",
        "mappings",
    },
    "database": {"columns", "constraints", "indexes", "relationships", "mappings"},
}


def read_artifact(db: Session, data: DesignArtifactInput) -> dict:
    """概要、構造、モックアップ、仕様出力を選択して取得する。"""
    if data.section in {"openapi", "ddl"}:
        export = export_openapi if data.section == "openapi" else export_ddl
        return export(
            db, data.project_id, None, data.design_id, data.revision
        ).model_dump(mode="json")
    if data.revision:
        result = SERVICE.revision(
            db, data.project_id, data.design_id, data.revision
        ).snapshot.copy()
    else:
        row = SERVICE.get(db, data.project_id, data.design_id)
        result = SpecificationRead.model_validate(row).model_dump(mode="json")
    content = result.pop("content", {})
    if data.section == "structure":
        result["content"] = {
            key: value for key, value in content.items() if key != "mockups"
        }
        result["mockup_count"] = len(content.get("mockups", []))
    if data.section == "mockups":
        result["mockups"] = content.get("mockups", [])
    return result


def read_items(db: Session, data: DesignItemsInput) -> dict:
    """大きいHTMLやレイアウト本体は専用指定で取得する。"""
    if data.revision is not None:
        snapshot = SERVICE.revision(
            db, data.project_id, data.design_id, data.revision
        ).snapshot
        content, version = snapshot["content"], data.revision
    else:
        row = SERVICE.get(db, data.project_id, data.design_id)
        content, version = row.content, row.version
    items = _section(content, data.section, data.parent_id)
    if data.item_ids is not None:
        ids = {str(identifier) for identifier in data.item_ids}
        items = [item for item in items if item.get("id") in ids]
    return {
        "design_id": data.design_id,
        "version": version,
        "source": "revision" if data.revision else "current",
        "section": data.section,
        **_page([_compact_item(data.section, item) for item in items], data),
    }


def _section(
    content: dict, section: str, parent_id: UUID | None, *, for_edit: bool = False
):
    kind = content.get("type")
    if not isinstance(kind, str) or section not in SECTIONS.get(kind, set()):
        raise BadRequestError("設計の種類に含まれる構成を指定してください")
    if section != "components":
        return content.setdefault(section, []) if for_edit else content.get(section, [])
    parent = next(
        (
            item
            for item in content.get("artboards", [])
            if item.get("id") == str(parent_id)
        ),
        None,
    )
    if parent is None:
        raise NotFoundError("アートボードが見つかりません")
    return (
        parent.setdefault("components", [])
        if for_edit
        else parent.get("components", [])
    )


def _compact_item(section: str, item: dict) -> dict:
    if section == "mockups":
        return {key: value for key, value in item.items() if key not in {"html", "css"}}
    if section == "artboards":
        return {
            **{key: value for key, value in item.items() if key != "components"},
            "component_count": len(item.get("components", [])),
        }
    return item


def _page(items: list, data) -> dict:
    start = (data.page - 1) * data.page_size
    return {
        "items": items[start : start + data.page_size],
        "total": len(items),
        "page": data.page,
        "page_size": data.page_size,
    }


def patch_artifacts(db: Session, actor: User, data: DesignArtifactPatchBatch) -> dict:
    """順に行をロックし、全行を照合してから同じ保存処理に渡す。"""
    prepared = db.no_autoflush
    updates = []
    with prepared:
        for patch in sorted(data.updates, key=lambda item: item.design_id):
            row = SERVICE.get(db, data.project_id, patch.design_id, lock=True)
            if row.version != patch.version:
                raise VersionConflictError(
                    SpecificationListItem.model_validate(row).model_dump(mode="json")
                )
            content = deepcopy(row.content)
            content.update(patch.settings)
            for edit in patch.items:
                items = _section(content, edit.section, edit.parent_id, for_edit=True)
                identifier = str(edit.item_id)
                index = next(
                    (
                        index
                        for index, item in enumerate(items)
                        if item.get("id") == identifier
                    ),
                    None,
                )
                if edit.mode == "add":
                    if index is not None:
                        raise BadRequestError("追加する項目IDがすでに存在します")
                    items.append({**edit.values, "id": identifier})
                elif index is None:
                    raise NotFoundError("変更する設計項目が見つかりません")
                elif edit.mode == "remove":
                    items.pop(index)
                else:
                    items[index] = {**items[index], **edit.values}
            try:
                command = SpecificationUpdate.model_validate(
                    {
                        "title": row.title if patch.title is None else patch.title,
                        "summary": row.summary
                        if patch.summary is None
                        else patch.summary,
                        "content": content,
                        "version": patch.version,
                    }
                )
            except ValidationError as exc:
                raise BadRequestError(
                    "変更後の設計内容が不正です。構成と参照先を確認してください"
                ) from exc
            updates.append((row, command))
    for row, command in updates:
        SERVICE.stage_update(db, row, command, actor)
    return {
        "items": [
            SpecificationListItem.model_validate(row).model_dump(mode="json")
            for row, _ in updates
        ]
    }


def read_design(db: Session, actor: User, name: str, data) -> dict:
    if name == "get_design_items":
        return read_items(db, data)
    if name == "get_design_artifact":
        return read_artifact(db, data)
    if name == "list_design_artifacts":
        return SERVICE.list(
            db, data.project_id, data.kind, data.q, data.page, data.page_size
        ).model_dump(mode="json")
    if name == "list_design_collections":
        return {
            "items": [
                CollectionRead.model_validate(row).model_dump(
                    mode="json", exclude={"configuration"}
                )
                for row in WORKSPACE.collections(db, data.project_id)
            ]
        }
    if name == "get_design_collection":
        return CollectionRead.model_validate(
            collection(db, data.project_id, data.collection_id, data.kind)
        ).model_dump(mode="json")
    if name == "get_design_diagram":
        return WORKSPACE.diagram(
            db,
            data.project_id,
            data.kind,
            data.group_id,
            data.q,
            data.page,
            data.page_size,
            collection_id=data.collection_id,
            include_references=data.include_references,
        ).model_dump(mode="json")
    if name == "get_design_links":
        return _page(
            [
                item.model_dump(mode="json")
                for item in SERVICE.links(db, data.project_id, data.design_id, actor)
            ],
            data,
        )
    if name == "get_design_impact":
        row = SERVICE.get(db, data.project_id, data.design_id)
        result = analyze_impact(
            db, data.project_id, row, actor, data.base_version
        ).model_dump(mode="json")
        return {**result, **_page(result["items"], data)}
    if name == "list_design_review_requests":
        return {
            "items": [
                item.model_dump(mode="json")
                for item in DesignReviewService().listing(
                    db, data.project_id, actor, data.pending_only
                )
            ]
        }
    raise NotFoundError()


def write_design(db: Session, actor: User, name: str, data) -> dict:
    if name == "patch_design_artifacts":
        return patch_artifacts(db, actor, data)
    if name in {"create_design_collection", "update_design_collection"}:
        value = WORKSPACE.save_collection(
            db, data.project_id, getattr(data, "collection_id", None), data.data
        )
        return CollectionRead.model_validate(value).model_dump(
            mode="json", exclude={"configuration"}
        )
    if name == "save_design_impact_decision":
        return save_impact(
            db, data.project_id, data.design_id, data.data, actor
        ).model_dump(mode="json")
    if name == "create_design_artifact":
        row = SERVICE.create(db, data.project_id, data.data, actor)
    elif name == "update_design_artifact":
        row = SERVICE.update(db, data.project_id, data.design_id, data.data, actor)
    else:
        raise NotFoundError()
    return SpecificationListItem.model_validate(row).model_dump(mode="json")
