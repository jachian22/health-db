"""Planner-lite request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EntityName = Literal["glucose", "runs", "sleep", "weight", "meals"]
IntentName = Literal[
    "render_glucose_around_runs",
    "render_meal_response",
    "daily_overview",
    "weekly_overview",
    "sleep_trend",
    "weight_trend",
    "fasting_window",
    "build_chart",
    "custom",
]
GoalName = Literal["build_chart", "compare", "summarize", "explore"]


class PlanRetrieveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str = Field(description="What the agent wants to accomplish")
    horizon_days: int = Field(default=30, ge=1, le=365)
    entities: list[str] = Field(default_factory=list)
    goal: str = Field(default="build_chart")
    user_id: str | None = None
    anchor_event: str | None = Field(
        default=None,
        description="Optional anchor such as 'last_run' or 'last_meal_completed'",
    )


class RecommendedEndpoint(BaseModel):
    method: str = "QUERY"
    path: str
    body_hint: dict


class PlanRetrieveResponse(BaseModel):
    intent: str
    recommended_entities: list[str]
    recommended_start: datetime
    recommended_end: datetime
    recommended_resolution: str
    recommended_endpoints: list[RecommendedEndpoint]
    constraints: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
