from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScenarioRunRequest(ApiModel):
    seed: int = Field(default=20260923, ge=0)


class ApprovalDecision(ApiModel):
    selected_option_id: str | None = None


class RequestData(ApiModel):
    requested_fields: list[str] = Field(min_length=1)
    message: str
