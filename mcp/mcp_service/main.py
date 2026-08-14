"""Stateless Streamable HTTP MCP service entrypoint.

The official MCP SDK Starlette app is the process. Uvicorn serves it directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_service.auth import McpAuthMiddleware
from mcp_service.config import Settings, get_settings
from mcp_service.logging import (
    RequestIdMiddleware,
    configure_logging,
    current_request_id,
    log_request,
)
from mcp_service.query_api_client import HealthDBQueryAPIClient
from mcp_service.tools import QueryClient, build_mcp_server


def _transport_security(settings: Settings) -> TransportSecuritySettings:
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=settings.allowed_host_list,
        allowed_origins=settings.allowed_origin_list,
    )


def _register_probes(mcp: MCPServer, client: QueryClient) -> None:
    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @mcp.custom_route("/ready", methods=["GET"])
    async def ready(request: Request) -> JSONResponse:
        request_id = str(getattr(request.state, "request_id", None) or current_request_id())
        reachable = await client.check_ready()
        if not reachable:
            log_request(
                request_id=request_id,
                category="ready",
                http_status=503,
                outcome="unreachable",
                principal="probe",
                error_code="UPSTREAM_UNAVAILABLE",
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
            outcome="reachable",
            principal="probe",
        )
        return JSONResponse(
            status_code=200,
            content={"status": "ready", "query_api": "reachable"},
            headers={"X-Request-ID": request_id},
        )


async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, HTTPException):
        raise exc
    request_id = str(getattr(request.state, "request_id", None) or current_request_id())
    log_request(
        request_id=request_id,
        category="http",
        http_status=500,
        outcome="internal_error",
        error_code="INTERNAL_ERROR",
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {"code": "INTERNAL_ERROR", "message": "Internal server error"},
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )


def create_app(
    *,
    settings: Settings | None = None,
    query_client: QueryClient | None = None,
) -> Starlette:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)
    client = query_client or HealthDBQueryAPIClient(resolved)

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[dict[str, object]]:
        try:
            yield {}
        finally:
            await client.aclose()

    mcp = build_mcp_server(resolved, client, lifespan=lifespan)
    _register_probes(mcp, client)
    application = mcp.streamable_http_app(
        stateless_http=True,
        streamable_http_path="/mcp",
        transport_security=_transport_security(resolved),
    )
    application.add_middleware(McpAuthMiddleware, api_key=resolved.mcp_api_key)
    application.add_middleware(RequestIdMiddleware)
    application.add_exception_handler(Exception, _unhandled_error)
    application.state.settings = resolved
    application.state.query_client = client
    application.state.mcp = mcp
    return application
