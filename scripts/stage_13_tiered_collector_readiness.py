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
import errno
import hashlib
import json
import math
import os
import re
import socket
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
MDD_HOSTNAME = "mdd.gorheinland.com"
MDD_PORT = 443
REQUESTOR_REF = "CologneTransitIntelligence"
MDD_DIAGNOSTIC_POLICY = "STAGE13_TIERED_READINESS_DIAGNOSTIC_V1"
HISTORICAL_INITIAL_DIAGNOSTIC_BUDGET = 100
MDD_RESULT_LIMITS = (5, 10, 20, 30)
# Stage 13 records the approved design, while the live diagnostic remains a
# bounded historical sample.  No production collector is implemented here.
PRODUCTION_MAXIMUM_CONCURRENCY = 2
DIAGNOSTIC_CONCURRENCY_CAP = PRODUCTION_MAXIMUM_CONCURRENCY
PRODUCTION_MAX_ATTEMPTS = 2
PRODUCTION_MAX_AUTOMATIC_RETRIES = PRODUCTION_MAX_ATTEMPTS - 1
PRODUCTION_RETRY_RESERVE_RATE = 0.10
AUTOMATIC_RETRY_PROTECTION_BOUNDARY = 207_500
ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS = 24
PRIOR_STAGE13_DIAGNOSTIC_ATTEMPTS = 90
AUTHORIZED_CUMULATIVE_CLIENT_ATTEMPT_CEILING = (
    PRIOR_STAGE13_DIAGNOSTIC_ATTEMPTS + ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS
)
PROVIDER_QUOTA_UNKNOWN_BECAUSE_NO_HTTP_RESPONSE = (
    "UNKNOWN_NOT_CONFIRMED_BECAUSE_FAILURE_OCCURRED_BEFORE_HTTP_RESPONSE"
)
PROVIDER_QUOTA_NOT_INFERRED_FROM_RESPONSE_COUNT = (
    "NOT_INFERRED_FROM_HTTP_RESPONSE_COUNT"
)
SAFE_TRANSPORT_FAILURE_CATEGORIES = frozenset(
    {
        "DNS_RESOLUTION_FAILED",
        "NETWORK_UNREACHABLE",
        "CONNECTION_REFUSED",
        "TLS_FAILURE",
        "PROXY_OR_ENVIRONMENT_FAILURE",
        "TIMEOUT",
        "OTHER_NETWORK_FAILURE",
    }
)
LEGACY_BROAD_TRANSPORT_FAILURE = "NETWORK_OR_DNS_FAILURE"
ADDITIONAL_RESULT_LIMIT_APPROVAL_REQUEST = (
    "The corrected NumberOfResults coverage diagnostic requires 24 additional client-side "
    "MDD request invocations. They are separately authorized from the historical 100-attempt "
    "budget; provider quota consumption will not be inferred if an invocation fails before "
    "an HTTP response. Do you approve these 24 invocations?"
)
CORRECTED_RESULT_LIMIT_DIAGNOSTIC_SCOPE = "six deterministic representatives × N=5/N=10/N=20/N=30; sequential only"
LIVE_RERUN_APPROVAL_REQUEST = (
    "The prior 24-invocation authorization has been exercised. After reviewing the offline "
    "network-preflight and fail-fast changes, do you explicitly approve one new corrected "
    "diagnostic with at most 24 client-side MDD request invocations?"
)

MONTHLY_CEILING = 250_000
NORMAL_ENVELOPE_MIN = 207_500
NORMAL_ENVELOPE_MAX = 210_000
HEARTBEAT_MINUTES = 5
HEARTBEAT_SECONDS = HEARTBEAT_MINUTES * 60
PRODUCTION_REQUEST_TIMEOUT_SECONDS = 30
LEGACY_EXECUTIONS = 1_599
LEGACY_HTTP_ATTEMPTS = 1_629
LEGACY_ADDITIONAL_RETRY_ATTEMPTS = 35
LEGACY_ADDITIONAL_ATTEMPT_SHARE = LEGACY_ADDITIONAL_RETRY_ATTEMPTS / LEGACY_HTTP_ATTEMPTS
LEGACY7_RESERVED_MONTHLY_REQUESTS = {30: 8_640, 31: 8_928}
DIAGNOSTIC_RESERVE_HTTP_ATTEMPTS = HISTORICAL_INITIAL_DIAGNOSTIC_BUDGET
FORWARD_WINDOWS_MINUTES = (5, 10, 15, 30)
# The historical bounded diagnostic used fixed N30.  The approved production
# policy is explicitly tier-specific and must not be collapsed to fixed N30.
HISTORICAL_DIAGNOSTIC_RESULT_LIMIT = 30
PRODUCTION_NUMBER_OF_RESULTS_BY_TIER = {"A": 20, "B": 30, "C": 30}
CORRECTED_LIVE_FORWARD_COVERAGE_LIMITATION = (
    "CORRECTED_LIVE_FORWARD_COVERAGE_UNAVAILABLE_DUE_TO_EXECUTION_ENVIRONMENT"
)

PHASE_ASSIGNMENT_IDENTITY = "source_parent_station_id / parent StopPointRef"
PHASE_ASSIGNMENT_HASH = "SHA-256(panel version + tier + source_parent_station_id)"
PHASE_INDEX_MATERIALIZATION_STATUS = "DESIGN_ONLY_STAGE14_MATERIALIZATION_REQUIRED"
SKIPPED_HEARTBEAT_BUDGET = "SKIPPED_HEARTBEAT_BUDGET"
RETRY_SKIPPED_HEARTBEAT_BUDGET = "RETRY_SKIPPED_HEARTBEAT_BUDGET"

APPROVED_TRANSIENT_HTTP_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
APPROVED_TRANSIENT_TRANSPORT_FAILURE_CATEGORIES = frozenset(
    {
        "CONNECTION_REFUSED",
        "NETWORK_UNREACHABLE",
        "PROXY_OR_ENVIRONMENT_FAILURE",
        "TIMEOUT",
        "TLS_FAILURE",
        "OTHER_NETWORK_FAILURE",
    }
)

# A deterministic, persisted phase-capacity order is part of the approved
# design.  The C order is deliberately a permutation of the balanced counts
# so the combined six-heartbeat load is 19/18/18/18/19/18.
FINAL_PHASE_CAPACITIES = {
    "A": (8,),
    "B": (7, 7, 6),
    "C": (4, 3, 4, 3, 4, 4),
}

# Retained only as the historical comparison candidate. It is never used for
# the approved tier assignment or runtime phase materialization.
HISTORICAL_TIERED50_PLAN = (
    {"tier": "A", "station_count": 10, "interval_minutes": 5},
    {"tier": "B", "station_count": 20, "interval_minutes": 10},
    {"tier": "C", "station_count": 20, "interval_minutes": 30},
)

# The final user-approved production design.  This is readiness evidence only;
# Stage 14 must still implement and validate it end to end.
PRODUCTION_TIER_PLAN = (
    {"tier": "A", "station_count": 8, "interval_minutes": 5},
    {"tier": "B", "station_count": 20, "interval_minutes": 15},
    {"tier": "C", "station_count": 22, "interval_minutes": 30},
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
    tiers: Sequence[Mapping[str, int]] = PRODUCTION_TIER_PLAN,
    *,
    status: str = "APPROVED_STAGE13_DESIGN",
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
    retry_reserve = math.ceil(base * additional_attempt_rate)
    return {
        "days": days,
        "base_logical_requests": base,
        "additional_attempt_rate": _round(additional_attempt_rate, 9),
        "retry_reserve_requests": retry_reserve,
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
            "kind": "historical_comparison_scenario",
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
            "label": "approved_normal_retry_reserve",
            "kind": "approved_planning_reserve",
            "additional_attempt_rate": PRODUCTION_RETRY_RESERVE_RATE,
            "approved_production_reserve": True,
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
            "The approved production retry policy uses MaxAttempts=2, meaning one initial attempt plus at most one automatic retry for approved transient failures.",
            "The normal planning reserve is 10% of nominal Tiered50 logical requests; it is a quota-planning reserve, not an observed Tiered50 retry rate.",
            "Automatic retries must be blocked when projected/current monthly consumption would exceed the 207500 conservative protection boundary.",
            "The full documented Legacy7 monthly cadence is reserved conservatively because current shared-account usage is unavailable.",
            "Repository diagnostics are inactive after closeout; the Stage 13 diagnostic hard budget is reserved for reconciliation only.",
        ],
    }


def retry_eligible(
    *,
    http_status: int | None = None,
    transport_failure_category: str | None = None,
) -> bool:
    """Return whether the approved policy permits one automatic retry."""

    if http_status in {401, 403}:
        return False
    if http_status is not None:
        return http_status in APPROVED_TRANSIENT_HTTP_STATUS_CODES
    return transport_failure_category in APPROVED_TRANSIENT_TRANSPORT_FAILURE_CATEGORIES


def retry_policy_contract() -> dict[str, Any]:
    """Return the approved retry contract without implementing a collector."""

    return {
        "max_attempts": PRODUCTION_MAX_ATTEMPTS,
        "automatic_retry_count_max": PRODUCTION_MAX_AUTOMATIC_RETRIES,
        "initial_attempt_included": True,
        "approved_transient_http_statuses": sorted(APPROVED_TRANSIENT_HTTP_STATUS_CODES),
        "approved_transient_transport_failure_categories": sorted(
            APPROVED_TRANSIENT_TRANSPORT_FAILURE_CATEGORIES
        ),
        "respect_retry_after_for_http_429": True,
        "retry_after_must_fit_current_heartbeat_deadline": True,
        "retry_budget_exhaustion_outcome": RETRY_SKIPPED_HEARTBEAT_BUDGET,
        "do_not_retry_http_statuses": [401, 403],
        "do_not_retry_deterministic_request_contract_errors": True,
        "do_not_retry_deterministic_parser_schema_errors": True,
        "do_not_retry_deterministic_trias_application_errors": True,
        "one_failed_station_does_not_abort_other_due_targets": True,
        "implementation_status": "DESIGN_ONLY_STAGE14_IMPLEMENTATION_REQUIRED",
    }


def heartbeat_degraded_mode_contract() -> dict[str, Any]:
    """Return the bounded dispatcher contract for degraded execution.

    Stage 13 records the deadline and outcome contract only.  It does not
    implement a dispatcher, choose a persistence/cleanup margin, or change
    the production per-attempt timeout.
    """

    return {
        "dispatcher_invocation_owns_only_current_logical_heartbeat": True,
        "heartbeat_execution_budget_seconds": HEARTBEAT_SECONDS,
        "production_work_must_not_extend_into_later_logical_buckets": True,
        "initial_attempt_start_rule": (
            "start only when the remaining current-heartbeat execution budget is at least the "
            "approved per-attempt timeout"
        ),
        "retry_start_rule": (
            "start only when the remaining current-heartbeat execution budget can contain both "
            "Retry-After and the approved per-attempt timeout"
        ),
        "in_flight_operation_deadline_contract": (
            "an in-flight operation must obey the dispatcher heartbeat deadline and cancellation contract"
        ),
        "target_budget_exhaustion_outcome": SKIPPED_HEARTBEAT_BUDGET,
        "retry_budget_exhaustion_outcome": RETRY_SKIPPED_HEARTBEAT_BUDGET,
        "budget_outcomes_trigger_catch_up": False,
        "budget_outcome_classification": "COLLECTION_QUALITY_OUTCOME_NOT_SERVICE_OPERATIONAL_CONCLUSION",
        "per_target_budget_isolation": True,
        "budget_exhaustion_does_not_abort_unrelated_targets": True,
        "retry_after_cannot_cross_heartbeat_deadline": True,
        "http_429_retry_after_rule": (
            "respect Retry-After; do not sleep or retry past the current heartbeat deadline; "
            "record RETRY_SKIPPED_HEARTBEAT_BUDGET when it cannot fit"
        ),
        "expired_dispatcher_next_heartbeat_behavior": (
            "an expired dispatcher cannot execute its prior bucket; the next heartbeat may execute "
            "only its current logical bucket"
        ),
        "expired_dispatcher_cannot_execute_prior_bucket": True,
        "persistence_cleanup_safety_margin": {
            "stage13_concrete_seconds": None,
            "required_before_stage14_activation": True,
            "requirement": (
                "Stage 14 must determine and validate a concrete nonzero persistence/cleanup safety "
                "margin with end-to-end dry-run measurements before activation"
            ),
            "arbitrary_stage13_duration_invented": False,
        },
        "implementation_status": "DESIGN_ONLY_STAGE14_IMPLEMENTATION_REQUIRED",
    }


def dispatcher_heartbeat_budget_decision(
    elapsed_seconds: float,
    *,
    retry: bool = False,
    retry_after_seconds: float = 0.0,
    operation_timeout_seconds: int = PRODUCTION_REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Decide whether one attempt may start before the current deadline.

    This is an offline contract helper, not a network operation.  It models
    the approved 30-second request timeout and leaves any Stage 14
    persistence/cleanup margin to end-to-end measurement.
    """

    elapsed = float(elapsed_seconds)
    retry_after = float(retry_after_seconds)
    timeout = int(operation_timeout_seconds)
    if not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError("elapsed_seconds must be a finite non-negative number")
    if not math.isfinite(retry_after) or retry_after < 0:
        raise ValueError("retry_after_seconds must be a finite non-negative number")
    if timeout <= 0:
        raise ValueError("operation_timeout_seconds must be positive")

    remaining = max(0.0, HEARTBEAT_SECONDS - elapsed)
    required = float(timeout) + (retry_after if retry else 0.0)
    allowed = remaining >= required
    return {
        "retry": bool(retry),
        "retry_after_seconds": _round(retry_after, 3),
        "operation_timeout_seconds": timeout,
        "heartbeat_execution_budget_seconds": HEARTBEAT_SECONDS,
        "elapsed_seconds": _round(elapsed, 3),
        "remaining_heartbeat_budget_seconds": _round(remaining, 3),
        "required_remaining_budget_seconds": _round(required, 3),
        "start_allowed": allowed,
        "outcome": None
        if allowed
        else RETRY_SKIPPED_HEARTBEAT_BUDGET
        if retry
        else SKIPPED_HEARTBEAT_BUDGET,
        "retry_after_respected": bool(retry),
        "cannot_cross_current_heartbeat_deadline": True,
    }


def automatic_retry_guardrail(
    current_month_requests: int,
    projected_automatic_retry_requests: int,
    *,
    protection_boundary: int = AUTOMATIC_RETRY_PROTECTION_BOUNDARY,
) -> dict[str, Any]:
    """Evaluate the Stage 14 automatic-retry quota protection boundary."""

    current = int(current_month_requests)
    projected = int(projected_automatic_retry_requests)
    if current < 0 or projected < 0:
        raise ValueError("monthly request counts must be non-negative")
    projected_total = current + projected
    return {
        "current_month_requests": current,
        "projected_automatic_retry_requests": projected,
        "projected_monthly_consumption": projected_total,
        "automatic_retry_protection_boundary": int(protection_boundary),
        "automatic_retries_allowed": projected_total <= int(protection_boundary),
        "over_boundary_action": "BLOCK_AUTOMATIC_RETRY" if projected_total > int(protection_boundary) else "ALLOW_WITHIN_BOUNDARY",
        "implementation_status": "DESIGN_ONLY_STAGE14_GUARDRAIL_REQUIRED",
    }


def _phase_slot_count(interval_minutes: int) -> int:
    if interval_minutes <= 0 or interval_minutes % HEARTBEAT_MINUTES != 0:
        raise ValueError("cadence intervals must be positive multiples of the heartbeat")
    return interval_minutes // HEARTBEAT_MINUTES


def _balanced_counts(total: int, slots: int) -> list[int]:
    base, remainder = divmod(total, slots)
    return [base + (1 if index < remainder else 0) for index in range(slots)]


def _phase_capacities_for_tier(
    tier: Mapping[str, int],
    *,
    station_count: int | None = None,
) -> list[int]:
    """Return the approved phase order when the final plan is in use."""

    tier_name = str(tier["tier"])
    expected_count = int(tier["station_count"])
    actual_count = expected_count if station_count is None else int(station_count)
    phase_count = _phase_slot_count(int(tier["interval_minutes"]))
    approved = FINAL_PHASE_CAPACITIES.get(tier_name)
    if (
        approved is not None
        and actual_count == expected_count
        and len(approved) == phase_count
        and sum(approved) == expected_count
    ):
        return list(approved)
    return _balanced_counts(actual_count, phase_count)


def heartbeat_phase_distribution(
    tiers: Sequence[Mapping[str, int]] = PRODUCTION_TIER_PLAN,
) -> dict[str, Any]:
    """Reconcile deterministic phase offsets over the cadence super-cycle."""

    tier_phase_counts: dict[str, list[int]] = {}
    super_cycle_steps = 1
    for tier in tiers:
        slots = _phase_slot_count(int(tier["interval_minutes"]))
        super_cycle_steps = math.lcm(super_cycle_steps, slots)
        tier_phase_counts[str(tier["tier"])] = _phase_capacities_for_tier(tier)

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
            "assignment_key": PHASE_ASSIGNMENT_IDENTITY,
            "assignment_hash": PHASE_ASSIGNMENT_HASH,
            "phase_assignment_finalized_in_stage13": True,
            "production_phase_index_persisted": False,
            "phase_index_must_be_persisted_in_stage14": True,
            "independent_of_mutable_rank_order": True,
            "balanced_phase_rule": "sort by stable identity hash and fill the persisted deterministic phase capacities in order",
            "approved_phase_capacity_order": {
                tier: list(capacities)
                for tier, capacities in FINAL_PHASE_CAPACITIES.items()
            },
            "optimal_total_distribution": [19, 18, 18, 18, 19, 18],
            "materialization_status": PHASE_INDEX_MATERIALIZATION_STATUS,
            "stage14_must_materialize_without_recomputing_from_database_ids": True,
        },
    }


def deterministic_phase_index(source_parent_station_id: str, phase_count: int) -> int:
    """Return a stable candidate phase index from a parent StopPointRef."""

    if not source_parent_station_id:
        raise ValueError("source_parent_station_id must not be empty")
    if phase_count <= 0:
        raise ValueError("phase_count must be positive")
    digest = hashlib.sha256(source_parent_station_id.encode("utf-8")).digest()
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
        capacities = _phase_capacities_for_tier(
            cadence,
            station_count=len(rows_by_tier[tier_name]),
        )
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
                        "phase_assignment_identity": row["source_parent_station_id"],
                        "phase_assignment_identity_type": PHASE_ASSIGNMENT_IDENTITY,
                        "source_parent_station_id": row["source_parent_station_id"],
                        "station": row["station_name"],
                        "panel_version": panel_version,
                        "production_tier": tier_name,
                        "interval_minutes": interval,
                        "phase_index": phase_index,
                        "phase_count": phase_count,
                        "phase_capacity": capacities[phase_index],
                        "assignment_basis": PHASE_ASSIGNMENT_HASH,
                    }
                )
            cursor += capacity
    return sorted(assignments, key=lambda row: (row["production_tier"], row["phase_assignment_identity"]))


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


def _annotate_approved_density_rows(
    station_rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Reapply the approved density tiers and result-limit policy to retained rows."""

    updated, tier_assignment = _assign_production_tiers(station_rows)
    for row in updated:
        tier_name = str(row["production_tier"])
        tier = next(item for item in PRODUCTION_TIER_PLAN if str(item["tier"]) == tier_name)
        interval = int(tier["interval_minutes"])
        row["approved_cadence_interval_minutes"] = interval
        row["approved_number_of_results"] = PRODUCTION_NUMBER_OF_RESULTS_BY_TIER[tier_name]
        row["historical_diagnostic_result_limit"] = HISTORICAL_DIAGNOSTIC_RESULT_LIMIT
        row["approved_static_support_basis"] = (
            "station-level P95 and observed station-day window maximum recalculated from retained exact-feed evidence"
        )
    final_static_support = _tier_specific_static_fit(
        updated,
        PRODUCTION_TIER_PLAN,
        PRODUCTION_NUMBER_OF_RESULTS_BY_TIER,
    )
    return updated, tier_assignment, final_static_support


def _retained_stage13_density(root: Path) -> dict[str, Any] | None:
    """Recalculate final density outputs from the retained Stage 13 rows offline."""

    path = root / "docs" / "stages" / "13-tiered-collector-readiness-evidence.json"
    if not path.exists():
        return None
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    retained = existing.get("static_event_density", {})
    raw_rows = retained.get("station_tier_rows", [])
    source_snapshot = retained.get("source_snapshot", {})
    if (
        retained.get("status") != "COMPLETE"
        or len(raw_rows) != 50
        or source_snapshot.get("feed_version") != STAGE12_FEED_VERSION
        or source_snapshot.get("zip_sha256") != STAGE12_ZIP_SHA256
    ):
        return None
    rows, tier_assignment, final_static_support = _annotate_approved_density_rows(
        json.loads(json.dumps(raw_rows))
    )
    recalculated = json.loads(json.dumps(retained))
    recalculated.update(
        {
            "status": "COMPLETE",
            "blockers": [],
            "station_tier_rows": rows,
            "tier_assignment": tier_assignment,
            "final_approved_static_support": final_static_support,
            "recalculation": {
                "basis": "retained Stage 13 station-level exact-feed evidence",
                "source_path": "docs/stages/13-tiered-collector-readiness-evidence.json",
                "network_access_required": False,
                "gtfs_zip_reopened": False,
                "stage12_panel_membership_changed": False,
            },
            "coverage_conclusion": (
                "The retained exact static snapshot supports deterministic density-based final tier membership and "
                "the approved A20/B30/C30 static planning limits; it does not guarantee realtime response coverage."
            ),
        }
    )
    recalculated["source_snapshot"] = {
        **source_snapshot,
        "retained_stage13_source_evidence": "docs/stages/13-tiered-collector-readiness-evidence.json",
        "recalculated_offline_for_final_stage13_design": True,
    }
    recalculated["analysis_contract"] = {
        **recalculated.get("analysis_contract", {}),
        "final_tier_membership_basis": "density ranks 1–8 / 9–28 / 29–50 from retained exact-feed metrics",
        "final_number_of_results_policy": dict(PRODUCTION_NUMBER_OF_RESULTS_BY_TIER),
        "final_static_support_recalculated": True,
    }
    return recalculated


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

    del stage4  # retained in the signature for callers of the prior audit module
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
        retained = _retained_stage13_density(root)
        if retained is not None:
            return retained
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
            "historical_diagnostic_result_limit": HISTORICAL_DIAGNOSTIC_RESULT_LIMIT,
            "historical_diagnostic_result_limit_static_coverage": {
                str(window): {
                    "active_service_days_at_or_below_limit": sum(
                        value <= HISTORICAL_DIAGNOSTIC_RESULT_LIMIT for value in daily_maxima[window]
                    ),
                    "active_service_day_count": len(daily_maxima[window]),
                    "percentage_at_or_below_limit": _round(
                        (
                            sum(value <= HISTORICAL_DIAGNOSTIC_RESULT_LIMIT for value in daily_maxima[window])
                            / len(daily_maxima[window])
                            * 100
                        )
                        if daily_maxima[window]
                        else None,
                        3,
                    ),
                    "p95_at_or_below_limit": (
                        window_statistics[str(window)]["p95"] is not None
                        and window_statistics[str(window)]["p95"] <= HISTORICAL_DIAGNOSTIC_RESULT_LIMIT
                    ),
                }
                for window in FORWARD_WINDOWS_MINUTES
            },
        }
        station_rows.append(row)

    station_rows, tier_assignment, final_static_support = _annotate_approved_density_rows(station_rows)
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
        "coverage_conclusion": "The exact static snapshot supports deterministic density-based final tier membership and the approved A20/B30/C30 static planning limits; it does not guarantee realtime response coverage.",
        "final_approved_static_support": final_static_support,
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


def _safe_transport_failure_category(value: Any) -> str | None:
    """Return an allow-listed transport category without retaining details."""

    if value is None:
        return None
    text = str(value)
    if text in SAFE_TRANSPORT_FAILURE_CATEGORIES:
        return text
    # The earlier 24-attempt artifact retained only this broad label.  Its raw
    # exception details were intentionally not retained, so it cannot now be
    # safely narrowed to DNS, TLS, proxy, or another subtype.
    if text == LEGACY_BROAD_TRANSPORT_FAILURE:
        return "OTHER_NETWORK_FAILURE"
    return "OTHER_NETWORK_FAILURE"


def _network_dns_preflight(endpoint: str = ENDPOINT) -> dict[str, Any]:
    """Resolve the MDD host locally without creating an HTTP request.

    Only non-secret facts are returned.  In particular, no API key, headers,
    proxy configuration, exception text, or resolved address is retained.
    """

    del endpoint  # The Stage 13 endpoint is fixed by the request contract.
    hostname = MDD_HOSTNAME
    port = MDD_PORT
    try:
        addresses = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        families = sorted(
            {
                "IPv4" if family == socket.AF_INET else "IPv6" if family == socket.AF_INET6 else "OTHER"
                for family, _socktype, _proto, _canonname, _sockaddr in addresses
            }
        )
    except socket.gaierror:
        return {
            "status": "DNS_RESOLUTION_FAILED",
            "hostname": hostname,
            "port": port,
            "resolved_address_count": 0,
            "address_families": [],
            "safe_failure_category": "DNS_RESOLUTION_FAILED",
        }
    except OSError as error:
        # The resolver did not provide a usable result.  Keep the public
        # preflight status deterministic and expose only the safe category.
        category = "OTHER_NETWORK_FAILURE"
        if getattr(error, "errno", None) in {errno.ENETUNREACH, errno.EHOSTUNREACH}:
            category = "NETWORK_UNREACHABLE"
        return {
            "status": "DNS_RESOLUTION_FAILED",
            "hostname": hostname,
            "port": port,
            "resolved_address_count": 0,
            "address_families": [],
            "safe_failure_category": category,
        }
    if not addresses:
        return {
            "status": "DNS_RESOLUTION_FAILED",
            "hostname": hostname,
            "port": port,
            "resolved_address_count": 0,
            "address_families": [],
            "safe_failure_category": "DNS_RESOLUTION_FAILED",
        }
    return {
        "status": "DNS_RESOLUTION_OK",
        "hostname": hostname,
        "port": port,
        "resolved_address_count": len(addresses),
        "address_families": families,
        "safe_failure_category": None,
    }


def _request_accounting(
    client_request_attempts_issued: int,
    http_responses_received: int,
    *,
    conservative_quota_accounting_attempts: int | None = None,
) -> dict[str, Any]:
    """Separate client invocation, response, and quota-accounting concepts."""

    client_attempts = int(client_request_attempts_issued)
    responses = int(http_responses_received)
    if client_attempts < 0 or responses < 0 or responses > client_attempts:
        raise ValueError("request accounting counts are inconsistent")
    conservative = client_attempts if conservative_quota_accounting_attempts is None else int(
        conservative_quota_accounting_attempts
    )
    if conservative < 0:
        raise ValueError("conservative quota accounting count must be non-negative")
    return {
        "client_request_attempts_issued": client_attempts,
        "http_responses_received": responses,
        "confirmed_provider_http_attempts": responses,
        "provider_quota_consumption_status": (
            PROVIDER_QUOTA_UNKNOWN_BECAUSE_NO_HTTP_RESPONSE
            if responses == 0 and client_attempts > 0
            else PROVIDER_QUOTA_NOT_INFERRED_FROM_RESPONSE_COUNT
            if client_attempts > 0
            else "NO_CLIENT_REQUEST_INVOCATIONS_ISSUED"
        ),
        "conservative_quota_accounting_attempts": conservative,
    }


def _normalize_transport_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Upgrade a retained sanitized record to the safe transport taxonomy."""

    updated = dict(record)
    existing = updated.get("transport_failure")
    category = _safe_transport_failure_category(existing)
    if category is not None:
        updated["transport_failure"] = category
        updated["transport_failure_category"] = category
        if existing == LEGACY_BROAD_TRANSPORT_FAILURE:
            updated["legacy_transport_failure_label"] = LEGACY_BROAD_TRANSPORT_FAILURE
        if updated.get("error_classification") == LEGACY_BROAD_TRANSPORT_FAILURE:
            updated["error_classification"] = category
    elif "transport_failure_category" not in updated:
        updated["transport_failure_category"] = None
    return updated


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
        "transport_failure": _safe_transport_failure_category(result.failure),
        "transport_failure_category": _safe_transport_failure_category(result.failure),
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
        record["error_classification"] = _safe_transport_failure_category(result.failure)
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
    accounting = _request_accounting(
        len(records),
        sum(row.get("http_status") is not None for row in records),
        conservative_quota_accounting_attempts=len(records),
    )
    return {
        **accounting,
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
        "transport_failure_category_distribution": dict(
            sorted(
                Counter(
                    str(row.get("transport_failure_category"))
                    for row in records
                    if row.get("transport_failure_category") is not None
                ).items()
            )
        ),
    }


def _run_live_diagnostics(
    station_rows: Sequence[Mapping[str, Any]],
    *,
    api_key: str | None = None,
    include_concurrency_probe: bool = True,
    attempt_budget: int | None = None,
) -> dict[str, Any]:
    """Run a sanitized diagnostic with zero-request preflight and fail-fast smoke."""

    if attempt_budget is None:
        attempt_budget = HISTORICAL_INITIAL_DIAGNOSTIC_BUDGET
    if attempt_budget <= 0:
        raise ValueError("attempt_budget must be positive")

    empty_accounting = _request_accounting(0, 0, conservative_quota_accounting_attempts=0)
    try:
        import stage_08_mdd_realtime_pilot as stage8
    except Exception as exc:  # pragma: no cover - import failure is environment-specific
        return {
            "status": "BLOCKED_RUNTIME_IMPORT_FAILURE",
            "credential_preflight": "NOT_EVALUATED",
            "network_preflight": {"status": "NOT_EVALUATED"},
            **empty_accounting,
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
            "network_preflight": {"status": "NOT_EVALUATED"},
            **empty_accounting,
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

    network_preflight = _network_dns_preflight()
    static_support = {str(number): _static_limit_support(station_rows, number) for number in MDD_RESULT_LIMITS}
    empty_summaries = {
        str(number): _summary_for_records([]) for number in MDD_RESULT_LIMITS
    }
    smoke_gate: dict[str, Any] = {
        "status": "NOT_RUN",
        "counts_inside_authorized_budget": True,
        "policy": "first authorized request is a smoke request; only a valid HTTP/TRIAS response permits the remaining matrix",
        "provider_http_error_policy": "STOP_ON_FIRST_PROVIDER_HTTP_ERROR; retain HTTP status and do not classify it as transport failure",
        "client_request_attempts_issued": 0,
        "http_responses_received": 0,
    }
    if network_preflight.get("status") != "DNS_RESOLUTION_OK":
        return {
            "status": "NOT_RUN_NETWORK_PREFLIGHT_FAILED",
            "credential_preflight": preflight_label,
            "network_preflight": network_preflight,
            "execution_environment_suitable_for_live_diagnostic": False,
            "representatives": representatives,
            "result_limit_probe": {
                "requested_result_limits": list(MDD_RESULT_LIMITS),
                "sequential_records": [],
                "summary_by_result_limit": empty_summaries,
                "static_cadence_window_support_by_result_limit": static_support,
                "smoke_gate": smoke_gate,
                "production_selection": {
                    "decision": "NOT_ESTABLISHED",
                    "technically_evaluated_number_of_results": None,
                    "production_number_of_results": None,
                    "user_approved_design": False,
                    "provider_maximum_status": "PROVIDER_MAXIMUM_NOT_ESTABLISHED",
                },
            },
            **empty_accounting,
            "current_run_attempt_budget": attempt_budget,
            "stop_reason": "DNS_RESOLUTION_FAILED_ZERO_MDD_REQUESTS_ISSUED",
            "diagnostic_validity": "INVALID_FOR_NUMBER_OF_RESULTS_DECISION",
            "raw_payloads_persisted": False,
            "request_headers_persisted": False,
            "api_key_persisted": False,
        }

    if not candidates:
        return {
            "status": "NOT_RUN_NO_DIAGNOSTIC_REPRESENTATIVES",
            "credential_preflight": preflight_label,
            "network_preflight": network_preflight,
            "execution_environment_suitable_for_live_diagnostic": True,
            "representatives": representatives,
            "result_limit_probe": {
                "requested_result_limits": list(MDD_RESULT_LIMITS),
                "sequential_records": [],
                "summary_by_result_limit": empty_summaries,
                "static_cadence_window_support_by_result_limit": static_support,
                "smoke_gate": smoke_gate,
                "production_selection": {
                    "decision": "NOT_ESTABLISHED",
                    "technically_evaluated_number_of_results": None,
                    "production_number_of_results": None,
                    "user_approved_design": False,
                    "provider_maximum_status": "PROVIDER_MAXIMUM_NOT_ESTABLISHED",
                },
            },
            **empty_accounting,
            "current_run_attempt_budget": attempt_budget,
            "stop_reason": "no deterministic diagnostic representatives available",
            "diagnostic_validity": "INVALID_FOR_NUMBER_OF_RESULTS_DECISION",
            "raw_payloads_persisted": False,
            "request_headers_persisted": False,
            "api_key_persisted": False,
        }

    anchor = stage8.trias_utc(stage8.utc_now())
    attempts = 0
    stopped = False
    stop_reason: str | None = None

    def execute(candidate: Mapping[str, Any], number_of_results: int) -> Any | None:
        nonlocal attempts, stop_reason
        if stopped or attempts >= attempt_budget:
            stop_reason = stop_reason or "CURRENT_RUN_CLIENT_ATTEMPT_BUDGET_REACHED"
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
        return result

    def stop_reason_for_record(record: Mapping[str, Any], *, smoke: bool) -> str | None:
        phase = "SMOKE_GATE" if smoke else "MATRIX"
        category = record.get("transport_failure_category") or record.get("transport_failure")
        if category in SAFE_TRANSPORT_FAILURE_CATEGORIES:
            return f"{phase}_STOPPED_ENVIRONMENT_FAILURE_{category}"
        if record.get("http_status") in {401, 403}:
            return f"{phase}_STOPPED_HTTP_401_OR_403"
        if record.get("http_status") == 429:
            return f"{phase}_STOPPED_PROVIDER_HTTP_429_EXPLICIT_POLICY"
        if isinstance(record.get("http_status"), int) and record["http_status"] >= 500:
            return f"{phase}_STOPPED_PROVIDER_HTTP_5XX_EXPLICIT_POLICY"
        if record.get("error_classification") == "RESPONSE_SECRET_DETECTED_NOT_PERSISTED":
            return f"{phase}_STOPPED_SECRET_DETECTED_NOT_PERSISTED"
        if smoke and not (
            record.get("http_status") is not None
            and 200 <= int(record["http_status"]) < 300
            and record.get("parser_success")
            and record.get("valid_trias_application_response")
        ):
            return "SMOKE_GATE_STOPPED_INVALID_HTTP_OR_TRIAS_RESPONSE"
        if record.get("http_status") is not None and not 200 <= int(record["http_status"]) < 300:
            return f"{phase}_STOPPED_PROVIDER_HTTP_ERROR_EXPLICIT_POLICY"
        return None

    sequential_records: list[dict[str, Any]] = []
    for candidate_index, candidate in enumerate(candidates):
        for result_limit_index, number_of_results in enumerate(MDD_RESULT_LIMITS):
            is_smoke = candidate_index == 0 and result_limit_index == 0
            result = execute(candidate, number_of_results)
            if result is None:
                break
            record = _probe_record(
                stage8,
                result,
                candidate,
                number_of_results=number_of_results,
                dep_arr_time=anchor,
                execution_mode="smoke_gate" if is_smoke else "sequential_result_limit_comparison",
            )
            record["representative_role"] = candidate["representative_role"]
            sequential_records.append(record)
            if is_smoke:
                smoke_gate.update(
                    {
                        "status": "PASSED" if stop_reason_for_record(record, smoke=True) is None else "FAILED",
                        "station": candidate["station_name"],
                        "source_parent_station_id": candidate["source_parent_station_id"],
                        "requested_result_limit": number_of_results,
                        "client_request_attempts_issued": 1,
                        "http_responses_received": int(record.get("http_status") is not None),
                        "usable_http_trias_response": stop_reason_for_record(record, smoke=True) is None,
                    }
                )
            reason = stop_reason_for_record(record, smoke=is_smoke)
            if reason is not None:
                stopped = True
                stop_reason = reason
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
            stop_reason = stop_reason or "CURRENT_RUN_CLIENT_ATTEMPT_BUDGET_REACHED"
            break
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=DIAGNOSTIC_CONCURRENCY_CAP) as executor:
            futures = [executor.submit(execute, candidate, HISTORICAL_DIAGNOSTIC_RESULT_LIMIT) for candidate in pair]
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
                number_of_results=HISTORICAL_DIAGNOSTIC_RESULT_LIMIT,
                dep_arr_time=anchor,
                execution_mode="bounded_concurrency_cap_2",
                pair_id=f"PAIR_{pair_index}",
            )
            record["representative_role"] = candidate["representative_role"]
            record["pair_batch_duration_ms"] = _round(batch_elapsed_ms, 3)
            concurrency_records.append(record)
            reason = stop_reason_for_record(record, smoke=False)
            if reason is not None:
                stopped = True
                stop_reason = reason
        if stopped:
            break

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
        or row.get("transport_failure_category") in SAFE_TRANSPORT_FAILURE_CATEGORIES
        for row in pressure_rows
    )
    cap2_complete = include_concurrency_probe and (
        len(concurrency_records) == len(pairs) * 2
        and all(row.get("parser_success") and row.get("valid_trias_application_response") for row in concurrency_records)
        and not provider_pressure_signal
        and not any(row.get("http_status") in {401, 403} for row in concurrency_records)
    )
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
    if attempts > 0 and not any(row.get("http_status") is not None for row in sequential_records + concurrency_records):
        diagnostic_status = "COMPLETED_NO_VALID_HTTP_RESPONSES"
    else:
        diagnostic_status = (
            "COMPLETE_TECHNICAL_RESULT_LIMIT_COMPARISON"
            if n30_supported and (cap2_complete or not include_concurrency_probe) and not stopped
            else "CONDITIONALLY_BLOCKED"
        )
    http_responses_received = sum(
        row.get("http_status") is not None for row in sequential_records + concurrency_records
    )
    sequential_accounting = _request_accounting(
        len(sequential_records),
        sum(row.get("http_status") is not None for row in sequential_records),
        conservative_quota_accounting_attempts=len(sequential_records),
    )
    concurrency_accounting = _request_accounting(
        len(concurrency_records),
        sum(row.get("http_status") is not None for row in concurrency_records),
        conservative_quota_accounting_attempts=len(concurrency_records),
    )
    accounting = _request_accounting(
        attempts,
        http_responses_received,
        conservative_quota_accounting_attempts=attempts,
    )
    return {
        "status": diagnostic_status,
        "credential_preflight": preflight_label,
        "network_preflight": network_preflight,
        "execution_environment_suitable_for_live_diagnostic": True,
        "endpoint": ENDPOINT,
        "request_contract": {
            "requestor_ref": REQUESTOR_REF,
            "stop_event_type": "arrival",
            "include_previous_calls": True,
            "include_onward_calls": True,
            "include_realtime_data": True,
            "max_attempts": 1,
            "retries": 0,
            "timeout_seconds": PRODUCTION_REQUEST_TIMEOUT_SECONDS,
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
            "smoke_gate": smoke_gate,
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
            "requested_max_concurrency": DIAGNOSTIC_CONCURRENCY_CAP,
            "pair_count": len(pairs),
            **concurrency_accounting,
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
            **sequential_accounting,
            "request_duration_statistics_ms": duration_statistics(
                [float(row["duration_ms"]) for row in sequential_records if row.get("duration_ms") is not None]
            ),
            "retries": 0,
        },
        **accounting,
        "current_run_attempt_budget": attempt_budget,
        "stopped_on_auth_failure": any(row.get("http_status") in {401, 403} for row in sequential_records),
        "stop_reason": stop_reason,
        "diagnostic_validity": (
            "INVALID_FOR_NUMBER_OF_RESULTS_DECISION"
            if not n30_supported
            else "VALID_TECHNICAL_RESULT_LIMIT_EVIDENCE"
        ),
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
    live_rerun_approved: bool = False,
) -> dict[str, Any]:
    """Run only the corrected six-station × four-limit comparison.

    The previously exercised 24-invocation authorization is never reused for a
    new live run.  A future rerun must carry a separate explicit approval after
    the network-preflight changes have been reviewed.
    """

    gate = {
        "approval_request": ADDITIONAL_RESULT_LIMIT_APPROVAL_REQUEST,
        "required": True,
        "approved": bool(additional_requests_approved),
        "authorization_basis": "NEW_EXPLICIT_APPROVAL_REQUIRED_FOR_ANY_FUTURE_LIVE_RERUN",
        "corrected_diagnostic_scope": CORRECTED_RESULT_LIMIT_DIAGNOSTIC_SCOPE,
        "requested_additional_client_request_attempts": ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
        "historical_client_diagnostic_attempts": PRIOR_STAGE13_DIAGNOSTIC_ATTEMPTS,
        "prior_cumulative_client_request_invocations": prior_attempts,
        "historical_initial_diagnostic_budget": HISTORICAL_INITIAL_DIAGNOSTIC_BUDGET,
        "explicit_additional_user_authorization": ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
        "authorized_cumulative_client_attempt_ceiling": prior_attempts + ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
        "historical_budget_rewritten": False,
        "additional_client_request_attempts_issued": 0,
        "corrected_live_diagnostic_issued": False,
        "raw_responses_persisted": False,
    }
    if not additional_requests_approved or not live_rerun_approved:
        return {
            "status": "NOT_RUN_NEW_LIVE_RERUN_APPROVAL_REQUIRED"
            if additional_requests_approved and not live_rerun_approved
            else "NOT_RUN_USER_APPROVAL_REQUIRED",
            "credential_preflight": "NOT_EVALUATED",
            "network_preflight": {"status": "NOT_EVALUATED"},
            **_request_accounting(0, 0, conservative_quota_accounting_attempts=0),
            "current_run_attempt_budget": ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
            "additional_request_gate": gate,
            "stop_reason": (
                "the previous 24-invocation authorization is exhausted; explicit new live-rerun approval is required"
                if additional_requests_approved and not live_rerun_approved
                else "explicit approval is required before any corrected MDD request invocation"
            ),
            "raw_payloads_persisted": False,
        }
    if prior_attempts not in {
        PRIOR_STAGE13_DIAGNOSTIC_ATTEMPTS,
        AUTHORIZED_CUMULATIVE_CLIENT_ATTEMPT_CEILING,
    }:
        raise Stage13Blocked(
            "corrected result-limit diagnostic requires prior_attempts=90 for the historical authorization or 114 for a separately approved rerun"
        )
    live = _run_live_diagnostics(
        station_rows,
        api_key=api_key,
        include_concurrency_probe=False,
        attempt_budget=ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
    )
    live["additional_request_gate"] = gate
    gate["additional_client_request_attempts_issued"] = int(live.get("client_request_attempts_issued", 0))
    gate["corrected_live_diagnostic_issued"] = gate["additional_client_request_attempts_issued"] > 0
    live["historical_client_diagnostic_attempts"] = PRIOR_STAGE13_DIAGNOSTIC_ATTEMPTS
    live["prior_cumulative_client_request_invocations"] = prior_attempts
    live["corrected_client_request_attempts"] = int(live.get("client_request_attempts_issued", 0))
    live["cumulative_client_request_invocations"] = prior_attempts + int(live.get("client_request_attempts_issued", 0))
    live["historical_initial_diagnostic_budget"] = HISTORICAL_INITIAL_DIAGNOSTIC_BUDGET
    live["explicit_additional_user_authorization"] = ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS
    live["authorized_cumulative_client_attempt_ceiling"] = prior_attempts + ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS
    live["historical_budget_rewritten"] = False
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
        stress = next(item for item in projections["projections"] if item["label"] == "approved_normal_retry_reserve")
        rows.append(
            {
                "label": label,
                "tiers": cadence["tiers"],
                "logical_requests_per_hour": cadence["logical_requests_per_hour"],
                "logical_requests_per_day": cadence["logical_requests_per_day"],
                "monthly_logical_requests": cadence["monthly_logical_requests"],
                "static_density_fit_at_n30": _cadence_static_fit(station_rows, tiers, 30) if station_rows else None,
                "31_day_approved_retry_reserve_with_legacy_and_diagnostics": stress["31_day"]["conservative_reserve_reconciliation"],
                "technically_evaluated_option": label == "10_20_20_at_5_15_30_evaluated_option",
                "user_review_preference": label == "8_20_22_at_5_15_30",
                "user_approved_design": label == "8_20_22_at_5_15_30",
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
        observed_max_values = [
            row["window_event_count_statistics"][interval]["max"]
            for row in rows
            if row["window_event_count_statistics"][interval].get("max") is not None
        ]
        supported = [value is not None and float(value) <= limit for value in p95_values]
        by_tier[tier_name] = {
            "result_limit": limit,
            "interval_minutes": int(interval),
            "station_count": len(rows),
            "station_p95_supported_count": sum(supported),
            "all_station_p95_supported": bool(rows) and all(supported),
            "maximum_station_p95": max(p95_values) if p95_values else None,
            "maximum_observed_static_scheduled_arrival_count": max(observed_max_values)
            if observed_max_values
            else None,
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
    transport_categories = {
        row.get("transport_failure_category") or _safe_transport_failure_category(row.get("transport_failure"))
        for row in records
        if row.get("http_status") is None
    }
    if records and all(row.get("http_status") is None for row in records) and transport_categories:
        return CORRECTED_LIVE_FORWARD_COVERAGE_LIMITATION
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
            "client_request_attempts_issued": summary.get("client_request_attempts_issued", 0),
            "http_responses_received": summary.get("http_responses_received", 0),
            "confirmed_provider_http_attempts": summary.get("confirmed_provider_http_attempts", 0),
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
        "corrected_live_limitation": CORRECTED_LIVE_FORWARD_COVERAGE_LIMITATION,
        "forward_coverage_metric_basis": "latest THIS-call arrival at or after requested DepArrTime minus requested DepArrTime",
        "static_scheduled_density_is_not_realtime_coverage": True,
        "static_evidence_boundary": "static scheduled-density evidence is planning evidence only; it is not realtime response coverage",
        "fixed_n20": {
            "static_cadence_window_fit": fixed_n20,
            "corrected_live": live_n20,
            "decision": "COMPARISON_ONLY_NOT_APPROVED",
        },
        "fixed_n30": {
            "static_cadence_window_fit": fixed_n30,
            "corrected_live": live_n30,
            "decision": "COMPARISON_ONLY_NOT_APPROVED",
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
            "decision": "APPROVED_USER_DESIGN_STATIC_SUPPORTED_LIVE_LIMITATION_RETAINED",
            "approved_production_design": True,
            "provider_maximum_status": "PROVIDER_MAXIMUM_NOT_ESTABLISHED",
            "historical_provider_acceptance": {
                "accepted_result_limits": [20, 30],
                "scope": "bounded representative historical diagnostics only",
                "valid_http_trias_responses": True,
                "provider_wide_maximum_established": False,
                "realtime_completeness_guaranteed": False,
            },
            "approved_static_support": tier_specific,
            "corrected_live_limitation": CORRECTED_LIVE_FORWARD_COVERAGE_LIMITATION,
        },
        "simpler_alternative": {
            "candidate": "A=20, B=30, C=30",
            "static_cadence_window_fit": tier_specific,
            "corrected_live_status": corrected_status,
            "recommendation": "APPROVED_BY_USER_AS_PRODUCTION_DESIGN_WITH_RETAINED_LIVE_LIMITATION",
        },
        "provider_maximum_status": "PROVIDER_MAXIMUM_NOT_ESTABLISHED",
        "number_of_results_finalized": True,
        "approved_production_policy": dict(PRODUCTION_NUMBER_OF_RESULTS_BY_TIER),
        "approved_policy_basis": [
            "exact-feed static density support",
            "bounded historical provider acceptance of N20 and N30",
            "lower unnecessary response volume for Tier A",
            "simple two-value configuration",
        ],
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
    """Separate normal observed timing from degraded-mode deadline safety."""

    max_due = int(phase.get("max_due_targets_in_one_heartbeat", 0))
    cap2 = live_diagnostics.get("bounded_concurrency_probe", {})
    batch_stats = cap2.get("pair_batch_duration_statistics_ms", {})
    request_stats = cap2.get("request_duration_statistics_ms", {})
    batch_count = math.ceil(max_due / PRODUCTION_MAXIMUM_CONCURRENCY) if max_due else 0
    p95_batch = batch_stats.get("p95_ms")
    max_batch = batch_stats.get("max_ms")
    estimated_p95 = batch_count * float(p95_batch) if p95_batch is not None else None
    estimated_max = batch_count * float(max_batch) if max_batch is not None else None
    normal_observed = {
        "evidence_basis": "retained bounded cap-2 successful HTTP timing observations",
        "successful_observation_only": True,
        "not_a_guarantee_under_timeout_or_retry_failure": True,
        "cap2_request_duration_statistics_ms": request_stats,
        "cap2_pair_batch_duration_statistics_ms": batch_stats,
        "max_due_batches_at_cap2": batch_count,
        "estimated_p95_heartbeat_execution_ms": _round(estimated_p95, 3),
        "estimated_max_observed_heartbeat_execution_ms": _round(estimated_max, 3),
        "observed_p95_within_300_second_heartbeat": estimated_p95 is not None and estimated_p95 <= 300_000,
        "observed_max_within_300_second_heartbeat": estimated_max is not None and estimated_max <= 300_000,
        "feasibility_decision": (
            "NORMAL_OBSERVED_FEASIBILITY_SUPPORTED"
            if estimated_p95 is not None and estimated_p95 <= 300_000
            else "NORMAL_OBSERVED_FEASIBILITY_NOT_ESTABLISHED"
        ),
    }
    theoretical_attempts = max_due * PRODUCTION_MAX_ATTEMPTS
    timeout_heavy_batches = (
        math.ceil(theoretical_attempts / PRODUCTION_MAXIMUM_CONCURRENCY) if theoretical_attempts else 0
    )
    degraded_budget = {
        "heartbeat_execution_budget_seconds": HEARTBEAT_SECONDS,
        "max_due_targets": max_due,
        "maximum_concurrency": PRODUCTION_MAXIMUM_CONCURRENCY,
        "per_attempt_timeout_seconds": PRODUCTION_REQUEST_TIMEOUT_SECONDS,
        "max_attempts": PRODUCTION_MAX_ATTEMPTS,
        "automatic_retry_count_max": PRODUCTION_MAX_AUTOMATIC_RETRIES,
        "theoretical_timeout_heavy_attempt_count": theoretical_attempts,
        "initial_attempt_timeout_heavy_wall_seconds_at_cap2": (
            math.ceil(max_due / PRODUCTION_MAXIMUM_CONCURRENCY) * PRODUCTION_REQUEST_TIMEOUT_SECONDS
            if max_due
            else 0
        ),
        "theoretical_timeout_heavy_wall_seconds_at_cap2": timeout_heavy_batches * PRODUCTION_REQUEST_TIMEOUT_SECONDS,
        "theoretical_timeout_heavy_wall_seconds_is_lower_bound": True,
        "unconstrained_timeout_heavy_batch_exceeds_300_seconds": (
            timeout_heavy_batches * PRODUCTION_REQUEST_TIMEOUT_SECONDS > HEARTBEAT_SECONDS
        ),
        "pressure_scenario": (
            "19 due targets; concurrency 2; 30-second timeout per attempt; MaxAttempts=2; "
            "all allowed attempts reach timeout"
        ),
        "deadline_contract": heartbeat_degraded_mode_contract(),
    }
    return {
        "heartbeat_interval_seconds": HEARTBEAT_MINUTES * 60,
        "due_targets_per_heartbeat": phase.get("total_due_targets_by_heartbeat", []),
        "max_due_targets_in_one_heartbeat": max_due,
        "requests_per_super_cycle": phase.get("super_cycle_total_logical_requests"),
        "requests_per_hour": phase.get("hour_total_logical_requests"),
        "technically_evaluated_maximum_concurrency": PRODUCTION_MAXIMUM_CONCURRENCY if cap2.get("decision") in {
            "APPROVED_MAXIMUM_CONCURRENCY_2",
            "TECHNICALLY_EVALUATED_OPTION_MAXIMUM_CONCURRENCY_2",
        } else None,
        "user_approved_maximum_concurrency": True,
        "NORMAL_OBSERVED_HEARTBEAT_FEASIBILITY": normal_observed,
        "DEGRADED_MODE_HEARTBEAT_BUDGET": degraded_budget,
    }


def _phase_index_map(assignments: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Normalize current or legacy assignment rows for offline comparison."""

    result: dict[str, int] = {}
    for row in assignments:
        identity = (
            row.get("phase_assignment_identity")
            or row.get("source_parent_station_id")
            or row.get("sampling_target_identity")
        )
        if identity is not None and row.get("phase_index") is not None:
            result[str(identity)] = int(row["phase_index"])
    return result


def _phase_index_reproduction(
    root: Path,
    assignments: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Verify that the identity correction leaves approved indexes unchanged."""

    current = _phase_index_map(assignments)
    path = root / "docs" / "stages" / "13-tiered-collector-readiness-evidence.json"
    if not path.exists():
        return {
            "identity": PHASE_ASSIGNMENT_IDENTITY,
            "hash": PHASE_ASSIGNMENT_HASH,
            "compared_to_prior_stage13_evidence": False,
            "phase_indexes_reproduced_exactly": False,
            "compared_assignment_count": 0,
            "sampling_target_id_values_participate_in_phase_hash": False,
        }
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        existing = {}
    previous = existing.get("phase_staggering", {}).get("phase_assignments", [])
    previous_map = _phase_index_map(previous)
    compared = bool(previous_map) and previous_map == current
    return {
        "identity": PHASE_ASSIGNMENT_IDENTITY,
        "hash": PHASE_ASSIGNMENT_HASH,
        "compared_to_prior_stage13_evidence": bool(previous_map),
        "phase_indexes_reproduced_exactly": compared,
        "compared_assignment_count": len(current) if compared else 0,
        "sampling_target_id_values_participate_in_phase_hash": False,
        "database_generated_sampling_target_ids_can_change_phase_indexes": False,
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
    raw_old_concurrency = existing.get("sequential_vs_bounded_concurrency", {}).get(
        "bounded_live_diagnostic", {}
    )
    old_concurrency_records = (
        list(raw_old_concurrency.get("records", []))
        if isinstance(raw_old_concurrency, Mapping)
        else []
    )
    old_concurrency = _normalize_duration_evidence(raw_old_concurrency, records=old_concurrency_records)
    historical_records = list(
        existing.get("number_of_results", {}).get("historical_invalidated_sequential_records", [])
    )
    historical_summaries = existing.get("number_of_results", {}).get(
        "historical_invalidated_sequential_summary_by_result_limit", {}
    )
    old_sequential = _normalize_duration_evidence(
        existing.get("sequential_vs_bounded_concurrency", {}).get("sequential_live_diagnostic", {}),
        records=historical_records,
    )
    normalized_historical_summaries = {
        str(number): _normalize_duration_evidence(
            historical_summaries.get(str(number), {}),
            records=[
                row
                for row in historical_records
                if row.get("requested_result_limit") == number
            ],
        )
        for number in MDD_RESULT_LIMITS
        if historical_records
        and any(row.get("requested_result_limit") == number for row in historical_records)
    }
    if not normalized_historical_summaries:
        normalized_historical_summaries = dict(historical_summaries)
    old_probe = old_bounded.get("result_limit_probe")
    if not isinstance(old_probe, Mapping) or not old_probe.get("sequential_records"):
        return None
    existing_gate = old_bounded.get("additional_request_gate", {})
    corrected_records = list(old_probe.get("sequential_records", []))
    old_issued = existing_gate.get(
        "additional_client_request_attempts_issued",
        existing_gate.get("additional_requests_issued", 0),
    )
    if (
        existing_gate.get("approved") is True
        and old_issued == ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS
        and len(corrected_records) == ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS
        and all(row.get("forward_temporal_span_seconds") is None for row in corrected_records)
    ):
        probe = dict(old_probe)
        probe["representatives"] = _select_diagnostic_representatives(station_rows)
        probe["sequential_records"] = [_normalize_transport_record(row) for row in corrected_records]
        probe["summary_by_result_limit"] = {
            str(number): _summary_for_records(
                [
                    row
                    for row in probe["sequential_records"]
                    if row.get("requested_result_limit") == number
                ]
            )
            for number in MDD_RESULT_LIMITS
        }
        probe["smoke_gate"] = {
            "status": "NOT_PERFORMED_HISTORICAL_RUN",
            "counts_inside_authorized_budget": True,
            "policy": "smoke gate added after this historical run; no retroactive request was issued",
            "provider_http_error_policy": "STOP_ON_FIRST_PROVIDER_HTTP_ERROR; retain HTTP status and do not classify it as transport failure",
            "client_request_attempts_issued": 0,
            "http_responses_received": 0,
        }
        production_selection = dict(probe.get("production_selection", {}))
        production_selection["technically_evaluated_number_of_results"] = production_selection.get(
            "technically_evaluated_number_of_results"
        )
        production_selection["decision"] = (
            "TECHNICALLY_EVALUATED_OPTION_REQUIRES_USER_APPROVAL"
            if production_selection["technically_evaluated_number_of_results"] == HISTORICAL_DIAGNOSTIC_RESULT_LIMIT
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
        gate = {
            "approval_request": ADDITIONAL_RESULT_LIMIT_APPROVAL_REQUEST,
            "required": True,
            "approved": True,
            "authorization_basis": "HISTORICAL_APPROVAL_ALREADY_EXERCISED; NOT AUTHORIZATION FOR ANOTHER RERUN",
            "corrected_diagnostic_scope": CORRECTED_RESULT_LIMIT_DIAGNOSTIC_SCOPE,
            "requested_additional_client_request_attempts": ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
            "historical_client_diagnostic_attempts": PRIOR_STAGE13_DIAGNOSTIC_ATTEMPTS,
            "prior_cumulative_client_request_invocations": PRIOR_STAGE13_DIAGNOSTIC_ATTEMPTS,
            "historical_initial_diagnostic_budget": HISTORICAL_INITIAL_DIAGNOSTIC_BUDGET,
            "explicit_additional_user_authorization": ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
            "authorized_cumulative_client_attempt_ceiling": AUTHORIZED_CUMULATIVE_CLIENT_ATTEMPT_CEILING,
            "historical_budget_rewritten": False,
            "additional_client_request_attempts_issued": ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
            "corrected_live_diagnostic_issued": True,
            "raw_responses_persisted": False,
        }
        accounting = _request_accounting(
            len(probe["sequential_records"]),
            sum(row.get("http_status") is not None for row in probe["sequential_records"]),
            conservative_quota_accounting_attempts=ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
        )
        return {
            "status": "COMPLETED_NO_VALID_HTTP_RESPONSES",
            "credential_preflight": old_bounded.get("credential_preflight", "PRESENT"),
            "network_preflight": {
                "status": "NOT_PERFORMED_HISTORICAL_RUN",
                "reason": "zero-request DNS/network preflight was added after this run",
            },
            "execution_environment_suitable_for_live_diagnostic": "NOT_EVALUATED_HISTORICAL_RUN",
            "diagnostic_anchor_dep_arr_time": old_bounded.get("diagnostic_anchor_dep_arr_time"),
            "diagnostic_run_timestamp_utc": existing.get("generated_at_utc"),
            "diagnostic_run_timestamp_basis": "retained artifact generated_at_utc; per-request timestamps were not retained",
            "representatives": probe.get("representatives", []),
            "result_limit_probe": probe,
            "bounded_concurrency_probe": old_concurrency,
            "sequential_duration_evidence": old_sequential,
            **accounting,
            "historical_client_diagnostic_attempts": PRIOR_STAGE13_DIAGNOSTIC_ATTEMPTS,
            "corrected_client_request_attempts": len(probe["sequential_records"]),
            "prior_cumulative_client_request_invocations": PRIOR_STAGE13_DIAGNOSTIC_ATTEMPTS,
            "cumulative_client_request_invocations": AUTHORIZED_CUMULATIVE_CLIENT_ATTEMPT_CEILING,
            "historical_initial_diagnostic_budget": HISTORICAL_INITIAL_DIAGNOSTIC_BUDGET,
            "explicit_additional_user_authorization": ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
            "authorized_cumulative_client_attempt_ceiling": AUTHORIZED_CUMULATIVE_CLIENT_ATTEMPT_CEILING,
            "historical_budget_rewritten": False,
            "additional_request_gate": gate,
            "historical_invalidated_sequential_records": existing.get("number_of_results", {}).get(
                "historical_invalidated_sequential_records", []
            ),
            "historical_invalidated_sequential_summary_by_result_limit": normalized_historical_summaries,
            "stop_reason": old_bounded.get("stop_reason") or "HISTORICAL_RUN_COMPLETED_WITHOUT_VALID_HTTP_RESPONSES",
            "diagnostic_validity": "INVALID_FOR_NUMBER_OF_RESULTS_DECISION",
            "raw_payloads_persisted": False,
            "request_headers_persisted": False,
            "api_key_persisted": False,
        }
    probe = dict(old_probe)
    probe["representatives"] = _select_diagnostic_representatives(station_rows)
    probe["sequential_records"] = [
        _mark_legacy_forward_span_invalid(_normalize_transport_record(record))
        for record in probe.get("sequential_records", [])
    ]
    probe["summary_by_result_limit"] = {
        str(number): _mark_legacy_summary_invalid(
            _summary_for_records(
                [
                    row
                    for row in probe["sequential_records"]
                    if row.get("requested_result_limit") == number
                ]
            )
        )
        for number in MDD_RESULT_LIMITS
    }
    production_selection = dict(probe.get("production_selection", {}))
    production_selection["decision"] = "TECHNICALLY_EVALUATED_OPTION_REQUIRES_USER_APPROVAL"
    production_selection["technically_evaluated_number_of_results"] = production_selection.get(
        "technically_evaluated_number_of_results",
        production_selection.get("production_number_of_results", HISTORICAL_DIAGNOSTIC_RESULT_LIMIT),
    )
    production_selection["production_number_of_results"] = None
    production_selection["user_approved_design"] = False
    probe["production_selection"] = production_selection
    old_concurrency = dict(old_concurrency)
    old_concurrency["records"] = [
        _mark_legacy_forward_span_invalid(_normalize_transport_record(record))
        for record in old_concurrency.get("records", [])
    ]
    if old_concurrency.get("decision") == "APPROVED_MAXIMUM_CONCURRENCY_2":
        old_concurrency["decision"] = "TECHNICALLY_EVALUATED_OPTION_MAXIMUM_CONCURRENCY_2"
    old_concurrency["user_approved_design"] = False
    first_record = probe["sequential_records"][0]
    accounting = _request_accounting(
        len(probe["sequential_records"]),
        sum(row.get("http_status") is not None for row in probe["sequential_records"]),
        conservative_quota_accounting_attempts=len(probe["sequential_records"]),
    )
    return {
        "status": "HISTORICAL_SANITIZED_EVIDENCE_RETAINED; FORWARD_COVERAGE_INVALIDATED",
        "credential_preflight": "PRESENT",
        "network_preflight": {
            "status": "NOT_PERFORMED_HISTORICAL_RUN",
            "reason": "zero-request DNS/network preflight was added after this run",
        },
        "execution_environment_suitable_for_live_diagnostic": "NOT_EVALUATED_HISTORICAL_RUN",
        "diagnostic_run_timestamp_utc": existing.get("generated_at_utc"),
        "diagnostic_run_timestamp_basis": "retained artifact generated_at_utc; per-request timestamps were not retained",
        "diagnostic_anchor_dep_arr_time": first_record.get("requested_dep_arr_time"),
        "representatives": probe["representatives"],
        "result_limit_probe": probe,
        "historical_invalidated_sequential_records": probe.get("sequential_records", []),
        "historical_invalidated_sequential_summary_by_result_limit": normalized_historical_summaries
        or probe.get("summary_by_result_limit", {}),
        "bounded_concurrency_probe": old_concurrency,
        "sequential_duration_evidence": old_sequential,
        **accounting,
        "diagnostic_validity": "INVALID_FOR_NUMBER_OF_RESULTS_DECISION",
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


def _normalize_additional_request_gate(
    raw_gate: Mapping[str, Any] | None,
    *,
    historical_attempts: int,
    current_attempts: int,
) -> dict[str, Any]:
    """Normalize old HTTP-worded gate fields to client-invocation accounting."""

    raw = dict(raw_gate or {})
    historical = int(
        raw.get(
            "historical_client_diagnostic_attempts",
            raw.get("prior_attempts_consumed", historical_attempts or PRIOR_STAGE13_DIAGNOSTIC_ATTEMPTS),
        )
    )
    issued = int(
        raw.get(
            "additional_client_request_attempts_issued",
            raw.get("additional_requests_issued", current_attempts),
        )
    )
    approved = bool(raw.get("approved", issued > 0))
    if not approved:
        issued = 0
    prior_cumulative = int(
        raw.get("prior_cumulative_client_request_invocations", historical)
    )
    authorization_basis = raw.get("authorization_basis") or (
        "HISTORICAL_APPROVAL_ALREADY_EXERCISED; NOT AUTHORIZATION FOR ANOTHER RERUN"
        if approved and issued > 0
        else "NEW_EXPLICIT_APPROVAL_REQUIRED_FOR_ANY_FUTURE_LIVE_RERUN"
    )
    return {
        "approval_request": ADDITIONAL_RESULT_LIMIT_APPROVAL_REQUEST,
        "required": True,
        "approved": approved,
        "authorization_basis": authorization_basis,
        "corrected_diagnostic_scope": CORRECTED_RESULT_LIMIT_DIAGNOSTIC_SCOPE,
        "requested_additional_client_request_attempts": ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
        "historical_client_diagnostic_attempts": historical,
        "prior_cumulative_client_request_invocations": prior_cumulative,
        "historical_initial_diagnostic_budget": HISTORICAL_INITIAL_DIAGNOSTIC_BUDGET,
        "explicit_additional_user_authorization": ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
        "authorized_cumulative_client_attempt_ceiling": prior_cumulative + ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
        "historical_budget_rewritten": False,
        "additional_client_request_attempts_issued": issued,
        "corrected_live_diagnostic_issued": bool(
            raw.get("corrected_live_diagnostic_issued", issued > 0)
        ),
        "raw_responses_persisted": False,
    }


def _normalize_duration_evidence(
    value: Mapping[str, Any] | None,
    *,
    records: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normalize retained aggregate accounting from its row-level evidence."""

    normalized = dict(value or {})
    if "request_count" in normalized:
        normalized["client_request_attempts_issued"] = normalized.pop("request_count")
    if records is not None:
        rows = list(records)
        normalized.update(
            _request_accounting(
                len(rows),
                sum(row.get("http_status") is not None for row in rows),
                conservative_quota_accounting_attempts=len(rows),
            )
        )
    else:
        if "http_responses_received" not in normalized:
            normalized["http_responses_received"] = None
        if normalized.get("http_responses_received") is not None:
            normalized.setdefault(
                "confirmed_provider_http_attempts",
                normalized["http_responses_received"],
            )
    return normalized


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
    live_rerun_approved: bool = False,
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
    phase["phase_assignment_reproduction"] = _phase_index_reproduction(root, phase_assignments)
    if live_diagnostics is None:
        if run_live_diagnostics and len(station_rows) == 50:
            if not live_rerun_approved:
                raise Stage13Blocked(
                    "the previous 24-invocation authorization is exhausted; a new explicit live-rerun approval is required"
                )
            live = _run_corrected_result_limit_diagnostic(
                station_rows,
                prior_attempts=prior_live_attempts,
                additional_requests_approved=True,
                live_rerun_approved=True,
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
                "network_preflight": {"status": "NOT_EVALUATED"},
                **_request_accounting(0, 0, conservative_quota_accounting_attempts=0),
                "raw_payloads_persisted": False,
            }
        else:
            live = {
                "status": "NOT_RUN",
                "credential_preflight": "NOT_EVALUATED",
                "network_preflight": {"status": "NOT_EVALUATED"},
                **_request_accounting(0, 0, conservative_quota_accounting_attempts=0),
                "raw_payloads_persisted": False,
            }
    else:
        live = dict(live_diagnostics)
    historical_client_attempts = int(
        live.get("historical_client_diagnostic_attempts", 0)
        or PRIOR_STAGE13_DIAGNOSTIC_ATTEMPTS
    )
    prior_cumulative_client_attempts = int(
        live.get("prior_cumulative_client_request_invocations", 0)
        or prior_live_attempts
        or historical_client_attempts
    )
    current_client_attempts = int(
        live.get(
            "client_request_attempts_issued",
            live.get("current_run_http_attempts_consumed", live.get("http_attempts_consumed", 0)),
        )
    )
    http_responses_received = int(
        live.get(
            "http_responses_received",
            sum(
                row.get("http_status") is not None
                for row in live.get("result_limit_probe", {}).get("sequential_records", [])
            ),
        )
    )
    conservative_attempts = int(
        live.get("conservative_quota_accounting_attempts", current_client_attempts)
    )
    live.update(
        _request_accounting(
            current_client_attempts,
            http_responses_received,
            conservative_quota_accounting_attempts=conservative_attempts,
        )
    )
    live["historical_client_diagnostic_attempts"] = historical_client_attempts
    live["corrected_client_request_attempts"] = current_client_attempts
    live["prior_cumulative_client_request_invocations"] = prior_cumulative_client_attempts
    live["cumulative_client_request_invocations"] = prior_cumulative_client_attempts + current_client_attempts
    live["historical_initial_diagnostic_budget"] = HISTORICAL_INITIAL_DIAGNOSTIC_BUDGET
    live["explicit_additional_user_authorization"] = ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS
    live["authorized_cumulative_client_attempt_ceiling"] = prior_cumulative_client_attempts + ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS
    live["historical_budget_rewritten"] = False
    for obsolete_key in (
        "http_attempts_consumed",
        "current_run_http_attempts_consumed",
        "prior_attempts_consumed",
        "http_attempts_consumed_total",
        "max_http_attempts",
    ):
        live.pop(obsolete_key, None)

    number_limit_probe = live.get("result_limit_probe", {})
    production_selection = number_limit_probe.get("production_selection", {}) if isinstance(number_limit_probe, Mapping) else {}
    historically_evaluated_number_of_results = production_selection.get(
        "technically_evaluated_number_of_results",
        production_selection.get("production_number_of_results"),
    )
    cap2_probe = live.get("bounded_concurrency_probe", {})
    cap2_evaluated = cap2_probe.get("decision") in {
        "APPROVED_MAXIMUM_CONCURRENCY_2",
        "TECHNICALLY_EVALUATED_OPTION_MAXIMUM_CONCURRENCY_2",
    }
    cadence = candidate_cadence(PRODUCTION_TIER_PLAN, status="APPROVED_STAGE13_DESIGN")
    alternatives = _cadence_alternatives(station_rows, legacy_reserved_requests=LEGACY7_RESERVED_MONTHLY_REQUESTS)
    quota = quota_projection_set(
        cadence["logical_requests_per_hour"],
        legacy_reserved_requests=LEGACY7_RESERVED_MONTHLY_REQUESTS,
        diagnostic_reserved_requests=DIAGNOSTIC_RESERVE_HTTP_ATTEMPTS,
    )
    ten_percent_projection = next(item for item in quota["projections"] if item["label"] == "approved_normal_retry_reserve")
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
    if not cap2_evaluated:
        blockers.append("CONCURRENCY_BEHAVIOR_NOT_ESTABLISHED")
    if not quota_safety_closed:
        blockers.append("QUOTA_HEADROOM_NOT_ESTABLISHED")
    if len(station_rows) == 50 and len(phase_assignments) != 50:
        blockers.append("PHASE_ASSIGNMENT_NOT_ESTABLISHED")
    design_ready = (
        stage12_approved
        and density.get("status") == "COMPLETE"
        and cap2_evaluated
        and quota_safety_closed
        and len(station_rows) == 50
        and len(phase_assignments) == 50
    )
    status = (
        "COMPLETE — APPROVED TIERED COLLECTOR READINESS DESIGN; PRODUCTION NOT STARTED"
        if design_ready
        else "IN PROGRESS — EVIDENCE READY; USER DESIGN APPROVAL REQUIRED"
    )
    generated_at_utc = generated_at_utc or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    result_limit_evidence = _historical_result_limit_evidence(root)
    additional_request_gate = _normalize_additional_request_gate(
        live.get("additional_request_gate"),
        historical_attempts=historical_client_attempts,
        current_attempts=current_client_attempts,
    )
    live["additional_request_gate"] = additional_request_gate
    result_limit_evidence.update(
        {
            "decision": "APPROVED_USER_DESIGN_A20_B30_C30",
            "historically_evaluated_number_of_results": historically_evaluated_number_of_results,
            "production_number_of_results": dict(PRODUCTION_NUMBER_OF_RESULTS_BY_TIER),
            "production_number_of_results_policy": "A20/B30/C30",
            "provider_maximum_status": "PROVIDER_MAXIMUM_NOT_ESTABLISHED",
            "historical_provider_acceptance": {
                "accepted_result_limits": [20, 30],
                "scope": "bounded representative historical diagnostics only",
                "valid_http_trias_responses": True,
                "provider_wide_maximum_established": False,
                "realtime_completeness_guaranteed": False,
            },
            "approved_static_support": number_of_results_comparison[
                "tier_specific_a20_b30_c30"
            ]["static_cadence_window_fit"],
            "corrected_live_limitation": CORRECTED_LIVE_FORWARD_COVERAGE_LIMITATION,
            "additional_request_gate": additional_request_gate,
            "corrected_metric_status": corrected_metric_status,
            "failed_corrected_diagnostic_audit": {
                "status": (
                    "INVALID_FOR_NUMBER_OF_RESULTS_DECISION"
                    if current_client_attempts > 0 and http_responses_received == 0
                    else "NOT_AVAILABLE"
                ),
                "timestamp_utc": live.get("diagnostic_run_timestamp_utc", generated_at_utc),
                "timestamp_basis": live.get(
                    "diagnostic_run_timestamp_basis",
                    "artifact generated timestamp when a precise run timestamp was not retained",
                ),
                "requested_station_result_limit_matrix": {
                    "representatives": live.get("representatives", []),
                    "requested_result_limits": list(MDD_RESULT_LIMITS),
                    "expected_client_request_attempts": ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
                },
                "client_request_attempts_issued": current_client_attempts,
                "http_responses_received": http_responses_received,
                "confirmed_provider_http_attempts": live.get("confirmed_provider_http_attempts", 0),
                "provider_quota_consumption_status": live.get("provider_quota_consumption_status"),
                "conservative_quota_accounting_attempts": conservative_attempts,
                "transport_failure_category_distribution": {
                    str(key): value
                    for key, value in sorted(
                        Counter(
                            row.get("transport_failure_category")
                            or _safe_transport_failure_category(row.get("transport_failure"))
                            for row in number_limit_probe.get("sequential_records", [])
                            if row.get("http_status") is None
                        ).items()
                    )
                    if key != "None"
                },
                "raw_response_retention": "ZERO_RAW_RESPONSES_RETAINED",
            },
            "bounded_live_diagnostic": {
                "status": live.get("status"),
                "historical_client_diagnostic_attempts": historical_client_attempts,
                "corrected_client_request_attempts": current_client_attempts,
                "prior_cumulative_client_request_invocations": prior_cumulative_client_attempts,
                "cumulative_client_request_invocations": prior_cumulative_client_attempts + current_client_attempts,
                "client_request_attempts_issued": current_client_attempts,
                "http_responses_received": http_responses_received,
                "confirmed_provider_http_attempts": live.get("confirmed_provider_http_attempts", 0),
                "provider_quota_consumption_status": live.get("provider_quota_consumption_status"),
                "conservative_quota_accounting_attempts": conservative_attempts,
                "historical_initial_diagnostic_budget": HISTORICAL_INITIAL_DIAGNOSTIC_BUDGET,
                "explicit_additional_user_authorization": ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
                "authorized_cumulative_client_attempt_ceiling": prior_cumulative_client_attempts + ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
                "stop_reason": live.get("stop_reason"),
                "network_preflight": live.get("network_preflight", {"status": "NOT_EVALUATED"}),
                "diagnostic_anchor_dep_arr_time": live.get("diagnostic_anchor_dep_arr_time"),
                "representatives": live.get("representatives", []),
                "result_limit_probe": number_limit_probe,
                "additional_request_gate": additional_request_gate,
            },
            "selection_limitation": "The approved A20/B30/C30 policy is a user-approved production-design choice; provider-wide maximum, guaranteed realtime completeness, guaranteed 20/30-event availability, and complete realtime forward coverage remain unestablished.",
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
        "HISTORICAL_BOUNDED_N20_N30_ACCEPTANCE_IS_NOT_GUARANTEED_REALTIME_COMPLETENESS",
        "GUARANTEED_20_30_EVENT_AVAILABILITY_NOT_ESTABLISHED",
        "COMPLETE_REALTIME_FORWARD_COVERAGE_NOT_ESTABLISHED",
        CORRECTED_LIVE_FORWARD_COVERAGE_LIMITATION,
    ]
    user_decision_gate = {
        "status": "USER_APPROVED_FINAL_STAGE13_DESIGN",
        "technical_evaluation_status": "RETAINED_AND_RECONCILED",
        "user_approval_received": True,
        "all_material_design_choices_user_approved": True,
        "stage_13_complete_allowed": True,
        "production_number_of_results_approved": True,
        "production_concurrency_approved": True,
        "production_cadence_approved": True,
        "production_retry_policy_approved": True,
        "production_tier_membership_approved": True,
        "material_decisions": {
            "production_number_of_results": {
                "historical_bounded_acceptance": [20, 30],
                "user_approved_design": dict(PRODUCTION_NUMBER_OF_RESULTS_BY_TIER),
                "approved": True,
            },
            "production_concurrency": {
                "bounded_historical_sample": PRODUCTION_MAXIMUM_CONCURRENCY,
                "user_approved_design": PRODUCTION_MAXIMUM_CONCURRENCY,
                "approved": True,
            },
            "production_cadence": {
                "user_approved_design": cadence,
                "approved": True,
            },
            "production_retry_policy": {
                "user_approved_design": retry_policy_contract(),
                "approved": True,
            },
            "production_tier_membership": {
                "user_approved_design": {
                    "panel_version": PANEL_VERSION,
                    "density_rank_ranges": {"A": "1-8", "B": "9-28", "C": "29-50"},
                },
                "approved": True,
            },
        },
        "approval_required_before": [
            "starting Stage 14",
            "activating Tiered50",
            "creating production database objects or Windows Scheduled Tasks",
            "modifying Power BI",
            "materializing phase assignments or durable claims in production",
        ],
    }
    return {
        "artifact_version": "stage-13-tiered-collector-readiness-evidence-5.1",
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
            "historical_comparison": candidate_cadence(HISTORICAL_TIERED50_PLAN, status="HISTORICAL_COMPARISON"),
            "technically_evaluated_option": cadence,
            "user_approved_design": cadence,
            "cadence_selection_alternatives": alternatives,
            "selection_reason": "The user-approved 8/20/22 split follows the deterministic density ranks 1–8 / 9–28 / 29–50 and reconciles to 220 logical requests/hour, 110 per 30-minute super-cycle, and 220 per hour.",
        },
        "retry_policy": retry_policy_contract(),
        "cadence_boundary_review": cadence_boundary_review,
        "number_of_results_comparison": number_of_results_comparison,
        "future_live_diagnostic_execution_boundary": {
            "previous_24_invocation_authorization_exhausted": True,
            "new_explicit_user_approval_required": True,
            "approval_request": LIVE_RERUN_APPROVAL_REQUEST,
            "preflight_required_before_any_mdd_http_request": True,
            "smoke_request_required_before_remaining_matrix": True,
            "expected_max_additional_client_request_attempts_for_successful_rerun": ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
            "provider_quota_consumption_for_rerun": "UNKNOWN_UNTIL_HTTP_RESPONSES_ARE_RECEIVED; DO NOT INFER FROM CLIENT INVOCATIONS",
            "network_capable_local_action": (
                "python3 scripts/stage_13_tiered_collector_readiness.py "
                "--gtfs-zip /path/to/verified-stage12-vrs-global-id.zip "
                "--run-live-diagnostics "
                "--approve-live-rerun-after-network-preflight "
                "--prior-client-attempts 114"
            ),
            "api_key_requirement": "provide MDD_API_KEY through the inherited environment only; never put the key in command text or evidence",
        },
        "user_review_preferences": {
            "source": "explicit current user approval recorded in S68.md",
            "approved_panel_version": PANEL_VERSION,
            "approved_cadence": "8/20/22 @ 5/15/30",
            "approved_bounded_production_concurrency": PRODUCTION_MAXIMUM_CONCURRENCY,
            "approved_concurrency_condition": "Stage 14 must validate concurrency 2 end-to-end including actual persistence/database behavior",
            "approved_production_retry_policy": retry_policy_contract(),
            "approved_number_of_results": dict(PRODUCTION_NUMBER_OF_RESULTS_BY_TIER),
            "stage_13_complete_allowed": True,
        },
        "quota": {
            **quota,
            "approved_production_design": {
                "normal_retry_policy": retry_policy_contract(),
                "user_approved_design": True,
                "normal_retry_reserve_rate": PRODUCTION_RETRY_RESERVE_RATE,
                "historical_legacy_retry_sensitivity": _round(LEGACY_ADDITIONAL_ATTEMPT_SHARE, 9),
                "legacy_current_usage_observed": False,
                "legacy_reserved_conservatively": dict(LEGACY7_RESERVED_MONTHLY_REQUESTS),
                "historical_diagnostic_reserved_attempts": HISTORICAL_INITIAL_DIAGNOSTIC_BUDGET,
                "corrected_diagnostic_conservative_quota_accounting_attempts": ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
                "corrected_diagnostic_provider_quota_consumption_status": live.get(
                    "provider_quota_consumption_status"
                ),
                "thirty_day_nominal_requests": quota["projections"][0]["30_day"]["base_logical_requests"],
                "thirty_one_day_nominal_requests": quota["projections"][0]["31_day"]["base_logical_requests"],
                "thirty_day_retry_reserve_requests": ten_percent_projection["30_day"]["retry_reserve_requests"],
                "thirty_one_day_retry_reserve_requests": ten_percent_projection["31_day"]["retry_reserve_requests"],
                "thirty_day_tiered50_with_retry_reserve": ten_percent_projection["30_day"]["projected_http_requests"],
                "thirty_one_day_tiered50_with_retry_reserve": ten_percent_projection["31_day"]["projected_http_requests"],
                "thirty_day_conservative_planning_total": ten_percent_projection["30_day"]["conservative_reserve_reconciliation"]["conservative_total_http_requests"],
                "thirty_one_day_conservative_planning_total": ten_percent_projection["31_day"]["conservative_reserve_reconciliation"]["conservative_total_http_requests"],
                "automatic_retry_protection_boundary": AUTOMATIC_RETRY_PROTECTION_BOUNDARY,
                "automatic_retry_guardrail": automatic_retry_guardrail(
                    0,
                    ten_percent_projection["31_day"]["projected_http_requests"],
                ),
                "quota_safety_gate_closed_without_fabricated_current_usage": quota_safety_closed,
                "production_cadence_approved": True,
                "production_retry_policy_approved": True,
            },
        },
        "credential_consumers": _credential_consumers(),
        "number_of_results": result_limit_evidence,
        "static_event_density": density,
        "execution_duration_evidence": _load_duration_evidence(root),
        "retry_and_http_attempt_evidence": {
            "approved_production_retry_policy": retry_policy_contract(),
            "automatic_retry_quota_guardrail": {
                "boundary": AUTOMATIC_RETRY_PROTECTION_BOUNDARY,
                "rule": "block automatic retries when projected/current monthly consumption would exceed 207500",
                "stage14_required": True,
            },
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
                "historical_client_diagnostic_attempts": historical_client_attempts,
                "corrected_client_request_attempts": current_client_attempts,
                "prior_cumulative_client_request_invocations": prior_cumulative_client_attempts,
                "cumulative_client_request_invocations": prior_cumulative_client_attempts + current_client_attempts,
                "client_request_attempts_issued": current_client_attempts,
                "http_responses_received": http_responses_received,
                "confirmed_provider_http_attempts": live.get("confirmed_provider_http_attempts", 0),
                "provider_quota_consumption_status": live.get("provider_quota_consumption_status"),
                "conservative_quota_accounting_attempts": conservative_attempts,
                "historical_initial_diagnostic_budget": HISTORICAL_INITIAL_DIAGNOSTIC_BUDGET,
                "explicit_additional_user_authorization": ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
                "authorized_cumulative_client_attempt_ceiling": prior_cumulative_client_attempts + ADDITIONAL_RESULT_LIMIT_DIAGNOSTIC_ATTEMPTS,
                "retries": 0,
                "raw_payloads_persisted": False,
            },
        },
        "phase_staggering": phase,
        "heartbeat_feasibility": heartbeat,
        "sequential_vs_bounded_concurrency": {
            "decision": "APPROVED_MAXIMUM_CONCURRENCY_2" if cap2_evaluated else "NOT_ESTABLISHED",
            "approval_state": "USER_APPROVED" if cap2_evaluated else "NOT_ESTABLISHED",
            "technically_evaluated_maximum_concurrency": PRODUCTION_MAXIMUM_CONCURRENCY if cap2_evaluated else None,
            "maximum_concurrency": PRODUCTION_MAXIMUM_CONCURRENCY if cap2_evaluated else None,
            "user_approved_design": cap2_evaluated,
            "approval_rationale": [
                "sequential execution is technically feasible under current HTTP measurements",
                "bounded cap-2 was successfully tested on the retained representative sample",
                "concurrency 2 provides additional heartbeat execution margin",
                "no 429, timeout, 5xx, transport-pressure, or parse failure was observed in the bounded cap-2 sample",
                "cap-2 is not a provider-wide rate-limit claim",
            ],
            "stage14_activation_condition": "validate concurrency 2 end-to-end including actual persistence/database behavior; stop activation on provider pressure, SQL contention, persistence instability, overlap problems, or other material failure",
            "values_above_two_require_new_evidence_and_decision": True,
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
            "decision": "APPROVED_DESIGN_REQUIREMENT",
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
            "production_cadence_approved": True,
            "production_tier_membership_approved": True,
            "production_number_of_results_approved": True,
            "production_concurrency_approved": True,
            "production_retry_policy_approved": True,
            "stage_13_status": status,
            "production_activation": "NOT_STARTED",
            "limitations": remaining_limitations,
            "blockers": blockers,
            "stage_14_status": "NOT STARTED",
            "stage_14_started": False,
            "tiered50_activated": False,
            "power_bi_modified": False,
            "production_database_objects_created": False,
            "windows_scheduled_tasks_created": False,
        },
    }


def _validate_final_evidence(evidence: Mapping[str, Any]) -> None:
    """Validate the closed Stage 13 design without network or live side effects."""

    required = {
        "artifact_version",
        "record_type",
        "stage_13_status",
        "user_decision_gate",
        "stage_12_gate",
        "candidate_cadence",
        "cadence_boundary_review",
        "quota",
        "retry_policy",
        "number_of_results",
        "number_of_results_comparison",
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
    final_status = "COMPLETE — APPROVED TIERED COLLECTOR READINESS DESIGN; PRODUCTION NOT STARTED"
    if evidence.get("stage_13_status") != final_status:
        raise ValueError("Stage 13 is not in the final approved readiness state")

    stage12 = evidence["stage_12_gate"]
    if stage12.get("approved_panel_version") != PANEL_VERSION or stage12.get("panel_station_count") != 50:
        raise ValueError("approved Stage 12 panel must remain TIERED50_PANEL_V1 with exactly 50 stations")
    if stage12.get("panel_membership_changed") is not False:
        raise ValueError("Stage 12 panel membership changed")

    density = evidence["static_event_density"]
    rows = list(density.get("station_tier_rows", []))
    if density.get("status") != "COMPLETE" or len(rows) != 50:
        raise ValueError("final density evidence must contain exactly 50 complete station rows")
    if density.get("tier_assignment", {}).get("tier_counts") != {"A": 8, "B": 20, "C": 22}:
        raise ValueError("final density tier counts are not 8/20/22")
    ordered = sorted(rows, key=lambda row: int(row["density_rank"]))
    if [int(row["density_rank"]) for row in ordered] != list(range(1, 51)):
        raise ValueError("density ranks must be the complete immutable 1–50 ordering")
    expected_tiers = ["A"] * 8 + ["B"] * 20 + ["C"] * 22
    if [row.get("production_tier") for row in ordered] != expected_tiers:
        raise ValueError("final tier membership is not density ranks 1–8 / 9–28 / 29–50")
    if density.get("tier_assignment", {}).get("stage12_selection_rank_used") is not False:
        raise ValueError("mutable Stage 12 SelectionRank was used for production tiers")

    plan = evidence["candidate_cadence"].get("user_approved_design", {})
    if plan.get("logical_requests_per_hour") != 220 or plan.get("monthly_logical_requests") != {
        "30_day": 158400,
        "31_day": 163680,
    }:
        raise ValueError("approved cadence arithmetic does not reconcile to 220/hour")
    expected_plan = {"A": (8, 5), "B": (20, 15), "C": (22, 30)}
    for tier in plan.get("tiers", []):
        if expected_plan.get(str(tier.get("tier"))) != (
            int(tier.get("station_count", -1)),
            int(tier.get("interval_minutes", -1)),
        ):
            raise ValueError("approved cadence/tier plan is not 8/20/22 @ 5/15/30")

    static_support = density.get("final_approved_static_support", {})
    if static_support.get("station_p95_supported_count") != 50 or not static_support.get("all_station_p95_supported"):
        raise ValueError("final static P95 support is not 50/50")
    expected_support = {
        "A": (8, 20, 19, 20),
        "B": (20, 30, 23, 23),
        "C": (22, 30, 29, 29),
    }
    for tier_name, (count, result_limit, p95_max, observed_max) in expected_support.items():
        row = static_support.get("by_tier", {}).get(tier_name, {})
        if (
            row.get("station_count"),
            row.get("result_limit"),
            row.get("maximum_station_p95"),
            row.get("maximum_observed_static_scheduled_arrival_count"),
        ) != (count, result_limit, p95_max, observed_max):
            raise ValueError(f"final exact-feed static support mismatch for Tier {tier_name}")

    result_limits = evidence["number_of_results"]
    if result_limits.get("production_number_of_results") != dict(PRODUCTION_NUMBER_OF_RESULTS_BY_TIER):
        raise ValueError("approved tier-specific NumberOfResults policy is not A20/B30/C30")
    if result_limits.get("decision") != "APPROVED_USER_DESIGN_A20_B30_C30":
        raise ValueError("NumberOfResults approval decision is missing")
    if result_limits.get("provider_maximum_status") != "PROVIDER_MAXIMUM_NOT_ESTABLISHED":
        raise ValueError("provider maximum boundary was weakened")
    if result_limits.get("corrected_live_limitation") != CORRECTED_LIVE_FORWARD_COVERAGE_LIMITATION:
        raise ValueError("corrected live forward-coverage limitation was changed")
    comparison = evidence["number_of_results_comparison"]
    if comparison.get("tier_specific_a20_b30_c30", {}).get("decision") != "APPROVED_USER_DESIGN_STATIC_SUPPORTED_LIVE_LIMITATION_RETAINED":
        raise ValueError("approved tier-specific NumberOfResults comparison is missing")

    gate = evidence["user_decision_gate"]
    required_gate_flags = {
        "production_number_of_results_approved",
        "production_concurrency_approved",
        "production_cadence_approved",
        "production_tier_membership_approved",
        "production_retry_policy_approved",
        "user_approval_received",
        "all_material_design_choices_user_approved",
        "stage_13_complete_allowed",
    }
    if any(gate.get(flag) is not True for flag in required_gate_flags):
        raise ValueError("final Stage 13 user-decision gate is not fully approved")

    retry = evidence["retry_policy"]
    if retry.get("max_attempts") != 2 or retry.get("automatic_retry_count_max") != 1:
        raise ValueError("approved retry policy must allow at most one retry")
    if retry.get("approved_transient_http_statuses") != [408, 429, 500, 502, 503, 504]:
        raise ValueError("approved transient HTTP retry statuses changed")
    if retry.get("do_not_retry_http_statuses") != [401, 403]:
        raise ValueError("401/403 retry prohibition changed")
    if evidence["quota"]["approved_production_design"].get("automatic_retry_protection_boundary") != 207500:
        raise ValueError("automatic-retry quota protection boundary changed")

    approved_projection = next(
        item for item in evidence["quota"]["projections"]
        if item.get("label") == "approved_normal_retry_reserve"
    )
    expected_quota = {
        "30_day": (158400, 15840, 174240, 182980),
        "31_day": (163680, 16368, 180048, 189076),
    }
    for day, (base, reserve, tiered, total) in expected_quota.items():
        projection = approved_projection[day]
        reconciliation = projection["conservative_reserve_reconciliation"]
        if projection.get("base_logical_requests") != base or projection.get("retry_reserve_requests") != reserve:
            raise ValueError(f"{day} nominal/retry reserve arithmetic does not reconcile")
        if projection.get("projected_http_requests") != tiered:
            raise ValueError(f"{day} Tiered50 retry-reserve projection does not reconcile")
        if reconciliation.get("conservative_total_http_requests") != total:
            raise ValueError(f"{day} conservative planning total does not reconcile")

    phase = evidence["phase_staggering"]
    if phase.get("total_due_targets_by_heartbeat") != [19, 18, 18, 18, 19, 18]:
        raise ValueError("final heartbeat phase distribution is not the persisted balanced order")
    if phase.get("super_cycle_total_logical_requests") != 110 or phase.get("hour_total_logical_requests") != 220:
        raise ValueError("final phase super-cycle/hour totals do not reconcile")
    if phase.get("max_due_targets_in_one_heartbeat") != 19:
        raise ValueError("final maximum heartbeat load is not 19")
    phase_contract = phase.get("phase_assignment_contract", {})
    if phase_contract.get("assignment_key") != PHASE_ASSIGNMENT_IDENTITY:
        raise ValueError("phase assignment identity must be the approved parent StopPointRef")
    if phase_contract.get("assignment_hash") != PHASE_ASSIGNMENT_HASH:
        raise ValueError("phase assignment hash must use source_parent_station_id")
    if phase_contract.get("phase_assignment_finalized_in_stage13") is not True:
        raise ValueError("phase assignment finalization is missing")
    if phase_contract.get("production_phase_index_persisted") is not False:
        raise ValueError("Stage 13 must not claim production phase indexes are persisted")
    if phase_contract.get("phase_index_must_be_persisted_in_stage14") is not True:
        raise ValueError("Stage 14 phase-index persistence requirement is missing")
    if phase_contract.get("materialization_status") != PHASE_INDEX_MATERIALIZATION_STATUS:
        raise ValueError("Stage 14 phase materialization status changed")
    if phase_contract.get("stage14_must_materialize_without_recomputing_from_database_ids") is not True:
        raise ValueError("Stage 14 must materialize phase indexes without database-ID recomputation")
    if "phase_index_is_persisted" in phase_contract:
        raise ValueError("ambiguous phase_index_is_persisted terminology remains")
    if phase.get("tier_phase_distribution") != [
        {
            "tier": "A",
            "interval_minutes": 5,
            "phase_count": 1,
            "phase_target_counts": [8],
            "due_targets_by_heartbeat": [8, 8, 8, 8, 8, 8],
        },
        {
            "tier": "B",
            "interval_minutes": 15,
            "phase_count": 3,
            "phase_target_counts": [7, 7, 6],
            "due_targets_by_heartbeat": [7, 7, 6, 7, 7, 6],
        },
        {
            "tier": "C",
            "interval_minutes": 30,
            "phase_count": 6,
            "phase_target_counts": [4, 3, 4, 3, 4, 4],
            "due_targets_by_heartbeat": [4, 3, 4, 3, 4, 4],
        },
    ]:
        raise ValueError("final persisted tier phase capacities changed")
    assignments = phase.get("phase_assignments", [])
    if len(assignments) != 50 or len({row.get("phase_assignment_identity") for row in assignments}) != 50:
        raise ValueError("all 50 final phase assignments must be unique and auditable")
    if any(
        row.get("phase_assignment_identity_type") != PHASE_ASSIGNMENT_IDENTITY
        or row.get("source_parent_station_id") != row.get("phase_assignment_identity")
        or row.get("assignment_basis") != PHASE_ASSIGNMENT_HASH
        for row in assignments
    ):
        raise ValueError("phase assignment rows do not use the corrected parent StopPointRef identity")
    if assignments != phase_assignments_for_tier_rows(rows):
        raise ValueError("phase assignments are not reproducible from immutable identity")
    reproduction = phase.get("phase_assignment_reproduction", {})
    if reproduction.get("phase_indexes_reproduced_exactly") is not True:
        raise ValueError("approved phase indexes were not reproduced exactly under the corrected identity")
    if reproduction.get("compared_assignment_count") != 50:
        raise ValueError("phase-index reproduction did not compare all 50 assignments")
    if reproduction.get("sampling_target_id_values_participate_in_phase_hash") is not False:
        raise ValueError("database-generated SamplingTargetId values must not participate in phase assignment")

    concurrency = evidence["sequential_vs_bounded_concurrency"]
    if concurrency.get("maximum_concurrency") != PRODUCTION_MAXIMUM_CONCURRENCY or not concurrency.get("user_approved_design"):
        raise ValueError("approved concurrency must be bounded MaximumConcurrency=2")
    if concurrency.get("values_above_two_require_new_evidence_and_decision") is not True:
        raise ValueError("concurrency-above-two decision guardrail is missing")

    heartbeat = evidence["heartbeat_feasibility"]
    normal_observed = heartbeat.get("NORMAL_OBSERVED_HEARTBEAT_FEASIBILITY", {})
    degraded = heartbeat.get("DEGRADED_MODE_HEARTBEAT_BUDGET", {})
    if normal_observed.get("successful_observation_only") is not True:
        raise ValueError("normal heartbeat feasibility must be explicitly observation-only")
    if normal_observed.get("not_a_guarantee_under_timeout_or_retry_failure") is not True:
        raise ValueError("normal timing evidence must not be represented as degraded-mode guarantee")
    if degraded.get("max_due_targets") != 19:
        raise ValueError("degraded heartbeat pressure must cover the 19-target maximum")
    if (
        degraded.get("maximum_concurrency"),
        degraded.get("per_attempt_timeout_seconds"),
        degraded.get("max_attempts"),
    ) != (2, PRODUCTION_REQUEST_TIMEOUT_SECONDS, 2):
        raise ValueError("degraded heartbeat pressure inputs changed")
    if degraded.get("theoretical_timeout_heavy_attempt_count") != 38:
        raise ValueError("degraded heartbeat attempt pressure does not reconcile")
    if degraded.get("theoretical_timeout_heavy_wall_seconds_at_cap2") != 570:
        raise ValueError("degraded heartbeat timeout pressure does not reconcile to 570 seconds")
    if degraded.get("unconstrained_timeout_heavy_batch_exceeds_300_seconds") is not True:
        raise ValueError("degraded timeout-heavy pressure must exceed one heartbeat")
    degraded_contract = degraded.get("deadline_contract", {})
    if degraded_contract.get("target_budget_exhaustion_outcome") != SKIPPED_HEARTBEAT_BUDGET:
        raise ValueError("target heartbeat-budget outcome is missing")
    if degraded_contract.get("retry_budget_exhaustion_outcome") != RETRY_SKIPPED_HEARTBEAT_BUDGET:
        raise ValueError("retry heartbeat-budget outcome is missing")
    if degraded_contract.get("budget_outcomes_trigger_catch_up") is not False:
        raise ValueError("heartbeat-budget outcomes must not trigger catch-up")
    if degraded_contract.get("per_target_budget_isolation") is not True:
        raise ValueError("heartbeat budget exhaustion must isolate unrelated targets")
    if degraded_contract.get("budget_exhaustion_does_not_abort_unrelated_targets") is not True:
        raise ValueError("heartbeat budget exhaustion must not abort unrelated targets")
    if degraded_contract.get("retry_after_cannot_cross_heartbeat_deadline") is not True:
        raise ValueError("Retry-After must not cross the heartbeat deadline")
    if degraded_contract.get("expired_dispatcher_cannot_execute_prior_bucket") is not True:
        raise ValueError("expired dispatchers must not execute a prior bucket")
    safety_margin = degraded_contract.get("persistence_cleanup_safety_margin", {})
    if safety_margin.get("stage13_concrete_seconds") is not None:
        raise ValueError("Stage 13 must not invent a persistence/cleanup safety margin")
    if safety_margin.get("required_before_stage14_activation") is not True:
        raise ValueError("Stage 14 safety-margin validation requirement is missing")
    if safety_margin.get("arbitrary_stage13_duration_invented") is not False:
        raise ValueError("Stage 13 safety-margin terminology is inconsistent")

    overlap = evidence["overlap_and_duplicate_execution_contract"]
    if overlap.get("unique_execution_identity") != ["SamplingTargetId", "LogicalDueBucket"]:
        raise ValueError("unique execution identity contract changed")
    if overlap.get("catch_up_behavior") is not False or overlap.get("production_scheduler_implemented") is not False:
        raise ValueError("historical catch-up or scheduler implementation boundary changed")

    historical_records = result_limits.get("historical_invalidated_sequential_records", [])
    if len(historical_records) != 24:
        raise ValueError("historical sequential evidence must retain 24 records")

    def require_population_accounting(label: str, aggregate: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> None:
        expected = _request_accounting(
            len(records),
            sum(row.get("http_status") is not None for row in records),
            conservative_quota_accounting_attempts=len(records),
        )
        for field in (
            "client_request_attempts_issued",
            "http_responses_received",
            "confirmed_provider_http_attempts",
            "provider_quota_consumption_status",
            "conservative_quota_accounting_attempts",
        ):
            if aggregate.get(field) != expected[field]:
                raise ValueError(f"{label} accounting does not reconcile from retained records")

    require_population_accounting(
        "historical sequential diagnostic",
        evidence["sequential_vs_bounded_concurrency"].get("sequential_live_diagnostic", {}),
        historical_records,
    )
    cap2 = evidence["sequential_vs_bounded_concurrency"].get("bounded_live_diagnostic", {})
    cap2_records = cap2.get("records", [])
    if len(cap2_records) != 6 or any(row.get("http_status") != 200 for row in cap2_records):
        raise ValueError("retained cap-2 evidence must contain six HTTP 200 records")
    require_population_accounting("cap-2 diagnostic", cap2, cap2_records)
    failed = result_limits.get("failed_corrected_diagnostic_audit", {})
    if {
        failed.get("client_request_attempts_issued"),
        failed.get("http_responses_received"),
        failed.get("confirmed_provider_http_attempts"),
        failed.get("provider_quota_consumption_status"),
        failed.get("conservative_quota_accounting_attempts"),
    } != {
        24,
        0,
        0,
        PROVIDER_QUOTA_UNKNOWN_BECAUSE_NO_HTTP_RESPONSE,
        24,
    }:
        raise ValueError("failed corrected diagnostic accounting changed")
    if failed.get("status") != "INVALID_FOR_NUMBER_OF_RESULTS_DECISION":
        raise ValueError("failed corrected diagnostic must remain invalid for final coverage evidence")
    additional_gate = result_limits.get("additional_request_gate", {})
    if (
        additional_gate.get("historical_initial_diagnostic_budget") != 100
        or additional_gate.get("explicit_additional_user_authorization") != 24
        or additional_gate.get("prior_cumulative_client_request_invocations") != 90
        or additional_gate.get("authorized_cumulative_client_attempt_ceiling") != 114
    ):
        raise ValueError("historical 100-budget and explicit 24 authorization are not separated")

    readiness = evidence["readiness_decision"]
    for flag in (
        "production_cadence_approved",
        "production_tier_membership_approved",
        "production_number_of_results_approved",
        "production_concurrency_approved",
        "production_retry_policy_approved",
    ):
        if readiness.get(flag) is not True:
            raise ValueError(f"readiness decision {flag} is not approved")
    if readiness.get("stage_14_started") or readiness.get("stage_14_status") != "NOT STARTED":
        raise ValueError("Stage 14 must remain NOT STARTED")
    if readiness.get("tiered50_activated") or readiness.get("production_activation") != "NOT_STARTED":
        raise ValueError("Tiered50 production must remain inactive")
    if readiness.get("production_database_objects_created") or readiness.get("windows_scheduled_tasks_created") or readiness.get("power_bi_modified"):
        raise ValueError("Stage 13 created or modified a prohibited production artifact")
    if evidence["historical_continuity"].get("cutover_performed") is not False:
        raise ValueError("historical Legacy7/Tiered50 cutover occurred in Stage 13")


def validate_evidence(evidence: Mapping[str, Any]) -> None:
    """Validate the final Stage 13 machine-readable schema and hard boundaries."""

    return _validate_final_evidence(evidence)


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
        help="run a newly approved corrected result-limit diagnostic with zero-request DNS preflight and a fail-fast smoke gate",
    )
    parser.add_argument(
        "--approve-additional-mdd-requests",
        action="store_true",
        help="legacy compatibility flag; it does not authorize a new live rerun",
    )
    parser.add_argument(
        "--prior-client-attempts",
        "--prior-http-attempts",
        dest="prior_client_attempts",
        type=int,
        default=0,
        help="prior cumulative Stage 13 client invocations (legacy alias: --prior-http-attempts)",
    )
    parser.add_argument(
        "--reuse-existing-live-evidence",
        action="store_true",
        help="reuse the current sanitized live diagnostic without issuing new MDD requests",
    )
    parser.add_argument(
        "--approve-live-rerun-after-network-preflight",
        action="store_true",
        help="explicitly approve a new live rerun after reviewing the offline preflight/fail-fast correction; the previous 24-invocation approval is not reused",
    )
    args = parser.parse_args()
    if args.prior_client_attempts < 0:
        parser.error("--prior-client-attempts must be non-negative")
    if args.run_live_diagnostics and not args.approve_live_rerun_after_network_preflight:
        parser.error("--run-live-diagnostics requires --approve-live-rerun-after-network-preflight")
    evidence = build_evidence(
        gtfs_zip=args.gtfs_zip,
        run_live_diagnostics=args.run_live_diagnostics,
        prior_live_attempts=args.prior_client_attempts,
        reuse_existing_live=args.reuse_existing_live_evidence,
        additional_mdd_requests_approved=args.approve_additional_mdd_requests,
        live_rerun_approved=args.approve_live_rerun_after_network_preflight,
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
