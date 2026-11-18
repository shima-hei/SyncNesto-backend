"""タスク単位の見積方法と分単位の入力。"""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

EstimateMethod = Literal["simple", "pert", "triangular", "parametric", "analogous"]
Minutes = Annotated[int, Field(strict=True, ge=0, le=2147483647)]


EstimateDecimal = Annotated[
    Decimal, Field(ge=0, lt=100000000, max_digits=12, decimal_places=4)
]
ReferenceWork = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class TaskEstimateInputs(BaseModel):
    quantity: EstimateDecimal | None = None
    unit_hours: EstimateDecimal | None = None
    reference_work: ReferenceWork | None = None
    reference_minutes: Minutes | None = None
    factor: EstimateDecimal | None = None


class TaskEstimateUpdate(TaskEstimateInputs):
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
        required = {
            "simple": {"minutes"},
            "pert": {"optimistic_minutes", "likely_minutes", "pessimistic_minutes"},
            "triangular": {
                "optimistic_minutes",
                "likely_minutes",
                "pessimistic_minutes",
            },
            "parametric": {"quantity", "unit_hours"},
            "analogous": {"reference_work", "reference_minutes", "factor"},
        }
        supplied = {
            key
            for keys in required.values()
            for key in keys
            if getattr(self, key) is not None
        }
        if supplied != required[self.method]:
            raise ValueError("選択した見積手法に対応する入力を指定してください")
        if self.method in ("pert", "triangular"):
            assert (
                self.optimistic_minutes is not None
                and self.likely_minutes is not None
                and self.pessimistic_minutes is not None
            )
            if (
                not self.optimistic_minutes
                <= self.likely_minutes
                <= self.pessimistic_minutes
            ):
                raise ValueError("3点見積は楽観、最頻、悲観の順に入力してください")
        if self.calculated_minutes() > 2147483647:
            raise ValueError("見積結果が分単位の上限を超えています")
        return self

    def calculated_minutes(self) -> int:
        if self.method == "simple":
            assert self.minutes is not None
            return self.minutes
        if self.method == "parametric":
            assert self.quantity is not None and self.unit_hours is not None
            result = self.quantity * self.unit_hours * 60
            return int(result.to_integral_value(rounding=ROUND_HALF_UP))
        if self.method == "analogous":
            assert self.reference_minutes is not None and self.factor is not None
            result = self.factor * self.reference_minutes
            return int(result.to_integral_value(rounding=ROUND_HALF_UP))
        optimistic = self.optimistic_minutes
        likely = self.likely_minutes
        pessimistic = self.pessimistic_minutes
        assert optimistic is not None and likely is not None and pessimistic is not None
        if self.method == "pert":
            return (optimistic + 4 * likely + pessimistic + 3) // 6
        return (optimistic + likely + pessimistic + 1) // 3


class TaskEstimateRevisionRead(TaskEstimateInputs, from_attributes=True):
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
    planned_minutes: int | None = None
    remaining_minutes: int | None = None
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


class ProjectEstimateStandard(BaseModel):
    method: EstimateMethod
    version: int = Field(ge=1)


class EstimateWorksheetRow(BaseModel):
    task_id: int
    version: int
    current: TaskEstimateRevisionRead


class EstimateWorksheetChange(TaskEstimateUpdate):
    task_id: int = Field(gt=0)


class EstimateWorksheetSave(BaseModel):
    rows: list[EstimateWorksheetChange] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def distinct_tasks(self):
        if len({row.task_id for row in self.rows}) != len(self.rows):
            raise ValueError("同一のタスクが複数含まれています")
        return self
