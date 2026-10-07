#!/usr/bin/env python3
"""Read-only SQL confirmation that the legacy Windows writer has stopped."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    connect_mssql,
)
from cologne_transit_collector.runtime_configuration import SecretRequired  # noqa: E402


EXPECTED_DATABASE = "CologneTransitIntelligence"
DEFAULT_OUTPUT = Path.home() / "Library" / "Application Support" / "CologneTransitIntelligence" / "windows-retirement-confirmation.json"
OPERATOR_TASK_STATE = "DISABLED"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _columns(cursor: Any, schema: str, table: str) -> set[str]:
    cursor.execute(
        """
        SELECT c.name
        FROM sys.columns AS c
        WHERE c.object_id = OBJECT_ID(%s)
        """,
        (f"{schema}.{table}",),
    )
    rows = cursor.fetchall()
    return {str(row.get("name") if isinstance(row, dict) else row[0]) for row in rows}


def _row_dict(row: Any, names: Iterable[str]) -> dict[str, Any]:
    if isinstance(row, dict):
        return {name: row.get(name) for name in names}
    return {name: row[index] if row is not None and index < len(row) else None for index, name in enumerate(names)}


def _query_latest_windows_run(cursor: Any) -> dict[str, Any] | None:
    run_columns = _columns(cursor, "ctl", "MddCollectorRun")
    requested = [
        "CollectorRunId",
        "StartedAtUtc",
        "CompletedAtUtc",
        "SamplingTargetId",
        "SamplingMode",
        "SamplingSlot",
        "Status",
        "HttpStatus",
        "HttpAttempts",
        "ObservedAtUtc",
    ]
    selected = [name for name in requested if name in run_columns]
    if "CollectorRunId" not in selected or "StartedAtUtc" not in selected:
        raise RuntimeError("legacy collector run schema lacks required identity/timestamp columns")
    projection = ", ".join(f"r.[{name}] AS [{name}]" for name in selected)
    target_join = "LEFT JOIN ctl.MddRealtimeSamplingTarget AS t ON t.SamplingTargetId = r.SamplingTargetId" if "SamplingTargetId" in selected else ""
    stop_projection = ", t.StopPointRef AS StopPointRef" if target_join else ", CAST(NULL AS NVARCHAR(400)) AS StopPointRef"
    cursor.execute(
        f"""
        SELECT TOP (1) {projection}{stop_projection}
        FROM ctl.MddCollectorRun AS r
        {target_join}
        ORDER BY r.[StartedAtUtc] DESC, r.[CollectorRunId] DESC
        """
    )
    row = cursor.fetchone()
    if row is None:
        return None
    result = _row_dict(row, selected + ["StopPointRef"])
    return {key: _iso(value) if key.endswith("AtUtc") or key == "ObservedAtUtc" else value for key, value in result.items()}


def _query_latest_observation(cursor: Any) -> dict[str, Any] | None:
    observation_columns = _columns(cursor, "stg", "MddRealtimeStopObservation")
    requested = ["ObservedAtUtc", "SamplingTargetId", "StopPointRef", "HttpStatus", "CollectorRunId"]
    selected = [name for name in requested if name in observation_columns]
    if "ObservedAtUtc" not in selected:
        raise RuntimeError("realtime observation schema lacks ObservedAtUtc")
    projection = ", ".join(f"o.[{name}] AS [{name}]" for name in selected)
    cursor.execute(
        f"""
        SELECT TOP (1) {projection}
        FROM stg.MddRealtimeStopObservation AS o
        ORDER BY o.[ObservedAtUtc] DESC
        """
    )
    row = cursor.fetchone()
    if row is None:
        return None
    result = _row_dict(row, selected)
    return {key: _iso(value) if key == "ObservedAtUtc" else value for key, value in result.items()}


def capture(settings: MssqlConnectionSettings, *, boundary: datetime) -> dict[str, Any]:
    connection = connect_mssql(settings)
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT DB_NAME() AS database_name")
        database_row = cursor.fetchone()
        database_name = database_row.get("database_name") if isinstance(database_row, dict) else database_row[0]
        if database_name != EXPECTED_DATABASE:
            raise RuntimeError(f"connected SQL Server database must be {EXPECTED_DATABASE}")

        latest_run = _query_latest_windows_run(cursor)
        latest_observation = _query_latest_observation(cursor)
        cursor.execute(
            """
            SELECT COUNT_BIG(*) AS run_count
            FROM ctl.MddCollectorRun
            WHERE StartedAtUtc > %s OR CompletedAtUtc > %s
            """,
            (boundary, boundary),
        )
        run_row = cursor.fetchone()
        newer_runs = int(run_row.get("run_count", 0) if isinstance(run_row, dict) else run_row[0] or 0)
        cursor.execute(
            """
            SELECT COUNT_BIG(*) AS observation_count
            FROM stg.MddRealtimeStopObservation
            WHERE ObservedAtUtc > %s
            """,
            (boundary,),
        )
        observation_row = cursor.fetchone()
        newer_observations = int(
            observation_row.get("observation_count", 0) if isinstance(observation_row, dict) else observation_row[0] or 0
        )
        return {
            "captured_at_utc": _iso(_utc_now()),
            "operator_task_name": r"\Cologne Transit Realtime Collector",
            "operator_task_state": OPERATOR_TASK_STATE,
            "operator_boundary_utc": _iso(boundary),
            "database": database_name,
            "latest_windows_collector_run": latest_run,
            "latest_windows_realtime_observation": latest_observation,
            "newer_windows_collector_runs_after_boundary": newer_runs,
            "newer_windows_realtime_observations_after_boundary": newer_observations,
            "windows_writer_state": "STILL_ACTIVE" if newer_runs or newer_observations else "NO_NEW_ACTIVITY",
        }
    finally:
        connection.close()


def observe(
    settings: MssqlConnectionSettings,
    *,
    boundary: datetime,
    boundaries: int,
    interval_seconds: int,
) -> dict[str, Any]:
    if boundaries < 1:
        raise ValueError("boundaries must be positive")
    observations: list[dict[str, Any]] = []
    for index in range(boundaries):
        observations.append(capture(settings, boundary=boundary))
        if index + 1 >= boundaries:
            break
        deadline = time.monotonic() + interval_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(30.0, remaining))
    final = dict(observations[-1])
    final["heartbeat_observations"] = observations
    final["observed_former_five_minute_boundaries"] = boundaries
    final["status"] = "PASS" if final["windows_writer_state"] == "NO_NEW_ACTIVITY" else "WINDOWS_WRITER_STILL_ACTIVE"
    return final


def _parse_utc(value: str | None) -> datetime:
    if value is None:
        return _utc_now()
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("boundary must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _write_output(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    path.chmod(0o600)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operator-boundary-utc", help="operator disable/evidence boundary; defaults to this invocation")
    parser.add_argument("--boundaries", type=int, default=1, help="number of read-only captures; use 2 for two heartbeat boundaries")
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        boundary = _parse_utc(args.operator_boundary_utc)
        if args.interval_seconds <= 0:
            raise ValueError("interval must be positive")
        settings = MssqlConnectionSettings.from_environment()
        result = observe(
            settings,
            boundary=boundary,
            boundaries=args.boundaries,
            interval_seconds=args.interval_seconds,
        )
        _write_output(args.output.expanduser().resolve(), result)
    except SecretRequired as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    if result.get("status") == "WINDOWS_WRITER_STILL_ACTIVE":
        print("WINDOWS_WRITER_STILL_ACTIVE", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
