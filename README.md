# Health Data Platform — Phase 1

Personal FastAPI + PostgreSQL service that ingests an iOS HealthKit export, stores normalized health data idempotently, and exposes Query API v1 (authenticated, read-only) for later MCP/agent access.

## What Phase 1 does

- Accept version-1 iOS export payloads via `POST /v1/ingest/batch`
- Store one raw payload copy per ingestion batch (audit/debug)
- Upsert typed rows keyed by `(user_id, source, source_sample_id)`
- Expose Query API v1: `coverage`, `glucose/series`, `glucose/summary`, `meals`
- Separate ingest (`INGEST_API_KEY`) and read (`READ_API_KEY`) credentials
- Request ID on every response (persistent request-audit storage is deferred)

## What Phase 1 does **not** do

- Direct HealthKit access, iOS upload, OAuth, or multi-user product flows
- MCP server, agent orchestration, charts/UI
- Workout / sleep / weight Query API routes (coverage reports existence only)
- Arbitrary SQL or natural-language → SQL
- Sleep sessionization, fasting windows, meal-response derivation
- Meal notes in Query API responses

## Architecture

```text
iPhone app
→ POST /v1/ingest/batch
→ Railway Postgres
→ GET /v1/query/*
→ future MCP server
→ agent / visualization client
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | no (boot) / yes (data) | Postgres URL. Optional at process start so `/health` works; required for `/ready` success and data APIs. Railway `postgres://` / `postgresql://` URLs are normalized to async SQLAlchemy. |
| `INGEST_API_KEY` | yes | Bearer key for ingest endpoints only |
| `READ_API_KEY` | yes | Bearer key for read/query endpoints only |
| `ENVIRONMENT` | no | `development` / `test` / `production` |
| `LOG_LEVEL` | no | Default `INFO` |
| `CORS_ORIGINS` | no | `*` or comma-separated origins |

Copy `.env.example` to `.env` and edit values. Never commit secrets. Do not log `DATABASE_URL`.

## Database readiness

| Endpoint | Meaning |
|---|---|
| `GET /health` | Process liveness only. No database dependency, no auth. Always `200` when FastAPI is running: `{"status":"ok"}`. |
| `GET /ready` | Database availability. Runs `SELECT 1`. Returns `200` with `{"status":"ready","database":"connected"}` when Postgres is reachable; `503` with `DATABASE_UNAVAILABLE` when the URL is missing or the database cannot be reached. Does not expose credentials or raw driver errors. |

Railway supplies `DATABASE_URL` via the Postgres plugin (`DATABASE_URL=${{Postgres.DATABASE_URL}}`).

**Alembic migrations are intentionally not enabled yet** — do not run them on Railway deploy until that step is explicitly rolled out.

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
| `READ_API_KEY` | `read` | `GET /v1/query/*` only |

- Missing/invalid key → `401 UNAUTHORIZED`
- Keys are **not** interchangeable (`INGEST_API_KEY` cannot call Query API; `READ_API_KEY` cannot ingest)
- `GET /health` and `GET /ready` are unauthenticated operational probes

Identity is resolved server-side as `personal-primary`. Clients must **not** send `user_id`.

## Query API v1

All query endpoints are **read-only** and require explicit bounded time ranges (`start`, `end` as timezone-aware ISO-8601). Windows are half-open `[start, end)`. Default timezone: `America/New_York`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/query/coverage` | Counts + first/last per dataset |
| GET | `/v1/query/glucose/series` | Raw or aggregated glucose (`raw`/`5m`/`15m`/`hourly`) |
| GET | `/v1/query/glucose/summary` | Overall or daily descriptive stats |
| GET | `/v1/query/meals` | Meals with foods (notes excluded); cursor pagination |

Glucose resolution hard limits: raw 7d, 5m 31d, 15m 90d, hourly 365d (oversized ranges rejected). Meals default `limit=100`, max `500`.

## Idempotency

Record identity: `(user_id, source, source_sample_id)`.

| Situation | Result |
|---|---|
| New identity | insert |
| Same identity + same fields | unchanged |
| Same identity + changed fields | update |
| Invalid entity | rejected (reported); other entity types still ingest |

## Range semantics

Query API time-range queries use half-open windows:

```text
[start, end)
```

Include records at exactly `start`; exclude records at exactly `end`. Glucose series additionally enforces per-resolution maximum spans (see above). Meal pages never silently truncate: when more rows remain, `truncated=true` and `next_cursor` are returned.

## Example curl commands

### Health check (liveness)

```bash
curl http://localhost:8000/health
```

### Database readiness

```bash
curl -i http://localhost:8000/ready
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

### Coverage

```bash
curl -sS \
  "http://127.0.0.1:8000/v1/query/coverage?start=2026-08-01T00:00:00Z&end=2026-08-12T00:00:00Z" \
  -H "Authorization: Bearer $READ_API_KEY" | python -m json.tool
```

### Glucose series

```bash
curl -sS \
  "http://127.0.0.1:8000/v1/query/glucose/series?start=2026-08-01T00:00:00Z&end=2026-08-07T00:00:00Z&resolution=raw" \
  -H "Authorization: Bearer $READ_API_KEY" | python -m json.tool
```

### Glucose summary

```bash
curl -sS \
  "http://127.0.0.1:8000/v1/query/glucose/summary?start=2026-08-01T00:00:00Z&end=2026-08-07T00:00:00Z&bucket=overall" \
  -H "Authorization: Bearer $READ_API_KEY" | python -m json.tool
```

### Meals

```bash
curl -sS \
  "http://127.0.0.1:8000/v1/query/meals?start=2026-08-01T00:00:00Z&end=2026-08-12T00:00:00Z" \
  -H "Authorization: Bearer $READ_API_KEY" | python -m json.tool
```

## Data privacy / logging policy

**Logged (application logs, metadata only):** request ID, path, method, status, error type. Persistent per-request audit rows (`request_audit_logs`) are deferred to a later phase; the Phase 1 schema intentionally omits that table.

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

Postgres is provisioned and wired via `DATABASE_URL=${{Postgres.DATABASE_URL}}` plus `ENVIRONMENT=production`. The service boots without connecting at startup; use `/health` for process liveness and `/ready` to confirm database reachability.

**Start command** (must run under a shell so `$PORT` expands):

```bash
sh -c 'exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}'
```

Do **not** set a dashboard start command to:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Railway can pass the literal string `$PORT` into uvicorn (which then fails with `'$PORT' is not a valid integer`). Either clear the custom start command (use the Dockerfile `CMD`) or use the `sh -c` form above.

### Later (not this step)

1. Set distinct `INGEST_API_KEY` and `READ_API_KEY`
2. Enable Alembic migrations (`alembic upgrade head`) as an explicit release step — not on every process boot
3. Authenticated ingest + read verification

Do not manually edit the Railway production database.

## Phase 1 principle

The database is the source of truth. The API enforces identity, validation, and query boundaries so a future agent can select tools and ranges safely.
