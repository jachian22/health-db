"""Planner-lite: recommend bounded retrieval plans for agents."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.core.errors import InvalidRangeError
from app.schemas.plan import PlanRetrieveRequest, PlanRetrieveResponse, RecommendedEndpoint

ENTITY_SERIES_PATHS = {
    "glucose": "/v1/series/glucose",
    "runs": "/v1/series/runs",
    "sleep": "/v1/series/sleep",
    "weight": "/v1/series/weight",
    "meals": "/v1/series/meals",
}

ENTITY_KEYWORDS = ("glucose", "runs", "run", "sleep", "weight", "meals", "meal")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _body(start: datetime, end: datetime) -> dict:
    return {"start": _iso(start), "end": _iso(end)}


def _plan_glucose_around_runs(
    req: PlanRetrieveRequest, start: datetime, end: datetime, entities: list[str]
) -> tuple[list[str], str, list[RecommendedEndpoint], list[str], datetime]:
    recommended = entities or ["glucose", "runs", "meals"]
    resolution = "5m" if req.horizon_days <= 14 else "15m"
    body = _body(start, end)
    endpoints = [
        RecommendedEndpoint(path="/v1/series/runs", body_hint={**body, "resolution": "raw"}),
        RecommendedEndpoint(path="/v1/series/glucose", body_hint={**body, "resolution": resolution}),
        RecommendedEndpoint(path="/v1/series/meals", body_hint={**body, "resolution": "raw"}),
    ]
    caveats = [
        (
            "Overlay runs as event markers on glucose series; use meal_completed_at (or meal_end) "
            "to classify fasted vs fed runs later."
        )
    ]
    return recommended, resolution, endpoints, caveats, start


def _plan_meal_response(
    req: PlanRetrieveRequest, start: datetime, end: datetime, entities: list[str]
) -> tuple[list[str], str, list[RecommendedEndpoint], list[str], datetime]:
    recommended = entities or ["meals", "glucose"]
    resolution = "5m"
    meal_start = end - timedelta(days=min(req.horizon_days, 14))
    body = _body(meal_start, end)
    endpoints = [
        RecommendedEndpoint(path="/v1/events/meals", body_hint={**body, "limit": 50}),
        RecommendedEndpoint(path="/v1/series/glucose", body_hint={**body, "resolution": resolution}),
    ]
    caveats = [
        (
            "Pivot glucose response around meal_completed_at when available; fall back to meal_end. "
            "meal_start/meal_end are retained for interval fidelity."
        )
    ]
    return recommended, resolution, endpoints, caveats, meal_start


def _plan_daily_overview(
    req: PlanRetrieveRequest, start: datetime, end: datetime, entities: list[str]
) -> tuple[list[str], str, list[RecommendedEndpoint], list[str], datetime]:
    recommended = entities or ["glucose", "runs", "sleep", "meals", "weight"]
    body = _body(start, end)
    return recommended, "1d", [RecommendedEndpoint(path="/v1/summary/daily", body_hint=body)], [], start


def _plan_weekly_overview(
    req: PlanRetrieveRequest, start: datetime, end: datetime, entities: list[str]
) -> tuple[list[str], str, list[RecommendedEndpoint], list[str], datetime]:
    recommended = entities or ["glucose", "runs", "sleep", "meals"]
    body = _body(start, end)
    return recommended, "1d", [RecommendedEndpoint(path="/v1/summary/weekly", body_hint=body)], [], start


def _plan_sleep_trend(
    req: PlanRetrieveRequest, start: datetime, end: datetime, entities: list[str]
) -> tuple[list[str], str, list[RecommendedEndpoint], list[str], datetime]:
    recommended = entities or ["sleep"]
    body = _body(start, end)
    endpoints = [
        RecommendedEndpoint(path="/v1/summary/sleep", body_hint=body),
        RecommendedEndpoint(path="/v1/series/sleep", body_hint={**body, "resolution": "raw"}),
    ]
    return recommended, "1d", endpoints, [], start


def _plan_weight_trend(
    req: PlanRetrieveRequest, start: datetime, end: datetime, entities: list[str]
) -> tuple[list[str], str, list[RecommendedEndpoint], list[str], datetime]:
    recommended = entities or ["weight"]
    body = _body(start, end)
    endpoints = [
        RecommendedEndpoint(path="/v1/series/weight", body_hint={**body, "resolution": "raw"}),
    ]
    return recommended, "1d", endpoints, [], start


def _plan_fasting_window(
    req: PlanRetrieveRequest, start: datetime, end: datetime, entities: list[str]
) -> tuple[list[str], str, list[RecommendedEndpoint], list[str], datetime]:
    recommended = entities or ["meals", "runs", "glucose"]
    resolution = "15m"
    body = _body(start, end)
    endpoints = [
        RecommendedEndpoint(path="/v1/events/meals", body_hint={**body, "limit": 100}),
        RecommendedEndpoint(path="/v1/events/runs", body_hint={**body, "limit": 50}),
        RecommendedEndpoint(path="/v1/series/glucose", body_hint={**body, "resolution": resolution}),
    ]
    caveats = [
        (
            "Fasting windows should be derived from successive meal_completed_at timestamps. "
            "Phase 1 stores the field but does not yet compute fasting intervals server-side."
        )
    ]
    return recommended, resolution, endpoints, caveats, start


PlanHandler = Callable[
    [PlanRetrieveRequest, datetime, datetime, list[str]],
    tuple[list[str], str, list[RecommendedEndpoint], list[str], datetime],
]

KNOWN_INTENTS: dict[str, PlanHandler] = {
    "render_glucose_around_runs": _plan_glucose_around_runs,
    "render_meal_response": _plan_meal_response,
    "daily_overview": _plan_daily_overview,
    "weekly_overview": _plan_weekly_overview,
    "sleep_trend": _plan_sleep_trend,
    "weight_trend": _plan_weight_trend,
    "fasting_window": _plan_fasting_window,
}


def _normalize_entity_keyword(token: str) -> str | None:
    if token in ("run", "runs"):
        return "runs"
    if token in ("meal", "meals"):
        return "meals"
    if token in ("glucose", "sleep", "weight"):
        return token
    return None


def _heuristic_entities(intent: str) -> list[str]:
    matched: list[str] = []
    seen: set[str] = set()
    for kw in ENTITY_KEYWORDS:
        if kw in intent:
            ent = _normalize_entity_keyword(kw)
            if ent and ent not in seen:
                seen.add(ent)
                matched.append(ent)
    return matched


def _series_endpoints(
    entities: list[str], start: datetime, end: datetime, resolution: str
) -> list[RecommendedEndpoint]:
    body = _body(start, end)
    endpoints: list[RecommendedEndpoint] = []
    for ent in entities:
        path = ENTITY_SERIES_PATHS.get(ent)
        if path:
            endpoints.append(
                RecommendedEndpoint(path=path, body_hint={**body, "resolution": resolution})
            )
    return endpoints


def plan_retrieve(req: PlanRetrieveRequest) -> PlanRetrieveResponse:
    settings = get_settings()
    if req.horizon_days > settings.max_lookback_days:
        raise InvalidRangeError(
            f"horizon_days {req.horizon_days} exceeds max lookback of {settings.max_lookback_days}"
        )

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=req.horizon_days)
    intent = req.intent.lower().strip()
    entities = [e.lower() for e in req.entities] if req.entities else []
    constraints = [
        f"max_lookback_days={settings.max_lookback_days}",
        f"max_rows_per_response={settings.max_rows_per_response}",
        "start and end required on series/summary/events endpoints",
        "timestamps must be UTC",
    ]
    caveats: list[str] = []
    resolution = "15m"
    endpoints: list[RecommendedEndpoint] = []
    recommended_entities: list[str] = []

    handler = KNOWN_INTENTS.get(intent)
    if handler is not None:
        recommended_entities, resolution, endpoints, caveats, start = handler(
            req, start, end, entities
        )
    elif intent in ("build_chart", "custom"):
        recommended_entities = entities or ["glucose"]
        endpoints = _series_endpoints(recommended_entities, start, end, resolution)
        if not endpoints:
            endpoints = _series_endpoints(["glucose"], start, end, resolution)
            recommended_entities = ["glucose"]
        caveats.append("Generic intent — defaulted to series retrieval for requested entities.")
    elif req.goal == "summarize" and not entities:
        recommended_entities, resolution, endpoints, caveats, start = _plan_daily_overview(
            req, start, end, entities
        )
    else:
        # Unknown intent: keyword heuristics — recommend ALL matched entities.
        matched = _heuristic_entities(intent)
        if matched:
            recommended_entities = entities or matched
            endpoints = _series_endpoints(recommended_entities, start, end, resolution)
            caveats.append(
                (
                    "Unrecognized intent — heuristically interpreted from keywords; "
                    "recommended series endpoints for all matched entities."
                )
            )
        else:
            recommended_entities = entities or ["glucose"]
            endpoints = _series_endpoints(recommended_entities, start, end, resolution)
            if not endpoints:
                endpoints = _series_endpoints(["glucose"], start, end, resolution)
                recommended_entities = ["glucose"]
            caveats.append(
                "Unrecognized intent — defaulted to series retrieval for requested entities."
            )

    if req.horizon_days > 90:
        caveats.append("Wide horizons may require coarser resolution to stay under row limits.")

    return PlanRetrieveResponse(
        intent=req.intent,
        recommended_entities=recommended_entities,
        recommended_start=start,
        recommended_end=end,
        recommended_resolution=resolution,
        recommended_endpoints=endpoints,
        constraints=constraints,
        caveats=caveats,
    )
