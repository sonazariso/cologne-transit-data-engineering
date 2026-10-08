#!/usr/bin/env python3
"""Validate and optionally register the future MDD quota reset contract.

This is an administrator-only workflow.  It intentionally refuses to create
the contract from the current ``UNVERIFIED`` evidence artifact.  Once the
provider supplies an exact reset timezone/boundary, the evidence artifact can
be updated and this command can be invoked with ``--apply --approve``.  The
normal collector then performs all later period creation automatically.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.quota_rollover import (  # noqa: E402
    QuotaRolloverError,
    build_reset_contract,
)
from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    MssqlPersistence,
)
from cologne_transit_collector.quota_baseline import load_evidence_file  # noqa: E402


EVIDENCE = ROOT / "docs" / "operational" / "mdd-quota-reset-contract.json"


def _utc(value: str | None, *, field: str) -> datetime:
    if not value:
        raise QuotaRolloverError(f"{field} is required")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise QuotaRolloverError(f"{field} must include an explicit timezone")
    return parsed.astimezone(timezone.utc)


def run(
    *,
    evidence_path: Path = EVIDENCE,
    contract_key: str | None = None,
    reset_timezone: str | None = None,
    effective_from: datetime | None = None,
    approved_at: datetime | None = None,
    activation_delay_seconds: int = 1800,
    supported_reset_timezones: list[str] | None = None,
    approve_bounded_assumption: bool = False,
    apply: bool = False,
    approve: bool = False,
) -> dict[str, object]:
    if apply and not approve:
        return {
            "status": "BLOCKED",
            "reason": "reset contract registration requires both --apply and explicit --approve",
            "sql_writes_performed": 0,
        }
    evidence = load_evidence_file(evidence_path)
    if not apply:
        return {
            "status": "PASS" if evidence.get("reset_contract_status") == "VERIFIED" else "AWAITING_PROVIDER_RESET_EVIDENCE",
            "reset_contract_status": evidence.get("reset_contract_status"),
            "automatic_rollover": evidence.get("automatic_rollover_implementation"),
            "unresolved_dependency": evidence.get("unresolved_dependency"),
            "sql_writes_performed": 0,
        }
    if not contract_key or effective_from is None or approved_at is None:
        return {
            "status": "BLOCKED",
            "reason": "--contract-key, --effective-from, and --approved-at are required for apply",
            "sql_writes_performed": 0,
        }
    contract = build_reset_contract(
        evidence=evidence,
        contract_key=contract_key,
        reset_timezone=reset_timezone or "",
        effective_from_utc=effective_from,
        approved_at_utc=approved_at,
        activation_delay_seconds=activation_delay_seconds,
        supported_reset_timezones=supported_reset_timezones,
        approve_bounded_assumption=approve_bounded_assumption,
    )
    persistence = MssqlPersistence(settings=MssqlConnectionSettings.from_environment())
    result = dict(persistence.register_quota_reset_contract(contract=contract))
    return {
        "status": "PASS",
        "reset_contract_status": "VERIFIED",
        "contract_key": contract_key,
        "reset_timezone": reset_timezone,
        "supported_reset_timezones": supported_reset_timezones,
        "effective_from_utc": contract["effective_from_utc"],
        "activation_delay_seconds": activation_delay_seconds,
        "application": result,
        "sql_writes_performed": int(result.get("writes_performed", 0) or 0),
        "mdd_requests_issued": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    parser.add_argument("--contract-key")
    parser.add_argument("--timezone", dest="reset_timezone")
    parser.add_argument(
        "--supported-timezone",
        dest="supported_reset_timezones",
        action="append",
        help="one explicitly bounded IANA timezone for the approved conservative policy",
    )
    parser.add_argument("--effective-from")
    parser.add_argument("--approved-at")
    parser.add_argument("--activation-delay-seconds", type=int, default=1800)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--approve-bounded-assumption", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run(
            evidence_path=args.evidence.expanduser().resolve(),
            contract_key=args.contract_key,
            reset_timezone=args.reset_timezone,
            effective_from=_utc(args.effective_from, field="--effective-from") if args.effective_from else None,
            approved_at=_utc(args.approved_at, field="--approved-at") if args.approved_at else None,
            activation_delay_seconds=args.activation_delay_seconds,
            supported_reset_timezones=args.supported_reset_timezones,
            approve_bounded_assumption=args.approve_bounded_assumption,
            apply=args.apply,
            approve=args.approve,
        )
    except Exception as exc:
        result = {
            "status": "BLOCKED",
            "error_type": type(exc).__name__,
            "reason": str(exc)[:400],
            "sql_writes_performed": 0,
            "mdd_requests_issued": 0,
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result.get("status") in {"PASS", "AWAITING_PROVIDER_RESET_EVIDENCE"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
