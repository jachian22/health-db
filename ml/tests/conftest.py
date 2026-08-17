"""ML package tests — no Postgres, Railway, or real credentials."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

os.environ["HEALTH_API_URL"] = "http://query-api.test"
os.environ["HEALTH_API_READ_KEY"] = "test-read-key"
os.environ.setdefault("LOG_LEVEL", "WARNING")

from health_ml.clients.health_api import HealthAPIClient, to_iso8601  # noqa: E402
from health_ml.config import Settings  # noqa: E402
from health_ml.datasets.manifest import (  # noqa: E402
    ArtifactRef,
    read_manifest,
    sha256_file,
    write_manifest,
)
from health_ml.datasets.snapshot import build_snapshot  # noqa: E402
from health_ml.schemas.canonical import (  # noqa: E402
    GlucoseRecord,
    MealRecord,
    SleepInterval,
    WeightRecord,
    WorkoutRecord,
)

TEST_READ_KEY = "test-read-key"
START = datetime(2026, 8, 1, tzinfo=UTC)
END = datetime(2026, 8, 16, tzinfo=UTC)


def make_settings(**overrides: Any) -> Settings:
    payload = {
        "health_api_url": "http://query-api.test",
        "health_api_read_key": TEST_READ_KEY,
    }
    payload.update(overrides)
    return Settings(_env_file=None, **payload)


def iso(value: datetime) -> str:
    return to_iso8601(value)


def paged_body(
    *,
    items: list[dict[str, Any]],
    start: datetime = START,
    end: datetime = END,
    next_cursor: str | None = None,
    truncated: bool = False,
) -> dict[str, Any]:
    return {
        "request_id": "req-test",
        "start": iso(start),
        "end": iso(end),
        "timezone": "America/New_York",
        "record_count": len(items),
        "truncated": truncated,
        "next_cursor": next_cursor,
        "items": items,
    }


def glucose_series_body(
    points: list[dict[str, Any]],
    *,
    start: datetime = START,
    end: datetime = END,
    truncated: bool = False,
) -> dict[str, Any]:
    return {
        "request_id": "req-test",
        "start": iso(start),
        "end": iso(end),
        "timezone": "America/New_York",
        "resolution": "raw",
        "source_record_count": len(points),
        "returned_point_count": len(points),
        "truncated": truncated,
        "points": points,
    }


def client_for(handler, *, max_retries: int = 3) -> HealthAPIClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(
        transport=transport,
        base_url="http://query-api.test",
        headers={"Accept": "application/json"},
    )
    return HealthAPIClient(
        make_settings(),
        http_client=http,
        sleeper=lambda _delay: None,
        max_retries=max_retries,
    )


def sample_glucose(*stamps: datetime) -> list[GlucoseRecord]:
    return [
        GlucoseRecord(timestamp=stamp, glucose_mg_dl=100.0 + index, trend=None, source=None)
        for index, stamp in enumerate(stamps)
    ]


@dataclass
class FakeHealthClient:
    glucose: list[GlucoseRecord] = field(default_factory=list)
    meals: list[MealRecord] = field(default_factory=list)
    workouts: list[WorkoutRecord] = field(default_factory=list)
    sleep: list[SleepInterval] = field(default_factory=list)
    weight: list[WeightRecord] = field(default_factory=list)
    api_base_url: str | None = None

    def get_glucose(self, start: datetime, end: datetime) -> list[GlucoseRecord]:
        return [row for row in self.glucose if start <= row.timestamp < end]

    def get_meals(self, start: datetime, end: datetime) -> list[MealRecord]:
        return [row for row in self.meals if start <= row.timestamp < end]

    def get_workouts(self, start: datetime, end: datetime) -> list[WorkoutRecord]:
        return [row for row in self.workouts if row.start < end and row.end > start]

    def get_sleep(self, start: datetime, end: datetime) -> list[SleepInterval]:
        return [row for row in self.sleep if row.start < end and row.end > start]

    def get_weight(self, start: datetime, end: datetime) -> list[WeightRecord]:
        return [row for row in self.weight if start <= row.timestamp < end]

    def close(self) -> None:
        return None


@pytest.fixture
def populated_client() -> FakeHealthClient:
    t0 = datetime(2026, 8, 5, 16, 0, tzinfo=UTC)
    return FakeHealthClient(
        glucose=sample_glucose(
            t0,
            t0 + timedelta(minutes=5),
            t0 + timedelta(minutes=10),
            t0 + timedelta(minutes=20),
        ),
        meals=[
            MealRecord(
                meal_id="meal-1",
                timestamp=datetime(2026, 8, 5, 19, 42, tzinfo=UTC),
                foods=["rice", "chicken"],
                source="manual",
            )
        ],
        workouts=[
            WorkoutRecord(
                workout_id="workout-1",
                start=datetime(2026, 8, 6, 11, 0, tzinfo=UTC),
                end=datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
                sport="running",
                distance_meters=5000.0,
                active_energy=None,
                average_hr=None,
                max_hr=None,
                source="apple_health",
            )
        ],
        sleep=[
            SleepInterval(
                sleep_id="sleep-1",
                start=datetime(2026, 8, 5, 3, 0, tzinfo=UTC),
                end=datetime(2026, 8, 5, 10, 0, tzinfo=UTC),
                stage="asleep",
                source="apple_health",
            )
        ],
        weight=[
            WeightRecord(
                weight_id="weight-1",
                timestamp=datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
                weight_kg=82.5,
                source="apple_health",
            )
        ],
    )


def write_snapshot(
    tmp_path,
    client: FakeHealthClient | None = None,
    *,
    start: datetime = START,
    end: datetime = END,
    timezone: str = "America/New_York",
):
    output = Path(tmp_path) / "snapshots"
    result = build_snapshot(
        start,
        end,
        output,
        client=client if client is not None else FakeHealthClient(),
        timezone=timezone,
    )
    return result


def regular_glucose(
    start: datetime,
    *,
    count: int,
    step_minutes: int = 5,
    value: float = 100.0,
) -> list[GlucoseRecord]:
    return [
        GlucoseRecord(
            timestamp=start + timedelta(minutes=step_minutes * index),
            glucose_mg_dl=value,
        )
        for index in range(count)
    ]


def replace_category_parquet(snapshot_dir: Path, name: str, table) -> None:
    import pyarrow.parquet as pq

    path = snapshot_dir / f"{name}.parquet"
    pq.write_table(table, path, compression="zstd")
    digest = sha256_file(path)
    manifest = read_manifest(snapshot_dir / "manifest.json")
    artifacts = dict(manifest.artifacts)
    artifacts[name] = ArtifactRef(file=f"{name}.parquet", sha256=digest, rows=table.num_rows)
    checksums = dict(manifest.checksums)
    checksums[name] = digest
    row_counts = dict(manifest.row_counts)
    row_counts[name] = table.num_rows
    updated = manifest.model_copy(
        update={
            "artifacts": artifacts,
            "checksums": checksums,
            "row_counts": row_counts,
        }
    )
    write_manifest(snapshot_dir / "manifest.json", updated)


def write_snapshot_and_diagnostics(
    tmp_path,
    client: FakeHealthClient | None = None,
    *,
    start: datetime = START,
    end: datetime = END,
    timezone: str = "America/New_York",
    **diagnostics_kwargs,
):
    from health_ml.diagnostics.runner import run_diagnostics

    snapshot = write_snapshot(
        tmp_path,
        client,
        start=start,
        end=end,
        timezone=timezone,
    )
    diagnostics = run_diagnostics(
        snapshot.output_dir,
        Path(tmp_path) / "diagnostics",
        **diagnostics_kwargs,
    )
    return snapshot, diagnostics


