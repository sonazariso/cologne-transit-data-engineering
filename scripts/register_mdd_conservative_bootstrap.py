#!/usr/bin/env python3
"""Validate and optionally register the bounded October quota bootstrap.

The default command is a read-only reconciliation.  SQL registration requires
both ``--apply`` and ``--approve`` plus an explicit UTC approval timestamp.
The command uses the administrator connection only; it never enables launchd
and never issues an MDD request.
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

from cologne_transit_collector.conservative_bootstrap import (  # noqa: E402
    BOOTSTRAP_APPROVAL_STATEMENT,
    BOOTSTRAP_AUTHORIZATION_MODE,
    ConservativeBootstrapError,
    build_conservative_bootstrap_authorization,
)
from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    MssqlPersistence,
)
from cologne_transit_collector.quota_baseline import load_evidence_file  # noqa: E402


PROPOSAL = ROOT / "docs" / "operational" / "mdd-october-conservative-bootstrap-proposal.json"
DASHBOARD = ROOT / "docs" / "operational" / "mdd-october-monitoring-dashboard-evidence.json"


def _utc(value: str | None, *, field: str) -> datetime:
    if not value:
        raise ConservativeBootstrapError(f"{field} is required")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ConservativeBootstrapError(f"{field} must include an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _summary(
    authorization: Mapping[str, Any],
    *,
    local_usage: Mapping[str, Any],
    application: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "PASS",
        "authorization_mode": authorization["authorization_mode"],
        "accounting_model": authorization["accounting_model"],
        "month_key": authorization["month_key"],
        "provider_dashboard_requests": authorization["screenshot_cumulative_requests"],
        "provider_monthly_limit": authorization["screenshot_monthly_limit"],
        "provider_measurement_cutoff": "NOT_ESTABLISHED",
        "provider_reset_timezone": "NOT_ESTABLISHED",
        "provider_scope_verified": authorization["provider_scope_verified"],
        "operator_approval_statement": BOOTSTRAP_APPROVAL_STATEMENT,
        "operator_approval_statement_hash": authorization["operator_approval_statement_hash"],
        "operator_approval_at_utc": authorization["operator_approval_at_utc"],
        "valid_from_utc": authorization["valid_from_utc"],
        "stop_at_utc": authorization["stop_at_utc"],
        "risk": (
            "The dashboard cutoff, freshness, reset timezone, and provider counter scope remain unverified; "
            "the 554 value may overlap local permits, which are intentionally overcounted for safety."
        ),
        "conservative_accounting": {
            "confirmed_local_attempts": authorization["confirmed_local_attempts"],
            "conservative_unknown_local_attempts": authorization["conservative_unknown_local_attempts"],
            "pending_local_reservations": authorization["pending_local_reservations"],
            "released_no_network_excluded": int(local_usage.get("released_no_network", 0) or 0),
            "accounted_total_at_authorization": authorization["accounted_total_at_authorization"],
            "remaining_headroom": authorization["remaining_headroom"],
            "local_usage_evidence": dict(local_usage),
        },
        "stop_conditions": authorization["stop_conditions"],
        "sql_writes_performed": int((application or {}).get("writes_performed", 0) or 0),
        "mdd_requests_issued": 0,
        "application": dict(application or {"status": "NOT_APPLIED"}),
    }


def run(
    *,
    proposal_path: Path = PROPOSAL,
    dashboard_path: Path = DASHBOARD,
    approved_at: datetime | None = None,
    apply: bool = False,
    approve: bool = False,
    authorization_key: str | None = None,
) -> dict[str, Any]:
    if apply and not approve:
        return {
            "status": "BLOCKED",
            "authorization_mode": BOOTSTRAP_AUTHORIZATION_MODE,
            "reason": "SQL bootstrap registration requires both --apply and explicit --approve",
            "sql_writes_performed": 0,
            "mdd_requests_issued": 0,
        }
    if apply and approved_at is None:
        return {
            "status": "BLOCKED",
            "authorization_mode": BOOTSTRAP_AUTHORIZATION_MODE,
            "reason": "--approved-at is required for a production bootstrap write",
            "sql_writes_performed": 0,
            "mdd_requests_issued": 0,
        }

    now = datetime.now(timezone.utc)
    approval_time = approved_at or now
    proposal = load_evidence_file(proposal_path)
    dashboard = load_evidence_file(dashboard_path)
    settings = MssqlConnectionSettings.from_environment()
    persistence = MssqlPersistence(settings=settings)
    local_usage = persistence.read_conservative_bootstrap_inputs(observed_through_utc=approval_time)
    authorization = build_conservative_bootstrap_authorization(
        proposal=proposal,
        dashboard=dashboard,
        local_usage=local_usage,
        approved_at_utc=approval_time,
        now_utc=now,
        authorization_key=authorization_key,
    )
    result = _summary(authorization, local_usage=local_usage)
    result["mode"] = "APPLY" if apply else "DRY_RUN"
    result["administrator_context"] = "NOT_REQUIRED_FOR_DRY_RUN"
    if apply:
        result["administrator_context"] = dict(persistence.quota_administrator_context())
        result["application"] = dict(
            persistence.register_conservative_october_bootstrap(
                authorization=authorization,
                local_usage=local_usage,
            )
        )
        result["sql_writes_performed"] = int(result["application"].get("writes_performed", 0) or 0)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal", type=Path, default=PROPOSAL)
    parser.add_argument("--dashboard", type=Path, default=DASHBOARD)
    parser.add_argument("--approved-at", help="explicit actual operator approval timestamp in ISO-8601 UTC")
    parser.add_argument("--authorization-key", help="optional idempotent authorization key")
    parser.add_argument("--apply", action="store_true", help="register the validated bootstrap in SQL")
    parser.add_argument("--approve", action="store_true", help="explicitly approve the SQL registration")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run(
            proposal_path=args.proposal.expanduser().resolve(),
            dashboard_path=args.dashboard.expanduser().resolve(),
            approved_at=_utc(args.approved_at, field="--approved-at") if args.approved_at else None,
            apply=args.apply,
            approve=args.approve,
            authorization_key=args.authorization_key,
        )
    except (OSError, ValueError, ConservativeBootstrapError) as exc:
        result = {
            "status": "BLOCKED",
            "authorization_mode": BOOTSTRAP_AUTHORIZATION_MODE,
            "error_type": type(exc).__name__,
            "reason": str(exc)[:400],
            "sql_writes_performed": 0,
            "mdd_requests_issued": 0,
        }
    except Exception as exc:
        result = {
            "status": "BLOCKED",
            "authorization_mode": BOOTSTRAP_AUTHORIZATION_MODE,
            "error_type": type(exc).__name__,
            "sql_writes_performed": 0,
            "mdd_requests_issued": 0,
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result.get("status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
