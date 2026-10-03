"""HOMEの所属・権限による絞り込みとDB集計。"""

from datetime import date, timedelta

from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session

from app.models.project import Project, ProjectMember
from app.models.rbac import Permission, Role, RolePermission
from app.models.task import Task

FINISHED_STATUSES = ("done", "cancelled")
NEAR_DUE_DAYS = 7


class HomeRepository:
    """業務データを変更せず、必要な行と集計だけを取得する。"""

    def member_projects(
        self, user_id: int, system_permissions: dict[str, bool], *, tasks: bool
    ) -> Select:
        """system権限があってもHOMEは有効な参加案件に限定する。"""
        scope = (
            select(Project)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(
                Project.deleted_at.is_(None),
                ProjectMember.deleted_at.is_(None),
                ProjectMember.user_id == user_id,
            )
        )
        for code in ("project:read", "task:read") if tasks else ("project:read",):
            if not system_permissions[code]:
                allowed_roles = (
                    select(RolePermission.role_id)
                    .join(Role, Role.id == RolePermission.role_id)
                    .join(Permission, Permission.id == RolePermission.permission_id)
                    .where(Role.scope == "project", Permission.code == code)
                )
                scope = scope.where(ProjectMember.role_id.in_(allowed_roles))
        return scope

    def work(
        self,
        db: Session,
        user_id: int,
        permissions: dict[str, bool],
        today: date,
        limit: int,
    ) -> tuple[dict, list[dict]]:
        """本人の未完了タスクを期限・優先度順で上限付き取得する。"""
        project_ids = self.member_projects(
            user_id, permissions, tasks=True
        ).with_only_columns(Project.id)
        scope = (
            Task.project_id.in_(project_ids),
            Task.deleted_at.is_(None),
            Task.assignee_id == user_id,
            Task.status.not_in(FINISHED_STATUSES),
        )
        near_date = today + timedelta(days=NEAR_DUE_DAYS)
        summary = (
            db.execute(
                select(
                    func.count(Task.id).label("total"),
                    func.count(Task.id).filter(Task.due_date < today).label("overdue"),
                    func.count(Task.id)
                    .filter(Task.due_date == today)
                    .label("due_today"),
                    func.count(Task.id)
                    .filter(Task.due_date > today, Task.due_date <= near_date)
                    .label("due_soon"),
                ).where(*scope)
            )
            .mappings()
            .one()
        )
        bucket = case(
            (Task.due_date < today, 0),
            (Task.due_date == today, 1),
            (Task.due_date <= near_date, 2),
            else_=3,
        )
        priority = case(
            (Task.priority == "critical", 0),
            (Task.priority == "high", 1),
            (Task.priority == "medium", 2),
            else_=3,
        )
        items = (
            db.execute(
                select(
                    Task.id,
                    Task.project_id,
                    Project.name.label("project_name"),
                    Project.project_code,
                    Task.task_code,
                    Task.title,
                    Task.status,
                    Task.priority,
                    Task.start_date,
                    Task.due_date,
                )
                .join(Project, Project.id == Task.project_id)
                .where(*scope)
                .order_by(bucket, Task.due_date.asc().nulls_last(), priority, Task.id)
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return dict(summary), [dict(item) for item in items]

    def projects(
        self,
        db: Session,
        user_id: int,
        permissions: dict[str, bool],
        today: date,
        limit: int,
    ) -> tuple[int, list[dict]]:
        """先に案件を上限で絞り、その案件のタスクを一括集計する。"""
        scope = self.member_projects(user_id, permissions, tasks=False)
        total = db.scalar(select(func.count()).select_from(scope.subquery())) or 0
        task_projects = self.member_projects(
            user_id, permissions, tasks=True
        ).with_only_columns(Project.id)
        projects = (
            db.execute(
                scope.with_only_columns(
                    Project.id,
                    Project.project_code,
                    Project.name,
                    Project.id.in_(task_projects).label("can_read_tasks"),
                )
                .order_by(Project.id)
                .limit(limit)
            )
            .mappings()
            .all()
        )
        ids = [item["id"] for item in projects if item["can_read_tasks"]]
        stats = {}
        if ids:
            open_task = Task.status.not_in(FINISHED_STATUSES)
            rows = (
                db.execute(
                    select(
                        Task.project_id,
                        func.count(Task.id)
                        .filter(open_task, Task.assignee_id == user_id)
                        .label("my_open_count"),
                        func.count(Task.id)
                        .filter(open_task, Task.due_date < today)
                        .label("overdue_count"),
                        func.count(Task.id)
                        .filter(Task.status == "done")
                        .label("done_count"),
                        func.count(Task.id).label("total_count"),
                        func.min(Task.due_date)
                        .filter(open_task, Task.due_date >= today)
                        .label("next_due_date"),
                    )
                    .where(Task.project_id.in_(ids), Task.deleted_at.is_(None))
                    .group_by(Task.project_id)
                )
                .mappings()
                .all()
            )
            stats = {row["project_id"]: dict(row) for row in rows}
        empty = dict(
            my_open_count=0,
            overdue_count=0,
            done_count=0,
            total_count=0,
            next_due_date=None,
        )
        return total, [
            {
                "id": item["id"],
                "project_code": item["project_code"],
                "name": item["name"],
                "tasks": stats.get(item["id"], empty)
                if item["can_read_tasks"]
                else None,
            }
            for item in projects
        ]
