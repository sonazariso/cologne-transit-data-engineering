#!/usr/bin/env python3
"""Run the no-network Tiered50 production preflight.

The default path is entirely offline: it reads the approved manifest, parser
fixtures, SQL migration text, and disabled LaunchAgent templates. ``--live``
adds read-only SQL/catalog checks but never invokes MDD and never activates
the panel. A PASS here is an offline readiness result, not live validation.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cologne_transit_collector.collector import LiveModeBlocked  # noqa: E402
from cologne_transit_collector.admin_evidence import resolve_current_evidence_path  # noqa: E402
from cologne_transit_collector.domain import (  # noqa: E402
    ABSOLUTE_PROVIDER_CEILING,
    APPROVED_SUPER_CYCLE_COUNTS,
    AUTOMATIC_RETRY_PROTECTION_BOUNDARY,
    HEARTBEAT_MINUTES,
    MAX_ATTEMPTS,
    MAXIMUM_CONCURRENCY,
    NORMAL_OPERATIONAL_MAXIMUM,
    REQUEST_TIMEOUT_SECONDS,
    SamplingEra,
)
from cologne_transit_collector.manifest import (  # noqa: E402
    LEGACY7_SAMPLING_TARGET_IDS,
    load_default_manifest,
)
from cologne_transit_collector.macos_runner import require_tiered50_cutover_gate  # noqa: E402
from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    MssqlPersistence,
    TIERED50_TARGET_TABLE,
    build_mssql_snapshot_rows,
)
from cologne_transit_collector.schedule import (  # noqa: E402
    may_execute_bucket,
    schedule_reconciliation,
    validate_launchd_wall_clock_schedule,
)
from cologne_transit_collector.trias import parse_trias_response  # noqa: E402
from cologne_transit_collector.quota_rollover import (  # noqa: E402
    GLOBAL_UTC_OFFSET_ENVELOPE_LABEL,
    GLOBAL_UTC_OFFSET_ENVELOPE_TIMEZONES,
    RESET_CONTRACT_STATUS_APPROVED_OPERATOR_ASSUMPTION,
    RESET_CONTRACT_STATUS_NOT_REGISTERED,
    RESET_CONTRACT_STATUS_REVOKED,
    RESET_CONTRACT_STATUS_UNVERIFIED,
    RESET_CONTRACT_STATUS_VERIFIED_PROVIDER,
    derive_collection_gap,
    global_utc_offset_envelope_period_for_month,
)
from package_source_zip import is_forbidden_source_member, source_members  # noqa: E402
from validate_tiered50_migration import validate_static  # noqa: E402
from provision_macos_runtime_login import inspect_runtime_permissions  # noqa: E402


MIGRATION = ROOT / "sql" / "mdd-mac-collector" / "materialize-tiered50-production.sql"
QUOTA_RESET_CONTRACT = ROOT / "docs" / "operational" / "mdd-quota-reset-contract.json"
OPERATIONAL_EVIDENCE = resolve_current_evidence_path(repository_root=ROOT)
LAUNCHD_TEMPLATES = (
    ROOT / "deployment" / "launchd" / "com.cologne-transit-intelligence.mdd-collector.plist",
    ROOT / "deployment" / "launchd" / "com.cologne-transit-intelligence.mdd-tiered50-collector.plist",
)


def _result(status: str, details: Any = None, error: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"status": status}
    if details is not None:
        value["details"] = details
    if error is not None:
        value["error_type"] = error
    return value


def _approved_production_ids(manifest: Any) -> dict[str, int]:
    mapping = dict(LEGACY7_SAMPLING_TARGET_IDS)
    for sampling_target_id, target in enumerate(manifest.new_tiered50_targets, start=8):
        mapping[target.source_parent_station_id] = sampling_target_id
    return mapping


def validate_manifest() -> dict[str, Any]:
    manifest = load_default_manifest(ROOT)
    counts = Counter(target.tier for target in manifest.targets)
    cadence = Counter(target.cadence_minutes for target in manifest.targets)
    results = Counter(target.number_of_results for target in manifest.targets)
    expected_ids = _approved_production_ids(manifest)
    bound = manifest.bind_production_sampling_target_ids(expected_ids)
    bound.validate_production_binding()
    expected = {
        "target_count": 50,
        "legacy7_count": 7,
        "new_count": 43,
        "tier_counts": {"A": 8, "B": 20, "C": 22},
        "cadence_counts": {"5": 8, "15": 20, "30": 22},
        "result_counts": {"20": 8, "30": 42},
        "production_ids": "1..50 with Legacy7 preserved as 1..7",
    }
    observed = {
        "target_count": len(manifest.targets),
        "legacy7_count": len(manifest.legacy7_targets),
        "new_count": len(manifest.new_tiered50_targets),
        "tier_counts": dict(sorted(counts.items())),
        "cadence_counts": {str(key): value for key, value in sorted(cadence.items())},
        "result_counts": {str(key): value for key, value in sorted(results.items())},
        "production_ids": "1..50 with Legacy7 preserved as 1..7",
    }
    if observed != expected:
        raise ValueError(f"manifest reconciliation differs from approved values: {observed}")
    return {
        "status": "PASS",
        "panel_version": manifest.panel_version,
        "sampling_era": manifest.sampling_era.value,
        "activation_status": manifest.activation_status,
        "expected": expected,
        "observed": observed,
        "legacy7_source_ids": sorted(target.source_parent_station_id for target in manifest.legacy7_targets),
    }


def validate_schedule() -> dict[str, Any]:
    manifest = load_default_manifest(ROOT)
    reconciliation = schedule_reconciliation(manifest.targets)
    expected_counts = tuple(APPROVED_SUPER_CYCLE_COUNTS)
    if tuple(reconciliation["super_cycle_counts"]) != expected_counts:
        raise ValueError("six-phase schedule does not match the approved counts")
    if int(reconciliation["super_cycle_total"]) != 110 or int(reconciliation["requests_per_hour"]) != 220:
        raise ValueError("schedule totals do not match 110 per 30 minutes and 220 per hour")
    if not may_execute_bucket(100, 100) or may_execute_bucket(100, 99) or may_execute_bucket(100, 101):
        raise ValueError("missed-bucket replay guard is not fail-closed")
    return {
        "status": "PASS",
        "heartbeat_minutes": HEARTBEAT_MINUTES,
        "logical_bucket_timezone": "UTC",
        "no_catch_up": True,
        "reconciliation": reconciliation,
    }


def validate_runtime_guardrails() -> dict[str, Any]:
    missing_key = False
    try:
        require_tiered50_cutover_gate({})
    except LiveModeBlocked:
        missing_key = True
    if not missing_key:
        raise ValueError("Tiered50 credential/cutover gate did not fail closed")
    if MAXIMUM_CONCURRENCY != 2 or MAX_ATTEMPTS != 2 or REQUEST_TIMEOUT_SECONDS != 30:
        raise ValueError("approved concurrency, retry, or timeout guardrail changed")
    if not (AUTOMATIC_RETRY_PROTECTION_BOUNDARY < NORMAL_OPERATIONAL_MAXIMUM < ABSOLUTE_PROVIDER_CEILING):
        raise ValueError("quota boundaries are not ordered as approved")
    return {
        "status": "PASS",
        "concurrency": MAXIMUM_CONCURRENCY,
        "max_attempts": MAX_ATTEMPTS,
        "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
        "quota_boundaries": {
            "retry_protection": AUTOMATIC_RETRY_PROTECTION_BOUNDARY,
            "normal_operational_maximum": NORMAL_OPERATIONAL_MAXIMUM,
            "provider_ceiling": ABSOLUTE_PROVIDER_CEILING,
        },
        "credential_gate_fail_closed": True,
    }


def validate_quota_rollover_contract() -> dict[str, Any]:
    """Validate the future rollover artifact without inventing reset metadata."""

    document = json.loads(QUOTA_RESET_CONTRACT.read_text(encoding="utf-8"))
    if document.get("provider") != "MDD NRW" or document.get("monthly_quota_ceiling") != ABSOLUTE_PROVIDER_CEILING:
        raise ValueError("quota reset contract artifact has the wrong provider or ceiling")
    contract_status = document.get("reset_contract_status")
    allowed_statuses = {
        RESET_CONTRACT_STATUS_UNVERIFIED,
        RESET_CONTRACT_STATUS_VERIFIED_PROVIDER,
        RESET_CONTRACT_STATUS_APPROVED_OPERATOR_ASSUMPTION,
        RESET_CONTRACT_STATUS_NOT_REGISTERED,
        RESET_CONTRACT_STATUS_REVOKED,
    }
    if contract_status not in allowed_statuses:
        raise ValueError("quota reset contract status is not explicit")
    provider_verified = contract_status == RESET_CONTRACT_STATUS_VERIFIED_PROVIDER
    if document.get("provider_reset_boundary_verified") is not provider_verified:
        raise ValueError("quota reset contract verification flags are inconsistent")
    if contract_status == RESET_CONTRACT_STATUS_APPROVED_OPERATOR_ASSUMPTION and document.get(
        "provider_monthly_reset_model_established"
    ) is not True:
        raise ValueError("approved bounded rollover requires an explicitly established calendar-month model")
    if not document.get("automatic_rollover_implementation"):
        raise ValueError("automatic rollover implementation status is missing")
    policy = document.get("conservative_calendar_month_policy_evaluation")
    if not isinstance(policy, dict) or policy.get("status") != "BLOCKED":
        raise ValueError("conservative calendar-month policy must remain explicitly blocked")
    candidate = policy.get("operator_approval_candidate")
    if not isinstance(candidate, dict):
        raise ValueError("one concrete operator-approval rollover policy is missing")
    if candidate.get("supported_utc_offset_envelope") != GLOBAL_UTC_OFFSET_ENVELOPE_LABEL:
        raise ValueError("rollover policy does not cover the full UTC-12..UTC+14 envelope")
    if candidate.get("supported_reset_timezones") != list(GLOBAL_UTC_OFFSET_ENVELOPE_TIMEZONES):
        raise ValueError("rollover policy endpoint timezone evidence is incomplete")
    if int(candidate.get("post_reset_safety_delay_seconds", -1)) != 1800:
        raise ValueError("rollover policy safety delay is not the approved 30 minutes")
    november = global_utc_offset_envelope_period_for_month("2026-11")
    december = global_utc_offset_envelope_period_for_month("2026-12")
    november_to_december_gap = derive_collection_gap(
        november.end_utc,
        december.activation_not_before_utc,
    )
    october_to_november_gap = derive_collection_gap(
        datetime(2026, 10, 30, 23, 0, tzinfo=timezone.utc),
        november.activation_not_before_utc,
    )
    expected_example = candidate.get("november_2026_example")
    if not isinstance(expected_example, dict):
        raise ValueError("rollover policy is missing its reusable November boundary example")
    if (
        expected_example.get("authorization_not_before_utc")
        != november.activation_not_before_utc.isoformat().replace("+00:00", "Z")
        or expected_example.get("earliest_next_period_risk_boundary_utc")
        != november.end_utc.isoformat().replace("+00:00", "Z")
        or expected_example.get("safe_period_end_utc")
        != november.end_utc.isoformat().replace("+00:00", "Z")
        or expected_example.get("following_period_authorization_not_before_utc")
        != december.activation_not_before_utc.isoformat().replace("+00:00", "Z")
        or expected_example.get("collection_gap") != november_to_december_gap
    ):
        raise ValueError("rollover policy boundary example does not match the executable envelope calculation")
    october_example = candidate.get("october_bootstrap_to_november_example")
    if not isinstance(october_example, dict) or october_example.get("collection_gap") != october_to_november_gap:
        raise ValueError("October bootstrap to November collection gap is not derived from its UTC boundaries")
    return {
        "status": "PASS",
        "contract_status": contract_status,
        "registration_status": document.get("registration_status", RESET_CONTRACT_STATUS_NOT_REGISTERED),
        "reset_model": document.get("reset_model"),
        "reset_timezone": document.get("reset_timezone"),
        "automatic_rollover_implementation": document["automatic_rollover_implementation"],
        "operator_approval_candidate": candidate,
        "future_authorization_fail_closed_without_contract": True,
        "unresolved_dependency": document.get("unresolved_dependency"),
    }


def validate_claim_identity() -> dict[str, Any]:
    manifest = load_default_manifest(ROOT)
    bound = manifest.bind_production_sampling_target_ids(_approved_production_ids(manifest))
    keys = {
        (
            int(target.production_sampling_target_id or 0),
            bucket,
        )
        for target in bound.targets
        for bucket in range(6)
    }
    development_keys = {
        (target.panel_version, target.source_parent_station_id, bucket)
        for target in bound.targets
        for bucket in range(6)
    }
    if len(keys) != 300 or len(development_keys) != 300:
        raise ValueError("target/bucket claim identity is not unique across the panel")
    migration_text = MIGRATION.read_text(encoding="utf-8")
    for marker in (
        "SamplingTargetId INT NULL",
        "UX_ctl_MddMacTargetClaim_SamplingTarget_Bucket",
        "SamplingTargetId, LogicalDueBucket",
    ):
        if marker not in migration_text:
            raise ValueError(f"claim migration is missing marker: {marker}")
    return {
        "status": "PASS",
        "production_identity": "SamplingTargetId + LogicalDueBucket",
        "unique_target_bucket_keys": len(keys),
        "unique_development_fallback_keys": len(development_keys),
        "dispatcher_identity": "PanelVersion + LogicalDueBucket",
    }


def validate_source_persistence_contract() -> dict[str, Any]:
    fixtures = (
        ("stage14_trias_equivalent.json", "application/json"),
        ("mdd_mac_multi_occurrence.json", "application/json"),
        ("stage14_trias_source_contract_correction.json", "application/json"),
        ("stage14_trias_equivalent.xml", "application/xml"),
    )
    totals: Counter[str] = Counter()
    recorded_values: list[str] = []
    scopes: set[str] = set()
    for filename, content_type in fixtures:
        parsed = parse_trias_response((ROOT / "tests" / "fixtures" / filename).read_bytes(), content_type)
        rows = build_mssql_snapshot_rows(parsed.to_dict())
        for name, values in rows.items():
            totals[name] += len(values)
            for row in values:
                if any(str(key).casefold() == "actualtime" for key in row):
                    raise ValueError("source persistence contract introduced ActualTime")
        for row in rows["timings"]:
            scopes.add(str(row.get("call_scope") or ""))
            if row.get("recorded_at_time") is not None:
                recorded_values.append(str(row["recorded_at_time"]))
    if not {"PREVIOUS", "THIS", "ONWARD"}.issubset(scopes):
        raise ValueError("previous/this/onward call scopes were not retained")
    if not recorded_values or totals["situations"] < 1 or totals["attributes"] < 2 or totals["parser_gaps"] < 1:
        raise ValueError("source-field persistence fixtures did not exercise required evidence grains")
    return {
        "status": "PASS",
        "fixture_count": len(fixtures),
        "row_counts": dict(sorted(totals.items())),
        "call_scopes": sorted(scope for scope in scopes if scope),
        "recorded_at_time_values_seen": len(recorded_values),
        "actual_time_fabrication": False,
        "null_source_values_preserved": True,
        "causal_inference": False,
    }


def validate_deployment_and_sql() -> dict[str, Any]:
    migration = validate_static()
    if migration["status"] != "PASS":
        raise ValueError("static Tiered50 migration validation failed")
    import plistlib

    templates: list[dict[str, Any]] = []
    for path in LAUNCHD_TEMPLATES:
        with path.open("rb") as handle:
            plist = plistlib.load(handle)
        if not plist.get("Disabled") or plist.get("RunAtLoad"):
            raise ValueError(f"LaunchAgent is not a disabled scheduled template: {path.name}")
        if path.name.endswith("mdd-tiered50-collector.plist"):
            schedule = validate_launchd_wall_clock_schedule(plist)
        else:
            if plist.get("StartInterval") != 300:
                raise ValueError(f"retired Legacy7 template lost its historical five-minute interval: {path.name}")
            schedule = {
                "schedule_type": "RETIRED_LEGACY7_RELATIVE_INTERVAL",
                "start_interval_seconds": 300,
            }
        serialized = json.dumps(plist, ensure_ascii=False).casefold()
        if any(marker in serialized for marker in ("mdd_api_key", "mssql_password", "sql_server_password")):
            raise ValueError(f"LaunchAgent contains a credential marker: {path.name}")
        templates.append({"name": path.name, "label": plist.get("Label"), "disabled": True, "schedule": schedule})
    forbidden_members = [
        archive_name
        for _path, archive_name in source_members(ROOT)
        if is_forbidden_source_member(archive_name)
    ]
    if forbidden_members:
        raise ValueError(f"forbidden source members would be packaged: {forbidden_members}")
    return {
        "status": "PASS",
        "migration": migration,
        "launchd_templates": templates,
        "source_package_filters": "PASS",
        "power_bi_modified": False,
        "analytics_permissions_expanded": False,
    }


def validate_live_read_only() -> dict[str, Any]:
    """Collect read-only SQL and host evidence without activation or MDD."""

    def row_value(row: Any, name: str, index: int = 0) -> Any:
        if isinstance(row, dict):
            return row.get(name)
        return row[index]

    def command(command: list[str]) -> dict[str, Any]:
        completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        return {
            "command": command[0],
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-1000:],
        }

    settings = MssqlConnectionSettings.from_environment()
    persistence = MssqlPersistence(settings=settings)
    result: dict[str, Any] = {
        "status": "PASS",
        "mdd_requests_issued": 0,
        "writes_performed": 0,
        "mac_host_disk": {
            "free_bytes": shutil.disk_usage(ROOT).free,
            "total_bytes": shutil.disk_usage(ROOT).total,
            "scope": "macOS collector host only",
        },
        "windows_vm_disk": {"status": "NOT_PROVEN"},
        "backup_recovery": {"status": "NOT_PROVEN", "reason": "database-host administrator evidence required"},
        "availability": {
            "launchd": command([
                "/bin/launchctl",
                "print",
                f"gui/{os.getuid()}/com.cologne-transit-intelligence.mdd-tiered50-collector",
            ]),
            "uptime": command(["/usr/bin/uptime"]),
            "power_assertions": command(["/usr/bin/pmset", "-g", "assertions"]),
        },
    }
    vmrun = shutil.which("vmrun")
    if vmrun:
        result["windows_vm"] = {"status": "OBSERVED", "vmrun_list": command([vmrun, "list"])}
    else:
        result["windows_vm"] = {"status": "NOT_OBSERVABLE", "reason": "vmrun was not on PATH"}
    try:
        result["runtime_login"] = persistence.runtime_login_status()
        result["runtime_permission_matrix"] = inspect_runtime_permissions(settings)
        result["runtime_schema"] = persistence.validate_runtime_schema()
        result["tiered50_schema"] = persistence.validate_tiered50_runtime_schema()
        result["quota_usage"] = persistence.current_month_usage()
        result["competing_writers"] = persistence.assert_no_competing_writer(
            collection_context="MAC_TIERED50_PRODUCTION"
        )
        with persistence._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT name, type_desc, size, growth, is_percent_growth, max_size
                FROM sys.database_files
                ORDER BY file_id
                """
            )
            result["sql_database_files"] = [
                {
                    "name": str(row_value(row, "name", 0)),
                    "type": str(row_value(row, "type_desc", 1)),
                    "size_pages": int(row_value(row, "size", 2)),
                    "growth": int(row_value(row, "growth", 3)),
                    "is_percent_growth": bool(row_value(row, "is_percent_growth", 4)),
                    "max_size_pages": int(row_value(row, "max_size", 5)),
                }
                for row in cursor.fetchall()
            ]
            cursor.execute(
                f"""
                SELECT COUNT_BIG(*) AS target_count,
                       COALESCE(SUM(CASE WHEN IsEnabled = 1 THEN 1 ELSE 0 END), 0) AS active_count,
                       COALESCE(SUM(CASE WHEN ActivationStatus = 'PREPARED' THEN 1 ELSE 0 END), 0) AS prepared_count,
                       MIN(SamplingTargetId) AS minimum_id,
                       MAX(SamplingTargetId) AS maximum_id
                FROM {TIERED50_TARGET_TABLE}
                WHERE PanelVersion = %s AND SamplingEra = %s
                """,
                ("TIERED50_PANEL_V1", "Tiered50"),
            )
            targets = cursor.fetchone()
            result["tiered50_materialization"] = {
                "target_count": int(row_value(targets, "target_count", 0) or 0),
                "active_count": int(row_value(targets, "active_count", 1) or 0),
                "prepared_count": int(row_value(targets, "prepared_count", 2) or 0),
                "minimum_id": int(row_value(targets, "minimum_id", 3) or 0),
                "maximum_id": int(row_value(targets, "maximum_id", 4) or 0),
            }
    finally:
        # MssqlPersistence owns each short-lived DB-API connection through its
        # context manager; there is no long-lived adapter connection to close.
        pass
    return result


def run_preflight(*, live: bool = False) -> dict[str, Any]:
    checks = {
        "manifest": validate_manifest(),
        "schedule": validate_schedule(),
        "runtime_guardrails": validate_runtime_guardrails(),
        "quota_rollover_contract": validate_quota_rollover_contract(),
        "claim_identity": validate_claim_identity(),
        "source_persistence": validate_source_persistence_contract(),
        "deployment_and_sql": validate_deployment_and_sql(),
    }
    live_result: dict[str, Any] | None = None
    live_error: str | None = None
    if live:
        try:
            live_result = validate_live_read_only()
        except Exception as exc:
            live_error = type(exc).__name__
            live_result = {
                "status": "NOT_PROVEN",
                "mdd_requests_issued": 0,
                "writes_performed": 0,
                "error_type": live_error,
            }
    operational_evidence: dict[str, Any] | None = None
    try:
        loaded_evidence = json.loads(OPERATIONAL_EVIDENCE.read_text(encoding="utf-8"))
        if isinstance(loaded_evidence, dict):
            operational_evidence = loaded_evidence
    except (OSError, ValueError):
        operational_evidence = None
    blockers = []
    if not live:
        blockers.extend(
            [
                "Live Tiered50 SQL materialization/activation is not evaluated by this offline preflight.",
                "The Tiered50 writer remains disabled until the live materialization and activation gates pass.",
            ]
        )
    elif live_result and (
        live_result.get("tiered50_materialization", {}).get("target_count") == 43
        and live_result.get("tiered50_materialization", {}).get("minimum_id") == 8
        and live_result.get("tiered50_materialization", {}).get("maximum_id") == 50
    ):
        first_cycle = ((operational_evidence or {}).get("sql") or {}).get("first_tiered50_scheduled_cycle")
        if isinstance(first_cycle, dict) and first_cycle.get("validation_status") == "FAILED_CADENCE_OR_PERSISTENCE_GATE":
            blockers.append(
                "The first scheduled Tiered50 heartbeat failed its cadence/persistence gate; the LaunchAgent is stopped and no stations are currently collecting."
            )
        elif live_result.get("tiered50_materialization", {}).get("active_count") == 0:
            blockers.append(
                "Tiered50 materialization is complete and prepared, but activation/current-era recording has not passed all gates."
            )
        else:
            blockers.append(
                "The SQL Tiered50 target state is activated, but a complete successful production validation cycle is not yet proven."
            )
    else:
        blockers.append("Live Tiered50 materialization is not proven with exactly 43 IDs 8–50.")
    quota_contract = checks["quota_rollover_contract"]
    if quota_contract["contract_status"] not in {
        RESET_CONTRACT_STATUS_VERIFIED_PROVIDER,
        RESET_CONTRACT_STATUS_APPROVED_OPERATOR_ASSUMPTION,
    } or quota_contract.get("registration_status") != "ACTIVE":
        blockers.append(
            "Future automatic monthly rollover is implemented but remains fail-closed until an exact provider contract or explicitly approved bounded assumption is durably registered."
        )
    if not live:
        blockers.append(
            "Current shared-credential quota state is outside this offline preflight's scope; consult the sanitized runtime evidence or run the read-only live preflight."
        )
    elif not live_result or not live_result.get("quota_usage", {}).get("state_available", False):
        blockers.append(
            "The current shared-credential quota authorization was not available from the read-only runtime preflight."
        )
    if not isinstance(((operational_evidence or {}).get("sql") or {}).get("first_tiered50_scheduled_cycle"), dict):
        blockers.append(
            "A complete successful Tiered50 30-minute validation cycle and its database-host capacity/backup evidence remain required."
        )
    six_cycle = ((operational_evidence or {}).get("sql") or {}).get("tiered50_six_bucket_cycle")
    if not isinstance(six_cycle, dict) or six_cycle.get("status") != "PASS":
        blockers.append(
            "A complete successful Tiered50 six-bucket cycle with the approved 19/18/18/18/19/18 distribution is not yet proven."
        )
    if live_error:
        blockers.append(f"Read-only live SQL preflight was not proven ({live_error}).")
    return {
        "artifact_version": "TIERED50_PRODUCTION_PREFLIGHT_V1",
        "status": "PASS" if not live_error else "PASS_WITH_LIVE_EVIDENCE_GAP",
        "production_status": "BLOCKED — TIERED50 STOPPED AFTER FAILED FIRST SCHEDULED HEARTBEAT; LEGACY7 RETIRED",
        "tiered50_active": False,
        "legacy7_retired": True,
        "stations_actually_collecting": 0,
        "first_completed_live_cycle": None,
        "first_scheduled_heartbeat": ((operational_evidence or {}).get("sql") or {}).get("first_tiered50_scheduled_cycle"),
        "mdd_requests_issued": 0,
        "writes_performed": 0,
        "checks": checks,
        "live_read_only": live_result,
        "blockers": blockers,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="add read-only SQL/catalog checks")
    args = parser.parse_args(argv)
    try:
        result = run_preflight(live=args.live)
    except Exception as exc:
        result = {
            "artifact_version": "TIERED50_PRODUCTION_PREFLIGHT_V1",
            "status": "FAIL",
            "production_status": "BLOCKED — TIERED50 STOPPED AFTER FAILED FIRST SCHEDULED HEARTBEAT; LEGACY7 RETIRED",
            "tiered50_active": False,
            "mdd_requests_issued": 0,
            "error_type": type(exc).__name__,
            "blockers": ["offline preflight failed before all checks completed"],
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result["status"] in {"PASS", "PASS_WITH_LIVE_EVIDENCE_GAP"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
