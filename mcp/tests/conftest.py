"""MCP test fixtures — no Postgres, Railway, or real credentials."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from starlette.testclient import TestClient

os.environ["ENVIRONMENT"] = "test"
os.environ["MCP_API_KEY"] = "test-mcp-key"
os.environ["READ_API_KEY"] = "test-read-key"
os.environ["QUERY_API_BASE_URL"] = "http://query-api.test"
os.environ.setdefault("LOG_LEVEL", "INFO")
os.environ["INGEST_API_KEY"] = "test-ingest-key"

from mcp_service.config import Settings  # noqa: E402
from mcp_service.errors import QueryAPIError  # noqa: E402
from mcp_service.main import create_app  # noqa: E402
from mcp_service.models import (  # noqa: E402
    CoverageCategory,
    CoverageMap,
    CoverageResponse,
    GlucoseRawPoint,
    GlucoseSeriesResponse,
    GlucoseSummaryResponse,
    GlucoseSummaryStats,
    MealItem,
    MealsResponse,
)
from mcp_service.tools import build_mcp_server  # noqa: E402

TEST_MCP_KEY = "test-mcp-key"
TEST_READ_KEY = "test-read-key"
TEST_INGEST_KEY = "test-ingest-key"
UNIQUE_FOOD = "UNIQUE_FOOD_STRING_xyz"
UNIQUE_GLUCOSE = 123.456
UNIQUE_SOURCE_ID = "source-sample-UNIQUE-id"

START = datetime(2026, 8, 1, tzinfo=UTC)
END = datetime(2026, 8, 12, tzinfo=UTC)


def make_settings(**overrides: Any) -> Settings:
    data = {
        "MCP_API_KEY": TEST_MCP_KEY,
        "READ_API_KEY": TEST_READ_KEY,
        "QUERY_API_BASE_URL": "http://query-api.test",
        "LOG_LEVEL": "INFO",
        "_env_file": None,
    }
    data.update(overrides)
    env_file = data.pop("_env_file")
    return Settings(**data, _env_file=env_file)


def _empty_category() -> CoverageCategory:
    return CoverageCategory(count=0, first_at=None, last_at=None)


def default_coverage() -> CoverageResponse:
    return CoverageResponse(
        request_id="query-req-1",
        start=START,
        end=END,
        timezone="America/New_York",
        coverage=CoverageMap(
            glucose=CoverageCategory(
                count=2,
                first_at=datetime(2026, 8, 5, 14, 15, tzinfo=UTC),
                last_at=datetime(2026, 8, 5, 14, 30, tzinfo=UTC),
            ),
            meals=CoverageCategory(
                count=1,
                first_at=datetime(2026, 8, 5, 19, 42, tzinfo=UTC),
                last_at=datetime(2026, 8, 5, 19, 42, tzinfo=UTC),
            ),
            workouts=_empty_category(),
            sleep_intervals=_empty_category(),
            weight_measurements=_empty_category(),
        ),
    )


def default_series() -> GlucoseSeriesResponse:
    return GlucoseSeriesResponse(
        request_id="query-req-2",
        start=START,
        end=END,
        timezone="America/New_York",
        resolution="15m",
        aggregation="mean_min_max",
        source_record_count=1,
        returned_point_count=1,
        truncated=False,
        data_fresh_through=datetime(2026, 8, 5, 14, 30, tzinfo=UTC),
        points=[
            GlucoseRawPoint(
                timestamp=datetime(2026, 8, 5, 14, 15, tzinfo=UTC),
                value_mg_dl=UNIQUE_GLUCOSE,
            )
        ],
    )


def default_summary() -> GlucoseSummaryResponse:
    return GlucoseSummaryResponse(
        request_id="query-req-3",
        start=START,
        end=END,
        timezone="America/New_York",
        bucket="overall",
        summary=GlucoseSummaryStats(
            sample_count=1,
            first_at=datetime(2026, 8, 5, 14, 15, tzinfo=UTC),
            last_at=datetime(2026, 8, 5, 14, 15, tzinfo=UTC),
            min_mg_dl=UNIQUE_GLUCOSE,
            max_mg_dl=UNIQUE_GLUCOSE,
            mean_mg_dl=UNIQUE_GLUCOSE,
            median_mg_dl=UNIQUE_GLUCOSE,
        ),
        days=None,
    )


def default_meals() -> MealsResponse:
    return MealsResponse(
        request_id="query-req-4",
        start=START,
        end=END,
        timezone="America/New_York",
        record_count=1,
        truncated=False,
        next_cursor=None,
        data_fresh_through=datetime(2026, 8, 5, 19, 42, tzinfo=UTC),
        items=[
            MealItem(
                id=UNIQUE_SOURCE_ID,
                meal_completed_at=datetime(2026, 8, 5, 19, 42, tzinfo=UTC),
                foods=[UNIQUE_FOOD],
                source="manual",
            )
        ],
    )


@dataclass
class FakeQueryClient:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    coverage: CoverageResponse = field(default_factory=default_coverage)
    series: GlucoseSeriesResponse = field(default_factory=default_series)
    summary: GlucoseSummaryResponse = field(default_factory=default_summary)
    meals: MealsResponse = field(default_factory=default_meals)
    ready_ok: bool = True
    error: Exception | None = None
    closed: bool = False

    async def check_ready(self) -> bool:
        self.calls.append(("ready", {}))
        return self.ready_ok

    async def aclose(self) -> None:
        self.closed = True

    async def get_coverage(self, **kwargs: Any) -> CoverageResponse:
        self.calls.append(("coverage", kwargs))
        if self.error:
            raise self.error
        return self.coverage

    async def get_glucose_series(self, **kwargs: Any) -> GlucoseSeriesResponse:
        self.calls.append(("glucose_series", kwargs))
        if self.error:
            raise self.error
        return self.series.model_copy(update={"resolution": kwargs["resolution"]})

    async def get_glucose_summary(self, **kwargs: Any) -> GlucoseSummaryResponse:
        self.calls.append(("glucose_summary", kwargs))
        if self.error:
            raise self.error
        return self.summary.model_copy(update={"bucket": kwargs["bucket"]})

    async def get_meals(self, **kwargs: Any) -> MealsResponse:
        self.calls.append(("meals", kwargs))
        if self.error:
            raise self.error
        return self.meals


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def fake_query_client() -> FakeQueryClient:
    return FakeQueryClient()


@pytest.fixture
def mcp_server(settings: Settings, fake_query_client: FakeQueryClient):
    return build_mcp_server(settings, fake_query_client)


@pytest.fixture
def app(settings: Settings, fake_query_client: FakeQueryClient):
    return create_app(settings=settings, query_client=fake_query_client)


@pytest.fixture
def client(app) -> TestClient:
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client


@pytest.fixture
def mcp_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_MCP_KEY}"}


def assert_no_secrets(text: str) -> None:
    for secret in (TEST_MCP_KEY, TEST_READ_KEY, TEST_INGEST_KEY):
        assert secret not in text


def query_error(code: str, message: str, **extra: Any) -> QueryAPIError:
    return QueryAPIError(code=code, message=message, **extra)


def parse_mcp_http_body(response) -> dict[str, Any]:
    """Parse a Streamable HTTP JSON or SSE JSON-RPC body."""
    content_type = (response.headers.get("content-type") or "").lower()
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                data = line[5:].strip()
                if data and data != "[DONE]":
                    return json.loads(data)
        raise AssertionError(f"no SSE JSON-RPC payload in {response.text!r}")
    return response.json()
