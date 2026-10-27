"""Taskを全件実体化せずに日程をページ取得する。"""

from sqlalchemy import case, exists, func, or_, select

from app.models.requirement import Requirement, RequirementDocument
from app.models.task import Milestone, RequirementTaskRelation, Task
from app.repositories.requirement_trace import RequirementTraceRepository

TASK_COLUMNS = (
    Task.id,
    Task.task_code,
    Task.title,
    Task.start_date,
    Task.due_date,
    Task.status,
    Task.progress_percent,
)


class RequirementScheduleRepository:
    """有効な要件/関連/Taskを同一案件内で扱う。"""

    def requirements(self, project_id, document_id=None, q=None, status=None):
        """要件追跡と同じ検索対象・削除条件でIDだけを返す。"""
        stmt = (
            select(Requirement.id)
            .join(RequirementDocument)
            .where(*RequirementTraceRepository().scope(project_id))
        )
        if document_id is not None:
            stmt = stmt.where(Requirement.document_id == document_id)
        if q:
            pattern = f"%{q}%"
            stmt = stmt.where(
                or_(
                    Requirement.requirement_code.ilike(pattern),
                    Requirement.title.ilike(pattern),
                    Requirement.description.ilike(pattern),
                )
            )
        if status:
            stmt = stmt.where(Requirement.status == status)
        return stmt

    def related(self, requirements):
        """関連種別が複数あってもTaskの存在判定は一度。"""
        return exists().where(
            RequirementTaskRelation.task_id == Task.id,
            RequirementTaskRelation.requirement_id.in_(requirements),
        )

    def summary(self, db, project_id, requirements, filtered, today):
        """要件ページの外も含めた一意Task数をSQLで集約する。"""
        planned = (
            Task.start_date.is_not(None)
            & Task.due_date.is_not(None)
            & (Task.due_date >= Task.start_date)
        )
        overdue = (Task.due_date < today) & Task.status.not_in(["done", "cancelled"])
        stmt = select(
            func.count().label("total"),
            func.coalesce(func.sum(case((planned, 1), else_=0)), 0).label("planned"),
            func.coalesce(func.sum(case((overdue, 1), else_=0)), 0).label("overdue"),
        ).where(Task.project_id == project_id, Task.deleted_at.is_(None))
        if filtered:
            stmt = stmt.where(self.related(requirements))
        row = dict(db.execute(stmt).mappings().one())
        return {**row, "unplanned": row["total"] - row["planned"]}

    def groups(self, db, project_id, rows):
        """要件ごとのcountと上位10件を一括取得しN+1を避ける。"""
        if not rows:
            return []
        links = (
            select(RequirementTaskRelation.requirement_id, *TASK_COLUMNS)
            .join(Task, Task.id == RequirementTaskRelation.task_id)
            .where(
                RequirementTaskRelation.requirement_id.in_([r["id"] for r in rows]),
                Task.project_id == project_id,
                Task.deleted_at.is_(None),
            )
            .distinct()
            .subquery()
        )
        counts = dict(
            db.execute(
                select(links.c.requirement_id, func.count()).group_by(
                    links.c.requirement_id
                )
            ).all()
        )
        ranked = select(
            links,
            func.row_number()
            .over(
                partition_by=links.c.requirement_id,
                order_by=(links.c.due_date.asc().nulls_last(), links.c.id),
            )
            .label("position"),
        ).subquery()
        tasks = {}
        for item in db.execute(
            select(ranked)
            .where(ranked.c.position <= 10)
            .order_by(ranked.c.requirement_id, ranked.c.position)
        ).mappings():
            value = dict(item)
            rid = value.pop("requirement_id")
            value.pop("position")
            tasks.setdefault(rid, []).append(value)
        return [
            {
                **{k: r[k] for k in ("id", "document_id", "requirement_code", "title")},
                "task_count": counts.get(r["id"], 0),
                "tasks": tasks.get(r["id"], []),
                "has_more": counts.get(r["id"], 0) > 10,
            }
            for r in rows
        ]

    def common(self, db, project_id, page):
        """現在有効な要件との関連がない作業を別にページ取得。"""
        conditions = (
            Task.project_id == project_id,
            Task.deleted_at.is_(None),
            ~self.related(self.requirements(project_id)),
        )
        total = db.scalar(select(func.count(Task.id)).where(*conditions)) or 0
        tasks = (
            db.execute(
                select(*TASK_COLUMNS)
                .where(*conditions)
                .order_by(Task.due_date.asc().nulls_last(), Task.id)
                .offset((page - 1) * 20)
                .limit(20)
            )
            .mappings()
            .all()
        )
        return tasks, total

    def milestones(self, db, project_id, start, end):
        """表示期間内の節目を50件とhas_moreで返す。"""
        return (
            db.execute(
                select(
                    Milestone.id,
                    Milestone.title,
                    Milestone.target_date,
                    Milestone.status,
                )
                .where(
                    Milestone.project_id == project_id,
                    Milestone.target_date.between(start, end),
                    Milestone.deleted_at.is_(None),
                )
                .order_by(Milestone.target_date, Milestone.id)
                .limit(51)
            )
            .mappings()
            .all()
        )
