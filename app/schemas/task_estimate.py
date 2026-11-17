"""タスク単位の見積方法と分単位の入力。"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

EstimateMethod = Literal["simple", "pert", "triangular"]
Minutes = Annotated[int, Field(strict=True, ge=0, le=2147483647)]


class TaskEstimateUpdate(BaseModel):
    version: int = Field(ge=1)
    method: EstimateMethod
    minutes: Minutes | None = None
    optimistic_minutes: Minutes | None = None
    likely_minutes: Minutes | None = None
    pessimistic_minutes: Minutes | None = None
    assumptions: str | None = Field(default=None, max_length=2000)
    change_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_method_inputs(self):
        points = (
            self.optimistic_minutes,
            self.likely_minutes,
            self.pessimistic_minutes,
        )
        if self.method == "simple":
            if self.minutes is None or any(point is not None for point in points):
                raise ValueError("単純見積には見積工数を入力してください")
        else:
            optimistic, likely, pessimistic = points
            if self.minutes is not None or (
                optimistic is None
                or likely is None
                or pessimistic is None
                or not (optimistic <= likely <= pessimistic)
            ):
                raise ValueError("3点見積は楽観、最頻、悲観の順に入力してください")
        return self

    def calculated_minutes(self) -> int:
        if self.method == "simple":
            assert self.minutes is not None
            return self.minutes
        optimistic = self.optimistic_minutes
        likely = self.likely_minutes
        pessimistic = self.pessimistic_minutes
        assert optimistic is not None and likely is not None and pessimistic is not None
        if self.method == "pert":
            return (optimistic + 4 * likely + pessimistic + 3) // 6
        return (optimistic + likely + pessimistic + 1) // 3


class TaskEstimateRevisionRead(BaseModel, from_attributes=True):
    task_version: int
    method: EstimateMethod | None = None
    minutes: int | None = None
    optimistic_minutes: int | None = None
    likely_minutes: int | None = None
    pessimistic_minutes: int | None = None
    assumptions: str | None = None
    change_reason: str | None = None
    actor_id: int | None = None
    created_at: datetime | None = None


class TaskEstimateRead(BaseModel):
    version: int
    current: TaskEstimateRevisionRead
    history: list[TaskEstimateRevisionRead]
    history_total: int
    page: int


class TaskEstimateMethodRead(BaseModel):
    task_id: int
    method: EstimateMethod | None


class TaskEstimateBaselineConfirm(BaseModel):
    estimate_version: int = Field(gt=0)
    expected_baseline_id: int | None = Field(gt=0)
    reason: str | None = Field(default=None, max_length=500)


class TaskEstimateBaselineRead(BaseModel):
    id: int
    estimate: TaskEstimateRevisionRead
    confirmed_by: int | None
    confirmed_at: datetime
    reason: str | None


class TaskEstimateBaselines(BaseModel):
    current: TaskEstimateBaselineRead | None
    items: list[TaskEstimateBaselineRead]
    total: int
    page: int
