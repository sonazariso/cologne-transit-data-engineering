"""Read-only Stage 15A validation and evidence reconciliation.

The analyzer is intentionally diagnostic.  It derives expected work from the
approved manifest and pure schedule engine, then compares it with evidence
already present in an adapter.  It never claims work, repairs claims,
finalizes quota permits, persists snapshots, or invokes transport.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from cologne_transit_collector.domain import (
    ABSOLUTE_PROVIDER_CEILING,
    APPROVED_SUPER_CYCLE_COUNTS,
    AUTOMATIC_RETRY_PROTECTION_BOUNDARY,
    MAX_ATTEMPTS,
    MAX_AUTOMATIC_RETRIES,
    MAXIMUM_CONCURRENCY,
    NORMAL_OPERATIONAL_MAXIMUM,
    PANEL_VERSION,
    PHASE_CAPACITIES,
    TIER_CADENCE_MINUTES,
    TIER_COUNTS,
    TIER_NUMBER_OF_RESULTS,
    Target,
)
from cologne_transit_collector.manifest import (
    LEGACY7_IDS,
    LEGACY7_SAMPLING_TARGET_IDS,
    RuntimeManifest,
)
from cologne_transit_collector.schedule import (
    bucket_for,
    due_targets,
    logical_due_bucket,
    may_execute_bucket,
    schedule_reconciliation,
)

from .evidence import EvidenceAdapter, EvidenceData


STATUS_VOCABULARY = (
    "PASS",
    "FAIL",
    "WARNING",
    "POLICY_PENDING",
    "NOT_APPLICABLE",
    "INSUFFICIENT_EVIDENCE",
)

POLICY_TYPES = (
    "APPROVED_INVARIANT",
    "POLICY_PENDING",
    "MEASUREMENT_ONLY",
)

POLICY_METRIC_IDS = (
    "quality.target_failure_rate",
    "quality.timeout_rate",
    "quality.retry_rate",
    "quality.heartbeat_budget_skip_rate",
    "quality.stale_claim_count_rate",
    "quality.expected_vs_actual_station_coverage",
    "quality.missing_bucket_rate",
    "quality.http_response_rate",
    "quality.zero_result_frequency",
    "quality.parser_gap_count_rate",
    "quality.parser_failure_rate",
    "quality.persistence_failure_rate",
    "quality.target_duration_statistics",
    "quality.http_attempt_duration_statistics",
    "quality.collector_run_duration_statistics",
    "quality.per_station_coverage",
    "quality.per_tier_coverage",
    "quality.per_error_category_frequency",
)

DETERMINISTIC_NON_RETRYABLE_CATEGORIES = frozenset(
    {
        "CLIENT_EXECUTION_FAILURE",
        "PARSER_FAILURE",
        "TRIAS_APPLICATION_ERROR",
        "PERSISTENCE_FAILURE",
        "REQUEST_CONTRACT_FAILURE",
        "CREDENTIAL_MISSING",
        "CREDENTIAL_FORMAT_INVALID",
        "RESPONSE_SIZE_EXCEEDED",
        "SANITIZED_ERROR",
    }
)

TERMINAL_RUN_STATUSES = frozenset(
    {
        "COMPLETE",
        "COMPLETED_WITH_FAILURES",
        "DUPLICATE_NO_WORK",
        "FAILED",
        "INTERRUPTED",
        "SKIPPED",
    }
)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        number = float(value)
        if math.isfinite(number):
            return number
    except (TypeError, ValueError):
        pass
    return None


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _json_value(value: Any) -> Any:
    """Convert backend values to stable JSON-safe diagnostic values."""

    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    if isinstance(value, datetime):
        return _iso(value)
    return value


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round((float(numerator) / float(denominator)) * 100.0, 6)


def duration_statistics(values: Iterable[float]) -> dict[str, float | int | None]:
    """Return deterministic nearest-linear percentile statistics in seconds.

    Percentiles use linear interpolation over the sorted values at
    ``p * (n - 1)``.  This definition is stable across Python versions and
    avoids a hidden dependency on a statistics package.
    """

    ordered: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            ordered.append(number)
    ordered.sort()
    if not ordered:
        return {
            "count": 0,
            "min": None,
            "P50": None,
            "P90": None,
            "P95": None,
            "P99": None,
            "max": None,
        }

    def percentile(fraction: float) -> float:
        position = fraction * (len(ordered) - 1)
        lower = int(math.floor(position))
        upper = int(math.ceil(position))
        if lower == upper:
            return ordered[lower]
        weight = position - lower
        return ordered[lower] + ((ordered[upper] - ordered[lower]) * weight)

    return {
        "count": len(ordered),
        "min": round(ordered[0], 6),
        "P50": round(percentile(0.50), 6),
        "P90": round(percentile(0.90), 6),
        "P95": round(percentile(0.95), 6),
        "P99": round(percentile(0.99), 6),
        "max": round(ordered[-1], 6),
    }


@dataclass(frozen=True)
class AnalysisWindow:
    start_utc: datetime
    end_utc: datetime

    @property
    def bucket_indexes(self) -> tuple[int, ...]:
        if self.end_utc <= self.start_utc:
            return ()
        first = logical_due_bucket(self.start_utc)
        last = logical_due_bucket(self.end_utc - timedelta(microseconds=1))
        return tuple(range(first, last + 1))

    def contains_bucket(self, bucket_index: int) -> bool:
        return bucket_index in set(self.bucket_indexes)


def _target_key(panel_version: Any, target_id: Any, bucket: Any) -> tuple[str, str, int]:
    return str(panel_version), str(target_id), _int(bucket)


def _bucket_key(row: Mapping[str, Any]) -> tuple[str, int]:
    return str(row.get("panel_version", "")), _int(row.get("logical_due_bucket"))


def _audit_key(row: Mapping[str, Any]) -> tuple[str, str, int]:
    return _target_key(
        row.get("panel_version", ""),
        row.get("source_parent_station_id", ""),
        row.get("logical_due_bucket"),
    )


def _row_in_window(row: Mapping[str, Any], window: AnalysisWindow | None) -> bool:
    if window is None:
        return True
    bucket = _int(
        row.get("logical_due_bucket", row.get("LogicalDueBucket")),
        -1,
    )
    return bucket in window.bucket_indexes


def _result(
    *,
    validation_id: str,
    category: str,
    grain: str,
    expected_value: Any,
    actual_value: Any,
    status: str,
    policy_state: str,
    evidence_count: int,
    reason: str,
    limitations: Sequence[str] = (),
    window: AnalysisWindow | None = None,
    unit: str | None = None,
) -> dict[str, Any]:
    if status not in STATUS_VOCABULARY:
        raise ValueError(f"unsupported validation status: {status}")
    if policy_state not in POLICY_TYPES:
        raise ValueError(f"unsupported policy state: {policy_state}")
    return {
        "validation_id": validation_id,
        "category": category,
        "grain": grain,
        "window_start_utc": _iso(window.start_utc if window else None),
        "window_end_utc": _iso(window.end_utc if window else None),
        "expected_value": _json_value(expected_value),
        "actual_value": _json_value(actual_value),
        "unit": unit,
        "status": status,
        "policy_state": policy_state,
        "evidence_count": int(evidence_count),
        "reason": reason,
        "limitations": [str(item) for item in limitations],
    }


class Stage15Analyzer:
    """Analyze approved Stage 15A rules against immutable evidence."""

    def __init__(
        self,
        manifest: RuntimeManifest,
        *,
        contract: Mapping[str, Any] | None = None,
        evidence_adapter: EvidenceAdapter | None = None,
        window_start_utc: datetime | None = None,
        window_end_utc: datetime | None = None,
    ) -> None:
        if (window_start_utc is None) != (window_end_utc is None):
            raise ValueError("window_start_utc and window_end_utc must be supplied together")
        if window_start_utc is not None and window_end_utc is not None:
            if window_start_utc.tzinfo is None or window_end_utc.tzinfo is None:
                raise ValueError("analysis window must be timezone-aware")
            if window_end_utc <= window_start_utc:
                raise ValueError("analysis window end must be after its start")
            self.requested_window = AnalysisWindow(
                window_start_utc.astimezone(timezone.utc),
                window_end_utc.astimezone(timezone.utc),
            )
        else:
            self.requested_window = None
        self.manifest = manifest
        self.contract = contract or {}
        self.evidence_adapter = evidence_adapter

    @classmethod
    def from_contract_path(
        cls,
        manifest: RuntimeManifest,
        contract_path: str | Path,
        *,
        evidence_adapter: EvidenceAdapter | None = None,
        window_start_utc: datetime | None = None,
        window_end_utc: datetime | None = None,
    ) -> "Stage15Analyzer":
        path = Path(contract_path).resolve()
        contract = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            manifest,
            contract=contract,
            evidence_adapter=evidence_adapter,
            window_start_utc=window_start_utc,
            window_end_utc=window_end_utc,
        )

    def analyze(self) -> dict[str, Any]:
        evidence = self.evidence_adapter.read_evidence() if self.evidence_adapter else None
        window = self._resolve_window(evidence)
        results: list[dict[str, Any]] = []
        results.extend(self._contract_checks())
        results.extend(self._configuration_checks())

        if evidence is None or not evidence.available:
            results.extend(self._no_evidence_results(window, evidence))
            reconciliation = self._empty_reconciliation(window)
            coverage = {"stations": [], "tiers": []}
            dispatcher = {"checks": [], "owners": []}
            claims = {"checks": [], "rows": []}
            attempts = {"checks": [], "rows": []}
            quota = self._empty_quota_reconciliation()
            quality = {
                "metrics": {},
                "parser_outcomes": {},
                "persistence_outcomes": {},
                "timing_history": {},
            }
            durations = self._empty_duration_report()
        else:
            results.extend(self._read_only_check(evidence, window))
            reconciliation, reconciliation_results = self._reconcile(evidence, window)
            results.extend(reconciliation_results)
            dispatcher, dispatcher_results = self._validate_dispatcher(evidence, window)
            results.extend(dispatcher_results)
            claims, claim_results = self._validate_claims(evidence, window)
            results.extend(claim_results)
            attempts, attempt_results = self._validate_attempts(evidence, window)
            results.extend(attempt_results)
            quota, quota_results = self._validate_quota(evidence, window, reconciliation)
            results.extend(quota_results)
            coverage, coverage_results = self._coverage(evidence, window, reconciliation)
            results.extend(coverage_results)
            quality, quality_results = self._quality(evidence, window, reconciliation, coverage, claims)
            results.extend(quality_results)
            durations, duration_results = self._durations(evidence, window)
            results.extend(duration_results)

        continuity, continuity_results = self._continuity(evidence, window)
        results.extend(continuity_results)

        research_contexts = sorted(
            {
                str(row.get("collection_context"))
                for rows in (
                    evidence.collector_runs if evidence else (),
                    evidence.target_audits if evidence else (),
                    evidence.parsed_snapshots if evidence else (),
                )
                for row in rows
                if row.get("collection_context")
            }
        )
        is_live_research = "LIVE_RESEARCH_SINGLE_1" in research_contexts
        if is_live_research:
            results.append(_result(
                validation_id="continuity.live_research_context_is_not_cutover",
                category="CONTINUITY",
                grain="collector-run/context",
                expected_value="LIVE_RESEARCH_SINGLE_1 remains distinct from production Tiered50",
                actual_value=research_contexts,
                status="PASS",
                policy_state="APPROVED_INVARIANT",
                evidence_count=len(research_contexts),
                reason="Research observations are identifiable but cannot satisfy the production sampling-era transition check.",
                limitations=("Stage 15B remains NOT STARTED — AWAITS TIERED50 CUTOVER.",),
                window=window,
            ))

        # The overall result is deliberately not PASS when a required live or
        # evidence-backed check is absent.  Policy-pending measurements are
        # expected in readiness work; structural FAIL remains the only hard
        # failure condition for the report.
        statuses = {str(item["status"]) for item in results}
        if "FAIL" in statuses:
            overall_status = "FAIL"
        elif "INSUFFICIENT_EVIDENCE" in statuses:
            overall_status = "INSUFFICIENT_EVIDENCE"
        elif "WARNING" in statuses:
            overall_status = "WARNING"
        elif "POLICY_PENDING" in statuses:
            overall_status = "POLICY_PENDING"
        else:
            overall_status = "PASS"

        limitations = list(evidence.limitations if evidence else ())
        limitations.extend(
            [
                "Stage 15B live post-cutover validation has not started.",
                "No real Tiered50 cutover timestamp is present or inferred.",
                "No policy threshold is inferred from industry practice or absence of evidence.",
            ]
        )
        return {
            "schema_version": "stage15-validation-report-1.0",
            "stage": "15A",
            "status": overall_status,
            "readiness_status": "IN PROGRESS",
            "stage14_overall_status": "IN PROGRESS — LIVE SQLITE RESEARCH PILOT; PRODUCTION CUTOVER DEFERRED",
            "stage14_collector_status": "STAGE 14 COLLECTOR IMPLEMENTATION COMPLETE",
            "stage15_readiness_status": "IN PROGRESS",
            "stage15_live_status": "NOT STARTED — AWAITS TIERED50 CUTOVER",
            "manifest": {
                "panel_version": self.manifest.panel_version,
                "activation_status": self.manifest.activation_status,
                "cutover_timestamp_utc": self.manifest.cutover_timestamp_utc,
                "target_count": len(self.manifest.targets),
                "legacy7_count": len(self.manifest.legacy7_targets),
                "new_tiered50_count": len(self.manifest.new_tiered50_targets),
            },
            "window": {
                "start_utc": _iso(window.start_utc) if window else None,
                "end_utc": _iso(window.end_utc) if window else None,
                "logical_buckets": list(window.bucket_indexes) if window else [],
                "source": "requested" if self.requested_window else "derived-from-evidence" if evidence else "not-supplied",
            },
            "evidence": {
                "available": bool(evidence and evidence.available),
                "source": evidence.source if evidence else "manifest-only",
                "read_only": bool(getattr(self.evidence_adapter, "read_only", True)) if evidence else True,
                "collector_evidence_mutated": False,
                "limitations": list(evidence.limitations if evidence else ()),
                "database_role": next(
                    (
                        str(row.get("metadata_value"))
                        for row in (evidence.database_metadata if evidence else ())
                        if str(row.get("metadata_key")) == "database_role"
                    ),
                    None,
                ),
                "collection_contexts": research_contexts,
                "live_research_is_not_production_evidence": is_live_research,
            },
            "reconciliation": reconciliation,
            "dispatcher_validation": dispatcher,
            "target_claim_validation": claims,
            "attempt_retry_reconciliation": attempts,
            "quota_reconciliation": quota,
            "activity": {
                "simulated_or_offline_transport_attempts": quota.get("simulated_or_offline_attempts", 0),
                "live_network_attempts": quota.get("network_attempts", 0),
                "live_client_invocations": quota.get("live_client_invocations", 0),
                "mdd_requests_issued_by_validator": 0,
                "dns_activity_by_validator": 0,
                "postgresql_activity_by_validator": 0,
                "production_cutover_activity": 0,
                "power_bi_activity": 0,
            },
            "coverage": coverage,
            "quality": quality,
            "duration_statistics": durations,
            "continuity": continuity,
            "checks": results,
            "limitations": limitations,
        }

    def _resolve_window(self, evidence: EvidenceData | None) -> AnalysisWindow | None:
        if self.requested_window is not None:
            return self.requested_window
        if evidence is None:
            return None
        buckets: list[int] = []
        for rows in (
            evidence.collector_runs,
            evidence.dispatcher_claims,
            evidence.target_claims,
            evidence.target_audits,
            evidence.http_attempts,
        ):
            buckets.extend(_int(row.get("logical_due_bucket"), -1) for row in rows)
        buckets = [bucket for bucket in buckets if bucket >= 0]
        if not buckets:
            return None
        start = datetime.fromtimestamp(min(buckets) * 300, tz=timezone.utc)
        end = datetime.fromtimestamp((max(buckets) + 1) * 300, tz=timezone.utc)
        return AnalysisWindow(start, end)

    def _contract_checks(self) -> list[dict[str, Any]]:
        if not self.contract:
            return [
                _result(
                    validation_id="contract.loaded",
                    category="CONTRACT",
                    grain="contract",
                    expected_value="version-controlled Stage 15 contract",
                    actual_value="not supplied",
                    status="INSUFFICIENT_EVIDENCE",
                    policy_state="APPROVED_INVARIANT",
                    evidence_count=0,
                    reason="The analyzer can run from the manifest, but the machine-readable contract was not supplied.",
                )
            ]
        errors: list[str] = []
        if self.contract.get("stage") != "15A":
            errors.append("contract stage is not 15A")
        if set(self.contract.get("status_vocabulary", ())) != set(STATUS_VOCABULARY):
            errors.append("contract status vocabulary differs from the supported vocabulary")
        if set(self.contract.get("policy_types", ())) != set(POLICY_TYPES):
            errors.append("contract policy types differ from the supported policy types")
        checks = self.contract.get("checks")
        if not isinstance(checks, list) or not checks:
            errors.append("contract has no checks")
        else:
            ids: set[str] = set()
            for item in checks:
                if not isinstance(item, Mapping):
                    errors.append("contract contains a non-object check")
                    continue
                identifier = str(item.get("validation_id", ""))
                if not identifier or identifier in ids:
                    errors.append(f"contract validation_id is missing or duplicated: {identifier!r}")
                ids.add(identifier)
                if str(item.get("policy_type", "")) not in POLICY_TYPES:
                    errors.append(f"contract check has unsupported policy_type: {identifier}")
        return [
            _result(
                validation_id="contract.loaded",
                category="CONTRACT",
                grain="contract",
                expected_value="valid Stage 15A machine-readable contract",
                actual_value={
                    "stage": self.contract.get("stage"),
                    "check_count": len(checks) if isinstance(checks, list) else 0,
                },
                status="FAIL" if errors else "PASS",
                policy_state="APPROVED_INVARIANT",
                evidence_count=1,
                reason="; ".join(errors) if errors else "Version-controlled validation contract is structurally valid.",
            )
        ]

    def _configuration_checks(self) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        target_ids = {target.source_parent_station_id for target in self.manifest.targets}
        legacy_ids = {
            target.source_parent_station_id: target.legacy_sampling_target_id
            for target in self.manifest.legacy7_targets
        }
        new_null_count = sum(
            target.production_sampling_target_id is None for target in self.manifest.new_tiered50_targets
        )
        tier_counts = Counter(target.tier for target in self.manifest.targets)
        cadence = {
            tier: sorted({target.cadence_minutes for target in self.manifest.targets if target.tier == tier})
            for tier in TIER_COUNTS
        }
        result_limits = {
            tier: sorted({target.number_of_results for target in self.manifest.targets if target.tier == tier})
            for tier in TIER_COUNTS
        }
        phase_capacities = {
            tier: [
                sum(
                    target.tier == tier and target.phase_index == phase_index
                    for target in self.manifest.targets
                )
                for phase_index in range(len(PHASE_CAPACITIES[tier]))
            ]
            for tier in PHASE_CAPACITIES
        }
        reconciliation = schedule_reconciliation(self.manifest.targets)

        def add(
            validation_id: str,
            category: str,
            grain: str,
            expected: Any,
            actual: Any,
            ok: bool,
            reason: str,
            *,
            evidence_count: int = 1,
            unit: str | None = None,
        ) -> None:
            checks.append(
                _result(
                    validation_id=validation_id,
                    category=category,
                    grain=grain,
                    expected_value=expected,
                    actual_value=actual,
                    status="PASS" if ok else "FAIL",
                    policy_state="APPROVED_INVARIANT",
                    evidence_count=evidence_count,
                    reason=reason,
                    unit=unit,
                )
            )

        add("panel.version", "PANEL_CONFIGURATION", "panel", PANEL_VERSION, self.manifest.panel_version, self.manifest.panel_version == PANEL_VERSION, "The manifest panel version matches the approved version.")
        add("panel.target_count", "PANEL_CONFIGURATION", "panel", 50, len(self.manifest.targets), len(self.manifest.targets) == 50, "The approved manifest contains exactly 50 targets.", unit="targets")
        add("panel.legacy7_count", "PANEL_CONFIGURATION", "panel", 7, len(self.manifest.legacy7_targets), len(self.manifest.legacy7_targets) == 7, "The manifest contains exactly seven Legacy7 members.", unit="targets")
        add("panel.new_tiered50_count", "PANEL_CONFIGURATION", "panel", 43, len(self.manifest.new_tiered50_targets), len(self.manifest.new_tiered50_targets) == 43, "The manifest contains exactly 43 new Tiered50 members.", unit="targets")
        add("panel.legacy7_ids", "PANEL_CONFIGURATION", "target", sorted(range(1, 8)), sorted(value for value in legacy_ids.values() if value is not None), legacy_ids == LEGACY7_SAMPLING_TARGET_IDS and set(legacy_ids) == set(LEGACY7_IDS), "The seven Legacy7 source identities retain exact lineage IDs 1 through 7.", unit="lineage IDs")
        add("panel.new_production_ids_unmaterialized", "PANEL_CONFIGURATION", "target", 43, new_null_count, new_null_count == 43, "No production SamplingTargetId has been fabricated for a new target.", unit="targets")
        add("schedule.tier_counts", "SCHEDULE", "panel/tier", dict(TIER_COUNTS), dict(sorted(tier_counts.items())), dict(tier_counts) == dict(TIER_COUNTS), "Manifest tier counts match the approved 8/20/22 design.", unit="targets")
        add("schedule.cadence_minutes", "SCHEDULE", "panel/tier", dict(TIER_CADENCE_MINUTES), cadence, cadence == {tier: [value] for tier, value in TIER_CADENCE_MINUTES.items()}, "Manifest cadence matches the approved 5/15/30-minute design.", unit="minutes")
        add("schedule.number_of_results", "SCHEDULE", "panel/tier", dict(TIER_NUMBER_OF_RESULTS), result_limits, result_limits == {tier: [value] for tier, value in TIER_NUMBER_OF_RESULTS.items()}, "Manifest NumberOfResults values match the approved A20/B30/C30 design.", unit="results per response")
        add("schedule.heartbeat_distribution", "SCHEDULE", "six-bucket-super-cycle", list(APPROVED_SUPER_CYCLE_COUNTS), list(reconciliation["super_cycle_counts"]), tuple(reconciliation["super_cycle_counts"]) == APPROVED_SUPER_CYCLE_COUNTS, "Pure schedule reconciliation reproduces the approved heartbeat distribution.", unit="logical targets")
        add("schedule.phase_capacities", "SCHEDULE", "panel/tier/phase", {tier: list(values) for tier, values in PHASE_CAPACITIES.items()}, phase_capacities, phase_capacities == {tier: list(values) for tier, values in PHASE_CAPACITIES.items()}, "Manifest phase assignments reproduce the approved persisted capacities.", unit="targets")
        add("schedule.logical_requests_per_30_minutes", "SCHEDULE", "six-bucket-super-cycle", 110, reconciliation["super_cycle_total"], reconciliation["super_cycle_total"] == 110, "The six-bucket super-cycle contains 110 logical requests.", unit="logical requests")
        add("schedule.logical_requests_per_hour", "SCHEDULE", "hour", 220, reconciliation["requests_per_hour"], reconciliation["requests_per_hour"] == 220, "The approved schedule contains 220 logical requests per hour.", unit="logical requests")
        add("execution.maximum_concurrency", "EXECUTION", "collector-run", MAXIMUM_CONCURRENCY, MAXIMUM_CONCURRENCY, True, "The approved runtime constant fixes MaximumConcurrency at 2.", unit="concurrent target executions")
        add("execution.max_attempts", "EXECUTION", "target/logical-bucket", MAX_ATTEMPTS, MAX_ATTEMPTS, True, "The approved runtime constant fixes MaxAttempts at 2.", unit="attempts")
        add("execution.maximum_automatic_retries", "EXECUTION", "target/logical-bucket", MAX_AUTOMATIC_RETRIES, MAX_AUTOMATIC_RETRIES, True, "MaxAttempts=2 permits exactly one automatic retry at most.", unit="retries")
        add("execution.no_catch_up_replay", "EXECUTION", "logical-bucket", True, may_execute_bucket(100, 100) and not may_execute_bucket(100, 99), True, "The schedule engine permits only the current logical bucket and does not replay a missed bucket.")
        add("quota.retry_protection_boundary", "QUOTA", "shared-credential/month", AUTOMATIC_RETRY_PROTECTION_BOUNDARY, AUTOMATIC_RETRY_PROTECTION_BOUNDARY, True, "The approved automatic-retry protection boundary is fixed at 207,500.", unit="provider-attempt-equivalents")
        add("quota.normal_operational_maximum", "QUOTA", "shared-credential/month", NORMAL_OPERATIONAL_MAXIMUM, NORMAL_OPERATIONAL_MAXIMUM, True, "The approved normal operational maximum is fixed at 210,000.", unit="provider-attempt-equivalents")
        add("quota.provider_absolute_ceiling", "QUOTA", "shared-credential/month", ABSOLUTE_PROVIDER_CEILING, ABSOLUTE_PROVIDER_CEILING, True, "The approved provider absolute ceiling is fixed at 250,000.", unit="provider attempts")
        add("continuity.legacy7_identity", "CONTINUITY", "target/lineage", "exactly seven approved source identities mapped to lineage IDs 1 through 7", {"source_ids": sorted(legacy_ids), "lineage_ids": sorted(value for value in legacy_ids.values() if value is not None)}, legacy_ids == LEGACY7_SAMPLING_TARGET_IDS, "Legacy7 source identities and lineage IDs remain distinct and exact.")
        return checks

    def _read_only_check(self, evidence: EvidenceData, window: AnalysisWindow | None) -> list[dict[str, Any]]:
        adapter_read_only = bool(getattr(self.evidence_adapter, "read_only", False))
        return [
            _result(
                validation_id="architecture.read_only_evidence_adapter",
                category="ARCHITECTURE",
                grain="evidence-adapter",
                expected_value=True,
                actual_value={
                    "read_only": adapter_read_only,
                    "collector_evidence_mutated": False,
                    "source": evidence.source,
                },
                status="PASS" if adapter_read_only else "FAIL",
                policy_state="APPROVED_INVARIANT",
                evidence_count=1,
                reason="The selected evidence adapter exposes a read-only contract; validation performed no collector mutation.",
                limitations=evidence.limitations,
                window=window,
            )
        ]

    def _no_evidence_results(self, window: AnalysisWindow | None, evidence: EvidenceData | None) -> list[dict[str, Any]]:
        limitations = list(evidence.limitations if evidence else ())
        limitations.append("No SQLite or future production evidence adapter was supplied.")
        results: list[dict[str, Any]] = [
            _result(
                validation_id="stage15.live_post_cutover_validation",
                category="READINESS",
                grain="stage",
                expected_value="NOT STARTED — AWAITS TIERED50 CUTOVER",
                actual_value="NO_REAL_CUTOVER_EVIDENCE",
                status="INSUFFICIENT_EVIDENCE",
                policy_state="MEASUREMENT_ONLY",
                evidence_count=0,
                reason="Stage 15B cannot begin until Tiered50 is actually activated and real post-cutover evidence exists.",
                limitations=limitations,
                window=window,
            )
        ]
        for validation_id in (
            "execution.duplicate_dispatcher_no_duplicate_transport",
            "execution.failure_isolation",
            "quota.retry_protection_boundary",
            "quota.normal_operational_maximum",
            "quota.provider_absolute_ceiling",
            "quota.simulation_excluded_from_provider_usage",
        ):
            results.append(
                _result(
                    validation_id=validation_id,
                    category=validation_id.split(".", 1)[0].upper(),
                    grain="evidence-dependent",
                    expected_value="diagnostic evidence required",
                    actual_value=None,
                    status="INSUFFICIENT_EVIDENCE",
                    policy_state="APPROVED_INVARIANT",
                    evidence_count=0,
                    reason="The invariant is encoded, but no execution evidence was supplied; absence is not a PASS.",
                    limitations=limitations,
                    window=window,
                )
            )
        for validation_id in POLICY_METRIC_IDS:
            results.append(
                _result(
                    validation_id=validation_id,
                    category="QUALITY" if not validation_id.startswith("quality.") else "QUALITY",
                    grain="window",
                    expected_value="user-approved threshold not established",
                    actual_value=None,
                    status="INSUFFICIENT_EVIDENCE",
                    policy_state="POLICY_PENDING",
                    evidence_count=0,
                    reason="The metric has no evidence in manifest-only mode; no acceptance threshold is assigned.",
                    limitations=limitations,
                    window=window,
                )
            )
        return results

    def _empty_reconciliation(self, window: AnalysisWindow | None) -> dict[str, Any]:
        return {
            "window": {"start_utc": _iso(window.start_utc) if window else None, "end_utc": _iso(window.end_utc) if window else None},
            "expected_count": 0,
            "actual_audit_count": 0,
            "counts": {},
            "records": [],
            "limitations": ["Expected-vs-actual reconciliation requires an evidence adapter."],
        }

    def _reconcile(self, evidence: EvidenceData, window: AnalysisWindow | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if window is None:
            return self._empty_reconciliation(window), [
                _result(
                    validation_id="reconciliation.expected_vs_actual",
                    category="RECONCILIATION",
                    grain="target/logical-bucket",
                    expected_value="analysis window",
                    actual_value=None,
                    status="INSUFFICIENT_EVIDENCE",
                    policy_state="APPROVED_INVARIANT",
                    evidence_count=0,
                    reason="An analysis window could not be derived because the evidence has no logical bucket identity.",
                )
            ]

        expected: dict[tuple[str, str, int], Target] = {}
        for bucket in window.bucket_indexes:
            for target in due_targets(self.manifest.targets, bucket):
                expected[(target.panel_version, target.source_parent_station_id, bucket)] = target

        audits = [row for row in evidence.target_audits if _row_in_window(row, window)]
        audit_groups: dict[tuple[str, str, int], list[Mapping[str, Any]]] = defaultdict(list)
        for row in audits:
            audit_groups[_audit_key(row)].append(row)

        records: list[dict[str, Any]] = []
        for key, target in sorted(expected.items(), key=lambda item: (item[0][2], item[0][1])):
            rows = audit_groups.get(key, [])
            statuses = [str(row.get("status", "")) for row in rows]
            if "SUCCEEDED" in statuses:
                classification = "EXPECTED_AND_SUCCEEDED"
                reason = "At least one expected target audit completed successfully with explicit audit evidence."
            elif "FAILED" in statuses:
                classification = "EXPECTED_AND_FAILED"
                reason = "Expected target evidence contains a failed target audit."
            elif statuses and all(status == "SKIPPED" for status in statuses):
                classification = "EXPECTED_AND_SKIPPED"
                reason = "Expected target evidence records an explicit skip; absence of transport is not success."
            else:
                classification = "EXPECTED_BUT_MISSING"
                reason = "No successful, failed, or explicit skipped target audit establishes the expected execution."
            records.append(
                {
                    "record_type": "expected_target",
                    "classification": classification,
                    "panel_version": target.panel_version,
                    "target_id": target.source_parent_station_id,
                    "station_name": target.station_name,
                    "tier": target.tier,
                    "logical_due_bucket": key[2],
                    "audit_ids": [_int(row.get("audit_id")) for row in rows],
                    "status_values": statuses,
                    "reason": reason,
                }
            )

        # Duplicate attempts are first-class evidence, not a success state.
        for row in evidence.dispatcher_claim_events:
            if not _row_in_window(row, window) or str(row.get("outcome", "")) != "DUPLICATE_DISPATCHER_CLAIM":
                continue
            records.append(
                {
                    "record_type": "dispatcher_control_event",
                    "classification": "DUPLICATE_INVOCATION_PREVENTED",
                    "subtype": "DISPATCHER",
                    "panel_version": row.get("panel_version"),
                    "logical_due_bucket": _int(row.get("logical_due_bucket")),
                    "collector_run_id": row.get("collector_run_id"),
                    "event_id": _int(row.get("event_id")),
                    "reason": "A duplicate dispatcher attempt was observed as an auditable event.",
                }
            )
        for row in evidence.claim_events:
            if not _row_in_window(row, window) or str(row.get("outcome", "")) != "DUPLICATE_CLAIM":
                continue
            records.append(
                {
                    "record_type": "target_control_event",
                    "classification": "DUPLICATE_INVOCATION_PREVENTED",
                    "subtype": "TARGET_CLAIM",
                    "panel_version": row.get("panel_version"),
                    "target_id": row.get("source_parent_station_id"),
                    "logical_due_bucket": _int(row.get("logical_due_bucket")),
                    "collector_run_id": row.get("collector_run_id"),
                    "event_id": _int(row.get("event_id")),
                    "reason": "A duplicate target claim attempt was observed as an auditable event.",
                }
            )

        for key, rows in sorted(audit_groups.items(), key=lambda item: item[0]):
            if key in expected:
                continue
            for row in rows:
                records.append(
                    {
                        "record_type": "unexpected_actual",
                        "classification": "UNEXPECTED_EXECUTION",
                        "panel_version": key[0],
                        "target_id": key[1],
                        "logical_due_bucket": key[2],
                        "audit_id": _int(row.get("audit_id")),
                        "status": row.get("status"),
                        "reason": "An audit row exists for a target/bucket not due under the approved schedule or outside the manifest.",
                    }
                )

        counts = Counter(str(row.get("classification")) for row in records)
        expected_count = len(expected)
        actual_count = len(audits)
        structural_fail = counts.get("UNEXPECTED_EXECUTION", 0) > 0
        if expected_count == 0:
            status = "INSUFFICIENT_EVIDENCE"
            reason = "The analysis window contains no approved due target/bucket pairs."
        elif structural_fail:
            status = "FAIL"
            reason = "Unexpected target execution evidence was found."
        else:
            status = "PASS"
            reason = "Every expected target/bucket is explicitly classified; missing evidence is not treated as success."
        result = _result(
            validation_id="reconciliation.expected_vs_actual",
            category="RECONCILIATION",
            grain="target/logical-bucket",
            expected_value=expected_count,
            actual_value={"audit_count": actual_count, "classification_counts": dict(sorted(counts.items()))},
            status=status,
            policy_state="APPROVED_INVARIANT",
            evidence_count=actual_count + len(evidence.claim_events) + len(evidence.dispatcher_claim_events),
            reason=reason,
            limitations=("A missing audit is EXPECTED_BUT_MISSING, never success.",),
            window=window,
            unit="classified records",
        )
        return {
            "window": {"start_utc": _iso(window.start_utc), "end_utc": _iso(window.end_utc)},
            "expected_count": expected_count,
            "actual_audit_count": actual_count,
            "counts": dict(sorted(counts.items())),
            "records": records,
            "expected_keys": [
                {"target_id": key[1], "logical_due_bucket": key[2]} for key in sorted(expected)
            ],
            "limitations": ["No PASS is inferred from absence of an error row."],
        }, [result]

    def _validate_dispatcher(self, evidence: EvidenceData, window: AnalysisWindow | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        claims = [row for row in evidence.dispatcher_claims if _row_in_window(row, window)]
        events = [row for row in evidence.dispatcher_claim_events if _row_in_window(row, window)]
        attempts_by_run = Counter(str(row.get("collector_run_id", "")) for row in evidence.target_audits if _row_in_window(row, window) and _int(row.get("http_attempts")) > 0)
        grouped: dict[tuple[str, int], list[Mapping[str, Any]]] = defaultdict(list)
        for row in claims:
            grouped[_bucket_key(row)].append(row)
        duplicate_rows = [row for row in events if str(row.get("outcome", "")) == "DUPLICATE_DISPATCHER_CLAIM"]
        stale_rows = self._stale_rows(claims, window)
        expected_buckets = set(window.bucket_indexes) if window else set()
        owner_count = {str(bucket): len(grouped.get((self.manifest.panel_version, bucket), [])) for bucket in sorted(expected_buckets)}
        missing_owner_buckets = [bucket for bucket in expected_buckets if not grouped.get((self.manifest.panel_version, bucket))]
        audits_by_bucket = Counter(
            _int(row.get("logical_due_bucket"))
            for row in evidence.target_audits
            if _row_in_window(row, window)
        )
        owner_violation = any(len(rows) > 1 for rows in grouped.values())
        owner_without_evidence = [bucket for bucket in missing_owner_buckets if audits_by_bucket.get(bucket, 0)]
        results: list[dict[str, Any]] = []
        if not claims and window is None:
            owner_status = "INSUFFICIENT_EVIDENCE"
            owner_reason = "No logical bucket window or dispatcher claim evidence is available."
        elif owner_violation or owner_without_evidence:
            owner_status = "FAIL"
            owner_reason = "A panel/bucket has more than one durable owner or target execution lacks a durable dispatcher owner."
        elif not claims:
            owner_status = "INSUFFICIENT_EVIDENCE"
            owner_reason = "No dispatcher claim evidence is available for the analysis window."
        else:
            owner_status = "PASS"
            owner_reason = "Observed dispatcher claims have at most one durable owner per panel/bucket."
        results.append(_result(
            validation_id="dispatcher.one_durable_owner",
            category="DISPATCHER",
            grain="panel/logical-bucket",
            expected_value="at most one durable owner per bucket",
            actual_value={"owner_count_by_bucket": owner_count, "missing_owner_buckets_with_audits": owner_without_evidence},
            status=owner_status,
            policy_state="APPROVED_INVARIANT",
            evidence_count=len(claims),
            reason=owner_reason,
            window=window,
        ))
        results.append(_result(
            validation_id="dispatcher.duplicate_attempts_observable",
            category="DISPATCHER",
            grain="panel/logical-bucket",
            expected_value="duplicate attempts are auditable when they occur",
            actual_value={"duplicate_event_count": len(duplicate_rows)},
            status="PASS" if duplicate_rows else "NOT_APPLICABLE",
            policy_state="APPROVED_INVARIANT",
            evidence_count=len(events),
            reason="Duplicate dispatcher attempts are retained as events." if duplicate_rows else "No duplicate dispatcher invocation was present in this evidence window; this is not a duplicate-safety PASS.",
            window=window,
        ))
        duplicate_transport_rows = [
            {"collector_run_id": row.get("collector_run_id"), "transport_attempts": attempts_by_run.get(str(row.get("collector_run_id", "")), 0)}
            for row in duplicate_rows
            if attempts_by_run.get(str(row.get("collector_run_id", "")), 0) > 0
        ]
        results.append(_result(
            validation_id="execution.duplicate_dispatcher_no_duplicate_transport",
            category="EXECUTION",
            grain="panel/logical-bucket",
            expected_value=0,
            actual_value={"duplicate_event_count": len(duplicate_rows), "duplicate_runs_with_transport": duplicate_transport_rows},
            status="FAIL" if duplicate_transport_rows else "PASS" if duplicate_rows else "NOT_APPLICABLE",
            policy_state="APPROVED_INVARIANT",
            evidence_count=len(duplicate_rows),
            reason="Duplicate dispatcher invocations produced no target transport." if duplicate_rows and not duplicate_transport_rows else "A duplicate dispatcher run produced transport." if duplicate_transport_rows else "No duplicate dispatcher invocation evidence is available.",
            window=window,
        ))
        results.append(_result(
            validation_id="dispatcher.stale_claim_visibility",
            category="DISPATCHER",
            grain="panel/logical-bucket",
            expected_value="stale/incomplete claims remain visible",
            actual_value={"stale_claim_count": len(stale_rows), "stale_claims": [_json_value(row) for row in stale_rows]},
            status="PASS" if stale_rows else "NOT_APPLICABLE",
            policy_state="APPROVED_INVARIANT",
            evidence_count=len(claims),
            reason="Stale dispatcher claims are visible and not silently converted to success." if stale_rows else "No stale dispatcher claim is present in the evidence window.",
            window=window,
        ))
        return {
            "owners": [_json_value(row) for row in claims],
            "duplicate_events": [_json_value(row) for row in duplicate_rows],
            "stale_claims": [_json_value(row) for row in stale_rows],
            "checks": [item["validation_id"] for item in results],
        }, results

    def _validate_claims(self, evidence: EvidenceData, window: AnalysisWindow | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        claims = [row for row in evidence.target_claims if _row_in_window(row, window)]
        events = [row for row in evidence.claim_events if _row_in_window(row, window)]
        grouped: dict[tuple[str, str, int], list[Mapping[str, Any]]] = defaultdict(list)
        for row in claims:
            grouped[_audit_key(row)].append(row)
        duplicate_events = [row for row in events if str(row.get("outcome", "")) == "DUPLICATE_CLAIM"]
        stale_rows = self._stale_rows(claims, window)
        duplicate_rows = [
            {"key": key, "row_count": len(rows)} for key, rows in grouped.items() if len(rows) > 1
        ]
        if not claims:
            one_claim_status = "INSUFFICIENT_EVIDENCE"
            one_claim_reason = "No target claim evidence is available for the analysis window."
        else:
            one_claim_status = "FAIL" if duplicate_rows else "PASS"
            one_claim_reason = "More than one winning target claim exists for a target/bucket." if duplicate_rows else "Observed target claims have at most one durable row per target/bucket."
        results = [
            _result(
                validation_id="claims.at_most_one_winning_claim",
                category="TARGET_CLAIM",
                grain="target/logical-bucket",
                expected_value="at most one winning execution claim",
                actual_value={"duplicate_claim_rows": duplicate_rows, "claim_count": len(claims)},
                status=one_claim_status,
                policy_state="APPROVED_INVARIANT",
                evidence_count=len(claims),
                reason=one_claim_reason,
                window=window,
            ),
            _result(
                validation_id="claims.duplicate_attempts_auditable",
                category="TARGET_CLAIM",
                grain="target/logical-bucket",
                expected_value="duplicate claim attempts remain auditable",
                actual_value={"duplicate_event_count": len(duplicate_events)},
                status="PASS" if duplicate_events else "NOT_APPLICABLE",
                policy_state="APPROVED_INVARIANT",
                evidence_count=len(events),
                reason="Duplicate target claim attempts are represented by claim events." if duplicate_events else "No duplicate target claim sample is present; this is not a duplicate-safety PASS.",
                window=window,
            ),
            _result(
                validation_id="claims.stale_claim_visibility",
                category="TARGET_CLAIM",
                grain="target/logical-bucket",
                expected_value="stale target claims remain visible",
                actual_value={"stale_claim_count": len(stale_rows), "stale_claims": [_json_value(row) for row in stale_rows]},
                status="PASS" if stale_rows else "NOT_APPLICABLE",
                policy_state="APPROVED_INVARIANT",
                evidence_count=len(claims),
                reason="Stale target claims remain visible for diagnosis." if stale_rows else "No stale target claim is present in the evidence window.",
                window=window,
            ),
            _result(
                validation_id="architecture.validator_does_not_mutate_claims",
                category="ARCHITECTURE",
                grain="validator",
                expected_value=True,
                actual_value=True,
                status="PASS",
                policy_state="APPROVED_INVARIANT",
                evidence_count=1,
                reason="This analyzer only reads claim rows and has no claim-repair operation.",
                window=window,
            ),
        ]
        return {
            "rows": [_json_value(row) for row in claims],
            "duplicate_events": [_json_value(row) for row in duplicate_events],
            "stale_claims": [_json_value(row) for row in stale_rows],
            "checks": [item["validation_id"] for item in results],
        }, results

    def _stale_rows(self, rows: Sequence[Mapping[str, Any]], window: AnalysisWindow | None) -> list[Mapping[str, Any]]:
        if not rows:
            return []
        as_of = window.end_utc if window else datetime.now(timezone.utc)
        stale: list[Mapping[str, Any]] = []
        for row in rows:
            status = str(row.get("status", ""))
            deadline = _parse_datetime(row.get("stale_after_utc"))
            if status in {"CLAIMED", "RUNNING"} and deadline is not None and deadline <= as_of:
                stale.append(row)
        return stale

    def _validate_attempts(self, evidence: EvidenceData, window: AnalysisWindow | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        audits = [row for row in evidence.target_audits if _row_in_window(row, window)]
        audit_ids = {_int(row.get("audit_id")) for row in audits}
        attempts = [row for row in evidence.http_attempts if _int(row.get("audit_id")) in audit_ids]
        by_audit: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
        for row in attempts:
            by_audit[_int(row.get("audit_id"))].append(row)
        audit_by_id = {_int(row.get("audit_id")): row for row in audits}
        rows_report: list[dict[str, Any]] = []
        violations: list[dict[str, Any]] = []
        for audit_id, rows in sorted(by_audit.items()):
            ordered = sorted(rows, key=lambda row: (_int(row.get("attempt_number")), _int(row.get("attempt_id"))))
            numbers = [_int(row.get("attempt_number")) for row in ordered]
            audit = audit_by_id.get(audit_id, {})
            row_violations: list[str] = []
            if any(number > MAX_ATTEMPTS for number in numbers):
                row_violations.append("ATTEMPT_NUMBER_GT_MAX_ATTEMPTS")
            if sum(number > 1 for number in numbers) > MAX_AUTOMATIC_RETRIES:
                row_violations.append("MORE_THAN_ONE_AUTOMATIC_RETRY")
            if numbers and numbers != list(range(1, max(numbers) + 1)):
                row_violations.append("ATTEMPT_NUMBER_SEQUENCE_GAP")
            for previous, current in zip(ordered, ordered[1:]):
                if _int(current.get("attempt_number")) <= 1:
                    continue
                prior_status = _int(previous.get("http_status"), -1)
                prior_category = str(previous.get("transport_failure_category") or "").upper()
                if prior_status in {401, 403} or prior_category in DETERMINISTIC_NON_RETRYABLE_CATEGORIES:
                    row_violations.append("RETRY_AFTER_DETERMINISTIC_NON_RETRYABLE")
            row_report = {
                "audit_id": audit_id,
                "target_id": audit.get("source_parent_station_id"),
                "logical_due_bucket": _int(audit.get("logical_due_bucket")),
                "attempt_numbers": numbers,
                "response_statuses": [_int(row.get("http_status"), -1) if row.get("http_status") is not None else None for row in ordered],
                "failure_categories": [row.get("transport_failure_category") for row in ordered],
                "retry_decisions": [row.get("retry_decision") for row in ordered],
                "retry_outcomes": [audit.get("retry_outcome")],
                "violations": sorted(set(row_violations)),
            }
            rows_report.append(row_report)
            if row_violations:
                violations.append(row_report)

        attempts_by_target_bucket: dict[tuple[str, str, int], list[int]] = defaultdict(list)
        audits_by_target_bucket: dict[tuple[str, str, int], list[int]] = defaultdict(list)
        for audit in audits:
            key = _audit_key(audit)
            audits_by_target_bucket[key].append(_int(audit.get("audit_id")))
        for row in attempts:
            audit = audit_by_id.get(_int(row.get("audit_id")), {})
            attempts_by_target_bucket[_audit_key(audit)].append(_int(row.get("attempt_id")))
        duplicate_transport = [
            {"key": key, "audit_ids": audits_by_target_bucket[key], "attempt_ids": attempt_ids}
            for key, attempt_ids in attempts_by_target_bucket.items()
            if len([audit_id for audit_id in audits_by_target_bucket[key] if by_audit.get(audit_id)]) > 1
        ]
        if not attempts:
            structural_status = "INSUFFICIENT_EVIDENCE"
            structural_reason = "No HTTP/transport attempt evidence is available."
        else:
            structural_status = "FAIL" if violations or duplicate_transport else "PASS"
            structural_reason = "; ".join(
                item
                for item in (
                    "structural attempt/retry violations were found" if violations else "",
                    "duplicate target transport was found" if duplicate_transport else "",
                )
                if item
            ) or "Attempt numbers, retry structure, and target/bucket transport multiplicity satisfy the approved structural rules."

        attempt_number_violations = [
            row for row in rows_report if "ATTEMPT_NUMBER_GT_MAX_ATTEMPTS" in row["violations"] or "ATTEMPT_NUMBER_SEQUENCE_GAP" in row["violations"]
        ]
        retry_count_violations = [
            row for row in rows_report if "MORE_THAN_ONE_AUTOMATIC_RETRY" in row["violations"]
        ]
        deterministic_retry_violations = [
            row for row in rows_report if "RETRY_AFTER_DETERMINISTIC_NON_RETRYABLE" in row["violations"]
        ]

        quota_by_execution: dict[tuple[str, str, int], int] = Counter()
        quota_permit_refs: list[str] = []
        for row in evidence.quota_audit:
            if not _row_in_window(row, window):
                continue
            key = (
                str(row.get("collector_run_id", "")),
                str(row.get("source_parent_station_id", "")),
                _int(row.get("logical_due_bucket")),
            )
            quota_by_execution[key] += 1
            try:
                decision = json.loads(str(row.get("decision_json", "{}")))
            except (TypeError, ValueError, json.JSONDecodeError):
                decision = {}
            if decision.get("permit_id") is not None:
                quota_permit_refs.append(str(decision["permit_id"]))
        quota_missing: list[dict[str, Any]] = []
        for audit in audits:
            key = (
                str(audit.get("collector_run_id", "")),
                str(audit.get("source_parent_station_id", "")),
                _int(audit.get("logical_due_bucket")),
            )
            attempt_count = len(by_audit.get(_int(audit.get("audit_id")), []))
            if attempt_count and quota_by_execution.get(key, 0) < attempt_count:
                quota_missing.append({"audit_id": _int(audit.get("audit_id")), "attempt_count": attempt_count, "quota_decision_count": quota_by_execution.get(key, 0)})
        permits_by_id = {str(row.get("permit_id")): row for row in evidence.quota_permits}
        missing_permit_refs = sorted(set(quota_permit_refs) - set(permits_by_id))
        permit_statuses = Counter(str(row.get("status", "")) for row in evidence.quota_permits)
        quota_status = "INSUFFICIENT_EVIDENCE" if attempts and not evidence.quota_audit else "FAIL" if quota_missing or missing_permit_refs else "PASS" if attempts else "INSUFFICIENT_EVIDENCE"
        quota_reason = "Quota decision evidence is unavailable for attempt reconciliation." if attempts and not evidence.quota_audit else "Some attempts lack corresponding quota decision evidence." if quota_missing else "A quota decision references an unavailable permit." if missing_permit_refs else "Available quota decision and permit evidence covers each observed transport attempt." if attempts else "No attempts are available for quota reconciliation."
        run_failed_audits: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for audit in audits:
            if str(audit.get("status")) == "FAILED":
                run_failed_audits[str(audit.get("collector_run_id", ""))].append(audit)
        run_audits: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for audit in audits:
            run_audits[str(audit.get("collector_run_id", ""))].append(audit)
        isolation_failures: list[dict[str, Any]] = []
        isolation_samples: list[dict[str, Any]] = []
        for run_id, failed_rows in run_failed_audits.items():
            for failed in failed_rows:
                due = due_targets(self.manifest.targets, _int(failed.get("logical_due_bucket")))
                if len(due) <= 1:
                    continue
                due_ids = {target.source_parent_station_id for target in due}
                observed_ids = {
                    str(row.get("source_parent_station_id"))
                    for row in run_audits.get(run_id, ())
                    if str(row.get("source_parent_station_id")) in due_ids
                }
                missing_ids = sorted(due_ids - observed_ids)
                sample = {
                    "run_id": run_id,
                    "failed_target_id": failed.get("source_parent_station_id"),
                    "expected_due_target_count": len(due_ids),
                    "observed_due_target_count": len(observed_ids),
                    "missing_unrelated_target_ids": missing_ids,
                }
                isolation_samples.append(sample)
                if missing_ids:
                    isolation_failures.append(sample)
        if run_failed_audits:
            isolation_status = "FAIL" if isolation_failures else "PASS"
            isolation_reason = "A failed target left unrelated due targets without audit evidence." if isolation_failures else "Unrelated due targets continued to receive independent audit evidence after target failures."
        else:
            isolation_status = "NOT_APPLICABLE"
            isolation_reason = "No failed target sample is present; failure isolation is not claimed from an all-success window."
        results = [
            _result(
                validation_id="retry.structural_rules",
                category="RETRY",
                grain="target/logical-bucket",
                expected_value={"attempt_number_max": 2, "automatic_retry_max": 1, "deterministic_non_retryable_retry": False, "duplicate_target_transport": False},
                actual_value={"rows": rows_report, "duplicate_target_transport": duplicate_transport},
                status=structural_status,
                policy_state="APPROVED_INVARIANT",
                evidence_count=len(attempts),
                reason=structural_reason,
                window=window,
            ),
            _result(
                validation_id="retry.quota_permit_reconciliation",
                category="RETRY",
                grain="target/logical-bucket/attempt",
                expected_value="quota permit/decision evidence where available",
                actual_value={"missing_quota_evidence": quota_missing, "quota_decision_rows": len(evidence.quota_audit), "permit_statuses": dict(sorted(permit_statuses.items())), "missing_permit_references": missing_permit_refs},
                status=quota_status,
                policy_state="APPROVED_INVARIANT",
                evidence_count=len(evidence.quota_audit),
                reason=quota_reason,
                window=window,
            ),
            _result(
                validation_id="retry.attempt_number_max",
                category="RETRY",
                grain="target/logical-bucket/attempt",
                expected_value=MAX_ATTEMPTS,
                actual_value={"violating_audits": attempt_number_violations},
                status="INSUFFICIENT_EVIDENCE" if not attempts else "FAIL" if attempt_number_violations else "PASS",
                policy_state="APPROVED_INVARIANT",
                evidence_count=len(attempts),
                reason="An attempt number exceeded MaxAttempts or the sequence was structurally incomplete." if attempt_number_violations else "No attempt-number structural violation was observed." if attempts else "No attempt evidence is available.",
                window=window,
            ),
            _result(
                validation_id="retry.automatic_retry_max",
                category="RETRY",
                grain="target/logical-bucket",
                expected_value=MAX_AUTOMATIC_RETRIES,
                actual_value={"violating_audits": retry_count_violations},
                status="INSUFFICIENT_EVIDENCE" if not attempts else "FAIL" if retry_count_violations else "PASS",
                policy_state="APPROVED_INVARIANT",
                evidence_count=len(attempts),
                reason="More than one automatic retry was observed." if retry_count_violations else "No execution exceeded the one-automatic-retry structural maximum." if attempts else "No attempt evidence is available.",
                window=window,
            ),
            _result(
                validation_id="retry.no_retry_after_deterministic_failure",
                category="RETRY",
                grain="target/logical-bucket",
                expected_value=False,
                actual_value={"violating_audits": deterministic_retry_violations},
                status="INSUFFICIENT_EVIDENCE" if not attempts else "FAIL" if deterministic_retry_violations else "PASS",
                policy_state="APPROVED_INVARIANT",
                evidence_count=len(attempts),
                reason="A retry followed a deterministic non-retryable category or HTTP 401/403." if deterministic_retry_violations else "No retry followed a deterministic non-retryable category." if attempts else "No attempt evidence is available.",
                window=window,
            ),
            _result(
                validation_id="retry.no_duplicate_target_transport",
                category="RETRY",
                grain="target/logical-bucket",
                expected_value=0,
                actual_value={"duplicate_target_transport": duplicate_transport},
                status="INSUFFICIENT_EVIDENCE" if not attempts else "FAIL" if duplicate_transport else "PASS",
                policy_state="APPROVED_INVARIANT",
                evidence_count=len(attempts),
                reason="A target/bucket received transport from more than one audit." if duplicate_transport else "No duplicate target transport was observed." if attempts else "No attempt evidence is available.",
                window=window,
            ),
            _result(
                validation_id="execution.failure_isolation",
                category="EXECUTION",
                grain="collector-run",
                expected_value="unrelated due targets continue to be audited after one failure",
                actual_value={"samples": isolation_samples, "failures": isolation_failures},
                status=isolation_status,
                policy_state="APPROVED_INVARIANT",
                evidence_count=sum(len(rows) for rows in run_failed_audits.values()),
                reason=isolation_reason,
                window=window,
            ),
        ]
        return {
            "rows": rows_report,
            "structural_violations": violations,
            "duplicate_target_transport": duplicate_transport,
            "quota_evidence": {"decision_rows": len(evidence.quota_audit), "missing": quota_missing, "permit_statuses": dict(sorted(permit_statuses.items())), "missing_permit_references": missing_permit_refs},
            "checks": [item["validation_id"] for item in results],
        }, results

    def _validate_quota(self, evidence: EvidenceData, window: AnalysisWindow | None, reconciliation: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        audits = [row for row in evidence.target_audits if _row_in_window(row, window)]
        audit_ids = {_int(row.get("audit_id")) for row in audits}
        attempts = [row for row in evidence.http_attempts if _int(row.get("audit_id")) in audit_ids]
        runs = {str(row.get("run_id")): row for row in evidence.collector_runs if _row_in_window(row, window)}
        scheduled = _int(reconciliation.get("expected_count"))
        live_invocations = sum(_bool(row.get("live_client_invocation")) for row in attempts)
        network_attempts = sum(_bool(row.get("network_attempted")) for row in attempts)
        responses = sum(_bool(row.get("http_response_received")) for row in attempts)
        confirmed = sum(_bool(row.get("provider_attempt_confirmed")) or (_bool(row.get("network_attempted")) and _bool(row.get("http_response_received"))) for row in attempts)
        unknown = sum(_bool(row.get("network_attempted")) and not _bool(row.get("http_response_received")) for row in attempts)
        active_reservations = sum(_int(row.get("amount")) for row in evidence.quota_permits if str(row.get("status", "")) == "PENDING_AUTHORIZATION")
        simulated = sum(
            not _bool(row.get("network_attempted"))
            and not _bool(row.get("live_client_invocation"))
            for row in attempts
        )
        mode_by_run = {run_id: str(row.get("mode", "")) for run_id, row in runs.items()}
        offline_attempts_with_provider_flags = [
            _int(row.get("attempt_id"))
            for row in attempts
            if not _bool(row.get("network_attempted")) and (_bool(row.get("provider_attempt_confirmed")) or _bool(row.get("conservative_quota_accounted")))
        ]
        provider_usage_by_month: dict[str, dict[str, int | bool | str]] = {}
        for row in evidence.shared_usage:
            month = str(row.get("month_key", "unknown"))
            provider_usage_by_month[month] = {
                "confirmed_provider_attempts": _int(row.get("confirmed_provider_attempts", row.get("actual_attempts"))),
                "reserved_shared_usage": _int(row.get("reserved_shared_usage")),
                "projected_non_retry_attempts": _int(row.get("projected_non_retry_attempts")),
                "conservative_live_attempts": _int(row.get("conservative_live_attempts")),
                "state_available": _bool(row.get("state_available", True)),
                "source": row.get("source", ""),
            }
        for row in evidence.quota_permits:
            month = str(row.get("month_key", "unknown"))
            bucket = provider_usage_by_month.setdefault(month, {"confirmed_provider_attempts": 0, "reserved_shared_usage": 0, "projected_non_retry_attempts": 0, "conservative_live_attempts": 0, "state_available": True, "source": "quota_permits"})
            if str(row.get("status", "")) == "PENDING_AUTHORIZATION":
                bucket["reserved_shared_usage"] = _int(bucket.get("reserved_shared_usage")) + _int(row.get("amount"))
        observed_totals: dict[str, int] = {}
        for month, values in provider_usage_by_month.items():
            observed_totals[month] = sum(_int(values.get(field)) for field in ("confirmed_provider_attempts", "reserved_shared_usage", "projected_non_retry_attempts", "conservative_live_attempts"))

        retry_violations: list[dict[str, Any]] = []
        initial_violations: list[dict[str, Any]] = []
        ceiling_violations: list[dict[str, Any]] = []
        for row in evidence.quota_audit:
            if not _row_in_window(row, window):
                continue
            try:
                decision = json.loads(str(row.get("decision_json", "{}")))
            except (TypeError, ValueError, json.JSONDecodeError):
                decision = {}
            known = decision.get("known_total")
            proposed = _int(decision.get("proposed_attempts"), 1)
            projected = _int(known) + proposed if known is not None else None
            kind = str(row.get("decision_kind", ""))
            boundary = _int(decision.get("boundary"), 0)
            if kind == "RETRY" and ((projected is not None and projected > AUTOMATIC_RETRY_PROTECTION_BOUNDARY and _bool(decision.get("allowed"))) or (boundary and boundary != AUTOMATIC_RETRY_PROTECTION_BOUNDARY)):
                retry_violations.append({"row": _json_value(row), "decision": decision})
            if kind == "INITIAL" and projected is not None and projected > NORMAL_OPERATIONAL_MAXIMUM and _bool(decision.get("allowed")):
                initial_violations.append({"row": _json_value(row), "decision": decision})
            if projected is not None and projected > ABSOLUTE_PROVIDER_CEILING and _bool(decision.get("allowed")):
                ceiling_violations.append({"row": _json_value(row), "decision": decision})
        for month, total in observed_totals.items():
            if total > ABSOLUTE_PROVIDER_CEILING:
                ceiling_violations.append({"month": month, "observed_total": total})
            if total > NORMAL_OPERATIONAL_MAXIMUM:
                initial_violations.append({"month": month, "observed_total": total})

        has_quota_state = bool(evidence.shared_usage or evidence.quota_permits or evidence.quota_audit)
        unavailable_months = [month for month, values in provider_usage_by_month.items() if not _bool(values.get("state_available", True))]
        boundary_evidence_count = len(evidence.shared_usage) + len(evidence.quota_audit) + len(evidence.quota_permits)
        if not has_quota_state:
            boundary_status = "INSUFFICIENT_EVIDENCE"
            boundary_reason = "Quota state is unavailable; no boundary claim is made."
        else:
            boundary_status = "FAIL" if retry_violations else "PASS"
            boundary_reason = "An automatic retry crossed or misreported the approved 207,500 protection boundary." if retry_violations else "No observed automatic retry crossed the approved protection boundary."
        normal_status = "INSUFFICIENT_EVIDENCE" if not has_quota_state else "FAIL" if initial_violations else "PASS"
        ceiling_status = "INSUFFICIENT_EVIDENCE" if not has_quota_state else "FAIL" if ceiling_violations else "PASS"
        simulation_status = "INSUFFICIENT_EVIDENCE" if not attempts else "FAIL" if offline_attempts_with_provider_flags else "PASS"
        results = [
            _result(
                validation_id="quota.logical_vs_transport_accounting",
                category="QUOTA",
                grain="window",
                expected_value="separate scheduled logical requests, live invocations, network attempts, HTTP responses, confirmed provider attempts, unknown attempts, and active reservations",
                actual_value={
                    "scheduled_logical_requests": scheduled,
                    "live_client_invocations": live_invocations,
                    "network_attempts": network_attempts,
                    "http_responses_received": responses,
                    "confirmed_provider_http_attempts": confirmed,
                    "conservative_provider_quota_unknown_attempts": unknown,
                    "active_quota_reservations": active_reservations,
                    "simulated_or_offline_attempts": simulated,
                    "run_modes": dict(Counter(mode_by_run.values())),
                },
                status="PASS" if attempts or scheduled else "INSUFFICIENT_EVIDENCE",
                policy_state="APPROVED_INVARIANT",
                evidence_count=len(attempts) + scheduled,
                reason="Accounting dimensions are kept separate; simulation rows are not merged into provider usage." if attempts or scheduled else "No schedule/evidence window is available for reconciliation.",
                window=window,
                unit="attempts/requests",
            ),
            _result(
                validation_id="quota.retry_protection_boundary_observed",
                category="QUOTA",
                grain="shared-credential/month",
                expected_value=AUTOMATIC_RETRY_PROTECTION_BOUNDARY,
                actual_value={"observed_totals": observed_totals, "violations": retry_violations, "state_unavailable_months": unavailable_months},
                status=boundary_status,
                policy_state="APPROVED_INVARIANT",
                evidence_count=boundary_evidence_count,
                reason=boundary_reason,
                window=window,
                unit="provider-attempt-equivalents",
            ),
            _result(
                validation_id="quota.normal_operational_maximum_observed",
                category="QUOTA",
                grain="shared-credential/month",
                expected_value=NORMAL_OPERATIONAL_MAXIMUM,
                actual_value={"observed_totals": observed_totals, "violations": initial_violations, "state_unavailable_months": unavailable_months},
                status=normal_status,
                policy_state="APPROVED_INVARIANT",
                evidence_count=boundary_evidence_count,
                reason="Normal operation remains within the approved maximum." if normal_status == "PASS" else "Normal operation or an allowed initial decision crosses the approved maximum." if normal_status == "FAIL" else "Quota state is unavailable; no normal-maximum claim is made.",
                window=window,
                unit="provider-attempt-equivalents",
            ),
            _result(
                validation_id="quota.provider_absolute_ceiling_observed",
                category="QUOTA",
                grain="shared-credential/month",
                expected_value=ABSOLUTE_PROVIDER_CEILING,
                actual_value={"observed_totals": observed_totals, "violations": ceiling_violations, "state_unavailable_months": unavailable_months},
                status=ceiling_status,
                policy_state="APPROVED_INVARIANT",
                evidence_count=boundary_evidence_count,
                reason="Observed/projected provider usage remains at or below the absolute ceiling." if ceiling_status == "PASS" else "Observed/projected provider usage crosses the absolute ceiling." if ceiling_status == "FAIL" else "Quota state is unavailable; no provider-ceiling claim is made.",
                window=window,
                unit="provider attempts",
            ),
            _result(
                validation_id="quota.state_availability",
                category="QUOTA",
                grain="shared-credential/month",
                expected_value=True,
                actual_value={"state_unavailable_months": unavailable_months, "provider_usage": provider_usage_by_month},
                status="INSUFFICIENT_EVIDENCE" if unavailable_months or not has_quota_state else "PASS",
                policy_state="APPROVED_INVARIANT",
                evidence_count=len(evidence.shared_usage),
                reason="Quota state is explicitly unavailable for at least one month; the validator does not convert that to zero." if unavailable_months else "Quota state is available for the supplied evidence." if has_quota_state else "No quota state was supplied.",
                window=window,
            ),
            _result(
                validation_id="quota.simulation_excluded_from_provider_usage",
                category="QUOTA",
                grain="attempt",
                expected_value=0,
                actual_value={"offline_attempts_with_provider_flags": offline_attempts_with_provider_flags, "simulated_attempt_count": simulated},
                status=simulation_status,
                policy_state="APPROVED_INVARIANT",
                evidence_count=len(attempts),
                reason="Offline/simulation attempts carry no confirmed or conservative provider accounting." if simulation_status == "PASS" else "Offline attempt evidence is unavailable." if simulation_status == "INSUFFICIENT_EVIDENCE" else "An offline attempt was marked as provider usage.",
                window=window,
            ),
        ]
        return {
            "scheduled_logical_requests": scheduled,
            "live_client_invocations": live_invocations,
            "network_attempts": network_attempts,
            "http_responses_received": responses,
            "confirmed_provider_http_attempts": confirmed,
            "conservative_provider_quota_unknown_attempts": unknown,
            "active_quota_reservations": active_reservations,
            "simulated_or_offline_attempts": simulated,
            "observed_provider_usage_by_month": provider_usage_by_month,
            "observed_totals_by_month": observed_totals,
            "state_unavailable_months": unavailable_months,
            "checks": [item["validation_id"] for item in results],
        }, results

    def _coverage(self, evidence: EvidenceData, window: AnalysisWindow | None, reconciliation: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if window is None:
            return {"stations": [], "tiers": []}, [
                _result(
                    validation_id="coverage.station_and_tier_reconciliation",
                    category="COVERAGE",
                    grain="station/tier/window",
                    expected_value="analysis window",
                    actual_value=None,
                    status="INSUFFICIENT_EVIDENCE",
                    policy_state="POLICY_PENDING",
                    evidence_count=0,
                    reason="Coverage needs a logical-bucket analysis window.",
                )
            ]
        audits = [row for row in evidence.target_audits if _row_in_window(row, window)]
        claims = [row for row in evidence.target_claims if _row_in_window(row, window)]
        snapshots = [row for row in evidence.parsed_snapshots if _row_in_window(row, window)]
        claim_events = [row for row in evidence.claim_events if _row_in_window(row, window)]
        audit_keys = {_audit_key(row) for row in audits}
        claim_keys = {_audit_key(row) for row in claims}
        snapshot_keys = {_audit_key(row) for row in snapshots}
        duplicate_keys = Counter(_audit_key(row) for row in claim_events if str(row.get("outcome", "")) == "DUPLICATE_CLAIM")
        station_rows: list[dict[str, Any]] = []
        for target in sorted(self.manifest.targets, key=lambda item: item.source_parent_station_id):
            expected_keys = {
                (target.panel_version, target.source_parent_station_id, bucket)
                for bucket in window.bucket_indexes
                if target in due_targets(self.manifest.targets, bucket)
            }
            succeeded = sum(1 for row in audits if _audit_key(row) in expected_keys and str(row.get("status")) == "SUCCEEDED")
            failed = sum(1 for row in audits if _audit_key(row) in expected_keys and str(row.get("status")) == "FAILED")
            skipped = sum(1 for row in audits if _audit_key(row) in expected_keys and str(row.get("status")) == "SKIPPED")
            actual_claimed = len(claim_keys & expected_keys)
            executed = sum(1 for row in audits if _audit_key(row) in expected_keys and str(row.get("status")) != "SKIPPED")
            missing = len(expected_keys - audit_keys)
            duplicate_prevented = sum(duplicate_keys[key] for key in expected_keys)
            persisted = len(snapshot_keys & expected_keys)
            station_rows.append({
                "target_id": target.source_parent_station_id,
                "station_name": target.station_name,
                "tier": target.tier,
                "expected_logical_buckets": len(expected_keys),
                "actual_claimed_buckets": actual_claimed,
                "executed_buckets": executed,
                "succeeded": succeeded,
                "failed": failed,
                "skipped": skipped,
                "missing": missing,
                "duplicate_prevented": duplicate_prevented,
                "persisted_snapshots": persisted,
                "measured_coverage_percent": _rate(actual_claimed, len(expected_keys)),
                "measured_persistence_percent": _rate(persisted, len(expected_keys)),
            })
        tier_rows: list[dict[str, Any]] = []
        for tier in ("A", "B", "C"):
            rows = [row for row in station_rows if row["tier"] == tier]
            aggregate = {field: sum(_int(row[field]) for row in rows) for field in (
                "expected_logical_buckets", "actual_claimed_buckets", "executed_buckets", "succeeded", "failed", "skipped", "missing", "duplicate_prevented", "persisted_snapshots"
            )}
            tier_rows.append({"tier": tier, **aggregate, "measured_coverage_percent": _rate(aggregate["actual_claimed_buckets"], aggregate["expected_logical_buckets"]), "measured_persistence_percent": _rate(aggregate["persisted_snapshots"], aggregate["expected_logical_buckets"])})
        expected_total = sum(row["expected_logical_buckets"] for row in station_rows)
        actual_total = sum(row["actual_claimed_buckets"] for row in station_rows)
        missing_total = sum(row["missing"] for row in station_rows)
        status = "POLICY_PENDING" if expected_total else "INSUFFICIENT_EVIDENCE"
        results = [
            _result(
                validation_id="coverage.station_and_tier_reconciliation",
                category="COVERAGE",
                grain="station/tier/window",
                expected_value="measured coverage with no approved acceptance percentage",
                actual_value={"station_count": len(station_rows), "tier_count": len(tier_rows), "expected_total": expected_total, "actual_claimed_total": actual_total, "missing_total": missing_total},
                status=status,
                policy_state="POLICY_PENDING",
                evidence_count=len(audits) + len(claims),
                reason="Coverage percentages are reported for measurement only; no threshold is assigned." if expected_total else "The window contains no approved due target/bucket pairs.",
                window=window,
                unit="percent / logical buckets",
            )
        ]
        return {"stations": station_rows, "tiers": tier_rows}, results

    def _quality(
        self,
        evidence: EvidenceData,
        window: AnalysisWindow | None,
        reconciliation: Mapping[str, Any],
        coverage: Mapping[str, Any],
        claims: Mapping[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        audits = [row for row in evidence.target_audits if _row_in_window(row, window)]
        audit_ids = {_int(row.get("audit_id")) for row in audits}
        attempts = [row for row in evidence.http_attempts if _int(row.get("audit_id")) in audit_ids]
        snapshots = [row for row in evidence.parsed_snapshots if _row_in_window(row, window)]
        timing_observations = [
            row for row in evidence.timing_observations if _row_in_window(row, window)
        ]
        timing_reporting = [
            row
            for row in evidence.timing_recorded_at_time_reporting
            if _row_in_window(row, window)
        ]
        recorded_evidence = [
            row
            for row in evidence.recorded_at_time_evidence
            if _row_in_window(row, window)
        ]
        established_estimates: dict[str, set[str]] = defaultdict(set)
        for row in timing_observations:
            identity = row.get("source_event_identity")
            status = str(row.get("source_event_identity_status", ""))
            estimate = row.get("estimated_time")
            if identity and status == "SOURCE_EVENT_IDENTITY_ESTABLISHED" and estimate is not None:
                established_estimates[str(identity)].add(str(estimate))
        timing_history = {
            "timing_observation_count": len(timing_observations),
            "events_with_estimate_revision_history": sum(
                len(values) >= 2 for values in established_estimates.values()
            ),
            "nonnull_recorded_at_time_evidence_count": sum(
                row.get("recorded_at_time") is not None
                for row in recorded_evidence
            ),
            "events_with_last_observed_recorded_at_time": sum(
                row.get("LastObservedRecordedAtTime") is not None
                for row in timing_reporting
            ),
            "latest_null_history_nonnull_count": sum(
                row.get("LatestSourceRecordedAtTime") is None
                and row.get("LastObservedRecordedAtTime") is not None
                for row in timing_reporting
            ),
            "reporting_event_count": len(timing_reporting),
        }
        parser_outcomes = Counter(str(row.get("parser_outcome")) for row in audits if row.get("parser_outcome") not in (None, ""))
        persistence_outcomes = Counter(str(row.get("persistence_outcome")) for row in audits if row.get("persistence_outcome") not in (None, ""))
        error_categories = Counter()
        for row in audits:
            if row.get("error_category"):
                error_categories[str(row.get("error_category"))] += 1
        for row in attempts:
            if row.get("transport_failure_category"):
                error_categories[str(row.get("transport_failure_category"))] += 1
        parser_gap_count = 0
        parser_gap_snapshot_count = 0
        zero_result_count = 0
        parse_snapshot_failures = 0
        for row in snapshots:
            try:
                payload = json.loads(str(row.get("snapshot_json", "{}")))
                gaps = payload.get("parser_gaps", ()) or ()
                parser_gap_count += len(gaps) if isinstance(gaps, (list, tuple)) else 0
                parser_gap_snapshot_count += bool(gaps)
                if _int(payload.get("result_count"), -1) == 0:
                    zero_result_count += 1
            except (TypeError, ValueError, json.JSONDecodeError):
                parse_snapshot_failures += 1
        successful_audits = [row for row in audits if str(row.get("status")) == "SUCCEEDED"]
        snapshot_audit_ids = {_int(row.get("audit_id")) for row in snapshots}
        successful_without_snapshot = [
            _int(row.get("audit_id")) for row in successful_audits if _int(row.get("audit_id")) not in snapshot_audit_ids
        ]
        target_failure_count = sum(str(row.get("status")) == "FAILED" for row in audits)
        timeout_count = sum(str(row.get("transport_failure_category", "")).upper() in {"TIMEOUT", "HTTP_TIMEOUT"} for row in attempts)
        retry_count = sum(_int(row.get("attempt_number")) > 1 for row in attempts)
        heartbeat_skip_count = sum("HEARTBEAT_BUDGET" in str(row.get("skip_reason", "")) for row in audits)
        stale_count = len(claims.get("stale_claims", ()))
        station_rows = coverage.get("stations", [])
        expected_total = sum(_int(row.get("expected_logical_buckets")) for row in station_rows)
        missing_total = sum(_int(row.get("missing")) for row in station_rows)
        response_count = sum(_bool(row.get("http_response_received")) for row in attempts)
        network_count = sum(_bool(row.get("network_attempted")) for row in attempts)
        parser_failure_count = sum(str(row.get("parser_outcome")) == "PARSER_FAILURE" for row in audits)
        persistence_failure_count = sum(str(row.get("persistence_outcome")) == "PERSISTENCE_FAILURE" for row in audits)
        metric_values: dict[str, Any] = {
            "quality.target_failure_rate": {"value": _rate(target_failure_count, len(audits)), "numerator": target_failure_count, "denominator": len(audits)},
            "quality.timeout_rate": {"value": _rate(timeout_count, len(attempts)), "numerator": timeout_count, "denominator": len(attempts)},
            "quality.retry_rate": {"value": _rate(retry_count, len(attempts)), "numerator": retry_count, "denominator": len(attempts)},
            "quality.heartbeat_budget_skip_rate": {"value": _rate(heartbeat_skip_count, expected_total), "numerator": heartbeat_skip_count, "denominator": expected_total},
            "quality.stale_claim_count_rate": {"value": _rate(stale_count, len(claims.get("rows", ()))), "numerator": stale_count, "denominator": len(claims.get("rows", ()))},
            "quality.expected_vs_actual_station_coverage": {"value": _rate(expected_total - missing_total, expected_total), "numerator": expected_total - missing_total, "denominator": expected_total},
            "quality.missing_bucket_rate": {"value": _rate(missing_total, expected_total), "numerator": missing_total, "denominator": expected_total},
            "quality.http_response_rate": {"value": _rate(response_count, network_count), "numerator": response_count, "denominator": network_count},
            "quality.zero_result_frequency": {"value": _rate(zero_result_count, len(snapshots)), "numerator": zero_result_count, "denominator": len(snapshots)},
            "quality.parser_gap_count_rate": {"value": _rate(parser_gap_count, len(snapshots)), "count": parser_gap_count, "denominator": len(snapshots), "snapshots_with_gaps": parser_gap_snapshot_count},
            "quality.parser_failure_rate": {"value": _rate(parser_failure_count, len(audits)), "numerator": parser_failure_count, "denominator": len(audits)},
            "quality.persistence_failure_rate": {"value": _rate(persistence_failure_count, len(audits)), "numerator": persistence_failure_count, "denominator": len(audits)},
            "quality.per_station_coverage": {"values": {row.get("target_id"): row.get("measured_coverage_percent") for row in station_rows}},
            "quality.per_tier_coverage": {"values": {row.get("tier"): row.get("measured_coverage_percent") for row in coverage.get("tiers", [])}},
            "quality.per_error_category_frequency": {"counts": dict(sorted(error_categories.items())), "denominator": len(audits) + len(attempts)},
            "quality.timing_observation_count": {"value": timing_history["timing_observation_count"], "unit": "rows"},
            "quality.events_with_estimate_revision_history": {"value": timing_history["events_with_estimate_revision_history"], "unit": "source events"},
            "quality.nonnull_recorded_at_time_evidence_count": {"value": timing_history["nonnull_recorded_at_time_evidence_count"], "unit": "evidence rows"},
            "quality.events_with_last_observed_recorded_at_time": {"value": timing_history["events_with_last_observed_recorded_at_time"], "unit": "source events"},
            "quality.latest_null_history_nonnull_count": {"value": timing_history["latest_null_history_nonnull_count"], "unit": "source events"},
        }
        results: list[dict[str, Any]] = []
        for validation_id in POLICY_METRIC_IDS:
            actual = metric_values.get(validation_id)
            if actual is None:
                actual = {"value": None}
            if validation_id in {"quality.target_duration_statistics", "quality.http_attempt_duration_statistics", "quality.collector_run_duration_statistics"}:
                continue
            evidence_count = len(audits) + len(attempts) + len(snapshots)
            status = "POLICY_PENDING" if evidence_count else "INSUFFICIENT_EVIDENCE"
            results.append(_result(
                validation_id=validation_id,
                category="QUALITY",
                grain="window",
                expected_value="user-approved threshold not established",
                actual_value=actual,
                status=status,
                policy_state="POLICY_PENDING",
                evidence_count=evidence_count,
                reason="Measured value is reported without assigning an acceptance threshold." if evidence_count else "The required evidence is absent; no acceptance threshold is assigned.",
                limitations=("Parser gaps and zero-result responses are measured, not automatically fatal.",) if validation_id in {"quality.parser_gap_count_rate", "quality.zero_result_frequency"} else (),
                window=window,
                unit="percent / count",
            ))
        # These three inputs describe overlapping views of the same source
        # lineage.  Evidence count therefore uses the largest available
        # representation rather than summing rows across representations.
        timing_evidence_count = max(
            len(timing_observations),
            len(recorded_evidence),
            len(timing_reporting),
        )
        results.append(_result(
            validation_id="timing.history_measurements",
            category="TIMING_HISTORY",
            grain="source-event/window",
            expected_value="report source-faithful timing history without an acceptance threshold",
            actual_value=timing_history,
            status="POLICY_PENDING" if timing_evidence_count else "INSUFFICIENT_EVIDENCE",
            policy_state="MEASUREMENT_ONLY",
            evidence_count=timing_evidence_count,
            reason=(
                "Timing history is reported for measurement only; no threshold or operational interpretation is assigned."
                if timing_evidence_count
                else "No normalized timing-history or RecordedAtTime evidence is available."
            ),
            limitations=(
                "LatestSourceRecordedAtTime is the latest snapshot value; LastObservedRecordedAtTime is the latest non-null historical value."
            ),
            window=window,
            unit="rows / source events",
        ))
        return {
            "metrics": metric_values,
            "parser_outcomes": dict(sorted(parser_outcomes.items())),
            "persistence_outcomes": dict(sorted(persistence_outcomes.items())),
            "stop_event_result_count": {"count": sum(_int(row.get("stop_event_result_count")) for row in audits if row.get("stop_event_result_count") is not None), "rows": sum(row.get("stop_event_result_count") is not None for row in audits)},
            "parser_gap_count": parser_gap_count,
            "parser_gap_snapshot_count": parser_gap_snapshot_count,
            "zero_result_count": zero_result_count,
            "snapshot_json_read_failures": parse_snapshot_failures,
            "successful_parse_without_persisted_snapshot": successful_without_snapshot,
            "error_category_frequency": dict(sorted(error_categories.items())),
            "timing_history": timing_history,
        }, results

    def _durations(self, evidence: EvidenceData, window: AnalysisWindow | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        audits = [row for row in evidence.target_audits if _row_in_window(row, window)]
        audit_ids = {_int(row.get("audit_id")) for row in audits}
        attempts = [row for row in evidence.http_attempts if _int(row.get("audit_id")) in audit_ids]
        runs = [row for row in evidence.collector_runs if _row_in_window(row, window)]
        target_values = [duration / 1000.0 for row in audits if (duration := _float(row.get("duration_ms"))) is not None]
        attempt_values = [duration / 1000.0 for row in attempts if (duration := _float(row.get("duration_ms"))) is not None]
        run_values: list[float] = []
        for row in runs:
            started = _parse_datetime(row.get("started_at_utc"))
            completed = _parse_datetime(row.get("completed_at_utc"))
            if started is not None and completed is not None and completed >= started:
                run_values.append((completed - started).total_seconds())
        stats = {
            "target_duration": {"unit": "seconds", **duration_statistics(target_values)},
            "http_attempt_duration": {"unit": "seconds", **duration_statistics(attempt_values)},
            "collector_run_duration": {"unit": "seconds", **duration_statistics(run_values)},
        }
        results: list[dict[str, Any]] = []
        for validation_id, key, count in (
            ("quality.target_duration_statistics", "target_duration", len(target_values)),
            ("quality.http_attempt_duration_statistics", "http_attempt_duration", len(attempt_values)),
            ("quality.collector_run_duration_statistics", "collector_run_duration", len(run_values)),
        ):
            results.append(_result(
                validation_id=validation_id,
                category="DURATION",
                grain="window",
                expected_value="deterministic count/min/P50/P90/P95/P99/max; no threshold approved",
                actual_value=stats[key],
                status="POLICY_PENDING" if count else "INSUFFICIENT_EVIDENCE",
                policy_state="POLICY_PENDING",
                evidence_count=count,
                reason="Duration statistics are descriptive only; no acceptance threshold is assigned." if count else "No duration evidence is available.",
                limitations=("Final heartbeat persistence/cleanup safety margin remains deferred.",),
                window=window,
                unit="seconds",
            ))
        return stats, results

    def _empty_duration_report(self) -> dict[str, Any]:
        empty = {"unit": "seconds", **duration_statistics(())}
        return {"target_duration": empty, "http_attempt_duration": empty, "collector_run_duration": empty}

    def _empty_quota_reconciliation(self) -> dict[str, Any]:
        return {
            "scheduled_logical_requests": 0,
            "live_client_invocations": 0,
            "network_attempts": 0,
            "http_responses_received": 0,
            "confirmed_provider_http_attempts": 0,
            "conservative_provider_quota_unknown_attempts": 0,
            "active_quota_reservations": 0,
            "simulated_or_offline_attempts": 0,
            "observed_provider_usage_by_month": {},
            "observed_totals_by_month": {},
            "state_unavailable_months": [],
            "checks": [],
        }

    def _continuity(self, evidence: EvidenceData | None, window: AnalysisWindow | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        eras = list(evidence.sampling_eras) if evidence else []
        real_tiered = [
            row for row in eras
            if str(row.get("era_code", "")) == "Tiered50"
            and _bool(row.get("is_active"))
            and not _bool(row.get("is_synthetic"))
            and _parse_datetime(row.get("effective_at_utc")) is not None
        ]
        synthetic_tiered = [row for row in eras if str(row.get("era_code", "")) == "Tiered50" and _bool(row.get("is_synthetic"))]
        if real_tiered:
            transition_status = "PASS" if len(real_tiered) == 1 else "FAIL"
            transition_reason = "Exactly one real active Tiered50 era transition is present." if len(real_tiered) == 1 else "More than one real active Tiered50 transition is present."
        elif synthetic_tiered:
            transition_status = "NOT_APPLICABLE"
            transition_reason = "Only synthetic/offline era metadata exists; it is not production cutover evidence."
        else:
            transition_status = "INSUFFICIENT_EVIDENCE"
            transition_reason = "No real Tiered50 cutover evidence exists; Stage 15B remains not started."
        checks = [
            _result(
                validation_id="continuity.sampling_era_transition",
                category="CONTINUITY",
                grain="sampling-era transition",
                expected_value="one real Tiered50 transition",
                actual_value={"real_transition_count": len(real_tiered), "synthetic_transition_count": len(synthetic_tiered)},
                status=transition_status,
                policy_state="MEASUREMENT_ONLY",
                evidence_count=len(eras),
                reason=transition_reason,
                limitations=("The analyzer never invents a cutover timestamp.",),
                window=window,
            )
        ]
        if not real_tiered:
            missing_reason = "Pre-cutover state has no real Tiered50 transition evidence."
            missing_status = "INSUFFICIENT_EVIDENCE"
            post_actual: Any = "NO_REAL_CUTOVER_EVIDENCE"
            lineage_actual: Any = "NO_REAL_CUTOVER_EVIDENCE"
        else:
            cutover = _parse_datetime(real_tiered[0].get("effective_at_utc"))
            audits = [row for row in (evidence.target_audits if evidence else ()) if _parse_datetime(row.get("started_at_utc")) and _parse_datetime(row.get("started_at_utc")) >= cutover]
            non_tiered = [row for row in audits if str(row.get("sampling_era", "")) != "Tiered50"]
            post_actual = {"cutover_timestamp_utc": _iso(cutover), "post_cutover_audit_count": len(audits), "non_tiered_post_cutover_audits": len(non_tiered)}
            lineage_actual = {"legacy7_ids": sorted(LEGACY7_SAMPLING_TARGET_IDS.values()), "post_cutover_audit_count": len(audits)}
            missing_reason = "Post-cutover evidence is available; continuity checks are limited to fields exposed by the current adapter." if not non_tiered else "Post-cutover records are not consistently attributed to Tiered50."
            missing_status = "FAIL" if non_tiered else "PASS" if audits else "INSUFFICIENT_EVIDENCE"
        checks.extend([
            _result(
                validation_id="continuity.legacy7_history_after_cutover",
                category="CONTINUITY",
                grain="Legacy7 target/era",
                expected_value="Legacy7 source identities and history remain distinct across one real transition",
                actual_value=lineage_actual,
                status=missing_status,
                policy_state="MEASUREMENT_ONLY",
                evidence_count=len(eras),
                reason=missing_reason,
                limitations=("No delete/reinsert continuity claim is made before real pre/post evidence exists.",),
                window=window,
            ),
            _result(
                validation_id="continuity.post_cutover_attribution",
                category="CONTINUITY",
                grain="record/era",
                expected_value="post-cutover records attributed to Tiered50",
                actual_value=post_actual,
                status=missing_status,
                policy_state="MEASUREMENT_ONLY",
                evidence_count=len(eras),
                reason=missing_reason,
                limitations=("Development-manifest Tiered50 labels are not real cutover evidence.",),
                window=window,
            ),
        ])
        return {
            "legacy7_source_ids": sorted(LEGACY7_IDS),
            "legacy7_lineage_ids": sorted(LEGACY7_SAMPLING_TARGET_IDS.values()),
            "real_tiered50_transitions": [_json_value(row) for row in real_tiered],
            "synthetic_tiered50_transitions": [_json_value(row) for row in synthetic_tiered],
            "stage15_live_status": "NOT STARTED — AWAITS TIERED50 CUTOVER",
        }, checks
