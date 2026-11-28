"""設計ワークスペースの案件内API。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.core.exceptions import ForbiddenError
from app.routers.specifications import Creator, Db, Editor, Reader
from app.schemas.design_content import ArtifactKind, HtmlMockup
from app.schemas.design_review_request import (
    DesignReviewCreate,
    DesignReviewDecision,
    DesignReviewer,
    DesignReviewRead,
)
from app.schemas.design_workspace import (
    CollectionInput,
    CollectionKind,
    CollectionRead,
    DesignDiagram,
    DesignImpactRead,
    DesignImpactUpdate,
    DesignOverview,
    FormatExport,
    MembershipUpdate,
    MockupPreviewRead,
    OpenApiComponentKind,
    OpenApiImport,
    OpenApiResult,
    OpenApiUsageRead,
    PlacementRead,
    PlacementUpdate,
)
from app.schemas.screen_list import (
    ScreenListBatch,
    ScreenListMember,
    ScreenListQuery,
    ScreenListRead,
    ScreenListSaved,
)
from app.schemas.specification import SpecificationRead
from app.schemas.wireframe_part import (
    WireframePartCreate,
    WireframePartRead,
    WireframePartSummary,
    WireframePartUpdate,
)
from app.services.body_image import stored_mockup_preview
from app.services.design_api_usage import usage
from app.services.design_formats import export_ddl, export_openapi, import_specification
from app.services.design_impact import analyze_impact, save_impact
from app.services.design_review_request import DesignReviewService
from app.services.design_workspace import DesignWorkspaceService
from app.services.screen_list import ScreenListService
from app.services.wireframe_part import create_part, list_parts, read_part, update_part

router = APIRouter(
    prefix="/projects/{project_id}/design-workspace", tags=["design-workspace"]
)
service = DesignWorkspaceService()
review = DesignReviewService()
screens = ScreenListService()


@router.get("/screen-list", response_model=ScreenListRead)
def design_screen_list(
    project_id: int, params: Annotated[ScreenListQuery, Query()], db: Db, user: Reader
):
    """案件の画面を関連、担当、表示条件とともに取得する。"""
    return screens.listing(db, project_id, params, user)


@router.get("/screen-list/members", response_model=list[ScreenListMember])
def design_screen_list_members(project_id: int, db: Db, user: Editor):
    """画面の担当者に指定できる案件内のメンバーを取得する。"""
    return screens.members(db, project_id)


@router.post("/screen-list/edits", response_model=list[ScreenListSaved])
def edit_design_screen_list(
    project_id: int, data: ScreenListBatch, db: Db, user: Editor
):
    """対象版と権限を照合して、新規行と既存行をまとめて保存する。"""
    return screens.save(db, project_id, user, data)


@router.get("/review-requests", response_model=list[DesignReviewRead])
def design_review_requests(
    project_id: int, db: Db, user: Reader, pending_only: bool = True
):
    """自分宛て、または自分が依頼したレビュー・承認を取得する。"""
    return review.listing(db, project_id, user, pending_only)


@router.get("/review-requests/candidates", response_model=list[DesignReviewer])
def design_review_candidates(project_id: int, db: Db, user: Editor):
    """レビューと設計編集権限を持つ案件内の依頼先を返す。"""
    return review.candidates(db, project_id, user)


@router.post("/review-requests", status_code=201, response_model=DesignReviewRead)
def create_design_review_request(
    project_id: int, data: DesignReviewCreate, db: Db, user: Editor
):
    """対象の版を照合して、まとめて1件の依頼にする。"""
    return review.create(db, project_id, user, data)


@router.post("/review-requests/{request_id}/decision", response_model=DesignReviewRead)
def decide_design_review_request(
    project_id: int, request_id: UUID, data: DesignReviewDecision, db: Db, user: Editor
):
    """指定された依頼先が固定した設計を確認・承認する。"""
    return review.decide(db, project_id, request_id, user, data)


@router.get("/overview", response_model=DesignOverview)
def design_overview(project_id: int, db: Db, user: Reader):
    """設計の件数、機能グループと最近の更新を取得する。"""
    can_read_requirements = service.specifications.can_read_target(
        db, user, project_id, "requirement"
    )
    return service.overview(db, project_id, can_read_requirements)


@router.get("/collections", response_model=list[CollectionRead])
def design_collections(
    project_id: int, db: Db, user: Reader, kind: CollectionKind | None = None
):
    """機能グループ・API仕様セット・DBモデルを取得する。"""
    return service.collections(db, project_id, kind)


@router.post("/collections", status_code=201, response_model=CollectionRead)
def create_design_collection(
    project_id: int, data: CollectionInput, db: Db, user: Creator
):
    """機能グループ・API仕様セット・DBモデルを作成する。"""
    return service.save_collection(db, project_id, None, data)


@router.get("/collections/{collection_id}/api-usage", response_model=OpenApiUsageRead)
def design_api_component_usage(
    project_id: int,
    collection_id: UUID,
    db: Db,
    user: Reader,
    category: OpenApiComponentKind,
    name: str = Query(min_length=1, max_length=200),
    page: int = Query(default=1, ge=1, le=10000),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """共通コンポーネントを参照するAPIと版を返す。"""
    return usage(db, project_id, collection_id, category, name, page, page_size)


@router.patch("/collections/{collection_id}", response_model=CollectionRead)
def update_design_collection(
    project_id: int, collection_id: UUID, data: CollectionInput, db: Db, user: Editor
):
    """既存コレクションを版付きで更新する。"""
    return service.save_collection(db, project_id, collection_id, data)


@router.put("/memberships/{specification_id}", response_model=SpecificationRead)
def update_design_memberships(
    project_id: int, specification_id: int, data: MembershipUpdate, db: Db, user: Editor
):
    """設計書の機能グループへの所属を変更する。"""
    return service.save_memberships(db, project_id, specification_id, data, user)


@router.get("/diagram", response_model=DesignDiagram)
def design_diagram(
    project_id: int,
    kind: ArtifactKind,
    db: Db,
    user: Reader,
    group_id: UUID | None = None,
    collection_id: UUID | None = None,
    include_references: bool = False,
    namespace: str = Query(default="", max_length=63),
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
):
    """図と一覧の共通データを本文なしで取得する。"""
    return service.diagram(
        db,
        project_id,
        kind,
        group_id,
        q,
        page,
        page_size,
        collection_id=collection_id,
        include_references=include_references,
        namespace=namespace,
    )


@router.put("/placements", response_model=list[PlacementRead])
def update_design_placements(
    project_id: int, data: PlacementUpdate, db: Db, user: Editor
):
    """配置変更をまとめて保存する。"""
    return service.place(db, project_id, data)


@router.post("/mockup-preview", response_model=MockupPreviewRead)
def preview_design_mockup(project_id: int, data: HtmlMockup, db: Db, user: Reader):
    """下書き原本を許可リストで変換し、iframe専用文書を返す。"""
    document, css_blocked = stored_mockup_preview(db, project_id, user.id, data)
    return MockupPreviewRead(document=document, css_blocked=css_blocked)


@router.get("/wireframe-parts", response_model=list[WireframePartSummary])
def design_wireframe_parts(project_id: int, db: Db, user: Reader):
    """案件の共通部品一覧を取得する。"""
    return list_parts(db, project_id)


@router.get("/wireframe-parts/{part_id}", response_model=WireframePartRead)
def read_design_wireframe_part(
    project_id: int,
    part_id: UUID,
    db: Db,
    user: Reader,
    version: int | None = Query(default=None, ge=1, le=2_147_483_647),
):
    """共通部品の最新または指定の固定版を取得する。"""
    return read_part(db, project_id, part_id, version)


@router.post("/wireframe-parts", status_code=201, response_model=WireframePartRead)
def create_design_wireframe_part(
    project_id: int, data: WireframePartCreate, db: Db, user: Creator
):
    """選んだレイアウトを案件共通部品として登録する。"""
    return create_part(db, project_id, data)


@router.patch("/wireframe-parts/{part_id}", response_model=WireframePartRead)
def update_design_wireframe_part(
    project_id: int, part_id: UUID, data: WireframePartUpdate, db: Db, user: Editor
):
    """共通部品の新しい版を作成する。"""
    return update_part(db, project_id, part_id, data)


@router.post("/openapi/import", response_model=OpenApiResult)
def import_design_openapi(project_id: int, data: OpenApiImport, db: Db, user: Editor):
    """仕様セットの差分を取得し、選択した操作だけを取り込む。"""
    if data.selections is not None:
        for permission in ("document:create", "document:update"):
            if not service.specifications.authorization.has_project_permission(
                db, user=user, project_id=project_id, permission_code=permission
            ):
                raise ForbiddenError()
    return import_specification(db, project_id, data, user)


@router.get("/openapi", response_model=FormatExport)
def export_design_openapi(
    project_id: int,
    db: Db,
    user: Reader,
    collection_id: UUID | None = None,
    design_id: int | None = Query(default=None, ge=1),
    revision: int | None = Query(default=None, ge=1),
):
    """仕様セットの下書き、または指定された固定版のOpenAPIを取得する。"""
    return export_openapi(db, project_id, collection_id, design_id, revision)


@router.get("/ddl", response_model=FormatExport)
def export_design_ddl(
    project_id: int,
    db: Db,
    user: Reader,
    collection_id: UUID | None = None,
    design_id: int | None = Query(default=None, ge=1),
    revision: int | None = Query(default=None, ge=1),
):
    """PostgreSQL向けDDLを生成し、実DBには接続しない。"""
    return export_ddl(db, project_id, collection_id, design_id, revision)


@router.get("/impact/{specification_id}", response_model=DesignImpactRead)
def read_design_impact(
    project_id: int,
    specification_id: int,
    db: Db,
    user: Reader,
    base_version: int | None = Query(default=None, ge=1),
):
    """設計の版を比較し、関連先への確認候補を返す。"""
    row = service.specifications.get(db, project_id, specification_id)
    return analyze_impact(db, project_id, row, user, base_version)


@router.put("/impact/{specification_id}", response_model=DesignImpactRead)
def update_design_impact(
    project_id: int,
    specification_id: int,
    data: DesignImpactUpdate,
    db: Db,
    user: Editor,
):
    """影響の確認状態だけを更新する。"""
    return save_impact(db, project_id, specification_id, data, user)
