"""画面一覧を関連付きで取得し、複数行を一つのトランザクションで保存する。"""

from collections import defaultdict

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.models.design_workspace import (
    DesignCollection,
    DesignMembership,
    ScreenListEditReceipt,
)
from app.models.project import Project, ProjectMember
from app.models.requirement import Requirement, RequirementDocument
from app.models.specification import Specification, SpecificationLink
from app.models.tenant import TenantMember
from app.models.user import User
from app.schemas.screen_list import (
    ScreenListBatch,
    ScreenListGroup,
    ScreenListMember,
    ScreenListQuery,
    ScreenListRead,
    ScreenListReference,
    ScreenListRow,
    ScreenListSaved,
)
from app.schemas.specification import (
    ScreenSpecification,
    SpecificationCreate,
    SpecificationUpdate,
)
from app.services.artifact_release_snapshot import digest
from app.services.design_workspace import (
    DesignWorkspaceService,
    collection,
    lock_project,
    replace_memberships,
)
from app.services.specification import SpecificationService


class ScreenListService:
    def __init__(self):
        self.specifications = SpecificationService()
        self.workspace = DesignWorkspaceService()

    @staticmethod
    def members(db: Session, project_id: int) -> list[ScreenListMember]:
        rows = db.execute(
            select(
                User.id,
                func.coalesce(TenantMember.display_name, User.name).label("name"),
            )
            .join(ProjectMember, ProjectMember.user_id == User.id)
            .join(Project, Project.id == ProjectMember.project_id)
            .join(
                TenantMember,
                (TenantMember.user_id == User.id)
                & (TenantMember.tenant_id == Project.tenant_id),
            )
            .where(
                Project.id == project_id,
                ProjectMember.deleted_at.is_(None),
                TenantMember.status == "active",
                User.user_type == "internal",
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
            .order_by(TenantMember.display_name, User.name, User.id)
        )
        return [ScreenListMember(id=row.id, name=row.name) for row in rows]

    @staticmethod
    def groups(db: Session, project_id: int, ids: list[int]):
        grouped: dict[int, list[ScreenListGroup]] = defaultdict(list)
        for row in db.execute(
            select(
                DesignMembership.specification_id,
                DesignCollection.id,
                DesignCollection.name,
            )
            .join(
                DesignCollection,
                (DesignCollection.id == DesignMembership.group_id)
                & (DesignCollection.project_id == DesignMembership.project_id),
            )
            .where(
                DesignMembership.project_id == project_id,
                DesignMembership.specification_id.in_(ids),
            )
            .order_by(DesignCollection.name, DesignCollection.id)
        ):
            grouped[row.specification_id].append(
                ScreenListGroup(id=row.id, name=row.name)
            )
        return grouped

    @staticmethod
    def requirements(db: Session, project_id: int, ids: list[int]):
        references: dict[int, list[ScreenListReference]] = defaultdict(list)
        for row in db.execute(
            select(
                SpecificationLink.specification_id,
                Requirement.id,
                Requirement.title,
                Requirement.requirement_code,
                Requirement.document_id,
            )
            .join(Requirement, Requirement.id == SpecificationLink.requirement_id)
            .join(
                RequirementDocument, RequirementDocument.id == Requirement.document_id
            )
            .where(
                SpecificationLink.specification_id.in_(ids),
                RequirementDocument.project_id == project_id,
                RequirementDocument.deleted_at.is_(None),
                Requirement.deleted_at.is_(None),
            )
            .order_by(Requirement.requirement_code, Requirement.id)
        ):
            references[row.specification_id].append(
                ScreenListReference(
                    id=row.id,
                    title=row.title,
                    code=row.requirement_code,
                    container_id=row.document_id,
                )
            )
        return references

    @staticmethod
    def api_references(db: Session, project_id: int, rows):
        """明示リンクと画面イベント・項目のAPI参照を重複なしで返す。"""
        ids = [row.id for row in rows]
        related: dict[int, set[int]] = defaultdict(set)
        source, target = (
            SpecificationLink.specification_id,
            SpecificationLink.related_specification_id,
        )
        for link in db.execute(
            select(source, target).where(
                target.is_not(None), or_(source.in_(ids), target.in_(ids))
            )
        ):
            if link.specification_id in ids:
                related[link.specification_id].add(link.related_specification_id)
            if link.related_specification_id in ids:
                related[link.related_specification_id].add(link.specification_id)
        for row in rows:
            related[row.id].update(item["target_id"] for item in row.mappings or [])
            related[row.id].update(
                step["specification_id"]
                for action in row.actions or []
                for step in action.get("steps", [])
                if step.get("kind") == "api"
                and step.get("specification_id") is not None
            )
        referenced = set().union(*related.values()) if related else set()
        if not referenced:
            return {}
        summaries = {
            row.id: ScreenListReference(
                id=row.id, title=row.title, code=f"API-{row.id:04d}"
            )
            for row in db.execute(
                select(Specification.id, Specification.title).where(
                    Specification.project_id == project_id,
                    Specification.deleted_at.is_(None),
                    Specification.kind == "api",
                    Specification.id.in_(referenced),
                )
            )
        }
        return {
            screen_id: [summaries[key] for key in sorted(keys) if key in summaries]
            for screen_id, keys in related.items()
        }

    def listing(
        self, db: Session, project_id: int, filters: ScreenListQuery, actor: User
    ) -> ScreenListRead:
        spec = Specification
        route = func.coalesce(spec.content["route"].as_string(), "")
        subtype = func.coalesce(spec.content["screen_kind"].as_string(), "other")
        name = func.coalesce(TenantMember.display_name, User.name)
        query = (
            select(
                spec.id,
                spec.title,
                spec.status,
                spec.version,
                spec.updated_at,
                route.label("route"),
                subtype.label("screen_kind"),
                spec.assignee_id,
                name.label("assignee_name"),
                spec.content["actions"].label("actions"),
                spec.content["mappings"].label("mappings"),
            )
            .join(Project, Project.id == spec.project_id)
            .outerjoin(User, User.id == spec.assignee_id)
            .outerjoin(
                TenantMember,
                (TenantMember.user_id == spec.assignee_id)
                & (TenantMember.tenant_id == Project.tenant_id),
            )
            .where(
                spec.project_id == project_id,
                spec.kind == "screen",
                spec.deleted_at.is_(None),
            )
        )
        if filters.group_id is not None:
            collection(db, project_id, filters.group_id, "group")
            query = query.where(
                exists().where(
                    DesignMembership.project_id == project_id,
                    DesignMembership.specification_id == spec.id,
                    DesignMembership.group_id == filters.group_id,
                )
            )
        if filters.q:
            query = query.where(
                or_(
                    spec.title.icontains(filters.q, autoescape=True),
                    route.icontains(filters.q, autoescape=True),
                )
            )
        if filters.status is not None:
            query = query.where(spec.status == filters.status)
        if filters.screen_kind is not None:
            query = query.where(subtype == filters.screen_kind)
        if filters.assignee_id is not None:
            query = query.where(spec.assignee_id == (filters.assignee_id or None))
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        sort = {
            "id": spec.id,
            "title": func.lower(spec.title),
            "status": spec.status,
            "route": route,
            "screen_kind": subtype,
            "assignee": name,
            "updated_at": spec.updated_at,
        }[filters.sort]
        ordered = sort.asc() if filters.direction == "asc" else sort.desc()
        rows = db.execute(
            query.order_by(ordered.nulls_last(), spec.id)
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        ).all()
        if not rows:
            return ScreenListRead(
                items=[], total=total, page=filters.page, page_size=filters.page_size
            )
        ids = [row.id for row in rows]
        groups = self.groups(db, project_id, ids)
        apis = self.api_references(db, project_id, rows)
        requirements = (
            self.requirements(db, project_id, ids)
            if self.specifications.can_read_target(db, actor, project_id, "requirement")
            else {}
        )
        return ScreenListRead(
            items=[
                ScreenListRow.model_validate(
                    dict(
                        id=row.id,
                        title=row.title,
                        status=row.status,
                        version=row.version,
                        updated_at=row.updated_at,
                        route=row.route,
                        screen_kind=row.screen_kind,
                        groups=groups.get(row.id, []),
                        related_apis=apis.get(row.id, []),
                        related_requirements=requirements.get(row.id, []),
                        assignee={"id": row.assignee_id, "name": row.assignee_name}
                        if row.assignee_id is not None and row.assignee_name is not None
                        else None,
                    )
                )
                for row in rows
            ],
            total=total,
            page=filters.page,
            page_size=filters.page_size,
        )

    def save(self, db: Session, project_id: int, actor: User, data: ScreenListBatch):
        signature = digest(data.model_dump(mode="json", exclude_unset=True))
        lock_project(db, project_id)
        receipt = db.get(ScreenListEditReceipt, (project_id, actor.id, data.request_id))
        if receipt is not None:
            if receipt.request_digest != signature:
                raise ConflictError()
            return [ScreenListSaved.model_validate(row) for row in receipt.result]
        if any(
            edit.id is None for edit in data.edits
        ) and not self.specifications.authorization.has_project_permission(
            db, user=actor, project_id=project_id, permission_code="document:create"
        ):
            raise ForbiddenError()
        owners = {
            edit.assignee_id for edit in data.edits if edit.assignee_id is not None
        }
        if owners and not owners <= {
            member.id for member in self.members(db, project_id)
        }:
            raise BadRequestError(
                "担当者は案件に所属する有効なメンバーから指定してください"
            )
        groups = {group for edit in data.edits for group in edit.group_ids or []}
        if groups and not groups <= {
            row.id for row in self.workspace.collections(db, project_id, "group")
        }:
            raise BadRequestError("機能グループは同じ案件内から選択してください")
        locked: dict[int, Specification] = {}
        for edit in sorted(data.edits, key=lambda item: item.id or 0):
            if edit.id is None or edit.version is None:
                continue
            row = self.specifications.get(db, project_id, edit.id, lock=True)
            if row.kind != "screen":
                raise NotFoundError()
            self.specifications.check_version(row, edit.version)
            locked[edit.id] = row
        changed = []
        edited: list[ScreenListSaved] = []
        for edit in data.edits:
            row = locked.get(edit.id) if edit.id is not None else None
            content = dict(row.content) if row is not None else {"type": "screen"}
            if edit.route is not None:
                content["route"] = edit.route
            if edit.screen_kind is not None:
                content["screen_kind"] = edit.screen_kind
            screen = ScreenSpecification.model_validate(content)
            if row is None:
                row = self.specifications.stage_create(
                    db,
                    project_id,
                    SpecificationCreate(title=edit.title or "", content=screen),
                    actor,
                    assignee_id=edit.assignee_id,
                )
                action = "created"
            else:
                if "assignee_id" in edit.model_fields_set:
                    row.assignee_id = edit.assignee_id
                self.specifications.stage_update(
                    db,
                    row,
                    SpecificationUpdate(
                        title=edit.title or row.title,
                        summary=row.summary,
                        content=screen,
                        version=row.version,
                    ),
                    actor,
                )
                action = "updated"
            if edit.group_ids is not None:
                replace_memberships(db, project_id, row.id, set(edit.group_ids))
            edited.append(
                ScreenListSaved(id=row.id, version=row.version, title=row.title)
            )
            changed.append((row, action))
        db.add(
            ScreenListEditReceipt(
                project_id=project_id,
                actor_id=actor.id,
                request_id=data.request_id,
                request_digest=signature,
                result=[row.model_dump(mode="json") for row in edited],
            )
        )
        db.commit()
        for row, action in changed:
            self.specifications.audit(db, row, actor.id, action)
        return edited
