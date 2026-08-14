"""Typed HTTP client for the existing health-db Query API.

Never forwards caller headers. Never interpolates unvalidated input into URLs.
Never includes credentials in raised errors or returned models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from pydantic import ValidationError

from mcp_service.config import Settings
from mcp_service.errors import QueryAPIError
from mcp_service.models import (
    ContextSnapshotResponse,
    CoverageResponse,
    GlucoseSeriesResponse,
    GlucoseSummaryResponse,
    LastLoggedMealResponse,
    MealsResponse,
    PersonalTimelineResponse,
    SleepIntervalsResponse,
    WeightMeasurementsResponse,
    WorkoutsResponse,
    to_iso8601,
)

COVERAGE_PATH = "/v1/query/coverage"
GLUCOSE_SERIES_PATH = "/v1/query/glucose/series"
GLUCOSE_SUMMARY_PATH = "/v1/query/glucose/summary"
MEALS_PATH = "/v1/query/meals"
WORKOUTS_PATH = "/v1/query/workouts"
SLEEP_INTERVALS_PATH = "/v1/query/sleep-intervals"
WEIGHT_MEASUREMENTS_PATH = "/v1/query/weight-measurements"
LAST_LOGGED_MEAL_PATH = "/v1/query/last-logged-meal"
CONTEXT_SNAPSHOT_PATH = "/v1/query/context-snapshot"
PERSONAL_TIMELINE_PATH = "/v1/query/personal-timeline"
READY_PATH = "/ready"

_SAFE_UNAVAILABLE = "The health data service is unavailable"
_SAFE_TIMEOUT = "The health data service timed out"
_SAFE_RATE_LIMIT = "The health data service is rate-limiting requests"
_SAFE_RESPONSE = "The health data service returned an unexpected response"


class HealthDBQueryAPIClient:
    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        if http_client is None:
            self._client = httpx.AsyncClient(
                base_url=self._settings.query_api_base_url_str,
                timeout=self._settings.query_api_timeout_seconds,
                headers={"Accept": "application/json"},
            )
            self._owns_client = True
        else:
            self._client = http_client
            self._owns_client = False

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def check_ready(self) -> bool:
        """GET /ready on the Query API. Does not send READ_API_KEY or query health data."""
        try:
            request = self._client.build_request("GET", READY_PATH)
            if "authorization" in request.headers:
                del request.headers["authorization"]
            response = await self._client.send(request)
        except httpx.RequestError:
            return False
        return response.status_code == 200

    async def get_coverage(
        self,
        *,
        start: datetime,
        end: datetime,
        timezone: str,
    ) -> CoverageResponse:
        data = await self._request(
            COVERAGE_PATH,
            {"start": to_iso8601(start), "end": to_iso8601(end), "timezone": timezone},
        )
        return self._parse(CoverageResponse, data)

    async def get_glucose_series(
        self,
        *,
        start: datetime,
        end: datetime,
        resolution: str,
        timezone: str,
    ) -> GlucoseSeriesResponse:
        data = await self._request(
            GLUCOSE_SERIES_PATH,
            {
                "start": to_iso8601(start),
                "end": to_iso8601(end),
                "resolution": resolution,
                "timezone": timezone,
            },
        )
        return self._parse(GlucoseSeriesResponse, data)

    async def get_glucose_summary(
        self,
        *,
        start: datetime,
        end: datetime,
        bucket: str,
        timezone: str,
    ) -> GlucoseSummaryResponse:
        data = await self._request(
            GLUCOSE_SUMMARY_PATH,
            {
                "start": to_iso8601(start),
                "end": to_iso8601(end),
                "bucket": bucket,
                "timezone": timezone,
            },
        )
        return self._parse(GlucoseSummaryResponse, data)

    async def get_meals(
        self,
        *,
        start: datetime,
        end: datetime,
        timezone: str,
        limit: int,
        cursor: str | None = None,
    ) -> MealsResponse:
        return await self._get_paged(
            MEALS_PATH,
            MealsResponse,
            start=start,
            end=end,
            timezone=timezone,
            limit=limit,
            cursor=cursor,
        )

    async def get_workouts(
        self,
        *,
        start: datetime,
        end: datetime,
        timezone: str,
        limit: int,
        cursor: str | None = None,
    ) -> WorkoutsResponse:
        return await self._get_paged(
            WORKOUTS_PATH,
            WorkoutsResponse,
            start=start,
            end=end,
            timezone=timezone,
            limit=limit,
            cursor=cursor,
        )

    async def get_sleep_intervals(
        self,
        *,
        start: datetime,
        end: datetime,
        timezone: str,
        limit: int,
        cursor: str | None = None,
    ) -> SleepIntervalsResponse:
        return await self._get_paged(
            SLEEP_INTERVALS_PATH,
            SleepIntervalsResponse,
            start=start,
            end=end,
            timezone=timezone,
            limit=limit,
            cursor=cursor,
        )

    async def get_weight_measurements(
        self,
        *,
        start: datetime,
        end: datetime,
        timezone: str,
        limit: int,
        cursor: str | None = None,
    ) -> WeightMeasurementsResponse:
        return await self._get_paged(
            WEIGHT_MEASUREMENTS_PATH,
            WeightMeasurementsResponse,
            start=start,
            end=end,
            timezone=timezone,
            limit=limit,
            cursor=cursor,
        )

    async def get_last_logged_meal(
        self,
        *,
        anchor: datetime,
        timezone: str,
        lookback_days: int,
    ) -> LastLoggedMealResponse:
        data = await self._request(
            LAST_LOGGED_MEAL_PATH,
            {
                "anchor": to_iso8601(anchor),
                "timezone": timezone,
                "lookback_days": lookback_days,
            },
        )
        return self._parse(LastLoggedMealResponse, data)

    async def get_context_snapshot(
        self,
        *,
        anchor: datetime,
        timezone: str,
        meal_lookback_days: int,
        sleep_lookback_hours: int,
        glucose_lookback_hours: int,
    ) -> ContextSnapshotResponse:
        data = await self._request(
            CONTEXT_SNAPSHOT_PATH,
            {
                "anchor": to_iso8601(anchor),
                "timezone": timezone,
                "meal_lookback_days": meal_lookback_days,
                "sleep_lookback_hours": sleep_lookback_hours,
                "glucose_lookback_hours": glucose_lookback_hours,
            },
        )
        return self._parse(ContextSnapshotResponse, data)

    async def get_personal_timeline(
        self,
        *,
        start: datetime,
        end: datetime,
        timezone: str,
    ) -> PersonalTimelineResponse:
        data = await self._request(
            PERSONAL_TIMELINE_PATH,
            {"start": to_iso8601(start), "end": to_iso8601(end), "timezone": timezone},
        )
        return self._parse(PersonalTimelineResponse, data)

    async def _get_paged[T](
        self,
        path: str,
        model: type[T],
        *,
        start: datetime,
        end: datetime,
        timezone: str,
        limit: int,
        cursor: str | None = None,
    ) -> T:
        params: dict[str, str | int] = {
            "start": to_iso8601(start),
            "end": to_iso8601(end),
            "timezone": timezone,
            "limit": limit,
        }
        if cursor is not None:
            params["cursor"] = cursor
        data = await self._request(path, params)
        return self._parse(model, data)

    def _parse[T](self, model: type[T], data: dict[str, Any]) -> T:
        try:
            return model.model_validate(data)  # type: ignore[attr-defined, no-any-return]
        except ValidationError as exc:
            raise QueryAPIError(code="UPSTREAM_RESPONSE_ERROR", message=_SAFE_RESPONSE) from exc

    async def _request(self, path: str, params: dict[str, str | int]) -> dict[str, Any]:
        try:
            response = await self._client.get(
                path,
                params=params,
                headers={
                    "Authorization": (
                        f"Bearer {self._settings.read_api_key.get_secret_value()}"
                    )
                },
            )
        except httpx.TimeoutException as exc:
            raise QueryAPIError(code="UPSTREAM_TIMEOUT", message=_SAFE_TIMEOUT) from exc
        except httpx.RequestError as exc:
            raise QueryAPIError(code="UPSTREAM_UNAVAILABLE", message=_SAFE_UNAVAILABLE) from exc

        return self._map_response(response)

    def _map_response(self, response: httpx.Response) -> dict[str, Any]:
        status = response.status_code
        if status == 200:
            try:
                payload = response.json()
            except ValueError as exc:
                raise QueryAPIError(code="UPSTREAM_RESPONSE_ERROR", message=_SAFE_RESPONSE) from exc
            if not isinstance(payload, dict):
                raise QueryAPIError(code="UPSTREAM_RESPONSE_ERROR", message=_SAFE_RESPONSE)
            return payload

        if status == 401:
            raise QueryAPIError(code="UPSTREAM_UNAVAILABLE", message=_SAFE_UNAVAILABLE)
        if status == 429:
            raise QueryAPIError(code="UPSTREAM_RATE_LIMITED", message=_SAFE_RATE_LIMIT)
        if status == 422:
            code, message, extra = _safe_query_error(response)
            raise QueryAPIError(code=code, message=message, **extra)
        if status >= 500:
            raise QueryAPIError(code="UPSTREAM_UNAVAILABLE", message=_SAFE_UNAVAILABLE)
        raise QueryAPIError(code="UPSTREAM_UNAVAILABLE", message=_SAFE_UNAVAILABLE)


def _safe_query_error(response: httpx.Response) -> tuple[str, str, dict[str, Any]]:
    try:
        body = response.json()
    except ValueError:
        return "INVALID_REQUEST", "The request was rejected", {}
    if not isinstance(body, dict):
        return "INVALID_REQUEST", "The request was rejected", {}
    error = body.get("error")
    if not isinstance(error, dict):
        return "INVALID_REQUEST", "The request was rejected", {}
    code = error.get("code")
    message = error.get("message")
    details = error.get("details")
    extra: dict[str, Any] = {}
    if isinstance(details, dict):
        extra.update(details)
    return (
        str(code) if isinstance(code, str) and code else "INVALID_REQUEST",
        str(message) if isinstance(message, str) and message else "The request was rejected",
        extra,
    )
