# health-db ML extraction (Phase 1)

Extract canonical health records from the existing Query API and write an immutable, versioned Parquet snapshot. This package does not train models, mutate Postgres, or change the Query API.

```text
existing Health Query API
          ↓
   ML extraction client
          ↓
  canonical ML records
          ↓
  reproducible snapshot
          ↓
        Parquet
```

Railway/Postgres remains the source of truth for raw health data. Derived ML datasets live here as versioned artifacts under `data/snapshots/`.

## Setup

From the `ml/` directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

`HEALTH_API_URL` and `HEALTH_API_READ_KEY` are required. `QUERY_API_BASE_URL` and `READ_API_KEY` are accepted as aliases so a local MCP `.env` can be reused. Do not hardcode credentials.

The repo-root `.env` is also loaded if present.

## Build a snapshot

From the repository root, after the package is installed:

```bash
python -m health_ml.cli build-dataset \
  --start 2026-08-01T00:00:00-04:00 \
  --end 2026-08-16T23:59:59-04:00 \
  --output data/snapshots
```

The Query API uses half-open `[start, end)`. An end timestamp of `23:59:59` excludes that exact instant; use the next midnight if you want the full civil day.

Output:

```text
data/snapshots/<snapshot-id>/
  glucose.parquet
  meals.parquet
  workouts.parquet
  sleep.parquet
  weight.parquet
  manifest.json
```

Existing snapshot directories are not overwritten unless `--overwrite` is passed.

## Diagnose a snapshot

Phase 1.5 reads an existing snapshot directory only. It does not call the Query API, repair source files, or generate episodes, labels, features, or models.

```bash
python -m health_ml.cli diagnose-snapshot \
  --snapshot data/snapshots/<snapshot-id> \
  --output data/diagnostics
```

Output:

```text
data/diagnostics/<snapshot-id>/<diagnostics-id>/
  diagnostics.json
  diagnostics.md
  glucose_gaps.parquet
  daily_coverage.parquet
  forecast_readiness.parquet
  manifest.json
```

Existing diagnostics artifacts are not overwritten unless `--overwrite` is passed. API credentials are not required.

## Build forecast episodes

Phase 2 reads one existing snapshot and its matching diagnostics artifact. It does not call the Query API, interpolate CGM, create splits, or train a model.

```bash
python -m health_ml.cli build-episodes \
  --snapshot data/snapshots/<snapshot-id> \
  --diagnostics data/diagnostics/<snapshot-id>/<diagnostics-id> \
  --output data/episodes
```

Output:

```text
data/episodes/<snapshot-id>/<episode-dataset-id>/
  episodes.parquet
  episode_glucose_history.parquet
  episode_targets.parquet
  episode_events.parquet
  rejected_anchors.parquet
  diagnostics.json
  manifest.json
  README.md
```

Existing episode datasets are not overwritten unless `--overwrite` is passed. API credentials are not required. Zero accepted episodes is a successful result when the inputs are valid.

Phase 2 still evaluates every glucose timestamp independently. `forecast_readiness.parquet` is used for a post-hoc comparison in `diagnostics.json`; Phase 1.5 `eligible_unique` is never treated as sufficient episode eligibility.

## Tests

Tests mock the Query API and never call Railway.

```bash
cd ml && pytest
```

## Query API contract (inspected)

Phase 1 reads **only** these authenticated Query API routes. It does not query Postgres.

| Category | Route | Window | Notes |
|---|---|---|---|
| Glucose | `GET /v1/query/glucose/series?resolution=raw` | `[start, end)`, max 7 days, 10000 points | Source observations `{timestamp, value_mg_dl}`. Empty buckets omitted; **no interpolation**. |
| Meals | `GET /v1/query/meals` | `[start, end)` on `meal_completed_at` | Cursor pages, max 500 |
| Workouts | `GET /v1/query/workouts` | overlap inclusion; **unclipped** stored intervals | Max 365 days; pagination |
| Sleep | `GET /v1/query/sleep-intervals` | overlap inclusion; **unclipped** stored intervals | Max 90 days; pagination |
| Weight | `GET /v1/query/weight-measurements` | `[start, end)` on `measured_at` | Max 365 days; pagination |

`GET /v1/query/personal-timeline` is **not** used. Its glucose series is a fixed 15-minute mean/min/max visualization envelope, not raw CGM.

There is no separate glucose list route. `resolution=raw` on `/glucose/series` is the authenticated bounded raw-glucose path.

## What this layer preserves

- Half-open extraction windows: `[start, end)`
- Timezone-aware timestamps, stored as UTC in Parquet. No derived America/New_York time-of-day features.
- Raw CGM observations with no interpolation, gap filling, or silent timestamp dedupe
- Meal timestamps as recorded (`meal_completed_at`); no invented meal start times
- Workout and sleep as original source intervals (not clipped to the extraction window). The manifest records the requested bounds.
- All five schema-valid Parquet files, including empty categories
- Fail-closed on malformed records (naive timestamps, invalid intervals, non-numeric glucose, duplicate stable IDs)
- Snapshot identity:
  - **request identity**: requested range plus schema/extractor/API-contract metadata
  - **artifact identity**: SHA-256 of each produced Parquet file
  - `created_at` is provenance only and is not part of the snapshot directory id

## Query API fields omitted from v0.1 parquet

The current Query API does not return these fields. They may exist as optional Python-model hooks, but they are **not written** into snapshot parquet until the API actually exposes them (that will be a new `schema_version`):

- Glucose `trend` and `source`
- Workout `active_energy`, `average_hr`, and `max_hr`

Workout distance is stored as `distance_meters`. Sleep and weight keep the Query API public ids (`sleep_id`, `weight_id`) for traceability.

Glucose values are whatever the Query API returns (currently rounded to 1 decimal place).

## Range handling

The client windows **point-in-range** requests to existing Query API limits. Overlap-inclusion resources (workouts, sleep) are requested as a single `[start, end)` range so original intervals are not duplicated at window seams.

| Resource | API limit | Client behavior |
|---|---|---|
| Glucose `resolution=raw` | 7 days, 10000 points | 7-day half-open windows; split further on `RESULT_TOO_LARGE` |
| Meals | cursor pages, max 500 | follow `next_cursor` |
| Workouts | 365 days + pagination | one request + pagination; oversized ranges surface `RANGE_TOO_LARGE` |
| Sleep | 90 days + pagination | one request + pagination; oversized ranges surface `RANGE_TOO_LARGE` |
| Weight | 365 days + pagination | 365-day half-open windows + pagination; duplicate ids are an error |

A truncated page without `next_cursor` is an error. Records are never silently dropped, interpolated, or repaired. CGM gaps and empty categories are warnings. Duplicate glucose timestamps are kept and counted.

Retries apply to timeouts and 429/5xx. Authentication (`401`) and validation (`422`) errors are not retried.

## Out of scope (later phases)

Model training, CGM gap filling, meal nutrition enrichment, Modal, dashboards, and Query API / database changes.
