"""工程のロックと一意Task集合からの工数集計。"""

from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session

from app.models.task import Task
from app.models.task_phase import ProjectPhase, ProjectPhaseEvent
from app.repositories.task_item import TaskRepository
from app.schemas.task_phase import EffortFilters, EffortMetric, EffortTotals


def effort_totals(row) -> EffortTotals:
    """SQL集計の未入力と未確認を保持する。"""
    values = {}
    for metric in ("estimated", "actual"):
        missing, unreviewed = (
            int(row[f"{metric}_missing"] or 0),
            int(row[f"{metric}_unreviewed"] or 0),
        )
        values[metric] = EffortMetric(
            minutes=int(row[f"{metric}_minutes"] or 0),
            missing_count=missing,
            unreviewed_count=unreviewed,
            complete=not (missing or unreviewed),
        )
    return EffortTotals(task_count=int(row["task_count"]), **values)


class TaskPhaseRepository:
    """工程の既存所属を隠さずに取得する。"""

    def events(self, db: Session, project_id: int, phase_id: int, page: int):
        """所属を確認した工程の履歴をページ取得する。"""
        query = select(ProjectPhaseEvent).where(
            ProjectPhaseEvent.project_id == project_id,
            ProjectPhaseEvent.phase_id == phase_id,
        )
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        return list(
            db.scalars(
                query.order_by(ProjectPhaseEvent.id.desc())
                .offset((page - 1) * 20)
                .limit(20)
            )
        ), total

    def get(self, db: Session, project_id: int, phase_id: int, *, lock=False):
        """同じ工程行をアーカイブと割当でロックする。"""
        if lock and db.info.get("demo_id") is not None:
            from app.db.demo_scope import lock_active_demo

            lock_active_demo(db)
        stmt = select(ProjectPhase).where(
            ProjectPhase.id == phase_id, ProjectPhase.project_id == project_id
        )
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return db.scalar(stmt)

    def counts(self, db: Session, project_id: int):
        """工程別の有効Task件数を一括取得する。"""
        return {
            phase_id: count
            for phase_id, count in db.execute(
                select(Task.phase_id, func.count())
                .where(Task.project_id == project_id, Task.deleted_at.is_(None))
                .group_by(Task.phase_id)
            ).all()
        }

    def summary(self, db: Session, project_id: int, filters: EffortFilters):
        """同額の別Taskを落とさず、関連joinの重複だけを除く。"""
        query = TaskRepository().filtered_query(
            db, project_id=project_id, **filters.model_dump()
        )
        source = (
            query.with_entities(
                Task.id,
                Task.phase_scope,
                Task.phase_id,
                Task.status,
                Task.estimated_minutes,
                Task.actual_minutes,
                Task.estimated_basis,
                Task.actual_basis,
            )
            .distinct()
            .subquery()
        )
        aggregates = [func.count().label("task_count")]
        for metric in ("estimated", "actual"):
            basis, minutes = source.c[f"{metric}_basis"], source.c[f"{metric}_minutes"]
            aggregates.extend(
                [
                    func.coalesce(
                        func.sum(case((basis == "own", minutes), else_=0)), 0
                    ).label(f"{metric}_minutes"),
                    func.count()
                    .filter((basis == "own") & minutes.is_(None))
                    .label(f"{metric}_missing"),
                    func.count()
                    .filter(basis == "unreviewed")
                    .label(f"{metric}_unreviewed"),
                ]
            )
        return (
            db.execute(
                select(
                    source.c.phase_scope,
                    source.c.phase_id,
                    source.c.status,
                    *aggregates,
                ).group_by(source.c.phase_scope, source.c.phase_id, source.c.status)
            )
            .mappings()
            .all()
        )

    def presentation(self, db: Session, tasks: list[Task]):
        """取得済みの認可されたTaskだけをrootに、子集計を一括取得する。"""
        if not tasks:
            return {}
        aggregates = []
        for metric in ("estimated", "actual"):
            aggregates.extend(
                [
                    f"coalesce(sum(case when t.{metric}_basis='own' "
                    f"then t.{metric}_minutes else 0 end),0) AS {metric}_minutes",
                    f"count(*) filter(where t.{metric}_basis='own' "
                    f"and t.{metric}_minutes is null) AS {metric}_missing",
                    f"count(*) filter(where t.{metric}_basis='unreviewed') "
                    f"AS {metric}_unreviewed",
                ]
            )
        # 削除された中間親を経由しても有効な子を落とさず、UNIONで循環を止める。
        stmt = text(
            """WITH RECURSIVE tree(root_id, project_id, id) AS (
          SELECT id, project_id, id FROM tasks WHERE id=ANY(:ids)
          UNION SELECT tree.root_id, tree.project_id, child.id FROM tree
          JOIN tasks child ON child.parent_task_id=tree.id
            AND child.project_id=tree.project_id
        ) SELECT tree.root_id, count(*) AS task_count, """
            + ",".join(aggregates)
            + " FROM tree JOIN tasks t ON t.id=tree.id "
            "WHERE t.deleted_at IS NULL GROUP BY tree.root_id"
        )
        totals = {
            row["root_id"]: effort_totals(row)
            for row in (
                db.execute(stmt, {"ids": [task.id for task in tasks]}).mappings()
                if len(tasks) == 1
                else []
            )
        }
        phases = {
            p.id: p
            for p in db.scalars(
                select(ProjectPhase).where(
                    ProjectPhase.id.in_(
                        {t.phase_id for t in tasks if t.phase_id is not None}
                    )
                )
            )
        }
        return {
            task.id: {
                "phase_scope": task.phase_scope,
                "phase_id": task.phase_id,
                "phase_name": phases[task.phase_id].name
                if task.phase_id in phases
                else None,
                "phase_archived": bool(
                    task.phase_id in phases and phases[task.phase_id].archived_at
                ),
                "estimated_basis": task.estimated_basis,
                "actual_basis": task.actual_basis,
                "effort_totals": totals[task.id].model_dump()
                if task.id in totals
                else None,
            }
            for task in tasks
        }
