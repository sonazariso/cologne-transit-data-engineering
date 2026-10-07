"""Pure UTC bucket and due-target scheduling functions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from .domain import HEARTBEAT_SECONDS, LogicalBucket, Plan, Target
from .legacy7_schedule import legacy7_sampling_slot_for_bucket


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("collector wall-clock values must be timezone-aware UTC datetimes")
    return value.astimezone(timezone.utc)


def logical_due_bucket(value: datetime) -> int:
    """Return the deterministic UTC five-minute epoch bucket for ``value``."""

    utc_value = ensure_utc(value)
    return int(utc_value.timestamp() // HEARTBEAT_SECONDS)


def bucket_for(value: datetime) -> LogicalBucket:
    index = logical_due_bucket(value)
    start = datetime.fromtimestamp(index * HEARTBEAT_SECONDS, tz=timezone.utc)
    return LogicalBucket(index=index, start_utc=start, end_utc=start + timedelta(seconds=HEARTBEAT_SECONDS))


def may_execute_bucket(current_bucket: int, candidate_bucket: int) -> bool:
    """Only the current bucket is executable; missed buckets are never replayed."""

    return int(current_bucket) == int(candidate_bucket)


def target_is_due(target: Target, bucket_index: int) -> bool:
    """Pure due rule using the finalized manifest phase, never mutable rank/order."""

    if target.selected_sampling_slot is not None:
        # The live SQL selector has already chosen this one target for the
        # current bucket.  Keeping the marker explicit prevents the generic
        # Tiered50 phase planner from re-expanding the selected heartbeat.
        return True
    if target.legacy_sampling_slots:
        return legacy7_sampling_slot_for_bucket(bucket_index) in target.legacy_sampling_slots
    return int(bucket_index) % target.phase_count == target.phase_index


def due_targets(targets: Iterable[Target], bucket_index: int) -> tuple[Target, ...]:
    return tuple(
        sorted(
            (target for target in targets if target_is_due(target, bucket_index)),
            key=lambda target: target.source_parent_station_id,
        )
    )


def plan_for(targets: Sequence[Target], at: datetime) -> Plan:
    bucket = bucket_for(at)
    return Plan(
        bucket=bucket,
        due_targets=due_targets(targets, bucket.index),
        distribution_index=bucket.index % 6,
    )


def super_cycle_counts(targets: Sequence[Target], start_bucket_index: int = 0) -> tuple[int, ...]:
    return tuple(len(due_targets(targets, start_bucket_index + offset)) for offset in range(6))


def schedule_reconciliation(targets: Sequence[Target]) -> dict[str, object]:
    counts = super_cycle_counts(targets)
    return {
        "super_cycle_counts": counts,
        "super_cycle_total": sum(counts),
        "requests_per_hour": sum(counts) * 2,
        "expected_super_cycle_counts": (19, 18, 18, 18, 19, 18),
        "expected_super_cycle_total": 110,
        "expected_requests_per_hour": 220,
    }
