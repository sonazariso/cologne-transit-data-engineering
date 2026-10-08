"""Fail-closed Mac production runner for one SQL-backed heartbeat.

Legacy7 and Tiered50 are intentionally separate runtime modes. The tracked
LaunchAgent templates disable both modes by default; a production invocation
must pass the mode-specific human-controlled gates before credentials are
resolved or an MDD client can be used.
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
    MAC_SQL_MODE_ENV,
    MAC_SQL_REQUIRED_RUNTIME_STATE,
    MAC_SQL_REQUIRED_TARGET_SCOPE,
    MAC_SQL_REQUIRED_VALIDATION_POLICY,
    MAC_SQL_RUNTIME_STATE_ENV,
    MAC_SQL_TARGET_SCOPE_ENV,
    MAC_SQL_VALIDATION_POLICY_ENV,
    MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT,
    MAC_TIERED50_CUTOVER_APPROVED_ENV,
    MAC_TIERED50_ENABLED_ENV,
    MAC_TIERED50_REQUIRED_TARGET_SCOPE,
    MAC_TIERED50_REQUIRED_VALIDATION_POLICY,
    MAC_TIERED50_TARGET_SCOPE_ENV,
    MAC_TIERED50_VALIDATION_POLICY_ENV,
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
from .schedule import schedule_reconciliation
from .transport import LiveMddClient, validate_api_key_format


LEGACY7_MODE = "legacy7"
TIERED50_MODE = "tiered50"


def _parse_utc(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("--at must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def require_cutover_gate(environ: dict[str, str] | None = None) -> None:
    """Reject Legacy7 network collection until its existing gates pass."""

    values = os.environ if environ is None else environ
    if values.get(MAC_SQL_MODE_ENV) not in {None, "LEGACY7"}:
        raise LiveModeBlocked("Legacy7 runner mode does not match the configured runtime mode")
    if values.get(MAC_SQL_ENABLED_ENV) != "1":
        raise LiveModeBlocked("direct Mac collection is disabled; no network request performed")
    if values.get(MAC_SQL_RUNTIME_STATE_ENV) != MAC_SQL_REQUIRED_RUNTIME_STATE:
        raise LiveModeBlocked(
            "current Windows collector runtime is not confirmed retired; no network request performed"
        )
    if values.get(MAC_SQL_CUTOVER_APPROVED_ENV) != "1":
        raise LiveModeBlocked("explicit cutover approval is missing; no network request performed")
    if values.get(MAC_SQL_TARGET_SCOPE_ENV) != MAC_SQL_REQUIRED_TARGET_SCOPE:
        raise LiveModeBlocked(
            "explicit existing-seven-target scope approval is missing; no network request performed"
        )
    if values.get(MAC_SQL_VALIDATION_POLICY_ENV) != MAC_SQL_REQUIRED_VALIDATION_POLICY:
        raise LiveModeBlocked(
            "cutover requires offline SQLite reconciliation; no manual Heumarkt validation is a runtime gate"
        )
    if validate_api_key_format(values.get("MDD_API_KEY")) != "VALID":
        raise LiveModeBlocked("MDD_API_KEY is missing or invalid; no network request performed")


def require_tiered50_cutover_gate(environ: dict[str, str] | None = None) -> None:
    """Reject Tiered50 collection unless its independent gates pass."""

    values = os.environ if environ is None else environ
    if values.get(MAC_SQL_MODE_ENV) != "TIERED50":
        raise LiveModeBlocked("Tiered50 runner requires COLLECTOR_MAC_SQL_MODE=TIERED50")
    if values.get(MAC_TIERED50_ENABLED_ENV) != "1":
        raise LiveModeBlocked("Tiered50 collection is disabled; no network request performed")
    if values.get(MAC_SQL_RUNTIME_STATE_ENV) != MAC_SQL_REQUIRED_RUNTIME_STATE:
        raise LiveModeBlocked(
            "current Windows collector runtime is not confirmed retired; no network request performed"
        )
    if values.get(MAC_TIERED50_CUTOVER_APPROVED_ENV) != "1":
        raise LiveModeBlocked("explicit Tiered50 cutover approval is missing; no network request performed")
    if values.get(MAC_TIERED50_TARGET_SCOPE_ENV) != MAC_TIERED50_REQUIRED_TARGET_SCOPE:
        raise LiveModeBlocked("explicit approved Tiered50 target scope is missing; no network request performed")
    if values.get(MAC_TIERED50_VALIDATION_POLICY_ENV) != MAC_TIERED50_REQUIRED_VALIDATION_POLICY:
        raise LiveModeBlocked(
            "Tiered50 requires completed offline validation before live collection; no network request performed"
        )
    if validate_api_key_format(values.get("MDD_API_KEY")) != "VALID":
        raise LiveModeBlocked("MDD_API_KEY is missing or invalid; no network request performed")


def _require_nonsecret_cutover_gate(environ: dict[str, str], *, mode: str) -> None:
    """Check mode-specific non-secret gates before touching Keychain secrets."""

    if mode == LEGACY7_MODE:
        if environ.get(MAC_SQL_ENABLED_ENV) != "1":
            raise LiveModeBlocked("direct Mac collection is disabled; no network request performed")
        if environ.get(MAC_SQL_RUNTIME_STATE_ENV) != MAC_SQL_REQUIRED_RUNTIME_STATE:
            raise LiveModeBlocked(
                "current Windows collector runtime is not confirmed retired; no network request performed"
            )
        if environ.get(MAC_SQL_CUTOVER_APPROVED_ENV) != "1":
            raise LiveModeBlocked("explicit cutover approval is missing; no network request performed")
        if environ.get(MAC_SQL_TARGET_SCOPE_ENV) != MAC_SQL_REQUIRED_TARGET_SCOPE:
            raise LiveModeBlocked(
                "explicit existing-seven-target scope approval is missing; no network request performed"
            )
        if environ.get(MAC_SQL_VALIDATION_POLICY_ENV) != MAC_SQL_REQUIRED_VALIDATION_POLICY:
            raise LiveModeBlocked(
                "cutover requires offline SQLite reconciliation; no manual Heumarkt validation is a runtime gate"
            )
        return
    if mode == TIERED50_MODE:
        if environ.get(MAC_SQL_MODE_ENV) != "TIERED50":
            raise LiveModeBlocked("Tiered50 runner requires COLLECTOR_MAC_SQL_MODE=TIERED50")
        if environ.get(MAC_TIERED50_ENABLED_ENV) != "1":
            raise LiveModeBlocked("Tiered50 collection is disabled; no network request performed")
        if environ.get(MAC_SQL_RUNTIME_STATE_ENV) != MAC_SQL_REQUIRED_RUNTIME_STATE:
            raise LiveModeBlocked(
                "current Windows collector runtime is not confirmed retired; no network request performed"
            )
        if environ.get(MAC_TIERED50_CUTOVER_APPROVED_ENV) != "1":
            raise LiveModeBlocked("explicit Tiered50 cutover approval is missing; no network request performed")
        if environ.get(MAC_TIERED50_TARGET_SCOPE_ENV) != MAC_TIERED50_REQUIRED_TARGET_SCOPE:
            raise LiveModeBlocked("explicit approved Tiered50 target scope is missing; no network request performed")
        if environ.get(MAC_TIERED50_VALIDATION_POLICY_ENV) != MAC_TIERED50_REQUIRED_VALIDATION_POLICY:
            raise LiveModeBlocked(
                "Tiered50 requires completed offline validation before live collection; no network request performed"
            )
        return
    raise ValueError(f"unsupported Mac runtime mode: {mode}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one explicitly approved direct-database MDD heartbeat.")
    parser.add_argument(
        "--mode",
        choices=(LEGACY7_MODE, TIERED50_MODE),
        default=TIERED50_MODE,
        help="Tiered50 is the only production runtime; legacy7 is accepted only to emit a retirement block",
    )
    parser.add_argument("--at", dest="at_utc", help="UTC ISO-8601 time for deterministic validation")
    return parser


def _validate_runtime_login(login_status: dict[str, object], *, mode: str) -> None:
    if str(login_status.get("login_name", "")).casefold() in {"", "sa"}:
        raise MssqlSafetyError(f"Mac {mode} runtime requires the dedicated non-sa SQL login")
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
        raise MssqlSafetyError(f"Mac {mode} runtime login has a prohibited broad privilege")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configured_root = os.environ.get("COLLECTOR_RUNTIME_ROOT")
    root = Path(configured_root).expanduser().resolve() if configured_root else Path(__file__).resolve().parents[2]
    runtime_mode = args.mode
    try:
        if runtime_mode == LEGACY7_MODE:
            raise LiveModeBlocked("Legacy7 production runtime is retired; no network request performed")
        if runtime_mode != TIERED50_MODE:
            raise LiveModeBlocked(f"unsupported production runtime mode: {runtime_mode}")
        collector_mode = "mac-tiered50"
        collection_context = MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT
        base_configuration = merge_non_secret_runtime_values()
        _require_nonsecret_cutover_gate(dict(base_configuration.values), mode=runtime_mode)
        configuration = resolve_required_secrets(base_configuration)
        runtime_values = dict(configuration.values)
        require_tiered50_cutover_gate(runtime_values)
        mssql_settings = MssqlConnectionSettings.from_environment(runtime_values)
        if mssql_settings.database != EXPECTED_SQL_DATABASE:
            raise MssqlSafetyError(f"selected target database must be {EXPECTED_SQL_DATABASE}")
        at_utc = _parse_utc(args.at_utc) or datetime.now(timezone.utc)
        base_manifest = load_default_manifest(root)
        persistence = MssqlPersistence(
            settings=mssql_settings,
            raw_evidence_directory=runtime_values.get("MSSQL_RAW_EVIDENCE_DIR"),
            collection_context=collection_context,
        )
        login_status = persistence.runtime_login_status()
        _validate_runtime_login(login_status, mode=runtime_mode)

        schema = persistence.validate_runtime_schema()
        tiered_schema = persistence.validate_tiered50_runtime_schema()
        targets = persistence.load_tiered50_production_targets(base_manifest)
        manifest = replace(
            base_manifest,
            status="ACTIVE_TIERED50_PRODUCTION",
            activation_status="EXPLICIT_CUTOVER_APPROVED",
            targets=targets,
        )
        manifest.validate_production_binding()
        selection_policy_version = "TIERED50_UTC_PHASE_V1"
        selection_details = {
            "production_sampling_target_ids": sorted(
                int(target.production_sampling_target_id or 0) for target in targets
            ),
            "schedule_reconciliation": schedule_reconciliation(targets),
        }
        schema_details = {"base": schema, "tiered50": tiered_schema}
        quota_recovery = persistence.recover_stale_quota_permits(at_utc=at_utc)

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
            result = collector.run_once(mode=collector_mode, allow_live=True, at_utc=at_utc)
        finally:
            for signal_number, handler in previous_handlers.items():
                signal.signal(signal_number, handler)
        payload = {
            "status": result.status,
            "mode": collector_mode,
            "runtime_scope": runtime_mode,
            "collection_context": collection_context,
            "target_count": len(targets),
            "selection_policy_version": selection_policy_version,
            "logical_due_bucket": int(at_utc.timestamp() // 300),
            "sql_login_status": login_status,
            "schema": schema_details,
            "selection": selection_details,
            "quota_recovery": quota_recovery,
            "result": result.to_dict(),
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
        return 0 if result.status in {"COMPLETE", "DUPLICATE_NO_WORK"} else 1
    except SecretRequired as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (LiveModeBlocked, MssqlDependencyError, MssqlSafetyError, MssqlSchemaError) as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason": str(exc),
                    "simulated_transport_calls": 0,
                    "live_network_calls": 0,
                },
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
        # or credential-adjacent diagnostics. Audit tables receive sanitized
        # detail once a run has been opened.
        print(json.dumps({"status": "ERROR", "error_type": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
