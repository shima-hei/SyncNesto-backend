"""Markdown本文で参照する、版をまたいで不変の非公開画像。"""

import re
from datetime import UTC, datetime
from io import BytesIO
from typing import Literal
from uuid import UUID

from markdown_it import MarkdownIt
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.comment_image import BODY_KINDS, CommentImage
from app.services.comment_image import DRAFT_RETENTION, CommentImageService
from app.services.storage import StorageService

BodyImageKind = Literal[
    "task", "requirement", "specification", "test_design", "document"
]
BODY_IMAGE_PREFIX = "/api/body-images/"
MAX_PROJECT_IMAGES = 512
MAX_BODY_IMAGES = 32
MAX_IMAGE_PIXELS = 20_000_000
MEDIA_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
IMAGE_REFERENCE = re.compile(
    r"/api/body-images/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)
PARSER = MarkdownIt("commonmark", {"html": False}).enable("table")
SNAPSHOT_FIELDS = {
    "requirement_document": (
        "requirement",
        ("purpose", "project_goal", "project_scope", "project_completion_criteria"),
    ),
    "section": ("requirement", ("content",)),
    "requirement": ("requirement", ("description", "rationale", "acceptance_criteria")),
    "specification": ("specification", ("summary",)),
    "test_design": ("test_design", ("description",)),
}


class BodyImageService(CommentImageService):
    """コメントと同じ送信手順を使い、本文参照とコメントの所属を分ける。"""

    kinds = BODY_KINDS

    def validate_content(
        self, storage: StorageService, filename: str, content_type: str, content: bytes
    ) -> str:
        """ファイル実体と展開後の大きさを確認する。"""
        name = super().validate_content(storage, filename, content_type, content)
        try:
            with Image.open(BytesIO(content)) as image:
                if (
                    MEDIA_TYPES.get(image.format or "") != content_type
                    or image.width * image.height > MAX_IMAGE_PIXELS
                    or getattr(image, "is_animated", False)
                ):
                    raise BadRequestError("画像の形式・解像度を確認してください。")
                image.verify()
            with Image.open(BytesIO(content)) as image:
                image.load()
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            raise BadRequestError("画像を読み込めませんでした。") from None
        return name

    def check_project_limit(self, db: Session, project_id: int) -> None:
        """案件全体の画像件数・最大容量を制限する。"""
        count = db.scalar(
            select(func.count(CommentImage.id)).where(
                CommentImage.project_id == project_id, CommentImage.kind.in_(self.kinds)
            )
        )
        if (count or 0) >= MAX_PROJECT_IMAGES:
            raise BadRequestError("案件の本文画像が上限に達しています。")

    def stored_image(self, db: Session, image_id: UUID) -> CommentImage:
        """通常の組織スコープで本文用の画像だけを取得する。"""
        row = db.get(CommentImage, image_id)
        if row is None or row.kind not in self.kinds:
            raise NotFoundError()
        return row


def image_references(*values: object) -> set[UUID]:
    """コード例や普通のリンクを除き、Markdown画像の永続IDを取得する。"""
    references = set()
    for value in values:
        if not isinstance(value, str) or BODY_IMAGE_PREFIX not in value:
            continue
        if f"{BODY_IMAGE_PREFIX}pending/" in value:
            raise BadRequestError(
                "本文画像のアップロードを完了するか、取り消してください。"
            )
        for token in PARSER.parse(value):
            for child in token.children or ():
                if child.type == "image":
                    src = child.attrGet("src")
                    match = (
                        IMAGE_REFERENCE.fullmatch(src) if isinstance(src, str) else None
                    )
                    if match:
                        references.add(UUID(match[1]))
    return references


def retain_body_images(
    db: Session,
    project_id: int,
    kind: BodyImageKind,
    actor_id: int | None,
    *values: object,
) -> None:
    """保存と同じトランザクションで、同じ案件・用途の画像を確定する。"""
    references = image_references(*values)
    if not references:
        return
    if len(references) > MAX_BODY_IMAGES:
        raise BadRequestError("本文に挿入できる画像は32枚までです。")
    rows = db.scalars(
        select(CommentImage)
        .where(
            CommentImage.id.in_(references),
            CommentImage.project_id == project_id,
            CommentImage.kind == f"body_{kind}",
        )
        .order_by(CommentImage.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    now = datetime.now(UTC)
    if len(rows) != len(references) or any(
        row.retained_at is None
        and (
            actor_id is None
            or row.uploaded_by != actor_id
            or row.created_at < now - DRAFT_RETENTION
        )
        for row in rows
    ):
        raise BadRequestError("本文画像を確認できません。画像を選び直してください。")
    for row in rows:
        if row.retained_at is None:
            row.retained_at = now


def snapshot_references(node_kind: str, payload: dict) -> tuple[str, set[UUID]]:
    """実際に描画する本文フィールドの参照だけを返す。"""
    kind, fields = SNAPSHOT_FIELDS.get(node_kind, ("", ()))
    if node_kind == "test_design" and "body_image_ids" in payload:
        return kind, {UUID(value) for value in payload["body_image_ids"]}
    return kind, image_references(*(payload.get(field) for field in fields))


def ensure_snapshot_images(db: Session, project_id: int, nodes: list[dict]) -> None:
    """確定前の画像や別案件・用途の画像を含む版を作らない。"""
    references = {
        (kind, image_id)
        for node in nodes
        for kind, ids in [snapshot_references(node["node_kind"], node["payload"])]
        for image_id in ids
    }
    if not references:
        return
    rows = db.execute(
        select(CommentImage.kind, CommentImage.id).where(
            CommentImage.id.in_(image_id for _, image_id in references),
            CommentImage.project_id == project_id,
            CommentImage.retained_at.is_not(None),
        )
    ).all()
    if not references.issubset(
        {(kind.removeprefix("body_"), image_id) for kind, image_id in rows}
    ):
        raise BadRequestError("本文画像のアップロードと保存を完了してください。")
