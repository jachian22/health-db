# Health Data Platform — Phase 1

Personal health data API for one user. Accepts normalized events from an iPhone extractor, stores them in Postgres, and exposes a bounded, typed, read-oriented surface designed for agent consumption.

## What Phase 1 includes

- Batch ingestion with idempotent upserts and soft-delete tombstones
- Series, summary, and event lookup endpoints
- Planner-lite (`/v1/plan/retrieve`) for recommending retrieval ranges
- API key auth on all data endpoints
- Hard lookback / row-count / resolution bounds
- Alembic migrations for Railway Postgres

## Stack

- FastAPI + Pydantic v2
- SQLAlchemy 2 (async) + asyncpg
- Alembic
- Railway (Postgres + API service)

## Project layout

```text
app/
├── api/          # route handlers (ingest, series, summary, events, plan)
├── core/         # config, auth, bounds, errors, logging
├── db/           # engine / session
├── models/       # SQLAlchemy models
├── schemas/      # Pydantic request/response models
├── services/     # business logic
└── main.py
alembic/          # migrations
tests/
```

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Start Postgres (local or Railway), set `DATABASE_URL` in `.env`, then:

```bash
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Open docs at `http://localhost:8000/docs`.

## Auth

Pass the API key on every data request:

```http
Authorization: Bearer <API_KEY>
```

or

```http
X-API-Key: <API_KEY>
```

## CORS

`CORS_ORIGINS` defaults to `*`. When using the wildcard, credentials are disabled (Fetch spec). Set an explicit origin list to enable credentials.
## Ingestion

```bash
curl -X POST http://localhost:8000/v1/ingest/batch \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user_1",
    "glucose_samples": [{
      "source_name": "stelo",
      "source_sample_id": "abc",
      "sample_time": "2026-08-01T10:00:00Z",
      "value": 110,
      "unit": "mg/dL"
    }],
    "workouts": [],
    "sleep_sessions": [],
    "weight_measurements": [],
    "meal_events": [],
    "sync_state": []
  }'
```

Upserts are keyed by `(user, source, source_sample_id)`. Set `deleted_at` to tombstone a sample.

## Read API (QUERY / POST)

Read endpoints accept HTTP `QUERY` (preferred) or `POST` with a JSON body. All require `start` and `end` (UTC).

| Endpoint | Purpose |
|---|---|
| `QUERY /v1/series/glucose` | Time-series points (optional resolution: `raw`, `1m`, `5m`, `15m`, `1h`, `1d`) |
| `QUERY /v1/series/runs` | Workout markers (optional `sport` substring filter) |
| `QUERY /v1/series/sleep` | Sleep sessions |
| `QUERY /v1/series/weight` | Weight points |
| `QUERY /v1/series/meals` | Meal intervals + `anchor` (`meal_completed_at` or `meal_end`) |
| `QUERY /v1/summary/daily` | Per-day aggregates |
| `QUERY /v1/summary/weekly` | Per-week aggregates |
| `QUERY /v1/summary/glucose` | Glucose stats (+ `group_by`) |
| `QUERY /v1/summary/runs` | Run stats |
| `QUERY /v1/summary/sleep` | Sleep stats |
| `QUERY /v1/events/meals` | Meal event lookup |
| `QUERY /v1/events/runs` | Run event lookup |
| `QUERY /v1/events/glucose` | Glucose event lookup |
| `QUERY /v1/plan/retrieve` | Planner-lite retrieval recommendations |

Example:

```bash
curl -X POST http://localhost:8000/v1/series/glucose \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "start": "2026-07-06T00:00:00Z",
    "end": "2026-08-05T00:00:00Z",
    "resolution": "15m",
    "user_id": "user_1"
  }'
```

Responses use a stable envelope:

```json
{
  "data": [],
  "meta": { "count": 0, "start": "...", "end": "...", "resolution": "15m", "bounded": true },
  "warnings": [],
  "next_cursor": null
}
```

## Bounds

| Policy | Default |
|---|---|
| Max lookback | 365 days (`MAX_LOOKBACK_DAYS`) |
| Default lookback | 30 days |
| Max rows | 5000 (`MAX_ROWS_PER_RESPONSE`) |
| Resolutions | `raw`, `1m`, `5m`, `15m`, `1h`, `1d` |

Errors are structured: `UNAUTHORIZED`, `INVALID_RANGE`, `RANGE_TOO_WIDE`, `UNSUPPORTED_RESOLUTION`, `TOO_MANY_ROWS`, etc.

## Meals and completion anchors

Meals store `meal_start` and `meal_end`. Optional `meal_completed_at` is the future canonical pivot for fasting windows and post-meal glucose response. Series responses expose `anchor` = `meal_completed_at` if set, else `meal_end`.

## Railway deploy

1. Create a Railway project with a Postgres plugin and this API service.
2. Set env vars: `DATABASE_URL` (auto-linked), `API_KEY`, `ENVIRONMENT=production`.
3. Deploy. `railway.json` runs `alembic upgrade head` then starts uvicorn on `$PORT`.

Private networking between the API and Postgres is preferred; do not expose Postgres publicly.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Tests use in-memory SQLite and do not require Postgres.

## Phase 1 principle

Safe, typed, queryable data with a disciplined retrieval contract — not a database shell.
