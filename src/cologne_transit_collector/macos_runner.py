"""Fail-closed future runner for one direct Microsoft-database heartbeat.

This module is intentionally not enabled by the repository's scheduler
artifact.  It can only proceed after the operator has separately confirmed
that the prior collector is retired, approved the cutover, selected the
existing seven-target scope, and supplied the inherited MDD credential.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .collector import Collector, LiveModeBlocked
from .configuration import CollectorSettings
from .domain import (
    MAC_SQL_COLLECTION_CONTEXT,
    MAC_SQL_CUTOVER_APPROVED_ENV,
    MAC_SQL_ENABLED_ENV,
    MAC_SQL_REQUIRED_RUNTIME_STATE,
    MAC_SQL_REQUIRED_TARGET_SCOPE,
    MAC_SQL_REQUIRED_VALIDATION_POLICY,
    MAC_SQL_RUNTIME_STATE_ENV,
    MAC_SQL_TARGET_SCOPE_ENV,
    MAC_SQL_VALIDATION_POLICY_ENV,
    SamplingEra,
)
from .manifest import load_default_manifest
from .mssql_persistence import (
    MssqlConnectionSettings,
    MssqlDependencyError,
    MssqlPersistence,
    MssqlSafetyError,
    MssqlSchemaError,
)
from .quota import QuotaGuard
from .runtime_configuration import (
    EXPECTED_SQL_DATABASE,
    SecretRequired,
    merge_non_secret_runtime_values,
    resolve_required_secrets,
)
from .transport import LiveMddClient, validate_api_key_format


def _parse_utc(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("--at must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def require_cutover_gate(environ: dict[str, str] | None = None) -> None:
    """Reject every run until the human-controlled cutover gates are set."""

    values = os.environ if environ is None else environ
    if values.get(MAC_SQL_ENABLED_ENV) != "1":
        raise LiveModeBlocked("direct Mac collection is disabled; no network request performed")
    if values.get(MAC_SQL_RUNTIME_STATE_ENV) != MAC_SQL_REQUIRED_RUNTIME_STATE:
        raise LiveModeBlocked("current Windows collector runtime is not confirmed retired; no network request performed")
    if values.get(MAC_SQL_CUTOVER_APPROVED_ENV) != "1":
        raise LiveModeBlocked("explicit cutover approval is missing; no network request performed")
    if values.get(MAC_SQL_TARGET_SCOPE_ENV) != MAC_SQL_REQUIRED_TARGET_SCOPE:
        raise LiveModeBlocked("explicit existing-seven-target scope approval is missing; no network request performed")
    if values.get(MAC_SQL_VALIDATION_POLICY_ENV) != MAC_SQL_REQUIRED_VALIDATION_POLICY:
        raise LiveModeBlocked(
            "cutover requires offline SQLite reconciliation; no manual Heumarkt validation is a runtime gate"
        )
    if validate_api_key_format(values.get("MDD_API_KEY")) != "VALID":
        raise LiveModeBlocked("MDD_API_KEY is missing or invalid; no network request performed")


def _require_nonsecret_cutover_gate(environ: dict[str, str]) -> None:
    """Check gates before touching Keychain secrets."""

    if environ.get(MAC_SQL_ENABLED_ENV) != "1":
        raise LiveModeBlocked("direct Mac collection is disabled; no network request performed")
    if environ.get(MAC_SQL_RUNTIME_STATE_ENV) != MAC_SQL_REQUIRED_RUNTIME_STATE:
        raise LiveModeBlocked("current Windows collector runtime is not confirmed retired; no network request performed")
    if environ.get(MAC_SQL_CUTOVER_APPROVED_ENV) != "1":
        raise LiveModeBlocked("explicit cutover approval is missing; no network request performed")
    if environ.get(MAC_SQL_TARGET_SCOPE_ENV) != MAC_SQL_REQUIRED_TARGET_SCOPE:
        raise LiveModeBlocked("explicit existing-seven-target scope approval is missing; no network request performed")
    if environ.get(MAC_SQL_VALIDATION_POLICY_ENV) != MAC_SQL_REQUIRED_VALIDATION_POLICY:
        raise LiveModeBlocked(
            "cutover requires offline SQLite reconciliation; no manual Heumarkt validation is a runtime gate"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one explicitly approved direct-database MDD heartbeat.")
    parser.add_argument("--at", dest="at_utc", help="UTC ISO-8601 time for deterministic validation")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configured_root = os.environ.get("COLLECTOR_RUNTIME_ROOT")
    root = Path(configured_root).expanduser().resolve() if configured_root else Path(__file__).resolve().parents[2]
    try:
        base_configuration = merge_non_secret_runtime_values()
        _require_nonsecret_cutover_gate(dict(base_configuration.values))
        configuration = resolve_required_secrets(base_configuration)
        runtime_values = dict(configuration.values)
        require_cutover_gate(runtime_values)
        mssql_settings = MssqlConnectionSettings.from_environment(runtime_values)
        if mssql_settings.database != EXPECTED_SQL_DATABASE:
            raise MssqlSafetyError(f"selected target database must be {EXPECTED_SQL_DATABASE}")
        at_utc = _parse_utc(args.at_utc) or datetime.now(timezone.utc)
        base_manifest = load_default_manifest(root)
        persistence = MssqlPersistence(
            settings=mssql_settings,
            raw_evidence_directory=runtime_values.get("MSSQL_RAW_EVIDENCE_DIR"),
            collection_context=MAC_SQL_COLLECTION_CONTEXT,
        )
        login_status = persistence.runtime_login_status()
        if str(login_status.get("login_name", "")).casefold() in {"", "sa"}:
            raise MssqlSafetyError("Mac Legacy7 runtime requires the dedicated non-sa SQL login")
        if any(
            bool(login_status.get(name))
            for name in (
                "is_sysadmin",
                "is_serveradmin",
                "is_securityadmin",
                "is_db_owner",
                "is_db_datareader",
                "is_db_datawriter",
            )
        ):
            raise MssqlSafetyError("Mac Legacy7 runtime login has a prohibited broad privilege")
        schema = persistence.validate_runtime_schema()
        approved_targets = persistence.load_existing_sampling_targets()
        selected_target = persistence.select_existing_legacy7_target(
            at_utc=at_utc,
            targets=approved_targets,
        )
        targets = (selected_target,)
        manifest = replace(
            base_manifest,
            panel_version="MDD_MAC_LEGACY7_EXISTING_V1",
            status="PREPARED_EXISTING_LEGACY7",
            sampling_era=SamplingEra.LEGACY7,
            activation_status="EXPLICIT_CUTOVER_APPROVED",
            targets=targets,
        )
        settings = CollectorSettings.for_repository(root, environ=runtime_values)
        collector = Collector(
            manifest=manifest,
            persistence=persistence,
            mdd_client=LiveMddClient(
                settings.endpoint,
                requestor_ref=settings.requestor_ref,
                api_key=runtime_values["MDD_API_KEY"],
                enabled=True,
            ),
            quota_guard=QuotaGuard(persistence, reservation_scope="LIVE_PROVIDER"),
            safety_margin_seconds=settings.safety_margin_seconds,
        )
        previous_handlers: dict[int, object] = {}
        for signal_name in ("SIGINT", "SIGTERM"):
            signal_number = getattr(signal, signal_name, None)
            if signal_number is not None:
                previous_handlers[signal_number] = signal.getsignal(signal_number)
                signal.signal(signal_number, lambda _signum, _frame: collector.request_shutdown())
        try:
            result = collector.run_once(mode="mac-sql", allow_live=True, at_utc=at_utc)
        finally:
            for signal_number, handler in previous_handlers.items():
                signal.signal(signal_number, handler)
        print(
            json.dumps(
                {
                    "status": result.status,
                    "mode": "mac-sql",
                    "collection_context": MAC_SQL_COLLECTION_CONTEXT,
                    "target_count": len(targets),
                    "approved_target_scope_count": len(approved_targets),
                    "selection_policy_version": selected_target.selection_policy_version,
                    "sampling_slot": selected_target.selected_sampling_slot,
                    "sampling_target_id": selected_target.legacy_sampling_target_id,
                    "stop_point_ref": selected_target.source_parent_station_id,
                    "station_name": selected_target.station_name,
                    "number_of_results": selected_target.number_of_results,
                    "logical_due_bucket": int(at_utc.timestamp() // 300),
                    "sql_login_status": login_status,
                    "schema": schema,
                    "result": result.to_dict(),
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        )
        return 0 if result.status in {"COMPLETE", "DUPLICATE_NO_WORK"} else 1
    except SecretRequired as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (LiveModeBlocked, MssqlDependencyError, MssqlSafetyError, MssqlSchemaError) as exc:
        print(
            json.dumps(
                {"status": "BLOCKED", "reason": str(exc), "simulated_transport_calls": 0, "live_network_calls": 0},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "ERROR", "reason": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    except Exception as exc:
        # Do not echo DB-API/provider exception text; it may contain endpoint
        # or credential-adjacent diagnostics.  The audit tables receive the
        # detailed sanitized outcome once a run has been opened.
        print(json.dumps({"status": "ERROR", "error_type": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
