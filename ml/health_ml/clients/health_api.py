"""Typed Health Query API client: API responses → canonical ML records.

Does not interpolate CGM, invent meal start times, or perform feature engineering.
Windows requests to the existing Query API range limits and follows pagination.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from time import sleep
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from health_ml.config import (
    DEFAULT_PAGE_LIMIT,
    DEFAULT_QUERY_TIMEZONE,
    MAX_GLUCOSE_POINTS,
    MAX_RETRIES,
    MAX_WEIGHT_RANGE_DAYS,
    RAW_GLUCOSE_MAX_DAYS,
    Settings,
)
from health_ml.errors import HealthAPIError
from health_ml.schemas.canonical import (
    GlucoseRecord,
    MealRecord,
    SleepInterval,
    WeightRecord,
    WorkoutRecord,
    require_aware_utc,
    require_finite,
)
from health_ml.times import require_aware_range, to_iso8601

GLUCOSE_SERIES_PATH = "/v1/query/glucose/series"
MEALS_PATH = "/v1/query/meals"
WORKOUTS_PATH = "/v1/query/workouts"
SLEEP_INTERVALS_PATH = "/v1/query/sleep-intervals"
WEIGHT_MEASUREMENTS_PATH = "/v1/query/weight-measurements"

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_SAFE_UNAVAILABLE = "The health data service is unavailable"
_SAFE_TIMEOUT = "The health data service timed out"
_SAFE_AUTH = "Authentication failed. Check HEALTH_API_READ_KEY."
_SAFE_RESPONSE = "The health data service returned an unexpected response"
_SAFE_TRUNCATED = "The health data service truncated results without a continuation cursor"


class HealthDataClient(Protocol):
    """Extraction surface used by the snapshot builder. Injectable in tests."""

    api_base_url: str | None

    def get_glucose(self, start: datetime, end: datetime) -> list[GlucoseRecord]: ...

    def get_meals(self, start: datetime, end: datetime) -> list[MealRecord]: ...

    def get_workouts(self, start: datetime, end: datetime) -> list[WorkoutRecord]: ...

    def get_sleep(self, start: datetime, end: datetime) -> list[SleepInterval]: ...

    def get_weight(self, start: datetime, end: datetime) -> list[WeightRecord]: ...

    def close(self) -> None: ...


class _ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _GlucoseRawPoint(_ApiModel):
    timestamp: datetime
    value_mg_dl: float

    @field_validator("timestamp")
    @classmethod
    def timestamp_aware(cls, value: datetime) -> datetime:
        return require_aware_utc(value, field_name="timestamp")

    @field_validator("value_mg_dl", mode="before")
    @classmethod
    def glucose_numeric(cls, value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("value_mg_dl must be numeric")
        return require_finite(float(value), field_name="value_mg_dl")


class _GlucoseSeriesResponse(_ApiModel):
    request_id: str
    start: datetime
    end: datetime
    timezone: str
    resolution: str
    aggregation: str | None = None
    source_record_count: int
    returned_point_count: int
    truncated: bool = False
    data_fresh_through: datetime | None = None
    points: list[dict[str, Any]] = Field(default_factory=list)


class _MealItem(_ApiModel):
    id: str
    meal_completed_at: datetime
    foods: list[str]
    source: str

    @field_validator("meal_completed_at")
    @classmethod
    def timestamp_aware(cls, value: datetime) -> datetime:
        return require_aware_utc(value, field_name="meal_completed_at")


class _IntervalItem(_ApiModel):
    id: str
    start_time: datetime
    end_time: datetime

    @field_validator("start_time", "end_time")
    @classmethod
    def timestamp_aware(cls, value: datetime) -> datetime:
        return require_aware_utc(value, field_name="timestamp")

    @model_validator(mode="after")
    def start_before_end(self) -> _IntervalItem:
        if self.end_time <= self.start_time:
            raise ValueError("interval end must be later than start")
        return self


class _WorkoutItem(_IntervalItem):
    sport: str
    distance_meters: float | None = None
    duration_minutes: float
    source: str


class _SleepItem(_IntervalItem):
    duration_minutes: float
    stage: str
    source: str


class _WeightItem(_ApiModel):
    id: str
    measured_at: datetime
    value_kg: float
    source: str

    @field_validator("measured_at")
    @classmethod
    def timestamp_aware(cls, value: datetime) -> datetime:
        return require_aware_utc(value, field_name="measured_at")

    @field_validator("value_kg", mode="before")
    @classmethod
    def weight_numeric(cls, value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("value_kg must be numeric")
        return require_finite(float(value), field_name="value_kg")


class _PagedResponse[TItem](_ApiModel):
    request_id: str
    start: datetime
    end: datetime
    timezone: str
    record_count: int
    truncated: bool = False
    next_cursor: str | None = None
    data_fresh_through: datetime | None = None
    items: list[TItem] = Field(default_factory=list)


class _MealsResponse(_PagedResponse[_MealItem]):
    pass


class _WorkoutsResponse(_PagedResponse[_WorkoutItem]):
    pass


class _SleepResponse(_PagedResponse[_SleepItem]):
    pass


class _WeightResponse(_PagedResponse[_WeightItem]):
    pass


def iter_windows(
    start: datetime, end: datetime, max_days: int
) -> Iterator[tuple[datetime, datetime]]:
    """Half-open [start, end) windows that respect a Query API max span."""
    cursor = start
    step = timedelta(days=max_days)
    while cursor < end:
        nxt = min(cursor + step, end)
        yield cursor, nxt
        cursor = nxt


def _ensure_unique_ids[T](items: list[T], *, key: Callable[[T], str], category: str) -> list[T]:
    seen: set[str] = set()
    for item in items:
        ident = key(item)
        if ident in seen:
            raise HealthAPIError(
                "DUPLICATE_RECORD",
                f"Duplicate {category} id in query results: {ident}",
            )
        seen.add(ident)
    return items


class HealthAPIClient:
    """Synchronous Query API client. Pass `http_client` to test without credentials."""

    def __init__(
        self,
        settings: Settings,
        http_client: httpx.Client | None = None,
        *,
        sleeper: Callable[[float], None] | None = None,
        max_retries: int = MAX_RETRIES,
        page_limit: int = DEFAULT_PAGE_LIMIT,
        timezone: str = DEFAULT_QUERY_TIMEZONE,
    ) -> None:
        self._settings = settings
        self._sleeper = sleeper or sleep
        self._max_retries = max_retries
        self._page_limit = page_limit
        self._timezone = timezone
        if http_client is None:
            self._client = httpx.Client(
                base_url=settings.health_api_url_str,
                timeout=settings.health_api_timeout_seconds,
                headers={"Accept": "application/json", "User-Agent": "health-ml/0.1"},
            )
            self._owns_client = True
        else:
            self._client = http_client
            self._owns_client = False

    @property
    def api_base_url(self) -> str:
        return self._settings.health_api_url_str

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> HealthAPIClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_glucose(self, start: datetime, end: datetime) -> list[GlucoseRecord]:
        start_utc, end_utc = require_aware_range(start, end)
        records: list[GlucoseRecord] = []
        for window_start, window_end in iter_windows(start_utc, end_utc, RAW_GLUCOSE_MAX_DAYS):
            records.extend(self._glucose_window(window_start, window_end))
        return records

    def get_meals(self, start: datetime, end: datetime) -> list[MealRecord]:
        start_utc, end_utc = require_aware_range(start, end)
        items = _ensure_unique_ids(
            self._fetch_all_pages(MEALS_PATH, _MealsResponse, start_utc, end_utc),
            key=lambda item: item.id,
            category="meals",
        )
        try:
            return [
                MealRecord(
                    meal_id=item.id,
                    timestamp=item.meal_completed_at,
                    foods=list(item.foods),
                    source=item.source,
                )
                for item in items
            ]
        except ValidationError as exc:
            raise HealthAPIError("UPSTREAM_RESPONSE_ERROR", _SAFE_RESPONSE) from exc

    def get_workouts(self, start: datetime, end: datetime) -> list[WorkoutRecord]:
        start_utc, end_utc = require_aware_range(start, end)
        items = _ensure_unique_ids(
            self._fetch_all_pages(WORKOUTS_PATH, _WorkoutsResponse, start_utc, end_utc),
            key=lambda item: item.id,
            category="workouts",
        )
        try:
            return [
                WorkoutRecord(
                    workout_id=item.id,
                    start=item.start_time,
                    end=item.end_time,
                    sport=item.sport,
                    distance_meters=item.distance_meters,
                    active_energy=None,
                    average_hr=None,
                    max_hr=None,
                    source=item.source,
                )
                for item in items
            ]
        except ValidationError as exc:
            raise HealthAPIError("UPSTREAM_RESPONSE_ERROR", _SAFE_RESPONSE) from exc

    def get_sleep(self, start: datetime, end: datetime) -> list[SleepInterval]:
        start_utc, end_utc = require_aware_range(start, end)
        items = _ensure_unique_ids(
            self._fetch_all_pages(SLEEP_INTERVALS_PATH, _SleepResponse, start_utc, end_utc),
            key=lambda item: item.id,
            category="sleep",
        )
        try:
            return [
                SleepInterval(
                    sleep_id=item.id,
                    start=item.start_time,
                    end=item.end_time,
                    stage=item.stage,
                    source=item.source,
                )
                for item in items
            ]
        except ValidationError as exc:
            raise HealthAPIError("UPSTREAM_RESPONSE_ERROR", _SAFE_RESPONSE) from exc

    def get_weight(self, start: datetime, end: datetime) -> list[WeightRecord]:
        start_utc, end_utc = require_aware_range(start, end)
        items = self._fetch_windowed_pages(
            WEIGHT_MEASUREMENTS_PATH,
            _WeightResponse,
            start_utc,
            end_utc,
            max_days=MAX_WEIGHT_RANGE_DAYS,
            category="weight",
            unique_key=lambda item: item.id,
        )
        try:
            return [
                WeightRecord(
                    weight_id=item.id,
                    timestamp=item.measured_at,
                    weight_kg=item.value_kg,
                    source=item.source,
                )
                for item in items
            ]
        except ValidationError as exc:
            raise HealthAPIError("UPSTREAM_RESPONSE_ERROR", _SAFE_RESPONSE) from exc

    def _glucose_window(self, start: datetime, end: datetime) -> list[GlucoseRecord]:
        try:
            return self._glucose_request(start, end)
        except HealthAPIError as exc:
            span = end - start
            if exc.code == "RESULT_TOO_LARGE" and span > timedelta(hours=1):
                mid = start + span / 2
                return self._glucose_window(start, mid) + self._glucose_window(mid, end)
            raise

    def _glucose_request(self, start: datetime, end: datetime) -> list[GlucoseRecord]:
        payload = self._request(
            GLUCOSE_SERIES_PATH,
            {
                "start": to_iso8601(start),
                "end": to_iso8601(end),
                "resolution": "raw",
                "timezone": self._timezone,
            },
        )
        response = self._parse(_GlucoseSeriesResponse, payload)
        if response.resolution != "raw":
            raise HealthAPIError(
                "UPSTREAM_RESPONSE_ERROR",
                "Glucose series did not return raw observations",
            )
        if response.truncated:
            raise HealthAPIError("RESULT_TOO_LARGE", _SAFE_TRUNCATED)
        records: list[GlucoseRecord] = []
        for point in response.points:
            try:
                raw = _GlucoseRawPoint.model_validate(point)
            except ValidationError as exc:
                raise HealthAPIError("UPSTREAM_RESPONSE_ERROR", _SAFE_RESPONSE) from exc
            try:
                records.append(
                    GlucoseRecord(
                        timestamp=raw.timestamp,
                        glucose_mg_dl=raw.value_mg_dl,
                    )
                )
            except ValidationError as exc:
                raise HealthAPIError("UPSTREAM_RESPONSE_ERROR", _SAFE_RESPONSE) from exc
        if len(records) > MAX_GLUCOSE_POINTS:
            raise HealthAPIError(
                "RESULT_TOO_LARGE",
                f"Glucose query matched more than {MAX_GLUCOSE_POINTS} points; "
                "narrow the time range",
            )
        return records

    def _fetch_windowed_pages[T](
        self,
        path: str,
        model: type[T],
        start: datetime,
        end: datetime,
        *,
        max_days: int,
        category: str,
        unique_key: Callable[[Any], str],
    ) -> list[Any]:
        """Half-open timestamp windows. Not used for overlap-inclusion resources."""
        items = [
            item
            for window_start, window_end in iter_windows(start, end, max_days)
            for item in self._fetch_all_pages(path, model, window_start, window_end)
        ]
        return _ensure_unique_ids(items, key=unique_key, category=category)

    def _fetch_all_pages[T](
        self,
        path: str,
        model: type[T],
        start: datetime,
        end: datetime,
    ) -> list[Any]:
        items: list[Any] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            params: dict[str, str | int] = {
                "start": to_iso8601(start),
                "end": to_iso8601(end),
                "timezone": self._timezone,
                "limit": self._page_limit,
            }
            if cursor is not None:
                params["cursor"] = cursor
            payload = self._request(path, params)
            page = self._parse(model, payload)
            items.extend(page.items)
            if not page.next_cursor:
                if page.truncated:
                    raise HealthAPIError("RESULT_TOO_LARGE", _SAFE_TRUNCATED)
                break
            if page.next_cursor in seen_cursors:
                raise HealthAPIError("UPSTREAM_RESPONSE_ERROR", "Pagination cursor repeated")
            seen_cursors.add(page.next_cursor)
            cursor = page.next_cursor
        return items

    def _parse[T](self, model: type[T], data: dict[str, Any]) -> T:
        try:
            return model.model_validate(data)  # type: ignore[attr-defined, no-any-return]
        except ValidationError as exc:
            raise HealthAPIError("UPSTREAM_RESPONSE_ERROR", _SAFE_RESPONSE) from exc

    def _request(self, path: str, params: dict[str, str | int]) -> dict[str, Any]:
        delay = 0.5
        last_error: HealthAPIError | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.get(
                    path,
                    params=params,
                    headers={
                        "Authorization": (
                            f"Bearer {self._settings.health_api_read_key.get_secret_value()}"
                        )
                    },
                )
            except httpx.TimeoutException as exc:
                last_error = HealthAPIError("UPSTREAM_TIMEOUT", _SAFE_TIMEOUT)
                last_error.__cause__ = exc
            except httpx.RequestError as exc:
                last_error = HealthAPIError("UPSTREAM_UNAVAILABLE", _SAFE_UNAVAILABLE)
                last_error.__cause__ = exc
            else:
                if response.status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                    retry_after = _retry_after_seconds(response)
                    self._sleeper(retry_after if retry_after is not None else delay)
                    delay *= 2
                    continue
                return self._map_response(response)

            if attempt >= self._max_retries:
                assert last_error is not None
                raise last_error
            self._sleeper(delay)
            delay *= 2

        assert last_error is not None
        raise last_error

    def _map_response(self, response: httpx.Response) -> dict[str, Any]:
        status = response.status_code
        if status == 200:
            try:
                payload = response.json()
            except ValueError as exc:
                raise HealthAPIError("UPSTREAM_RESPONSE_ERROR", _SAFE_RESPONSE) from exc
            if not isinstance(payload, dict):
                raise HealthAPIError("UPSTREAM_RESPONSE_ERROR", _SAFE_RESPONSE)
            return payload

        if status == 401:
            raise HealthAPIError("AUTHENTICATION_FAILED", _SAFE_AUTH)
        if status == 422:
            code, message, extra = _query_error(response)
            raise HealthAPIError(code, message, **extra)
        if status == 429:
            raise HealthAPIError("UPSTREAM_RATE_LIMITED", "The health data service is rate-limiting requests")
        if status >= 500:
            raise HealthAPIError("UPSTREAM_UNAVAILABLE", _SAFE_UNAVAILABLE)
        raise HealthAPIError("UPSTREAM_UNAVAILABLE", _SAFE_UNAVAILABLE)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _query_error(response: httpx.Response) -> tuple[str, str, dict[str, Any]]:
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
