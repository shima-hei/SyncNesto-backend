"""プロジェクトRepositoryを定義するモジュール。"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.project import Project, ProjectMember
from app.schemas.project import ProjectCreate, ProjectUpdate


class ProjectRepository:
    """Projectテーブルへのデータアクセス処理を提供する。"""

    def create(self, db: Session, project_in: ProjectCreate) -> Project:
        """プロジェクトを作成する。

        Args:
            db: DBセッション。
            project_in: プロジェクト作成リクエストの入力値。

        Returns:
            作成されたプロジェクト。
        """
        project = Project(
            name=project_in.name,
            description=project_in.description,
        )
        db.add(project)
        db.commit()
        db.refresh(project)
        return project

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

    def update(
        self,
        db: Session,
        *,
        project: Project,
        project_in: ProjectUpdate,
    ) -> Project:
        """プロジェクトを更新する。

        Args:
            db: DBセッション。
            project: 更新対象プロジェクト。
            project_in: プロジェクト更新リクエストの入力値。

        Returns:
            更新されたプロジェクト。
        """
        if project_in.name is not None:
            project.name = project_in.name
        if project_in.description is not None:
            project.description = project_in.description

        db.commit()
        db.refresh(project)
        return project

    def soft_delete(self, db: Session, project: Project) -> Project:
        """プロジェクトを論理削除する。

        Args:
            db: DBセッション。
            project: 削除対象プロジェクト。

        Returns:
            論理削除されたプロジェクト。
        """
        project.deleted_at = datetime.now(UTC)
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
        db.commit()
        db.refresh(member)
        return member

    def soft_delete(self, db: Session, member: ProjectMember) -> ProjectMember:
        """プロジェクトメンバーを論理削除する。

        Args:
            db: DBセッション。
            member: 削除対象メンバー。

        Returns:
            論理削除されたプロジェクトメンバー。
        """
        member.deleted_at = datetime.now(UTC)
        db.commit()
        db.refresh(member)
        return member
