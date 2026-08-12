"""Stateless Streamable HTTP MCP service entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mcp.server.transport_security import TransportSecuritySettings

from app.config import Settings, get_settings, validate_query_api_base_url
from app.logging import configure_logging, current_request_id, log_request
from app.query_api_client import HealthDBQueryAPIClient
from app.tools import QueryClient, build_mcp_server


def _transport_security(settings: Settings) -> TransportSecuritySettings:
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=settings.allowed_host_list,
        allowed_origins=settings.allowed_origin_list,
    )


def create_app(
    *,
    settings: Settings | None = None,
    query_client: QueryClient | None = None,
) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)
    client = query_client or HealthDBQueryAPIClient(resolved)
    mcp = build_mcp_server(resolved, client)
    mcp_asgi = mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=_transport_security(resolved),
        streamable_http_path="/mcp",
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            aclose = getattr(client, "aclose", None)
            if aclose is not None:
                await aclose()

    application = FastAPI(
        title="health-db MCP",
        version=resolved.mcp_service_version,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.state.settings = resolved
    application.state.query_client = client
    application.state.mcp = mcp

    from app.auth import McpAuthMiddleware
    from app.logging import RequestIdMiddleware

    application.add_middleware(McpAuthMiddleware, api_key=resolved.mcp_api_key)
    application.add_middleware(RequestIdMiddleware)

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/ready")
    async def ready(request: Request) -> JSONResponse:
        request_id = str(getattr(request.state, "request_id", None) or current_request_id())
        if not validate_query_api_base_url(resolved.query_api_base_url_str):
            log_request(
                request_id=request_id,
                category="ready",
                http_status=503,
                principal="probe",
                error_code="UPSTREAM_UNAVAILABLE",
                upstream_outcome="invalid_base_url",
            )
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "UPSTREAM_UNAVAILABLE",
                        "message": "Query API is unreachable",
                    },
                    "request_id": request_id,
                },
                headers={"X-Request-ID": request_id},
            )
        check_ready = getattr(client, "check_ready", None)
        reachable = await check_ready() if check_ready is not None else False
        if not reachable:
            log_request(
                request_id=request_id,
                category="ready",
                http_status=503,
                principal="probe",
                error_code="UPSTREAM_UNAVAILABLE",
                upstream_outcome="unreachable",
            )
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "UPSTREAM_UNAVAILABLE",
                        "message": "Query API is unreachable",
                    },
                    "request_id": request_id,
                },
                headers={"X-Request-ID": request_id},
            )
        log_request(
            request_id=request_id,
            category="ready",
            http_status=200,
            principal="probe",
            upstream_outcome="reachable",
        )
        return JSONResponse(
            status_code=200,
            content={"status": "ready", "query_api": "reachable"},
            headers={"X-Request-ID": request_id},
        )

    application.mount("/", mcp_asgi)
    return application
