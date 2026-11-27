"""共通部品を案件に限定し、本文をイミュータブルな版として保存する。"""

from uuid import UUID, uuid4

from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, NotFoundError, VersionConflictError
from app.models.design_workspace import WireframePartRevision
from app.schemas.design_content import WireframeArtboard
from app.schemas.wireframe_part import (
    WireframePartCreate,
    WireframePartSummary,
    WireframePartUpdate,
)


def list_parts(db: Session, project_id: int) -> list[WireframePartSummary]:
    """部品ごとに最新の名前と版のみを一覧に返す。"""
    part = WireframePartRevision
    latest = (
        select(part.part_id, func.max(part.version).label("version"))
        .where(part.project_id == project_id)
        .group_by(part.part_id)
        .subquery()
    )
    rows = db.execute(
        select(part.part_id, part.name, part.version)
        .join(
            latest,
            (part.part_id == latest.c.part_id) & (part.version == latest.c.version),
        )
        .where(part.project_id == project_id)
        .order_by(part.name, part.part_id)
    )
    return [WireframePartSummary.model_validate(row) for row in rows]


def read_part(
    db: Session, project_id: int, part_id: UUID, version: int | None = None
) -> WireframePartRevision:
    """同じ案件の指定版を読み出す。"""
    part = WireframePartRevision
    query = select(part).where(part.project_id == project_id, part.part_id == part_id)
    if version is not None:
        query = query.where(part.version == version)
    row = db.scalar(query.order_by(part.version.desc()).limit(1))
    if row is None:
        raise NotFoundError("共通部品の版が見つかりません")
    return row


def create_part(
    db: Session, project_id: int, data: WireframePartCreate
) -> WireframePartRevision:
    """部品の初版を作成する。"""
    row = WireframePartRevision(
        project_id=project_id,
        part_id=uuid4(),
        version=1,
        name=data.name,
        content=data.content.model_dump(mode="json"),
    )
    db.add(row)
    db.commit()
    return row


def update_part(
    db: Session, project_id: int, part_id: UUID, data: WireframePartUpdate
) -> WireframePartRevision:
    """読み込んだ最新の次の版だけを追加し、並行更新時は競合にする。"""
    current = read_part(db, project_id, part_id)
    if current.version != data.version:
        raise VersionConflictError(
            WireframePartSummary.model_validate(current).model_dump(mode="json")
        )
    part = WireframePartRevision
    row = db.scalar(
        insert(part)
        .values(
            project_id=project_id,
            part_id=part_id,
            version=data.version + 1,
            name=data.name,
            content=data.content.model_dump(mode="json"),
        )
        .on_conflict_do_nothing(
            index_elements=[part.project_id, part.part_id, part.version]
        )
        .returning(part)
    )
    if row is None:
        latest = read_part(db, project_id, part_id)
        raise VersionConflictError(
            WireframePartSummary.model_validate(latest).model_dump(mode="json")
        )
    db.commit()
    return row


def check_part_pins(
    db: Session, project_id: int, boards: list[WireframeArtboard]
) -> None:
    """同じ案件に実在する固定版だけを画面へ配置できるようにする。"""
    pins = {
        (item.part_pin.part_id, item.part_pin.version)
        for board in boards
        for item in board.components
        if item.part_pin is not None
    }
    if not pins:
        return
    part = WireframePartRevision
    present = {
        (row.part_id, row.version)
        for row in db.execute(
            select(part.part_id, part.version).where(
                part.project_id == project_id,
                tuple_(part.part_id, part.version).in_(pins),
            )
        )
    }
    if pins != present:
        raise BadRequestError("共通部品は同じ案件にある登録済みの版を指定してください")
