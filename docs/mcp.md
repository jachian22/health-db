# health-db MCP service

Stateless, authenticated, read-only Streamable HTTP MCP server for Cursor. It does not connect to Postgres. It calls the existing Query API.

The MCP service is stateless. It contains no direct Postgres connection. It calls the existing Query API.

## Architecture

```text
Cursor agent
→ authenticated remote Streamable HTTP MCP service  (this service, /mcp)
→ authenticated HTTP calls to existing Query API     (READ_API_KEY)
→ Railway Postgres
```

Roles:

```text
Query API:
  REST/read API over HTTP.
  Owns health-data logic, range limits, timezone rules, aggregation, and database access.

MCP service:
  Stateless Streamable HTTP tool server.
  Owns MCP transport, MCP caller authentication, tool schemas, input validation,
  and authenticated calls to Query API.

Cursor:
  MCP client and agent host.
  Sees MCP tools.
  Does not directly query the Railway Query API.
```

The MCP endpoint is the protocol path `/mcp` (POST, plus any GET the Streamable HTTP transport requires). There are no REST-shaped tool URLs such as `GET /mcp/get-meals`.

```text
GET  /health   process liveness (no Query API, no auth)
GET  /ready    configuration present + Query API /ready reachable (no auth)
POST /mcp      Streamable HTTP MCP (Authorization: Bearer <MCP_API_KEY>)
```

## Credential boundaries

```text
Cursor client:
  MCP_API_KEY only   (stored locally as HEALTH_DB_MCP_API_KEY; never committed)

MCP Railway service:
  MCP_API_KEY
  READ_API_KEY
  QUERY_API_BASE_URL

Existing backend Railway service:
  READ_API_KEY
  INGEST_API_KEY
  DATABASE_URL

iPhone:
  INGEST_API_KEY in Keychain only
```

Cursor never receives `READ_API_KEY` or `INGEST_API_KEY`. The MCP service never reads `INGEST_API_KEY` and has no database URL setting.

Do not reuse `READ_API_KEY` or `INGEST_API_KEY` as `MCP_API_KEY`.

## Environment variables

Names only. Never put real secrets in git, Dockerfiles, fixtures, or chat.

| Variable | Required | Description |
|---|---|---|
| `MCP_API_KEY` | yes | Bearer token for Cursor → MCP |
| `READ_API_KEY` | yes | Bearer token for MCP → Query API |
| `QUERY_API_BASE_URL` | yes | Query API origin, e.g. `https://health-db-production.up.railway.app` |
| `MCP_SERVICE_NAME` | no | Default `health-db` |
| `MCP_SERVICE_VERSION` | no | Defaults to package version |
| `QUERY_API_TIMEOUT_SECONDS` | no | Default `20` |
| `LOG_LEVEL` | no | Default `INFO` |
| `MCP_ALLOWED_HOSTS` | no | Extra Host allowlist (comma-separated) for DNS-rebinding protection |
| `MCP_ALLOWED_ORIGINS` | no | Extra Origin allowlist. A missing Origin is allowed (Cursor is not a browser) |

Production Query API base URL:

```text
QUERY_API_BASE_URL=https://health-db-production.up.railway.app
```

Missing required settings fail fast at process start. Secret values are never printed.

## Initial tools

Exactly four read-only tools. Each maps to one Query API route.

| MCP tool | Query API | Purpose |
|---|---|---|
| `get_data_coverage` | `GET /v1/query/coverage` | Discover category counts and first/last timestamps |
| `get_glucose_series` | `GET /v1/query/glucose/series` | Bounded raw or aggregated glucose |
| `get_glucose_summary` | `GET /v1/query/glucose/summary` | Descriptive stats without the full series |
| `get_meals` | `GET /v1/query/meals` | Meal events with foods; notes excluded |

Typical agent workflow:

```text
get_data_coverage → get_meals → get_glucose_series → get_glucose_summary
```

Shared inputs: timezone-aware ISO-8601 `start` / `end` (half-open `[start, end)`). Optional `timezone` defaults to `America/New_York`.

### Tool input limits

These are enforced in the MCP service for actionable errors. The Query API still validates independently.

| Constraint | Limit |
|---|---|
| `end` vs `start` | `end` must be later than `start`; both timezone-aware |
| `timezone` | Valid IANA name |
| Glucose `resolution` | `raw` \| `5m` \| `15m` \| `hourly` (default `15m`) |
| `raw` span | maximum 7 days |
| `5m` span | maximum 31 days |
| `15m` span | maximum 90 days |
| `hourly` span | maximum 365 days |
| Summary `bucket` | `overall` \| `daily` (default `overall`) |
| Meal `limit` | default 100, minimum 1, maximum 500 |
| Meal `cursor` | opaque string; passed through unchanged |

Oversized raw glucose example:

```json
{
  "code": "RANGE_TOO_LARGE",
  "message": "Raw glucose queries are limited to 7 days",
  "max_days": 7
}
```

## Privacy rules

**May appear in authenticated tool results:** glucose values, food strings, timestamps, bounded result data, pagination cursors, freshness metadata.

**Must never appear in logs:** `MCP_API_KEY`, `READ_API_KEY`, `INGEST_API_KEY`, `Authorization` headers, glucose values, meal foods, meal notes, source sample IDs, raw tool JSON, raw upstream bodies, database URLs, SQL, stack traces sent to callers.

**Logged:** `request_id`, route/MCP method category, tool name, HTTP status, principal category (`mcp_caller` / `unauthenticated` / `probe`), requested start/end/timezone/resolution/bucket, returned count, truncated flag, upstream outcome category, `latency_ms`, safe error code.

Errors include a `request_id` for Railway log lookup.

This service does not generate charts, files, medical commentary, or LLM output.

## Local run

Use test-only keys. Do not use production secrets.

Terminal 1 — existing Query API (repo root):

```bash
export READ_API_KEY="local-test-read-key"
export INGEST_API_KEY="local-test-ingest-key"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Terminal 2 — MCP service:

```bash
cd mcp
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

export QUERY_API_BASE_URL="http://127.0.0.1:8000"
export READ_API_KEY="local-test-read-key"
export MCP_API_KEY="local-test-mcp-key"

uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8001
```

Checks:

```bash
curl -i http://127.0.0.1:8001/health
curl -i http://127.0.0.1:8001/ready

curl -i -X POST http://127.0.0.1:8001/mcp \
  -H "Content-Type: application/json" --data '{}'

curl -i -X POST http://127.0.0.1:8001/mcp \
  -H "Authorization: Bearer wrong-key" \
  -H "Content-Type: application/json" --data '{}'
```

Unauthenticated and wrong-key `/mcp` requests return HTTP 401 with `WWW-Authenticate: Bearer`. A raw curl POST is not an MCP protocol client; use the SDK test harness (`pytest` in `mcp/`) or Inspector against `/mcp` with `Authorization: Bearer local-test-mcp-key`.

```bash
cd mcp && pytest
```

From the repo root, existing backend tests remain:

```bash
pytest
```

## Railway deployment

Deploy as a **new service** in the existing health-db Railway project. Do not change the existing FastAPI service, its Dockerfile, or its start command.

1. New service from the same GitHub repository.
2. Set **Root Directory** to `mcp` so this service uses `mcp/Dockerfile` and `mcp/railway.json`.
3. Health check path: `/health`.
4. Give the service its own public domain.
5. Variables (values only in Railway / a password manager):

```text
QUERY_API_BASE_URL=https://health-db-production.up.railway.app
READ_API_KEY=<existing Query API read key>
MCP_API_KEY=<new long random secret>
MCP_ALLOWED_HOSTS=<your-mcp-public-hostname>
```

Generate `MCP_API_KEY` locally:

```bash
openssl rand -hex 32
```

Save it in a password manager. Do not commit it. Do not paste it into git, Dockerfiles, or this repo.

Optional: Railway injects `RAILWAY_PUBLIC_DOMAIN`; the service allowlists that Host automatically.

After deploy:

```bash
curl -i https://YOUR-MCP-RAILWAY-DOMAIN/health
curl -i https://YOUR-MCP-RAILWAY-DOMAIN/ready

curl -i -X POST https://YOUR-MCP-RAILWAY-DOMAIN/mcp \
  -H "Content-Type: application/json" --data '{}'

curl -i -X POST https://YOUR-MCP-RAILWAY-DOMAIN/mcp \
  -H "Authorization: Bearer wrong-key" \
  -H "Content-Type: application/json" --data '{}'
```

Then use an MCP client/inspector with the real `MCP_API_KEY` against `https://YOUR-MCP-RAILWAY-DOMAIN/mcp` to list tools and call all four. Confirm Railway logs show request IDs, tool names, and latency — not health values, foods, notes, secrets, or raw payloads.

## Cursor configuration

Verified against [Cursor MCP documentation](https://cursor.com/docs/mcp) (Streamable HTTP remote servers).

Cursor detects a remote server from the `url` field. Official docs do **not** require `"type": "streamable-http"`; `url` plus `headers` is the current supported shape. Config interpolation `${env:NAME}` is supported in `url` and `headers`.

Set the secret on your Mac (shell profile or system environment). Do not commit it:

```bash
export HEALTH_DB_MCP_API_KEY='PASTE_YOUR_MCP_API_KEY'
```

Preferred location: **global** `~/.cursor/mcp.json` (not a project file). Do not put the real key in `.cursor/mcp.json` inside this repository.

```json
{
  "mcpServers": {
    "health-db": {
      "url": "https://YOUR-MCP-RAILWAY-DOMAIN/mcp",
      "headers": {
        "Authorization": "Bearer ${env:HEALTH_DB_MCP_API_KEY}"
      }
    }
  }
}
```

Cursor receives only `MCP_API_KEY`. Cursor never receives `READ_API_KEY` or `INGEST_API_KEY`.

Reload MCP servers from **Customize** (or restart Cursor). Confirm exactly these tools:

```text
get_data_coverage
get_glucose_series
get_glucose_summary
get_meals
```

Bounded smoke workflow (describe available data only; do not make medical claims):

```text
Use get_data_coverage for 2026-08-01 through 2026-08-12.
Then retrieve meals for the same window.
Then retrieve 15-minute glucose data for the same window.
Then retrieve an overall glucose summary.
Describe the available data only; do not make medical claims.
```

## Troubleshooting

| Symptom | Likely cause | What to check |
|---|---|---|
| HTTP 401 from `/mcp` | Missing, malformed, or wrong `Authorization: Bearer` | Cursor header uses `HEALTH_DB_MCP_API_KEY`; MCP service `MCP_API_KEY` matches; `READ_API_KEY` / `INGEST_API_KEY` are rejected on purpose |
| MCP `/ready` is 200 but tools fail | Query API auth or range/validation | MCP `READ_API_KEY` must match the Query API service; tool errors include `request_id` and a safe `code` |
| Query API upstream unavailable | Backend down, wrong `QUERY_API_BASE_URL`, or Query API 401/5xx | `curl` the Query API `/ready`; MCP 401-from-upstream is reported as `UPSTREAM_UNAVAILABLE` without key details |
| Cursor cannot discover tools | Wrong URL, 401, Host allowlist 421, or MCP not reloaded | URL must end with `/mcp`; Host allowlist / `MCP_ALLOWED_HOSTS` / `RAILWAY_PUBLIC_DOMAIN`; reload MCP in Cursor |
| Invalid range/resolution | Window exceeds Query API limits or bad enum | Use the tool error `code` (`RANGE_TOO_LARGE`, `INVALID_RESOLUTION`, `INVALID_TIME_RANGE`, `INVALID_TIMEZONE`, `INVALID_LIMIT`) |
| HTTP 421 Invalid Host header | DNS-rebinding protection | Set `MCP_ALLOWED_HOSTS` to the public hostname (and `hostname:*`) |
| HTTP 403 Invalid Origin header | Unexpected browser `Origin` | Cursor should omit `Origin`. Do not set `Access-Control-Allow-Origin: *`. Add a specific origin only if a browser client is intentional |

## Statelessness

- `stateless_http=True` on the official MCP Python SDK Streamable HTTP app.
- No custom MCP session IDs are created, issued, or required.
- No agent conversation state, health-data cache, or sticky sessions.
- Every `/mcp` request is independently authenticated.
- Every tool call carries all inputs needed to execute.
- Request-scoped values last only for that request.

SDK caveat: the 2026-07-28 protocol is sessionless by construction. `stateless_http=True` also makes the legacy (handshake-era) leg per-request so Railway does not need sticky sessions. This service does not use elicitation, sampling, or other server-to-client back-channel features that legacy sticky sessions would provide.

## Intentionally deferred

OAuth 2.1 / dynamic client registration, multi-user authorization, public third-party MCP access, MCP resources/prompts, chart/report generation, workout/sleep/weight tools, database access from MCP, write/delete tools, MCP session persistence, and HTTP `QUERY` method transport.
