"""設計のMCP操作をHTTP APIと同じサービスに接続する。"""

from sqlalchemy.orm import Session

from app.schemas.mcp_design import DesignArtifactInput
from app.schemas.specification import SpecificationRead
from app.services.design_formats import export_ddl, export_openapi
from app.services.specification import SpecificationService

SERVICE = SpecificationService()


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
