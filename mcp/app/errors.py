"""Safe MCP/tool/upstream errors. Messages never include secrets or raw bodies."""

from __future__ import annotations

from typing import Any


class ToolError(Exception):
    """Raised inside a tool so the model receives a structured, actionable error."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        self.code = code
        self.message = message
        self.extra = extra
        payload = {"code": code, "message": message, **extra}
        super().__init__(_encode(payload))

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


class QueryAPIError(Exception):
    """Mapped Query API / transport failure. Safe to surface as a tool error."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        self.code = code
        self.message = message
        self.extra = extra
        super().__init__(message)

    def to_tool_error(self, *, request_id: str | None = None) -> ToolError:
        extra = dict(self.extra)
        if request_id:
            extra.setdefault("request_id", request_id)
        return ToolError(self.code, self.message, **extra)


def _encode(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, default=str, separators=(",", ":"))
