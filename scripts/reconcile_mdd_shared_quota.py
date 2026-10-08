#!/usr/bin/env python3
"""Reconcile and optionally register the provider-wide MDD billing-period quota.

The default operation is read-only.  A provider usage report/confirmation is
required as a JSON evidence document; missing external usage is never treated
as zero.  ``--apply --approve`` is the only path that writes SQL, and that path
requires a separately authorized administrative connection.  Credentials are
resolved through the normal environment/Keychain configuration and are never
accepted as command-line arguments or printed.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    MssqlPersistence,
)
from cologne_transit_collector.quota_baseline import (  # noqa: E402
    QuotaBaselineError,
    load_evidence_file,
    reconcile_provider_evidence,
)


def _utc(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise QuotaBaselineError("local_usage_included_through_utc must include an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _summary(reconciliation: Mapping[str, Any], *, local_usage: Mapping[str, Any]) -> dict[str, Any]:
    """Return the review-safe portion of a reconciliation."""

    return {
        "month_key": reconciliation["month_key"],
        "accounting_status": reconciliation["accounting_status"],
        "accounting_model": reconciliation["accounting_model"],
        "source_description": reconciliation["source_description"],
        "source_reference": reconciliation["source_reference"],
        "source_evidence_hash": reconciliation["source_evidence_hash"],
        "reconciliation_key": reconciliation["reconciliation_key"],
        "provider_reported_cumulative_attempts": reconciliation["provider_reported_cumulative_attempts"],
        "provider_reported_confirmed_attempts": reconciliation["provider_reported_confirmed_attempts"],
        "provider_reported_unknown_attempts": reconciliation["provider_reported_unknown_attempts"],
        "unexplained_discrepancy_attempts": reconciliation["unexplained_discrepancy_attempts"],
        "provider_includes_local_usage": reconciliation["provider_includes_local_usage"],
        "confirmed_provider_attempts_registered": reconciliation["confirmed_provider_attempts"],
        "conservative_unknown_attempts_registered": reconciliation["conservative_unknown_attempts"],
        "explicit_shared_consumer_reserved_attempts_registered": reconciliation[
            "explicit_shared_consumer_reserved_attempts"
        ],
        "local_usage": reconciliation["local_usage"],
        "local_sql_evidence": _local_evidence_summary(local_usage),
        "local_usage_boundary_rule": reconciliation["local_usage_boundary_rule"],
        "baseline_component_total": reconciliation["baseline_component_total"],
        "post_provider_cutoff_local_usage_registered": reconciliation[
            "post_provider_cutoff_local_usage_registered"
        ],
        "baseline_as_of_utc": reconciliation["baseline_as_of_utc"],
        "local_usage_included_through_utc": reconciliation["local_usage_included_through_utc"],
        "provider_measurement_cutoff_utc": reconciliation["provider_measurement_cutoff_utc"],
        "stale_after_utc": reconciliation["stale_after_utc"],
        "provider_period_timezone": reconciliation["provider_period_timezone"],
        "provider_period_start_utc": reconciliation["provider_period_start_utc"],
        "provider_period_end_utc": reconciliation["provider_period_end_utc"],
        "provider_period_selection": reconciliation.get("provider_period_selection", "EXPLICIT_PROVIDER_PERIOD"),
        "current_conservative_accounted_total": reconciliation["current_conservative_accounted_total"],
        "remaining_headroom": reconciliation["remaining_headroom"],
        "sql_writes_performed": 0,
        "mdd_requests_issued": 0,
    }


def _local_evidence_summary(local_usage: Mapping[str, Any]) -> dict[str, Any]:
    """Return SQL-local evidence without turning it into provider-wide usage."""

    return {
        "permit_counts": dict(local_usage.get("permit_counts") or {}),
        "http_attempts": dict(local_usage.get("http_attempts") or {}),
        "collector_activity": list(local_usage.get("collector_activity") or []),
        "windows_collector_activity": dict(local_usage.get("windows_collector_activity") or {}),
        "quota_audit_activity": list(local_usage.get("quota_audit_activity") or []),
        "imported_research_evidence": list(local_usage.get("imported_research_evidence") or []),
        "previous_reconciliations": list(local_usage.get("previous_reconciliations") or []),
        "local_usage_included_through_utc": local_usage.get("local_usage_included_through_utc"),
    }


def run(*, evidence_path: Path | None, apply: bool = False, approve: bool = False) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    if evidence_path is None:
        result: dict[str, Any] = {
            "status": "BLOCKED",
            "accounting_status": "NOT_REGISTERED",
            "reason": "authoritative provider-wide usage report or confirmation is required",
            "required_input": (
                "provider cumulative usage with confirmed/unknown components, explicit unexplained discrepancies, "
                "measurement cutoff, shared-consumer scope, operating-model evidence, and reset calendar"
            ),
            "sql_writes_performed": 0,
            "mdd_requests_issued": 0,
            "local_sql_evidence_status": "NOT_PROVEN",
            "local_sql_evidence_reason": (
                "provider billing period metadata is required before local SQL reconciliation; "
                "the UTC calendar month is not an authorization boundary"
            ),
        }
        return result
    if apply and not approve:
        return {
            "status": "BLOCKED",
            "accounting_status": "NOT_REGISTERED",
            "reason": "SQL baseline writes require both --apply and explicit --approve",
            "sql_writes_performed": 0,
            "mdd_requests_issued": 0,
        }

    document = load_evidence_file(evidence_path)
    provider = document.get("provider")
    if not isinstance(provider, Mapping):
        raise QuotaBaselineError("provider evidence must contain a provider object")
    local_cutoff = _utc(document.get("local_usage_included_through_utc"))
    provider_cutoff = _utc(provider.get("usage_through_utc", provider.get("measurement_cutoff_utc")))
    provider_period_start = _utc(provider.get("period_start_utc"))
    provider_period_end = _utc(provider.get("period_end_utc"))
    settings = MssqlConnectionSettings.from_environment()
    persistence = MssqlPersistence(settings=settings)
    local_usage = persistence.read_quota_reconciliation_inputs(
        month_key=str(document.get("month_key") or ""),
        local_usage_included_through_utc=local_cutoff,
        provider_measurement_cutoff_utc=provider_cutoff,
        provider_period_start_utc=provider_period_start,
        provider_period_end_utc=provider_period_end,
    )
    reconciliation = reconcile_provider_evidence(
        document,
        local_usage=local_usage,
        now_utc=now,
        require_current_month=True,
    )
    result = _summary(reconciliation, local_usage=local_usage)
    result["status"] = "PASS"
    result["mode"] = "APPLY" if apply else "DRY_RUN"
    result["administrator_context"] = "NOT_REQUIRED_FOR_DRY_RUN"
    if apply:
        # The persistence method repeats this check immediately before the
        # transaction, preventing the runtime login from acquiring admin write
        # rights through this tool.
        result["administrator_context"] = dict(persistence.quota_administrator_context())
        result["application"] = dict(
            persistence.register_shared_quota_baseline(
                reconciliation=reconciliation,
                local_usage=local_usage,
            )
        )
        result["sql_writes_performed"] = int(result["application"].get("writes_performed", 0) or 0)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence",
        type=Path,
        help="sanitized provider usage report/confirmation JSON; omit to remain blocked",
    )
    parser.add_argument("--apply", action="store_true", help="apply the validated baseline in one SQL transaction")
    parser.add_argument(
        "--approve",
        action="store_true",
        help="explicitly approve the SQL baseline write when used with --apply",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run(evidence_path=args.evidence, apply=args.apply, approve=args.approve)
    except (OSError, ValueError, QuotaBaselineError) as exc:
        result = {
            "status": "BLOCKED",
            "accounting_status": "NOT_REGISTERED",
            "error_type": type(exc).__name__,
            "reason": str(exc)[:300],
            "sql_writes_performed": 0,
            "mdd_requests_issued": 0,
        }
    except Exception as exc:
        # Do not expose driver text, connection strings, or evidence payloads.
        result = {
            "status": "BLOCKED",
            "accounting_status": "NOT_REGISTERED",
            "error_type": type(exc).__name__,
            "sql_writes_performed": 0,
            "mdd_requests_issued": 0,
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result.get("status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
