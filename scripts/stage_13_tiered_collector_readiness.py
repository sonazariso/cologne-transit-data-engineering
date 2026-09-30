#!/usr/bin/env python3
"""Offline Stage 13 Tiered50 readiness audit.

This module reads only source-controlled evidence.  It deliberately has no
network, database, scheduler, or collector path.  The generated artifact is a
readiness decision, not a production activation mechanism.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPOSITORY_ROOT / "docs" / "stages" / "13-tiered-collector-readiness-evidence.json"

MONTHLY_CEILING = 250_000
NORMAL_ENVELOPE_MIN = 207_500
NORMAL_ENVELOPE_MAX = 210_000
HEARTBEAT_MINUTES = 5
HEARTBEATS_PER_SUPER_CYCLE = 6
HEARTBEAT_SECONDS = HEARTBEAT_MINUTES * 60
LEGACY_EXECUTIONS = 1_599
LEGACY_HTTP_ATTEMPTS = 1_629
LEGACY_ADDITIONAL_RETRY_ATTEMPTS = 35
LEGACY_ADDITIONAL_ATTEMPT_SHARE = LEGACY_ADDITIONAL_RETRY_ATTEMPTS / LEGACY_HTTP_ATTEMPTS

TIERED50_CANDIDATE = (
    {"tier": "A", "station_count": 10, "interval_minutes": 5},
    {"tier": "B", "station_count": 20, "interval_minutes": 10},
    {"tier": "C", "station_count": 20, "interval_minutes": 30},
)


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


def duration_statistics(values: Sequence[float]) -> dict[str, Any]:
    """Summarize one homogeneous execution-mode population in milliseconds."""

    if not values:
        return {
            "count": 0,
            "min_ms": None,
            "p50_ms": None,
            "p90_ms": None,
            "p95_ms": None,
            "p99_ms": None,
            "max_ms": None,
        }
    ordered = sorted(float(value) for value in values)
    return {
        "count": len(ordered),
        "min_ms": _round(ordered[0], 3),
        "p50_ms": _round(percentile(ordered, 0.50), 3),
        "p90_ms": _round(percentile(ordered, 0.90), 3),
        "p95_ms": _round(percentile(ordered, 0.95), 3),
        "p99_ms": _round(percentile(ordered, 0.99), 3),
        "max_ms": _round(ordered[-1], 3),
    }


def candidate_cadence(tiers: Sequence[Mapping[str, int]] = TIERED50_CANDIDATE) -> dict[str, Any]:
    """Calculate logical request load for the historical Tiered50 candidate."""

    tier_rows: list[dict[str, Any]] = []
    requests_per_hour = 0
    for tier in tiers:
        station_count = int(tier["station_count"])
        interval_minutes = int(tier["interval_minutes"])
        per_hour = station_count * (60 // interval_minutes)
        row = {
            "tier": tier["tier"],
            "station_count": station_count,
            "interval_minutes": interval_minutes,
            "logical_requests_per_hour": per_hour,
            "logical_requests_per_day": per_hour * 24,
        }
        tier_rows.append(row)
        requests_per_hour += per_hour

    requests_per_day = requests_per_hour * 24
    monthly = {
        "30_day": requests_per_day * 30,
        "31_day": requests_per_day * 31,
    }
    return {
        "candidate_status": "CALCULATED_ONLY_NOT_APPROVED",
        "tiers": tier_rows,
        "logical_requests_per_hour": requests_per_hour,
        "logical_requests_per_day": requests_per_day,
        "monthly_logical_requests": monthly,
        "reconciliation": {
            "requests_per_30_minutes": requests_per_hour // 2,
            "requests_per_hour_from_30_minute_super_cycle": (requests_per_hour // 2) * 2,
            "expected_30_minute_requests": 140,
            "expected_hour_requests": 280,
        },
    }


def quota_projection(
    logical_requests_per_hour: int,
    days: int,
    additional_attempt_rate: float = 0.0,
) -> dict[str, Any]:
    """Project base and retry-sensitive request counts.

    ``additional_attempt_rate`` is an explicit scenario parameter.  It is not
    an observed production retry rate unless the caller supplies an observed
    evidence ratio and labels it accordingly.
    """

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


def quota_projection_set(logical_requests_per_hour: int) -> dict[str, Any]:
    scenarios = [
        {
            "label": "no_additional_attempts",
            "kind": "calculation_scenario",
            "additional_attempt_rate": 0.0,
        },
        {
            "label": "legacy_observed_additional_attempt_share",
            "kind": "observed_legacy_evidence_applied_as_sensitivity_only",
            "additional_attempt_rate": LEGACY_ADDITIONAL_ATTEMPT_SHARE,
            "not_an_observed_tiered50_retry_rate": True,
        },
        {
            "label": "five_percent_sensitivity",
            "kind": "calculation_scenario",
            "additional_attempt_rate": 0.05,
        },
        {
            "label": "ten_percent_sensitivity",
            "kind": "calculation_scenario",
            "additional_attempt_rate": 0.10,
        },
    ]
    return {
        "monthly_ceiling": MONTHLY_CEILING,
        "normal_operational_planning_envelope": {
            "minimum": NORMAL_ENVELOPE_MIN,
            "maximum": NORMAL_ENVELOPE_MAX,
            "percentage_of_ceiling": {"minimum": 83.0, "maximum": 84.0},
        },
        "unallocated_headroom_before_candidate": {
            "to_ceiling_minimum": MONTHLY_CEILING - NORMAL_ENVELOPE_MIN,
            "to_ceiling_maximum": MONTHLY_CEILING - NORMAL_ENVELOPE_MAX,
        },
        "projections": [
            {
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
            for scenario in scenarios
        ],
        "accounting_rules": [
            "Each retry/additional attempt is one additional HTTP request.",
            "NumberOfResults changes response cardinality, not HTTP request count.",
            "Duplicate claimed target/buckets must not issue another HTTP request.",
            "No catch-up burst may spend quota on missed historical buckets.",
            "No quota reserve is assigned to completed or inactive diagnostics.",
            "Current shared-account production usage remains unavailable and must be audited before activation.",
        ],
    }


def heartbeat_phase_distribution() -> dict[str, Any]:
    tier_a = [10] * HEARTBEATS_PER_SUPER_CYCLE
    tier_b = [10] * HEARTBEATS_PER_SUPER_CYCLE
    tier_c = [4, 3, 3, 4, 3, 3]
    totals = [a + b + c for a, b, c in zip(tier_a, tier_b, tier_c)]
    return {
        "heartbeat_interval_minutes": HEARTBEAT_MINUTES,
        "super_cycle_minutes": HEARTBEAT_MINUTES * HEARTBEATS_PER_SUPER_CYCLE,
        "tier_a_due_targets_by_heartbeat": tier_a,
        "tier_b_due_targets_by_heartbeat": tier_b,
        "tier_c_due_targets_by_heartbeat": tier_c,
        "total_due_targets_by_heartbeat": totals,
        "super_cycle_total_logical_requests": sum(totals),
        "hour_total_logical_requests": sum(totals) * 2,
        "expected_sequence": [24, 23, 23, 24, 23, 23],
        "phase_distribution_reconciles": totals == [24, 23, 23, 24, 23, 23],
        "phase_assignment_contract": {
            "tier_a": "all 10 targets are due on every heartbeat",
            "tier_b": "20 targets are persisted in two phases of 10; one phase is due on each heartbeat",
            "tier_c": "20 targets are persisted in six phases with counts 4/3/3/4/3/3",
            "assignment_key": "immutable SamplingTargetId",
            "phase_index_is_persisted": True,
            "independent_of_mutable_rank_order": True,
            "materialization_status": "WAITING_FOR_STAGE_12_FIXED_PANEL",
        },
    }


def deterministic_phase_index(sampling_target_id: str, phase_count: int) -> int:
    """Return a stable candidate phase index from an immutable target ID.

    This helper is an auditable assignment rule for tests/design review only.
    Stage 12 must materialize and persist each result in its approved panel;
    this module does not create or activate a scheduler.
    """

    if not sampling_target_id:
        raise ValueError("sampling_target_id must not be empty")
    if phase_count <= 0:
        raise ValueError("phase_count must be positive")
    digest = hashlib.sha256(sampling_target_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % phase_count


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
    stage3a_path = root / "stage03a-mdd-discovery.jsonl"
    stage3a_rows = _load_jsonl(stage3a_path)
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
    stage9_rows = [
        row for row in stage9_summary.get("mdd_request_contexts", []) if row.get("elapsed_ms") is not None
    ]

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
            "duration_statistics": {
                "count": 343,
                "min_ms": None,
                "p50_ms": None,
                "p90_ms": None,
                "p95_ms": None,
                "p99_ms": None,
                "max_ms": None,
            },
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


def _result_limit_evidence(root: Path) -> dict[str, Any]:
    stage3a_rows = _load_jsonl(root / "stage03a-mdd-discovery.jsonl")
    station_rows = [
        row for row in stage3a_rows if row.get("record_type") == "mdd_discovery_station_summary"
    ]
    stage9_path = root / "docs" / "stages" / "09-source-backed-matching-summary.json"
    stage9 = json.loads(stage9_path.read_text(encoding="utf-8")) if stage9_path.exists() else {}
    stage9_contexts = stage9.get("mdd_request_contexts", [])
    return {
        "decision": "BLOCKED_NO_PRODUCTION_VALUE_SELECTED",
        "production_number_of_results": None,
        "required_blocker": "PROVIDER_MAXIMUM_NOT_ESTABLISHED",
        "provider_maximum_status": "PROVIDER_MAXIMUM_NOT_ESTABLISHED",
        "evidence_samples": [
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
                "coverage_interpretation": "all observed responses reached N=5; N=5 is not a defensible production limit",
            },
            {
                "source": "docs/stages/08-small-realtime-pilot-blocked.md",
                "execution_mode": "one-hour Köln Hbf Stage 8 pilot",
                "station_scope": "one station only",
                "number_of_results": 20,
                "request_count": 343,
                "returned_result_count_total": 6860,
                "saturation_basis": "aggregate 6860 equals 343 × 20; per-response rows were cleaned after evidence extraction",
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
                "coverage_interpretation": "all three bounded responses reached N=20; no forward-horizon or provider-maximum evidence",
            },
        ],
        "audit_questions": {
            "historical_returned_result_distribution": "bounded distributions are available only for the listed diagnostic populations",
            "saturation_at_configured_limit": "observed at both N=5 and N=20 in bounded samples",
            "forward_temporal_coverage": "NOT_ESTABLISHED",
            "station_or_tier_differences": "NOT_ESTABLISHED; the fixed panel is approved but production tiers and tier-specific evidence are not assigned",
            "static_scheduled_event_density": "NOT_ESTABLISHED in this Stage 13 audit; Stage 12 static panel evidence is separate and does not assign production tiers",
            "fixed_result_limit_defensible": False,
            "tier_specific_limits_materially_beneficial": "NOT_ESTABLISHED",
        },
        "application_validation_ranges_are_not_provider_evidence": True,
    }


def _credential_consumers() -> list[dict[str, Any]]:
    return [
        {
            "caller": "scripts/stage_08_mdd_realtime_pilot.py",
            "purpose": "bounded one-station Stage 8 pilot",
            "normal_cadence": "none; manual bounded run, historical execution completed",
            "retry_policy": "zero retries; 30-second timeout; overlap slots skipped",
            "mode": "bounded diagnostic",
            "production_or_scheduled": False,
            "include_in_monthly_quota_planning": "historical actual only; no forward reserve",
            "maximum_or_observed_requests": 1200,
        },
        {
            "caller": "scripts/stage_08_authorization_request_contract_diagnostic.py",
            "purpose": "manual Hbf N5/N20 authorization and request-breadth controls",
            "normal_cadence": "none; manual diagnostic",
            "retry_policy": "zero retries; decision-tree controls only",
            "mode": "bounded diagnostic",
            "production_or_scheduled": False,
            "include_in_monthly_quota_planning": "no forward reserve; run only under explicit diagnostic approval",
            "maximum_or_observed_requests": "bounded controls; exact historical count not retained in this checkout",
        },
        {
            "caller": "scripts/stage_03a_disruption_evidence_probe.py",
            "purpose": "Stage 3A MDD Situation/disruption evidence and ten-station discovery",
            "normal_cadence": "none; manual host-only diagnostic",
            "retry_policy": "zero retries; no fallback; sequential",
            "mode": "bounded diagnostic",
            "production_or_scheduled": False,
            "include_in_monthly_quota_planning": "historical actual only; no forward reserve",
            "maximum_or_observed_requests": 10,
        },
        {
            "caller": "scripts/stage_09_source_backed_acceptance.py",
            "purpose": "bounded Stage 9 source-backed matching acceptance",
            "normal_cadence": "none; manual bounded probe",
            "retry_policy": "zero retries; one request per up to three stations",
            "mode": "bounded diagnostic",
            "production_or_scheduled": False,
            "include_in_monthly_quota_planning": "historical actual only; no forward reserve",
            "maximum_or_observed_requests": 3,
        },
        {
            "caller": "scripts/mdd_recorded_time_lifecycle_probe.py",
            "purpose": "manual near-event RecordedAtTime lifecycle validation",
            "normal_cadence": "none; historical diagnostic",
            "retry_policy": "zero retries; seven sequential attempts maximum",
            "mode": "bounded diagnostic",
            "production_or_scheduled": False,
            "include_in_monthly_quota_planning": "historical actual only; no forward reserve",
            "maximum_or_observed_requests": 7,
        },
        {
            "caller": "scripts/stage_02a2_probe.py",
            "purpose": "manual one-request MDD raw-response contract diagnostic",
            "normal_cadence": "none; historical diagnostic",
            "retry_policy": "zero retries; one request maximum",
            "mode": "inactive diagnostic",
            "production_or_scheduled": False,
            "include_in_monthly_quota_planning": "no; obsolete/inactive for Stage 13",
            "maximum_or_observed_requests": 1,
        },
        {
            "caller": "scripts/stage_02a3_historical_actual_time_probe.py",
            "purpose": "manual historical-horizon availability diagnostic",
            "normal_cadence": "none; historical diagnostic",
            "retry_policy": "zero retries; one request per configured horizon",
            "mode": "inactive diagnostic",
            "production_or_scheduled": False,
            "include_in_monthly_quota_planning": "no; obsolete/inactive for Stage 13",
            "maximum_or_observed_requests": 5,
        },
        {
            "caller": "Legacy Collector / ctl.MddCollectorRun",
            "purpose": "historical external production collector outside this repository",
            "normal_cadence": "current cadence unavailable in restart repository",
            "retry_policy": "historical audit records 35 additional attempts; current policy unavailable",
            "mode": "external production history / current state unknown",
            "production_or_scheduled": "not repository-controlled",
            "include_in_monthly_quota_planning": "must be included before activation; current usage unavailable",
            "maximum_or_observed_requests": {
                "historical_executions": LEGACY_EXECUTIONS,
                "historical_http_attempts": LEGACY_HTTP_ATTEMPTS,
                "historical_additional_retry_attempts": LEGACY_ADDITIONAL_RETRY_ATTEMPTS,
            },
        },
    ]


def _static_event_density(
    root: Path,
    stage4: Mapping[str, Any],
    *,
    stage12_approved: bool = False,
) -> dict[str, Any]:
    metadata = stage4.get("source_metadata", {})
    scope_counts = stage4.get("scope_counts", {})
    expected_raw_snapshot_names = [
        "vrs-global-id-current.zip",
        "data/vrs-global-id-current.zip",
        "raw/vrs-global-id-current.zip",
    ]
    raw_snapshot_paths = [name for name in expected_raw_snapshot_names if (root / name).exists()]
    density_blockers = ["APPROVED_STAGE_4_RAW_GTFS_SNAPSHOT_NOT_RETAINED"]
    if not stage12_approved:
        density_blockers.insert(0, "STAGE_12_PANEL_APPROVAL_REQUIRED")
    else:
        density_blockers.insert(0, "STAGE12_TIER_ASSIGNMENT_AND_FORWARD_HORIZON_NOT_RECONCILED_IN_THIS_AUDIT")
    return {
        "status": "NOT_ESTABLISHED",
        "blockers": density_blockers,
        "source_snapshot": {
            "feed_version": metadata.get("feed_info", {}).get("feed_version"),
            "feed_start_date": metadata.get("feed_info", {}).get("feed_start_date"),
            "feed_end_date": metadata.get("feed_info", {}).get("feed_end_date"),
            "zip_sha256": metadata.get("source_metadata", {}).get("zip_sha256")
            if isinstance(metadata.get("source_metadata"), Mapping)
            else metadata.get("zip_sha256"),
            "evidence_path": "docs/stages/04-cologne-scope-mode-evidence.json",
            "raw_snapshot_paths_found": raw_snapshot_paths,
        },
        "aggregate_context_not_a_density_measure": {
            "cologne_scheduled_stop_events": scope_counts.get("cologne_scheduled_stop_events"),
            "cologne_distinct_stop_ids": scope_counts.get("cologne_distinct_stop_ids"),
            "interpretation": "aggregate Stage 4 scope baselines only; no station/window density is inferred",
        },
        "row_contract": [
            "station",
            "tier",
            "scheduled_event_density",
            "forward_window",
            "result_limit_coverage_status",
        ],
        "station_tier_rows": [],
        "distribution": {
            "count": 0,
            "p50": None,
            "p90": None,
            "p95": None,
            "p99": None,
            "outliers": [],
        },
        "coverage_conclusion": "NOT_ESTABLISHED; do not claim that N=20 covers the intended forward horizon",
        "static_vs_realtime_boundary": "scheduled static density is planning evidence only and is not realtime response cardinality",
    }


def build_evidence(
    root: Path = REPOSITORY_ROOT,
    *,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    stage4_path = root / "docs" / "stages" / "04-cologne-scope-mode-evidence.json"
    stage4 = json.loads(stage4_path.read_text(encoding="utf-8"))
    cadence = candidate_cadence()
    phase = heartbeat_phase_distribution()
    expected_stage12_paths = [
        "docs/stages/12-50-station-selection.md",
        "docs/stages/12-50-station-selection-completion-decision.md",
        "docs/stages/12-50-station-selection-evidence.json",
    ]
    stage12_paths_found = [path for path in expected_stage12_paths if (root / path).exists()]
    stage12_evidence_path = root / "docs" / "stages" / "12-50-station-selection-evidence.json"
    stage12_evidence = (
        json.loads(stage12_evidence_path.read_text(encoding="utf-8"))
        if stage12_evidence_path.exists()
        else {}
    )
    stage12_approved = (
        len(stage12_paths_found) == len(expected_stage12_paths)
        and stage12_evidence.get("status") == "COMPLETE — APPROVED FIXED 50-STATION PANEL"
        and stage12_evidence.get("panel_version") == "TIERED50_PANEL_V1"
    )
    stage12_blocker = None if stage12_approved else "STAGE_12_PANEL_APPROVAL_REQUIRED"
    remaining_blockers = [
        "PROVIDER_MAXIMUM_NOT_ESTABLISHED",
        "STATIC_EVENT_DENSITY_NOT_ESTABLISHED",
        "CURRENT_PRODUCTION_MDD_AUDIT_UNAVAILABLE",
        "CONCURRENCY_BEHAVIOR_NOT_ESTABLISHED",
    ]
    if stage12_blocker:
        remaining_blockers.insert(0, stage12_blocker)
    generated_at_utc = generated_at_utc or datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )

    return {
        "artifact_version": "stage-13-tiered-collector-readiness-evidence-1.0",
        "record_type": "stage_13_tiered_collector_readiness_evidence",
        "generated_at_utc": generated_at_utc,
        "stage_13_status": "CONDITIONALLY BLOCKED — " + "; ".join(remaining_blockers),
        "stage_12_gate": {
            "authoritative_completion_artifact_present": stage12_approved,
            "expected_artifact_paths": expected_stage12_paths,
            "artifact_paths_found": stage12_paths_found,
            "blocker": stage12_blocker,
            "approved_panel_version": stage12_evidence.get("panel_version") if stage12_approved else None,
            "panel_station_count": len(stage12_evidence.get("panel", [])) if stage12_approved else 0,
            "provisional_analysis_population": {
                "status": "PROVISIONAL_COUNT_ONLY",
                "station_count": 50,
                "tier_counts": {"A": 10, "B": 20, "C": 20},
                "station_identities_available": stage12_approved,
                "fixed_panel_version": stage12_evidence.get("panel_version") if stage12_approved else None,
                "used_for": ["quota arithmetic", "phase-count reconciliation only"],
                "not_used_for": [
                    "station-level density",
                    "production target creation",
                    "production activation",
                    "network-wide coverage claim",
                ],
            },
        },
        "evidence_sources": [
            "docs/PROJECT-STATUS.md",
            "docs/ROADMAP.md",
            "docs/GUARDRAILS.md",
            "docs/stages/07-realtime-pilot-design.md",
            "docs/stages/07-realtime-pilot-design-completion-decision.md",
            "docs/stages/08-small-realtime-pilot-blocked.md",
            "docs/stages/09-matching-data-quality.md",
            "docs/stages/09-matching-data-quality-evidence.json",
            "docs/stages/09-source-backed-matching-summary.json",
            "docs/stages/10-operational-fact.md",
            "docs/stages/11-power-bi-model-design.md",
            "docs/collector-design/realtime-collector-requirements.md",
            "docs/source-contracts/mdd-trias-request-contract.md",
            "docs/source-contracts/mdd-trias-response-contract.md",
            "docs/source-contracts/mdd-trias-raw-retention.md",
            "docs/source-contracts/mdd-trias-event-lifecycle-validation.md",
            "docs/stages/04-cologne-scope-mode-evidence.json",
            "stage03a-mdd-discovery.jsonl",
            "hbf-lifecycle-1.log",
            "hbf-lifecycle-2.log",
            "neumarkt-lifecycle-1.log",
            "neumarkt-lifecycle-2.log",
            "neumarkt-lifecycle-3.log",
        ],
        "candidate_cadence": cadence,
        "quota": quota_projection_set(cadence["logical_requests_per_hour"]),
        "credential_consumers": _credential_consumers(),
        "number_of_results": _result_limit_evidence(root),
        "static_event_density": _static_event_density(root, stage4, stage12_approved=stage12_approved),
        "execution_duration_evidence": _load_duration_evidence(root),
        "retry_and_http_attempt_evidence": {
            "source_controlled_bounded_populations": [
                {
                    "population": "Stage 3A ten-station MDD discovery",
                    "source": "stage03a-mdd-discovery.jsonl",
                    "http_status_distribution": {"200": 10},
                    "successful_first_attempt_percentage": 100.0,
                    "retry_count": 0,
                    "additional_attempt_rate": 0.0,
                    "timeouts": 0,
                    "network_failures": 0,
                },
                {
                    "population": "Stage 8 one-hour Hbf pilot",
                    "source": "docs/stages/08-small-realtime-pilot-blocked.md",
                    "http_status_distribution": {"200": 343},
                    "successful_first_attempt_percentage": 100.0,
                    "retry_count": 0,
                    "additional_attempt_rate": 0.0,
                    "timeouts": 0,
                    "network_failures": 0,
                },
                {
                    "population": "Stage 9 source-backed acceptance",
                    "source": "docs/stages/09-source-backed-matching-summary.json",
                    "http_status_distribution": {"200": 3},
                    "successful_first_attempt_percentage": 100.0,
                    "retry_count": 0,
                    "additional_attempt_rate": 0.0,
                    "timeouts": 0,
                    "network_failures": 0,
                },
                {
                    "population": "Stage 8 latest historical authorization attempt",
                    "source": "docs/stages/08-small-realtime-pilot-blocked.md",
                    "http_status_distribution": {"401": 1},
                    "successful_first_attempt_percentage": 0.0,
                    "retry_count": 0,
                    "additional_attempt_rate": 0.0,
                    "timeouts": 0,
                    "network_failures": 0,
                },
            ],
            "legacy_historical_evidence": {
                "source": "docs/PROJECT-STATUS.md; historical Legacy quota audit snapshot 2026-09-25",
                "executions": LEGACY_EXECUTIONS,
                "http_attempts": LEGACY_HTTP_ATTEMPTS,
                "additional_retry_attempts": LEGACY_ADDITIONAL_RETRY_ATTEMPTS,
                "additional_attempt_share_of_all_http_attempts": _round(LEGACY_ADDITIONAL_ATTEMPT_SHARE, 9),
                "successful_first_attempt_percentage": None,
                "http_failure_distribution": None,
                "current_production_database_available": False,
                "interpretation": "historical retry-count evidence only; not a current Tiered50 retry rate",
            },
            "current_production_audit": {
                "ctl_mdd_collector_run_history_present": False,
                "status": "UNAVAILABLE_CURRENT_PRODUCTION_AUDIT_EVIDENCE",
                "must_be_resolved_before_activation": True,
            },
        },
        "phase_staggering": phase,
        "sequential_vs_bounded_concurrency": {
            "decision": "CONDITIONALLY_PREFER_BOUNDED_CONCURRENCY",
            "approval_state": "NOT_APPROVED_FOR_ACTIVATION",
            "option_a_fully_sequential": {
                "status": "NOT_APPROVED_AS_PRODUCTION_GUARANTEE",
                "stage_08_observed_average_wall_seconds_per_attempt": _round(3606.801 / 343, 6),
                "estimated_seconds_for_24_due_targets_at_that_average": _round((3606.801 / 343) * 24, 6),
                "five_minute_heartbeat_seconds": 300,
                "remaining_seconds_at_observed_average": _round(300 - (3606.801 / 343) * 24, 6),
                "reason": "the observed sequential pilot cycle leaves limited margin before retries, persistence variance, or a timeout; the diagnostic 3-second slot was not a production requirement",
            },
            "option_b_small_bounded_concurrency": {
                "status": "CONDITIONAL_DESIGN_DIRECTION_ONLY",
                "provisional_max_concurrency": 2,
                "justification": "two workers provide margin for 23–24 due targets while remaining bounded and isolating one slow target; no provider-safe concurrent-run evidence exists",
                "risks": [
                    "provider concurrency/rate-limit behavior is unmeasured",
                    "each retry remains an additional quota-consuming HTTP request",
                    "claim/lease semantics must prevent duplicate target/bucket execution",
                ],
            },
            "request_timeout_baseline_seconds": 30,
            "request_timeout_status": "Stage 8 evidence baseline only; production value remains to be approved",
            "exact_blocker": "CONCURRENCY_BEHAVIOR_NOT_ESTABLISHED",
            "unbounded_concurrency": "PROHIBITED",
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
            "catch_up_behavior": "false; a later process may claim the current wall-clock bucket only",
            "historical_snapshot_replay": "prohibited",
            "production_scheduler_implemented": False,
        },
        "failure_isolation": {
            "decision": "APPROVED_REQUIREMENT",
            "one_failed_station_aborts_remaining_due_targets": False,
            "per_target_fields": [
                "audit",
                "http_status",
                "attempt_count",
                "duration",
                "parser_result",
                "persistence_result",
                "error_metadata",
            ],
        },
        "historical_continuity": {
            "decision": "PRESERVE",
            "rules": [
                "do not delete Legacy7 realtime history",
                "preserve the existing 7 SamplingTargetIds",
                "add only newly approved targets",
                "do not delete/reinsert existing targets",
                "record explicit Legacy7 versus Tiered50 sampling era/version boundary",
                "do not compare raw volumes before/after without cadence qualification",
            ],
            "cutover_performed": False,
        },
        "readiness_decision": {
            "production_cadence_approved": False,
            "production_number_of_results_approved": False,
            "production_concurrency_approved": False,
            "production_activation": "NOT_APPROVED",
            "blockers": remaining_blockers,
            "stage_14_started": False,
            "tiered50_activated": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the offline Stage 13 readiness evidence artifact")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="output JSON path (default: docs/stages/13-tiered-collector-readiness-evidence.json)",
    )
    args = parser.parse_args()
    evidence = build_evidence()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
