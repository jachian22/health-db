# health-db MCP service

Stateless, read-only Streamable HTTP MCP server. It authenticates Cursor with `MCP_API_KEY` and calls the existing Query API with `READ_API_KEY`. It has no database connection.

Full documentation: [`docs/mcp.md`](../docs/mcp.md)

## Local run (test keys only)

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

The existing Query API must already be running on port 8000 with the same `READ_API_KEY`.

## Tests

```bash
cd mcp
pytest
```

Tests never call Railway, Postgres, or real credentials.
