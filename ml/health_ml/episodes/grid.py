"""Fixed-grid view over raw observed glucose. No interpolation or value repair."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from health_ml.episodes.config import EpisodeConfig
from health_ml.episodes.contracts import GlucoseHistoryRow
from health_ml.schemas.canonical import GlucoseRecord, require_aware_utc


@dataclass(frozen=True)
class GridSlot:
    grid_index: int
    grid_timestamp_utc: datetime
    glucose_mg_dl: float | None
    observed_mask: bool
    source_timestamp_utc: datetime | None
    source: str | None

    def to_history_row(self, episode_id: str) -> GlucoseHistoryRow:
        return GlucoseHistoryRow(
            episode_id=episode_id,
            grid_index=self.grid_index,
            grid_timestamp_utc=self.grid_timestamp_utc,
            glucose_mg_dl=self.glucose_mg_dl,
            observed_mask=self.observed_mask,
            source_timestamp_utc=self.source_timestamp_utc,
            source=self.source,
        )


@dataclass(frozen=True)
class GridMapping:
    slots: tuple[GridSlot, ...]
    ambiguous: bool

    @property
    def missing_count(self) -> int:
        return sum(1 for slot in self.slots if not slot.observed_mask)


def grid_timestamps(anchor: datetime, config: EpisodeConfig) -> tuple[datetime, ...]:
    """Inclusive elapsed-UTC grid from history_start to anchor."""
    anchor_utc = require_aware_utc(anchor, field_name="anchor")
    history_start = anchor_utc - timedelta(minutes=config.history_minutes)
    cadence = timedelta(minutes=config.grid_cadence_minutes)
    count = config.grid_position_count
    stamps = tuple(history_start + cadence * index for index in range(count))
    if stamps[-1] != anchor_utc:
        raise ValueError("grid end must equal the anchor timestamp")
    return stamps


def map_history_grid(
    records: Sequence[GlucoseRecord],
    anchor: datetime,
    config: EpisodeConfig,
    *,
    timestamps: Sequence[datetime] | None = None,
) -> GridMapping:
    """Map raw observations onto the fixed grid without choosing or averaging.

    Non-final slots use half-open bands `[grid_time - tol, grid_time + tol)` so a
    point on a shared boundary maps to exactly one index. The last slot is closed
    at the anchor: `[anchor - tol, anchor]`. Multiple records in one slot make
    the whole episode ambiguous. Missing slots are retained as null/false/null.
    """
    anchor_utc = require_aware_utc(anchor, field_name="anchor")
    stamps = grid_timestamps(anchor_utc, config)
    tolerance = timedelta(minutes=config.history_start_tolerance_minutes)
    stamp_index = timestamps if timestamps is not None else [row.timestamp for row in records]
    slots: list[GridSlot] = []
    ambiguous = False
    last_index = len(stamps) - 1
    for index, grid_time in enumerate(stamps):
        band_start = grid_time - tolerance
        if index == last_index:
            band_end = min(grid_time + tolerance, anchor_utc)
            left = bisect_left(stamp_index, band_start)
            right = bisect_right(stamp_index, band_end)
        else:
            band_end = grid_time + tolerance
            left = bisect_left(stamp_index, band_start)
            right = bisect_left(stamp_index, band_end)
        matches = list(records[left:right])
        if len(matches) > 1:
            ambiguous = True
            slots.append(
                GridSlot(
                    grid_index=index,
                    grid_timestamp_utc=grid_time,
                    glucose_mg_dl=None,
                    observed_mask=False,
                    source_timestamp_utc=None,
                    source=None,
                )
            )
            continue
        if len(matches) == 1:
            observed = matches[0]
            slots.append(
                GridSlot(
                    grid_index=index,
                    grid_timestamp_utc=grid_time,
                    glucose_mg_dl=observed.glucose_mg_dl,
                    observed_mask=True,
                    source_timestamp_utc=observed.timestamp,
                    source=observed.source,
                )
            )
            continue
        slots.append(
            GridSlot(
                grid_index=index,
                grid_timestamp_utc=grid_time,
                glucose_mg_dl=None,
                observed_mask=False,
                source_timestamp_utc=None,
                source=None,
            )
        )
    return GridMapping(slots=tuple(slots), ambiguous=ambiguous)
