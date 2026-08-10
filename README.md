# Health Data Platform — Phase 1

Personal FastAPI + PostgreSQL service that ingests an iOS HealthKit export, stores normalized health data idempotently, and exposes a narrow authenticated read API for later agent consumption.

## What Phase 1 does

- Accept version-1 iOS export payloads via `POST /v1/ingest/batch`
- Store one raw payload copy per ingestion batch (audit/debug)
- Upsert typed rows keyed by `(user_id, source, source_sample_id)`
- Expose bounded read endpoints for glucose, runs, sleep intervals, weight, and meals
- Separate ingest and read API keys
- Request ID + metadata-only audit logging

## What Phase 1 does **not** do

- Direct HealthKit access, iOS upload, OAuth, or multi-user product flows
- MCP / planner / compare / daily-weekly summary endpoints
- Arbitrary SQL or natural-language → SQL
- Sleep sessionization, fasting windows, meal-response derivation
- Charts or UI
- Railway deployment (repo is ready; deploy is deferred)

## Architecture

```text
Future iOS exporter
      |
      | POST /v1/ingest/batch
      v
FastAPI service
      |
      | validate, report, upsert
      v
PostgreSQL
      |
      +-- ingestion_batches (audit/debug)
      +-- typed health records
      |
      v
Authenticated read API
      |
      v
Future agent tools / MCP harness
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | yes | Async Postgres URL (`postgresql+asyncpg://...`) |
| `INGEST_API_KEY` | yes | Bearer key for ingest endpoints only |
| `READ_API_KEY` | yes | Bearer key for read/query endpoints only |
| `ENVIRONMENT` | no | `development` / `test` / `production` |
| `LOG_LEVEL` | no | Default `INFO` |
| `CORS_ORIGINS` | no | `*` or comma-separated origins |

Copy `.env.example` to `.env` and edit values. Never commit secrets.

## Local setup

```bash
# Optional: local Postgres + API via Docker
docker compose up -d db

python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

With [uv](https://github.com/astral-sh/uv):

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Migrations

```bash
alembic upgrade head
```

This creates tables and idempotently seeds `personal-primary`.

Downgrade (dev only):

```bash
alembic downgrade -1
```

## Run the API

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Docs: http://localhost:8000/docs

Or full stack:

```bash
docker compose up --build
```

## Authentication roles

| Key | Role | Access |
|---|---|---|
| `INGEST_API_KEY` | `ingest` | `POST /v1/ingest/batch` only |
| `READ_API_KEY` | `read` | `/v1/query/...` only |

- Missing/invalid key → `401 UNAUTHORIZED`
- Valid key, wrong role → `403 FORBIDDEN`
- `GET /health` is unauthenticated

Identity is resolved from auth context as `personal-primary`. Clients must **not** send `user_id`.

## Idempotency

Record identity: `(user_id, source, source_sample_id)`.

| Situation | Result |
|---|---|
| New identity | insert |
| Same identity + same fields | unchanged |
| Same identity + changed fields | update |
| Invalid entity | rejected (reported); other entity types still ingest |

## Range semantics

All time-range queries use half-open windows:

```text
[start, end)
```

Include records at exactly `start`; exclude records at exactly `end`. Maximum range: **365 days**. Default row cap: **5000**. Hard cap: **20000**. Overflow returns `TOO_MANY_ROWS` (never silent truncation).

## Example curl commands

### Health check

```bash
curl http://localhost:8000/health
```

### Ingest

```bash
curl -X POST http://localhost:8000/v1/ingest/batch \
  -H "Authorization: Bearer $INGEST_API_KEY" \
  -H "Content-Type: application/json" \
  -d @- <<'EOF'
{
  "payload": {
    "complete": true,
    "schema_version": 1,
    "exported_at": "2026-08-10T20:02:50.510Z",
    "data_start": "2026-07-30T00:00:00.000Z",
    "data_end": "2026-08-10T20:02:50.369Z",
    "errors": [],
    "glucose_samples": [],
    "workouts": [],
    "sleep_sessions": [],
    "weight_measurements": [],
    "meal_events": []
  }
}
EOF
```

### Glucose query

```bash
curl -X POST http://localhost:8000/v1/query/series/glucose \
  -H "Authorization: Bearer $READ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "start": "2026-08-01T00:00:00Z",
    "end": "2026-08-08T00:00:00Z",
    "resolution": "raw",
    "limit": 5000
  }'
```

### Runs query

```bash
curl -X POST http://localhost:8000/v1/query/series/runs \
  -H "Authorization: Bearer $READ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "start": "2026-08-01T00:00:00Z",
    "end": "2026-08-08T00:00:00Z"
  }'
```

### Sleep query

```bash
curl -X POST http://localhost:8000/v1/query/series/sleep \
  -H "Authorization: Bearer $READ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "start": "2026-08-01T00:00:00Z",
    "end": "2026-08-08T00:00:00Z",
    "stages": ["core", "deep", "rem", "awake"]
  }'
```

### Weight query

```bash
curl -X POST http://localhost:8000/v1/query/series/weight \
  -H "Authorization: Bearer $READ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "start": "2026-08-01T00:00:00Z",
    "end": "2026-08-08T00:00:00Z"
  }'
```

### Meals query

```bash
curl -X POST http://localhost:8000/v1/query/events/meals \
  -H "Authorization: Bearer $READ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "start": "2026-08-01T00:00:00Z",
    "end": "2026-08-08T00:00:00Z"
  }'
```

## Data privacy / logging policy

**Logged / audited (metadata only):** request ID, path, method, auth role, status, latency, query window, resolution, row count, error code.

**Not logged:** Authorization headers, raw export payloads (except the intentional one-copy `ingestion_batches.raw_payload`), glucose arrays, meal foods/text, full response bodies, database credentials.

## Tests

Requires a running Postgres (e.g. `docker compose up -d db`).

```bash
export DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/health_db_test
# create DB once:
psql postgresql://postgres:postgres@localhost:5432/postgres -c 'CREATE DATABASE health_db_test;'
alembic upgrade head   # against health_db_test via DATABASE_URL
pytest
```

Or use the helper in `tests/conftest.py`, which migrates the test database automatically when `DATABASE_URL` / `TEST_DATABASE_URL` is set.

## Railway deployment

This step is intentionally **API-only**: no Postgres wiring, no Alembic on boot, no ingestion secrets required for the service to become healthy.

**Start command** (configured in `railway.json` and the `Dockerfile` `CMD`):

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

- FastAPI module/variable: `app.main:app`
- Railway injects `PORT`; the process binds to `0.0.0.0:$PORT` (not localhost / not a hard-coded 8000)
- Health check path: `GET /health` → `{"status":"ok"}` (HTTP 200, no database)
- The Docker image does **not** run Alembic on boot

Postgres is **not** configured in this deployment step. Migrations and `DATABASE_URL` come later.

### Railway dashboard

If the service overrides the start command in the dashboard, set it to the same uvicorn command above (or clear the override so `railway.json` is used). Keep the health-check path at `/health`.

### Later (not this step)

1. Provision Railway Postgres and set `DATABASE_URL`
2. Set distinct `INGEST_API_KEY` and `READ_API_KEY`
3. Run Alembic (`alembic upgrade head`) before or as a separate release step
4. Authenticated ingest + read verification

Do not manually edit the Railway production database.

## Phase 1 principle

The database is the source of truth. The API enforces identity, validation, and query boundaries so a future agent can select tools and ranges safely.
