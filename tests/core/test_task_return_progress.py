"""差し戻し中の実装進捗を完了/未着手に読み替えない。"""

from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.services.task import TaskService

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize("status", ["returned", "resolved"])
def test_unfinished_task_is_in_progress_even_with_previous_100_percent(
    monkeypatch, status
):
    """進捗率は保持する一方、業務状態は再対応中として集計する。"""
    service = TaskService()
    for progress in (70, 100):
        monkeypatch.setattr(
            service,
            "list_requirement_tasks",
            lambda *args, progress=progress: [
                SimpleNamespace(status=status, progress_percent=progress)
            ],
        )
        with Session() as db:
            result = service.get_requirement_progress(db, 1)
        assert result.status == "in_progress"
        assert result.progress_percent == progress
