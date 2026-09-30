#!/usr/bin/env python3
"""Stage 13 Tiered50 collector-readiness evidence and bounded diagnostics.

This module owns the Stage 13 design evidence only. It can read the exact
Stage 12 GTFS snapshot for offline schedule-density analysis and, when
explicitly requested, run a small sanitized MDD diagnostic. It never creates
production targets, a scheduler, a database object, or a production request
loop.

The live path deliberately keeps response bodies in memory only. The API key
is accepted only from the inherited ``MDD_API_KEY`` environment variable and
is never returned, written, logged, hashed, or included in evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import time
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPOSITORY_ROOT / "docs" / "stages" / "13-tiered-collector-readiness-evidence.json"

PANEL_VERSION = "TIERED50_PANEL_V1"
STAGE12_FEED_VERSION = "VERSION__20260930_0053"
STAGE12_ZIP_SHA256 = "288c26f23f91a2069078be82167cfa70eb5b2948e06cfeee9e23584910817147"
STAGE12_SOURCE_URL = "https://download.vrs.de/gtfs/google_transit_goR.zip"

ENDPOINT = "https://mdd.gorheinland.com/delfi"
REQUESTOR_REF = "CologneTransitIntelligence"
MDD_DIAGNOSTIC_POLICY = "STAGE13_TIERED_READINESS_DIAGNOSTIC_V1"
MDD_HTTP_ATTEMPT_HARD_MAX = 100
MDD_RESULT_LIMITS = (5, 10, 20, 30)
# This remains a technically evaluated option.  It is not a user-approved
# production setting until the Stage 13 decision gate is explicitly closed.
MDD_CONCURRENCY_CAP = 2
PRODUCTION_MAX_ATTEMPTS_CANDIDATE = 1
ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS = 24
PRIOR_STAGE13_DIAGNOSTIC_ATTEMPTS = 90
ADDITIONAL_RESULT_LIMIT_APPROVAL_REQUEST = (
    "The corrected NumberOfResults coverage diagnostic requires 24 additional MDD requests, "
    "increasing cumulative Stage 13 diagnostic attempts from 90 to 114. "
    "Do you approve these 24 additional requests?"
)
CORRECTED_RESULT_LIMIT_DIAGNOSTIC_SCOPE = "six deterministic representatives × N=5/N=10/N=20/N=30; sequential only"

MONTHLY_CEILING = 250_000
NORMAL_ENVELOPE_MIN = 207_500
NORMAL_ENVELOPE_MAX = 210_000
HEARTBEAT_MINUTES = 5
HEARTBEAT_SECONDS = HEARTBEAT_MINUTES * 60
LEGACY_EXECUTIONS = 1_599
LEGACY_HTTP_ATTEMPTS = 1_629
LEGACY_ADDITIONAL_RETRY_ATTEMPTS = 35
LEGACY_ADDITIONAL_ATTEMPT_SHARE = LEGACY_ADDITIONAL_RETRY_ATTEMPTS / LEGACY_HTTP_ATTEMPTS
LEGACY7_RESERVED_MONTHLY_REQUESTS = {30: 8_640, 31: 8_928}
DIAGNOSTIC_RESERVE_HTTP_ATTEMPTS = MDD_HTTP_ATTEMPT_HARD_MAX
FORWARD_WINDOWS_MINUTES = (5, 10, 15, 30)
# This remains a technically evaluated option.  It must not be serialized as
# an approved production decision before explicit user approval.
PRODUCTION_NUMBER_OF_RESULTS_CANDIDATE = 30

# Retained only as the historical comparison candidate. It is never used as
# an approved tier assignment.
TIERED50_CANDIDATE = (
    {"tier": "A", "station_count": 10, "interval_minutes": 5},
    {"tier": "B", "station_count": 20, "interval_minutes": 10},
    {"tier": "C", "station_count": 20, "interval_minutes": 30},
)

# The production design candidate is evaluated against measured density and
# quota evidence, but remains an option until the user decision gate is closed.
PRODUCTION_TIER_PLAN = (
    {"tier": "A", "station_count": 10, "interval_minutes": 5},
    {"tier": "B", "station_count": 20, "interval_minutes": 15},
    {"tier": "C", "station_count": 20, "interval_minutes": 30},
)


class Stage13Blocked(RuntimeError):
    """Raised when the exact Stage 12 source cannot support the analysis."""


def _round(value: float | int | None, digits: int = 6) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return round(value, digits)


def percentile(values: Sequence[float], quantile: float) -> float | None:
    """Return a linearly interpolated percentile without external packages."""

    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def value_statistics(values: Sequence[float | int]) -> dict[str, Any]:
    """Summarize a numeric planning population."""

    if not values:
        return {
            "count": 0,
            "min": None,
            "median": None,
            "p90": None,
            "p95": None,
            "p99": None,
            "max": None,
        }
    ordered = sorted(float(value) for value in values)
    return {
        "count": len(ordered),
        "min": _round(ordered[0], 3),
        "median": _round(percentile(ordered, 0.50), 3),
        "p90": _round(percentile(ordered, 0.90), 3),
        "p95": _round(percentile(ordered, 0.95), 3),
        "p99": _round(percentile(ordered, 0.99), 3),
        "max": _round(ordered[-1], 3),
    }


def duration_statistics(values: Sequence[float]) -> dict[str, Any]:
    """Summarize one homogeneous execution-mode population in milliseconds."""

    statistics = value_statistics(values)
    return {
        "count": statistics["count"],
        "min_ms": statistics["min"],
        "p50_ms": statistics["median"],
        "p90_ms": statistics["p90"],
        "p95_ms": statistics["p95"],
        "p99_ms": statistics["p99"],
        "max_ms": statistics["max"],
    }


def candidate_cadence(
    tiers: Sequence[Mapping[str, int]] = TIERED50_CANDIDATE,
    *,
    status: str = "CALCULATED_ONLY_NOT_APPROVED",
) -> dict[str, Any]:
    """Calculate logical request load for a tier-count/cadence candidate."""

    tier_rows: list[dict[str, Any]] = []
    requests_per_hour = 0
    station_count = 0
    for tier in tiers:
        count = int(tier["station_count"])
        interval = int(tier["interval_minutes"])
        if count < 0 or interval <= 0 or 60 % interval != 0:
            raise ValueError("tier station counts and hourly intervals must be valid divisors")
        per_hour = count * (60 // interval)
        row = {
            "tier": str(tier["tier"]),
            "station_count": count,
            "interval_minutes": interval,
            "logical_requests_per_hour": per_hour,
            "logical_requests_per_day": per_hour * 24,
        }
        tier_rows.append(row)
        requests_per_hour += per_hour
        station_count += count

    requests_per_day = requests_per_hour * 24
    return {
        "candidate_status": status,
        "station_count": station_count,
        "tiers": tier_rows,
        "logical_requests_per_hour": requests_per_hour,
        "logical_requests_per_day": requests_per_day,
        "monthly_logical_requests": {
            "30_day": requests_per_day * 30,
            "31_day": requests_per_day * 31,
        },
        "reconciliation": {
            "requests_per_30_minutes": requests_per_hour // 2,
            "requests_per_hour_from_30_minute_super_cycle": (requests_per_hour // 2) * 2,
        },
    }


def quota_projection(
    logical_requests_per_hour: int,
    days: int,
    additional_attempt_rate: float = 0.0,
) -> dict[str, Any]:
    """Project base and retry-sensitive request counts."""

    base = logical_requests_per_hour * 24 * days
    projected = base * (1.0 + additional_attempt_rate)
    return {
        "days": days,
        "base_logical_requests": base,
        "additional_attempt_rate": _round(additional_attempt_rate, 9),
        "projected_http_requests": _round(projected, 6),
        "percentage_of_250000_ceiling": _round(projected / MONTHLY_CEILING * 100, 6),
        "percentage_of_normal_envelope_min": _round(projected / NORMAL_ENVELOPE_MIN * 100, 6),
        "percentage_of_normal_envelope_max": _round(projected / NORMAL_ENVELOPE_MAX * 100, 6),
        "headroom_to_250000_ceiling": _round(MONTHLY_CEILING - projected, 6),
        "headroom_to_normal_envelope_min": _round(NORMAL_ENVELOPE_MIN - projected, 6),
        "headroom_to_normal_envelope_max": _round(NORMAL_ENVELOPE_MAX - projected, 6),
        "base_within_normal_envelope": NORMAL_ENVELOPE_MIN <= base <= NORMAL_ENVELOPE_MAX,
        "projected_within_normal_envelope": NORMAL_ENVELOPE_MIN <= projected <= NORMAL_ENVELOPE_MAX,
    }


def _conservative_quota_total(
    projection: Mapping[str, Any],
    *,
    legacy_reserved_requests: int,
    diagnostic_reserved_requests: int,
) -> dict[str, Any]:
    projected_http = float(projection["projected_http_requests"])
    total = math.ceil(projected_http) + legacy_reserved_requests + diagnostic_reserved_requests
    return {
        "projected_http_requests_rounded_up": math.ceil(projected_http),
        "legacy_reserved_requests": legacy_reserved_requests,
        "diagnostic_reserved_requests": diagnostic_reserved_requests,
        "conservative_total_http_requests": total,
        "headroom_to_250000_ceiling": MONTHLY_CEILING - total,
        "headroom_to_normal_envelope_min": NORMAL_ENVELOPE_MIN - total,
        "headroom_to_normal_envelope_max": NORMAL_ENVELOPE_MAX - total,
        "within_monthly_ceiling": total <= MONTHLY_CEILING,
        "within_normal_envelope_max": total <= NORMAL_ENVELOPE_MAX,
    }


def quota_projection_set(
    logical_requests_per_hour: int,
    *,
    legacy_reserved_requests: Mapping[int, int] | None = None,
    diagnostic_reserved_requests: int = 0,
) -> dict[str, Any]:
    scenarios = [
        {
            "label": "normal_zero_retry_policy",
            "kind": "technically_evaluated_policy",
            "additional_attempt_rate": 0.0,
        },
        {
            "label": "legacy_observed_additional_attempt_share",
            "kind": "observed_legacy_evidence_applied_as_sensitivity_only",
            "additional_attempt_rate": LEGACY_ADDITIONAL_ATTEMPT_SHARE,
            "not_an_observed_tiered50_retry_rate": True,
        },
        {
            "label": "five_percent_stress_sensitivity",
            "kind": "calculation_scenario",
            "additional_attempt_rate": 0.05,
        },
        {
            "label": "ten_percent_stress_sensitivity",
            "kind": "calculation_scenario",
            "additional_attempt_rate": 0.10,
        },
    ]
    reserved = dict(legacy_reserved_requests or {30: 0, 31: 0})
    projections: list[dict[str, Any]] = []
    for scenario in scenarios:
        row = {
            **scenario,
            "30_day": quota_projection(
                logical_requests_per_hour,
                30,
                float(scenario["additional_attempt_rate"]),
            ),
            "31_day": quota_projection(
                logical_requests_per_hour,
                31,
                float(scenario["additional_attempt_rate"]),
            ),
        }
        row["30_day"]["conservative_reserve_reconciliation"] = _conservative_quota_total(
            row["30_day"],
            legacy_reserved_requests=int(reserved.get(30, 0)),
            diagnostic_reserved_requests=diagnostic_reserved_requests,
        )
        row["31_day"]["conservative_reserve_reconciliation"] = _conservative_quota_total(
            row["31_day"],
            legacy_reserved_requests=int(reserved.get(31, 0)),
            diagnostic_reserved_requests=diagnostic_reserved_requests,
        )
        projections.append(row)
    return {
        "monthly_ceiling": MONTHLY_CEILING,
        "normal_operational_planning_envelope": {
            "minimum": NORMAL_ENVELOPE_MIN,
            "maximum": NORMAL_ENVELOPE_MAX,
            "percentage_of_ceiling": {"minimum": 83.0, "maximum": 84.0},
        },
        "legacy_reserved_monthly_requests": {
            "30_day": int(reserved.get(30, 0)),
            "31_day": int(reserved.get(31, 0)),
        },
        "diagnostic_reserved_http_attempts": diagnostic_reserved_requests,
        "projections": projections,
        "accounting_rules": [
            "Each retry/additional attempt is one additional HTTP request.",
            "NumberOfResults changes response cardinality, not HTTP request count.",
            "Duplicate claimed target/buckets must not issue another HTTP request.",
            "No catch-up burst may spend quota on missed historical buckets.",
            "The normal Stage 13 retry policy option uses MaxAttempts=1 and zero automatic retries; user approval remains required.",
            "The full documented Legacy7 monthly cadence is reserved conservatively because current shared-account usage is unavailable.",
            "Repository diagnostics are inactive after closeout; the Stage 13 diagnostic hard budget is reserved for reconciliation only.",
        ],
    }


def _phase_slot_count(interval_minutes: int) -> int:
    if interval_minutes <= 0 or interval_minutes % HEARTBEAT_MINUTES != 0:
        raise ValueError("cadence intervals must be positive multiples of the heartbeat")
    return interval_minutes // HEARTBEAT_MINUTES


def _balanced_counts(total: int, slots: int) -> list[int]:
    base, remainder = divmod(total, slots)
    return [base + (1 if index < remainder else 0) for index in range(slots)]


def heartbeat_phase_distribution(
    tiers: Sequence[Mapping[str, int]] = TIERED50_CANDIDATE,
) -> dict[str, Any]:
    """Reconcile deterministic phase offsets over the cadence super-cycle."""

    tier_phase_counts: dict[str, list[int]] = {}
    super_cycle_steps = 1
    for tier in tiers:
        slots = _phase_slot_count(int(tier["interval_minutes"]))
        super_cycle_steps = math.lcm(super_cycle_steps, slots)
        tier_phase_counts[str(tier["tier"])] = _balanced_counts(int(tier["station_count"]), slots)

    total_due = []
    per_tier_due: dict[str, list[int]] = {str(tier["tier"]): [] for tier in tiers}
    for heartbeat in range(super_cycle_steps):
        for tier in tiers:
            tier_name = str(tier["tier"])
            phase_counts = tier_phase_counts[tier_name]
            per_tier_due[tier_name].append(phase_counts[heartbeat % len(phase_counts)])
        total_due.append(sum(per_tier_due[str(tier["tier"])][-1] for tier in tiers))

    tier_rows = []
    for tier in tiers:
        tier_name = str(tier["tier"])
        tier_rows.append(
            {
                "tier": tier_name,
                "interval_minutes": int(tier["interval_minutes"]),
                "phase_count": len(tier_phase_counts[tier_name]),
                "phase_target_counts": tier_phase_counts[tier_name],
                "due_targets_by_heartbeat": per_tier_due[tier_name],
            }
        )
    return {
        "heartbeat_interval_minutes": HEARTBEAT_MINUTES,
        "super_cycle_minutes": super_cycle_steps * HEARTBEAT_MINUTES,
        "super_cycle_heartbeats": super_cycle_steps,
        "tier_phase_distribution": tier_rows,
        "total_due_targets_by_heartbeat": total_due,
        "max_due_targets_in_one_heartbeat": max(total_due) if total_due else 0,
        "super_cycle_total_logical_requests": sum(total_due),
        "hour_total_logical_requests": sum(total_due) * (60 // (super_cycle_steps * HEARTBEAT_MINUTES)),
        "phase_distribution_reconciles": True,
        "phase_assignment_contract": {
            "assignment_key": "immutable SamplingTargetId",
            "assignment_hash": "SHA-256(panel version + tier + immutable SamplingTargetId)",
            "phase_index_is_persisted": True,
            "independent_of_mutable_rank_order": True,
            "balanced_phase_rule": "sort by stable identity hash and fill deterministic phase capacities in order",
            "materialization_status": "DESIGN_ONLY_STAGE14_MATERIALIZATION_REQUIRED",
        },
    }


def deterministic_phase_index(sampling_target_id: str, phase_count: int) -> int:
    """Return a stable candidate phase index from an immutable target ID."""

    if not sampling_target_id:
        raise ValueError("sampling_target_id must not be empty")
    if phase_count <= 0:
        raise ValueError("phase_count must be positive")
    digest = hashlib.sha256(sampling_target_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % phase_count


def phase_assignments_for_tier_rows(
    tier_rows: Sequence[Mapping[str, Any]],
    *,
    panel_version: str = PANEL_VERSION,
    tiers: Sequence[Mapping[str, int]] = PRODUCTION_TIER_PLAN,
) -> list[dict[str, Any]]:
    """Assign stable, balanced phase offsets without using SelectionRank."""

    assignments: list[dict[str, Any]] = []
    rows_by_tier: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in tier_rows:
        tier_name = str(row.get("production_tier") or row.get("tier") or "")
        if tier_name:
            rows_by_tier[tier_name].append(row)

    cadence_by_tier = {str(row["tier"]): row for row in tiers}
    for tier_name in sorted(rows_by_tier):
        cadence = cadence_by_tier[tier_name]
        interval = int(cadence["interval_minutes"])
        phase_count = _phase_slot_count(interval)
        capacities = _balanced_counts(len(rows_by_tier[tier_name]), phase_count)
        ordered = sorted(
            rows_by_tier[tier_name],
            key=lambda row: hashlib.sha256(
                f"{panel_version}|{tier_name}|{row['source_parent_station_id']}".encode("utf-8")
            ).hexdigest(),
        )
        cursor = 0
        for phase_index, capacity in enumerate(capacities):
            for row in ordered[cursor : cursor + capacity]:
                assignments.append(
                    {
                        "sampling_target_identity": row["source_parent_station_id"],
                        "station": row["station_name"],
                        "panel_version": panel_version,
                        "production_tier": tier_name,
                        "interval_minutes": interval,
                        "phase_index": phase_index,
                        "phase_count": phase_count,
                        "assignment_basis": "SHA-256(panel version + tier + immutable SamplingTargetId)",
                    }
                )
            cursor += capacity
    return sorted(assignments, key=lambda row: (row["production_tier"], row["sampling_target_identity"]))


def logical_due_bucket(epoch_seconds: float) -> int:
    """Use UTC epoch buckets so DST cannot duplicate or skip a bucket."""

    return int(epoch_seconds // HEARTBEAT_SECONDS)


def claim_identity(sampling_target_id: str, logical_bucket: int) -> tuple[str, int]:
    if not sampling_target_id:
        raise ValueError("sampling_target_id must not be empty")
    return sampling_target_id, int(logical_bucket)


def may_execute_bucket(current_bucket: int, candidate_bucket: int) -> bool:
    """Disallow replay/catch-up of a missed historical logical bucket."""

    return int(current_bucket) == int(candidate_bucket)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _duration_population(
    *,
    population_id: str,
    execution_mode: str,
    source_paths: Sequence[str],
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    rows = list(rows)
    durations = [float(row["elapsed_ms"]) for row in rows if row.get("elapsed_ms") is not None]
    statuses = Counter(str(row.get("http_status")) for row in rows if row.get("http_status") is not None)
    failures = [row for row in rows if row.get("request_failure") or row.get("transport_failure")]
    parser_failures = [row for row in rows if str(row.get("parser_status", "")).startswith("FAILED")]
    return {
        "population_id": population_id,
        "execution_mode": execution_mode,
        "source_paths": list(source_paths),
        "duration_unit": "milliseconds",
        "duration_statistics": duration_statistics(durations),
        "http_status_distribution": dict(sorted(statuses.items())),
        "transport_or_request_failure_count": len(failures),
        "parser_failure_count": len(parser_failures),
        "timeout_count": sum("TIMEOUT" in str(row.get("request_failure", "")).upper() for row in failures),
        "retry_count": 0,
        "population_is_combined_with_other_modes": False,
    }


def _load_duration_evidence(root: Path) -> list[dict[str, Any]]:
    stage3a_rows = _load_jsonl(root / "stage03a-mdd-discovery.jsonl")
    stage3a_response_rows = [
        row for row in stage3a_rows if row.get("record_type") == "mdd_discovery_station_summary"
    ]
    lifecycle_rows: list[dict[str, Any]] = []
    lifecycle_paths: list[str] = []
    for path in sorted(root.glob("*-lifecycle-*.log")):
        lifecycle_paths.append(path.name)
        lifecycle_rows.extend(_load_jsonl(path))
    lifecycle_response_rows = [row for row in lifecycle_rows if row.get("elapsed_ms") is not None]
    summary_path = root / "docs" / "stages" / "09-source-backed-matching-summary.json"
    stage9_summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    stage9_rows = [row for row in stage9_summary.get("mdd_request_contexts", []) if row.get("elapsed_ms") is not None]
    populations = [
        _duration_population(
            population_id="stage_03a_mdd_discovery",
            execution_mode="one sequential MDD request per diagnostic station",
            source_paths=["stage03a-mdd-discovery.jsonl"],
            rows=stage3a_response_rows,
        ),
        _duration_population(
            population_id="stage_08_near_event_lifecycle",
            execution_mode="five separate sequential lifecycle runs; seven observations each",
            source_paths=lifecycle_paths,
            rows=lifecycle_response_rows,
        ),
        _duration_population(
            population_id="stage_09_source_backed_acceptance",
            execution_mode="three sequential source-backed MDD requests",
            source_paths=["docs/stages/09-source-backed-matching-summary.json"],
            rows=stage9_rows,
        ),
    ]
    populations.append(
        {
            "population_id": "stage_08_one_hour_pilot_run_level",
            "execution_mode": "one sequential Stage 8 pilot with fixed 3-second diagnostic slots",
            "source_paths": ["docs/stages/08-small-realtime-pilot-blocked.md", "docs/PROJECT-STATUS.md"],
            "duration_unit": "seconds for whole run; no per-request durations retained",
            "duration_statistics": {"count": 343, "min_ms": None, "p50_ms": None, "p90_ms": None, "p95_ms": None, "p99_ms": None, "max_ms": None},
            "actual_elapsed_seconds": 3606.801,
            "attempted_http_requests": 343,
            "scheduled_logical_slots": 1200,
            "skipped_overlap_slots": 857,
            "attempted_fraction_of_slots": _round(343 / 1200, 6),
            "skipped_fraction_of_slots": _round(857 / 1200, 6),
            "observed_average_wall_seconds_per_attempt": _round(3606.801 / 343, 6),
            "http_status_distribution": {"200": 343},
            "transport_or_request_failure_count": 0,
            "parser_failure_count": 0,
            "persistence_failure_count": 0,
            "timeout_count": 0,
            "retry_count": 0,
            "population_is_combined_with_other_modes": False,
            "interpretation": "Run-level evidence only; the 3-second diagnostic interval is not a production requirement.",
        }
    )
    return populations


def _historical_result_limit_evidence(root: Path) -> dict[str, Any]:
    stage3a_rows = _load_jsonl(root / "stage03a-mdd-discovery.jsonl")
    station_rows = [row for row in stage3a_rows if row.get("record_type") == "mdd_discovery_station_summary"]
    stage9_path = root / "docs" / "stages" / "09-source-backed-matching-summary.json"
    stage9 = json.loads(stage9_path.read_text(encoding="utf-8")) if stage9_path.exists() else {}
    stage9_contexts = stage9.get("mdd_request_contexts", [])
    return {
        "source_controlled_historical_samples": [
            {
                "source": "stage03a-mdd-discovery.jsonl",
                "execution_mode": "bounded ten-station MDD discovery",
                "station_scope": "ten diagnostic stations; not a Tiered50 panel",
                "number_of_results": 5,
                "request_count": len(station_rows),
                "returned_result_count_distribution": {
                    "count": len(station_rows),
                    "min": min((row.get("result_count", 0) for row in station_rows), default=None),
                    "max": max((row.get("result_count", 0) for row in station_rows), default=None),
                },
                "saturated_responses": sum(row.get("result_count") == 5 for row in station_rows),
                "coverage_interpretation": "all observed responses reached N=5; N=5 is not sufficient production evidence",
            },
            {
                "source": "docs/stages/08-small-realtime-pilot-blocked.md",
                "execution_mode": "one-hour Köln Hbf Stage 8 pilot",
                "station_scope": "one station only",
                "number_of_results": 20,
                "request_count": 343,
                "returned_result_count_total": 6860,
                "saturated_responses_inferred": 343,
                "coverage_interpretation": "bounded Hbf compatibility and N20 saturation only; not provider-wide coverage",
            },
            {
                "source": "docs/stages/09-source-backed-matching-summary.json",
                "execution_mode": "three-station source-backed acceptance probe",
                "station_scope": "Köln Hbf, Heumarkt, Neumarkt; not a Tiered50 panel",
                "number_of_results": 20,
                "request_count": len(stage9_contexts),
                "returned_result_count_distribution": {
                    "count": len(stage9_contexts),
                    "values": [row.get("bundle_stop_event_count") for row in stage9_contexts],
                },
                "saturated_responses": sum(row.get("bundle_stop_event_count") == 20 for row in stage9_contexts),
                "coverage_interpretation": "bounded three-station saturation only; no provider-maximum claim",
            },
        ],
        "application_validation_ranges_are_not_provider_evidence": True,
    }


def _credential_consumers() -> list[dict[str, Any]]:
    return [
        {
            "caller": "scripts/stage_08_mdd_realtime_pilot.py",
            "purpose": "bounded one-station Stage 8 pilot",
            "normal_cadence": "none; completed diagnostic",
            "retry_policy": "zero retries; 30-second timeout",
            "classification": "inactive bounded diagnostic",
            "standing_quota_reserve": 0,
        },
        {
            "caller": "scripts/stage_08_authorization_request_contract_diagnostic.py",
            "purpose": "manual Hbf N5/N20 request-breadth controls",
            "normal_cadence": "none; manual diagnostic",
            "retry_policy": "zero retries",
            "classification": "inactive bounded diagnostic",
            "standing_quota_reserve": 0,
        },
        {
            "caller": "scripts/stage_03a_disruption_evidence_probe.py",
            "purpose": "Stage 3A MDD discovery and Situation evidence",
            "normal_cadence": "none; manual diagnostic",
            "retry_policy": "zero retries; sequential",
            "classification": "inactive bounded diagnostic",
            "standing_quota_reserve": 0,
        },
        {
            "caller": "scripts/stage_09_source_backed_acceptance.py",
            "purpose": "bounded Stage 9 source-backed acceptance",
            "normal_cadence": "none; manual diagnostic",
            "retry_policy": "zero retries",
            "classification": "inactive bounded diagnostic",
            "standing_quota_reserve": 0,
        },
        {
            "caller": "scripts/mdd_recorded_time_lifecycle_probe.py",
            "purpose": "near-event RecordedAtTime lifecycle validation",
            "normal_cadence": "none; historical diagnostic",
            "retry_policy": "zero retries",
            "classification": "inactive bounded diagnostic",
            "standing_quota_reserve": 0,
        },
        {
            "caller": "scripts/stage_02a2_probe.py and scripts/stage_02a3_historical_actual_time_probe.py",
            "purpose": "historical source-contract diagnostics",
            "normal_cadence": "none; inactive",
            "retry_policy": "zero retries",
            "classification": "inactive diagnostic",
            "standing_quota_reserve": 0,
        },
        {
            "caller": "Legacy Collector / ctl.MddCollectorRun",
            "purpose": "external shared-credential production consumer",
            "normal_cadence": "current cadence unavailable in this checkout",
            "retry_policy": "current policy unavailable; historical audit records 35 additional attempts",
            "classification": "current usage unknown; conservative reserve applied",
            "observed_current_usage": "UNAVAILABLE_CURRENT_PRODUCTION_AUDIT_EVIDENCE",
            "conservative_reserved_requests": dict(LEGACY7_RESERVED_MONTHLY_REQUESTS),
            "historical_evidence": {
                "executions": LEGACY_EXECUTIONS,
                "http_attempts": LEGACY_HTTP_ATTEMPTS,
                "additional_retry_attempts": LEGACY_ADDITIONAL_RETRY_ATTEMPTS,
            },
        },
    ]


def _parse_gtfs_time(value: Any) -> int | None:
    text = "" if value is None else str(value).strip()
    if not re.fullmatch(r"\d+:\d{2}:\d{2}", text):
        return None
    hours, minutes, seconds = (int(item) for item in text.split(":"))
    if minutes > 59 or seconds > 59:
        return None
    return hours * 3600 + minutes * 60 + seconds


def _window_max_count(sorted_times: Sequence[int], window_minutes: int) -> int:
    """Return max events in [scheduled anchor, anchor + window)."""

    if not sorted_times:
        return 0
    window_seconds = window_minutes * 60
    right = 0
    maximum = 0
    for left, anchor in enumerate(sorted_times):
        if right < left:
            right = left
        while right < len(sorted_times) and sorted_times[right] < anchor + window_seconds:
            right += 1
        maximum = max(maximum, right - left)
    return maximum


def _assign_production_tiers(station_rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Assign tiers from density metrics, never from Stage 12 SelectionRank."""

    ordered = sorted(
        station_rows,
        key=lambda row: (
            -float(row["window_event_count_statistics"]["30"]["p95"] or 0),
            -float(row["window_event_count_statistics"]["15"]["p95"] or 0),
            -float(row["window_event_count_statistics"]["5"]["p95"] or 0),
            str(row["source_parent_station_id"]),
        ),
    )
    counts = {str(row["tier"]): int(row["station_count"]) for row in PRODUCTION_TIER_PLAN}
    tier_by_id: dict[str, str] = {}
    density_rank_by_id: dict[str, int] = {}
    cursor = 0
    for tier_name in ("A", "B", "C"):
        batch = ordered[cursor : cursor + counts[tier_name]]
        for row in batch:
            station_id = str(row["source_parent_station_id"])
            tier_by_id[station_id] = tier_name
            density_rank_by_id[station_id] = ordered.index(row) + 1
        cursor += counts[tier_name]
    updated: list[dict[str, Any]] = []
    for row in station_rows:
        copy = dict(row)
        station_id = str(copy["source_parent_station_id"])
        copy["production_tier"] = tier_by_id[station_id]
        copy["density_rank"] = density_rank_by_id[station_id]
        updated.append(copy)
    membership = {
        tier_name: [
            row["source_parent_station_id"]
            for row in sorted(updated, key=lambda item: item["density_rank"])
            if row["production_tier"] == tier_name
        ]
        for tier_name in ("A", "B", "C")
    }
    return updated, {
        "assignment_basis": [
            "30-minute station-day rolling maximum P95 descending",
            "15-minute station-day rolling maximum P95 descending",
            "5-minute station-day rolling maximum P95 descending",
            "source parent station ID ascending tie-breaker",
        ],
        "stage12_selection_rank_used": False,
        "tier_counts": {tier: len(ids) for tier, ids in membership.items()},
        "membership": membership,
        "ordered_density_station_ids": [row["source_parent_station_id"] for row in sorted(updated, key=lambda item: item["density_rank"])],
        "stable_identity_rule": "membership is derived from measured density metrics and immutable source parent identity; mutable runtime rank is not an input",
    }


def _static_limit_support(
    station_rows: Sequence[Mapping[str, Any]],
    number_of_results: int,
) -> dict[str, Any]:
    supported: list[bool] = []
    station_results: list[dict[str, Any]] = []
    for row in station_rows:
        interval = next(
            int(tier["interval_minutes"])
            for tier in PRODUCTION_TIER_PLAN
            if str(tier["tier"]) == str(row["production_tier"])
        )
        metric = row["window_event_count_statistics"][str(interval)]["p95"]
        passes = metric is not None and float(metric) <= number_of_results
        supported.append(passes)
        station_results.append(
            {
                "station": row["station_name"],
                "source_parent_station_id": row["source_parent_station_id"],
                "production_tier": row["production_tier"],
                "cadence_window_minutes": interval,
                "daily_max_event_count_p95": metric,
                "p95_at_or_below_result_limit": passes,
            }
        )
    return {
        "result_limit": number_of_results,
        "cadence_window_support_count": sum(supported),
        "station_count": len(supported),
        "all_station_p95_supported": bool(supported) and all(supported),
        "station_results": station_results,
        "interpretation": "static schedule planning evidence only; it is not realtime response coverage",
    }


def _static_event_density(
    root: Path,
    stage4: Mapping[str, Any],
    *,
    stage12_approved: bool = False,
    stage12_evidence: Mapping[str, Any] | None = None,
    gtfs_zip: Path | None = None,
) -> dict[str, Any]:
    """Analyze rolling scheduled-arrival density from the exact Stage 12 ZIP."""

    del root, stage4  # retained in the signature for callers of the prior audit module
    stage12_evidence = stage12_evidence or {}
    base_source = {
        "feed_version": STAGE12_FEED_VERSION,
        "feed_start_date": None,
        "feed_end_date": None,
        "zip_sha256": STAGE12_ZIP_SHA256,
        "source_url": STAGE12_SOURCE_URL,
        "raw_zip_committed": False,
        "raw_zip_retained_by_stage13": False,
        "evidence_path": "docs/stages/12-50-station-selection-evidence.json",
    }
    if not stage12_approved:
        return {
            "status": "NOT_ESTABLISHED",
            "blockers": ["STAGE_12_PANEL_APPROVAL_REQUIRED"],
            "source_snapshot": base_source,
            "station_tier_rows": [],
            "distribution": {},
            "coverage_conclusion": "NOT_ESTABLISHED",
        }
    if gtfs_zip is None:
        return {
            "status": "NOT_ESTABLISHED",
            "blockers": ["EXACT_STAGE12_GTFS_SNAPSHOT_REQUIRED_FOR_DENSITY"],
            "source_snapshot": base_source,
            "station_tier_rows": [],
            "distribution": {},
            "coverage_conclusion": "NOT_ESTABLISHED; the final density decision cannot use an unverified newer feed",
            "static_vs_realtime_boundary": "scheduled static density is planning evidence only and is not realtime response cardinality",
        }
    zip_path = gtfs_zip.expanduser().resolve()
    if not zip_path.is_file() or not zipfile.is_zipfile(zip_path):
        return {
            "status": "NOT_ESTABLISHED",
            "blockers": ["EXACT_STAGE12_GTFS_SNAPSHOT_NOT_AVAILABLE"],
            "source_snapshot": base_source,
            "input_path_status": "MISSING_OR_NOT_A_ZIP",
            "station_tier_rows": [],
            "distribution": {},
            "coverage_conclusion": "NOT_ESTABLISHED",
        }

    digest = hashlib.sha256()
    with zip_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    observed_hash = digest.hexdigest()
    if observed_hash != STAGE12_ZIP_SHA256:
        return {
            "status": "NOT_ESTABLISHED",
            "blockers": ["EXACT_STAGE12_GTFS_HASH_MISMATCH"],
            "source_snapshot": {**base_source, "observed_zip_sha256": observed_hash},
            "station_tier_rows": [],
            "distribution": {},
            "coverage_conclusion": "NOT_ESTABLISHED; no newer or different feed was substituted",
        }

    try:
        import stage_12_50_station_selection as stage12
    except Exception as exc:  # pragma: no cover - import failure is environment-specific
        return {
            "status": "NOT_ESTABLISHED",
            "blockers": ["STAGE12_SOURCE_PARSER_IMPORT_FAILURE"],
            "source_snapshot": {**base_source, "observed_zip_sha256": observed_hash},
            "error_type": type(exc).__name__,
            "station_tier_rows": [],
            "distribution": {},
            "coverage_conclusion": "NOT_ESTABLISHED",
        }

    panel = list(stage12_evidence.get("panel", []))
    if len(panel) != 50:
        return {
            "status": "NOT_ESTABLISHED",
            "blockers": ["APPROVED_PANEL_NOT_EXACTLY_50_STATIONS"],
            "source_snapshot": {**base_source, "observed_zip_sha256": observed_hash},
            "station_tier_rows": [],
            "distribution": {},
            "coverage_conclusion": "NOT_ESTABLISHED",
        }
    child_to_parent = {
        str(child): str(row["source_parent_station_id"])
        for row in panel
        for child in row.get("static_child_stop_ids", [])
    }
    patterns_by_station_service: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    with zipfile.ZipFile(zip_path, "r") as archive:
        feed_rows = list(stage12.iter_csv_rows(archive, "feed_info.txt"))
        feed_info = feed_rows[0] if feed_rows else {}
        if str(feed_info.get("feed_version", "")).strip() != STAGE12_FEED_VERSION:
            return {
                "status": "NOT_ESTABLISHED",
                "blockers": ["STAGE12_FEED_VERSION_MISMATCH"],
                "source_snapshot": {**base_source, "observed_zip_sha256": observed_hash},
                "station_tier_rows": [],
                "distribution": {},
                "coverage_conclusion": "NOT_ESTABLISHED",
            }
        service_dates_by_id, calendar_summary = stage12.active_service_dates(archive, feed_info)
        trip_service_by_id = {
            str(row.get("trip_id")): str(row.get("service_id"))
            for row in stage12.iter_csv_rows(archive, "trips.txt")
            if row.get("trip_id") and row.get("service_id")
        }
        relevant_pattern_rows = 0
        invalid_time_rows = 0
        for row in stage12.iter_csv_rows(archive, "stop_times.txt"):
            station_id = child_to_parent.get(str(row.get("stop_id", "")))
            if station_id is None:
                continue
            service_id = trip_service_by_id.get(str(row.get("trip_id", "")))
            if service_id is None or service_id not in service_dates_by_id:
                continue
            arrival_seconds = _parse_gtfs_time(row.get("arrival_time"))
            if arrival_seconds is None:
                invalid_time_rows += 1
                continue
            patterns_by_station_service[station_id][service_id].append(arrival_seconds)
            relevant_pattern_rows += 1

    station_rows: list[dict[str, Any]] = []
    all_daily_counts: list[int] = []
    all_daily_maxima: dict[int, list[int]] = {window: [] for window in FORWARD_WINDOWS_MINUTES}
    for panel_row in panel:
        station_id = str(panel_row["source_parent_station_id"])
        patterns = patterns_by_station_service.get(station_id, {})
        active_dates = sorted({date for service_id in patterns for date in service_dates_by_id.get(service_id, set())})
        daily_counts: list[int] = []
        daily_maxima: dict[int, list[int]] = {window: [] for window in FORWARD_WINDOWS_MINUTES}
        for active_date in active_dates:
            day_times: list[int] = []
            for service_id, times in patterns.items():
                if active_date in service_dates_by_id.get(service_id, set()):
                    day_times.extend(times)
            day_times.sort()
            daily_counts.append(len(day_times))
            all_daily_counts.append(len(day_times))
            for window in FORWARD_WINDOWS_MINUTES:
                maximum = _window_max_count(day_times, window)
                daily_maxima[window].append(maximum)
                all_daily_maxima[window].append(maximum)
        window_statistics = {str(window): value_statistics(values) for window, values in daily_maxima.items()}
        row = {
            "station": panel_row["station_name"],
            "station_name": panel_row["station_name"],
            "source_parent_station_id": station_id,
            "panel_selection_rank": panel_row.get("SelectionRank"),
            "transport_modes": panel_row.get("observed_mode_set", []),
            "interchange_indicators": {
                "distinct_route_count": panel_row.get("distinct_route_count"),
                "distinct_approved_mode_count": panel_row.get("distinct_approved_mode_count"),
                "static_child_stop_count": panel_row.get("static_child_stop_count"),
            },
            "active_service_day_count": len(active_dates),
            "schedule_pattern_event_count": sum(len(times) for times in patterns.values()),
            "expanded_scheduled_arrival_count": sum(daily_counts),
            "daily_scheduled_arrival_count_statistics": value_statistics(daily_counts),
            "window_event_count_statistics": window_statistics,
            "window_definition": "maximum scheduled arrivals in [scheduled arrival anchor, anchor + window) on each active service day",
            "production_result_limit_candidate": PRODUCTION_NUMBER_OF_RESULTS_CANDIDATE,
            "production_result_limit_static_coverage": {
                str(window): {
                    "active_service_days_at_or_below_limit": sum(
                        value <= PRODUCTION_NUMBER_OF_RESULTS_CANDIDATE for value in daily_maxima[window]
                    ),
                    "active_service_day_count": len(daily_maxima[window]),
                    "percentage_at_or_below_limit": _round(
                        (
                            sum(value <= PRODUCTION_NUMBER_OF_RESULTS_CANDIDATE for value in daily_maxima[window])
                            / len(daily_maxima[window])
                            * 100
                        )
                        if daily_maxima[window]
                        else None,
                        3,
                    ),
                    "p95_at_or_below_limit": (
                        window_statistics[str(window)]["p95"] is not None
                        and window_statistics[str(window)]["p95"] <= PRODUCTION_NUMBER_OF_RESULTS_CANDIDATE
                    ),
                }
                for window in FORWARD_WINDOWS_MINUTES
            },
        }
        station_rows.append(row)

    station_rows, tier_assignment = _assign_production_tiers(station_rows)
    global_distribution = {
        "active_service_day_scheduled_arrival_counts": value_statistics(all_daily_counts),
        "station_level_p95_by_window": {
            str(window): value_statistics(
                [row["window_event_count_statistics"][str(window)]["p95"] for row in station_rows]
            )
            for window in FORWARD_WINDOWS_MINUTES
        },
        "all_station_service_day_window_maxima": {
            str(window): value_statistics(all_daily_maxima[window])
            for window in FORWARD_WINDOWS_MINUTES
        },
    }
    source_snapshot = {
        **base_source,
        "feed_version": str(feed_info.get("feed_version")),
        "feed_start_date": feed_info.get("feed_start_date"),
        "feed_end_date": feed_info.get("feed_end_date"),
        "zip_sha256": observed_hash,
        "zip_size_bytes": zip_path.stat().st_size,
        "temporary_input_name": zip_path.name,
        "calendar_summary": calendar_summary,
    }
    return {
        "status": "COMPLETE",
        "blockers": [],
        "source_snapshot": source_snapshot,
        "analysis_contract": {
            "station_count": len(station_rows),
            "forward_windows_minutes": list(FORWARD_WINDOWS_MINUTES),
            "arrival_time_source": "GTFS stop_times.arrival_time for validated Stage 12 child stops",
            "service_date_expansion": "GTFS calendar plus calendar_dates exceptions from the exact feed",
            "no_newer_feed_substitution": True,
            "static_vs_realtime_boundary": "scheduled static density is planning evidence only and is not realtime response cardinality",
            "relevant_pattern_rows": relevant_pattern_rows,
            "invalid_arrival_time_rows_for_panel": invalid_time_rows,
        },
        "station_tier_rows": station_rows,
        "tier_assignment": tier_assignment,
        "distribution": global_distribution,
        "coverage_conclusion": "The exact static snapshot supports deterministic density-based tier membership and N=30 as a planning limit for each tier cadence window; it does not guarantee realtime response coverage.",
        "static_vs_realtime_boundary": "scheduled static density is planning evidence only and is not realtime response cardinality",
    }


def _safe_time_value(value: Any) -> tuple[datetime, str] | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("[GMT]", "")
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc), text


def _returned_thiscall_arrivals(bundle: Mapping[str, Any]) -> list[str]:
    """Return only the requested StopEventResult THIS-call arrival values.

    ``stage_08_mdd_realtime_pilot.build_bundle`` promotes the first
    ``ThisCall/CallAtStop/ServiceArrival`` TimetabledTime to the stop-event's
    ``timetabled_time`` field.  PreviousCall, OnwardCall, and departure
    timings remain available in ``call_observations`` for context, but they
    are deliberately excluded from result-limit coverage.
    """

    values: list[str] = []
    for event in bundle.get("stop_events", []):
        this_arrivals: list[str] = []
        call_observations = event.get("call_observations", []) or []
        for call in call_observations:
            if str(call.get("call_scope", "")).upper() != "THIS":
                continue
            for context in call.get("arrival_timing", []) or []:
                for item in context.get("field_values", {}).get("TimetabledTime", []) or []:
                    this_arrivals.append(str(item))
        if this_arrivals:
            values.extend(this_arrivals)
        elif not call_observations and event.get("timetabled_time") is not None:
            # The Stage 8 bundle normally derives this field from the same
            # THIS-call arrival.  Use it only as a normalized fallback when
            # call observations were not retained in a test/adapter bundle;
            # never infer THIS from an event that explicitly contains only
            # PreviousCall, OnwardCall, or other non-THIS observations.
            values.append(str(event["timetabled_time"]))
    return values


def _returned_event_times(bundle: Mapping[str, Any]) -> list[str]:
    """Backward-compatible name for the corrected THIS-call-only helper."""

    return _returned_thiscall_arrivals(bundle)


def _forward_coverage_metrics(
    bundle: Mapping[str, Any],
    requested_dep_arr_time: str,
) -> dict[str, Any]:
    """Calculate result-limit forward coverage from THIS-call arrivals only.

    The anchor is the requested ``DepArrTime``.  Returned THIS-call arrivals
    before the anchor are retained as separately counted evidence and cannot
    reduce or inflate the forward coverage.  A missing/invalid anchor or a
    response with no valid THIS-call arrivals has no calculable coverage and
    therefore returns ``None`` rather than a misleading zero.
    """

    anchor = _safe_time_value(requested_dep_arr_time)
    arrivals = [
        parsed
        for value in _returned_thiscall_arrivals(bundle)
        if (parsed := _safe_time_value(value)) is not None
    ]
    arrivals.sort(key=lambda item: item[0])
    metrics: dict[str, Any] = {
        "returned_thiscall_event_count": len(arrivals),
        "events_before_anchor_count": 0,
        "events_at_or_after_anchor_count": 0,
        "earliest_thiscall_arrival": arrivals[0][1] if arrivals else None,
        "latest_thiscall_arrival": arrivals[-1][1] if arrivals else None,
        "forward_coverage_seconds": None,
        "forward_coverage_status": "NOT_CALCULABLE",
    }
    if anchor is None:
        metrics["forward_coverage_status"] = "INVALID_REQUEST_ANCHOR"
        return metrics

    at_or_after = [item for item in arrivals if item[0] >= anchor[0]]
    metrics["events_before_anchor_count"] = len(arrivals) - len(at_or_after)
    metrics["events_at_or_after_anchor_count"] = len(at_or_after)
    if at_or_after:
        metrics["forward_coverage_seconds"] = _round(
            (at_or_after[-1][0] - anchor[0]).total_seconds(),
            3,
        )
        metrics["forward_coverage_status"] = "VALID_THIS_CALL_ARRIVAL_ONLY"
    else:
        metrics["forward_coverage_status"] = "NO_THIS_CALL_ARRIVAL_AT_OR_AFTER_ANCHOR"
    return metrics


def _probe_record(
    stage8: Any,
    result: Any,
    candidate: Mapping[str, Any],
    *,
    number_of_results: int,
    dep_arr_time: str,
    execution_mode: str,
    pair_id: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "station": candidate["station_name"],
        "source_parent_station_id": candidate["source_parent_station_id"],
        "requested_result_limit": number_of_results,
        "requested_dep_arr_time": dep_arr_time,
        "execution_mode": execution_mode,
        "pair_id": pair_id,
        "http_status": result.http_status,
        "parser_success": False,
        "valid_trias_application_response": False,
        "returned_event_count": 0,
        "returned_thiscall_event_count": 0,
        "events_before_anchor_count": 0,
        "events_at_or_after_anchor_count": 0,
        "saturation": False,
        "earliest_thiscall_arrival": None,
        "latest_thiscall_arrival": None,
        "forward_coverage_seconds": None,
        "forward_coverage_status": "NOT_EVALUATED",
        # Retained as a visibly retired compatibility field.  New records do
        # not calculate a mixed-call temporal span.
        "forward_temporal_span_seconds": None,
        "forward_temporal_span_status": "RETIRED_NOT_USED_FOR_FORWARD_COVERAGE",
        "source_stop_point_ref_consistency": "NOT_EVALUATED",
        "duration_ms": _round(float(result.elapsed_ms), 3),
        "transport_failure": result.failure,
        "error_classification": None,
        "attempt_count": 1,
        "raw_payload_persisted": False,
    }
    if result.http_status in {401, 403}:
        record["error_classification"] = "HTTP_AUTHORIZATION_FAILURE"
        return record
    if result.http_status == 429:
        record["error_classification"] = "HTTP_429_PROVIDER_PRESSURE_SIGNAL"
        return record
    if result.http_status is not None and result.http_status >= 500:
        record["error_classification"] = "HTTP_5XX_PROVIDER_FAILURE"
        return record
    if result.failure:
        record["error_classification"] = str(result.failure)
        return record
    try:
        stage8.raw_payload_secret_check(result.body, os.environ.get("MDD_API_KEY", ""))
        parsed = stage8.parse_response(result.body, result.content_type)
        record["parser_success"] = True
        application_error = stage8.application_error(parsed)
        record["valid_trias_application_response"] = not application_error
        if application_error:
            record["error_classification"] = "TRIAS_APPLICATION_ERROR"
            return record
        bundle = stage8.build_bundle(
            parsed=parsed,
            response_id=f"STAGE13|{candidate['source_parent_station_id']}|N{number_of_results}",
            run_id=MDD_DIAGNOSTIC_POLICY,
            observed_at=stage8.iso_utc(stage8.utc_now()),
            sampling_policy_id=MDD_DIAGNOSTIC_POLICY,
        )
        events = list(bundle.get("stop_events", []))
        returned_count = len(events)
        record["returned_event_count"] = returned_count
        record["saturation"] = returned_count >= number_of_results
        expected_refs = set(candidate.get("static_child_stop_ids", [])) | {
            str(candidate["source_parent_station_id"])
        }
        returned_event_refs: set[str] = set()
        related_call_ref_count = 0
        for event in events:
            if event.get("stop_point_ref"):
                returned_event_refs.add(str(event["stop_point_ref"]))
            for call in event.get("call_observations", []):
                if call.get("stop_point_ref"):
                    related_call_ref_count += 1
        if not events:
            record["source_stop_point_ref_consistency"] = "NO_RETURNED_EVENTS"
        elif returned_event_refs and returned_event_refs <= expected_refs:
            record["source_stop_point_ref_consistency"] = "ALL_RETURNED_REFS_MATCH_REQUESTED_PARENT_OR_CHILD"
        elif not returned_event_refs:
            record["source_stop_point_ref_consistency"] = "NO_SOURCE_STOP_POINT_REF_IN_RETURNED_EVENTS"
        else:
            record["source_stop_point_ref_consistency"] = "MISMATCH"
        record["related_previous_or_onward_call_ref_count"] = related_call_ref_count
        record.update(_forward_coverage_metrics(bundle, dep_arr_time))
    except getattr(stage8, "CredentialContentError", ValueError):
        record["error_classification"] = "RESPONSE_SECRET_DETECTED_NOT_PERSISTED"
    except Exception as exc:  # parser failure is sanitized to its type only
        record["error_classification"] = f"PARSER_FAILURE:{type(exc).__name__}"
    return record


def _select_diagnostic_representatives(station_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(station_rows, key=lambda row: int(row["density_rank"]))
    legacy_ids = {
        "de:05315:11110",
        "de:05315:11201",
        "de:05315:12711",
        "de:05315:14201",
        "de:05315:16601",
        "de:05315:17311",
        "de:05315:19201",
    }
    selected: list[tuple[str, Mapping[str, Any]]] = []

    def add(role: str, predicate: Any) -> None:
        selected_ids = {item[1]["source_parent_station_id"] for item in selected}
        for row in ordered:
            if row["source_parent_station_id"] in selected_ids:
                continue
            if predicate(row):
                selected.append((role, row))
                return

    add("high_density", lambda row: True)
    add("high_density_legacy7", lambda row: row["source_parent_station_id"] in legacy_ids)
    add("middle_density", lambda row: row["density_rank"] >= 24)
    add("low_density", lambda row: row["density_rank"] == len(ordered))
    add(
        "interchange_mode_diversity",
        lambda row: any("S-Bahn" in str(mode) or "Regional" in str(mode) for mode in row.get("transport_modes", [])),
    )
    add(
        "urban_bus_tram_diversity",
        lambda row: "Urban / KVB Bus" in row.get("transport_modes", []) and "Stadtbahn / Tram" in row.get("transport_modes", []),
    )
    return [
        {
            "representative_role": role,
            "station": row["station_name"],
            "source_parent_station_id": row["source_parent_station_id"],
            "production_tier": row.get("production_tier"),
            "density_rank": row.get("density_rank"),
            "transport_modes": row.get("transport_modes", []),
        }
        for role, row in selected
    ]


def _summary_for_records(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    corrected_coverage_values = [
        float(row["forward_coverage_seconds"])
        for row in records
        if row.get("forward_coverage_seconds") is not None
        and row.get("forward_coverage_status") == "VALID_THIS_CALL_ARRIVAL_ONLY"
    ]
    legacy_span_values = [
        float(row["forward_temporal_span_seconds"])
        for row in records
        if row.get("forward_temporal_span_seconds") is not None
    ]
    return {
        "request_count": len(records),
        "http_status_distribution": dict(sorted(Counter(str(row.get("http_status")) for row in records).items())),
        "parser_success_count": sum(bool(row.get("parser_success")) for row in records),
        "valid_trias_response_count": sum(bool(row.get("valid_trias_application_response")) for row in records),
        "saturated_count": sum(bool(row.get("saturation")) for row in records),
        "returned_event_count_statistics": value_statistics([int(row.get("returned_event_count", 0)) for row in records]),
        "returned_thiscall_event_count_statistics": value_statistics(
            [int(row["returned_thiscall_event_count"]) for row in records if row.get("returned_thiscall_event_count") is not None]
        ),
        "events_before_anchor_count_statistics": value_statistics(
            [int(row["events_before_anchor_count"]) for row in records if row.get("events_before_anchor_count") is not None]
        ),
        "events_at_or_after_anchor_count_statistics": value_statistics(
            [int(row["events_at_or_after_anchor_count"]) for row in records if row.get("events_at_or_after_anchor_count") is not None]
        ),
        "forward_coverage_statistics_seconds": value_statistics(corrected_coverage_values),
        "forward_coverage_metric_basis": "latest THIS-call arrival at or after requested DepArrTime minus requested DepArrTime",
        "legacy_forward_temporal_span_statistics_seconds": value_statistics(legacy_span_values),
        "legacy_forward_temporal_span_status": "INVALID_FOR_FORWARD_COVERAGE_DECISION" if legacy_span_values else "NOT_PRESENT",
        "duration_statistics_ms": duration_statistics(
            [float(row["duration_ms"]) for row in records if row.get("duration_ms") is not None]
        ),
        "source_ref_consistency_distribution": dict(
            sorted(Counter(str(row.get("source_stop_point_ref_consistency")) for row in records).items())
        ),
    }


def _run_live_diagnostics(
    station_rows: Sequence[Mapping[str, Any]],
    *,
    api_key: str | None = None,
    include_concurrency_probe: bool = True,
    attempt_budget: int | None = None,
) -> dict[str, Any]:
    """Run a sanitized diagnostic without persisting raw response payloads.

    The historical full diagnostic path includes the cap-2 sample.  The
    corrected rerun passes ``include_concurrency_probe=False`` and a budget of
    exactly 24 attempts so it cannot silently repeat the already-valid
    concurrency evidence or consume the old 100-attempt budget again.
    """

    if attempt_budget is None:
        attempt_budget = MDD_HTTP_ATTEMPT_HARD_MAX
    if attempt_budget <= 0:
        raise ValueError("attempt_budget must be positive")

    try:
        import stage_08_mdd_realtime_pilot as stage8
    except Exception as exc:  # pragma: no cover - import failure is environment-specific
        return {
            "status": "BLOCKED_RUNTIME_IMPORT_FAILURE",
            "credential_preflight": "NOT_EVALUATED",
            "http_attempts_consumed": 0,
            "max_http_attempts": MDD_HTTP_ATTEMPT_HARD_MAX,
            "current_run_attempt_budget": attempt_budget,
            "error_type": type(exc).__name__,
            "raw_payloads_persisted": False,
        }

    effective_key = os.environ.get("MDD_API_KEY") if api_key is None else api_key
    preflight = stage8.validate_api_key_format(effective_key)
    preflight_label = {
        getattr(stage8, "CREDENTIAL_PREFLIGHT_MISSING", "MISSING"): "MISSING",
        getattr(stage8, "CREDENTIAL_PREFLIGHT_INVALID_FORMAT", "INVALID_FORMAT"): "INVALID_FORMAT",
        getattr(stage8, "CREDENTIAL_PREFLIGHT_VALID", "VALID"): "PRESENT",
    }.get(preflight, "INVALID_FORMAT")
    if preflight_label != "PRESENT":
        return {
            "status": "NOT_RUN_API_KEY_MISSING" if preflight_label == "MISSING" else "NOT_RUN_INVALID_API_KEY_FORMAT",
            "credential_preflight": preflight_label,
            "http_attempts_consumed": 0,
            "max_http_attempts": MDD_HTTP_ATTEMPT_HARD_MAX,
            "current_run_attempt_budget": attempt_budget,
            "stop_reason": "zero MDD requests issued because the inherited credential was not usable",
            "raw_payloads_persisted": False,
        }

    representatives = _select_diagnostic_representatives(station_rows)
    by_id = {row["source_parent_station_id"]: row for row in station_rows}
    stage12_path = REPOSITORY_ROOT / "docs" / "stages" / "12-50-station-selection-evidence.json"
    stage12_panel = json.loads(stage12_path.read_text(encoding="utf-8")).get("panel", [])
    panel_by_id = {row["source_parent_station_id"]: row for row in stage12_panel}
    candidates: list[dict[str, Any]] = []
    for representative in representatives:
        density_row = by_id[representative["source_parent_station_id"]]
        panel_row = panel_by_id[representative["source_parent_station_id"]]
        candidates.append(
            {
                **representative,
                "station_name": density_row["station_name"],
                "static_child_stop_ids": panel_row.get("static_child_stop_ids", []),
            }
        )

    anchor = stage8.trias_utc(stage8.utc_now())
    attempts = 0
    stopped = False
    stop_reason: str | None = None

    def execute(candidate: Mapping[str, Any], number_of_results: int) -> Any | None:
        nonlocal attempts, stopped, stop_reason
        if stopped or attempts >= attempt_budget:
            stop_reason = stop_reason or "CURRENT_RUN_ATTEMPT_BUDGET_REACHED"
            return None
        runtime_config = stage8.Stage8RuntimeConfig(
            station=str(candidate["station_name"]),
            stop_point_ref=str(candidate["source_parent_station_id"]),
            number_of_results=number_of_results,
            sampling_policy=MDD_DIAGNOSTIC_POLICY,
        )
        result = stage8.perform_http_request(
            effective_key,
            pilot_anchor_dep_arr_time=anchor,
            runtime_config=runtime_config,
        )
        attempts += 1
        if result.http_status in {401, 403}:
            stopped = True
            stop_reason = "HTTP_401_OR_403_STOPPED_ALL_FURTHER_STAGE13_PROBING"
        return result

    sequential_records: list[dict[str, Any]] = []
    for candidate in candidates:
        for number_of_results in MDD_RESULT_LIMITS:
            result = execute(candidate, number_of_results)
            if result is None:
                break
            record = _probe_record(
                stage8,
                result,
                candidate,
                number_of_results=number_of_results,
                dep_arr_time=anchor,
                execution_mode="sequential_result_limit_comparison",
            )
            record["representative_role"] = candidate["representative_role"]
            sequential_records.append(record)
            if record["error_classification"] == "RESPONSE_SECRET_DETECTED_NOT_PERSISTED":
                stopped = True
                stop_reason = "SECRET_DETECTED_IN_RESPONSE_NOT_PERSISTED"
            if stopped:
                break
        if stopped:
            break

    concurrency_records: list[dict[str, Any]] = []
    pair_batch_durations: list[float] = []
    pairs = (
        [(candidates[index], candidates[index + 1]) for index in range(0, len(candidates) - 1, 2)]
        if include_concurrency_probe
        else []
    )
    for pair_index, pair in enumerate(pairs, start=1):
        if stopped or attempts + len(pair) > attempt_budget:
            stop_reason = stop_reason or "CURRENT_RUN_ATTEMPT_BUDGET_REACHED"
            break
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=MDD_CONCURRENCY_CAP) as executor:
            futures = [executor.submit(execute, candidate, PRODUCTION_NUMBER_OF_RESULTS_CANDIDATE) for candidate in pair]
            results = [future.result() for future in futures]
        batch_elapsed_ms = (time.perf_counter() - started) * 1000
        pair_batch_durations.append(batch_elapsed_ms)
        for candidate, result in zip(pair, results):
            if result is None:
                continue
            record = _probe_record(
                stage8,
                result,
                candidate,
                number_of_results=PRODUCTION_NUMBER_OF_RESULTS_CANDIDATE,
                dep_arr_time=anchor,
                execution_mode="bounded_concurrency_cap_2",
                pair_id=f"PAIR_{pair_index}",
            )
            record["representative_role"] = candidate["representative_role"]
            record["pair_batch_duration_ms"] = _round(batch_elapsed_ms, 3)
            concurrency_records.append(record)
            if record["error_classification"] == "RESPONSE_SECRET_DETECTED_NOT_PERSISTED":
                stopped = True
                stop_reason = "SECRET_DETECTED_IN_RESPONSE_NOT_PERSISTED"

    result_limit_summary = {
        str(number): _summary_for_records(
            [row for row in sequential_records if row["requested_result_limit"] == number]
        )
        for number in MDD_RESULT_LIMITS
    }
    cap2_request_durations = [float(row["duration_ms"]) for row in concurrency_records if row.get("duration_ms") is not None]
    pressure_rows = sequential_records + concurrency_records
    provider_pressure_signal = any(
        row.get("http_status") == 429
        or (isinstance(row.get("http_status"), int) and row["http_status"] >= 500)
        or row.get("transport_failure") in {"TIMEOUT", "NETWORK_OR_DNS_FAILURE"}
        for row in pressure_rows
    )
    cap2_complete = include_concurrency_probe and (
        len(concurrency_records) == len(pairs) * 2
        and all(row.get("parser_success") and row.get("valid_trias_application_response") for row in concurrency_records)
        and not provider_pressure_signal
        and not any(row.get("http_status") in {401, 403} for row in concurrency_records)
    )
    static_support = {str(number): _static_limit_support(station_rows, number) for number in MDD_RESULT_LIMITS}
    n30_records = [row for row in sequential_records if row["requested_result_limit"] == 30]
    n30_supported = (
        len(n30_records) == len(candidates)
        and all(
            row.get("parser_success")
            and row.get("valid_trias_application_response")
            and row.get("source_stop_point_ref_consistency") != "MISMATCH"
            for row in n30_records
        )
        and static_support["30"]["all_station_p95_supported"]
    )
    number_limit_decision = "TECHNICALLY_EVALUATED_OPTION_N30_REQUIRES_USER_APPROVAL" if n30_supported else "NOT_ESTABLISHED"
    diagnostic_status = (
        "COMPLETE_TECHNICAL_RESULT_LIMIT_COMPARISON"
        if n30_supported and (cap2_complete or not include_concurrency_probe) and not stopped
        else "CONDITIONALLY_BLOCKED"
    )
    return {
        "status": diagnostic_status,
        "credential_preflight": preflight_label,
        "endpoint": ENDPOINT,
        "request_contract": {
            "requestor_ref": REQUESTOR_REF,
            "stop_event_type": "arrival",
            "include_previous_calls": True,
            "include_onward_calls": True,
            "include_realtime_data": True,
            "max_attempts": PRODUCTION_MAX_ATTEMPTS_CANDIDATE,
            "retries": 0,
            "timeout_seconds": 30,
            "dep_arr_time_anchor_reused_for_all_result_limit_comparisons": True,
            "concurrency_for_result_limit_comparison": "sequential",
        },
        "diagnostic_anchor_dep_arr_time": anchor,
        "representatives": representatives,
        "result_limit_probe": {
            "requested_result_limits": list(MDD_RESULT_LIMITS),
            "sequential_records": sequential_records,
            "summary_by_result_limit": result_limit_summary,
            "static_cadence_window_support_by_result_limit": static_support,
            "production_selection": {
                "decision": number_limit_decision,
                "technically_evaluated_number_of_results": 30 if n30_supported else None,
                "production_number_of_results": None,
                "user_approved_design": False,
                "provider_maximum_status": "PROVIDER_MAXIMUM_NOT_ESTABLISHED",
                "reason": "N=30 is a technically evaluated option from deterministic high/middle/low-density and mode/interchange representatives; exact-feed static P95 cadence-window counts are at or below 30 for all evaluated tier assignments. It is not a user-approved production decision or a theoretical provider-maximum claim.",
            },
        },
        "bounded_concurrency_probe": {
            "requested_max_concurrency": MDD_CONCURRENCY_CAP,
            "pair_count": len(pairs),
            "records": concurrency_records,
            "request_duration_statistics_ms": duration_statistics(cap2_request_durations),
            "pair_batch_duration_statistics_ms": duration_statistics(pair_batch_durations),
            "provider_pressure_signal": provider_pressure_signal,
            "failure_isolation_observed": len(concurrency_records) == len(pairs) * 2,
            "no_retries": True,
            "decision": (
                "TECHNICALLY_EVALUATED_OPTION_MAXIMUM_CONCURRENCY_2"
                if cap2_complete
                else "NOT_RUN_CORRECTED_RESULT_LIMIT_DIAGNOSTIC_ONLY"
                if not include_concurrency_probe
                else "FALLBACK_SEQUENTIAL_REQUIRED"
            ),
            "user_approved_design": False,
            "provider_scope_limit": "bounded sample only; no provider-wide rate-limit claim",
        },
        "sequential_duration_evidence": {
            "request_duration_statistics_ms": duration_statistics(
                [float(row["duration_ms"]) for row in sequential_records if row.get("duration_ms") is not None]
            ),
            "request_count": len(sequential_records),
            "retries": 0,
        },
        "http_attempts_consumed": attempts,
        "max_http_attempts": MDD_HTTP_ATTEMPT_HARD_MAX,
        "current_run_attempt_budget": attempt_budget,
        "stopped_on_auth_failure": stopped and stop_reason == "HTTP_401_OR_403_STOPPED_ALL_FURTHER_STAGE13_PROBING",
        "stop_reason": stop_reason,
        "raw_payloads_persisted": False,
        "request_headers_persisted": False,
        "api_key_persisted": False,
    }


def _run_corrected_result_limit_diagnostic(
    station_rows: Sequence[Mapping[str, Any]],
    *,
    prior_attempts: int,
    additional_requests_approved: bool,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Run only the corrected six-station × four-limit comparison.

    This path is intentionally closed until the user approves the additional
    requests.  It does not repeat the cap-2 diagnostic.  The historical
    100-attempt ceiling remains unchanged; the explicit approval gate records
    the separately authorized cumulative total of 90 + 24 = 114 attempts.
    """

    gate = {
        "approval_request": ADDITIONAL_RESULT_LIMIT_APPROVAL_REQUEST,
        "required": True,
        "approved": bool(additional_requests_approved),
        "corrected_diagnostic_scope": CORRECTED_RESULT_LIMIT_DIAGNOSTIC_SCOPE,
        "requested_additional_http_attempts": ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
        "prior_attempts_consumed": prior_attempts,
        "cumulative_attempts_if_approved": prior_attempts + ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
        "historical_diagnostic_hard_ceiling": MDD_HTTP_ATTEMPT_HARD_MAX,
        "historical_ceiling_changed": False,
        "additional_requests_issued": 0,
        "corrected_live_diagnostic_issued": False,
        "raw_responses_persisted": False,
    }
    if not additional_requests_approved:
        return {
            "status": "NOT_RUN_USER_APPROVAL_REQUIRED",
            "credential_preflight": "NOT_EVALUATED",
            "http_attempts_consumed": 0,
            "max_http_attempts": MDD_HTTP_ATTEMPT_HARD_MAX,
            "current_run_attempt_budget": ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
            "additional_request_gate": gate,
            "stop_reason": "explicit approval is required before any corrected MDD request",
            "raw_payloads_persisted": False,
        }
    if prior_attempts != PRIOR_STAGE13_DIAGNOSTIC_ATTEMPTS:
        raise Stage13Blocked(
            "corrected result-limit diagnostic requires prior_attempts=90 so cumulative usage is auditable"
        )
    live = _run_live_diagnostics(
        station_rows,
        api_key=api_key,
        include_concurrency_probe=False,
        attempt_budget=ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
    )
    live["additional_request_gate"] = gate
    gate["additional_requests_issued"] = int(live.get("http_attempts_consumed", 0))
    gate["corrected_live_diagnostic_issued"] = gate["additional_requests_issued"] > 0
    live["http_attempts_consumed_total"] = prior_attempts + int(live.get("http_attempts_consumed", 0))
    live["historical_diagnostic_hard_ceiling"] = MDD_HTTP_ATTEMPT_HARD_MAX
    live["historical_ceiling_changed"] = False
    live["concurrency_rerun"] = False
    return live


def _cadence_static_fit(
    station_rows: Sequence[Mapping[str, Any]],
    tiers: Sequence[Mapping[str, int]],
    number_of_results: int,
) -> dict[str, Any]:
    ordered = sorted(station_rows, key=lambda row: int(row["density_rank"]))
    cursor = 0
    results: list[dict[str, Any]] = []
    for tier in tiers:
        interval = int(tier["interval_minutes"])
        for row in ordered[cursor : cursor + int(tier["station_count"])]:
            metric = row["window_event_count_statistics"][str(interval)]["p95"]
            results.append(
                {
                    "source_parent_station_id": row["source_parent_station_id"],
                    "tier": tier["tier"],
                    "interval_minutes": interval,
                    "cadence_window_p95": metric,
                    "p95_at_or_below_result_limit": metric is not None and float(metric) <= number_of_results,
                }
            )
        cursor += int(tier["station_count"])
    return {
        "station_count": len(results),
        "station_p95_supported_count": sum(row["p95_at_or_below_result_limit"] for row in results),
        "all_station_p95_supported": bool(results) and all(row["p95_at_or_below_result_limit"] for row in results),
    }


def _cadence_alternatives(
    station_rows: Sequence[Mapping[str, Any]],
    *,
    legacy_reserved_requests: Mapping[int, int],
) -> list[dict[str, Any]]:
    alternatives = [
        (
            "historical_10_20_20_at_5_10_30",
            (
                {"tier": "A", "station_count": 10, "interval_minutes": 5},
                {"tier": "B", "station_count": 20, "interval_minutes": 10},
                {"tier": "C", "station_count": 20, "interval_minutes": 30},
            ),
        ),
        ("10_20_20_at_5_15_30_evaluated_option", PRODUCTION_TIER_PLAN),
        (
            "10_15_25_at_5_10_30",
            (
                {"tier": "A", "station_count": 10, "interval_minutes": 5},
                {"tier": "B", "station_count": 15, "interval_minutes": 10},
                {"tier": "C", "station_count": 25, "interval_minutes": 30},
            ),
        ),
        (
            "8_20_22_at_5_15_30",
            (
                {"tier": "A", "station_count": 8, "interval_minutes": 5},
                {"tier": "B", "station_count": 20, "interval_minutes": 15},
                {"tier": "C", "station_count": 22, "interval_minutes": 30},
            ),
        ),
        (
            "8_12_30_at_5_10_30",
            (
                {"tier": "A", "station_count": 8, "interval_minutes": 5},
                {"tier": "B", "station_count": 12, "interval_minutes": 10},
                {"tier": "C", "station_count": 30, "interval_minutes": 30},
            ),
        ),
    ]
    rows: list[dict[str, Any]] = []
    for label, tiers in alternatives:
        cadence = candidate_cadence(tiers)
        projections = quota_projection_set(
            cadence["logical_requests_per_hour"],
            legacy_reserved_requests=legacy_reserved_requests,
            diagnostic_reserved_requests=DIAGNOSTIC_RESERVE_HTTP_ATTEMPTS,
        )
        stress = next(item for item in projections["projections"] if item["label"] == "ten_percent_stress_sensitivity")
        rows.append(
            {
                "label": label,
                "tiers": cadence["tiers"],
                "logical_requests_per_hour": cadence["logical_requests_per_hour"],
                "logical_requests_per_day": cadence["logical_requests_per_day"],
                "monthly_logical_requests": cadence["monthly_logical_requests"],
                "static_density_fit_at_n30": _cadence_static_fit(station_rows, tiers, 30) if station_rows else None,
                "31_day_ten_percent_stress_with_legacy_and_diagnostics": stress["31_day"]["conservative_reserve_reconciliation"],
                "technically_evaluated_option": label == "10_20_20_at_5_15_30_evaluated_option",
                "user_review_preference": label == "8_20_22_at_5_15_30",
                "user_approved_design": False,
            }
        )
    return rows


def _tier_specific_static_fit(
    station_rows: Sequence[Mapping[str, Any]],
    tiers: Sequence[Mapping[str, int]],
    result_limits: Mapping[str, int],
) -> dict[str, Any]:
    """Summarize exact-feed P95 support for a small tier-specific limit set."""

    ordered = sorted(station_rows, key=lambda row: int(row["density_rank"]))
    cursor = 0
    by_tier: dict[str, Any] = {}
    for tier in tiers:
        tier_name = str(tier["tier"])
        station_count = int(tier["station_count"])
        interval = str(tier["interval_minutes"])
        limit = int(result_limits[tier_name])
        rows = ordered[cursor : cursor + station_count]
        p95_values = [row["window_event_count_statistics"][interval]["p95"] for row in rows]
        supported = [value is not None and float(value) <= limit for value in p95_values]
        by_tier[tier_name] = {
            "result_limit": limit,
            "interval_minutes": int(interval),
            "station_count": len(rows),
            "station_p95_supported_count": sum(supported),
            "all_station_p95_supported": bool(rows) and all(supported),
            "maximum_station_p95": max(p95_values) if p95_values else None,
        }
        cursor += station_count
    return {
        "by_tier": by_tier,
        "station_count": sum(item["station_count"] for item in by_tier.values()),
        "station_p95_supported_count": sum(item["station_p95_supported_count"] for item in by_tier.values()),
        "all_station_p95_supported": all(item["all_station_p95_supported"] for item in by_tier.values()),
    }


def _corrected_diagnostic_metric_status(live_diagnostics: Mapping[str, Any]) -> str:
    """Classify whether the corrected run produced usable coverage evidence."""

    probe = live_diagnostics.get("result_limit_probe", {})
    records = list(probe.get("sequential_records", [])) if isinstance(probe, Mapping) else []
    if not records:
        return "NOT_RECONSTRUCTABLE_FROM_SANITIZED_AGGREGATES"
    if any(row.get("forward_coverage_status") == "VALID_THIS_CALL_ARRIVAL_ONLY" for row in records):
        return "CORRECTED_ANCHOR_BASED_THIS_CALL_METRIC_AVAILABLE"
    if all(row.get("transport_failure") == "NETWORK_OR_DNS_FAILURE" for row in records):
        return "CORRECTED_DIAGNOSTIC_COMPLETED_NO_VALID_RESPONSES_NETWORK_OR_DNS_FAILURE"
    if all(row.get("http_status") in {401, 403} for row in records):
        return "CORRECTED_DIAGNOSTIC_STOPPED_AUTHORIZATION_FAILURE"
    return "CORRECTED_DIAGNOSTIC_COMPLETED_WITHOUT_USABLE_FORWARD_COVERAGE"


def _result_limit_comparison(
    station_rows: Sequence[Mapping[str, Any]],
    live_diagnostics: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare requested result-limit policies without converting failure into coverage."""

    probe = live_diagnostics.get("result_limit_probe", {})
    summaries = probe.get("summary_by_result_limit", {}) if isinstance(probe, Mapping) else {}
    corrected_status = _corrected_diagnostic_metric_status(live_diagnostics)

    def comparison_for_limit(number: int) -> dict[str, Any]:
        summary = dict(summaries.get(str(number), {}))
        return {
            "requested_result_limit": number,
            "request_count": summary.get("request_count", 0),
            "http_status_distribution": summary.get("http_status_distribution", {}),
            "valid_trias_response_count": summary.get("valid_trias_response_count", 0),
            "returned_event_count_statistics": summary.get("returned_event_count_statistics", {}),
            "saturated_count": summary.get("saturated_count", 0),
            "forward_coverage_statistics_seconds": summary.get("forward_coverage_statistics_seconds", {}),
            "duration_statistics_ms": summary.get("duration_statistics_ms", {}),
            "comparison_status": (
                "CORRECTED_THIS_CALL_EVIDENCE_AVAILABLE"
                if summary.get("forward_coverage_statistics_seconds", {}).get("count", 0) > 0
                else corrected_status
            ),
        }

    fixed_plan = PRODUCTION_TIER_PLAN
    tier_specific_limits = {"A": 20, "B": 30, "C": 30}
    fixed_n20 = _cadence_static_fit(station_rows, fixed_plan, 20) if station_rows else None
    fixed_n30 = _cadence_static_fit(station_rows, fixed_plan, 30) if station_rows else None
    tier_specific = (
        _tier_specific_static_fit(station_rows, fixed_plan, tier_specific_limits)
        if station_rows
        else None
    )
    live_n20 = comparison_for_limit(20)
    live_n30 = comparison_for_limit(30)
    return {
        "corrected_diagnostic_status": corrected_status,
        "forward_coverage_metric_basis": "latest THIS-call arrival at or after requested DepArrTime minus requested DepArrTime",
        "fixed_n20": {
            "static_cadence_window_fit": fixed_n20,
            "corrected_live": live_n20,
            "decision": "UNDECIDED_NO_VALID_CORRECTED_LIVE_RESPONSES",
        },
        "fixed_n30": {
            "static_cadence_window_fit": fixed_n30,
            "corrected_live": live_n30,
            "decision": "UNDECIDED_NO_VALID_CORRECTED_LIVE_RESPONSES",
        },
        "tier_specific_a20_b30_c30": {
            "static_cadence_window_fit": tier_specific,
            "corrected_live": {
                "source_result_limits": {"A": 20, "B": 30, "C": 30},
                "available_result_limit_probes": {
                    "N20": live_n20,
                    "N30": live_n30,
                },
                "comparison_status": corrected_status,
            },
            "decision": "UNDECIDED_NO_VALID_CORRECTED_LIVE_RESPONSES",
        },
        "simpler_alternative": {
            "candidate": "A=20, B=30, C=30",
            "static_cadence_window_fit": tier_specific,
            "corrected_live_status": corrected_status,
            "recommendation": "NO_SELECTION_UNTIL_VALID_CORRECTED_LIVE_RESPONSES_EXIST",
        },
        "provider_maximum_status": "PROVIDER_MAXIMUM_NOT_ESTABLISHED",
        "number_of_results_finalized": False,
    }


def _cadence_boundary_review(
    station_rows: Sequence[Mapping[str, Any]],
    stage12_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Explain the station changes implied by the two requested tier-count plans."""

    legacy_ids = {
        "de:05315:11110",
        "de:05315:11201",
        "de:05315:12711",
        "de:05315:14201",
        "de:05315:16601",
        "de:05315:17311",
        "de:05315:19201",
    }
    ordered = sorted(station_rows, key=lambda row: int(row["density_rank"]))
    panel_by_id = {row["source_parent_station_id"]: row for row in stage12_evidence.get("panel", [])}
    changed: list[dict[str, Any]] = []
    for rank, row in enumerate(ordered, start=1):
        old_tier = "A" if rank <= 10 else "B" if rank <= 30 else "C"
        new_tier = "A" if rank <= 8 else "B" if rank <= 28 else "C"
        if old_tier == new_tier:
            continue
        panel_row = panel_by_id.get(row["source_parent_station_id"], {})
        if old_tier == "A" and new_tier == "B":
            boundary_reason = "rank 9/10 leaves the 10-station A tier when A shrinks to 8; it becomes a B-tier station"
        else:
            boundary_reason = "rank 29/30 enters the 22-station C tier when C grows from 20 to 22"
        changed.append(
            {
                "density_rank": rank,
                "source_parent_station_id": row["source_parent_station_id"],
                "station_name": row["station_name"],
                "tier_in_10_20_20": old_tier,
                "tier_in_8_20_22": new_tier,
                "window_event_count_p95": {
                    str(window): row["window_event_count_statistics"][str(window)]["p95"]
                    for window in FORWARD_WINDOWS_MINUTES
                },
                "daily_scheduled_arrival_count_p95": row["daily_scheduled_arrival_count_statistics"]["p95"],
                "schedule_pattern_event_count": row.get("schedule_pattern_event_count"),
                "routes": panel_row.get("route_ids", []),
                "route_ids_by_mode": panel_row.get("route_ids_by_mode", {}),
                "modes": row.get("transport_modes", []),
                "child_stop_count": row.get("interchange_indicators", {}).get("static_child_stop_count"),
                "legacy7": row["source_parent_station_id"] in legacy_ids,
                "why_boundary_station": boundary_reason,
            }
        )
    return {
        "plan_a": "10/20/20 @ 5/15/30",
        "plan_b": "8/20/22 @ 5/15/30",
        "requested_difference_count": 2,
        "actual_density_ranked_assignment_difference_count": len(changed),
        "interpretation": "The stated cardinalities produce four changed tier assignments: two A→B and two B→C. The requested count of two is therefore not consistent with the deterministic density-ranked assignment.",
        "a_to_b_boundary_stations": [item for item in changed if item["tier_in_10_20_20"] == "A"],
        "b_to_c_boundary_stations": [item for item in changed if item["tier_in_10_20_20"] == "B"],
        "changed_stations": changed,
    }


def _heartbeat_feasibility(
    phase: Mapping[str, Any],
    live_diagnostics: Mapping[str, Any],
) -> dict[str, Any]:
    max_due = int(phase.get("max_due_targets_in_one_heartbeat", 0))
    cap2 = live_diagnostics.get("bounded_concurrency_probe", {})
    batch_stats = cap2.get("pair_batch_duration_statistics_ms", {})
    request_stats = cap2.get("request_duration_statistics_ms", {})
    batch_count = math.ceil(max_due / MDD_CONCURRENCY_CAP) if max_due else 0
    p95_batch = batch_stats.get("p95_ms")
    max_batch = batch_stats.get("max_ms")
    estimated_p95 = batch_count * float(p95_batch) if p95_batch is not None else None
    estimated_max = batch_count * float(max_batch) if max_batch is not None else None
    return {
        "heartbeat_interval_seconds": HEARTBEAT_MINUTES * 60,
        "due_targets_per_heartbeat": phase.get("total_due_targets_by_heartbeat", []),
        "max_due_targets_in_one_heartbeat": max_due,
        "requests_per_super_cycle": phase.get("super_cycle_total_logical_requests"),
        "requests_per_hour": phase.get("hour_total_logical_requests"),
        "technically_evaluated_maximum_concurrency": MDD_CONCURRENCY_CAP if cap2.get("decision") in {
            "APPROVED_MAXIMUM_CONCURRENCY_2",
            "TECHNICALLY_EVALUATED_OPTION_MAXIMUM_CONCURRENCY_2",
        } else None,
        "user_approved_maximum_concurrency": False,
        "cap2_request_duration_statistics_ms": request_stats,
        "cap2_pair_batch_duration_statistics_ms": batch_stats,
        "max_due_batches_at_cap2": batch_count,
        "estimated_p95_heartbeat_execution_ms": _round(estimated_p95, 3),
        "estimated_max_observed_heartbeat_execution_ms": _round(estimated_max, 3),
        "finish_within_five_minute_heartbeat_at_observed_p95": estimated_p95 is not None and estimated_p95 <= 300_000,
        "finish_within_five_minute_heartbeat_at_observed_max": estimated_max is not None and estimated_max <= 300_000,
        "feasibility_decision": "FEASIBLE_WITH_TECHNICALLY_EVALUATED_CAP_2" if estimated_p95 is not None and estimated_p95 <= 300_000 else "NOT_ESTABLISHED",
    }


def _reuse_existing_live_diagnostics(
    root: Path,
    station_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Rebuild the sanitized live object without issuing another request."""

    path = root / "docs/stages/13-tiered-collector-readiness-evidence.json"
    if not path.exists():
        return None
    existing = json.loads(path.read_text(encoding="utf-8"))
    old_bounded = existing.get("number_of_results", {}).get("bounded_live_diagnostic", {})
    old_concurrency = existing.get("sequential_vs_bounded_concurrency", {}).get("bounded_live_diagnostic", {})
    old_sequential = existing.get("sequential_vs_bounded_concurrency", {}).get("sequential_live_diagnostic", {})
    old_probe = old_bounded.get("result_limit_probe")
    if not isinstance(old_probe, Mapping) or not old_probe.get("sequential_records"):
        return None
    existing_gate = old_bounded.get("additional_request_gate", {})
    corrected_records = list(old_probe.get("sequential_records", []))
    if (
        existing_gate.get("approved") is True
        and existing_gate.get("additional_requests_issued") == ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS
        and len(corrected_records) == ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS
        and all(row.get("forward_temporal_span_seconds") is None for row in corrected_records)
    ):
        probe = dict(old_probe)
        probe["representatives"] = _select_diagnostic_representatives(station_rows)
        production_selection = dict(probe.get("production_selection", {}))
        production_selection["technically_evaluated_number_of_results"] = production_selection.get(
            "technically_evaluated_number_of_results"
        )
        production_selection["decision"] = (
            "TECHNICALLY_EVALUATED_OPTION_REQUIRES_USER_APPROVAL"
            if production_selection["technically_evaluated_number_of_results"] == PRODUCTION_NUMBER_OF_RESULTS_CANDIDATE
            else "NOT_ESTABLISHED"
        )
        production_selection["production_number_of_results"] = None
        production_selection["user_approved_design"] = False
        probe["production_selection"] = production_selection
        old_concurrency = dict(old_concurrency)
        old_concurrency["records"] = [
            _mark_legacy_forward_span_invalid(record)
            for record in old_concurrency.get("records", [])
        ]
        if old_concurrency.get("decision") == "APPROVED_MAXIMUM_CONCURRENCY_2":
            old_concurrency["decision"] = "TECHNICALLY_EVALUATED_OPTION_MAXIMUM_CONCURRENCY_2"
        old_concurrency["user_approved_design"] = False
        return {
            "status": old_bounded.get("status", "CONDITIONALLY_BLOCKED"),
            "credential_preflight": old_bounded.get("credential_preflight", "PRESENT"),
            "diagnostic_anchor_dep_arr_time": old_bounded.get("diagnostic_anchor_dep_arr_time"),
            "representatives": probe.get("representatives", []),
            "result_limit_probe": probe,
            "bounded_concurrency_probe": old_concurrency,
            "sequential_duration_evidence": old_sequential,
            "http_attempts_consumed": old_bounded.get("current_run_http_attempts_consumed", 0),
            "max_http_attempts": MDD_HTTP_ATTEMPT_HARD_MAX,
            "prior_attempts_consumed": old_bounded.get("prior_attempts_consumed", PRIOR_STAGE13_DIAGNOSTIC_ATTEMPTS),
            "additional_request_gate": existing_gate,
            "historical_invalidated_sequential_records": existing.get("number_of_results", {}).get(
                "historical_invalidated_sequential_records", []
            ),
            "historical_invalidated_sequential_summary_by_result_limit": existing.get("number_of_results", {}).get(
                "historical_invalidated_sequential_summary_by_result_limit", {}
            ),
            "stop_reason": old_bounded.get("stop_reason"),
            "raw_payloads_persisted": False,
            "request_headers_persisted": False,
            "api_key_persisted": False,
        }
    probe = dict(old_probe)
    probe["representatives"] = _select_diagnostic_representatives(station_rows)
    probe["sequential_records"] = [
        _mark_legacy_forward_span_invalid(record)
        for record in probe.get("sequential_records", [])
    ]
    probe["summary_by_result_limit"] = {
        str(number): _mark_legacy_summary_invalid(summary)
        for number, summary in probe.get("summary_by_result_limit", {}).items()
    }
    production_selection = dict(probe.get("production_selection", {}))
    production_selection["decision"] = "TECHNICALLY_EVALUATED_OPTION_REQUIRES_USER_APPROVAL"
    production_selection["technically_evaluated_number_of_results"] = production_selection.get(
        "technically_evaluated_number_of_results",
        production_selection.get("production_number_of_results", PRODUCTION_NUMBER_OF_RESULTS_CANDIDATE),
    )
    production_selection["production_number_of_results"] = None
    production_selection["user_approved_design"] = False
    probe["production_selection"] = production_selection
    old_concurrency = dict(old_concurrency)
    old_concurrency["records"] = [
        _mark_legacy_forward_span_invalid(record)
        for record in old_concurrency.get("records", [])
    ]
    if old_concurrency.get("decision") == "APPROVED_MAXIMUM_CONCURRENCY_2":
        old_concurrency["decision"] = "TECHNICALLY_EVALUATED_OPTION_MAXIMUM_CONCURRENCY_2"
    old_concurrency["user_approved_design"] = False
    first_record = probe["sequential_records"][0]
    return {
        "status": "HISTORICAL_SANITIZED_EVIDENCE_RETAINED; FORWARD_COVERAGE_INVALIDATED",
        "credential_preflight": "PRESENT",
        "diagnostic_anchor_dep_arr_time": first_record.get("requested_dep_arr_time"),
        "representatives": probe["representatives"],
        "result_limit_probe": probe,
        "historical_invalidated_sequential_records": probe.get("sequential_records", []),
        "historical_invalidated_sequential_summary_by_result_limit": probe.get("summary_by_result_limit", {}),
        "bounded_concurrency_probe": old_concurrency,
        "sequential_duration_evidence": old_sequential,
        "http_attempts_consumed": 0,
        "max_http_attempts": MDD_HTTP_ATTEMPT_HARD_MAX,
        "raw_payloads_persisted": False,
        "request_headers_persisted": False,
        "api_key_persisted": False,
    }


def _mark_legacy_forward_span_invalid(record: Mapping[str, Any]) -> dict[str, Any]:
    """Retain old sanitized records without treating their span as coverage."""

    updated = dict(record)
    updated["forward_temporal_span_status"] = "INVALID_FOR_FORWARD_COVERAGE_DECISION"
    updated["forward_coverage_seconds"] = None
    updated["forward_coverage_status"] = "NOT_RECONSTRUCTABLE_FROM_SANITIZED_AGGREGATE"
    updated["returned_thiscall_event_count"] = None
    updated["events_before_anchor_count"] = None
    updated["events_at_or_after_anchor_count"] = None
    updated["earliest_thiscall_arrival"] = None
    updated["latest_thiscall_arrival"] = None
    return updated


def _mark_legacy_summary_invalid(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Add an explicit invalidation marker to an old result-limit summary."""

    updated = dict(summary)
    if "forward_temporal_span_statistics_seconds" in updated:
        updated["legacy_forward_temporal_span_statistics_seconds"] = updated[
            "forward_temporal_span_statistics_seconds"
        ]
    updated["forward_coverage_statistics_seconds"] = value_statistics([])
    updated["forward_coverage_metric_basis"] = (
        "latest THIS-call arrival at or after requested DepArrTime minus requested DepArrTime"
    )
    updated["forward_temporal_span_statistics_status"] = "INVALID_FOR_FORWARD_COVERAGE_DECISION"
    updated["legacy_forward_temporal_span_status"] = "INVALID_FOR_FORWARD_COVERAGE_DECISION"
    return updated


def build_evidence(
    root: Path = REPOSITORY_ROOT,
    *,
    generated_at_utc: str | None = None,
    gtfs_zip: Path | None = None,
    run_live_diagnostics: bool = False,
    live_diagnostics: Mapping[str, Any] | None = None,
    prior_live_attempts: int = 0,
    reuse_existing_live: bool = False,
    additional_mdd_requests_approved: bool = False,
) -> dict[str, Any]:
    stage4_path = root / "docs" / "stages" / "04-cologne-scope-mode-evidence.json"
    stage4 = json.loads(stage4_path.read_text(encoding="utf-8")) if stage4_path.exists() else {}
    expected_stage12_paths = [
        "docs/stages/12-50-station-selection.md",
        "docs/stages/12-50-station-selection-completion-decision.md",
        "docs/stages/12-50-station-selection-evidence.json",
    ]
    stage12_paths_found = [path for path in expected_stage12_paths if (root / path).exists()]
    stage12_evidence_path = root / "docs" / "stages" / "12-50-station-selection-evidence.json"
    stage12_evidence = json.loads(stage12_evidence_path.read_text(encoding="utf-8")) if stage12_evidence_path.exists() else {}
    stage12_approved = (
        len(stage12_paths_found) == len(expected_stage12_paths)
        and stage12_evidence.get("status") == "COMPLETE — APPROVED FIXED 50-STATION PANEL"
        and stage12_evidence.get("panel_version") == PANEL_VERSION
        and len(stage12_evidence.get("panel", [])) == 50
    )
    density = _static_event_density(
        root,
        stage4,
        stage12_approved=stage12_approved,
        stage12_evidence=stage12_evidence,
        gtfs_zip=gtfs_zip,
    )
    station_rows = density.get("station_tier_rows", [])
    phase = heartbeat_phase_distribution(PRODUCTION_TIER_PLAN)
    phase_assignments = phase_assignments_for_tier_rows(station_rows) if len(station_rows) == 50 else []
    phase["phase_assignments"] = phase_assignments
    if live_diagnostics is None:
        if run_live_diagnostics and len(station_rows) == 50:
            if not additional_mdd_requests_approved:
                raise Stage13Blocked(
                    "corrected NumberOfResults diagnostic requires explicit approval for 24 additional MDD requests"
                )
            live = _run_corrected_result_limit_diagnostic(
                station_rows,
                prior_attempts=prior_live_attempts,
                additional_requests_approved=True,
            )
            previous_live = _reuse_existing_live_diagnostics(root, station_rows)
            if previous_live:
                live["bounded_concurrency_probe"] = previous_live.get("bounded_concurrency_probe", {})
                live["sequential_duration_evidence"] = previous_live.get("sequential_duration_evidence", {})
                live["historical_invalidated_sequential_records"] = previous_live.get(
                    "historical_invalidated_sequential_records", []
                )
                live["historical_invalidated_sequential_summary_by_result_limit"] = previous_live.get(
                    "historical_invalidated_sequential_summary_by_result_limit", {}
                )
        elif reuse_existing_live:
            live = _reuse_existing_live_diagnostics(root, station_rows) or {
                "status": "NOT_RUN",
                "credential_preflight": "NOT_EVALUATED",
                "http_attempts_consumed": 0,
                "max_http_attempts": MDD_HTTP_ATTEMPT_HARD_MAX,
                "raw_payloads_persisted": False,
            }
        else:
            live = {
                "status": "NOT_RUN",
                "credential_preflight": "NOT_EVALUATED",
                "http_attempts_consumed": 0,
                "max_http_attempts": MDD_HTTP_ATTEMPT_HARD_MAX,
                "raw_payloads_persisted": False,
            }
    else:
        live = dict(live_diagnostics)
    live["prior_attempts_consumed"] = int(prior_live_attempts)
    live["current_run_http_attempts_consumed"] = int(live.get("http_attempts_consumed", 0))
    live["http_attempts_consumed_total"] = int(prior_live_attempts) + int(live.get("http_attempts_consumed", 0))

    number_limit_probe = live.get("result_limit_probe", {})
    production_selection = number_limit_probe.get("production_selection", {}) if isinstance(number_limit_probe, Mapping) else {}
    technically_evaluated_number_of_results = production_selection.get(
        "technically_evaluated_number_of_results",
        production_selection.get("production_number_of_results"),
    )
    selected_number_of_results = None
    cap2_probe = live.get("bounded_concurrency_probe", {})
    cap2_evaluated = cap2_probe.get("decision") in {
        "APPROVED_MAXIMUM_CONCURRENCY_2",
        "TECHNICALLY_EVALUATED_OPTION_MAXIMUM_CONCURRENCY_2",
    }
    cadence = candidate_cadence(PRODUCTION_TIER_PLAN, status="TECHNICALLY_EVALUATED_OPTION")
    alternatives = _cadence_alternatives(station_rows, legacy_reserved_requests=LEGACY7_RESERVED_MONTHLY_REQUESTS)
    quota = quota_projection_set(
        cadence["logical_requests_per_hour"],
        legacy_reserved_requests=LEGACY7_RESERVED_MONTHLY_REQUESTS,
        diagnostic_reserved_requests=DIAGNOSTIC_RESERVE_HTTP_ATTEMPTS,
    )
    ten_percent_projection = next(item for item in quota["projections"] if item["label"] == "ten_percent_stress_sensitivity")
    quota_safety_closed = all(
        item["31_day"]["conservative_reserve_reconciliation"]["within_normal_envelope_max"]
        and item["30_day"]["conservative_reserve_reconciliation"]["within_normal_envelope_max"]
        for item in quota["projections"]
    )
    heartbeat = _heartbeat_feasibility(phase, live)
    corrected_metric_status = _corrected_diagnostic_metric_status(live)
    number_of_results_comparison = _result_limit_comparison(station_rows, live)
    cadence_boundary_review = _cadence_boundary_review(station_rows, stage12_evidence) if station_rows else {
        "status": "NOT_ESTABLISHED_STATION_ROWS_UNAVAILABLE"
    }

    blockers: list[str] = []
    if not stage12_approved:
        blockers.append("STAGE_12_PANEL_APPROVAL_REQUIRED")
    if density.get("status") != "COMPLETE":
        blockers.append("STATIC_EVENT_DENSITY_NOT_ESTABLISHED")
    if technically_evaluated_number_of_results != PRODUCTION_NUMBER_OF_RESULTS_CANDIDATE:
        blockers.append("PRODUCTION_NUMBER_OF_RESULTS_EVIDENCE_NOT_ESTABLISHED")
    if not cap2_evaluated:
        blockers.append("CONCURRENCY_BEHAVIOR_NOT_ESTABLISHED")
    if not quota_safety_closed:
        blockers.append("QUOTA_HEADROOM_NOT_ESTABLISHED")
    if len(station_rows) == 50 and len(phase_assignments) != 50:
        blockers.append("PHASE_ASSIGNMENT_NOT_ESTABLISHED")
    blockers.append("USER_DESIGN_APPROVAL_REQUIRED")
    status = "IN PROGRESS — EVIDENCE READY; USER DESIGN APPROVAL REQUIRED"
    generated_at_utc = generated_at_utc or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    result_limit_evidence = _historical_result_limit_evidence(root)
    additional_request_gate = live.get(
        "additional_request_gate",
        {
            "approval_request": ADDITIONAL_RESULT_LIMIT_APPROVAL_REQUEST,
            "required": True,
            "approved": False,
            "corrected_diagnostic_scope": CORRECTED_RESULT_LIMIT_DIAGNOSTIC_SCOPE,
            "requested_additional_http_attempts": ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
            "prior_attempts_consumed": live.get("http_attempts_consumed_total", 0),
            "cumulative_attempts_if_approved": int(live.get("http_attempts_consumed_total", 0))
            + ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
            "historical_diagnostic_hard_ceiling": MDD_HTTP_ATTEMPT_HARD_MAX,
            "historical_ceiling_changed": False,
            "additional_requests_issued": 0,
            "corrected_live_diagnostic_issued": False,
            "raw_responses_persisted": False,
        },
    )
    result_limit_evidence.update(
        {
            "decision": (
                "TECHNICALLY_EVALUATED_OPTION_REQUIRES_USER_APPROVAL"
                if technically_evaluated_number_of_results == PRODUCTION_NUMBER_OF_RESULTS_CANDIDATE
                else "NOT_ESTABLISHED"
            ),
            "technically_evaluated_number_of_results": technically_evaluated_number_of_results,
            "production_number_of_results": None,
            "provider_maximum_status": "PROVIDER_MAXIMUM_NOT_ESTABLISHED",
            "additional_request_gate": additional_request_gate,
            "corrected_metric_status": corrected_metric_status,
            "bounded_live_diagnostic": {
                "status": live.get("status"),
                "http_attempts_consumed": live.get("http_attempts_consumed_total", 0),
                "current_run_http_attempts_consumed": live.get("current_run_http_attempts_consumed", 0),
                "prior_attempts_consumed": live.get("prior_attempts_consumed", 0),
                "max_http_attempts": MDD_HTTP_ATTEMPT_HARD_MAX,
                "stop_reason": live.get("stop_reason"),
                "diagnostic_anchor_dep_arr_time": live.get("diagnostic_anchor_dep_arr_time"),
                "representatives": live.get("representatives", []),
                "result_limit_probe": number_limit_probe,
                "additional_request_gate": additional_request_gate,
            },
            "selection_limitation": "N=30 remains undecided; the provider theoretical maximum remains unknown and the corrected diagnostic produced no valid live responses because all 24 attempts ended with NETWORK_OR_DNS_FAILURE.",
        }
    )
    if live.get("historical_invalidated_sequential_records"):
        result_limit_evidence["historical_invalidated_sequential_records"] = live[
            "historical_invalidated_sequential_records"
        ]
        result_limit_evidence["historical_invalidated_sequential_summary_by_result_limit"] = live.get(
            "historical_invalidated_sequential_summary_by_result_limit", {}
        )

    remaining_limitations = [
        "PROVIDER_MAXIMUM_NOT_ESTABLISHED",
        "CURRENT_SHARED_CREDENTIAL_USAGE_NOT_OBSERVED; FULL_DOCUMENTED_LEGACY7_CADENCE_RESERVED_CONSERVATIVELY",
        "STATIC_DENSITY_IS_PLANNING_EVIDENCE_ONLY_NOT_REALTIME_RESPONSE_COVERAGE",
        "USER_DESIGN_APPROVAL_REQUIRED_FOR_NUMBER_OF_RESULTS_CADENCE_CONCURRENCY_AND_RETRY_POLICY",
        corrected_metric_status,
    ]
    user_decision_gate = {
        "status": "USER_APPROVAL_REQUIRED",
        "technical_evaluation_status": "TECHNICALLY_EVALUATED_OPTIONS_RECORDED",
        "user_approval_received": False,
        "all_material_design_choices_user_approved": False,
        "stage_13_complete_allowed": False,
        "production_number_of_results_approved": False,
        "production_concurrency_approved": False,
        "production_cadence_approved": False,
        "production_retry_policy_approved": False,
        "production_tier_membership_approved": False,
        "material_decisions": {
            "production_number_of_results": {
                "technically_evaluated_option": technically_evaluated_number_of_results,
                "user_approved_design": None,
                "approved": False,
            },
            "production_concurrency": {
                "technically_evaluated_option": MDD_CONCURRENCY_CAP if cap2_evaluated else None,
                "user_approved_design": None,
                "approved": False,
            },
            "production_cadence": {
                "technically_evaluated_option": cadence,
                "user_approved_design": None,
                "approved": False,
            },
            "production_retry_policy": {
                "technically_evaluated_option": {
                    "max_attempts": PRODUCTION_MAX_ATTEMPTS_CANDIDATE,
                    "automatic_retries": 0,
                },
                "user_approved_design": None,
                "approved": False,
            },
        },
        "approval_required_before": [
            "recording a final Stage 13 production design",
            "starting Stage 14",
            "activating Tiered50",
            "creating production database objects or Windows Scheduled Tasks",
            "modifying Power BI",
        ],
    }
    return {
        "artifact_version": "stage-13-tiered-collector-readiness-evidence-3.0",
        "record_type": "stage_13_tiered_collector_readiness_evidence",
        "generated_at_utc": generated_at_utc,
        "stage_13_status": status,
        "user_decision_gate": user_decision_gate,
        "stage_12_gate": {
            "authoritative_completion_artifact_present": stage12_approved,
            "expected_artifact_paths": expected_stage12_paths,
            "artifact_paths_found": stage12_paths_found,
            "blocker": None if stage12_approved else "STAGE_12_PANEL_APPROVAL_REQUIRED",
            "approved_panel_version": stage12_evidence.get("panel_version") if stage12_approved else None,
            "panel_station_count": len(stage12_evidence.get("panel", [])) if stage12_approved else 0,
            "approved_panel_station_ids": [row.get("source_parent_station_id") for row in stage12_evidence.get("panel", [])] if stage12_approved else [],
            "stage12_selection_rank_is_not_production_tier": True,
            "panel_membership_changed": False,
        },
        "evidence_sources": [
            "docs/PROJECT-STATUS.md",
            "docs/ROADMAP.md",
            "docs/GUARDRAILS.md",
            "docs/DECISION-LOG.md",
            "docs/stages/07-realtime-pilot-design.md",
            "docs/stages/08-small-realtime-pilot-blocked.md",
            "docs/stages/09-source-backed-matching-summary.json",
            "docs/stages/12-50-station-selection-evidence.json",
            "docs/stages/12-50-station-candidate-evidence.json",
            "docs/source-contracts/mdd-trias-request-contract.md",
            "docs/source-contracts/mdd-trias-response-contract.md",
            "docs/source-contracts/mdd-trias-raw-retention.md",
            "docs/stages/04-cologne-scope-mode-evidence.json",
            "stage03a-mdd-discovery.jsonl",
        ],
        "candidate_cadence": {
            "historical_comparison": candidate_cadence(TIERED50_CANDIDATE),
            "technically_evaluated_option": cadence,
            "user_approved_design": None,
            "cadence_selection_alternatives": alternatives,
            "selection_reason": "The measured density technically supports the 10/20/20 count split with a 5/15/30-minute cadence option, while the old 5/10/30 comparison exceeds the normal planning envelope. This is an evaluated option pending explicit user approval.",
        },
        "cadence_boundary_review": cadence_boundary_review,
        "number_of_results_comparison": number_of_results_comparison,
        "user_review_preferences": {
            "source": "explicit current user recommendation; not a final approval",
            "preferred_cadence_candidate": "8/20/22 @ 5/15/30",
            "preferred_bounded_production_concurrency": 2,
            "preferred_concurrency_condition": "subject to Stage 14 end-to-end dry-run validation",
            "preferred_production_retry_policy": {
                "max_attempts": 2,
                "bounded_retry_count": 1,
                "approved_transient_failures_only": True,
            },
            "number_of_results": "UNDECIDED_PENDING_CORRECTED_DIAGNOSTIC",
            "stage_13_complete_allowed": False,
        },
        "quota": {
            **quota,
            "technically_evaluated_option": {
                "normal_retry_policy": "MaxAttempts=1; zero automatic retries; failed targets are independently recorded and wait for the next current logical bucket",
                "user_approved_design": None,
                "evidence_supported_retry_allowance": 0,
                "historical_legacy_retry_sensitivity": _round(LEGACY_ADDITIONAL_ATTEMPT_SHARE, 9),
                "legacy_current_usage_observed": False,
                "legacy_reserved_conservatively": dict(LEGACY7_RESERVED_MONTHLY_REQUESTS),
                "diagnostic_reserved_http_attempts": DIAGNOSTIC_RESERVE_HTTP_ATTEMPTS,
                "thirty_day_nominal_conservative_total": quota["projections"][0]["30_day"]["conservative_reserve_reconciliation"]["conservative_total_http_requests"],
                "thirty_one_day_nominal_conservative_total": quota["projections"][0]["31_day"]["conservative_reserve_reconciliation"]["conservative_total_http_requests"],
                "thirty_one_day_ten_percent_stress_total": ten_percent_projection["31_day"]["conservative_reserve_reconciliation"]["conservative_total_http_requests"],
                "quota_safety_gate_closed_without_fabricated_current_usage": quota_safety_closed,
                "production_cadence_approved": False,
                "production_retry_policy_approved": False,
            },
        },
        "credential_consumers": _credential_consumers(),
        "number_of_results": result_limit_evidence,
        "static_event_density": density,
        "execution_duration_evidence": _load_duration_evidence(root),
        "retry_and_http_attempt_evidence": {
            "source_controlled_bounded_populations": [
                {
                    "population": "Stage 3A ten-station MDD discovery",
                    "source": "stage03a-mdd-discovery.jsonl",
                    "http_status_distribution": {"200": 10},
                    "successful_first_attempt_percentage": 100.0,
                    "retry_count": 0,
                },
                {
                    "population": "Stage 8 one-hour Hbf pilot",
                    "source": "docs/stages/08-small-realtime-pilot-blocked.md",
                    "http_status_distribution": {"200": 343},
                    "successful_first_attempt_percentage": 100.0,
                    "retry_count": 0,
                },
                {
                    "population": "Stage 9 source-backed acceptance",
                    "source": "docs/stages/09-source-backed-matching-summary.json",
                    "http_status_distribution": {"200": 3},
                    "successful_first_attempt_percentage": 100.0,
                    "retry_count": 0,
                },
            ],
            "legacy_historical_evidence": {
                "source": "historical Legacy quota audit snapshot 2026-09-25",
                "executions": LEGACY_EXECUTIONS,
                "http_attempts": LEGACY_HTTP_ATTEMPTS,
                "additional_retry_attempts": LEGACY_ADDITIONAL_RETRY_ATTEMPTS,
                "additional_attempt_share_of_all_http_attempts": _round(LEGACY_ADDITIONAL_ATTEMPT_SHARE, 9),
                "current_production_database_available": False,
                "interpretation": "historical retry-count evidence only; not a current Tiered50 retry rate",
            },
            "current_production_audit": {
                "ctl_mdd_collector_run_history_present": False,
                "status": "UNAVAILABLE_CURRENT_PRODUCTION_AUDIT_EVIDENCE",
                "conservative_full_legacy7_reserve_applied": True,
                "must_be_resolved_before_activation": True,
            },
            "stage13_live_diagnostic": {
                "http_attempts_consumed": live.get("http_attempts_consumed_total", 0),
                "current_run_http_attempts_consumed": live.get("current_run_http_attempts_consumed", 0),
                "prior_attempts_consumed": live.get("prior_attempts_consumed", 0),
                "max_http_attempts": MDD_HTTP_ATTEMPT_HARD_MAX,
                "retries": 0,
                "raw_payloads_persisted": False,
            },
        },
        "phase_staggering": phase,
        "heartbeat_feasibility": heartbeat,
        "sequential_vs_bounded_concurrency": {
            "decision": "TECHNICALLY_EVALUATED_OPTION_REQUIRES_USER_APPROVAL" if cap2_evaluated else "NOT_ESTABLISHED",
            "approval_state": "USER_APPROVAL_REQUIRED" if cap2_evaluated else "NOT_ESTABLISHED",
            "technically_evaluated_maximum_concurrency": MDD_CONCURRENCY_CAP if cap2_evaluated else None,
            "maximum_concurrency": None,
            "user_approved_design": False,
            "bounded_live_diagnostic": live.get("bounded_concurrency_probe", {}),
            "sequential_live_diagnostic": live.get("sequential_duration_evidence", {}),
            "historical_stage8_observed_average_wall_seconds_per_attempt": _round(3606.801 / 343, 6),
            "unbounded_concurrency": "PROHIBITED",
            "provider_scope_limit": "cap-2 result is a bounded sample decision, not a provider-wide rate-limit claim",
        },
        "overlap_and_duplicate_execution_contract": {
            "unique_execution_identity": ["SamplingTargetId", "LogicalDueBucket"],
            "logical_due_bucket": "floor(current UTC epoch seconds / 300); bucket identity is deterministic and DST-safe",
            "claim_persistence": "future durable control relation with a unique constraint on (SamplingTargetId, LogicalDueBucket); no physical table is created in Stage 13",
            "atomic_claim": "insert the claim with a unique-key conflict as the only duplicate/overlap decision; only the winning claim may issue HTTP",
            "claim_fields": [
                "SamplingTargetId",
                "LogicalDueBucket",
                "claim_token",
                "collector_run_id",
                "claimed_at_utc",
                "started_at_utc",
                "completed_at_utc",
                "status",
                "stale_after_utc",
                "error_metadata",
            ],
            "crash_safe_behavior": "a durable RUNNING/STALE_INCOMPLETE row remains observable; a later invocation does not re-execute the same historical bucket",
            "catch_up_behavior": False,
            "historical_snapshot_replay": "PROHIBITED",
            "production_scheduler_implemented": False,
        },
        "failure_isolation": {
            "decision": "TECHNICALLY_EVALUATED_REQUIREMENT_PENDING_USER_APPROVAL",
            "one_failed_station_aborts_remaining_due_targets": False,
            "per_target_fields": [
                "logical_due_bucket",
                "http_status",
                "attempt_count",
                "duration",
                "parser_status",
                "persistence_status",
                "error_metadata",
            ],
        },
        "historical_continuity": {
            "decision": "PRESERVE",
            "rules": [
                "do not delete Legacy7 realtime history",
                "preserve the existing 7 SamplingTargetIds",
                "future additions do not delete/reinsert existing targets",
                "record explicit Legacy7 versus Tiered50 sampling era/version boundary",
                "do not compare raw volumes before/after without cadence qualification",
            ],
            "cutover_performed": False,
        },
        "readiness_decision": {
            "production_cadence_approved": False,
            "production_tier_membership_approved": False,
            "production_number_of_results_approved": False,
            "production_concurrency_approved": False,
            "production_retry_policy_approved": False,
            "production_activation": "NOT_STARTED",
            "limitations": remaining_limitations,
            "blockers": blockers,
            "stage_14_started": False,
            "tiered50_activated": False,
            "power_bi_modified": False,
            "production_database_objects_created": False,
            "windows_scheduled_tasks_created": False,
        },
    }


def validate_evidence(evidence: Mapping[str, Any]) -> None:
    """Validate the Stage 13 machine-readable schema and hard boundaries."""

    required = {
        "artifact_version",
        "record_type",
        "stage_13_status",
        "user_decision_gate",
        "stage_12_gate",
        "candidate_cadence",
        "cadence_boundary_review",
        "quota",
        "number_of_results",
        "number_of_results_comparison",
        "user_review_preferences",
        "static_event_density",
        "phase_staggering",
        "heartbeat_feasibility",
        "sequential_vs_bounded_concurrency",
        "overlap_and_duplicate_execution_contract",
        "failure_isolation",
        "historical_continuity",
        "readiness_decision",
    }
    missing = sorted(required - set(evidence))
    if missing:
        raise ValueError(f"Stage 13 evidence missing required fields: {missing}")
    if evidence["stage_12_gate"].get("approved_panel_version") == PANEL_VERSION:
        if evidence["stage_12_gate"].get("panel_station_count") != 50:
            raise ValueError("approved Stage 12 panel must contain exactly 50 stations")
    if evidence["overlap_and_duplicate_execution_contract"].get("unique_execution_identity") != [
        "SamplingTargetId",
        "LogicalDueBucket",
    ]:
        raise ValueError("unique execution identity contract changed")
    if evidence["readiness_decision"].get("stage_14_started"):
        raise ValueError("Stage 14 must remain not started")
    if evidence["readiness_decision"].get("tiered50_activated"):
        raise ValueError("Tiered50 must remain inactive")
    gate = evidence["user_decision_gate"]
    required_gate_flags = {
        "production_number_of_results_approved",
        "production_concurrency_approved",
        "production_cadence_approved",
        "production_retry_policy_approved",
        "production_tier_membership_approved",
    }
    if any(gate.get(flag) is not False for flag in required_gate_flags):
        raise ValueError("Stage 13 material design approval flags must remain false until explicit user approval")
    if gate.get("all_material_design_choices_user_approved") is not False:
        raise ValueError("Stage 13 cannot be complete without explicit user approval")
    if evidence.get("stage_13_status") != "IN PROGRESS — EVIDENCE READY; USER DESIGN APPROVAL REQUIRED":
        raise ValueError("Stage 13 must remain in the reopened user-approval state")
    additional_gate = evidence["number_of_results"].get("additional_request_gate", {})
    if additional_gate.get("approval_request") != ADDITIONAL_RESULT_LIMIT_APPROVAL_REQUEST or {
        "requested_additional_http_attempts": additional_gate.get("requested_additional_http_attempts"),
        "prior_attempts_consumed": additional_gate.get("prior_attempts_consumed"),
        "cumulative_attempts_if_approved": additional_gate.get("cumulative_attempts_if_approved"),
        "historical_diagnostic_hard_ceiling": additional_gate.get("historical_diagnostic_hard_ceiling"),
    } != {
        "requested_additional_http_attempts": ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
        "prior_attempts_consumed": PRIOR_STAGE13_DIAGNOSTIC_ATTEMPTS,
        "cumulative_attempts_if_approved": PRIOR_STAGE13_DIAGNOSTIC_ATTEMPTS + ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
        "historical_diagnostic_hard_ceiling": MDD_HTTP_ATTEMPT_HARD_MAX,
    }:
        raise ValueError("corrected Stage 13 NumberOfResults request gate is not preserved")
    issued = additional_gate.get("additional_requests_issued")
    if not isinstance(issued, int) or not 0 <= issued <= ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS:
        raise ValueError("corrected Stage 13 additional request count is outside the explicit gate")
    if additional_gate.get("approved") is False and (issued != 0 or additional_gate.get("corrected_live_diagnostic_issued") is not False):
        raise ValueError("corrected Stage 13 requests were issued without approval")
    readiness = evidence["readiness_decision"]
    for flag in required_gate_flags:
        if readiness.get(flag) is not False:
            raise ValueError(f"readiness decision {flag} must remain false")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Stage 13 Tiered50 readiness evidence")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="output JSON path (default: docs/stages/13-tiered-collector-readiness-evidence.json)",
    )
    parser.add_argument(
        "--gtfs-zip",
        type=Path,
        help="exact Stage 12 GTFS ZIP; its SHA-256 must match the approved snapshot",
    )
    parser.add_argument(
        "--run-live-diagnostics",
        action="store_true",
        help="run only the corrected 24-request result-limit diagnostic after explicit approval",
    )
    parser.add_argument(
        "--approve-additional-mdd-requests",
        action="store_true",
        help="explicitly approve the separately gated 24 additional MDD requests",
    )
    parser.add_argument(
        "--prior-http-attempts",
        type=int,
        default=0,
        help="Stage 13 attempts already consumed by a prior bounded diagnostic run",
    )
    parser.add_argument(
        "--reuse-existing-live-evidence",
        action="store_true",
        help="reuse the current sanitized live diagnostic without issuing new MDD requests",
    )
    args = parser.parse_args()
    if args.prior_http_attempts < 0:
        parser.error("--prior-http-attempts must be non-negative")
    if args.run_live_diagnostics and not args.approve_additional_mdd_requests:
        parser.error("--run-live-diagnostics requires --approve-additional-mdd-requests")
    evidence = build_evidence(
        gtfs_zip=args.gtfs_zip,
        run_live_diagnostics=args.run_live_diagnostics,
        prior_live_attempts=args.prior_http_attempts,
        reuse_existing_live=args.reuse_existing_live_evidence,
        additional_mdd_requests_approved=args.approve_additional_mdd_requests,
    )
    validate_evidence(evidence)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
