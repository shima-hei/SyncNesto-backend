"""プロジェクトRepositoryを定義するモジュール。"""

from datetime import UTC, datetime

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.project import Project, ProjectMember
from app.schemas.project import ProjectCreate, ProjectUpdate


class ProjectRepository:
    """Projectテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        project_in: ProjectCreate,
        actor_id: int | None = None,
    ) -> Project:
        """プロジェクトを作成する。

        Args:
            db: DBセッション。
            project_in: プロジェクト作成リクエストの入力値。
            actor_id: 作成者ユーザーID。

        Returns:
            作成されたプロジェクト。
        """
        project = Project(
            project_code=project_in.project_code,
            name=project_in.name,
            description=project_in.description,
            status=project_in.status,
            start_date=project_in.start_date,
            end_date=project_in.end_date,
            created_by=actor_id,
            updated_by=actor_id,
        )
        db.add(project)
        db.commit()
        db.refresh(project)
        return project

    def get_by_project_code(self, db: Session, project_code: str) -> Project | None:
        """project_codeに一致するプロジェクトを取得する。

        Args:
            db: DBセッション。
            project_code: プロジェクトコード。

        Returns:
            一致するプロジェクト。存在しない場合はNone。
        """
        return (
            db.query(Project)
            .filter(
                Project.project_code == project_code,
                Project.deleted_at.is_(None),
            )
            .first()
        )

    def get_by_id(self, db: Session, project_id: int) -> Project | None:
        """idに一致するプロジェクトを取得する。

        Args:
            db: DBセッション。
            project_id: 検索対象プロジェクトID。

        Returns:
            一致するプロジェクト。存在しない場合はNone。
        """
        return (
            db.query(Project)
            .filter(Project.id == project_id, Project.deleted_at.is_(None))
            .first()
        )

    def list(self, db: Session) -> list[Project]:
        """削除されていないプロジェクト一覧を取得する。

        Args:
            db: DBセッション。

        Returns:
            プロジェクト一覧。
        """
        return (
            db.query(Project)
            .filter(Project.deleted_at.is_(None))
            .order_by(Project.id)
            .all()
        )

    def list_paginated(
        self,
        db: Session,
        *,
        page: int,
        page_size: int,
        q: str | None = None,
        status: str | None = None,
    ) -> tuple[list[Project], int]:
        """削除されていないプロジェクト一覧をページング付きで取得する。

        Args:
            db: DBセッション。
            page: ページ番号。
            page_size: 1ページあたりの件数。
            q: 検索キーワード。
            status: ステータス絞り込み。

        Returns:
            プロジェクト一覧と総件数。
        """
        query = db.query(Project).filter(Project.deleted_at.is_(None))
        query = self._apply_list_filters(query, q=q, status=status)
        total = query.count()
        projects = (
            query.order_by(Project.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return projects, total

    def list_by_user(self, db: Session, user_id: int) -> list[Project]:
        """ユーザーが所属するプロジェクト一覧を取得する。

        Args:
            db: DBセッション。
            user_id: ユーザーID。

        Returns:
            所属プロジェクト一覧。
        """
        return (
            db.query(Project)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .filter(
                Project.deleted_at.is_(None),
                ProjectMember.deleted_at.is_(None),
                ProjectMember.user_id == user_id,
            )
            .order_by(Project.id)
            .all()
        )

    def list_by_user_paginated(
        self,
        db: Session,
        *,
        user_id: int,
        page: int,
        page_size: int,
        q: str | None = None,
        status: str | None = None,
    ) -> tuple[list[Project], int]:
        """ユーザーが所属するプロジェクト一覧をページング付きで取得する。

        Args:
            db: DBセッション。
            user_id: ユーザーID。
            page: ページ番号。
            page_size: 1ページあたりの件数。
            q: 検索キーワード。
            status: ステータス絞り込み。

        Returns:
            所属プロジェクト一覧と総件数。
        """
        query = (
            db.query(Project)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .filter(
                Project.deleted_at.is_(None),
                ProjectMember.deleted_at.is_(None),
                ProjectMember.user_id == user_id,
            )
        )
        query = self._apply_list_filters(query, q=q, status=status)
        total = query.count()
        projects = (
            query.order_by(Project.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return projects, total

    def update(
        self,
        db: Session,
        *,
        project: Project,
        project_in: ProjectUpdate,
        actor_id: int | None = None,
    ) -> Project:
        """プロジェクトを更新する。

        Args:
            db: DBセッション。
            project: 更新対象プロジェクト。
            project_in: プロジェクト更新リクエストの入力値。
            actor_id: 更新者ユーザーID。

        Returns:
            更新されたプロジェクト。
        """
        if project_in.project_code is not None:
            project.project_code = project_in.project_code
        if project_in.name is not None:
            project.name = project_in.name
        if "description" in project_in.model_fields_set:
            project.description = project_in.description
        if project_in.status is not None:
            project.status = project_in.status
        if "start_date" in project_in.model_fields_set:
            project.start_date = project_in.start_date
        if "end_date" in project_in.model_fields_set:
            project.end_date = project_in.end_date
        if actor_id is not None:
            project.updated_by = actor_id
        project.version += 1

        db.commit()
        db.refresh(project)
        return project

    def _apply_list_filters(self, query, *, q: str | None, status: str | None):
        """プロジェクト一覧用の検索条件を適用する。"""
        if q:
            like_pattern = f"%{q}%"
            query = query.filter(
                or_(
                    Project.project_code.ilike(like_pattern),
                    Project.name.ilike(like_pattern),
                    Project.description.ilike(like_pattern),
                )
            )
        if status is not None:
            query = query.filter(Project.status == status)
        return query

    def soft_delete(
        self,
        db: Session,
        project: Project,
        actor_id: int | None = None,
    ) -> Project:
        """プロジェクトを論理削除する。

        Args:
            db: DBセッション。
            project: 削除対象プロジェクト。
            actor_id: 更新者ユーザーID。

        Returns:
            論理削除されたプロジェクト。
        """
        project.deleted_at = datetime.now(UTC)
        if actor_id is not None:
            project.updated_by = actor_id
        db.commit()
        db.refresh(project)
        return project


class ProjectMemberRepository:
    """ProjectMemberテーブルへのデータアクセス処理を提供する。"""

    def create(
        self,
        db: Session,
        *,
        project_id: int,
        user_id: int,
        role_id: int,
    ) -> ProjectMember:
        """プロジェクトメンバーを作成する。

        Args:
            db: DBセッション。
            project_id: プロジェクトID。
            user_id: ユーザーID。
            role_id: ロールID。

        Returns:
            作成されたプロジェクトメンバー。
        """
        member = ProjectMember(
            project_id=project_id,
            user_id=user_id,
            role_id=role_id,
        )
        db.add(member)
        db.commit()
        db.refresh(member)
        return member

    def list_by_project(self, db: Session, project_id: int) -> list[ProjectMember]:
        """プロジェクトメンバー一覧を取得する。

        Args:
            db: DBセッション。
            project_id: プロジェクトID。

        Returns:
            プロジェクトメンバー一覧。
        """
        return (
            db.query(ProjectMember)
            .filter(
                ProjectMember.project_id == project_id,
                ProjectMember.deleted_at.is_(None),
            )
            .order_by(ProjectMember.id)
            .all()
        )

    def get_by_project_user(
        self,
        db: Session,
        *,
        project_id: int,
        user_id: int,
    ) -> ProjectMember | None:
        """プロジェクトIDとユーザーIDでメンバーを取得する。

        Args:
            db: DBセッション。
            project_id: プロジェクトID。
            user_id: ユーザーID。

        Returns:
            一致するメンバー。存在しない場合はNone。
        """
        return (
            db.query(ProjectMember)
            .filter(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user_id,
                ProjectMember.deleted_at.is_(None),
            )
            .first()
        )

    def update_role(
        self,
        db: Session,
        *,
        member: ProjectMember,
        role_id: int,
    ) -> ProjectMember:
        """プロジェクトメンバーのロールを更新する。

        Args:
            db: DBセッション。
            member: 更新対象メンバー。
            role_id: 更新後ロールID。

        Returns:
            更新されたプロジェクトメンバー。
        """
        member.role_id = role_id
        member.version += 1
        db.commit()
        db.refresh(member)
        return member

    def delete(self, db: Session, member: ProjectMember) -> None:
        """プロジェクトメンバーを物理削除する。

        Args:
            db: DBセッション。
            member: 削除対象メンバー。
        """
        db.delete(member)
        db.commit()
