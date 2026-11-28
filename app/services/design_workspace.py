"""同一案件の設計対象を図・一覧・概要に組み立てる。"""

from collections import defaultdict
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, load_only

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.design_workspace import (
    DesignCollection,
    DesignMembership,
    DesignPlacement,
)
from app.models.project import Project
from app.models.specification import Specification, SpecificationLink
from app.models.user import User
from app.repositories.specification import SUMMARY_COLUMNS
from app.schemas.design_content import ArtifactKind
from app.schemas.design_workspace import (
    CollectionInput,
    CollectionKind,
    CollectionRead,
    DesignCount,
    DesignDiagram,
    DesignEdge,
    DesignNode,
    DesignOverview,
    MembershipUpdate,
    PlacementRead,
    PlacementUpdate,
)
from app.schemas.specification import SpecificationListItem
from app.services.conflict import raise_if_version_conflict
from app.services.specification import SpecificationService


def lock_project(db: Session, project_id: int) -> None:
    """配置とグループ階層の変更を直列化する。"""
    if (
        db.scalar(select(Project.id).where(Project.id == project_id).with_for_update())
        is None
    ):
        raise NotFoundError("案件が見つかりません")


def collection(
    db: Session, project_id: int, collection_id: UUID, kind: str
) -> DesignCollection:
    """指定した案件・種類のコレクションだけを参照する。"""
    row = db.scalar(
        select(DesignCollection).where(
            DesignCollection.id == collection_id,
            DesignCollection.project_id == project_id,
            DesignCollection.kind == kind,
        )
    )
    if row is None:
        raise NotFoundError("設計グループまたはモデルが見つかりません")
    return row


def replace_memberships(
    db: Session, project_id: int, specification_id: int, group_ids: set[UUID]
) -> None:
    """照合済みの機能グループ所属を同じトランザクションで置き換える。"""
    db.execute(
        delete(DesignMembership).where(
            DesignMembership.project_id == project_id,
            DesignMembership.specification_id == specification_id,
        )
    )
    db.add_all(
        DesignMembership(
            project_id=project_id, specification_id=specification_id, group_id=group_id
        )
        for group_id in group_ids
    )


class DesignWorkspaceService:
    """設計書の基礎データを再利用し、設計項目を複製しない。"""

    def __init__(self):
        self.specifications = SpecificationService()

    def collections(
        self, db: Session, project_id: int, kind: CollectionKind | None = None
    ):
        """案件のコレクションを返す。"""
        query = select(DesignCollection).where(
            DesignCollection.project_id == project_id
        )
        if kind is not None:
            query = query.where(DesignCollection.kind == kind)
        return list(
            db.scalars(query.order_by(DesignCollection.name, DesignCollection.id)).all()
        )

    def save_collection(
        self,
        db: Session,
        project_id: int,
        identifier: UUID | None,
        data: CollectionInput,
    ) -> DesignCollection:
        """版と祖先を確認し、循環しない機能グループを保存する。"""
        lock_project(db, project_id)
        parents = {
            row.id: row.parent_id for row in self.collections(db, project_id, "group")
        }
        if data.parent_id is not None and data.parent_id not in parents:
            raise BadRequestError("親グループは同じ案件内から選択してください")
        parent = data.parent_id
        while parent is not None:
            if parent == identifier:
                raise BadRequestError("グループの親子関係が循環しています")
            parent = parents[parent]

        if identifier is None:
            count = db.scalar(
                select(func.count())
                .select_from(DesignCollection)
                .where(DesignCollection.project_id == project_id)
            )
            if (count or 0) >= 200:
                raise BadRequestError(
                    "グループ・仕様セット・DBモデルは案件ごとに200件まで登録できます"
                )
            row = DesignCollection(
                id=uuid4(), project_id=project_id, kind=data.kind, version=0
            )
            db.add(row)
        else:
            row = collection(db, project_id, identifier, data.kind)
        raise_if_version_conflict(
            current_version=row.version,
            requested_version=data.version,
            current=CollectionRead.model_validate(row).model_dump(mode="json")
            if row.version
            else {},
        )
        row.name = data.name
        row.configuration = data.configuration
        row.parent_id = data.parent_id
        row.version += 1
        db.commit()
        return row

    def save_memberships(
        self,
        db: Session,
        project_id: int,
        specification_id: int,
        data: MembershipUpdate,
        user: User,
    ):
        """設計書と所属を同じ版として保存する。"""
        row = self.specifications.get(db, project_id, specification_id, lock=True)
        self.specifications.check_version(row, data.version)
        legal_groups = {item.id for item in self.collections(db, project_id, "group")}
        if not data.group_ids <= legal_groups:
            raise BadRequestError("機能グループは同じ案件内から選択してください")
        replace_memberships(db, project_id, specification_id, data.group_ids)
        if row.status in {"approved", "in_review"}:
            row.status = "draft"
        row.version += 1
        row.updated_by = user.id
        self.specifications.repository.revision(db, row, user.id)
        db.commit()
        self.specifications.audit(db, row, user.id, "memberships_updated")
        return row

    def place(
        self, db: Session, project_id: int, data: PlacementUpdate
    ) -> list[PlacementRead]:
        """既存配置を版付きで更新し、設計書や遷移を削除しない。"""
        lock_project(db, project_id)
        if data.group_id is not None:
            collection(db, project_id, data.group_id, "group")
        view_key = str(data.group_id) if data.group_id is not None else "all"
        ids = {item.specification_id for item in data.placements}
        actual = set(
            db.scalars(
                select(Specification.id).where(
                    Specification.id.in_(ids),
                    Specification.project_id == project_id,
                    Specification.deleted_at.is_(None),
                    Specification.kind.in_(["screen", "database"]),
                )
            )
        )
        if len(ids) != len(data.placements) or ids != actual:
            raise BadRequestError("図に配置できる設計を重複なく選択してください")

        rows = {
            row.specification_id: row
            for row in db.scalars(
                select(DesignPlacement).where(
                    DesignPlacement.project_id == project_id,
                    DesignPlacement.specification_id.in_(ids),
                    DesignPlacement.view_key == view_key,
                )
            )
        }
        result: list[PlacementRead] = []
        for item in data.placements:
            row = rows.get(item.specification_id)
            current = PlacementRead.model_validate(row) if row is not None else None
            raise_if_version_conflict(
                current_version=current.version if current is not None else 0,
                requested_version=item.version,
                current=current.model_dump(mode="json") if current is not None else {},
            )
            if row is None:
                row = DesignPlacement(
                    project_id=project_id,
                    specification_id=item.specification_id,
                    group_id=data.group_id,
                    view_key=view_key,
                    version=0,
                )
                db.add(row)
            row.x, row.y, row.hidden = item.x, item.y, item.hidden
            row.version += 1
            result.append(PlacementRead.model_validate(row))
        db.commit()
        return result

    def overview(
        self, db: Session, project_id: int, can_read_requirements: bool
    ) -> DesignOverview:
        """本文を取得せず、設計状況と次の作業を返す。"""
        active = (
            Specification.project_id == project_id,
            Specification.deleted_at.is_(None),
        )
        records = db.execute(
            select(Specification.kind, Specification.status, func.count())
            .where(*active)
            .group_by(Specification.kind, Specification.status)
        )
        counts = [
            DesignCount(kind=cast(ArtifactKind, kind), status=status, count=count)
            for kind, status, count in records
        ]
        recent = self.specifications.list(db, project_id, None, "", 1, 8).items
        pending = db.scalars(
            select(Specification)
            .options(load_only(*SUMMARY_COLUMNS))
            .where(
                *active,
                Specification.status.in_(["in_review", "changes_requested"]),
            )
            .order_by(Specification.updated_at.desc())
            .limit(15)
        )
        group_counts: dict[str, dict[str, int]] = defaultdict(dict)
        for group_id, kind, count in db.execute(
            select(DesignMembership.group_id, Specification.kind, func.count())
            .join(
                Specification,
                Specification.id == DesignMembership.specification_id,
            )
            .where(*active, DesignMembership.project_id == project_id)
            .group_by(DesignMembership.group_id, Specification.kind)
        ):
            group_counts[str(group_id)][kind] = count
        linked = 0
        if can_read_requirements:
            linked = (
                db.scalar(
                    select(
                        func.count(func.distinct(SpecificationLink.specification_id))
                    )
                    .join(
                        Specification,
                        Specification.id == SpecificationLink.specification_id,
                    )
                    .where(*active, SpecificationLink.requirement_id.is_not(None))
                )
                or 0
            )

        return DesignOverview(
            counts=counts,
            recent=recent,
            pending=[SpecificationListItem.model_validate(row) for row in pending],
            groups=[
                CollectionRead.model_validate(group)
                for group in self.collections(db, project_id, "group")
            ],
            group_counts=group_counts,
            linked_requirements=linked,
        )

    def diagram(
        self,
        db: Session,
        project_id: int,
        kind: ArtifactKind,
        group_id: UUID | None,
        q: str,
        page: int,
        page_size: int,
        *,
        collection_id: UUID | None = None,
        include_references: bool = False,
    ) -> DesignDiagram:
        """設計書から画面遷移・外部キーを投影し、配置と統合する。"""
        if group_id is not None:
            collection(db, project_id, group_id, "group")
        if collection_id is not None:
            if kind == "screen":
                raise BadRequestError("画面には仕様セット・DBモデルを指定できません")
            collection(
                db,
                project_id,
                collection_id,
                "api_set" if kind == "api" else "db_model",
            )
        projection = select(
            Specification.id,
            Specification.kind,
            Specification.title,
            Specification.summary,
            Specification.status,
            Specification.version,
            Specification.updated_at,
            Specification.content["collection_id"].as_string().label("collection_id"),
            Specification.content[
                {"screen": "route", "api": "path", "database": "physical_name"}[kind]
            ]
            .as_string()
            .label("identifier"),
            Specification.content[
                {"screen": "screen_kind", "api": "method", "database": "namespace"}[
                    kind
                ]
            ]
            .as_string()
            .label("subtype"),
            Specification.content["transitions"].label("transitions")
            if kind == "screen"
            else Specification.content["constraints"].label("transitions"),
            Specification.content["relationships"].label("relationships"),
            Specification.content["columns"].label("columns"),
            Specification.content["operation"]["tags"].label("tags"),
        ).where(
            Specification.project_id == project_id,
            Specification.deleted_at.is_(None),
            Specification.kind == kind,
        )
        requested = projection
        if group_id is not None:
            requested = requested.where(
                Specification.id.in_(
                    select(DesignMembership.specification_id).where(
                        DesignMembership.project_id == project_id,
                        DesignMembership.group_id == group_id,
                    )
                )
            )
        if collection_id is not None:
            requested = requested.where(
                Specification.content["collection_id"].as_string() == str(collection_id)
            )
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            requested = requested.where(
                or_(
                    Specification.title.ilike(f"%{escaped}%", escape="\\"),
                    Specification.summary.ilike(f"%{escaped}%", escape="\\"),
                )
            )
        total = (
            db.scalar(
                requested.with_only_columns(func.count(), maintain_column_froms=True)
            )
            or 0
        )
        rows = list(
            db.execute(
                requested.order_by(Specification.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            .mappings()
            .all()
        )
        primary_ids = {row["id"] for row in rows}
        references: list[int] = []
        has_more = False
        if include_references and primary_ids and kind != "api":
            outgoing = {
                item["target_id"]
                for row in rows
                for item in self._diagram_relations(row, kind)
                if item.get("target_id") is not None
            }
            content = Specification.content
            keys = (
                ("transitions",)
                if kind == "screen"
                else ("constraints", "relationships")
            )
            inbound = [
                content[key].contains([{"target_id": identifier}])
                for key in keys
                for identifier in sorted(primary_ids)
            ]
            # 一度に描画する参照を制限し、続きの有無を返す。
            limit = 200
            neighbours = (
                db.execute(
                    projection.where(
                        Specification.id.not_in(primary_ids),
                        or_(Specification.id.in_(outgoing), *inbound),
                    )
                    .order_by(Specification.id)
                    .limit(limit + 1)
                )
                .mappings()
                .all()
            )
            has_more = len(neighbours) > limit
            for neighbour in neighbours[:limit]:
                references.append(neighbour["id"])
                rows.append(neighbour)
        ids = primary_ids | set(references)
        memberships: dict[int, list[UUID]] = defaultdict(list)
        for spec_id, member_group in db.execute(
            select(
                DesignMembership.specification_id,
                DesignMembership.group_id,
            )
            .where(
                DesignMembership.project_id == project_id,
                DesignMembership.specification_id.in_(ids),
            )
            .order_by(DesignMembership.group_id)
        ):
            memberships[spec_id].append(member_group)
        placements = {
            row.specification_id: PlacementRead.model_validate(row)
            for row in db.scalars(
                select(DesignPlacement).where(
                    DesignPlacement.project_id == project_id,
                    DesignPlacement.specification_id.in_(ids),
                    DesignPlacement.view_key == (str(group_id) if group_id else "all"),
                )
            )
        }

        nodes: list[DesignNode] = []
        edges: list[DesignEdge] = []
        for row in rows:
            source = row["id"]
            nodes.append(
                DesignNode.model_validate(
                    {
                        **row,
                        "identifier": row["identifier"] or "",
                        "subtype": row["subtype"] or "",
                        "group_ids": memberships[source],
                        "placement": placements.get(source),
                        "field_labels": [
                            self._column_label(item)
                            for item in (row["columns"] or [])[:8]
                        ],
                        "tags": [
                            tag for tag in (row["tags"] or []) if isinstance(tag, str)
                        ][:20],
                    }
                )
            )
            if kind == "api":
                continue
            for item in self._diagram_relations(row, kind):
                target_id = item.get("target_id")
                if target_id in ids and (
                    source in primary_ids or target_id in primary_ids
                ):
                    logical = "cardinality" in item
                    edges.append(
                        DesignEdge(
                            id=item["id"],
                            source_id=source,
                            target_id=target_id,
                            name=item["name"] or item.get("cardinality", ""),
                            kind="logical"
                            if logical
                            else "transition"
                            if kind == "screen"
                            else "foreign_key",
                            condition=item.get("condition", ""),
                        )
                    )
        return DesignDiagram(
            kind=kind,
            nodes=nodes,
            edges=edges,
            total=total,
            reference_ids=references,
            has_more_references=has_more,
        )

    @staticmethod
    def _diagram_relations(row, kind: ArtifactKind):
        for item in row["transitions"] or []:
            if kind == "screen" or item["kind"] == "foreign_key":
                yield item
        yield from row["relationships"] or []

    @staticmethod
    def _column_label(item: dict) -> str:
        name = item["physical_name"] or item["name"]
        nullability = "" if item["nullable"] else " · NOT NULL"
        return f"{name}  {item['data_type']}{nullability}"
