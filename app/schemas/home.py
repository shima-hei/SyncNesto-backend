"""個人HOMEの作業と参加案件の読み取り契約。"""

from datetime import date
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, BaseModel


def validate_timezone(value: str) -> str:
    """サーバーと表示で同じIANAタイムゾーンを使用する。"""
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("有効なIANAタイムゾーンを指定してください") from error
    return value


HomeTimezone = Annotated[str, AfterValidator(validate_timezone)]


class HomeTaskRead(BaseModel):
    """HOMEに必要なタスク識別情報だけを返す。"""

    id: int
    project_id: int
    project_name: str
    project_code: str
    task_code: str
    title: str
    status: str
    priority: str
    start_date: date | None
    due_date: date | None


class HomeTaskSummary(BaseModel):
    """表示上限とは独立した本人の未完了タスク集計。"""

    total: int
    overdue: int
    due_today: int
    due_soon: int


class HomeTasksRead(BaseModel):
    """今日の基準日、集計、件数を制限した作業一覧。"""

    today: date
    timezone: str
    summary: HomeTaskSummary
    items: list[HomeTaskRead]


class HomeProjectTaskSummary(BaseModel):
    """本人の未完了件数と案件全体のタスク状況。"""

    my_open_count: int
    overdue_count: int
    done_count: int
    total_count: int
    next_due_date: date | None


class HomeProjectRead(BaseModel):
    """参加案件。タスク閲覧権限がなければ集計は返さない。"""

    id: int
    project_code: str
    name: str
    tasks: HomeProjectTaskSummary | None


class HomeProjectsRead(BaseModel):
    """参加案件の総数と件数を制限したサマリー。"""

    today: date
    timezone: str
    total: int
    items: list[HomeProjectRead]
