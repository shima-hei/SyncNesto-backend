"""タスク管理に関するSQLAlchemyモデルを定義するモジュール。"""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.comments import db_comment


class Task(Base):
    """プロジェクト内のタスクを管理するモデル。"""

    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("project_id", "task_code", name="uq_tasks_project_task_code"),
        {
            "comment": db_comment(
                "タスク",
                "プロジェクト内の実作業タスクを管理するテーブル",
            ),
        },
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("タスクID", "タスクを一意に識別するID"),
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"),
        index=True,
        comment=db_comment("プロジェクトID", "タスクが属するプロジェクトID"),
    )
    parent_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id"),
        nullable=True,
        index=True,
        comment=db_comment("親タスクID", "親タスクのID"),
    )
    task_code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment=db_comment("タスクコード", "プロジェクト内で一意なタスク識別子"),
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment=db_comment("タイトル", "タスクのタイトル"),
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=db_comment("説明", "タスクの説明"),
    )
    task_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="other",
        comment=db_comment("タスク種別", "frontend/backend/testなどのタスク種別"),
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="backlog",
        index=True,
        comment=db_comment("ステータス", "タスクの状態"),
    )
    priority: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="medium",
        comment=db_comment("優先度", "critical/high/medium/lowの優先度"),
    )
    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        index=True,
        comment=db_comment("担当者ID", "タスクの担当ユーザーID"),
    )
    reporter_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("報告者ID", "タスクの起票ユーザーID"),
    )
    start_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment=db_comment("開始予定日", "タスクの開始予定日"),
    )
    due_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        index=True,
        comment=db_comment("終了予定日", "タスクの終了予定日"),
    )
    actual_start_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment=db_comment("実績開始日", "タスクの実績開始日"),
    )
    actual_end_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment=db_comment("実績終了日", "タスクの実績終了日"),
    )
    progress_percent: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment=db_comment("進捗率", "0から100までのタスク進捗率"),
    )
    estimated_minutes: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment=db_comment("見積分数", "タスクの見積工数"),
    )
    actual_minutes: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment=db_comment("実績分数", "タスクの実績工数"),
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment=db_comment("表示順", "ボードや一覧での表示順"),
    )
    tags: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
        comment=db_comment("タグ", "タスクに付与されたタグ一覧"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment=db_comment("バージョン", "楽観的排他制御に使用するバージョン番号"),
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("作成者ID", "このタスクを作成したユーザーID"),
    )
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("更新者ID", "このタスクを最後に更新したユーザーID"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment=db_comment("作成日時", "レコードが作成された日時"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment=db_comment("更新日時", "レコードが最後に更新された日時"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=db_comment("削除日時", "論理削除された日時"),
    )


class TaskComment(Base):
    """タスクコメントを管理するモデル。"""

    __tablename__ = "task_comments"
    __table_args__ = (
        {
            "comment": db_comment(
                "タスクコメント",
                "タスクに対するコメントと解決状態を管理するテーブル",
            ),
        },
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("タスクコメントID", "コメントを一意に識別するID"),
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id"),
        index=True,
        comment=db_comment("タスクID", "コメント対象のタスクID"),
    )
    parent_comment_id: Mapped[int | None] = mapped_column(
        ForeignKey("task_comments.id"),
        nullable=True,
        index=True,
        comment=db_comment("親コメントID", "返信元コメントのID"),
    )
    body: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=db_comment("本文", "コメント本文"),
    )
    is_resolved: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment=db_comment("解決済みフラグ", "コメントが解決済みかどうか"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment=db_comment("バージョン", "楽観的排他制御に使用するバージョン番号"),
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("作成者ID", "コメントを作成したユーザーID"),
    )
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("更新者ID", "コメントを最後に更新したユーザーID"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment=db_comment("作成日時", "レコードが作成された日時"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment=db_comment("更新日時", "レコードが最後に更新された日時"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=db_comment("削除日時", "論理削除された日時"),
    )


class RequirementTaskRelation(Base):
    """要件とタスクの関連を管理するモデル。"""

    __tablename__ = "requirement_task_relations"
    __table_args__ = (
        UniqueConstraint(
            "requirement_id",
            "task_id",
            "relation_type",
            name="uq_requirement_task_relations",
        ),
        {
            "comment": db_comment(
                "要件タスク関連",
                "要件とタスクの関連を管理するテーブル",
            ),
        },
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("要件タスク関連ID", "関連を一意に識別するID"),
    )
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("requirements.id"),
        index=True,
        comment=db_comment("要件ID", "関連元の要件ID"),
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id"),
        index=True,
        comment=db_comment("タスクID", "関連先のタスクID"),
    )
    relation_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="implements",
        comment=db_comment("関連種別", "implements/testsなどの関連種別"),
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("作成者ID", "関連を作成したユーザーID"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment=db_comment("作成日時", "レコードが作成された日時"),
    )


class TaskDependency(Base):
    """タスク間の依存関係を管理するモデル。"""

    __tablename__ = "task_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "predecessor_task_id",
            "successor_task_id",
            "dependency_type",
            name="uq_task_dependencies",
        ),
        {
            "comment": db_comment(
                "タスク依存関係",
                "タスク間の依存関係を管理するテーブル",
            ),
        },
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("タスク依存関係ID", "依存関係を一意に識別するID"),
    )
    predecessor_task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id"),
        index=True,
        comment=db_comment("先行タスクID", "先に完了すべきタスクID"),
    )
    successor_task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id"),
        index=True,
        comment=db_comment("後続タスクID", "依存する後続タスクID"),
    )
    dependency_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="finish_to_start",
        comment=db_comment("依存種別", "finish_to_startなどの依存種別"),
    )
    lag_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment=db_comment("ラグ日数", "依存関係に設定する遅延日数"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment=db_comment("バージョン", "楽観的排他制御に使用するバージョン番号"),
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("作成者ID", "依存関係を作成したユーザーID"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment=db_comment("作成日時", "レコードが作成された日時"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment=db_comment("更新日時", "レコードが最後に更新された日時"),
    )


class Milestone(Base):
    """プロジェクトのマイルストーンを管理するモデル。"""

    __tablename__ = "milestones"
    __table_args__ = {
        "comment": db_comment(
            "マイルストーン",
            "ガントチャート上の節目を管理するテーブル",
        ),
    }

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("マイルストーンID", "マイルストーンを一意に識別するID"),
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"),
        index=True,
        comment=db_comment("プロジェクトID", "マイルストーンが属するプロジェクトID"),
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment=db_comment("タイトル", "マイルストーンのタイトル"),
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=db_comment("説明", "マイルストーンの説明"),
    )
    target_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment=db_comment("目標日", "マイルストーンの目標日"),
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="planned",
        comment=db_comment("ステータス", "マイルストーンの状態"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment=db_comment("バージョン", "楽観的排他制御に使用するバージョン番号"),
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("作成者ID", "マイルストーンを作成したユーザーID"),
    )
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("更新者ID", "マイルストーンを最後に更新したユーザーID"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment=db_comment("作成日時", "レコードが作成された日時"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment=db_comment("更新日時", "レコードが最後に更新された日時"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=db_comment("削除日時", "論理削除された日時"),
    )


class Board(Base):
    """プロジェクト単位のタスクボードを管理するモデル。"""

    __tablename__ = "boards"
    __table_args__ = {
        "comment": db_comment(
            "タスクボード",
            "プロジェクト単位のカンバンボード設定を管理するテーブル",
        ),
    }

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("ボードID", "ボードを一意に識別するID"),
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"),
        index=True,
        comment=db_comment("プロジェクトID", "ボードが属するプロジェクトID"),
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment=db_comment("ボード名", "ボードの表示名"),
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=db_comment("説明", "ボードの説明"),
    )
    board_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="kanban",
        comment=db_comment("ボード種別", "kanban/sprint/customの種別"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment=db_comment("バージョン", "楽観的排他制御に使用するバージョン番号"),
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("作成者ID", "ボードを作成したユーザーID"),
    )
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        comment=db_comment("更新者ID", "ボードを最後に更新したユーザーID"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment=db_comment("作成日時", "レコードが作成された日時"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment=db_comment("更新日時", "レコードが最後に更新された日時"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=db_comment("削除日時", "論理削除された日時"),
    )


class BoardColumn(Base):
    """タスクボードの列を管理するモデル。"""

    __tablename__ = "board_columns"
    __table_args__ = (
        UniqueConstraint("board_id", "status_key", name="uq_board_columns_status"),
        {
            "comment": db_comment(
                "ボード列",
                "タスクボードの列設定を管理するテーブル",
            ),
        },
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("ボード列ID", "ボード列を一意に識別するID"),
    )
    board_id: Mapped[int] = mapped_column(
        ForeignKey("boards.id"),
        index=True,
        comment=db_comment("ボードID", "所属するボードID"),
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment=db_comment("列名", "ボード列の表示名"),
    )
    status_key: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment=db_comment("ステータスキー", "列に対応するタスクステータス"),
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment=db_comment("表示順", "ボード内の列表示順"),
    )
    wip_limit: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment=db_comment("WIP上限", "同時進行タスク数の上限"),
    )
    is_done_column: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment=db_comment("完了列フラグ", "完了扱いの列かどうか"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment=db_comment("バージョン", "楽観的排他制御に使用するバージョン番号"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment=db_comment("作成日時", "レコードが作成された日時"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        comment=db_comment("更新日時", "レコードが最後に更新された日時"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=db_comment("削除日時", "論理削除された日時"),
    )


class TaskChangeLog(Base):
    """タスク機能の変更履歴を管理するモデル。"""

    __tablename__ = "task_change_logs"
    __table_args__ = {
        "comment": db_comment(
            "タスク変更履歴",
            "タスク機能に関する変更履歴を管理するテーブル",
        ),
    }

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
        comment=db_comment("タスク変更履歴ID", "変更履歴を一意に識別するID"),
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"),
        index=True,
        comment=db_comment("プロジェクトID", "変更が発生したプロジェクトID"),
    )
    target_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment=db_comment("対象種別", "task/dependency/boardなどの対象種別"),
    )
    target_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
        comment=db_comment("対象ID", "変更対象リソースID"),
    )
    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment=db_comment("操作種別", "created/updated/deletedなどの操作種別"),
    )
    field_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment=db_comment("変更項目名", "変更された項目名"),
    )
    old_value: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment=db_comment("変更前値", "変更前の値"),
    )
    new_value: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment=db_comment("変更後値", "変更後の値"),
    )
    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=db_comment("理由", "変更理由"),
    )
    changed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        index=True,
        comment=db_comment("変更者ID", "変更を行ったユーザーID"),
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
        comment=db_comment("変更日時", "変更が行われた日時"),
    )
