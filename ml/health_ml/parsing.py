"""Small shared parsers. Callers wrap ValueError in layer-specific exceptions."""

from __future__ import annotations


def parse_horizon_list(value: str | list[int] | tuple[int, ...]) -> tuple[int, ...]:
    if isinstance(value, (list, tuple)):
        try:
            return tuple(int(item) for item in value)
        except (TypeError, ValueError) as exc:
            raise ValueError("horizons-minutes must be comma-separated positive integers") from exc
    text = value.strip()
    if not text:
        raise ValueError("horizons-minutes must not be empty")
    try:
        return tuple(int(part.strip()) for part in text.split(","))
    except ValueError as exc:
        raise ValueError("horizons-minutes must be comma-separated positive integers") from exc
