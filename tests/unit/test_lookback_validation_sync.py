"""Query API and MCP lookback parsers must stay aligned."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from app.core.errors import AppError
from app.schemas.queries import remap_lookback_validation, validate_lookback

MCP_ROOT = Path(__file__).resolve().parents[2] / "mcp"


def _mcp_validate_lookback():
    if "mcp_service" not in sys.modules:
        pkg = types.ModuleType("mcp_service")
        pkg.__path__ = [str(MCP_ROOT / "mcp_service")]
        sys.modules["mcp_service"] = pkg
    from mcp_service.errors import ToolError
    from mcp_service.models import validate_lookback as mcp_validate

    return mcp_validate, ToolError


def _kwargs(*, default: int, max_value: int, unit: str) -> dict[str, str | int]:
    return {
        "default": default,
        "max_value": max_value,
        "unit": unit,
        "field_name": "lookback",
        "label": "Lookback",
    }


@pytest.mark.parametrize(
    ("value", "default", "max_value", "unit", "expected_code", "expected_value", "detail_key"),
    [
        (None, 30, 30, "days", None, 30, None),
        (5, 30, 30, "days", None, 5, None),
        (0, 30, 30, "days", "INVALID_LOOKBACK", None, None),
        (-1, 30, 30, "days", "INVALID_LOOKBACK", None, None),
        ("abc", 30, 30, "days", "INVALID_LOOKBACK", None, None),
        (31, 30, 30, "days", "RANGE_TOO_LARGE", None, "max_days"),
        (37, 24, 36, "hours", "RANGE_TOO_LARGE", None, "max_hours"),
        (49, 24, 48, "hours", "RANGE_TOO_LARGE", None, "max_hours"),
    ],
)
def test_lookback_parsers_agree(
    value,
    default: int,
    max_value: int,
    unit: str,
    expected_code: str | None,
    expected_value: int | None,
    detail_key: str | None,
):
    mcp_validate, tool_error_cls = _mcp_validate_lookback()
    kwargs = _kwargs(default=default, max_value=max_value, unit=unit)

    if expected_code is None:
        assert validate_lookback(value, **kwargs) == expected_value
        assert mcp_validate(value, **kwargs) == expected_value
        return

    with pytest.raises(AppError) as app_exc:
        validate_lookback(value, **kwargs)
    assert app_exc.value.code == expected_code
    if detail_key is not None:
        assert app_exc.value.details == {detail_key: max_value}
    else:
        assert app_exc.value.details is None

    with pytest.raises(tool_error_cls) as mcp_exc:
        mcp_validate(value, **kwargs)
    assert mcp_exc.value.code == expected_code
    if detail_key is not None:
        assert mcp_exc.value.extra == {detail_key: max_value}
    else:
        assert detail_key not in mcp_exc.value.extra


def test_remap_lookback_validation_maps_query_parse_errors():
    err = remap_lookback_validation(
        [{"loc": ("query", "lookback_days"), "type": "int_parsing", "msg": "x"}]
    )
    assert err is not None
    assert err.code == "INVALID_LOOKBACK"
    assert err.status_code == 422
    assert "lookback_days" in err.message


def test_remap_lookback_validation_ignores_unrelated_fields():
    assert (
        remap_lookback_validation([{"loc": ("query", "limit"), "type": "int_parsing", "msg": "x"}])
        is None
    )
