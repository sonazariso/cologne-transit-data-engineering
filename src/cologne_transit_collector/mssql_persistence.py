"""DB-API persistence for the additive Microsoft database collector contract.

The module intentionally imports the optional client lazily.  Offline parser
and collector tests therefore do not require a native client library, while a
macOS deployment can use the pinned ``pymssql`` wheel.  No local database is
created by this adapter.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from .domain import (
    ABSOLUTE_PROVIDER_CEILING,
    HttpResponse,
    LEGACY7_SELECTION_POLICY_VERSION,
    MAC_SQL_COLLECTION_CONTEXT,
    MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT,
    NORMAL_OPERATIONAL_MAXIMUM,
    AUTOMATIC_RETRY_PROTECTION_BOUNDARY,
    approved_quota_boundary,
    TIERED50_SELECTION_POLICY_VERSION,
    SamplingEra,
    Target,
    TransportFailure,
)
from .legacy7_schedule import (
    LEGACY7_BUCKET_SLOT_RULE,
    LEGACY7_SLOT_TO_SAMPLING_TARGET_ID,
    legacy7_selection_for_bucket,
    validate_legacy7_slot_mapping,
)
from .manifest import LEGACY7_SAMPLING_TARGET_IDS, RuntimeManifest
from .quota import (
    CONSERVATIVE_LIVE_ATTEMPT,
    HTTP_PROVIDER_ATTEMPT_CONFIRMED,
    PENDING_AUTHORIZATION,
    RELEASED_NO_NETWORK,
)
from .quota_baseline import validate_stored_baseline_row
from .conservative_bootstrap import (
    BOOTSTRAP_AUTHORIZATION_MODE,
    BOOTSTRAP_LATEST_STOP_UTC,
    BOOTSTRAP_MONTH_KEY,
    ConservativeBootstrapError,
    KNOWN_PREBOOTSTRAP_RESEARCH_CONTEXT,
    bootstrap_local_counts,
    validate_stored_bootstrap_row,
)
from .quota_rollover import (
    AUTOMATIC_ROLLOVER_ACCOUNTING_MODEL,
    QuotaRolloverError,
    build_automatic_rollover_period,
)
from .runtime_configuration import (
    MSSQL_CREDENTIAL,
    SecretProvider,
    merge_non_secret_runtime_values,
    resolve_secret,
)
from .timing import iter_timing_observations
from .trias import iter_service_section_observations


MAC_COLLECTION_CONTEXT = MAC_SQL_COLLECTION_CONTEXT
WINDOWS_RUNTIME_UNCONFIRMED = "WINDOWS_COLLECTOR_RUNTIME_STATE_UNCONFIRMED"
RAW_RETENTION_HOURS = 24

_SECRET_NAMES = frozenset(
    {
        "apikey",
        "authorization",
        "credential",
        "password",
        "secret",
        "token",
    }
)


class MssqlDependencyError(RuntimeError):
    """Raised when the optional DB-API client is not installed."""


class MssqlSchemaError(RuntimeError):
    """Raised when the additive collector objects are not present."""


class MssqlSafetyError(RuntimeError):
    """Raised when a prepared collector operation would cross its safety gate."""


# SQL Server accepts at most 2,100 parameters in one prepared statement.  A
# skipped-target audit carries one value for every target-audit column below;
# keeping these values together makes the parameter-limit calculation and the
# statement construction impossible to drift independently.
MSSQL_PARAMETER_LIMIT = 2_100
SKIPPED_TARGET_AUDIT_COLUMNS = (
    "RunId",
    "PanelVersion",
    "TargetIdentity",
    "StationName",
    "Tier",
    "PhaseIndex",
    "NumberOfResults",
    "LogicalDueBucket",
    "SamplingEra",
    "SamplingTargetId",
    "SamplingSlot",
    "SelectionPolicyVersion",
    "ContinuityClassification",
    "CollectionContext",
    "StartedAtUtc",
    "CompletedAtUtc",
    "Status",
    "HttpAttempts",
    "HttpStatus",
    "StopEventResultCount",
    "ParserOutcome",
    "PersistenceOutcome",
    "ErrorCategory",
    "DurationMs",
    "SkipReason",
    "RetryDecision",
    "RetryOutcome",
    "OutcomeJson",
)
SKIPPED_TARGET_AUDIT_PARAMETERS_PER_ROW = len(SKIPPED_TARGET_AUDIT_COLUMNS)
MAX_SKIPPED_TARGET_AUDITS_PER_STATEMENT = (
    MSSQL_PARAMETER_LIMIT // SKIPPED_TARGET_AUDIT_PARAMETERS_PER_ROW
)


def build_skipped_target_audit_merge_statement(
    rows: Sequence[Sequence[Any]],
    *,
    transaction_control: bool = True,
) -> tuple[str, tuple[Any, ...]]:
    """Build the SQL Server skip-audit MERGE and its positional parameters.

    ``pymssql`` uses ``%s`` placeholders.  The source and target column lists
    deliberately come from the same tuple, and the MERGE has only an insert
    arm: a late pre-claim skip is allowed to fill a missing audit row but is
    never allowed to rewrite an audit or claim that another worker already
    owns.
    """

    row_values = [tuple(row) for row in rows]
    if not row_values:
        raise ValueError("at least one skipped-target audit row is required")
    if len(row_values) > MAX_SKIPPED_TARGET_AUDITS_PER_STATEMENT:
        raise MssqlSafetyError(
            "pre-claim skip batch exceeds the SQL Server 2,100-parameter limit; "
            f"maximum supported rows per statement is {MAX_SKIPPED_TARGET_AUDITS_PER_STATEMENT}"
        )
    if any(len(row) != SKIPPED_TARGET_AUDIT_PARAMETERS_PER_ROW for row in row_values):
        raise ValueError(
            "skipped-target audit row parameter count does not match the target-audit column contract"
        )

    placeholders = "(" + ", ".join("%s" for _ in SKIPPED_TARGET_AUDIT_COLUMNS) + ")"
    source_columns = ", ".join(SKIPPED_TARGET_AUDIT_COLUMNS)
    source_values = ", ".join(placeholders for _ in row_values)
    source_projection = ", ".join(f"source.{column}" for column in SKIPPED_TARGET_AUDIT_COLUMNS)
    transaction_prefix = """
        SET NOCOUNT ON;
        SET XACT_ABORT ON;
        BEGIN TRANSACTION;
    """ if transaction_control else """
        SET NOCOUNT ON;
    """
    transaction_suffix = "COMMIT TRANSACTION;" if transaction_control else ""
    statement = f"""
        {transaction_prefix}
        MERGE ctl.MddMacTargetAudit WITH (HOLDLOCK) AS target
        USING (VALUES {source_values}) AS source ({source_columns})
          ON target.RunId = source.RunId
         AND target.TargetIdentity = source.TargetIdentity
         AND target.LogicalDueBucket = source.LogicalDueBucket
        WHEN NOT MATCHED BY TARGET THEN
            INSERT ({source_columns})
            VALUES ({source_projection});
        {transaction_suffix}
    """
    parameters = tuple(value for row in row_values for value in row)
    expected_parameters = len(row_values) * SKIPPED_TARGET_AUDIT_PARAMETERS_PER_ROW
    if len(parameters) != expected_parameters or len(parameters) > MSSQL_PARAMETER_LIMIT:
        raise AssertionError("constructed skip-audit statement exceeds its SQL parameter contract")
    return statement, parameters


def _build_skipped_target_audit_merge_statement(
    rows: Sequence[Sequence[Any]],
) -> tuple[str, tuple[Any, ...]]:
    """Backward-compatible transaction-wrapped skip-audit builder."""

    return build_skipped_target_audit_merge_statement(rows, transaction_control=True)


# These lists are deliberately separate.  The full list belongs to an
# administrator/migration context; the runtime list is the smallest object
# surface used by one normal Mac Legacy7 heartbeat and its read-only startup
# validation.  In particular, the runtime list does not contain import,
# historical realtime, source-evidence, or analytics objects.
FULL_SCHEMA_TABLES = (
    "ctl.MddRealtimeSamplingTarget",
    "ctl.MddRealtimeSamplingSlot",
    "ctl.MddMacCollectorRun",
    "ctl.MddMacProductionLease",
    "ctl.MddMacProductionLeaseEvent",
    "ctl.MddMacDispatcherClaim",
    "ctl.MddMacDispatcherClaimEvent",
    "ctl.MddMacTargetClaim",
    "ctl.MddMacTargetClaimEvent",
    "ctl.MddMacTargetAudit",
    "ctl.MddMacHttpAttempt",
    "ctl.MddMacQuotaPermit",
    "ctl.MddMacQuotaAudit",
    "ctl.MddMacSharedQuotaAccounting",
    "ctl.MddMacSharedQuotaReconciliation",
    "ctl.MddMacConservativeBootstrapAuthorization",
    "ctl.MddMacQuotaResetContract",
    "ctl.MddMacSamplingEra",
    "ctl.MddMacCurrentProductionEra",
    "ctl.MddMacProductionCutover",
    "ctl.MddMacProductionResumeEvent",
    "ctl.MddMacMethodologyEvidence",
    "stg.MddMacSourceSnapshot",
    "stg.MddMacCallObservation",
    "stg.MddMacTimingObservation",
    "stg.MddMacSituationEvidence",
    "stg.MddMacServiceSectionEvidence",
    "stg.MddMacAttributeEvidence",
    "stg.MddMacResponseError",
    "stg.MddMacParserGap",
    "ctl.MddImportBatch",
    "ctl.MddImportTableAudit",
    "ctl.MddImportEvidenceAudit",
    "ctl.MddImportSourceRow",
    "ctl.MddImportOverlap",
)

FULL_SCHEMA_ROUTINES = (
    "ctl.uspGetMddRealtimeSamplingTarget",
    "ctl.uspEnsureMddMacQuotaPeriod",
    "stg.uspPersistMddMacSnapshot",
    "analytics.vwMddMacRecordedAtTimeHistory",
    "analytics.vwMddMacSourceFieldCoverage",
)

RUNTIME_SCHEMA_TABLES = (
    "ctl.MddRealtimeSamplingTarget",
    "ctl.MddRealtimeSamplingSlot",
    "ctl.MddMacCollectorRun",
    "ctl.MddMacProductionLease",
    "ctl.MddMacProductionLeaseEvent",
    "ctl.MddMacDispatcherClaim",
    "ctl.MddMacDispatcherClaimEvent",
    "ctl.MddMacTargetClaim",
    "ctl.MddMacTargetClaimEvent",
    "ctl.MddMacTargetAudit",
    "ctl.MddMacHttpAttempt",
    "ctl.MddMacQuotaPermit",
    "ctl.MddMacQuotaAudit",
    "ctl.MddMacSharedQuotaAccounting",
    "ctl.MddMacConservativeBootstrapAuthorization",
    "ctl.MddMacQuotaResetContract",
)

RUNTIME_SCHEMA_ROUTINES = (
    "ctl.uspGetMddRealtimeSamplingTarget",
    "ctl.uspEnsureMddMacQuotaPeriod",
    "stg.uspPersistMddMacSnapshot",
)

TIERED50_TARGET_TABLE = "ctl.MddMacTiered50ProductionTarget"
TIERED50_RUNTIME_SCHEMA_TABLES = RUNTIME_SCHEMA_TABLES + (TIERED50_TARGET_TABLE,)
PRODUCTION_LEASE_NAME = "MDD_MAC_PRODUCTION"
QUOTA_BASELINE_UNAVAILABLE = "SHARED_PROVIDER_BASELINE_UNAVAILABLE"
QUOTA_BASELINE_INCOMPLETE = "SHARED_PROVIDER_BASELINE_INCOMPLETE"
QUOTA_BASELINE_COLUMNS = (
    "MonthKey",
    "ProviderQuotaCeiling",
    "NormalOperationalMaximum",
    "RetryProtectionBoundary",
    "ConfirmedProviderAttempts",
    "ConservativeUnknownAttempts",
    "ExplicitSharedConsumerReservedAttempts",
    "AccountingStatus",
    "IncludesAllSharedConsumers",
    "BaselineAsOfUtc",
    "LocalUsageIncludedThroughUtc",
    "SourceDescription",
    "AccountingModel",
    "ProviderMeasurementCutoffUtc",
    "StaleAfterUtc",
    "SourceEvidenceHash",
    "ReconciliationKey",
    "ProviderPeriodTimezone",
    "ProviderPeriodStartUtc",
    "ProviderPeriodEndUtc",
)
BOOTSTRAP_AUTHORIZATION_COLUMNS = (
    "AuthorizationId",
    "AuthorizationKey",
    "MonthKey",
    "AuthorizationMode",
    "AccountingModel",
    "ScreenshotCumulativeRequests",
    "ScreenshotMonthlyLimit",
    "ScreenshotSha256",
    "ConfirmedLocalAttempts",
    "ConservativeUnknownLocalAttempts",
    "PendingLocalReservations",
    "AccountedTotalAtAuthorization",
    "ProviderQuotaCeiling",
    "NormalOperationalMaximum",
    "RetryProtectionBoundary",
    "ValidFromUtc",
    "LocalUsageIncludedThroughUtc",
    "StopAtUtc",
    "OperatorApprovalAtUtc",
    "OperatorApprovalStatementHash",
    "CredentialExclusivityConfirmed",
    "ProviderScopeVerified",
    "ProviderMeasurementCutoffEstablished",
    "ProviderResetTimezoneEstablished",
    "UnexpectedExternalUsage",
    "SourceEvidenceHash",
    "AuthorizationStatus",
    "EvidenceJson",
    "RecordedAtUtc",
    "RevokedAtUtc",
    "RevocationReason",
)
RESET_CONTRACT_COLUMNS = (
    "ContractId",
    "ContractKey",
    "ProviderName",
    "ResetModel",
    "ResetTimezone",
    "EffectiveFromUtc",
    "ActivationDelaySeconds",
    "ProviderQuotaCeiling",
    "CredentialExclusivityConfirmed",
    "ProviderResetBoundaryVerified",
    "CalendarMonthResetModelEstablished",
    "OperatorAssumptionApproved",
    "SupportedResetTimezonesJson",
    "SourceEvidenceHash",
    "EvidenceJson",
    "ContractStatus",
    "RecordedAtUtc",
    "RevokedAtUtc",
    "RevocationReason",
)


@dataclass(frozen=True)
class MssqlConnectionSettings:
    """Non-secret connection settings; the password is retained in memory only."""

    host: str
    database: str = "CologneTransitIntelligence"
    user: str | None = None
    password: str | None = None
    port: int = 1433
    login_timeout_seconds: int = 8
    query_timeout_seconds: int = 30

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        secret_provider: SecretProvider | None = None,
    ) -> "MssqlConnectionSettings":
        values = dict(merge_non_secret_runtime_values(environ).values)

        def first(*names: str, default: str | None = None) -> str | None:
            for name in names:
                value = values.get(name)
                if value is not None and value.strip():
                    return value.strip()
            return default

        host = first("MSSQL_HOST", "SQL_SERVER_HOST")
        if not host:
            raise ValueError("MSSQL_HOST or SQL_SERVER_HOST is required")
        database = first("MSSQL_DATABASE", "SQL_SERVER_DB", default="CologneTransitIntelligence")
        user = first("MSSQL_USER", "SQL_SERVER_USER")
        password = first("MSSQL_PASSWORD", "SQL_SERVER_PASSWORD")
        if password is None:
            password = resolve_secret(values, credential_type=MSSQL_CREDENTIAL, provider=secret_provider)
        try:
            port = int(first("MSSQL_PORT", "SQL_SERVER_CONTAINER_PORT", default="1433") or "1433")
            login_timeout = int(first("MSSQL_LOGIN_TIMEOUT_SECONDS", default="8") or "8")
            query_timeout = int(first("MSSQL_QUERY_TIMEOUT_SECONDS", default="30") or "30")
        except ValueError as exc:
            raise ValueError("MSSQL port and timeout settings must be integers") from exc
        if not 1 <= port <= 65535:
            raise ValueError("MSSQL port must be between 1 and 65535")
        if login_timeout <= 0 or query_timeout <= 0:
            raise ValueError("MSSQL timeouts must be positive")
        return cls(
            host=host,
            database=str(database or "CologneTransitIntelligence"),
            user=user,
            password=password,
            port=port,
            login_timeout_seconds=login_timeout,
            query_timeout_seconds=query_timeout,
        )

    def redacted(self) -> dict[str, Any]:
        """Return diagnostics with no credential material."""

        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user_configured": bool(self.user),
            "password_configured": bool(self.password),
            "login_timeout_seconds": self.login_timeout_seconds,
            "query_timeout_seconds": self.query_timeout_seconds,
        }


def _load_client() -> Any:
    try:
        return importlib.import_module("pymssql")
    except ImportError as exc:  # pragma: no cover - depends on deployment
        raise MssqlDependencyError(
            "pymssql is required for the macOS Microsoft database adapter; "
            "install requirements-mssql.txt"
        ) from exc


def connect_mssql(settings: MssqlConnectionSettings) -> Any:
    """Open one DB-API connection without logging secrets."""

    if not settings.user or settings.password is None:
        raise ValueError("MSSQL_USER and MSSQL_PASSWORD must be supplied outside source control")
    client = _load_client()
    return client.connect(
        server=settings.host,
        port=settings.port,
        user=settings.user,
        password=settings.password,
        database=settings.database,
        login_timeout=settings.login_timeout_seconds,
        timeout=settings.query_timeout_seconds,
        as_dict=True,
        charset="UTF-8",
    )


@dataclass(frozen=True)
class MssqlClaimResult:
    won: bool
    key: tuple[Any, ...]
    existing_status: str | None
    claim_token: str | None
    event_id: int


@dataclass(frozen=True)
class MssqlDispatcherClaimResult:
    won: bool
    key: tuple[Any, ...]
    existing_status: str | None
    claim_token: str | None
    event_id: int


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _utc(value).isoformat().replace("+00:00", "Z")


def _optional_utc(value: Any) -> datetime | None:
    """Normalize a database or caller timestamp for append-only comparisons."""

    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise MssqlSafetyError("sampling-era evidence contains an invalid timestamp") from exc
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _canonical_run_ids(run_ids: Sequence[str]) -> tuple[str, ...]:
    """Return the exact run-set identity used for methodology deduplication."""

    normalized = tuple(str(run_id) for run_id in run_ids)
    if not normalized or any(not run_id.strip() for run_id in normalized):
        raise MssqlSafetyError("methodology evidence requires a non-empty affected run set")
    if len(set(normalized)) != len(normalized):
        raise MssqlSafetyError("methodology evidence affected run set contains duplicate RunIds")
    return tuple(sorted(normalized))


def _stored_run_ids(value: Any) -> tuple[str, ...]:
    """Decode a stored RunIdsJson value, failing closed on malformed history."""

    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MssqlSafetyError("existing methodology evidence has invalid RunIdsJson") from exc
    if not isinstance(parsed, list) or not parsed or any(not isinstance(item, str) or not item.strip() for item in parsed):
        raise MssqlSafetyError("existing methodology evidence has an invalid affected run set")
    normalized = tuple(str(item) for item in parsed)
    if len(set(normalized)) != len(normalized):
        raise MssqlSafetyError("existing methodology evidence has duplicate affected RunIds")
    return tuple(sorted(normalized))


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if row is None:
        return None
    if isinstance(row, Mapping):
        return row.get(key)
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return None


def _row_named(row: Any, *keys: str, index: int = 0) -> Any:
    """Read a DB-API row using a small set of case-insensitive aliases."""

    if isinstance(row, Mapping):
        lowered = {str(key).lower(): value for key, value in row.items()}
        for key in keys:
            if key.lower() in lowered:
                return lowered[key.lower()]
        return None
    return _row_value(row, keys[0] if keys else "", index)


def _row_mapping(row: Any, columns: Sequence[str]) -> dict[str, Any]:
    """Normalize a DB-API row for shared validation helpers."""

    if isinstance(row, Mapping):
        lowered = {str(key).casefold(): value for key, value in row.items()}
        return {column: lowered.get(column.casefold()) for column in columns}
    return {column: _row_value(row, column, index) for index, column in enumerate(columns)}


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(child) for child in value]
    if isinstance(value, datetime):
        return _iso(value)
    return value


def _json_text(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (Mapping, list, tuple)):
        return _json_text(value)
    return str(value)


def _safe_document(value: Any, *, depth: int = 0) -> Any:
    """Bound the normalized document and remove credential-shaped values."""

    if depth > 8:
        return f"<{type(value).__name__}>"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value[:2000] if isinstance(value, str) else value
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for key, child in list(value.items())[:2000]:
            key_text = str(key)
            compact = re.sub(r"[^a-z0-9]", "", key_text.lower())
            if compact in _SECRET_NAMES:
                safe[key_text] = "<redacted>"
            elif compact in {"rawresponse", "rawpayload", "requestheaders", "responseheaders"}:
                safe[key_text] = "<not-persisted>"
            else:
                safe[key_text] = _safe_document(child, depth=depth + 1)
        return safe
    if isinstance(value, (list, tuple)):
        return [_safe_document(child, depth=depth + 1) for child in list(value)[:2000]]
    return str(value)[:2000]


def _target_sampling_id(target: Target) -> int | None:
    value = target.production_sampling_target_id or target.legacy_sampling_target_id
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _event_calls(event: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    calls = event.get("calls")
    if isinstance(calls, list):
        return [call for call in calls if isinstance(call, Mapping)]
    collected: list[Mapping[str, Any]] = []
    for name in ("previous_calls", "this_calls", "onward_calls"):
        values = event.get(name)
        if isinstance(values, list):
            collected.extend(call for call in values if isinstance(call, Mapping))
    return collected


def _call_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event_index, value in enumerate(snapshot.get("events", ()) or ()):
        if not isinstance(value, Mapping):
            continue
        event = value
        event_ordinal = event.get("event_ordinal", event.get("result_ordinal", event_index))
        for call_index, call_value in enumerate(_event_calls(event)):
            call = call_value
            source_paths = call.get("source_paths")
            scope_evidence = call.get("source_scope_evidence")
            rows.append(
                {
                    "result_id": _text(event.get("result_id")),
                    "result_ordinal": event.get("result_ordinal", event_ordinal),
                    "event_ordinal": event_ordinal,
                    "call_scope": str(call.get("call_scope", "")),
                    "call_ordinal": int(call.get("call_ordinal", call_index) or 0),
                    "source_path": call.get("source_path"),
                    "call_at_stop_source_path": call.get("call_at_stop_source_path"),
                    "stop_point_ref": _text(call.get("stop_point_ref")),
                    "stop_display_text": _text(call.get("stop_display_text")),
                    "stop_seq_number": call.get("stop_seq_number"),
                    "planned_bay": _text(call.get("planned_bay")),
                    "estimated_bay": _text(call.get("estimated_bay")),
                    "unplanned_stop": call.get("unplanned_stop"),
                    "not_serviced_stop": call.get("not_serviced_stop"),
                    "no_boarding_at_stop": call.get("no_boarding_at_stop"),
                    "no_alighting_at_stop": call.get("no_alighting_at_stop"),
                    "cancelled": (scope_evidence or {}).get("cancelled", {}).get("value")
                    if isinstance(scope_evidence, Mapping)
                    else None,
                    "deviation": (scope_evidence or {}).get("deviation", {}).get("value")
                    if isinstance(scope_evidence, Mapping)
                    else None,
                    "operating_day_ref": _text(event.get("operating_day_ref")),
                    "journey_ref": _text(event.get("journey_ref")),
                    "vehicle_journey_ref": _text(event.get("vehicle_journey_ref")),
                    "dated_vehicle_journey_ref": _text(event.get("dated_vehicle_journey_ref")),
                    "line_ref": _text(event.get("line_ref")),
                    "direction_ref": _text(event.get("direction_ref")),
                    "operator_ref": _text(event.get("operator_ref")),
                    "pt_mode": _text(event.get("pt_mode")),
                    "submode": _text(event.get("submode", event.get("rail_submode"))),
                    "origin_stop_point_ref": _text(event.get("origin_stop_point_ref")),
                    "origin_text": _text(event.get("origin_text")),
                    "destination_stop_point_ref": _text(event.get("destination_stop_point_ref")),
                    "destination_text": _text(event.get("destination_text")),
                    "source_paths_json": _json_text(source_paths or {}),
                    "source_event_json": _json_text(
                        {
                            "event_source_path": event.get("service_source_path"),
                            "identity_source_paths": event.get("identity_source_paths"),
                            "operational_event_field_source_paths": event.get(
                                "operational_event_field_source_paths"
                            ),
                            "source_scope_evidence": scope_evidence,
                        }
                    ),
                }
            )
    return rows


def _timing_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row_value in iter_timing_observations(snapshot):
        row = dict(row_value)
        identity_basis = row.pop("source_event_identity_basis", None)
        identity_missing = row.pop("source_event_identity_missing_fields", None)
        identity_ambiguity = row.pop("source_event_identity_ambiguity", None)
        row["result_ordinal"] = row.get("event_ordinal")
        row["source_event_identity_basis_json"] = _json_text(identity_basis)
        row["source_event_identity_missing_fields_json"] = _json_text(identity_missing or [])
        row["source_event_identity_ambiguity_json"] = _json_text(identity_ambiguity)
        row["source_paths_json"] = _json_text(row.get("source_paths") or {})
        for key in (
            "result_id",
            "operating_day_ref",
            "journey_ref",
            "vehicle_journey_ref",
            "dated_vehicle_journey_ref",
            "line_ref",
            "direction_ref",
            "operator_ref",
            "stop_point_ref",
            "timetabled_time",
            "estimated_time",
            "estimated_time_low",
            "estimated_time_high",
            "recorded_at_time",
            "source_path",
            "timetabled_time_source_path",
            "estimated_time_source_path",
            "estimated_time_low_source_path",
            "estimated_time_high_source_path",
            "recorded_at_time_source_path",
            "stop_point_ref_source_path",
            "stop_seq_number_source_path",
        ):
            if key in row and isinstance(row[key], (Mapping, list, tuple)):
                row[key] = _text(row[key])
        return_rows = row
        rows.append(return_rows)
    return rows


def _situation_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in snapshot.get("events", ()) or ():
        if not isinstance(event, Mapping):
            continue
        event_ordinal = event.get("event_ordinal", event.get("result_ordinal"))
        for evidence in event.get("situation_evidence", ()) or ():
            if not isinstance(evidence, Mapping):
                continue
            fields = evidence.get("fields") if isinstance(evidence.get("fields"), Mapping) else {}
            affected = evidence.get("affected_entity_context")
            affected_paths = evidence.get("affected_entity_field_paths")
            rows.append(
                {
                    "result_id": _text(evidence.get("result_id", event.get("result_id"))),
                    "result_ordinal": evidence.get("result_ordinal", event_ordinal),
                    "event_ordinal": evidence.get("event_ordinal", event_ordinal),
                    "evidence_type": evidence.get("evidence_type"),
                    "situation_type": evidence.get("situation_type"),
                    "reference_type": evidence.get("reference_type"),
                    "reference_value": _text(evidence.get("value")),
                    "participant_ref": _text(fields.get("participant_ref")),
                    "situation_number": _text(fields.get("situation_number")),
                    "version_source_text": _text(fields.get("version")),
                    "identifier": _text(fields.get("identifier")),
                    "summary": _text(fields.get("summary")),
                    "description": _text(fields.get("description")),
                    "detail": _text(fields.get("detail")),
                    "creation_time_source_text": _text(fields.get("creation_time")),
                    "valid_from_source_text": _text(fields.get("valid_from")),
                    "valid_to_source_text": _text(fields.get("valid_to")),
                    "source": _text(fields.get("source")),
                    "source_type": _text(fields.get("source_type")),
                    "priority_source_text": _text(fields.get("priority")),
                    "affects_json": _json_text(evidence.get("affected_entity_context", {}).get("affects"))
                    if isinstance(evidence.get("affected_entity_context"), Mapping)
                    else None,
                    "affected_entity_json": _json_text(affected),
                    "affected_journey_json": _json_text(
                        evidence.get("affected_entity_context", {}).get("affected_journey")
                        if isinstance(evidence.get("affected_entity_context"), Mapping)
                        else None
                    ),
                    "affected_line_json": _json_text(
                        evidence.get("affected_entity_context", {}).get("affected_line")
                        if isinstance(evidence.get("affected_entity_context"), Mapping)
                        else None
                    ),
                    "identity_key": _text(evidence.get("identity_key")),
                    "association_scope": evidence.get("association_scope"),
                    "call_scope": evidence.get("call_scope"),
                    "source_path": evidence.get("source_path"),
                    "field_paths_json": _json_text(evidence.get("field_paths") or {}),
                    "affected_entity_field_paths_json": _json_text(affected_paths or {}),
                    "source_representation_json": _json_text(evidence.get("source_representation")),
                }
            )
        for reference in event.get("situation_references", ()) or ():
            # The parser's explicit reference projection is also retained in
            # situation_evidence.  This fallback supports hand-authored
            # snapshots that only contain the compatibility reference list.
            if any(
                row.get("evidence_type") == "REFERENCE"
                and row.get("reference_value") == _text(reference)
                and row.get("event_ordinal") == event_ordinal
                for row in rows
            ):
                continue
            rows.append(
                {
                    "result_id": _text(event.get("result_id")),
                    "result_ordinal": event.get("result_ordinal", event_ordinal),
                    "event_ordinal": event_ordinal,
                    "evidence_type": "REFERENCE",
                    "situation_type": None,
                    "reference_type": "SituationFullRef",
                    "reference_value": _text(reference),
                    "participant_ref": None,
                    "situation_number": None,
                    "version_source_text": None,
                    "identifier": None,
                    "summary": None,
                    "description": None,
                    "detail": None,
                    "creation_time_source_text": None,
                    "valid_from_source_text": None,
                    "valid_to_source_text": None,
                    "source": None,
                    "source_type": None,
                    "priority_source_text": None,
                    "affects_json": None,
                    "affected_entity_json": None,
                    "affected_journey_json": None,
                    "affected_line_json": None,
                    "identity_key": None,
                    "association_scope": None,
                    "call_scope": None,
                    "source_path": None,
                    "field_paths_json": None,
                    "affected_entity_field_paths_json": None,
                    "source_representation_json": None,
                }
            )
    return rows


def _section_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for section in iter_service_section_observations(snapshot):
        row = dict(section)
        row["source_paths_json"] = _json_text(row.get("source_paths") or {})
        row["source_representation_json"] = _json_text(row.get("source_representation"))
        row["mode_source_representation"] = _json_text(row.get("mode_source_representation"))
        rows.append(row)
    return rows


def _attribute_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in snapshot.get("events", ()) or ():
        if not isinstance(event, Mapping):
            continue
        event_ordinal = event.get("event_ordinal", event.get("result_ordinal"))
        for ordinal, evidence in enumerate(event.get("service_attribute_evidence", ()) or ()):
            if not isinstance(evidence, Mapping):
                continue
            code = evidence.get("code")
            text = evidence.get("text")
            rows.append(
                {
                    "result_id": _text(evidence.get("result_id", event.get("result_id"))),
                    "result_ordinal": evidence.get("result_ordinal", event_ordinal),
                    "event_ordinal": evidence.get("event_ordinal", event_ordinal),
                    "attribute_ordinal": ordinal,
                    "attribute_type": evidence.get("attribute_type", evidence.get("attribute_name")),
                    "source_path": evidence.get("source_path"),
                    "association_context_path": evidence.get("association_context_path"),
                    "association_scope": evidence.get("association_scope"),
                    "call_scope": evidence.get("call_scope"),
                    "code_source_text": _text(code),
                    "text_source_text": _text(text),
                    "incident_call_source_path": evidence.get("incident_call_source_path"),
                    "info_call_source_path": evidence.get("info_call_source_path"),
                    "code_source_paths_json": _json_text(evidence.get("code_source_paths") or []),
                    "text_source_paths_json": _json_text(evidence.get("text_source_paths") or []),
                    "source_field_paths_json": _json_text(evidence.get("source_field_paths") or []),
                    "source_representation_json": _json_text(evidence.get("source_representation")),
                    "semantic_role": evidence.get("semantic_role", "PROVIDER_REPORTED_EVIDENCE_ONLY"),
                }
            )
    return rows


def _error_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value in snapshot.get("response_errors", ()) or ():
        if not isinstance(value, Mapping):
            continue
        rows.append(
            {
                "result_id": _text(value.get("result_id")),
                "result_ordinal": value.get("result_ordinal"),
                "field_name": value.get("field_name"),
                "value": _text(value.get("value")) or "",
                "error_scope": value.get("error_scope"),
                "source_path": value.get("source_path"),
                "result_source_path": value.get("result_source_path"),
            }
        )
    return rows


def _gap_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value in snapshot.get("parser_gaps", ()) or ():
        if not isinstance(value, Mapping):
            continue
        rows.append(
            {
                "result_id": _text(value.get("result_id")),
                "result_ordinal": value.get("result_ordinal"),
                "event_ordinal": value.get("event_ordinal"),
                "field_name": value.get("field_name"),
                "normalized_field_name": value.get("normalized_field_name", value.get("normalized_name")),
                "source_path": value.get("source_path"),
                "value_type": value.get("value_type"),
                "source_value": _text(value.get("source_value")),
                "source_representation_json": _json_text(value.get("source_representation")),
                "value_excerpt": _text(value.get("value_excerpt")),
                "mapping_status": value.get("mapping_status"),
                "semantic_hint": value.get("semantic_hint"),
                "event_context": value.get("event_context"),
                "association_scope": value.get("association_scope"),
                "call_scope": value.get("call_scope"),
            }
        )
    return rows


def build_mssql_snapshot_rows(parsed_snapshot: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Flatten one parser snapshot without losing source occurrence grain."""

    return {
        "calls": _call_rows(parsed_snapshot),
        "timings": _timing_rows(parsed_snapshot),
        "situations": _situation_rows(parsed_snapshot),
        "service_sections": _section_rows(parsed_snapshot),
        "attributes": _attribute_rows(parsed_snapshot),
        "response_errors": _error_rows(parsed_snapshot),
        "parser_gaps": _gap_rows(parsed_snapshot),
    }


class _TemporaryRawEvidence:
    """Owner-only, bounded raw evidence outside the database."""

    def __init__(self, directory: str | Path | None) -> None:
        self.directory = Path(directory).expanduser().resolve() if directory else None
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.directory.chmod(stat.S_IRWXU)

    @staticmethod
    def _contains_secret(body: bytes) -> bool:
        text = body.decode("utf-8", errors="ignore").casefold()
        for marker in ("authorization:", "x-api-key", "apikey", "password", "bearer "):
            if marker in text:
                return True
        configured_key = os.environ.get("MDD_API_KEY")
        return bool(configured_key and configured_key.encode("utf-8") in body)

    def retain(self, body: bytes, *, observed_at: datetime, reason: str) -> str:
        if self.directory is None:
            return "DISABLED_NO_EXTERNAL_RAW_DIRECTORY"
        if self._contains_secret(body):
            return "REFUSED_SECRET_DETECTED"
        digest = hashlib.sha256(body).hexdigest()[:20]
        name = f"mdd-response-{_utc(observed_at).strftime('%Y%m%dT%H%M%SZ')}-{digest}-{uuid.uuid4().hex}.bin"
        path = self.directory / name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(path, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(body)
            observed_timestamp = _utc(observed_at).timestamp()
            os.utime(path, (observed_timestamp, observed_timestamp))
        except BaseException:
            try:
                path.unlink()
            except OSError:
                pass
            raise
        return "RETAINED"

    def cleanup(self, *, now_utc: datetime | None = None) -> Mapping[str, Any]:
        if self.directory is None:
            return {"status": "NOT_APPLICABLE", "reason": "no external raw directory configured"}
        now = _utc(now_utc or datetime.now(timezone.utc))
        removed = 0
        for path in self.directory.glob("mdd-response-*.bin"):
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                if now - modified > timedelta(hours=RAW_RETENTION_HOURS):
                    path.unlink()
                    removed += 1
            except FileNotFoundError:
                continue
        return {"status": "COMPLETE", "removed": removed, "maximum_retention_hours": RAW_RETENTION_HOURS}


class MssqlPersistence:
    """Persistence port implementation backed directly by the additive contract."""

    is_production_ready = True
    supports_atomic_quota_finalization = True

    def __init__(
        self,
        settings: MssqlConnectionSettings | None = None,
        *,
        connection_factory: Callable[[], Any] | None = None,
        raw_evidence_directory: str | Path | None = None,
        collection_context: str = MAC_COLLECTION_CONTEXT,
        authorization_clock_fn: Callable[[], datetime] | None = None,
        allow_injected_authorization_time: bool = False,
    ) -> None:
        self.settings = settings or MssqlConnectionSettings.from_environment()
        self._connection_factory = connection_factory or (lambda: connect_mssql(self.settings))
        self.collection_context = collection_context
        if (
            (authorization_clock_fn is not None or allow_injected_authorization_time)
            and collection_context != "OFFLINE_DEVELOPMENT"
        ):
            raise MssqlSafetyError(
                "injected authorization clocks are permitted only in the isolated offline context"
            )
        self._authorization_clock_fn = authorization_clock_fn
        self._allow_injected_authorization_time = bool(allow_injected_authorization_time)
        configured_raw = raw_evidence_directory or os.environ.get("MSSQL_RAW_EVIDENCE_DIR")
        self._raw = _TemporaryRawEvidence(configured_raw)

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        connection = self._connection_factory()
        try:
            yield connection
        except BaseException:
            try:
                connection.rollback()
            except BaseException:
                pass
            raise
        finally:
            connection.close()

    @staticmethod
    def _cursor(connection: Any) -> Any:
        return connection.cursor()

    def _authoritative_authorization_time(self, fallback: datetime, *, cursor: Any | None = None) -> datetime:
        """Read the SQL Server clock for live permit authorization.

        An injected clock is available to deterministic tests. Production
        callers cannot make a stale ``at_utc`` value bypass bootstrap expiry:
        the default path asks SQL Server for its current UTC time and denies
        authorization if that read fails.
        """

        if self._allow_injected_authorization_time:
            return _utc(fallback)
        if self._authorization_clock_fn is not None:
            return _utc(self._authorization_clock_fn())
        def read_authoritative(active_cursor: Any) -> datetime:
            try:
                active_cursor.execute("SELECT SYSUTCDATETIME() AS AuthoritativeUtc")
                row = active_cursor.fetchone()
                value = _row_named(row, "AuthoritativeUtc", index=0)
                if value is None:
                    raise ValueError("SQL time query returned no value")
                parsed = _optional_utc(value)
                if parsed is None:
                    raise ValueError("SQL time query returned an invalid value")
                return _utc(parsed)
            except BaseException as exc:
                raise MssqlSafetyError(
                    "authoritative SQL UTC time is unavailable; network authorization denied"
                ) from exc

        if cursor is not None:
            return read_authoritative(cursor)
        with self._connection() as connection:
            return read_authoritative(self._cursor(connection))

    def authoritative_utc_now(self) -> datetime:
        """Return SQL UTC time for a final pre-transmission safety check."""

        return self._authoritative_authorization_time(datetime.now(timezone.utc))

    def validate_full_schema(self) -> Mapping[str, Any]:
        """Validate the complete additive schema under an admin context.

        This path intentionally includes historical-import and source-evidence
        objects.  It is for migration application, migration validation,
        administrator diagnostics, and import tooling only; production runtime
        callers must use :meth:`validate_runtime_schema`.
        """

        with self._connection() as connection:
            cursor = self._cursor(connection)
            cursor.execute(
                """
                SELECT s.name + N'.' + o.name AS object_name, o.type AS object_type
                FROM sys.objects AS o
                INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
                WHERE s.name IN ('ctl', 'stg', 'analytics')
                  AND (o.name LIKE 'MddMac%' OR o.name LIKE 'MddImport%' OR o.name LIKE 'MddRealtime%'
                       OR o.name IN ('uspGetMddRealtimeSamplingTarget', 'uspPersistMddMacSnapshot',
                                     'uspEnsureMddMacQuotaPeriod',
                                     'vwMddMacRecordedAtTimeHistory', 'vwMddMacSourceFieldCoverage'))
                """
            )
            rows = cursor.fetchall()
            cursor.execute(
                """
                SELECT s.name + N'.' + o.name AS object_name, c.name AS column_name
                FROM sys.columns AS c
                INNER JOIN sys.objects AS o ON o.object_id = c.object_id
                INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
                WHERE s.name = N'ctl'
                  AND o.name = N'MddMacTargetAudit'
                  AND c.name IN (N'SamplingSlot', N'SelectionPolicyVersion')
                """
            )
            column_rows = cursor.fetchall()
        names = {str(_row_value(row, "object_name", 0)) for row in rows}
        missing = [name for name in FULL_SCHEMA_TABLES + FULL_SCHEMA_ROUTINES if name not in names]
        if missing:
            raise MssqlSchemaError("additive collector objects are missing: " + ", ".join(missing))
        observed_columns = {str(_row_named(row, "column_name", index=1)) for row in column_rows}
        required_columns = {"SamplingSlot", "SelectionPolicyVersion"}
        missing_columns = sorted(required_columns - observed_columns)
        if missing_columns:
            raise MssqlSchemaError(
                "Legacy7 audit lineage columns are missing: " + ", ".join(missing_columns)
            )
        return {
            "status": "PASS",
            "validation_scope": "FULL_ADMIN",
            "expected_table_count": len(FULL_SCHEMA_TABLES),
            "expected_routine_count": len(FULL_SCHEMA_ROUTINES),
            "required_legacy7_audit_columns": sorted(required_columns),
            "missing": [],
        }

    def validate_runtime_schema(self) -> Mapping[str, Any]:
        """Validate only the direct production runtime object surface.

        Database metadata visibility is permission-scoped.  A dedicated
        collector login is intentionally not able to inspect import tables,
        historical realtime staging, source-evidence tables, or analytics
        views.  Query only the exact runtime objects and the two lineage
        columns needed by the collector so absence of unrelated metadata cannot
        make a valid runtime login fail preflight.
        """

        runtime_names = RUNTIME_SCHEMA_TABLES + RUNTIME_SCHEMA_ROUTINES
        ctl_tables = tuple(name.split(".", 1)[1] for name in RUNTIME_SCHEMA_TABLES if name.startswith("ctl."))
        ctl_routines = tuple(name.split(".", 1)[1] for name in RUNTIME_SCHEMA_ROUTINES if name.startswith("ctl."))
        stg_routines = tuple(name.split(".", 1)[1] for name in RUNTIME_SCHEMA_ROUTINES if name.startswith("stg."))

        def _sql_names(values: Sequence[str]) -> str:
            return ", ".join("N'" + value.replace("'", "''") + "'" for value in values)

        predicates = [
            f"(s.name = N'ctl' AND o.name IN ({_sql_names(ctl_tables)}))",
            f"(s.name = N'ctl' AND o.name IN ({_sql_names(ctl_routines)}))",
            f"(s.name = N'stg' AND o.name IN ({_sql_names(stg_routines)}))",
        ]
        with self._connection() as connection:
            cursor = self._cursor(connection)
            cursor.execute(
                """
                SELECT s.name + N'.' + o.name AS object_name, o.type AS object_type
                FROM sys.objects AS o
                INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
                WHERE """
                + " OR ".join(predicates)
            )
            rows = cursor.fetchall()
            cursor.execute(
                """
                SELECT c.name AS column_name
                FROM sys.columns AS c
                INNER JOIN sys.objects AS o ON o.object_id = c.object_id
                INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
                WHERE s.name = N'ctl'
                  AND o.name = N'MddMacTargetAudit'
                  AND c.name IN (N'SamplingSlot', N'SelectionPolicyVersion')
                """
            )
            column_rows = cursor.fetchall()

        observed_objects = {
            str(_row_value(row, "object_name", 0)): str(_row_named(row, "object_type", index=1) or "")
            for row in rows
        }
        missing = [name for name in runtime_names if name not in observed_objects]
        if missing:
            raise MssqlSchemaError(
                "production runtime objects are missing or not visible to the dedicated login: "
                + ", ".join(missing)
            )
        wrong_types = [
            name
            for name in RUNTIME_SCHEMA_TABLES
            if observed_objects.get(name) not in {"U", "U "}
        ] + [
            name
            for name in RUNTIME_SCHEMA_ROUTINES
            if observed_objects.get(name) not in {"P", "P "}
        ]
        if wrong_types:
            raise MssqlSchemaError(
                "production runtime objects have unexpected SQL types: " + ", ".join(wrong_types)
            )
        observed_columns = {str(_row_named(row, "column_name", index=0)) for row in column_rows}
        required_columns = {"SamplingSlot", "SelectionPolicyVersion"}
        missing_columns = sorted(required_columns - observed_columns)
        if missing_columns:
            raise MssqlSchemaError(
                "Legacy7 audit lineage columns are missing or not visible to the dedicated login: "
                + ", ".join(missing_columns)
            )
        return {
            "status": "PASS",
            "validation_scope": "PRODUCTION_RUNTIME",
            "expected_table_count": len(RUNTIME_SCHEMA_TABLES),
            "expected_routine_count": len(RUNTIME_SCHEMA_ROUTINES),
            "required_legacy7_audit_columns": sorted(required_columns),
            "unrelated_import_objects_required": False,
            "missing": [],
        }

    def validate_tiered50_runtime_schema(self) -> Mapping[str, Any]:
        """Validate the additional object surface used only by Tiered50.

        Legacy7 startup deliberately does not require this table.  That keeps
        an additive, prepared migration from taking the established writer
        offline while Tiered50 is still deferred.
        """

        required_objects = tuple(RUNTIME_SCHEMA_TABLES) + (TIERED50_TARGET_TABLE,)
        names = ", ".join(
            "N'" + name.split(".", 1)[1].replace("'", "''") + "'"
            for name in required_objects
            if name.startswith("ctl.")
        )
        with self._connection() as connection:
            cursor = self._cursor(connection)
            cursor.execute(
                """
                SELECT s.name + N'.' + o.name AS object_name, o.type AS object_type
                FROM sys.objects AS o
                INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
                WHERE s.name = N'ctl' AND o.name IN ("""
                + names
                + ")"
            )
            rows = cursor.fetchall()
            cursor.execute(
                """
                SELECT s.name + N'.' + o.name AS object_name, c.name AS column_name
                FROM sys.columns AS c
                INNER JOIN sys.objects AS o ON o.object_id = c.object_id
                INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
                WHERE s.name = N'ctl'
                  AND o.name IN (N'MddMacTiered50ProductionTarget',
                                 N'MddMacTargetClaim',
                                 N'MddMacTargetClaimEvent')
                """
            )
            column_rows = cursor.fetchall()
        observed_objects = {
            str(_row_value(row, "object_name", 0)): str(_row_named(row, "object_type", index=1) or "")
            for row in rows
        }
        missing = [name for name in required_objects if name not in observed_objects]
        if missing:
            raise MssqlSchemaError(
                "Tiered50 production runtime objects are missing or not visible: "
                + ", ".join(missing)
            )
        wrong_types = [
            name
            for name in required_objects
            if observed_objects.get(name) not in {"U", "U "}
        ]
        if wrong_types:
            raise MssqlSchemaError(
                "Tiered50 production runtime objects have unexpected SQL types: "
                + ", ".join(wrong_types)
            )
        required_columns = {
            "SamplingTargetId",
            "SourceParentStationId",
            "ParentStopPointRef",
            "PanelVersion",
            "SamplingEra",
            "Tier",
            "PhaseIndex",
            "PhaseCount",
            "CadenceMinutes",
            "NumberOfResults",
            "ContinuityClassification",
            "IsEnabled",
            "ActivationStatus",
        }
        observed_columns = {
            (
                str(_row_named(row, "object_name", index=0)),
                str(_row_named(row, "column_name", index=1)),
            )
            for row in column_rows
        }
        required_column_pairs = {
            (TIERED50_TARGET_TABLE, column) for column in required_columns
        } | {
            ("ctl.MddMacTargetClaim", "SamplingTargetId"),
            ("ctl.MddMacTargetClaimEvent", "SamplingTargetId"),
        }
        missing_columns = sorted(
            f"{object_name}.{column}"
            for object_name, column in required_column_pairs
            if (object_name, column) not in observed_columns
        )
        if missing_columns:
            raise MssqlSchemaError(
                "Tiered50 production target columns are missing or not visible: "
                + ", ".join(missing_columns)
            )
        return {
            "status": "PASS",
            "validation_scope": "TIERED50_PRODUCTION_RUNTIME",
            "expected_table_count": len(required_objects),
            "expected_routine_count": len(RUNTIME_SCHEMA_ROUTINES),
            "tiered50_target_table": TIERED50_TARGET_TABLE,
            "required_target_columns": sorted(required_columns),
            "required_claim_identity_columns": [
                "ctl.MddMacTargetClaim.SamplingTargetId",
                "ctl.MddMacTargetClaimEvent.SamplingTargetId",
            ],
            "missing": [],
        }

    def validate_schema(self) -> Mapping[str, Any]:
        """Backward-compatible alias for full/admin validation.

        New code must choose the explicit ``validate_full_schema`` or
        ``validate_runtime_schema`` path.  Keeping this alias avoids silently
        changing older administrator/import callers.
        """

        return self.validate_full_schema()

    def baseline(self) -> Mapping[str, Any]:
        """Read-only baseline for cutover evidence; no migration or writes."""

        with self._connection() as connection:
            cursor = self._cursor(connection)
            cursor.execute(
                """
                SELECT
                    (SELECT COUNT_BIG(*) FROM ctl.MddCollectorRun) AS collector_runs,
                    (SELECT COUNT_BIG(*) FROM ctl.MddRealtimeSamplingTarget) AS sampling_targets,
                    (SELECT COUNT_BIG(*) FROM stg.MddRealtimeStopObservation) AS stop_observations,
                    (SELECT COUNT_BIG(*) FROM stg.MddRealtimeSituationObservation) AS situation_observations,
                    (SELECT COUNT_BIG(*) FROM stg.MddRealtimeStopSituationLink) AS stop_situation_links,
                    (SELECT MAX(ObservedAtUtc) FROM stg.MddRealtimeStopObservation) AS latest_stop_observation_utc,
                    (SELECT MAX(ObservedAtUtc) FROM stg.MddRealtimeSituationObservation) AS latest_situation_observation_utc
                """
            )
            counts = cursor.fetchone()
            cursor.execute(
                "SELECT SamplingTargetId FROM ctl.MddRealtimeSamplingTarget ORDER BY SamplingTargetId"
            )
            target_rows = cursor.fetchall()
            cursor.execute(
                """
                SELECT TOP (1) CollectorRunId, StartedAtUtc, CompletedAtUtc, Status,
                              SamplingMode, SamplingTargetId, SamplingSlot, ObservedAtUtc,
                              HttpStatus, HttpAttempts
                FROM ctl.MddCollectorRun
                ORDER BY StartedAtUtc DESC, CollectorRunId DESC
                """
            )
            latest_run = cursor.fetchone()
        target_ids = [int(_row_value(row, "SamplingTargetId", 0)) for row in target_rows]
        return {
            "collector_runs": int(_row_value(counts, "collector_runs", 0) or 0),
            "sampling_targets": int(_row_value(counts, "sampling_targets", 1) or 0),
            "stop_observations": int(_row_value(counts, "stop_observations", 2) or 0),
            "situation_observations": int(_row_value(counts, "situation_observations", 3) or 0),
            "stop_situation_links": int(_row_value(counts, "stop_situation_links", 4) or 0),
            "latest_stop_observation_utc": _row_value(counts, "latest_stop_observation_utc", 5),
            "latest_situation_observation_utc": _row_value(counts, "latest_situation_observation_utc", 6),
            "sampling_target_ids": target_ids,
            "latest_collector_run": dict(latest_run) if isinstance(latest_run, Mapping) else latest_run,
        }

    def runtime_login_status(self) -> Mapping[str, Any]:
        """Return non-secret identity/privilege metadata for the active login."""

        with self._connection() as connection:
            cursor = self._cursor(connection)
            cursor.execute(
                """
                SELECT DB_NAME() AS database_name,
                       SUSER_SNAME() AS login_name,
                       IS_SRVROLEMEMBER(N'sysadmin') AS is_sysadmin,
                       IS_SRVROLEMEMBER(N'serveradmin') AS is_serveradmin,
                       IS_SRVROLEMEMBER(N'securityadmin') AS is_securityadmin,
                       IS_ROLEMEMBER(N'db_owner') AS is_db_owner,
                       IS_ROLEMEMBER(N'db_datareader') AS is_db_datareader,
                       IS_ROLEMEMBER(N'db_datawriter') AS is_db_datawriter,
                       CAST(SERVERPROPERTY('ServerName') AS NVARCHAR(128)) AS server_name,
                       CAST(SERVERPROPERTY('MachineName') AS NVARCHAR(128)) AS machine_name,
                       CAST(SERVERPROPERTY('InstanceName') AS NVARCHAR(128)) AS instance_name,
                       CAST(@@SERVERNAME AS NVARCHAR(128)) AS configured_server_name
                """
            )
            row = cursor.fetchone()
        return {
            "database_name": str(_row_named(row, "database_name", index=0) or ""),
            "login_name": str(_row_named(row, "login_name", index=1) or ""),
            "is_sysadmin": bool(_row_named(row, "is_sysadmin", index=2)),
            "is_serveradmin": bool(_row_named(row, "is_serveradmin", index=3)),
            "is_securityadmin": bool(_row_named(row, "is_securityadmin", index=4)),
            "is_db_owner": bool(_row_named(row, "is_db_owner", index=5)),
            "is_db_datareader": bool(_row_named(row, "is_db_datareader", index=6)),
            "is_db_datawriter": bool(_row_named(row, "is_db_datawriter", index=7)),
            "server_name": str(_row_named(row, "server_name", index=8) or ""),
            "machine_name": str(_row_named(row, "machine_name", index=9) or ""),
            "instance_name": str(_row_named(row, "instance_name", index=10) or ""),
            "configured_server_name": str(_row_named(row, "configured_server_name", index=11) or ""),
        }

    def runtime_privilege_boundary(self) -> Mapping[str, Any]:
        """Verify that the active login has no administrator or unrelated access."""

        with self._connection() as connection:
            cursor = self._cursor(connection)
            cursor.execute(
                """
                SELECT HAS_PERMS_BY_NAME(NULL, NULL, N'CONTROL SERVER') AS control_server,
                       HAS_PERMS_BY_NAME(NULL, NULL, N'ALTER ANY LOGIN') AS alter_any_login,
                       HAS_PERMS_BY_NAME(NULL, NULL, N'CREATE ANY DATABASE') AS create_any_database,
                       HAS_PERMS_BY_NAME(N'ctl.MddMacTargetAudit', N'OBJECT', N'CONTROL') AS audit_control,
                       HAS_PERMS_BY_NAME(N'ctl.MddMacTargetAudit', N'OBJECT', N'ALTER') AS audit_alter,
                       HAS_PERMS_BY_NAME(N'ctl.MddMacTargetAudit', N'OBJECT', N'DELETE') AS audit_delete,
                       HAS_PERMS_BY_NAME(N'ctl.MddMacSharedQuotaReconciliation', N'OBJECT', N'SELECT') AS quota_history_select,
                       HAS_PERMS_BY_NAME(N'ctl.MddMacSharedQuotaReconciliation', N'OBJECT', N'INSERT') AS quota_history_insert,
                       HAS_PERMS_BY_NAME(N'analytics.vwMddMacRecordedAtTimeHistory', N'OBJECT', N'SELECT') AS analytics_history_select,
                       HAS_PERMS_BY_NAME(N'analytics.vwMddMacSourceFieldCoverage', N'OBJECT', N'SELECT') AS analytics_coverage_select,
                       HAS_PERMS_BY_NAME(N'ctl.MddImportBatch', N'OBJECT', N'SELECT') AS import_batch_select
                """
            )
            row = cursor.fetchone()
        result = {
            "control_server": bool(_row_named(row, "control_server", index=0)),
            "alter_any_login": bool(_row_named(row, "alter_any_login", index=1)),
            "create_any_database": bool(_row_named(row, "create_any_database", index=2)),
            "audit_control": bool(_row_named(row, "audit_control", index=3)),
            "audit_alter": bool(_row_named(row, "audit_alter", index=4)),
            "audit_delete": bool(_row_named(row, "audit_delete", index=5)),
            "quota_history_select": bool(_row_named(row, "quota_history_select", index=6)),
            "quota_history_insert": bool(_row_named(row, "quota_history_insert", index=7)),
            "analytics_history_select": bool(_row_named(row, "analytics_history_select", index=8)),
            "analytics_coverage_select": bool(_row_named(row, "analytics_coverage_select", index=9)),
            "import_batch_select": bool(_row_named(row, "import_batch_select", index=10)),
        }
        forbidden = [name for name, granted in result.items() if granted]
        if forbidden:
            raise MssqlSafetyError(
                "dedicated runtime login has prohibited effective permissions: " + ", ".join(sorted(forbidden))
            )
        result["status"] = "PASS"
        return result

    def load_existing_sampling_targets(self) -> tuple[Target, ...]:
        """Read the unchanged seven-target and ten-slot Legacy7 contract."""

        with self._connection() as connection:
            cursor = self._cursor(connection)
            cursor.execute(
                """
                SELECT SamplingTargetId, StopPointRef, TargetName, NumberOfResults, IsEnabled
                FROM ctl.MddRealtimeSamplingTarget
                ORDER BY SamplingTargetId
                """
            )
            rows = cursor.fetchall()
            cursor.execute(
                """
                SELECT SamplingSlot, SamplingTargetId
                FROM ctl.MddRealtimeSamplingSlot
                ORDER BY SamplingSlot
                """
            )
            slot_rows = cursor.fetchall()
        normalized: list[tuple[int, str, str, int, bool]] = []
        for row in rows:
            target_id = int(_row_value(row, "SamplingTargetId", 0))
            stop_ref = str(_row_value(row, "StopPointRef", 1) or "")
            name = str(_row_value(row, "TargetName", 2) or stop_ref)
            number_of_results = int(_row_value(row, "NumberOfResults", 3) or 0)
            enabled = bool(_row_value(row, "IsEnabled", 4))
            normalized.append((target_id, stop_ref, name, number_of_results, enabled))
        if {target_id for target_id, *_ in normalized} != set(range(1, 8)):
            raise MssqlSafetyError("existing sampling target IDs are not exactly 1 through 7")
        if len(normalized) != 7 or any(not enabled for *_, enabled in normalized):
            raise MssqlSafetyError("the existing seven sampling targets are not all enabled")
        if any(LEGACY7_SAMPLING_TARGET_IDS.get(stop_ref) != target_id for target_id, stop_ref, *_ in normalized):
            raise MssqlSafetyError("existing sampling target identity mapping changed")
        if any(number_of_results <= 0 for *_, number_of_results, _ in normalized):
            raise MssqlSafetyError("existing sampling target result limits are invalid")
        try:
            slot_mapping = {
                int(_row_named(row, "SamplingSlot", "Slot", index=0)):
                int(_row_named(row, "SamplingTargetId", "TargetId", index=1))
                for row in slot_rows
            }
        except (TypeError, ValueError) as exc:
            raise MssqlSafetyError("existing Legacy7 sampling slots contain invalid IDs") from exc
        if len(slot_rows) != 10 or len(slot_mapping) != 10:
            raise MssqlSafetyError("existing Legacy7 sampling slots are not exactly ten unique rows")
        try:
            validate_legacy7_slot_mapping(slot_mapping)
        except (TypeError, ValueError) as exc:
            raise MssqlSafetyError("existing Legacy7 sampling-slot mapping changed") from exc
        slots_by_target: dict[int, list[int]] = {target_id: [] for target_id, *_ in normalized}
        for slot, target_id in sorted(slot_mapping.items()):
            if target_id not in slots_by_target:
                raise MssqlSafetyError("Legacy7 sampling slot references an unknown target")
            slots_by_target[target_id].append(slot)
        return tuple(
            Target(
                panel_version="MDD_MAC_LEGACY7_EXISTING_V1",
                source_parent_station_id=stop_ref,
                station_name=name,
                tier="Legacy7",
                cadence_minutes=5,
                number_of_results=number_of_results,
                phase_index=min(slots_by_target[target_id]) - 1,
                phase_count=10,
                continuity_classification="Existing SQL sampling target",
                sampling_era=SamplingEra.LEGACY7,
                legacy_sampling_target_id=target_id,
                phase_capacity=len(slots_by_target[target_id]),
                legacy_sampling_slots=tuple(slots_by_target[target_id]),
                selection_policy_version=LEGACY7_SELECTION_POLICY_VERSION,
            )
            for target_id, stop_ref, name, number_of_results, _enabled in sorted(normalized)
        )

    def load_tiered50_production_targets(
        self,
        manifest: RuntimeManifest,
    ) -> tuple[Target, ...]:
        """Load active SQL-bound Tiered50 rows and bind them to the manifest.

        The seven historical target rows are read from their original table;
        only the 43 new rows come from the additive Tiered50 table.  No SQL
        identity, membership, phase, or panel value is inferred from row order.
        """

        manifest.validate()
        existing = self.load_existing_sampling_targets()
        existing_by_id = {
            int(target.legacy_sampling_target_id or 0): target for target in existing
        }
        if set(existing_by_id) != set(range(1, 8)):
            raise MssqlSafetyError("Legacy7 SamplingTargetIds 1 through 7 are not preserved")

        with self._connection() as connection:
            cursor = self._cursor(connection)
            cursor.execute(
                f"""
                SELECT SamplingTargetId, SourceParentStationId, ParentStopPointRef,
                       TargetName, PanelVersion, SamplingEra, Tier, PhaseIndex,
                       PhaseCount, CadenceMinutes, NumberOfResults,
                       ContinuityClassification, IsEnabled, ActivationStatus
                FROM {TIERED50_TARGET_TABLE}
                WHERE PanelVersion = %s
                  AND SamplingEra = %s
                  AND IsEnabled = 1
                  AND ActivationStatus = 'ACTIVE'
                ORDER BY SamplingTargetId
                """,
                (manifest.panel_version, SamplingEra.TIERED50.value),
            )
            rows = list(cursor.fetchall())

        if len(rows) != 43:
            raise MssqlSafetyError(
                "active Tiered50 materialization must contain exactly 43 new targets"
            )
        expected_new_ids = set(range(8, 51))
        observed_ids: set[int] = set()
        observed_sources: set[str] = set()
        manifest_by_source = {
            target.source_parent_station_id: target for target in manifest.targets
        }
        production_ids: dict[str, int] = {}
        for row in rows:
            try:
                sampling_id = int(_row_named(row, "SamplingTargetId", index=0))
                source_parent = str(
                    _row_named(row, "SourceParentStationId", index=1) or ""
                )
                parent_ref = str(
                    _row_named(row, "ParentStopPointRef", index=2) or ""
                )
            except (TypeError, ValueError) as exc:
                raise MssqlSafetyError("Tiered50 target materialization contains invalid identity") from exc
            if sampling_id in observed_ids or sampling_id not in expected_new_ids:
                raise MssqlSafetyError("Tiered50 production IDs must be unique and exactly 8 through 50")
            if source_parent in observed_sources or source_parent not in manifest_by_source:
                raise MssqlSafetyError("Tiered50 materialization contains an unknown or duplicate parent station")
            approved = manifest_by_source[source_parent]
            if approved.is_legacy7_member:
                raise MssqlSafetyError("Tiered50 materialization must contain only the 43 new stations")
            expected_values = {
                "ParentStopPointRef": approved.source_parent_station_id,
                "TargetName": approved.station_name,
                "PanelVersion": approved.panel_version,
                "SamplingEra": SamplingEra.TIERED50.value,
                "Tier": approved.tier,
                "PhaseIndex": approved.phase_index,
                "PhaseCount": approved.phase_count,
                "CadenceMinutes": approved.cadence_minutes,
                "NumberOfResults": approved.number_of_results,
                "ContinuityClassification": approved.continuity_classification,
            }
            observed_values = {
                "ParentStopPointRef": parent_ref,
                "TargetName": str(_row_named(row, "TargetName", index=3) or ""),
                "PanelVersion": str(_row_named(row, "PanelVersion", index=4) or ""),
                "SamplingEra": str(_row_named(row, "SamplingEra", index=5) or ""),
                "Tier": str(_row_named(row, "Tier", index=6) or ""),
                "PhaseIndex": int(_row_named(row, "PhaseIndex", index=7)),
                "PhaseCount": int(_row_named(row, "PhaseCount", index=8)),
                "CadenceMinutes": int(_row_named(row, "CadenceMinutes", index=9)),
                "NumberOfResults": int(_row_named(row, "NumberOfResults", index=10)),
                "ContinuityClassification": str(
                    _row_named(row, "ContinuityClassification", index=11) or ""
                ),
            }
            if observed_values != expected_values:
                raise MssqlSafetyError(
                    "Tiered50 SQL target fields differ from the approved manifest for "
                    + source_parent
                )
            observed_ids.add(sampling_id)
            observed_sources.add(source_parent)
            production_ids[source_parent] = sampling_id
        if observed_ids != expected_new_ids:
            raise MssqlSafetyError("Tiered50 production IDs are not exactly 8 through 50")

        for target in manifest.legacy7_targets:
            legacy_id = int(target.legacy_sampling_target_id or 0)
            if legacy_id not in existing_by_id:
                raise MssqlSafetyError("Legacy7 target identity disappeared during Tiered50 binding")
            production_ids[target.source_parent_station_id] = legacy_id
        bound = manifest.bind_production_sampling_target_ids(production_ids)
        return tuple(
            replace(target, sampling_era=SamplingEra.TIERED50)
            for target in bound.targets
        )

    def legacy7_selector_definition(self) -> Mapping[str, Any]:
        """Capture the existing selector definition and parameter metadata read-only."""

        with self._connection() as connection:
            cursor = self._cursor(connection)
            cursor.execute(
                """
                SELECT p.parameter_id, p.name, TYPE_NAME(p.user_type_id) AS type_name,
                       p.max_length, p.is_output
                FROM sys.parameters AS p
                INNER JOIN sys.objects AS o ON o.object_id = p.object_id
                INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
                WHERE s.name = N'ctl' AND o.name = N'uspGetMddRealtimeSamplingTarget'
                  AND p.parameter_id > 0
                ORDER BY p.parameter_id
                """
            )
            parameters = cursor.fetchall()
            cursor.execute(
                """
                SELECT OBJECT_DEFINITION(OBJECT_ID(N'ctl.uspGetMddRealtimeSamplingTarget', N'P'))
                    AS procedure_definition
                """
            )
            definition_row = cursor.fetchone()
        definition = _row_named(definition_row, "procedure_definition", index=0)
        if not definition:
            raise MssqlSchemaError("Legacy7 selector definition is unavailable")
        return {
            "procedure_name": "ctl.uspGetMddRealtimeSamplingTarget",
            "offline_bucket_rule": LEGACY7_BUCKET_SLOT_RULE,
            "parameters": [
                {
                    "parameter_id": int(_row_named(row, "parameter_id", index=0) or 0),
                    "name": str(_row_named(row, "name", index=1) or ""),
                    "type_name": str(_row_named(row, "type_name", index=2) or ""),
                    "max_length": int(_row_named(row, "max_length", index=3) or 0),
                    "is_output": bool(_row_named(row, "is_output", index=4)),
                }
                for row in parameters
            ],
            "definition": str(definition or ""),
        }

    def select_existing_legacy7_target(
        self,
        *,
        at_utc: datetime,
        targets: Sequence[Target] | None = None,
    ) -> Target:
        """Use the established SQL selector for exactly one current heartbeat."""

        selected_at = _utc(at_utc)
        target_rows = tuple(targets or self.load_existing_sampling_targets())
        metadata = self.legacy7_selector_definition()
        parameters = metadata["parameters"]
        with self._connection() as connection:
            cursor = self._cursor(connection)
            if len(parameters) == 0:
                cursor.execute("EXEC ctl.uspGetMddRealtimeSamplingTarget")
            elif len(parameters) == 1:
                parameter_name = str(parameters[0]["name"])
                if not re.fullmatch(r"@[A-Za-z_][A-Za-z0-9_]*", parameter_name):
                    raise MssqlSafetyError("Legacy7 selector parameter name is not safe to call")
                parameter_type = str(parameters[0].get("type_name", "")).casefold()
                parameter_value: Any
                logical_bucket = int(selected_at.timestamp() // 300)
                if any(marker in parameter_type for marker in ("date", "time")):
                    parameter_value = selected_at
                elif parameter_type in {"tinyint", "smallint", "int", "bigint"}:
                    parameter_value = logical_bucket
                else:
                    raise MssqlSafetyError("Legacy7 selector parameter type is unsupported")
                cursor.execute(
                    f"EXEC ctl.uspGetMddRealtimeSamplingTarget {parameter_name}=%s",
                    (parameter_value,),
                )
            else:
                raise MssqlSafetyError("Legacy7 selector has an unsupported parameter contract")
            rows = cursor.fetchall()
            description = getattr(cursor, "description", None) or ()
            if description:
                column_names = [str(column[0]) for column in description]
                rows = [
                    {
                        column_names[index]: value
                        for index, value in enumerate(row)
                    }
                    if not isinstance(row, Mapping)
                    else row
                    for row in rows
                ]
        if len(rows) != 1:
            raise MssqlSafetyError("Legacy7 selector must return exactly one target row")
        row = rows[0]
        expected_selection = legacy7_selection_for_bucket(
            int(selected_at.timestamp() // 300)
        )
        target_value = _row_named(
            row,
            "SamplingTargetId",
            "TargetId",
            "LegacySamplingTargetId",
            index=0,
        )
        try:
            target_id = int(target_value or 0)
        except (TypeError, ValueError) as exc:
            raise MssqlSafetyError("Legacy7 selector returned an invalid target") from exc
        slot_value = _row_named(
            row,
            "SamplingSlot",
            "Slot",
            "SamplingSlotNumber",
            "SlotNumber",
            index=1,
        )
        try:
            sampling_slot = int(slot_value) if slot_value is not None else expected_selection.sampling_slot
        except (TypeError, ValueError) as exc:
            raise MssqlSafetyError("Legacy7 selector returned an invalid slot") from exc
        if target_id not in {1, 2, 3, 4, 5, 6, 7} or sampling_slot not in range(1, 11):
            raise MssqlSafetyError("Legacy7 selector returned an invalid target or slot")
        if (
            sampling_slot != expected_selection.sampling_slot
            or target_id != expected_selection.sampling_target_id
            or LEGACY7_SLOT_TO_SAMPLING_TARGET_ID[sampling_slot] != target_id
        ):
            raise MssqlSafetyError(
                "live Legacy7 selector does not match the approved deterministic UTC slot contract"
            )
        matching = [
            target for target in target_rows if target.legacy_sampling_target_id == target_id
        ]
        if len(matching) != 1:
            raise MssqlSafetyError("Legacy7 selector returned a target outside the approved seven-target scope")
        target = matching[0]
        if sampling_slot not in target.legacy_sampling_slots:
            raise MssqlSafetyError("Legacy7 selector returned a target outside its slot lineage")
        return replace(
            target,
            selected_sampling_slot=sampling_slot,
            selection_policy_version=LEGACY7_SELECTION_POLICY_VERSION,
        )

    def assert_no_competing_writer(self, *, collection_context: str) -> Mapping[str, Any]:
        """Fail closed when the other production Mac writer is still running."""

        context = str(collection_context)
        if context not in {
            MAC_SQL_COLLECTION_CONTEXT,
            MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT,
        }:
            raise MssqlSafetyError("unknown production collection context")
        other = (
            MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT
            if context == MAC_SQL_COLLECTION_CONTEXT
            else MAC_SQL_COLLECTION_CONTEXT
        )
        with self._connection() as connection:
            cursor = self._cursor(connection)
            cursor.execute(
                """
                SELECT CollectionContext, COUNT_BIG(*) AS RunningCount
                FROM ctl.MddMacCollectorRun WITH (READCOMMITTED)
                WHERE Status IN ('RUNNING', 'CLAIMED')
                  AND CollectionContext = %s
                GROUP BY CollectionContext
                """,
                (other,),
            )
            row = cursor.fetchone()
        running = int(_row_named(row, "RunningCount", index=1) or 0) if row else 0
        if running:
            raise MssqlSafetyError(
                f"competing production writer is active: {other} ({running})"
            )
        return {
            "status": "PASS",
            "collection_context": context,
            "competing_context": other,
            "competing_running_count": running,
        }

    def reconcile_live_legacy7_selector(
        self,
        *,
        reference_bucket: int | None = None,
        targets: Sequence[Target] | None = None,
    ) -> Mapping[str, Any]:
        """Prove the live SQL bucket rule against ten adjacent buckets.

        The live procedure is authoritative.  This read-only gate calls it
        with ten explicit bucket instants (for a one-parameter contract) and
        compares the returned target/slot to the offline rule.  A parameterless
        procedure cannot be tested for arbitrary buckets without waiting for
        wall-clock rollover, so it fails closed rather than claiming that the
        repository constant was live evidence.
        """

        metadata = self.legacy7_selector_definition()
        parameters = metadata["parameters"]
        if len(parameters) != 1:
            raise MssqlSafetyError(
                "live Legacy7 selector reconciliation requires exactly one explicit bucket/time parameter"
            )
        target_rows = tuple(targets or self.load_existing_sampling_targets())
        base_bucket = int(
            reference_bucket
            if reference_bucket is not None
            else datetime.now(timezone.utc).timestamp() // 300
        )
        observations: list[dict[str, Any]] = []
        for offset in range(10):
            bucket = base_bucket + offset
            selected = self.select_existing_legacy7_target(
                at_utc=datetime.fromtimestamp(bucket * 300, tz=timezone.utc),
                targets=target_rows,
            )
            observations.append(
                {
                    "logical_due_bucket": bucket,
                    "sampling_slot": selected.selected_sampling_slot,
                    "sampling_target_id": selected.legacy_sampling_target_id,
                    "expected_sampling_slot": legacy7_selection_for_bucket(bucket).sampling_slot,
                    "expected_sampling_target_id": legacy7_selection_for_bucket(bucket).sampling_target_id,
                }
            )
        if any(
            row["sampling_slot"] != row["expected_sampling_slot"]
            or row["sampling_target_id"] != row["expected_sampling_target_id"]
            for row in observations
        ):
            raise MssqlSafetyError("live Legacy7 selector arithmetic differs from the approved UTC bucket rule")
        return {
            "status": "PASS",
            "procedure_name": metadata["procedure_name"],
            "parameter_metadata": metadata["parameters"],
            "procedure_definition": metadata["definition"],
            "offline_rule_label": LEGACY7_BUCKET_SLOT_RULE,
            "reference_bucket": base_bucket,
            "observed_bucket_to_slot": observations,
            "authoritative_mapping": dict(LEGACY7_SLOT_TO_SAMPLING_TARGET_ID),
        }

    def start_collector_run(
        self,
        *,
        run_id: str,
        panel_version: str,
        logical_due_bucket: int,
        mode: str,
        started_at: datetime,
        collection_context: str | None = None,
    ) -> None:
        context = str(collection_context or self.collection_context)
        started = _utc(started_at)
        lease_required = context in {
            MAC_SQL_COLLECTION_CONTEXT,
            MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT,
        }
        with self._connection() as connection:
            cursor = self._cursor(connection)
            try:
                cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
                cursor.execute(
                    """
                    INSERT INTO ctl.MddMacCollectorRun
                        (RunId, PanelVersion, LogicalDueBucket, Mode, CollectionContext, StartedAtUtc)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(run_id),
                        panel_version,
                        int(logical_due_bucket),
                        mode,
                        context,
                        started,
                    ),
                )
                if lease_required:
                    lease_token = uuid.uuid4()
                    expires = started + timedelta(seconds=600)
                    cursor.execute(
                        """
                        SELECT LeaseName, RunId, LeaseToken, ExpiresAtUtc, Status
                        FROM ctl.MddMacProductionLease WITH (UPDLOCK, HOLDLOCK)
                        WHERE LeaseName = %s
                        """,
                        (PRODUCTION_LEASE_NAME,),
                    )
                    existing = cursor.fetchone()
                    existing_status = str(_row_named(existing, "Status", index=4) or "") if existing else ""
                    existing_expires = _optional_utc(_row_named(existing, "ExpiresAtUtc", index=3)) if existing else None
                    existing_run_id = str(_row_named(existing, "RunId", index=1) or "") if existing else None
                    if existing_status == "ACTIVE" and existing_expires is not None and existing_expires > started:
                        raise MssqlSafetyError(
                            "durable production lease is already held"
                            + (f" by run {existing_run_id}" if existing_run_id else "")
                        )

                    if existing is None:
                        cursor.execute(
                            """
                            INSERT INTO ctl.MddMacProductionLease
                                (LeaseName, CollectionContext, RunId, LeaseToken,
                                 AcquiredAtUtc, ExpiresAtUtc, LastHeartbeatAtUtc, Status)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, 'ACTIVE')
                            """,
                            (
                                PRODUCTION_LEASE_NAME,
                                context,
                                str(run_id),
                                lease_token,
                                started,
                                expires,
                                started,
                            ),
                        )
                        lease_outcome = "ACQUIRED"
                    else:
                        cursor.execute(
                            """
                            UPDATE ctl.MddMacProductionLease
                            SET CollectionContext = %s,
                                RunId = %s,
                                LeaseToken = %s,
                                AcquiredAtUtc = %s,
                                ExpiresAtUtc = %s,
                                LastHeartbeatAtUtc = %s,
                                Status = 'ACTIVE',
                                ReleasedAtUtc = NULL
                            WHERE LeaseName = %s
                            """,
                            (
                                context,
                                str(run_id),
                                lease_token,
                                started,
                                expires,
                                started,
                                PRODUCTION_LEASE_NAME,
                            ),
                        )
                        lease_outcome = "RECLAIMED_STALE"
                    cursor.execute(
                        """
                        INSERT INTO ctl.MddMacProductionLeaseEvent
                            (LeaseName, CollectionContext, RunId, EventAtUtc,
                             Outcome, PreviousRunId, PreviousStatus)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            PRODUCTION_LEASE_NAME,
                            context,
                            str(run_id),
                            started,
                            lease_outcome,
                            existing_run_id,
                            existing_status or None,
                        ),
                    )
                cursor.execute("COMMIT TRANSACTION;")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def claim_dispatcher_bucket(
        self,
        *,
        run_id: str,
        panel_version: str,
        logical_due_bucket: int,
        claimed_at: datetime,
    ) -> MssqlDispatcherClaimResult:
        claimed = _utc(claimed_at)
        stale_after = claimed + timedelta(seconds=300)
        token = str(uuid.uuid4())
        key = (panel_version, int(logical_due_bucket))
        with self._connection() as connection:
            cursor = self._cursor(connection)
            try:
                cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
                cursor.execute(
                    """
                    SELECT Status, StaleAfterUtc, RunId
                    FROM ctl.MddMacDispatcherClaim WITH (UPDLOCK, HOLDLOCK)
                    WHERE PanelVersion = %s AND LogicalDueBucket = %s
                    """,
                    key,
                )
                existing = cursor.fetchone()
                existing_stale_after = _optional_utc(_row_named(existing, "StaleAfterUtc", index=1)) if existing else None
                existing_status = _row_value(existing, "Status", 0) if existing else None
                existing_run_id = str(_row_named(existing, "RunId", index=2) or "") if existing else None
                reclaim = bool(
                    existing
                    and str(existing_status or "") in {"CLAIMED", "RUNNING"}
                    and existing_stale_after is not None
                    and existing_stale_after <= claimed
                )
                won = existing is None or reclaim
                if won:
                    if existing is None:
                        cursor.execute(
                            """
                            INSERT INTO ctl.MddMacDispatcherClaim
                                (PanelVersion, LogicalDueBucket, RunId, ClaimToken,
                                 ClaimedAtUtc, StaleAfterUtc, Status)
                            VALUES (%s, %s, %s, %s, %s, %s, 'CLAIMED')
                            """,
                            (*key, run_id, token, claimed, stale_after),
                        )
                    else:
                        cursor.execute(
                            """
                            UPDATE ctl.MddMacDispatcherClaim
                            SET RunId = %s, ClaimToken = %s, ClaimedAtUtc = %s,
                                StaleAfterUtc = %s, Status = 'CLAIMED', CompletedAtUtc = NULL
                            WHERE PanelVersion = %s AND LogicalDueBucket = %s
                            """,
                            (run_id, token, claimed, stale_after, *key),
                        )
                cursor.execute(
                    """
                    INSERT INTO ctl.MddMacDispatcherClaimEvent
                        (PanelVersion, LogicalDueBucket, RunId, EventAtUtc, Outcome, ExistingStatus)
                    VALUES (%s, %s, %s, %s, %s, %s);
                    SELECT CONVERT(BIGINT, SCOPE_IDENTITY()) AS DispatcherClaimEventId;
                    """,
                    (
                        *key,
                        run_id,
                        claimed,
                        "CLAIMED" if existing is None else ("RECLAIMED_STALE_DISPATCHER_CLAIM" if reclaim else "DUPLICATE_DISPATCHER_CLAIM"),
                        existing_status,
                    ),
                )
                event_id = int(_row_value(cursor.fetchone(), "DispatcherClaimEventId", 0))
                cursor.execute("COMMIT TRANSACTION;")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return MssqlDispatcherClaimResult(won, key, str(existing_status) if existing_status else None, token if won else None, event_id)

    def claim_target(
        self,
        *,
        run_id: str,
        target: Target,
        logical_due_bucket: int,
        claimed_at: datetime,
    ) -> MssqlClaimResult:
        claimed = _utc(claimed_at)
        stale_after = claimed + timedelta(seconds=300)
        token = str(uuid.uuid4())
        tiered50 = target.sampling_era is SamplingEra.TIERED50
        sampling_target_id = _target_sampling_id(target) if tiered50 else None
        if tiered50 and sampling_target_id is None:
            raise MssqlSafetyError("Tiered50 target claims require a stable SamplingTargetId")
        key = (
            target.panel_version,
            sampling_target_id if tiered50 else target.source_parent_station_id,
            int(logical_due_bucket),
        )
        with self._connection() as connection:
            cursor = self._cursor(connection)
            try:
                cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
                if tiered50:
                    cursor.execute(
                        """
                        SELECT Status, StaleAfterUtc, RunId
                        FROM ctl.MddMacTargetClaim WITH (UPDLOCK, HOLDLOCK)
                        WHERE PanelVersion = %s AND SamplingTargetId = %s AND LogicalDueBucket = %s
                        """,
                        (target.panel_version, sampling_target_id, int(logical_due_bucket)),
                    )
                else:
                    # Keep the already-active Legacy7 writer compatible with
                    # its existing claim table.  The additive migration adds
                    # SamplingTargetId for the separately guarded Tiered50
                    # path; Legacy7 remains keyed by its historical identity.
                    cursor.execute(
                        """
                        SELECT Status, StaleAfterUtc, RunId
                        FROM ctl.MddMacTargetClaim WITH (UPDLOCK, HOLDLOCK)
                        WHERE PanelVersion = %s AND TargetIdentity = %s AND LogicalDueBucket = %s
                        """,
                        (target.panel_version, target.source_parent_station_id, int(logical_due_bucket)),
                    )
                existing = cursor.fetchone()
                existing_stale_after = _optional_utc(_row_named(existing, "StaleAfterUtc", index=1)) if existing else None
                existing_status = _row_value(existing, "Status", 0) if existing else None
                existing_run_id = str(_row_named(existing, "RunId", index=2) or "") if existing else None
                reclaim = bool(
                    existing
                    and str(existing_status or "") in {"CLAIMED", "RUNNING"}
                    and existing_stale_after is not None
                    and existing_stale_after <= claimed
                )
                won = existing is None or reclaim
                if won:
                    if tiered50:
                        if existing is None:
                            cursor.execute(
                                """
                                INSERT INTO ctl.MddMacTargetClaim
                                    (PanelVersion, SamplingTargetId, TargetIdentity, LogicalDueBucket, RunId, ClaimToken,
                                     ClaimedAtUtc, StaleAfterUtc, Status)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'CLAIMED')
                                """,
                                (
                                    target.panel_version,
                                    sampling_target_id,
                                    target.source_parent_station_id,
                                    int(logical_due_bucket),
                                    run_id,
                                    token,
                                    claimed,
                                    stale_after,
                                ),
                            )
                        else:
                            cursor.execute(
                                """
                                UPDATE ctl.MddMacTargetClaim
                                SET RunId = %s, ClaimToken = %s, ClaimedAtUtc = %s,
                                    StaleAfterUtc = %s, Status = 'CLAIMED',
                                    StartedAtUtc = NULL, CompletedAtUtc = NULL
                                WHERE PanelVersion = %s AND SamplingTargetId = %s AND LogicalDueBucket = %s
                                """,
                                (
                                    run_id,
                                    token,
                                    claimed,
                                    stale_after,
                                    target.panel_version,
                                    sampling_target_id,
                                    int(logical_due_bucket),
                                ),
                            )
                    else:
                        if existing is None:
                            cursor.execute(
                                """
                                INSERT INTO ctl.MddMacTargetClaim
                                    (PanelVersion, TargetIdentity, LogicalDueBucket, RunId, ClaimToken,
                                     ClaimedAtUtc, StaleAfterUtc, Status)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, 'CLAIMED')
                                """,
                                (
                                    target.panel_version,
                                    target.source_parent_station_id,
                                    int(logical_due_bucket),
                                    run_id,
                                    token,
                                    claimed,
                                    stale_after,
                                ),
                            )
                        else:
                            cursor.execute(
                                """
                                UPDATE ctl.MddMacTargetClaim
                                SET RunId = %s, ClaimToken = %s, ClaimedAtUtc = %s,
                                    StaleAfterUtc = %s, Status = 'CLAIMED',
                                    StartedAtUtc = NULL, CompletedAtUtc = NULL
                                WHERE PanelVersion = %s AND TargetIdentity = %s AND LogicalDueBucket = %s
                                """,
                                (
                                    run_id,
                                    token,
                                    claimed,
                                    stale_after,
                                    target.panel_version,
                                    target.source_parent_station_id,
                                    int(logical_due_bucket),
                                ),
                            )
                if tiered50:
                    cursor.execute(
                        """
                        INSERT INTO ctl.MddMacTargetClaimEvent
                            (PanelVersion, SamplingTargetId, TargetIdentity, LogicalDueBucket, RunId,
                             EventAtUtc, Outcome, ExistingStatus)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                        SELECT CONVERT(BIGINT, SCOPE_IDENTITY()) AS TargetClaimEventId;
                        """,
                        (
                            target.panel_version,
                            sampling_target_id,
                            target.source_parent_station_id,
                            int(logical_due_bucket),
                            run_id,
                            claimed,
                            "CLAIMED" if existing is None else ("RECLAIMED_STALE_CLAIM" if reclaim else "DUPLICATE_CLAIM"),
                            existing_status,
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO ctl.MddMacTargetClaimEvent
                            (PanelVersion, TargetIdentity, LogicalDueBucket, RunId,
                             EventAtUtc, Outcome, ExistingStatus)
                        VALUES (%s, %s, %s, %s, %s, %s, %s);
                        SELECT CONVERT(BIGINT, SCOPE_IDENTITY()) AS TargetClaimEventId;
                        """,
                        (
                            target.panel_version,
                            target.source_parent_station_id,
                            int(logical_due_bucket),
                            run_id,
                            claimed,
                            "CLAIMED" if existing is None else ("RECLAIMED_STALE_CLAIM" if reclaim else "DUPLICATE_CLAIM"),
                            existing_status,
                        ),
                    )
                event_id = int(_row_value(cursor.fetchone(), "TargetClaimEventId", 0))
                cursor.execute("COMMIT TRANSACTION;")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return MssqlClaimResult(won, key, str(existing_status) if existing_status else None, token if won else None, event_id)

    def record_duplicate_target_claim_event(
        self,
        *,
        panel_version: str,
        target: Target,
        logical_due_bucket: int,
        run_id: str,
        event_at: datetime,
    ) -> int:
        tiered50 = target.sampling_era is SamplingEra.TIERED50
        sampling_target_id = _target_sampling_id(target) if tiered50 else None
        if tiered50 and sampling_target_id is None:
            raise MssqlSafetyError("Tiered50 duplicate claims require a stable SamplingTargetId")
        with self._connection() as connection:
            cursor = self._cursor(connection)
            if tiered50:
                cursor.execute(
                    """
                    INSERT INTO ctl.MddMacTargetClaimEvent
                        (PanelVersion, SamplingTargetId, TargetIdentity, LogicalDueBucket, RunId,
                         EventAtUtc, Outcome, ExistingStatus)
                        VALUES (%s, %s, %s, %s, %s, %s, 'DUPLICATE_DISPATCHER_CLAIM', NULL);
                    SELECT CONVERT(BIGINT, SCOPE_IDENTITY()) AS TargetClaimEventId;
                    """,
                    (
                        panel_version,
                        sampling_target_id,
                        target.source_parent_station_id,
                        int(logical_due_bucket),
                        run_id,
                        _utc(event_at),
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO ctl.MddMacTargetClaimEvent
                        (PanelVersion, TargetIdentity, LogicalDueBucket, RunId,
                         EventAtUtc, Outcome, ExistingStatus)
                        VALUES (%s, %s, %s, %s, %s, 'DUPLICATE_DISPATCHER_CLAIM', NULL);
                    SELECT CONVERT(BIGINT, SCOPE_IDENTITY()) AS TargetClaimEventId;
                    """,
                    (panel_version, target.source_parent_station_id, int(logical_due_bucket), run_id, _utc(event_at)),
                )
            event_id = int(_row_value(cursor.fetchone(), "TargetClaimEventId", 0))
            connection.commit()
        return event_id

    def start_target_audit(
        self,
        *,
        run_id: str,
        target: Target,
        logical_due_bucket: int,
        started_at: datetime,
        collection_context: str | None = None,
    ) -> int:
        context = str(collection_context or self.collection_context)
        if context == MAC_SQL_COLLECTION_CONTEXT and (
            target.sampling_era is not SamplingEra.LEGACY7
            or _target_sampling_id(target) is None
            or target.selected_sampling_slot is None
            or target.selection_policy_version != LEGACY7_SELECTION_POLICY_VERSION
        ):
            raise MssqlSafetyError(
                "MAC_LEGACY7_PRODUCTION audits require target ID, SamplingSlot, and selection-policy lineage"
            )
        if context == MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT and (
            target.sampling_era is not SamplingEra.TIERED50
            or _target_sampling_id(target) is None
            or target.production_sampling_target_id is None
            or target.selection_policy_version != TIERED50_SELECTION_POLICY_VERSION
        ):
            raise MssqlSafetyError(
                "MAC_TIERED50_PRODUCTION audits require production ID and phase-policy lineage"
            )
        with self._connection() as connection:
            cursor = self._cursor(connection)
            try:
                cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
                cursor.execute(
                    """
                    INSERT INTO ctl.MddMacTargetAudit
                    (RunId, PanelVersion, TargetIdentity, StationName, Tier, PhaseIndex,
                     NumberOfResults, LogicalDueBucket, SamplingEra, SamplingTargetId,
                     SamplingSlot, SelectionPolicyVersion, ContinuityClassification,
                     CollectionContext, StartedAtUtc)
                    OUTPUT INSERTED.AuditId
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_id,
                        target.panel_version,
                        target.source_parent_station_id,
                        target.station_name,
                        target.tier,
                        int(target.phase_index),
                        int(target.number_of_results),
                        int(logical_due_bucket),
                        target.sampling_era.value,
                        _target_sampling_id(target),
                        target.selected_sampling_slot,
                        target.selection_policy_version,
                        target.continuity_classification,
                        context,
                        _utc(started_at),
                    ),
                )
                audit_id = int(_row_value(cursor.fetchone(), "AuditId", 0))
                if context == MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT:
                    cursor.execute(
                        """
                        UPDATE ctl.MddMacTargetClaim
                        SET Status = 'RUNNING', StartedAtUtc = %s
                        WHERE PanelVersion = %s AND SamplingTargetId = %s AND LogicalDueBucket = %s
                        """,
                        (
                            _utc(started_at),
                            target.panel_version,
                            _target_sampling_id(target),
                            int(logical_due_bucket),
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE ctl.MddMacTargetClaim
                        SET Status = 'RUNNING', StartedAtUtc = %s
                        WHERE PanelVersion = %s AND TargetIdentity = %s AND LogicalDueBucket = %s
                        """,
                        (
                            _utc(started_at),
                            target.panel_version,
                            target.source_parent_station_id,
                            int(logical_due_bucket),
                        ),
                    )
                cursor.execute("COMMIT TRANSACTION;")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return audit_id

    def persist_skipped_target_audits(
        self,
        *,
        run_id: str,
        logical_due_bucket: int,
        skipped_audits: Sequence[Mapping[str, Any]],
        collection_context: str | None = None,
    ) -> None:
        """Persist a bounded pre-claim skip tail in one idempotent transaction.

        This path intentionally does not touch ``ctl.MddMacTargetClaim``.
        A target which never passed the just-in-time claim gate is represented
        by a terminal audit row only.  ``HOLDLOCK`` plus the existing unique
        audit identity makes an ambiguous retry safe, while terminal rows are
        never overwritten by a later skip batch.
        """

        entries = list(skipped_audits)
        if not entries:
            return
        if len(entries) > MAX_SKIPPED_TARGET_AUDITS_PER_STATEMENT:
            raise MssqlSafetyError(
                "pre-claim skip batch exceeds the SQL Server 2,100-parameter limit; "
                f"maximum supported rows per statement is {MAX_SKIPPED_TARGET_AUDITS_PER_STATEMENT}"
            )
        context = str(collection_context or self.collection_context)
        rows: list[tuple[Any, ...]] = []
        for entry in entries:
            target = entry["target"]
            if context == MAC_SQL_COLLECTION_CONTEXT and (
                target.sampling_era is not SamplingEra.LEGACY7
                or _target_sampling_id(target) is None
                or target.selected_sampling_slot is None
                or target.selection_policy_version != LEGACY7_SELECTION_POLICY_VERSION
            ):
                raise MssqlSafetyError(
                    "MAC_LEGACY7 pre-claim audits require target ID, SamplingSlot, and selection-policy lineage"
                )
            if context == MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT and (
                target.sampling_era is not SamplingEra.TIERED50
                or _target_sampling_id(target) is None
                or target.production_sampling_target_id is None
                or target.selection_policy_version != TIERED50_SELECTION_POLICY_VERSION
            ):
                raise MssqlSafetyError(
                    "MAC_TIERED50 pre-claim audits require production ID and phase-policy lineage"
                )
            outcome = dict(entry["outcome"])
            started_at = entry["started_at"]
            completed_at = entry.get("completed_at", started_at)
            encoded = json.dumps(_safe_document(outcome), ensure_ascii=False, sort_keys=True, default=str)
            rows.append(
                (
                    run_id,
                    target.panel_version,
                    target.source_parent_station_id,
                    target.station_name,
                    target.tier,
                    int(target.phase_index),
                    int(target.number_of_results),
                    int(logical_due_bucket),
                    target.sampling_era.value,
                    _target_sampling_id(target),
                    target.selected_sampling_slot,
                    target.selection_policy_version,
                    target.continuity_classification,
                    context,
                    _utc(started_at),
                    _utc(completed_at),
                    "SKIPPED",
                    int(outcome.get("http_attempts", 0) or 0),
                    outcome.get("http_status"),
                    outcome.get("stop_event_result_count"),
                    outcome.get("parser_outcome"),
                    outcome.get("persistence_outcome"),
                    outcome.get("error_category"),
                    float(outcome.get("duration_seconds", 0) or 0) * 1000.0,
                    outcome.get("skip_reason"),
                    outcome.get("retry_decision"),
                    outcome.get("retry_outcome"),
                    encoded,
                )
            )
        statement, parameters = _build_skipped_target_audit_merge_statement(rows)
        with self._connection() as connection:
            cursor = self._cursor(connection)
            try:
                cursor.execute(statement, parameters)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def _attempt_fields(
        *,
        response: HttpResponse | None,
        transport_failure: TransportFailure | None,
        started_at: datetime,
        completed_at: datetime,
        transport_kind: str,
        live_client_invocation: bool | None,
    ) -> dict[str, Any]:
        if response is not None:
            http_status = response.status_code
            category = None
            outcome = "HTTP_RESPONSE"
            duration = float(response.elapsed_seconds or (_utc(completed_at) - _utc(started_at)).total_seconds())
            network_attempted = bool(response.network_attempted)
            http_received = bool(response.http_response_received)
            resolved_kind = str(response.transport_kind or transport_kind)
            resolved_live = bool(response.live_client_invocation)
        elif transport_failure is not None:
            http_status = None
            category = str(transport_failure.category)[:80]
            outcome = "TRANSPORT_FAILURE"
            duration = (_utc(completed_at) - _utc(started_at)).total_seconds()
            network_attempted = bool(transport_failure.network_attempted)
            http_received = bool(transport_failure.http_response_received)
            resolved_kind = str(transport_failure.transport_kind or transport_kind)
            resolved_live = bool(transport_failure.live_client_invocation)
        else:
            http_status = None
            category = "SANITIZED_ERROR"
            outcome = "UNKNOWN_ATTEMPT"
            duration = (_utc(completed_at) - _utc(started_at)).total_seconds()
            network_attempted = False
            http_received = False
            resolved_kind = str(transport_kind)
            resolved_live = False
        if transport_kind != "UNKNOWN" and resolved_kind == "UNKNOWN":
            resolved_kind = str(transport_kind)
        if live_client_invocation is not None:
            resolved_live = bool(live_client_invocation)
        confirmed = network_attempted and http_received
        transport_execution_entered = bool(
            getattr(response, "transport_execution_entered", False)
            if response is not None
            else getattr(transport_failure, "transport_execution_entered", False)
            if transport_failure is not None
            else False
        )
        conservative = (network_attempted and not http_received) or (
            resolved_live and transport_execution_entered and not http_received
        )
        return {
            "http_status": http_status,
            "category": category,
            "outcome": outcome,
            "duration_ms": max(0.0, duration * 1000.0),
            "transport_kind": resolved_kind,
            "network_attempted": network_attempted,
            "http_response_received": http_received,
            "live_client_invocation": resolved_live,
            "transport_execution_entered": transport_execution_entered,
            "conservative_quota_accounted": conservative,
            "provider_quota_status": "CONFIRMED" if confirmed else "UNKNOWN" if conservative else "NOT_CONSUMED",
            "provider_attempt_confirmed": confirmed,
        }

    def record_http_attempt(
        self,
        *,
        audit_id: int,
        attempt_number: int,
        started_at: datetime,
        completed_at: datetime,
        response: HttpResponse | None = None,
        transport_failure: TransportFailure | None = None,
        retry_decision: str | None = None,
        retry_after_seconds: float | None = None,
        transport_kind: str = "UNKNOWN",
        quota_permit_id: Any = None,
        quota_finalization: str | None = None,
        finalized_at: datetime | None = None,
        live_client_invocation: bool | None = None,
    ) -> None:
        fields = self._attempt_fields(
            response=response,
            transport_failure=transport_failure,
            started_at=started_at,
            completed_at=completed_at,
            transport_kind=transport_kind,
            live_client_invocation=live_client_invocation,
        )
        with self._connection() as connection:
            cursor = self._cursor(connection)
            try:
                cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
                cursor.execute(
                    """
                    INSERT INTO ctl.MddMacHttpAttempt
                    (AuditId, AttemptNumber, StartedAtUtc, CompletedAtUtc, HttpStatus,
                     TransportFailureCategory, Outcome, RetryDecision, RetryAfterSeconds,
                     DurationMs, TransportKind, NetworkAttempted, HttpResponseReceived,
                     LiveClientInvocation, ConservativeQuotaAccounted, ProviderQuotaStatus,
                     ProviderAttemptConfirmed, QuotaPermitId)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s)
                    """,
                    (
                        int(audit_id),
                        int(attempt_number),
                        _utc(started_at),
                        _utc(completed_at),
                        fields["http_status"],
                        fields["category"],
                        fields["outcome"],
                        retry_decision,
                        retry_after_seconds,
                        fields["duration_ms"],
                        fields["transport_kind"],
                        int(fields["network_attempted"]),
                        int(fields["http_response_received"]),
                        int(fields["live_client_invocation"]),
                        int(fields["conservative_quota_accounted"]),
                        fields["provider_quota_status"],
                        int(fields["provider_attempt_confirmed"]),
                        quota_permit_id,
                    ),
                )
                cursor.execute(
                    """
                    UPDATE ctl.MddMacTargetAudit
                    SET HttpAttempts = HttpAttempts + 1,
                        HttpStatus = COALESCE(%s, HttpStatus)
                    WHERE AuditId = %s
                    """,
                    (fields["http_status"], int(audit_id)),
                )
                if quota_permit_id is not None and quota_finalization is not None:
                    self._finalize_quota_permit_cursor(
                        cursor,
                        permit_id=quota_permit_id,
                        finalization=quota_finalization,
                        finalized_at=finalized_at or completed_at,
                        live_client_invocation=bool(fields["live_client_invocation"]),
                    )
                cursor.execute("COMMIT TRANSACTION;")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def record_http_attempt_and_finalize_quota(self, **kwargs: Any) -> None:
        self.record_http_attempt(**kwargs)

    record_attempt = record_http_attempt

    def persist_snapshot(
        self,
        *,
        audit_id: int,
        target: Target,
        logical_due_bucket: int,
        parsed_snapshot: Mapping[str, Any],
        persisted_at: datetime,
        collection_context: str | None = None,
    ) -> None:
        snapshot = _safe_document(dict(parsed_snapshot))
        if not isinstance(snapshot, dict):
            raise ValueError("parsed snapshot must be an object")
        snapshot["raw_payload_persisted"] = False
        snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False, default=str)
        row_sets = build_mssql_snapshot_rows(snapshot)
        # The audit row is authoritative for RunId, so resolve it inside the
        # same transaction instead of trusting a caller-provided duplicate.
        with self._connection() as connection:
            cursor = self._cursor(connection)
            try:
                cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
                cursor.execute("SELECT RunId FROM ctl.MddMacTargetAudit WHERE AuditId = %s", (int(audit_id),))
                audit_row = cursor.fetchone()
                run_id = _row_value(audit_row, "RunId", 0)
                if not run_id:
                    raise KeyError(f"unknown MDD Mac audit: {audit_id}")
                cursor.execute(
                    """
                    EXEC stg.uspPersistMddMacSnapshot
                        @RunId=%s,
                        @AuditId=%s,
                        @TargetIdentity=%s,
                        @SamplingTargetId=%s,
                        @LogicalDueBucket=%s,
                        @ObservedAtUtc=%s,
                        @ResponseTimestampSourceText=%s,
                        @ParserVersion=%s,
                        @ApplicationStatus=%s,
                        @PayloadHashSha256=%s,
                        @SnapshotJson=%s,
                        @CallsJson=%s,
                        @TimingsJson=%s,
                        @SituationEvidenceJson=%s,
                        @ServiceSectionsJson=%s,
                        @AttributesJson=%s,
                        @ResponseErrorsJson=%s,
                        @ParserGapsJson=%s
                    """,
                    (
                        str(run_id),
                        int(audit_id),
                        target.source_parent_station_id,
                        _target_sampling_id(target),
                        int(logical_due_bucket),
                        _utc(persisted_at),
                        _text(snapshot.get("response_timestamp")),
                        str(snapshot.get("parser_version", "unknown")),
                        _text(snapshot.get("application_status")),
                        hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest(),
                        snapshot_json,
                        json.dumps(row_sets["calls"], ensure_ascii=False, sort_keys=True, default=str),
                        json.dumps(row_sets["timings"], ensure_ascii=False, sort_keys=True, default=str),
                        json.dumps(row_sets["situations"], ensure_ascii=False, sort_keys=True, default=str),
                        json.dumps(row_sets["service_sections"], ensure_ascii=False, sort_keys=True, default=str),
                        json.dumps(row_sets["attributes"], ensure_ascii=False, sort_keys=True, default=str),
                        json.dumps(row_sets["response_errors"], ensure_ascii=False, sort_keys=True, default=str),
                        json.dumps(row_sets["parser_gaps"], ensure_ascii=False, sort_keys=True, default=str),
                    ),
                )
                # The procedure emits one result set.  Consume it before the
                # connection is committed so DB-API clients can reuse it.
                try:
                    cursor.fetchall()
                except Exception:
                    pass
                cursor.execute("COMMIT TRANSACTION;")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    persist_parsed_snapshot = persist_snapshot

    def complete_target_audit(self, *, audit_id: int, outcome: Mapping[str, Any], completed_at: datetime) -> None:
        status = str(outcome.get("status", "FAILED"))
        claim_status = status if status in {"SUCCEEDED", "FAILED", "SKIPPED", "INTERRUPTED"} else "COMPLETED"
        encoded = json.dumps(_safe_document(dict(outcome)), ensure_ascii=False, sort_keys=True, default=str)
        with self._connection() as connection:
            cursor = self._cursor(connection)
            try:
                cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
                cursor.execute(
                    """
                    UPDATE ctl.MddMacTargetAudit
                    SET CompletedAtUtc = %s,
                        Status = %s,
                        HttpAttempts = COALESCE(%s, HttpAttempts),
                        HttpStatus = COALESCE(%s, HttpStatus),
                        StopEventResultCount = %s,
                        ParserOutcome = %s,
                        PersistenceOutcome = %s,
                        ErrorCategory = %s,
                        DurationMs = %s,
                        SkipReason = %s,
                        RetryDecision = %s,
                        RetryOutcome = %s,
                        OutcomeJson = %s
                    WHERE AuditId = %s
                    """,
                    (
                        _utc(completed_at),
                        status,
                        outcome.get("http_attempts"),
                        outcome.get("http_status"),
                        outcome.get("stop_event_result_count"),
                        outcome.get("parser_outcome"),
                        outcome.get("persistence_outcome"),
                        outcome.get("error_category"),
                        float(outcome.get("duration_seconds", 0) or 0) * 1000.0,
                        outcome.get("skip_reason"),
                        outcome.get("retry_decision"),
                        outcome.get("retry_outcome"),
                        encoded,
                        int(audit_id),
                    ),
                )
                cursor.execute(
                    """
                    UPDATE c
                    SET Status = %s, CompletedAtUtc = %s
                    FROM ctl.MddMacTargetClaim AS c
                    INNER JOIN ctl.MddMacTargetAudit AS a
                        ON a.PanelVersion = c.PanelVersion
                       AND a.TargetIdentity = c.TargetIdentity
                       AND a.LogicalDueBucket = c.LogicalDueBucket
                    WHERE a.AuditId = %s
                    """,
                    (claim_status, _utc(completed_at), int(audit_id)),
                )
                cursor.execute("COMMIT TRANSACTION;")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def complete_collector_run(self, *, run_id: str, status: str, completed_at: datetime, summary: Mapping[str, Any]) -> None:
        encoded = json.dumps(_safe_document(dict(summary)), ensure_ascii=False, sort_keys=True, default=str)
        with self._connection() as connection:
            cursor = self._cursor(connection)
            try:
                cursor.execute(
                    """
                    EXEC stg.uspPersistMddMacSnapshot
                        @RunId=%s,
                        @CompletedRunStatus=%s,
                        @CompletedAtUtc=%s,
                        @RunSummaryJson=%s
                    """,
                    (str(run_id), str(status), _utc(completed_at), encoded),
                )
                # The finalization branch returns a scalar NULL SnapshotId;
                # consume it before the DB-API connection is reused.
                try:
                    cursor.fetchall()
                except Exception:
                    pass
                if self.collection_context in {
                    MAC_SQL_COLLECTION_CONTEXT,
                    MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT,
                }:
                    cursor.execute(
                        """
                        UPDATE ctl.MddMacProductionLease
                        SET Status = 'RELEASED',
                            ReleasedAtUtc = %s,
                            LastHeartbeatAtUtc = %s
                        WHERE LeaseName = %s AND RunId = %s AND Status = 'ACTIVE'
                        """,
                        (_utc(completed_at), _utc(completed_at), PRODUCTION_LEASE_NAME, str(run_id)),
                    )
                    if getattr(cursor, "rowcount", 1):
                        cursor.execute(
                            """
                            INSERT INTO ctl.MddMacProductionLeaseEvent
                                (LeaseName, CollectionContext, RunId, EventAtUtc, Outcome)
                            VALUES (%s, %s, %s, %s, 'RELEASED')
                            """,
                            (
                                PRODUCTION_LEASE_NAME,
                                self.collection_context,
                                str(run_id),
                                _utc(completed_at),
                            ),
                        )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def fail_collector_run(self, *, run_id: str, completed_at: datetime, summary: Mapping[str, Any]) -> None:
        self.complete_collector_run(run_id=run_id, status="FAILED", completed_at=completed_at, summary=summary)

    def record_run_skip(self, *, run_id: str, reason: str, completed_at: datetime) -> None:
        self.complete_collector_run(
            run_id=run_id,
            status="SKIPPED",
            completed_at=completed_at,
            summary={"skip_reason": str(reason)},
        )

    def record_quota_decision(
        self,
        *,
        run_id: str,
        source_parent_station_id: str | None,
        logical_due_bucket: int | None,
        decision_kind: str,
        decision: Mapping[str, Any],
        recorded_at: datetime,
        collection_context: str | None = None,
    ) -> None:
        with self._connection() as connection:
            cursor = self._cursor(connection)
            cursor.execute(
                """
                INSERT INTO ctl.MddMacQuotaAudit
                    (RunId, TargetIdentity, LogicalDueBucket, DecisionKind,
                     DecisionJson, RecordedAtUtc, CollectionContext)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    source_parent_station_id,
                    logical_due_bucket,
                    decision_kind,
                    json.dumps(_safe_document(dict(decision)), ensure_ascii=False, sort_keys=True, default=str),
                    _utc(recorded_at),
                    str(collection_context or self.collection_context),
                ),
            )
            connection.commit()

    def recover_stale_quota_permits(
        self,
        *,
        at_utc: datetime | None = None,
        stale_after_seconds: int = 300,
    ) -> Mapping[str, Any]:
        """Conservatively settle permits left pending by a crashed process.

        A pending permit is never silently returned to the headroom pool after
        a process boundary.  Once it is older than the bounded request/claim
        window, the safest durable interpretation is an unknown provider
        attempt.  The update is idempotent because only pending rows qualify.
        """

        requested_at = _utc(at_utc or datetime.now(timezone.utc))
        at = self._authoritative_authorization_time(requested_at)
        cutoff = at - timedelta(seconds=max(1, int(stale_after_seconds)))
        self.ensure_automatic_quota_rollover(at_utc=at)
        with self._connection() as connection:
            cursor = self._cursor(connection)
            try:
                cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
                candidates = self._provider_period_baseline_candidates(cursor, at=at, lock=False)
                month: str | None = None
                if len(candidates) == 1:
                    month = str(_row_named(candidates[0], "MonthKey", index=0) or "")
                elif not candidates:
                    bootstrap_candidates = self._conservative_bootstrap_candidates(cursor, at=at, lock=True)
                    if len(bootstrap_candidates) == 1:
                        month = str(_row_named(bootstrap_candidates[0], "MonthKey", index=2) or "")
                if not month:
                    cursor.execute("COMMIT TRANSACTION;")
                    connection.commit()
                    return {
                        "status": "NOT_AVAILABLE",
                        "reason": (
                            "current provider billing period is missing or ambiguous; "
                            "stale permit recovery remains fail-closed"
                        ),
                        "month_key": None,
                        "recovered_permits": 0,
                        "cutoff_utc": _iso(cutoff),
                    }
                cursor.execute(
                    """
                    UPDATE ctl.MddMacQuotaPermit
                    SET Status = %s,
                        FinalizedAtUtc = %s,
                        ReleasedAtUtc = NULL,
                        FinalizationReason = %s,
                        LiveClientInvocation = 1
                    WHERE MonthKey = %s
                      AND Status = %s
                      AND ReservedAtUtc < %s
                    """,
                    (
                        CONSERVATIVE_LIVE_ATTEMPT,
                        at,
                        "CRASH_RECOVERY_CONSERVATIVE",
                        month,
                        PENDING_AUTHORIZATION,
                        cutoff,
                    ),
                )
                recovered = int(getattr(cursor, "rowcount", 0) or 0)
                cursor.execute("COMMIT TRANSACTION;")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return {
            "status": "PASS",
            "month_key": month,
            "recovered_permits": recovered,
            "cutoff_utc": _iso(cutoff),
        }

    @staticmethod
    def _provider_period_baseline_candidates(
        cursor: Any,
        *,
        at: datetime,
        lock: bool = False,
    ) -> list[Any]:
        """Return baselines whose declared provider period covers ``at``.

        Selecting by the persisted UTC period bounds avoids assuming that the
        host's UTC calendar month is the provider's billing month.  More than
        one covering row is unsafe and is handled as an ambiguous state by
        callers.
        """

        lock_hint = " WITH (UPDLOCK, HOLDLOCK)" if lock else ""
        cursor.execute(
            """
            SELECT TOP (2) MonthKey, ProviderQuotaCeiling, NormalOperationalMaximum,
                   RetryProtectionBoundary, ConfirmedProviderAttempts,
                   ConservativeUnknownAttempts, ExplicitSharedConsumerReservedAttempts,
                   AccountingStatus, IncludesAllSharedConsumers, BaselineAsOfUtc,
                   LocalUsageIncludedThroughUtc, SourceDescription,
                   AccountingModel, ProviderMeasurementCutoffUtc, StaleAfterUtc,
                   SourceEvidenceHash, ReconciliationKey, ProviderPeriodTimezone,
                   ProviderPeriodStartUtc, ProviderPeriodEndUtc
            FROM ctl.MddMacSharedQuotaAccounting"""
            + lock_hint
            + """
            WHERE ProviderPeriodStartUtc <= %s
              AND ProviderPeriodEndUtc > %s
            ORDER BY ProviderPeriodStartUtc DESC, MonthKey
            """,
            (_utc(at), _utc(at)),
        )
        return list(cursor.fetchall())

    @staticmethod
    def _conservative_bootstrap_candidates(
        cursor: Any,
        *,
        at: datetime,
        lock: bool = False,
    ) -> list[Any]:
        """Return active bounded bootstrap rows covering ``at``.

        The validity interval is stored explicitly because the provider's
        billing period is intentionally unknown.  The query never substitutes
        a UTC calendar month for provider metadata.
        """

        lock_hint = " WITH (UPDLOCK, HOLDLOCK)" if lock else ""
        cursor.execute(
            """
            SELECT TOP (2) AuthorizationId, AuthorizationKey, MonthKey,
                   AuthorizationMode, AccountingModel,
                   ScreenshotCumulativeRequests, ScreenshotMonthlyLimit,
                   ScreenshotSha256, ConfirmedLocalAttempts,
                   ConservativeUnknownLocalAttempts, PendingLocalReservations,
                   AccountedTotalAtAuthorization, ProviderQuotaCeiling,
                   NormalOperationalMaximum, RetryProtectionBoundary,
                   ValidFromUtc, LocalUsageIncludedThroughUtc, StopAtUtc,
                   OperatorApprovalAtUtc, OperatorApprovalStatementHash,
                   CredentialExclusivityConfirmed, ProviderScopeVerified,
                   ProviderMeasurementCutoffEstablished,
                   ProviderResetTimezoneEstablished, UnexpectedExternalUsage,
                   SourceEvidenceHash, AuthorizationStatus, EvidenceJson,
                   RecordedAtUtc, RevokedAtUtc, RevocationReason
            FROM ctl.MddMacConservativeBootstrapAuthorization"""
            + lock_hint
            + """
            WHERE AuthorizationStatus = 'ACTIVE'
              AND ValidFromUtc <= %s
              AND StopAtUtc > %s
            ORDER BY ValidFromUtc DESC, AuthorizationId DESC
            """,
            (_utc(at), _utc(at)),
        )
        return list(cursor.fetchall())

    @staticmethod
    def _bootstrap_permit_counts(
        cursor: Any,
        *,
        month: str,
        observed_through: datetime,
        lock: bool = False,
    ) -> Mapping[str, Any]:
        """Read every relevant October permit through one local cutoff."""

        lock_hint = " WITH (UPDLOCK, HOLDLOCK)" if lock else ""
        cursor.execute(
            """
            SELECT Status, COALESCE(SUM(Amount), 0) AS amount
            FROM ctl.MddMacQuotaPermit"""
            + lock_hint
            + """
            WHERE MonthKey = %s
              AND ReservedAtUtc >= %s
              AND ReservedAtUtc <= %s
            GROUP BY Status
            """,
            (
                month,
                datetime(2026, 10, 1, tzinfo=timezone.utc),
                _utc(observed_through),
            ),
        )
        counts = {"confirmed": 0, "unknown": 0, "pending": 0, "released_no_network": 0}
        unexpected: list[str] = []
        for row in cursor.fetchall():
            status = str(_row_named(row, "Status", index=0) or "")
            amount = int(_row_named(row, "amount", index=1) or 0)
            if amount < 0:
                raise MssqlSafetyError("local quota permit amount is negative")
            if status == HTTP_PROVIDER_ATTEMPT_CONFIRMED:
                counts["confirmed"] += amount
            elif status == CONSERVATIVE_LIVE_ATTEMPT:
                counts["unknown"] += amount
            elif status == PENDING_AUTHORIZATION:
                counts["pending"] += amount
            elif status == RELEASED_NO_NETWORK:
                counts["released_no_network"] += amount
            else:
                unexpected.append(status)

        cursor.execute(
            """
            SELECT COALESCE(a.CollectionContext, '') AS collection_context,
                   COUNT_BIG(*) AS attempts_without_permit
            FROM ctl.MddMacHttpAttempt AS h
            INNER JOIN ctl.MddMacTargetAudit AS a ON a.AuditId = h.AuditId
            WHERE h.CompletedAtUtc >= %s
              AND h.CompletedAtUtc <= %s
              AND h.NetworkAttempted = 1
              AND h.QuotaPermitId IS NULL
            GROUP BY a.CollectionContext
            ORDER BY a.CollectionContext
            """,
            (
                datetime(2026, 10, 1, tzinfo=timezone.utc),
                _utc(observed_through),
            ),
        )
        untracked_research_attempts = 0
        untracked_research_contexts: list[str] = []
        attempts_without_permit = 0
        for attempt_row in cursor.fetchall():
            context = str(_row_named(attempt_row, "collection_context", index=0) or "")
            count = int(_row_named(attempt_row, "attempts_without_permit", index=1) or 0)
            if count < 0:
                raise MssqlSafetyError("untracked network attempt count is negative")
            if context == KNOWN_PREBOOTSTRAP_RESEARCH_CONTEXT:
                untracked_research_attempts += count
                untracked_research_contexts.append(context)
            else:
                attempts_without_permit += count
                unexpected.append("NETWORK_ATTEMPT_WITHOUT_QUOTA_PERMIT")
        return {
            **counts,
            "total": counts["confirmed"] + counts["unknown"] + counts["pending"] + untracked_research_attempts,
            "unexpected_statuses": sorted(set(unexpected)),
            "attempts_without_permit": attempts_without_permit,
            "known_untracked_research_attempts": untracked_research_attempts,
            "untracked_research_attempt_contexts": sorted(set(untracked_research_contexts)),
        }

    def ensure_automatic_quota_rollover(self, *, at_utc: datetime) -> Mapping[str, Any]:
        """Create the next verified provider period atomically when eligible.

        October's bounded bootstrap is intentionally independent.  After its
        hard stop, a future period can be created only when an administrator
        has registered an evidence-backed reset contract.  The stored
        procedure locks that contract, the period key, and prior pending
        permits before inserting a zero-based accounting row; repeated
        five-minute heartbeats therefore become idempotent.
        """

        at = _utc(at_utc)
        if at < BOOTSTRAP_LATEST_STOP_UTC:
            return {
                "status": "NOT_APPLICABLE",
                "reason": "October bounded bootstrap remains inside its approved interval",
                "mdd_requests_issued": 0,
            }
        select_columns = """
            ContractId, ContractKey, ProviderName, ResetModel, ResetTimezone,
            EffectiveFromUtc, ActivationDelaySeconds, ProviderQuotaCeiling,
            CredentialExclusivityConfirmed, ProviderResetBoundaryVerified,
            CalendarMonthResetModelEstablished, OperatorAssumptionApproved,
            SupportedResetTimezonesJson,
            SourceEvidenceHash, EvidenceJson, ContractStatus, RecordedAtUtc,
            RevokedAtUtc, RevocationReason
        """
        with self._connection() as connection:
            cursor = self._cursor(connection)
            cursor.execute(
                f"""
                SELECT TOP (1) {select_columns}
                FROM ctl.MddMacQuotaResetContract WITH (READCOMMITTEDLOCK)
                WHERE ContractStatus = 'ACTIVE'
                ORDER BY EffectiveFromUtc DESC, ContractId DESC
                """
            )
            row = cursor.fetchone()
            if row is None:
                return {
                    "status": "NOT_CONFIGURED",
                    "reason": "no verified provider reset contract is registered",
                    "mdd_requests_issued": 0,
                }
            contract = _row_mapping(row, RESET_CONTRACT_COLUMNS)
            try:
                period = build_automatic_rollover_period(contract=contract, at_utc=at)
            except QuotaRolloverError as exc:
                message = str(exc)
                if "activation delay" in message or "not effective" in message:
                    return {
                        "status": "NOT_READY",
                        "reason": message,
                        "contract_key": str(contract.get("ContractKey") or ""),
                        "mdd_requests_issued": 0,
                    }
                raise MssqlSafetyError(message) from exc

            cursor.execute(
                """
                EXEC ctl.uspEnsureMddMacQuotaPeriod
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
                """,
                (
                    period["contract_key"],
                    period["month_key"],
                    period["provider_period_timezone"],
                    _optional_utc(period["provider_period_start_utc"]),
                    _optional_utc(period["provider_period_end_utc"]),
                    _optional_utc(period["local_usage_included_through_utc"]),
                    _optional_utc(period["at_utc"]),
                    period["source_evidence_hash"],
                    period["reconciliation_key"],
                ),
            )
            fetchall = getattr(cursor, "fetchall", None)
            if callable(fetchall):
                try:
                    fetchall()
                except Exception:
                    pass
        return {
            "status": "APPLIED_OR_IDEMPOTENT",
            "contract_key": period["contract_key"],
            "month_key": period["month_key"],
            "provider_period_timezone": period["provider_period_timezone"],
            "provider_period_start_utc": period["provider_period_start_utc"],
            "provider_period_end_utc": period["provider_period_end_utc"],
            "activation_not_before_utc": period["activation_not_before_utc"],
            "mdd_requests_issued": 0,
        }

    def _quota_baseline_row(self, cursor: Any, *, month: str, at: datetime) -> Any:
        cursor.execute(
            """
            SELECT MonthKey, ProviderQuotaCeiling, NormalOperationalMaximum,
                   RetryProtectionBoundary, ConfirmedProviderAttempts,
                   ConservativeUnknownAttempts, ExplicitSharedConsumerReservedAttempts,
                   AccountingStatus, IncludesAllSharedConsumers, BaselineAsOfUtc,
                   LocalUsageIncludedThroughUtc, SourceDescription,
                   AccountingModel, ProviderMeasurementCutoffUtc, StaleAfterUtc,
                   SourceEvidenceHash, ReconciliationKey, ProviderPeriodTimezone,
                   ProviderPeriodStartUtc, ProviderPeriodEndUtc
            FROM ctl.MddMacSharedQuotaAccounting WITH (UPDLOCK, HOLDLOCK)
            WHERE MonthKey = %s
            """,
            (month,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        mapped = _row_mapping(row, QUOTA_BASELINE_COLUMNS)
        usable, _reason = validate_stored_baseline_row(mapped, at_utc=at)
        if not usable:
            return None
        return row

    @staticmethod
    def _quota_usage_totals(
        cursor: Any,
        *,
        month: str,
        local_cutoff: datetime,
        observed_through: datetime | None = None,
        include_cutoff: bool = False,
    ) -> Mapping[str, int]:
        observed = _utc(observed_through or datetime.now(timezone.utc))
        cutoff_operator = ">=" if include_cutoff else ">"
        cursor.execute(
            f"""
            SELECT
                COALESCE(SUM(CASE WHEN Status = %s THEN Amount ELSE 0 END), 0) AS confirmed,
                COALESCE(SUM(CASE WHEN Status = %s THEN Amount ELSE 0 END), 0) AS conservative,
                COALESCE(SUM(CASE WHEN Status = %s THEN Amount ELSE 0 END), 0) AS pending,
                COALESCE(SUM(CASE WHEN LiveClientInvocation = 1 THEN Amount ELSE 0 END), 0) AS invocations
            FROM ctl.MddMacQuotaPermit WITH (READCOMMITTEDLOCK)
            WHERE MonthKey = %s
              AND ReservedAtUtc {cutoff_operator} %s
              AND ReservedAtUtc <= %s
            """,
            (
                HTTP_PROVIDER_ATTEMPT_CONFIRMED,
                CONSERVATIVE_LIVE_ATTEMPT,
                PENDING_AUTHORIZATION,
                month,
                local_cutoff,
                observed,
            ),
        )
        row = cursor.fetchone()
        return {
            "confirmed": int(_row_named(row, "confirmed", index=0) or 0),
            "conservative": int(_row_named(row, "conservative", index=1) or 0),
            "pending": int(_row_named(row, "pending", index=2) or 0),
            "invocations": int(_row_named(row, "invocations", index=3) or 0),
        }

    def current_month_usage(self, *, at_utc: datetime | None = None) -> Mapping[str, Any] | None:
        """Read a complete baseline or the explicitly bounded bootstrap.

        Missing, stale, ambiguous, or inconsistent shared-consumer evidence is
        never converted to zero.  The bootstrap branch is selected only when
        no usable provider-period baseline covers the authorization instant.
        """

        at = _utc(at_utc or datetime.now(timezone.utc))
        try:
            rollover = self.ensure_automatic_quota_rollover(at_utc=at)
            with self._connection() as connection:
                cursor = self._cursor(connection)
                complete_candidates = self._provider_period_baseline_candidates(cursor, at=at, lock=False)
                baseline: Any | None = None
                mapped_baseline: Mapping[str, Any] | None = None
                baseline_as_of: datetime | None = None
                local_cutoff: datetime | None = None
                mode = "COMPLETE"
                complete_reason = ""

                if len(complete_candidates) > 1:
                    return {
                        "state_available": False,
                        "source": QUOTA_BASELINE_UNAVAILABLE,
                        "availability_reason": "multiple provider-wide accounting rows cover the current provider billing period",
                        "month_key": None,
                        "known_total": None,
                    }
                if len(complete_candidates) == 1:
                    candidate = complete_candidates[0]
                    candidate_mapping = _row_mapping(candidate, QUOTA_BASELINE_COLUMNS)
                    usable, complete_reason = validate_stored_baseline_row(candidate_mapping, at_utc=at)
                    if usable:
                        baseline = candidate
                        mapped_baseline = candidate_mapping
                        baseline_as_of = _optional_utc(candidate_mapping["BaselineAsOfUtc"])
                        local_cutoff = _optional_utc(candidate_mapping["LocalUsageIncludedThroughUtc"])
                    else:
                        mode = "BOOTSTRAP"
                else:
                    mode = "BOOTSTRAP"

                if mode == "BOOTSTRAP":
                    bootstrap_candidates = self._conservative_bootstrap_candidates(cursor, at=at, lock=False)
                    if len(bootstrap_candidates) != 1:
                        reason = (
                            "no complete provider-wide baseline or active bounded bootstrap covers the authorization time"
                            if not bootstrap_candidates
                            else "multiple active bounded bootstrap authorizations cover the authorization time"
                        )
                        return {
                            "state_available": False,
                            "source": QUOTA_BASELINE_INCOMPLETE if complete_reason else QUOTA_BASELINE_UNAVAILABLE,
                            "availability_reason": complete_reason or reason,
                            "month_key": None,
                            "known_total": None,
                        }
                    bootstrap = bootstrap_candidates[0]
                    mapped_bootstrap = _row_mapping(bootstrap, BOOTSTRAP_AUTHORIZATION_COLUMNS)
                    usable, reason = validate_stored_bootstrap_row(mapped_bootstrap, at_utc=at)
                    if not usable:
                        return {
                            "state_available": False,
                            "source": QUOTA_BASELINE_INCOMPLETE,
                            "availability_reason": reason,
                            "month_key": str(mapped_bootstrap.get("MonthKey") or ""),
                            "known_total": None,
                        }
                    month = str(mapped_bootstrap["MonthKey"] or "")
                    local_cutoff = _optional_utc(mapped_bootstrap["LocalUsageIncludedThroughUtc"])
                    local = self._quota_usage_totals(
                        cursor,
                        month=month,
                        local_cutoff=local_cutoff,
                        observed_through=at,
                    )
                    baseline_confirmed = int(mapped_bootstrap["ScreenshotCumulativeRequests"] or 0) + int(
                        mapped_bootstrap["ConfirmedLocalAttempts"] or 0
                    )
                    baseline_conservative = int(mapped_bootstrap["ConservativeUnknownLocalAttempts"] or 0)
                    baseline_reserved = int(mapped_bootstrap["PendingLocalReservations"] or 0)
                    baseline_total = baseline_confirmed + baseline_conservative + baseline_reserved
                    post_cutoff_total = local["confirmed"] + local["conservative"] + local["pending"]
                    return {
                        "confirmed_provider_attempts": baseline_confirmed + local["confirmed"],
                        "reserved_shared_usage": baseline_reserved + local["pending"],
                        "projected_non_retry_attempts": 0,
                        "conservative_live_attempts": baseline_conservative + local["conservative"],
                        "live_client_invocations": local["invocations"],
                        "http_responses_received": baseline_confirmed + local["confirmed"],
                        "provider_quota_unknown_attempts": baseline_conservative + local["conservative"],
                        "state_available": True,
                        "source": BOOTSTRAP_AUTHORIZATION_MODE,
                        "authorization_mode": BOOTSTRAP_AUTHORIZATION_MODE,
                        "month_key": month,
                        "baseline_as_of_utc": _iso(_optional_utc(mapped_bootstrap["OperatorApprovalAtUtc"])),
                        "local_usage_included_through_utc": _iso(local_cutoff),
                        "provider_measurement_cutoff_utc": None,
                        "stale_after_utc": _iso(_optional_utc(mapped_bootstrap["StopAtUtc"])),
                        "accounting_model": str(mapped_bootstrap["AccountingModel"] or ""),
                        "source_evidence_hash": str(mapped_bootstrap["SourceEvidenceHash"] or ""),
                        "reconciliation_key": str(mapped_bootstrap["AuthorizationKey"] or ""),
                        "provider_period_timezone": None,
                        "provider_period_metadata_verified": False,
                        "baseline_component_total": baseline_total,
                        "post_cutoff_local_usage": {
                            "confirmed": local["confirmed"],
                            "conservative": local["conservative"],
                            "pending": local["pending"],
                            "total": post_cutoff_total,
                        },
                        "known_total": baseline_total + post_cutoff_total,
                        "automatic_rollover": rollover,
                    }

                assert baseline is not None and mapped_baseline is not None and local_cutoff is not None
                month = str(_row_named(baseline, "MonthKey", index=0) or "")
                local = self._quota_usage_totals(
                    cursor,
                    month=month,
                    local_cutoff=local_cutoff,
                    observed_through=at,
                    include_cutoff=(
                        str(mapped_baseline["AccountingModel"] or "")
                        == AUTOMATIC_ROLLOVER_ACCOUNTING_MODEL
                    ),
                )
        except Exception:
            # A schema/permission/connectivity failure is itself unavailable
            # provider-wide state; it must not become an implicit zero.
            return {
                "state_available": False,
                "source": QUOTA_BASELINE_UNAVAILABLE,
                "availability_reason": "provider-wide accounting state could not be read",
                "month_key": None,
                "known_total": None,
            }

        baseline_confirmed = int(_row_named(baseline, "ConfirmedProviderAttempts", index=4) or 0)
        baseline_conservative = int(_row_named(baseline, "ConservativeUnknownAttempts", index=5) or 0)
        baseline_reserved = int(_row_named(baseline, "ExplicitSharedConsumerReservedAttempts", index=6) or 0)
        baseline_total = baseline_confirmed + baseline_conservative + baseline_reserved
        confirmed = baseline_confirmed + local["confirmed"]
        conservative = baseline_conservative + local["conservative"]
        reserved = baseline_reserved + local["pending"]
        post_cutoff_total = local["confirmed"] + local["conservative"] + local["pending"]
        return {
            "confirmed_provider_attempts": confirmed,
            "reserved_shared_usage": reserved,
            "projected_non_retry_attempts": 0,
            "conservative_live_attempts": conservative,
            "live_client_invocations": local["invocations"],
            "http_responses_received": confirmed,
            "provider_quota_unknown_attempts": conservative,
            "state_available": True,
            "source": str(_row_named(baseline, "SourceDescription", index=11) or "SHARED_PROVIDER_BASELINE"),
            "authorization_mode": str(mapped_baseline["AccountingModel"] or "COMPLETE"),
            "month_key": month,
            "baseline_as_of_utc": _iso(baseline_as_of),
            "local_usage_included_through_utc": _iso(local_cutoff),
            "provider_measurement_cutoff_utc": _iso(_optional_utc(mapped_baseline["ProviderMeasurementCutoffUtc"])),
            "stale_after_utc": _iso(_optional_utc(mapped_baseline["StaleAfterUtc"])),
            "accounting_model": str(mapped_baseline["AccountingModel"] or ""),
            "source_evidence_hash": str(mapped_baseline["SourceEvidenceHash"] or ""),
            "reconciliation_key": str(mapped_baseline["ReconciliationKey"] or ""),
            "provider_period_timezone": str(mapped_baseline["ProviderPeriodTimezone"] or ""),
            "provider_period_metadata_verified": True,
            "baseline_component_total": baseline_total,
            "post_cutoff_local_usage": {
                "confirmed": local["confirmed"],
                "conservative": local["conservative"],
                "pending": local["pending"],
                "total": post_cutoff_total,
            },
            "known_total": baseline_total + post_cutoff_total,
            "automatic_rollover": rollover,
        }

    @staticmethod
    def _quota_permit_reconciliation_counts(
        cursor: Any,
        *,
        month: str,
        cutoff: datetime,
        observed_through: datetime | None = None,
        lock: bool = False,
    ) -> Mapping[str, int]:
        """Read permit counts around the provider measurement cutoff.

        ``cutoff`` is the provider measurement boundary. ``observed_through``
        bounds post-cutoff rows to the local SQL evidence actually read, so a
        reconciliation cannot classify future or unobserved permits as usage.
        """

        lock_hint = " WITH (UPDLOCK, HOLDLOCK)" if lock else ""
        observed = _utc(observed_through or cutoff)
        cursor.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN Status = %s AND ReservedAtUtc <= %s THEN Amount ELSE 0 END), 0) AS confirmed_through_cutoff,
                COALESCE(SUM(CASE WHEN Status = %s AND ReservedAtUtc <= %s THEN Amount ELSE 0 END), 0) AS unknown_through_cutoff,
                COALESCE(SUM(CASE WHEN Status = %s AND ReservedAtUtc <= %s THEN Amount ELSE 0 END), 0) AS pending_through_cutoff,
                COALESCE(SUM(CASE WHEN Status = %s AND ReservedAtUtc > %s AND ReservedAtUtc <= %s THEN Amount ELSE 0 END), 0) AS confirmed_after_cutoff,
                COALESCE(SUM(CASE WHEN Status = %s AND ReservedAtUtc > %s AND ReservedAtUtc <= %s THEN Amount ELSE 0 END), 0) AS unknown_after_cutoff,
                COALESCE(SUM(CASE WHEN Status = %s AND ReservedAtUtc > %s AND ReservedAtUtc <= %s THEN Amount ELSE 0 END), 0) AS pending_after_cutoff
            FROM ctl.MddMacQuotaPermit"""
            + lock_hint
            + """
            WHERE MonthKey = %s
            """,
            (
                HTTP_PROVIDER_ATTEMPT_CONFIRMED,
                cutoff,
                CONSERVATIVE_LIVE_ATTEMPT,
                cutoff,
                PENDING_AUTHORIZATION,
                cutoff,
                HTTP_PROVIDER_ATTEMPT_CONFIRMED,
                cutoff,
                observed,
                CONSERVATIVE_LIVE_ATTEMPT,
                cutoff,
                observed,
                PENDING_AUTHORIZATION,
                cutoff,
                observed,
                month,
            ),
        )
        row = cursor.fetchone()
        return {
            "confirmed_through_cutoff": int(_row_named(row, "confirmed_through_cutoff", index=0) or 0),
            "unknown_through_cutoff": int(_row_named(row, "unknown_through_cutoff", index=1) or 0),
            "pending_through_cutoff": int(_row_named(row, "pending_through_cutoff", index=2) or 0),
            "confirmed_after_cutoff": int(_row_named(row, "confirmed_after_cutoff", index=3) or 0),
            "unknown_after_cutoff": int(_row_named(row, "unknown_after_cutoff", index=4) or 0),
            "pending_after_cutoff": int(_row_named(row, "pending_after_cutoff", index=5) or 0),
        }

    def read_quota_reconciliation_inputs(
        self,
        *,
        month_key: str,
        local_usage_included_through_utc: datetime,
        provider_measurement_cutoff_utc: datetime | None = None,
        provider_period_start_utc: datetime | None = None,
        provider_period_end_utc: datetime | None = None,
    ) -> Mapping[str, Any]:
        """Read local quota, HTTP, and collector evidence without writing.

        Permit rows are the accounting source of truth for local consumption.
        HTTP and run counts are returned as separate reconciliation evidence so
        they can be compared without adding the same local attempt twice.
        """

        cutoff = _utc(local_usage_included_through_utc)
        if (provider_period_start_utc is None) != (provider_period_end_utc is None):
            raise ValueError("provider period start and end must be supplied together")
        if provider_period_start_utc is None:
            # Kept for old read-only diagnostics. Registration supplies the
            # explicit provider period and never uses this UTC fallback.
            month_start = datetime.strptime(month_key + "-01", "%Y-%m-%d").replace(tzinfo=timezone.utc)
            period_end = None
            period_selection = "UTC_CALENDAR_FALLBACK_NOT_FOR_REGISTRATION"
        else:
            month_start = _utc(provider_period_start_utc)
            period_end = _utc(provider_period_end_utc)
            if month_start >= period_end:
                raise ValueError("provider period must have a positive duration")
            period_selection = "EXPLICIT_PROVIDER_PERIOD"
        provider_cutoff = _utc(provider_measurement_cutoff_utc or cutoff)
        if provider_cutoff > cutoff:
            raise ValueError("provider measurement cutoff cannot be after the local SQL evidence cutoff")
        if period_end is not None and not month_start <= provider_cutoff < period_end:
            raise ValueError("provider measurement cutoff must be inside the provider period")
        if period_end is not None and not month_start <= cutoff < period_end:
            raise ValueError("local SQL evidence cutoff must be inside the provider period")
        if month_start > cutoff:
            raise ValueError("local usage cutoff must be after the provider period start")
        with self._connection() as connection:
            cursor = self._cursor(connection)
            permit_counts = self._quota_permit_reconciliation_counts(
                cursor,
                month=month_key,
                cutoff=provider_cutoff,
                observed_through=cutoff,
                lock=False,
            )
            cursor.execute(
                """
                SELECT COUNT_BIG(*) AS http_attempts,
                       COALESCE(SUM(CASE WHEN h.NetworkAttempted = 1 THEN 1 ELSE 0 END), 0) AS network_attempts,
                       COALESCE(SUM(CASE WHEN h.ProviderAttemptConfirmed = 1 THEN 1 ELSE 0 END), 0) AS confirmed_attempts,
                       COALESCE(SUM(CASE WHEN h.ConservativeQuotaAccounted = 1 THEN 1 ELSE 0 END), 0) AS unknown_attempts,
                       COALESCE(SUM(CASE WHEN h.QuotaPermitId IS NULL THEN 1 ELSE 0 END), 0) AS attempts_without_permit
                FROM ctl.MddMacHttpAttempt AS h
                INNER JOIN ctl.MddMacTargetAudit AS a ON a.AuditId = h.AuditId
                WHERE h.CompletedAtUtc <= %s
                  AND h.CompletedAtUtc >= %s
                """,
                (cutoff, month_start),
            )
            http_row = cursor.fetchone()
            cursor.execute(
                """
                SELECT CollectionContext, COUNT_BIG(*) AS run_count,
                       COALESCE(SUM(CASE WHEN Status = 'COMPLETE' THEN 1 ELSE 0 END), 0) AS complete_count,
                       COALESCE(SUM(CASE WHEN Status = 'FAILED' THEN 1 ELSE 0 END), 0) AS failed_count
                FROM ctl.MddMacCollectorRun
                WHERE StartedAtUtc >= %s AND StartedAtUtc <= %s
                GROUP BY CollectionContext
                ORDER BY CollectionContext
                """,
                (month_start, cutoff),
            )
            run_rows = cursor.fetchall()
            cursor.execute(
                """
                SELECT DecisionKind, CollectionContext, COUNT_BIG(*) AS decision_count
                FROM ctl.MddMacQuotaAudit
                WHERE RecordedAtUtc >= %s AND RecordedAtUtc <= %s
                GROUP BY DecisionKind, CollectionContext
                ORDER BY DecisionKind, CollectionContext
                """,
                (month_start, cutoff),
            )
            quota_audit_rows = cursor.fetchall()
            cursor.execute(
                """
                SELECT COUNT_BIG(*) AS run_count,
                       COALESCE(SUM(CASE WHEN Status = 'COMPLETE' THEN 1 ELSE 0 END), 0) AS complete_count,
                       COALESCE(SUM(CASE WHEN Status = 'FAILED' THEN 1 ELSE 0 END), 0) AS failed_count
                FROM ctl.MddCollectorRun
                WHERE (StartedAtUtc >= %s AND StartedAtUtc <= %s)
                   OR (CompletedAtUtc >= %s AND CompletedAtUtc <= %s)
                """,
                (month_start, cutoff, month_start, cutoff),
            )
            windows_row = cursor.fetchone()
            cursor.execute(
                """
                SELECT TOP (20) ImportBatchId, SourceDatabaseRole,
                       SourceCollectionContext, SourceSessionStartUtc,
                       SourceSessionEndUtc, Status, ImportStartedAtUtc,
                       ImportCompletedAtUtc
                FROM ctl.MddImportBatch
                WHERE COALESCE(SourceSessionEndUtc, ImportCompletedAtUtc, ImportStartedAtUtc) >= %s
                  AND COALESCE(SourceSessionStartUtc, ImportStartedAtUtc) <= %s
                ORDER BY ImportStartedAtUtc DESC
                """,
                (month_start, cutoff),
            )
            import_rows = cursor.fetchall()
            cursor.execute(
                """
                SELECT TOP (20) ReconciliationKey, SourceEvidenceHash,
                       BaselineAsOfUtc, LocalUsageIncludedThroughUtc,
                       AccountingStatus, RecordedAtUtc
                FROM ctl.MddMacSharedQuotaReconciliation
                WHERE MonthKey = %s
                ORDER BY RecordedAtUtc DESC, ReconciliationId DESC
                """,
                (month_key,),
            )
            prior_rows = cursor.fetchall()
        return {
            "month_key": month_key,
            "local_usage_included_through_utc": _iso(cutoff),
            "provider_measurement_cutoff_utc": _iso(provider_cutoff),
            "provider_period_start_utc": _iso(month_start),
            "provider_period_end_utc": _iso(period_end),
            "provider_period_selection": period_selection,
            "confirmed_through_cutoff": permit_counts["confirmed_through_cutoff"],
            "unknown_through_cutoff": permit_counts["unknown_through_cutoff"],
            "pending_through_cutoff": permit_counts["pending_through_cutoff"],
            "confirmed_after_cutoff": permit_counts["confirmed_after_cutoff"],
            "unknown_after_cutoff": permit_counts["unknown_after_cutoff"],
            "pending_after_cutoff": permit_counts["pending_after_cutoff"],
            "permit_counts": dict(permit_counts),
            "http_attempts": {
                "total": int(_row_named(http_row, "http_attempts", index=0) or 0),
                "network_attempts": int(_row_named(http_row, "network_attempts", index=1) or 0),
                "confirmed_attempts": int(_row_named(http_row, "confirmed_attempts", index=2) or 0),
                "unknown_attempts": int(_row_named(http_row, "unknown_attempts", index=3) or 0),
                "attempts_without_permit": int(_row_named(http_row, "attempts_without_permit", index=4) or 0),
            },
            "collector_activity": [
                {
                    "collection_context": str(_row_named(row, "CollectionContext", index=0) or ""),
                    "run_count": int(_row_named(row, "run_count", index=1) or 0),
                    "complete_count": int(_row_named(row, "complete_count", index=2) or 0),
                    "failed_count": int(_row_named(row, "failed_count", index=3) or 0),
                }
                for row in run_rows
            ],
            "windows_collector_activity": {
                "run_count": int(_row_named(windows_row, "run_count", index=0) or 0),
                "complete_count": int(_row_named(windows_row, "complete_count", index=1) or 0),
                "failed_count": int(_row_named(windows_row, "failed_count", index=2) or 0),
            },
            "quota_audit_activity": [
                {
                    "decision_kind": str(_row_named(row, "DecisionKind", index=0) or ""),
                    "collection_context": str(_row_named(row, "CollectionContext", index=1) or ""),
                    "decision_count": int(_row_named(row, "decision_count", index=2) or 0),
                }
                for row in quota_audit_rows
            ],
            "imported_research_evidence": [
                {
                    "import_batch_id": str(_row_named(row, "ImportBatchId", index=0) or ""),
                    "source_database_role": str(_row_named(row, "SourceDatabaseRole", index=1) or ""),
                    "source_collection_context": str(_row_named(row, "SourceCollectionContext", index=2) or ""),
                    "source_session_start_utc": _row_named(row, "SourceSessionStartUtc", index=3),
                    "source_session_end_utc": _row_named(row, "SourceSessionEndUtc", index=4),
                    "status": str(_row_named(row, "Status", index=5) or ""),
                    "import_started_at_utc": _row_named(row, "ImportStartedAtUtc", index=6),
                    "import_completed_at_utc": _row_named(row, "ImportCompletedAtUtc", index=7),
                }
                for row in import_rows
            ],
            "previous_reconciliations": [
                {
                    "reconciliation_key": str(_row_named(row, "ReconciliationKey", index=0) or ""),
                    "source_evidence_hash": str(_row_named(row, "SourceEvidenceHash", index=1) or ""),
                    "baseline_as_of_utc": _row_named(row, "BaselineAsOfUtc", index=2),
                    "local_usage_included_through_utc": _row_named(row, "LocalUsageIncludedThroughUtc", index=3),
                    "accounting_status": str(_row_named(row, "AccountingStatus", index=4) or ""),
                    "recorded_at_utc": _row_named(row, "RecordedAtUtc", index=5),
                }
                for row in prior_rows
            ],
        }

    def read_conservative_bootstrap_inputs(
        self,
        *,
        observed_through_utc: datetime,
        month_key: str = BOOTSTRAP_MONTH_KEY,
    ) -> Mapping[str, Any]:
        """Read the local ledger needed by the bounded bootstrap.

        This administrator/read-only path deliberately has no provider cutoff
        argument.  The dashboard cutoff is unknown, so all relevant local
        permits from the UTC October evidence window through the explicit
        approval cutoff are included in the conservative seed.
        """

        observed = _utc(observed_through_utc)
        if month_key != BOOTSTRAP_MONTH_KEY:
            raise MssqlSafetyError("conservative bootstrap is limited to October 2026")
        if observed.strftime("%Y-%m") != BOOTSTRAP_MONTH_KEY:
            raise MssqlSafetyError("bootstrap local evidence cutoff is outside October 2026")
        with self._connection() as connection:
            cursor = self._cursor(connection)
            counts = self._bootstrap_permit_counts(
                cursor,
                month=month_key,
                observed_through=observed,
                lock=False,
            )
        return {
            "month_key": month_key,
            "observed_through_utc": _iso(observed),
            "confirmed": int(counts["confirmed"]),
            "unknown": int(counts["unknown"]),
            "pending": int(counts["pending"]),
            "released_no_network": int(counts["released_no_network"]),
            "total": int(counts["total"]),
            "unexpected_statuses": list(counts["unexpected_statuses"]),
            "attempts_without_permit": int(counts["attempts_without_permit"]),
            "known_untracked_research_attempts": int(counts["known_untracked_research_attempts"]),
            "untracked_research_attempt_contexts": list(counts["untracked_research_attempt_contexts"]),
            "permit_counts": {
                "confirmed": int(counts["confirmed"]),
                "unknown": int(counts["unknown"]),
                "pending": int(counts["pending"]),
            },
            "accounting_rule": (
                "dashboard lower bound plus every confirmed, conservative-uncertain, "
                "pending October permit, plus explicitly identified pre-bootstrap research attempts, "
                "through the explicit approval cutoff; "
                "RELEASED_NO_NETWORK is not provider consumption"
            ),
        }

    def quota_administrator_context(self) -> Mapping[str, Any]:
        """Verify that the current connection is an administrative writer."""

        with self._connection() as connection:
            cursor = self._cursor(connection)
            cursor.execute(
                """
                SELECT SUSER_SNAME() AS login_name,
                       IS_SRVROLEMEMBER(N'sysadmin') AS is_sysadmin,
                       IS_ROLEMEMBER(N'db_owner') AS is_db_owner,
                       HAS_PERMS_BY_NAME(N'ctl.MddMacSharedQuotaAccounting', N'OBJECT', N'INSERT') AS baseline_insert,
                       HAS_PERMS_BY_NAME(N'ctl.MddMacSharedQuotaAccounting', N'OBJECT', N'UPDATE') AS baseline_update,
                       HAS_PERMS_BY_NAME(N'ctl.MddMacSharedQuotaReconciliation', N'OBJECT', N'INSERT') AS history_insert,
                       HAS_PERMS_BY_NAME(N'ctl.MddMacConservativeBootstrapAuthorization', N'OBJECT', N'INSERT') AS bootstrap_insert,
                       HAS_PERMS_BY_NAME(N'ctl.MddMacQuotaResetContract', N'OBJECT', N'INSERT') AS reset_contract_insert
                """
            )
            row = cursor.fetchone()
        result = {
            "login_name": str(_row_named(row, "login_name", index=0) or ""),
            "is_sysadmin": bool(_row_named(row, "is_sysadmin", index=1)),
            "is_db_owner": bool(_row_named(row, "is_db_owner", index=2)),
            "baseline_insert": bool(_row_named(row, "baseline_insert", index=3)),
            "baseline_update": bool(_row_named(row, "baseline_update", index=4)),
            "history_insert": bool(_row_named(row, "history_insert", index=5)),
            "bootstrap_insert": bool(_row_named(row, "bootstrap_insert", index=6)),
            "reset_contract_insert": bool(_row_named(row, "reset_contract_insert", index=7)),
        }
        if result["login_name"].casefold() == "colognetransitcollector":
            raise MssqlSafetyError("shared quota baseline registration requires a separate administrator login")
        if not (result["is_sysadmin"] or result["is_db_owner"]):
            if not all(
                result[key]
                for key in (
                    "baseline_insert",
                    "baseline_update",
                    "history_insert",
                    "bootstrap_insert",
                    "reset_contract_insert",
                )
            ):
                raise MssqlSafetyError("current login lacks the explicit shared quota registration privileges")
        return result

    def register_conservative_october_bootstrap(
        self,
        *,
        authorization: Mapping[str, Any],
        local_usage: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Atomically register the explicitly approved bounded bootstrap.

        This administrator-only write never changes the verified baseline,
        releases a permit, or rewrites an existing authorization. The active
        authorization row is locked before the local permit ledger, matching
        the runtime reservation path.
        """

        self.quota_administrator_context()
        mode = str(authorization.get("authorization_mode") or "")
        key = str(authorization.get("authorization_key") or "")
        source_hash = str(authorization.get("source_evidence_hash") or "")
        approval_at = _optional_utc(authorization.get("operator_approval_at_utc"))
        local_cutoff = _optional_utc(authorization.get("local_usage_included_through_utc"))
        stop_at = _optional_utc(authorization.get("stop_at_utc"))
        if mode != BOOTSTRAP_AUTHORIZATION_MODE or not key or not source_hash:
            raise MssqlSafetyError("validated bootstrap is missing its immutable mode, key, or evidence hash")
        if approval_at is None or local_cutoff is None or stop_at is None:
            raise MssqlSafetyError("validated bootstrap is missing its explicit validity interval")
        if len(source_hash) != 64 or not re.fullmatch(r"[0-9a-f]{64}", source_hash):
            raise MssqlSafetyError("validated bootstrap evidence hash is malformed")
        try:
            expected_counts = bootstrap_local_counts(local_usage)
        except ConservativeBootstrapError as exc:
            raise MssqlSafetyError(str(exc)) from exc
        declared_counts = {
            "confirmed": int(authorization.get("confirmed_local_attempts", 0) or 0),
            "unknown": int(authorization.get("conservative_unknown_local_attempts", 0) or 0),
            "pending": int(authorization.get("pending_local_reservations", 0) or 0),
        }
        if declared_counts != {name: expected_counts[name] for name in ("confirmed", "unknown", "pending")}:
            raise MssqlSafetyError("local SQL evidence does not match the validated bootstrap reconciliation")
        declared_total = int(authorization.get("accounted_total_at_authorization", -1) or -1)
        if declared_total != int(authorization.get("screenshot_cumulative_requests", 0) or 0) + expected_counts["total"]:
            raise MssqlSafetyError("bootstrap accounted total does not equal its components")
        proposed_row = {
            "MonthKey": str(authorization.get("month_key") or ""),
            "AuthorizationMode": mode,
            "AccountingModel": str(authorization.get("accounting_model") or ""),
            "AuthorizationStatus": str(authorization.get("authorization_status") or "ACTIVE"),
            "ScreenshotCumulativeRequests": authorization.get("screenshot_cumulative_requests"),
            "ScreenshotMonthlyLimit": authorization.get("screenshot_monthly_limit"),
            "ScreenshotSha256": authorization.get("screenshot_sha256"),
            "ConfirmedLocalAttempts": expected_counts["confirmed"],
            "ConservativeUnknownLocalAttempts": expected_counts["unknown"],
            "PendingLocalReservations": expected_counts["pending"],
            "AccountedTotalAtAuthorization": declared_total,
            "ProviderQuotaCeiling": authorization.get("provider_quota_ceiling"),
            "NormalOperationalMaximum": authorization.get("normal_operational_maximum"),
            "RetryProtectionBoundary": authorization.get("retry_protection_boundary"),
            "ValidFromUtc": authorization.get("valid_from_utc"),
            "LocalUsageIncludedThroughUtc": authorization.get("local_usage_included_through_utc"),
            "StopAtUtc": authorization.get("stop_at_utc"),
            "OperatorApprovalAtUtc": authorization.get("operator_approval_at_utc"),
            "OperatorApprovalStatementHash": authorization.get("operator_approval_statement_hash"),
            "CredentialExclusivityConfirmed": authorization.get("credential_exclusivity_confirmed"),
            "ProviderScopeVerified": authorization.get("provider_scope_verified"),
            "ProviderMeasurementCutoffEstablished": authorization.get(
                "provider_measurement_cutoff_established"
            ),
            "ProviderResetTimezoneEstablished": authorization.get("provider_reset_timezone_established"),
            "UnexpectedExternalUsage": authorization.get("unexpected_external_usage"),
            "SourceEvidenceHash": source_hash,
            "AuthorizationKey": key,
        }
        usable, reason = validate_stored_bootstrap_row(proposed_row, at_utc=approval_at)
        if not usable:
            raise MssqlSafetyError("bootstrap authorization failed immutable validation: " + reason)

        select_columns = """
            AuthorizationId, AuthorizationKey, MonthKey, AuthorizationMode,
            AccountingModel, ScreenshotCumulativeRequests, ScreenshotMonthlyLimit,
            ScreenshotSha256, ConfirmedLocalAttempts,
            ConservativeUnknownLocalAttempts, PendingLocalReservations,
            AccountedTotalAtAuthorization, ProviderQuotaCeiling,
            NormalOperationalMaximum, RetryProtectionBoundary, ValidFromUtc,
            LocalUsageIncludedThroughUtc, StopAtUtc, OperatorApprovalAtUtc,
            OperatorApprovalStatementHash, CredentialExclusivityConfirmed,
            ProviderScopeVerified, ProviderMeasurementCutoffEstablished,
            ProviderResetTimezoneEstablished, UnexpectedExternalUsage,
            SourceEvidenceHash, AuthorizationStatus, EvidenceJson, RecordedAtUtc,
            RevokedAtUtc, RevocationReason
        """
        with self._connection() as connection:
            cursor = self._cursor(connection)
            try:
                cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
                cursor.execute(
                    f"""
                    SELECT {select_columns}
                    FROM ctl.MddMacConservativeBootstrapAuthorization WITH (UPDLOCK, HOLDLOCK)
                    WHERE AuthorizationKey = %s
                    """,
                    (key,),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    existing_mapping = _row_mapping(existing, BOOTSTRAP_AUTHORIZATION_COLUMNS)
                    if str(existing_mapping.get("SourceEvidenceHash") or "") != source_hash:
                        raise MssqlSafetyError("bootstrap authorization key already exists with conflicting evidence")
                    if str(existing_mapping.get("AuthorizationStatus") or "") != "ACTIVE":
                        raise MssqlSafetyError("bootstrap authorization key already exists but is not active")
                    usable, reason = validate_stored_bootstrap_row(existing_mapping, at_utc=approval_at)
                    if not usable:
                        raise MssqlSafetyError("existing bootstrap authorization is not valid: " + reason)
                    cursor.execute("COMMIT TRANSACTION;")
                    connection.commit()
                    return {
                        "status": "IDEMPOTENT",
                        "authorization_key": key,
                        "source_evidence_hash": source_hash,
                        "writes_performed": 0,
                        "outstanding_permits_preserved": True,
                    }

                cursor.execute(
                    f"""
                    SELECT TOP (2) {select_columns}
                    FROM ctl.MddMacConservativeBootstrapAuthorization WITH (UPDLOCK, HOLDLOCK)
                    WHERE MonthKey = %s AND AuthorizationStatus = 'ACTIVE'
                    """,
                    (str(authorization.get("month_key") or BOOTSTRAP_MONTH_KEY),),
                )
                active_rows = list(cursor.fetchall())
                if active_rows:
                    raise MssqlSafetyError("another active October bootstrap authorization already exists")

                cursor.execute(
                    """
                    SELECT TOP (1) MonthKey
                    FROM ctl.MddMacSharedQuotaAccounting WITH (UPDLOCK, HOLDLOCK)
                    WHERE MonthKey = %s
                      AND AccountingStatus = 'COMPLETE'
                      AND ProviderPeriodStartUtc <= %s
                      AND ProviderPeriodEndUtc > %s
                    """,
                    (BOOTSTRAP_MONTH_KEY, approval_at, approval_at),
                )
                if cursor.fetchone() is not None:
                    raise MssqlSafetyError(
                        "a verified provider-period baseline already covers the bootstrap approval time"
                    )

                observed = self._bootstrap_permit_counts(
                    cursor,
                    month=BOOTSTRAP_MONTH_KEY,
                    observed_through=local_cutoff,
                    lock=True,
                )
                try:
                    observed_normalized = bootstrap_local_counts(observed)
                except ConservativeBootstrapError as exc:
                    raise MssqlSafetyError(str(exc)) from exc
                for name in ("confirmed", "unknown", "pending"):
                    if int(observed_normalized[name]) != int(expected_counts[name]):
                        raise MssqlSafetyError(
                            f"local permit counts changed during bootstrap registration for {name}; rerun the dry-run"
                        )
                if observed["unexpected_statuses"]:
                    raise MssqlSafetyError(
                        "local quota evidence contains unsupported statuses: "
                        + ", ".join(observed["unexpected_statuses"])
                    )

                cursor.execute(
                    """
                    INSERT INTO ctl.MddMacConservativeBootstrapAuthorization
                    (
                        AuthorizationKey, MonthKey, AuthorizationMode, AccountingModel,
                        ScreenshotCumulativeRequests, ScreenshotMonthlyLimit,
                        ScreenshotSha256, ConfirmedLocalAttempts,
                        ConservativeUnknownLocalAttempts, PendingLocalReservations,
                        AccountedTotalAtAuthorization, ProviderQuotaCeiling,
                        NormalOperationalMaximum, RetryProtectionBoundary,
                        ValidFromUtc, LocalUsageIncludedThroughUtc, StopAtUtc,
                        OperatorApprovalAtUtc, OperatorApprovalStatementHash,
                        CredentialExclusivityConfirmed, ProviderScopeVerified,
                        ProviderMeasurementCutoffEstablished,
                        ProviderResetTimezoneEstablished, UnexpectedExternalUsage,
                        SourceEvidenceHash, AuthorizationStatus, EvidenceJson
                    )
                    VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                     %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        key,
                        str(authorization.get("month_key") or BOOTSTRAP_MONTH_KEY),
                        mode,
                        str(authorization.get("accounting_model") or ""),
                        int(authorization.get("screenshot_cumulative_requests", 0) or 0),
                        int(authorization.get("screenshot_monthly_limit", 0) or 0),
                        str(authorization.get("screenshot_sha256") or ""),
                        expected_counts["confirmed"],
                        expected_counts["unknown"],
                        expected_counts["pending"],
                        declared_total,
                        int(authorization.get("provider_quota_ceiling", 0) or 0),
                        int(authorization.get("normal_operational_maximum", 0) or 0),
                        int(authorization.get("retry_protection_boundary", 0) or 0),
                        approval_at,
                        local_cutoff,
                        stop_at,
                        approval_at,
                        str(authorization.get("operator_approval_statement_hash") or ""),
                        int(bool(authorization.get("credential_exclusivity_confirmed"))),
                        int(bool(authorization.get("provider_scope_verified"))),
                        int(bool(authorization.get("provider_measurement_cutoff_established"))),
                        int(bool(authorization.get("provider_reset_timezone_established"))),
                        int(bool(authorization.get("unexpected_external_usage"))),
                        source_hash,
                        "ACTIVE",
                        str(authorization.get("evidence_json") or "{}"),
                    ),
                )
                cursor.execute("COMMIT TRANSACTION;")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return {
            "status": "APPLIED",
            "authorization_key": key,
            "source_evidence_hash": source_hash,
            "writes_performed": 1,
            "outstanding_permits_preserved": True,
            "provider_metadata_unverified": True,
        }

    def register_quota_reset_contract(self, *, contract: Mapping[str, Any]) -> Mapping[str, Any]:
        """Register one verified future-month reset contract idempotently."""

        self.quota_administrator_context()
        key = str(contract.get("contract_key") or "")
        provider_name = str(contract.get("provider_name") or "")
        reset_model = str(contract.get("reset_model") or "")
        timezone_name = str(contract.get("reset_timezone") or "")
        effective_from = _optional_utc(contract.get("effective_from_utc"))
        source_hash = str(contract.get("source_evidence_hash") or "")
        evidence_json = str(contract.get("evidence_json") or "")
        if not key or provider_name != "MDD NRW" or reset_model not in {
            "CALENDAR_MONTH_PROVIDER_TIMEZONE",
            "CALENDAR_MONTH_BOUNDED_TIMEZONE_UNCERTAINTY",
        }:
            raise MssqlSafetyError("reset contract is missing its immutable provider/reset model")
        if not timezone_name or effective_from is None or len(source_hash) != 64 or not re.fullmatch(r"[0-9a-f]{64}", source_hash):
            raise MssqlSafetyError("reset contract is missing a valid timezone, effective boundary, or evidence hash")
        if int(contract.get("activation_delay_seconds", 0) or 0) not in range(0, 86_401):
            raise MssqlSafetyError("reset contract activation delay is outside the approved range")
        if int(contract.get("provider_quota_ceiling", 0) or 0) != ABSOLUTE_PROVIDER_CEILING:
            raise MssqlSafetyError("reset contract does not preserve the approved provider ceiling")
        exact = reset_model == "CALENDAR_MONTH_PROVIDER_TIMEZONE"
        provider_verified = bool(contract.get("provider_reset_boundary_verified"))
        model_established = bool(contract.get("calendar_month_reset_model_established"))
        assumption_approved = bool(contract.get("operator_assumption_approved"))
        if not bool(contract.get("credential_exclusivity_confirmed")) or not model_established:
            raise MssqlSafetyError("reset contract lacks exclusive-credential or calendar-model approval")
        if exact and (not provider_verified or assumption_approved):
            raise MssqlSafetyError("exact reset contract lacks verified boundary evidence")
        if not exact and (provider_verified or not assumption_approved):
            raise MssqlSafetyError("bounded reset contract lacks explicit operator assumption approval")
        supported_timezones_json = (
            json.dumps(list(contract.get("supported_reset_timezones") or []), ensure_ascii=False, sort_keys=True)
            if not exact
            else None
        )
        with self._connection() as connection:
            cursor = self._cursor(connection)
            try:
                cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
                cursor.execute(
                    """
                    SELECT TOP (1) ContractKey, ResetModel, ResetTimezone,
                           EffectiveFromUtc, ActivationDelaySeconds,
                           ProviderQuotaCeiling, CredentialExclusivityConfirmed,
                           ProviderResetBoundaryVerified, CalendarMonthResetModelEstablished,
                           OperatorAssumptionApproved, SupportedResetTimezonesJson,
                           SourceEvidenceHash,
                           ContractStatus
                    FROM ctl.MddMacQuotaResetContract WITH (UPDLOCK, HOLDLOCK)
                    WHERE ContractKey = %s
                    """,
                    (key,),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    same = (
                        str(_row_named(existing, "ResetModel", index=1) or "") == reset_model
                        and str(_row_named(existing, "ResetTimezone", index=2) or "") == timezone_name
                        and _optional_utc(_row_named(existing, "EffectiveFromUtc", index=3)) == effective_from
                        and int(_row_named(existing, "ActivationDelaySeconds", index=4) or 0)
                        == int(contract["activation_delay_seconds"])
                        and int(_row_named(existing, "ProviderQuotaCeiling", index=5) or 0)
                        == ABSOLUTE_PROVIDER_CEILING
                        and bool(_row_named(existing, "CredentialExclusivityConfirmed", index=6))
                        and bool(_row_named(existing, "ProviderResetBoundaryVerified", index=7)) == provider_verified
                        and bool(_row_named(existing, "CalendarMonthResetModelEstablished", index=8)) == model_established
                        and bool(_row_named(existing, "OperatorAssumptionApproved", index=9)) == assumption_approved
                        and str(_row_named(existing, "SupportedResetTimezonesJson", index=10) or "")
                        == str(supported_timezones_json or "")
                        and str(_row_named(existing, "SourceEvidenceHash", index=11) or "") == source_hash
                    )
                    if same and str(_row_named(existing, "ContractStatus", index=12) or "") == "ACTIVE":
                        cursor.execute("COMMIT TRANSACTION;")
                        connection.commit()
                        return {
                            "status": "IDEMPOTENT",
                            "contract_key": key,
                            "writes_performed": 0,
                        }
                    raise MssqlSafetyError("reset contract key already exists with conflicting or revoked evidence")

                cursor.execute(
                    """
                    SELECT TOP (1) ContractKey
                    FROM ctl.MddMacQuotaResetContract WITH (UPDLOCK, HOLDLOCK)
                    WHERE ContractStatus = 'ACTIVE'
                    """
                )
                if cursor.fetchone() is not None:
                    raise MssqlSafetyError("another active MDD quota reset contract already exists")
                cursor.execute(
                    """
                    INSERT INTO ctl.MddMacQuotaResetContract
                    (
                        ContractKey, ProviderName, ResetModel, ResetTimezone,
                        EffectiveFromUtc, ActivationDelaySeconds, ProviderQuotaCeiling,
                        CredentialExclusivityConfirmed, ProviderResetBoundaryVerified,
                        CalendarMonthResetModelEstablished, OperatorAssumptionApproved,
                        SupportedResetTimezonesJson, SourceEvidenceHash, EvidenceJson,
                        ContractStatus
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'ACTIVE')
                    """,
                    (
                        key,
                        provider_name,
                        reset_model,
                        timezone_name,
                        effective_from,
                        int(contract["activation_delay_seconds"]),
                        ABSOLUTE_PROVIDER_CEILING,
                        1,
                        int(provider_verified),
                        int(model_established),
                        int(assumption_approved),
                        supported_timezones_json,
                        source_hash,
                        evidence_json,
                    ),
                )
                cursor.execute("COMMIT TRANSACTION;")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return {
            "status": "APPLIED",
            "contract_key": key,
            "writes_performed": 1,
        }

    def register_shared_quota_baseline(
        self,
        *,
        reconciliation: Mapping[str, Any],
        local_usage: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Atomically register one validated provider-wide baseline.

        Local permit counts are locked before the baseline row and
        reconciliation history, matching the reservation lock order.
        Outstanding permits are never deleted, released, or rewritten by this
        operation.
        """

        self.quota_administrator_context()
        month = str(reconciliation.get("month_key") or "")
        key = str(reconciliation.get("reconciliation_key") or "")
        source_hash = str(reconciliation.get("source_evidence_hash") or "")
        cutoff = _optional_utc(reconciliation.get("local_usage_included_through_utc"))
        provider_cutoff = _optional_utc(reconciliation.get("provider_measurement_cutoff_utc"))
        if not month or not key or not source_hash or cutoff is None or provider_cutoff is None:
            raise MssqlSafetyError("validated quota reconciliation is missing its immutable identity or cutoff")

        expected_local = dict(reconciliation.get("local_usage") or {})
        supplied_local = dict(local_usage.get("permit_counts") or local_usage)
        persisted_baseline_total = sum(
            int(reconciliation.get(name, 0) or 0)
            for name in (
                "confirmed_provider_attempts",
                "conservative_unknown_attempts",
                "explicit_shared_consumer_reserved_attempts",
            )
        )
        declared_baseline_total = int(
            reconciliation.get(
                "baseline_component_total",
                reconciliation.get("current_conservative_accounted_total", -1),
            )
            or 0
        )
        if persisted_baseline_total != declared_baseline_total:
            raise MssqlSafetyError(
                "validated quota reconciliation does not equal its persisted baseline components"
            )
        if persisted_baseline_total > ABSOLUTE_PROVIDER_CEILING:
            raise MssqlSafetyError("validated quota reconciliation exceeds the absolute provider ceiling")
        for name in (
            "confirmed_through_cutoff",
            "unknown_through_cutoff",
            "pending_through_cutoff",
            "confirmed_after_cutoff",
            "unknown_after_cutoff",
            "pending_after_cutoff",
        ):
            if int(supplied_local.get(name, 0) or 0) != int(expected_local.get(name, 0) or 0):
                raise MssqlSafetyError(
                    f"local SQL evidence does not match the validated reconciliation for {name}"
                )
        with self._connection() as connection:
            cursor = self._cursor(connection)
            try:
                cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
                # Match reserve_quota_attempt's lock order: serialize local
                # permit accounting before taking the monthly baseline lock.
                # This prevents a reservation holding a permit row from
                # deadlocking with a baseline writer holding the accounting
                # row and waiting for that same permit set.
                observed_local = self._quota_permit_reconciliation_counts(
                    cursor,
                    month=month,
                    cutoff=provider_cutoff,
                    observed_through=cutoff,
                    lock=True,
                )
                cursor.execute(
                    """
                    SELECT MonthKey, ProviderQuotaCeiling, NormalOperationalMaximum,
                           RetryProtectionBoundary, ConfirmedProviderAttempts,
                           ConservativeUnknownAttempts, ExplicitSharedConsumerReservedAttempts,
                           AccountingStatus, IncludesAllSharedConsumers, BaselineAsOfUtc,
                           LocalUsageIncludedThroughUtc, SourceDescription,
                           AccountingModel, ProviderMeasurementCutoffUtc, StaleAfterUtc,
                           SourceEvidenceHash, ReconciliationKey, ProviderPeriodTimezone,
                           ProviderPeriodStartUtc, ProviderPeriodEndUtc
                    FROM ctl.MddMacSharedQuotaAccounting WITH (UPDLOCK, HOLDLOCK)
                    WHERE MonthKey = %s
                    """,
                    (month,),
                )
                existing_baseline = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT ReconciliationId, SourceEvidenceHash, ReconciliationKey,
                           AccountingStatus
                    FROM ctl.MddMacSharedQuotaReconciliation WITH (UPDLOCK, HOLDLOCK)
                    WHERE ReconciliationKey = %s
                    """,
                    (key,),
                )
                existing_history = cursor.fetchone()
                if existing_history is not None:
                    old_hash = str(_row_named(existing_history, "SourceEvidenceHash", index=1) or "")
                    if old_hash != source_hash:
                        raise MssqlSafetyError("reconciliation key already exists with conflicting evidence")
                    if existing_baseline is None:
                        raise MssqlSafetyError("reconciliation history exists but the current baseline row is missing")
                    current = _row_mapping(existing_baseline, QUOTA_BASELINE_COLUMNS)
                    if str(current.get("SourceEvidenceHash") or "") != source_hash:
                        raise MssqlSafetyError("current baseline conflicts with its existing reconciliation history")
                    cursor.execute("COMMIT TRANSACTION;")
                    connection.commit()
                    return {
                        "status": "IDEMPOTENT",
                        "month_key": month,
                        "reconciliation_key": key,
                        "source_evidence_hash": source_hash,
                        "writes_performed": 0,
                    }

                if existing_baseline is not None:
                    current = _row_mapping(existing_baseline, QUOTA_BASELINE_COLUMNS)
                    old_cutoff = _optional_utc(current.get("ProviderMeasurementCutoffUtc"))
                    new_cutoff = _optional_utc(reconciliation.get("provider_measurement_cutoff_utc"))
                    if old_cutoff is not None and new_cutoff is not None and new_cutoff < old_cutoff:
                        raise MssqlSafetyError("new provider baseline is older than the current baseline")
                    old_local_cutoff = _optional_utc(current.get("LocalUsageIncludedThroughUtc"))
                    if old_local_cutoff is not None and cutoff < old_local_cutoff:
                        raise MssqlSafetyError(
                            "new local SQL accounting cutoff is older than the current baseline"
                        )
                    old_as_of = _optional_utc(current.get("BaselineAsOfUtc"))
                    new_as_of = _optional_utc(reconciliation.get("baseline_as_of_utc"))
                    if old_as_of is not None and new_as_of is not None and new_as_of < old_as_of:
                        raise MssqlSafetyError("new provider evidence timestamp is older than the current baseline")

                for name in (
                    "confirmed_through_cutoff",
                    "unknown_through_cutoff",
                    "pending_through_cutoff",
                    "confirmed_after_cutoff",
                    "unknown_after_cutoff",
                    "pending_after_cutoff",
                ):
                    expected = int(expected_local.get(name, 0) or 0)
                    if int(observed_local[name]) != expected:
                        raise MssqlSafetyError(
                            f"local permit counts changed during reconciliation for {name}; rerun the dry-run"
                        )

                cursor.execute(
                    """
                    INSERT INTO ctl.MddMacSharedQuotaReconciliation
                    (
                        ReconciliationKey, MonthKey, AccountingModel,
                        ProviderReportedCumulativeAttempts, ProviderReportedConfirmedAttempts,
                        ProviderReportedUnknownAttempts, UnexplainedDiscrepancyAttempts,
                        ProviderIncludesLocalUsage,
                        LocalUsageIncludedThroughUtc, LocalConfirmedAttemptsThroughCutoff,
                        LocalUnknownAttemptsThroughCutoff, LocalPendingReservationsThroughCutoff,
                        LocalConfirmedAttemptsAfterCutoff, LocalUnknownAttemptsAfterCutoff,
                        LocalPendingReservationsAfterCutoff, ExplicitSharedConsumerReservedAttempts,
                        ConservativeUnknownAttempts, AccountedTotalAtReconciliationUtc,
                        ProviderMeasurementCutoffUtc, BaselineAsOfUtc, StaleAfterUtc,
                        ProviderPeriodTimezone, ProviderPeriodStartUtc, ProviderPeriodEndUtc,
                        IncludesAllSharedConsumers, SourceDescription, SourceReference,
                        SourceEvidenceHash, OperatingModelEvidence, EvidenceJson,
                        AccountingStatus, RecordedAtUtc, AppliedAtUtc
                    )
                    VALUES
                    (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, 'APPLIED', SYSUTCDATETIME(), SYSUTCDATETIME()
                    )
                    """,
                    (
                        key,
                        month,
                        reconciliation["accounting_model"],
                        int(reconciliation["provider_reported_cumulative_attempts"]),
                        int(reconciliation["provider_reported_confirmed_attempts"]),
                        int(reconciliation["provider_reported_unknown_attempts"]),
                        int(reconciliation["unexplained_discrepancy_attempts"]),
                        int(bool(reconciliation["provider_includes_local_usage"])),
                        cutoff,
                        int(expected_local.get("confirmed_through_cutoff", 0) or 0),
                        int(expected_local.get("unknown_through_cutoff", 0) or 0),
                        int(expected_local.get("pending_through_cutoff", 0) or 0),
                        int(expected_local.get("confirmed_after_cutoff", 0) or 0),
                        int(expected_local.get("unknown_after_cutoff", 0) or 0),
                        int(expected_local.get("pending_after_cutoff", 0) or 0),
                        int(reconciliation["explicit_shared_consumer_reserved_attempts"]),
                        int(reconciliation["conservative_unknown_attempts"]),
                        int(reconciliation["current_conservative_accounted_total"]),
                        _optional_utc(reconciliation["provider_measurement_cutoff_utc"]),
                        _optional_utc(reconciliation["baseline_as_of_utc"]),
                        _optional_utc(reconciliation["stale_after_utc"]),
                        reconciliation["provider_period_timezone"],
                        _optional_utc(reconciliation["provider_period_start_utc"]),
                        _optional_utc(reconciliation["provider_period_end_utc"]),
                        1,
                        reconciliation["source_description"],
                        reconciliation["source_reference"],
                        source_hash,
                        reconciliation["operating_model_evidence"],
                        reconciliation["evidence_json"],
                    ),
                )
                cursor.execute(
                    """
                    UPDATE ctl.MddMacSharedQuotaAccounting
                    SET ProviderQuotaCeiling = %s,
                        NormalOperationalMaximum = %s,
                        RetryProtectionBoundary = %s,
                        ConfirmedProviderAttempts = %s,
                        ConservativeUnknownAttempts = %s,
                        ExplicitSharedConsumerReservedAttempts = %s,
                        AccountingStatus = 'COMPLETE',
                        IncludesAllSharedConsumers = 1,
                        BaselineAsOfUtc = %s,
                        LocalUsageIncludedThroughUtc = %s,
                        SourceDescription = %s,
                        AccountingModel = %s,
                        ProviderMeasurementCutoffUtc = %s,
                        StaleAfterUtc = %s,
                        SourceEvidenceHash = %s,
                        ReconciliationKey = %s,
                        ProviderPeriodTimezone = %s,
                        ProviderPeriodStartUtc = %s,
                        ProviderPeriodEndUtc = %s,
                        RecordedAtUtc = SYSUTCDATETIME()
                    WHERE MonthKey = %s
                    """,
                    (
                        int(reconciliation["provider_quota_ceiling"]),
                        int(reconciliation["normal_operational_maximum"]),
                        int(reconciliation["retry_protection_boundary"]),
                        int(reconciliation["confirmed_provider_attempts"]),
                        int(reconciliation["conservative_unknown_attempts"]),
                        int(reconciliation["explicit_shared_consumer_reserved_attempts"]),
                        _optional_utc(reconciliation["baseline_as_of_utc"]),
                        cutoff,
                        reconciliation["source_description"],
                        reconciliation["accounting_model"],
                        _optional_utc(reconciliation["provider_measurement_cutoff_utc"]),
                        _optional_utc(reconciliation["stale_after_utc"]),
                        source_hash,
                        key,
                        reconciliation["provider_period_timezone"],
                        _optional_utc(reconciliation["provider_period_start_utc"]),
                        _optional_utc(reconciliation["provider_period_end_utc"]),
                        month,
                    ),
                )
                if getattr(cursor, "rowcount", 1) == 0:
                    cursor.execute(
                        """
                        INSERT INTO ctl.MddMacSharedQuotaAccounting
                        (
                            MonthKey, ProviderQuotaCeiling, NormalOperationalMaximum,
                            RetryProtectionBoundary, ConfirmedProviderAttempts,
                            ConservativeUnknownAttempts, ExplicitSharedConsumerReservedAttempts,
                            AccountingStatus, IncludesAllSharedConsumers, BaselineAsOfUtc,
                            LocalUsageIncludedThroughUtc, SourceDescription, AccountingModel,
                            ProviderMeasurementCutoffUtc, StaleAfterUtc, SourceEvidenceHash,
                            ReconciliationKey, ProviderPeriodTimezone, ProviderPeriodStartUtc,
                            ProviderPeriodEndUtc
                        )
                        VALUES
                        (%s, %s, %s, %s, %s, %s, %s, 'COMPLETE', 1, %s, %s, %s,
                         %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            month,
                            int(reconciliation["provider_quota_ceiling"]),
                            int(reconciliation["normal_operational_maximum"]),
                            int(reconciliation["retry_protection_boundary"]),
                            int(reconciliation["confirmed_provider_attempts"]),
                            int(reconciliation["conservative_unknown_attempts"]),
                            int(reconciliation["explicit_shared_consumer_reserved_attempts"]),
                            _optional_utc(reconciliation["baseline_as_of_utc"]),
                            cutoff,
                            reconciliation["source_description"],
                            reconciliation["accounting_model"],
                            _optional_utc(reconciliation["provider_measurement_cutoff_utc"]),
                            _optional_utc(reconciliation["stale_after_utc"]),
                            source_hash,
                            key,
                            reconciliation["provider_period_timezone"],
                            _optional_utc(reconciliation["provider_period_start_utc"]),
                            _optional_utc(reconciliation["provider_period_end_utc"]),
                        ),
                    )
                cursor.execute("COMMIT TRANSACTION;")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return {
            "status": "APPLIED",
            "month_key": month,
            "reconciliation_key": key,
            "source_evidence_hash": source_hash,
            "writes_performed": 2,
            "outstanding_permits_preserved": True,
        }

    def reserve_quota_attempt(
        self,
        *,
        decision_kind: str,
        boundary: int,
        proposed_attempts: int = 1,
        at_utc: datetime | None = None,
        reservation_scope: str = "LIVE_PROVIDER",
        authorization_key: str | None = None,
    ) -> Mapping[str, Any]:
        amount = int(proposed_attempts)
        if amount <= 0:
            raise ValueError("proposed_attempts must be positive")
        requested_at = _utc(at_utc or datetime.now(timezone.utc))
        if decision_kind not in {"INITIAL", "RETRY"}:
            raise ValueError("decision_kind must be INITIAL or RETRY")
        effective_boundary = approved_quota_boundary(decision_kind, boundary)
        key = str(authorization_key) if authorization_key else None
        if key is not None and len(key) > 240:
            raise ValueError("authorization_key must be at most 240 characters")
        permit_id = str(uuid.uuid4())
        # Read once before rollover so a verified new provider period can be
        # materialized, then read again on the reservation connection.  The
        # second read is the timestamp used by every authorization query and
        # the permit insert in that transaction; a local/caller clock can
        # never substitute for either SQL read.
        at = self._authoritative_authorization_time(requested_at)
        self.ensure_automatic_quota_rollover(at_utc=at)
        with self._connection() as connection:
            cursor = self._cursor(connection)
            try:
                cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
                at = self._authoritative_authorization_time(requested_at, cursor=cursor)
                candidates = self._provider_period_baseline_candidates(cursor, at=at, lock=False)
                if len(candidates) > 1:
                    cursor.execute("COMMIT TRANSACTION;")
                    connection.commit()
                    decision = (
                        "INITIAL_DENIED_QUOTA_STATE_UNAVAILABLE"
                        if decision_kind == "INITIAL"
                        else "RETRY_DENIED_QUOTA_STATE_UNAVAILABLE"
                    )
                    return {
                        "allowed": False,
                        "decision": decision,
                        "proposed_attempts": amount,
                        "known_total": None,
                        "boundary": effective_boundary,
                        "reason": "current provider billing period is ambiguous",
                        "permit_id": None,
                        "authorization_at_utc": _iso(at),
                    }
                baseline = candidates[0] if candidates else None
                bootstrap = None
                if baseline is None or self._quota_baseline_row(
                    cursor,
                    month=str(_row_named(baseline, "MonthKey", index=0) or ""),
                    at=at,
                ) is None:
                    bootstrap_candidates = self._conservative_bootstrap_candidates(cursor, at=at, lock=True)
                    if len(bootstrap_candidates) != 1:
                        cursor.execute("COMMIT TRANSACTION;")
                        connection.commit()
                        decision = (
                            "INITIAL_DENIED_QUOTA_STATE_UNAVAILABLE"
                            if decision_kind == "INITIAL"
                            else "RETRY_DENIED_QUOTA_STATE_UNAVAILABLE"
                        )
                        reason = (
                            "current provider-wide shared-credential quota evidence is not registrable"
                            if not bootstrap_candidates
                            else "multiple active bounded bootstrap authorizations cover the authorization time"
                        )
                        return {
                            "allowed": False,
                            "decision": decision,
                            "proposed_attempts": amount,
                            "known_total": None,
                            "boundary": effective_boundary,
                            "reason": reason,
                            "permit_id": None,
                            "authorization_at_utc": _iso(at),
                        }
                    bootstrap = bootstrap_candidates[0]
                    month = str(_row_named(bootstrap, "MonthKey", index=2) or "")
                else:
                    month = str(_row_named(baseline, "MonthKey", index=0) or "")
                # A stale pending permit is conservatively finalized while the
                # same transaction holds the month/accounting lock.
                stale_cutoff = at - timedelta(seconds=300)
                cursor.execute(
                    """
                    UPDATE ctl.MddMacQuotaPermit
                    SET Status = %s,
                        FinalizedAtUtc = %s,
                        ReleasedAtUtc = NULL,
                        FinalizationReason = %s,
                        LiveClientInvocation = 1
                    WHERE MonthKey = %s AND Status = %s AND ReservedAtUtc < %s
                    """,
                    (
                        CONSERVATIVE_LIVE_ATTEMPT,
                        at,
                        "CRASH_RECOVERY_CONSERVATIVE",
                        month,
                        PENDING_AUTHORIZATION,
                        stale_cutoff,
                    ),
                )
                # Idempotent authorization replay is resolved before reading
                # the baseline.  Once a permit exists, a repeated client
                # invocation must not become a second reservation merely
                # because the external baseline is temporarily unavailable.
                if key is not None:
                    cursor.execute(
                        """
                        SELECT PermitId, MonthKey, DecisionKind, ReservationScope, Amount,
                               ReservedAtUtc, Status
                        FROM ctl.MddMacQuotaPermit WITH (UPDLOCK, HOLDLOCK)
                        WHERE AuthorizationKey = %s
                        """,
                        (key,),
                    )
                    existing = cursor.fetchone()
                    if existing is not None:
                        existing_permit = str(_row_named(existing, "PermitId", index=0))
                        same_contract = (
                            str(_row_named(existing, "MonthKey", index=1) or "") == month
                            and str(_row_named(existing, "DecisionKind", index=2) or "") == decision_kind
                            and str(_row_named(existing, "ReservationScope", index=3) or "") == str(reservation_scope)
                            and int(_row_named(existing, "Amount", index=4) or 0) == amount
                        )
                        existing_status = str(_row_named(existing, "Status", index=6) or "")
                        cursor.execute("COMMIT TRANSACTION;")
                        connection.commit()
                        if not same_contract:
                            return {
                                "allowed": False,
                                "decision": "DUPLICATE_AUTHORIZATION_CONFLICT",
                                "proposed_attempts": amount,
                                "known_total": None,
                                "boundary": effective_boundary,
                                "reason": "authorization key was already used with a different permit contract",
                                "permit_id": None,
                                "authorization_at_utc": _iso(at),
                            }
                        if existing_status == PENDING_AUTHORIZATION:
                            if str(_row_named(existing, "MonthKey", index=1) or "") != month:
                                return {
                                    "allowed": False,
                                    "decision": "DUPLICATE_AUTHORIZATION_EXPIRED",
                                    "proposed_attempts": amount,
                                    "known_total": None,
                                    "boundary": effective_boundary,
                                    "reason": "pending permit belongs to a prior provider accounting period",
                                    "permit_id": None,
                                    "authorization_at_utc": _iso(at),
                                }
                            return {
                                "allowed": True,
                                "decision": "DUPLICATE_AUTHORIZATION_REUSED",
                                "proposed_attempts": amount,
                                "known_total": None,
                                "boundary": effective_boundary,
                                "reason": "existing pending permit reused idempotently",
                                "permit_id": existing_permit,
                                "authorization_at_utc": _iso(at),
                            }
                        return {
                            "allowed": False,
                            "decision": "DUPLICATE_AUTHORIZATION_FINALIZED",
                            "proposed_attempts": amount,
                            "known_total": None,
                            "boundary": effective_boundary,
                            "reason": "authorization key already has a finalized permit",
                            "permit_id": None,
                            "authorization_at_utc": _iso(at),
                        }
                valid_until_utc: datetime | None = None
                if bootstrap is not None:
                    bootstrap_mapping = _row_mapping(bootstrap, BOOTSTRAP_AUTHORIZATION_COLUMNS)
                    valid_bootstrap, bootstrap_reason = validate_stored_bootstrap_row(
                        bootstrap_mapping,
                        at_utc=at,
                    )
                    if not valid_bootstrap:
                        cursor.execute("COMMIT TRANSACTION;")
                        connection.commit()
                        decision = (
                            "INITIAL_DENIED_QUOTA_STATE_UNAVAILABLE"
                            if decision_kind == "INITIAL"
                            else "RETRY_DENIED_QUOTA_STATE_UNAVAILABLE"
                        )
                        return {
                            "allowed": False,
                            "decision": decision,
                            "proposed_attempts": amount,
                            "known_total": None,
                            "boundary": effective_boundary,
                            "reason": bootstrap_reason,
                            "permit_id": None,
                            "authorization_at_utc": _iso(at),
                        }
                    valid_until_utc = _optional_utc(bootstrap_mapping["StopAtUtc"])
                    local_cutoff = _optional_utc(bootstrap_mapping["LocalUsageIncludedThroughUtc"])
                else:
                    valid_until_utc = _optional_utc(_row_named(baseline, "ProviderPeriodEndUtc", index=19))
                    local_cutoff = _optional_utc(_row_named(baseline, "LocalUsageIncludedThroughUtc", index=10))
                assert local_cutoff is not None
                local = self._quota_usage_totals(
                    cursor,
                    month=month,
                    local_cutoff=local_cutoff,
                    observed_through=at,
                    include_cutoff=(
                        bootstrap is None
                        and str(_row_named(baseline, "AccountingModel", index=12) or "")
                        == AUTOMATIC_ROLLOVER_ACCOUNTING_MODEL
                    ),
                )
                if bootstrap is not None:
                    baseline_total = (
                        int(_row_named(bootstrap, "ScreenshotCumulativeRequests", index=5) or 0)
                        + int(_row_named(bootstrap, "ConfirmedLocalAttempts", index=8) or 0)
                        + int(_row_named(bootstrap, "ConservativeUnknownLocalAttempts", index=9) or 0)
                        + int(_row_named(bootstrap, "PendingLocalReservations", index=10) or 0)
                    )
                else:
                    baseline_total = (
                        int(_row_named(baseline, "ConfirmedProviderAttempts", index=4) or 0)
                        + int(_row_named(baseline, "ConservativeUnknownAttempts", index=5) or 0)
                        + int(_row_named(baseline, "ExplicitSharedConsumerReservedAttempts", index=6) or 0)
                    )
                known_total = baseline_total + local["confirmed"] + local["conservative"] + local["pending"]

                projected = known_total + amount
                if projected > ABSOLUTE_PROVIDER_CEILING:
                    cursor.execute("COMMIT TRANSACTION;")
                    connection.commit()
                    return {
                        "allowed": False,
                        "decision": f"{decision_kind}_DENIED_PROVIDER_CEILING",
                        "proposed_attempts": amount,
                        "known_total": known_total,
                        "boundary": ABSOLUTE_PROVIDER_CEILING,
                        "reason": "provider quota ceiling would be exceeded",
                        "permit_id": None,
                        "authorization_at_utc": _iso(at),
                    }
                if projected > effective_boundary:
                    cursor.execute("COMMIT TRANSACTION;")
                    connection.commit()
                    return {
                        "allowed": False,
                        "decision": (
                            "INITIAL_DENIED_NORMAL_OPERATIONAL_MAXIMUM"
                            if decision_kind == "INITIAL"
                            else "RETRY_DENIED_PROTECTION_BOUNDARY"
                        ),
                        "proposed_attempts": amount,
                        "known_total": known_total,
                        "boundary": effective_boundary,
                        "reason": "quota boundary would be exceeded by this atomic reservation",
                        "permit_id": None,
                        "authorization_at_utc": _iso(at),
                    }
                cursor.execute(
                    """
                    INSERT INTO ctl.MddMacQuotaPermit
                        (PermitId, MonthKey, DecisionKind, ReservationScope, Amount,
                         Boundary, ReservedAtUtc, AuthorizationKey, Status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        permit_id,
                        month,
                        decision_kind,
                        reservation_scope,
                        amount,
                        effective_boundary,
                        at,
                        key,
                        PENDING_AUTHORIZATION,
                    ),
                )
                cursor.execute("COMMIT TRANSACTION;")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return {
            "allowed": True,
            "decision": "INITIAL_ALLOWED" if decision_kind == "INITIAL" else "RETRY_ALLOWED",
            "proposed_attempts": amount,
            "known_total": known_total,
            "boundary": effective_boundary,
            "reason": "within atomic provider-wide quota headroom",
            "permit_id": permit_id,
            "authorization_at_utc": _iso(at),
            "valid_until_utc": _iso(valid_until_utc),
        }

    authorize_attempt = reserve_quota_attempt

    def _finalize_quota_permit_cursor(
        self,
        cursor: Any,
        *,
        permit_id: Any,
        finalization: str,
        finalized_at: datetime,
        live_client_invocation: bool,
    ) -> None:
        if finalization not in {RELEASED_NO_NETWORK, CONSERVATIVE_LIVE_ATTEMPT, HTTP_PROVIDER_ATTEMPT_CONFIRMED}:
            raise ValueError(f"unknown quota finalization: {finalization}")
        cursor.execute(
            """
            UPDATE ctl.MddMacQuotaPermit
            SET Status = %s,
                FinalizedAtUtc = %s,
                ReleasedAtUtc = CASE WHEN %s = %s THEN %s ELSE NULL END,
                FinalizationReason = %s,
                LiveClientInvocation = %s
            WHERE PermitId = %s AND Status = %s
            """,
            (
                finalization,
                _utc(finalized_at),
                finalization,
                RELEASED_NO_NETWORK,
                _utc(finalized_at),
                {
                    RELEASED_NO_NETWORK: "NO_NETWORK_CONFIRMED",
                    CONSERVATIVE_LIVE_ATTEMPT: "LIVE_ATTEMPT_UNCERTAIN",
                    HTTP_PROVIDER_ATTEMPT_CONFIRMED: "HTTP_RESPONSE_CONFIRMED",
                }[finalization],
                int(live_client_invocation),
                str(permit_id),
                PENDING_AUTHORIZATION,
            ),
        )
        if getattr(cursor, "rowcount", 1) == 0:
            cursor.execute("SELECT Status FROM ctl.MddMacQuotaPermit WHERE PermitId = %s", (str(permit_id),))
            row = cursor.fetchone()
            if row is None:
                raise KeyError(f"unknown quota permit: {permit_id}")

    def _finalize_quota_permit(
        self,
        *,
        permit_id: Any,
        finalization: str,
        at_utc: datetime | None = None,
        live_client_invocation: bool = False,
    ) -> Mapping[str, Any]:
        if permit_id is None:
            return {"applied": False, "status": finalization, "permit_id": None}
        with self._connection() as connection:
            cursor = self._cursor(connection)
            try:
                cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
                cursor.execute("SELECT Status, Amount FROM ctl.MddMacQuotaPermit WHERE PermitId = %s", (str(permit_id),))
                row = cursor.fetchone()
                if row is None:
                    raise KeyError(f"unknown quota permit: {permit_id}")
                previous = str(_row_value(row, "Status", 0))
                self._finalize_quota_permit_cursor(
                    cursor,
                    permit_id=permit_id,
                    finalization=finalization,
                    finalized_at=at_utc or datetime.now(timezone.utc),
                    live_client_invocation=live_client_invocation,
                )
                cursor.execute("COMMIT TRANSACTION;")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return {
            "applied": previous == PENDING_AUTHORIZATION,
            "status": finalization if previous == PENDING_AUTHORIZATION else previous,
            "permit_id": str(permit_id),
            "amount": int(_row_value(row, "Amount", 1) or 0),
        }

    def finalize_quota_permit_no_network(self, *, permit_id: Any, at_utc: datetime | None = None, live_client_invocation: bool = False) -> Mapping[str, Any]:
        return self._finalize_quota_permit(permit_id=permit_id, finalization=RELEASED_NO_NETWORK, at_utc=at_utc, live_client_invocation=live_client_invocation)

    def finalize_quota_permit_conservative(self, *, permit_id: Any, at_utc: datetime | None = None, live_client_invocation: bool = True) -> Mapping[str, Any]:
        return self._finalize_quota_permit(permit_id=permit_id, finalization=CONSERVATIVE_LIVE_ATTEMPT, at_utc=at_utc, live_client_invocation=live_client_invocation)

    def finalize_quota_permit_http_response(self, *, permit_id: Any, at_utc: datetime | None = None, live_client_invocation: bool = True) -> Mapping[str, Any]:
        return self._finalize_quota_permit(permit_id=permit_id, finalization=HTTP_PROVIDER_ATTEMPT_CONFIRMED, at_utc=at_utc, live_client_invocation=live_client_invocation)

    def release_quota_attempt(self, *, permit_id: Any) -> None:
        self.finalize_quota_permit_no_network(permit_id=permit_id)

    def record_sampling_era_cutover(
        self,
        *,
        era_code: str,
        panel_version: str,
        effective_at_utc: datetime | None = None,
        timestamp_kind: str = "NOT_RECORDED",
        is_active: bool = False,
        is_synthetic: bool = False,
    ) -> int:
        """Record an era without duplicating or mutating append-only history."""

        result = self.record_sampling_era_cutover_idempotent(
            era_code=era_code,
            panel_version=panel_version,
            effective_at_utc=effective_at_utc,
            timestamp_kind=timestamp_kind,
            is_active=is_active,
            is_synthetic=is_synthetic,
        )
        return int(result["sampling_era_id"])

    def record_sampling_era_cutover_idempotent(
        self,
        *,
        era_code: str,
        panel_version: str,
        effective_at_utc: datetime | None = None,
        timestamp_kind: str = "NOT_RECORDED",
        is_active: bool = False,
        is_synthetic: bool = False,
    ) -> Mapping[str, Any]:
        """Return an equivalent non-synthetic era or append exactly one row.

        The history table deliberately has no mutable update path.  A locked
        read inside the same transaction as the optional insert makes an
        operator rerun safe while still failing closed if the corrected
        production era has conflicting or duplicate history.
        """

        normalized_era_code = str(era_code)
        normalized_panel_version = str(panel_version)
        normalized_timestamp_kind = str(timestamp_kind)
        normalized_effective_at = _utc(effective_at_utc) if effective_at_utc else None
        if is_active and normalized_effective_at is None:
            raise MssqlSafetyError("active sampling era requires an explicit effective UTC timestamp")
        if is_active and normalized_effective_at > datetime.now(timezone.utc):
            raise MssqlSafetyError("active sampling era effective timestamp cannot be in the future")

        with self._connection() as connection:
            cursor = self._cursor(connection)
            cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
            existing_rows: list[Any] = []
            if not is_synthetic:
                cursor.execute(
                    """
                    SELECT SamplingEraId, EraCode, PanelVersion, EffectiveAtUtc,
                           TimestampKind, IsActive, IsSynthetic
                    FROM ctl.MddMacSamplingEra WITH (UPDLOCK, HOLDLOCK)
                    WHERE IsSynthetic = 0
                    ORDER BY SamplingEraId
                    """
                )
                existing_rows = list(cursor.fetchall())

                same_code = [
                    row
                    for row in existing_rows
                    if str(_row_named(row, "EraCode", index=1) or "") == normalized_era_code
                ]
                exact = [
                    row
                    for row in same_code
                    if str(_row_named(row, "PanelVersion", index=2) or "") == normalized_panel_version
                    and _optional_utc(_row_named(row, "EffectiveAtUtc", index=3)) == _optional_utc(normalized_effective_at)
                    and str(_row_named(row, "TimestampKind", index=4) or "") == normalized_timestamp_kind
                    and bool(_row_named(row, "IsActive", index=5)) == bool(is_active)
                ]
                if len(exact) > 1:
                    raise MssqlSafetyError(
                        "duplicate corrected-production sampling era exists for " + normalized_era_code
                    )
                if len(exact) == 1:
                    if len(same_code) != 1:
                        raise MssqlSafetyError(
                            "conflicting corrected-production sampling era exists for " + normalized_era_code
                        )
                    era_id = int(_row_named(exact[0], "SamplingEraId", index=0))
                    if is_active and not is_synthetic:
                        self._upsert_current_production_era(
                            cursor,
                            sampling_era_id=era_id,
                            era_code=normalized_era_code,
                            panel_version=normalized_panel_version,
                            effective_at=normalized_effective_at,
                            recorded_at=datetime.now(timezone.utc),
                        )
                    connection.commit()
                    return {
                        "sampling_era_id": era_id,
                        "action": "REUSED",
                        "rows_written": 0,
                    }
                if same_code:
                    raise MssqlSafetyError(
                        "conflicting corrected-production sampling era exists for " + normalized_era_code
                    )

                if is_active:
                    active_same_panel = [
                        row
                        for row in existing_rows
                        if str(_row_named(row, "PanelVersion", index=2) or "") == normalized_panel_version
                        and bool(_row_named(row, "IsActive", index=5))
                    ]
                    if active_same_panel:
                        raise MssqlSafetyError(
                            "another active non-synthetic sampling era exists for panel "
                            + normalized_panel_version
                        )

            cursor.execute(
                """
                INSERT INTO ctl.MddMacSamplingEra
                    (EraCode, PanelVersion, EffectiveAtUtc, TimestampKind, IsActive, IsSynthetic)
                OUTPUT INSERTED.SamplingEraId
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    normalized_era_code,
                    normalized_panel_version,
                    normalized_effective_at,
                    normalized_timestamp_kind,
                    int(is_active),
                    int(is_synthetic),
                ),
            )
            era_id = int(_row_value(cursor.fetchone(), "SamplingEraId", 0))
            if is_active and not is_synthetic:
                self._upsert_current_production_era(
                    cursor,
                    sampling_era_id=era_id,
                    era_code=normalized_era_code,
                    panel_version=normalized_panel_version,
                    effective_at=normalized_effective_at,
                    recorded_at=datetime.now(timezone.utc),
                )
            connection.commit()
        return {
            "sampling_era_id": era_id,
            "action": "INSERTED",
            "rows_written": 1,
        }

    @staticmethod
    def _upsert_current_production_era(
        cursor: Any,
        *,
        sampling_era_id: int,
        era_code: str,
        panel_version: str,
        effective_at: datetime | None,
        recorded_at: datetime,
    ) -> None:
        if effective_at is None:
            raise MssqlSafetyError("current production era requires an effective UTC timestamp")
        cursor.execute(
            """
            UPDATE ctl.MddMacCurrentProductionEra
            SET SamplingEraId = %s,
                EraCode = %s,
                PanelVersion = %s,
                EffectiveAtUtc = %s,
                RecordedAtUtc = %s
            WHERE CurrentMarkerId = 1
            """,
            (int(sampling_era_id), era_code, panel_version, _utc(effective_at), _utc(recorded_at)),
        )
        if getattr(cursor, "rowcount", 1) == 0:
            cursor.execute(
                """
                INSERT INTO ctl.MddMacCurrentProductionEra
                    (CurrentMarkerId, SamplingEraId, EraCode, PanelVersion, EffectiveAtUtc, RecordedAtUtc)
                VALUES (1, %s, %s, %s, %s, %s)
                """,
                (int(sampling_era_id), era_code, panel_version, _utc(effective_at), _utc(recorded_at)),
            )

    def record_methodology_evidence(
        self,
        *,
        era_code: str,
        panel_version: str,
        collection_context: str,
        methodology_version: str,
        run_ids: Sequence[str],
        evidence: Mapping[str, Any],
        window_start_utc: datetime | None = None,
        window_end_utc: datetime | None = None,
        actual_initial_attempts: int | None = None,
        historical_equivalent_initial_attempts: int | None = None,
        excess_initial_attempts: int | None = None,
    ) -> int:
        """Record bounded methodology evidence without duplicating history."""

        result = self.record_methodology_evidence_idempotent(
            era_code=era_code,
            panel_version=panel_version,
            collection_context=collection_context,
            methodology_version=methodology_version,
            run_ids=run_ids,
            evidence=evidence,
            window_start_utc=window_start_utc,
            window_end_utc=window_end_utc,
            actual_initial_attempts=actual_initial_attempts,
            historical_equivalent_initial_attempts=historical_equivalent_initial_attempts,
            excess_initial_attempts=excess_initial_attempts,
        )
        return int(result["methodology_evidence_id"])

    def record_methodology_evidence_idempotent(
        self,
        *,
        era_code: str,
        panel_version: str,
        collection_context: str,
        methodology_version: str,
        run_ids: Sequence[str],
        evidence: Mapping[str, Any],
        window_start_utc: datetime | None = None,
        window_end_utc: datetime | None = None,
        actual_initial_attempts: int | None = None,
        historical_equivalent_initial_attempts: int | None = None,
        excess_initial_attempts: int | None = None,
    ) -> Mapping[str, Any]:
        """Return equivalent methodology history or append exactly one row.

        Run-set identity is compared from decoded JSON rather than raw JSON
        text, so harmless ordering differences cannot create a duplicate.  A
        same-run-set conflict or duplicate matching history is an explicit
        safety failure; no existing row is ever updated or deleted.
        """

        normalized_run_ids = tuple(str(run_id) for run_id in run_ids)
        canonical_run_ids = _canonical_run_ids(normalized_run_ids)
        run_ids_json = json.dumps(list(normalized_run_ids), ensure_ascii=False, sort_keys=True)
        evidence_json = json.dumps(_safe_document(dict(evidence)), ensure_ascii=False, sort_keys=True, default=str)
        normalized_identity = {
            "EraCode": str(era_code),
            "PanelVersion": str(panel_version),
            "CollectionContext": str(collection_context),
            "MethodologyVersion": str(methodology_version),
            "ActualInitialAttempts": actual_initial_attempts,
            "HistoricalEquivalentInitialAttempts": historical_equivalent_initial_attempts,
            "ExcessInitialAttempts": excess_initial_attempts,
        }

        with self._connection() as connection:
            cursor = self._cursor(connection)
            cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
            cursor.execute(
                """
                SELECT MethodologyEvidenceId, EraCode, PanelVersion, CollectionContext,
                       MethodologyVersion, ActualInitialAttempts,
                       HistoricalEquivalentInitialAttempts, ExcessInitialAttempts,
                       RunIdsJson
                FROM ctl.MddMacMethodologyEvidence WITH (UPDLOCK, HOLDLOCK)
                ORDER BY MethodologyEvidenceId
                """
            )
            existing_rows = list(cursor.fetchall())
            matching_rows: list[Any] = []
            conflicting_rows: list[Any] = []
            for row in existing_rows:
                if _stored_run_ids(_row_named(row, "RunIdsJson", index=8)) != canonical_run_ids:
                    continue
                same_identity = all(
                    _row_named(row, key, index=index) == value
                    for key, index, value in (
                        ("EraCode", 1, normalized_identity["EraCode"]),
                        ("PanelVersion", 2, normalized_identity["PanelVersion"]),
                        ("CollectionContext", 3, normalized_identity["CollectionContext"]),
                        ("MethodologyVersion", 4, normalized_identity["MethodologyVersion"]),
                        ("ActualInitialAttempts", 5, normalized_identity["ActualInitialAttempts"]),
                        ("HistoricalEquivalentInitialAttempts", 6, normalized_identity["HistoricalEquivalentInitialAttempts"]),
                        ("ExcessInitialAttempts", 7, normalized_identity["ExcessInitialAttempts"]),
                    )
                )
                (matching_rows if same_identity else conflicting_rows).append(row)

            if len(matching_rows) > 1:
                raise MssqlSafetyError("duplicate historical methodology evidence exists for the affected RunIds")
            if conflicting_rows:
                raise MssqlSafetyError("conflicting methodology evidence exists for the affected RunIds")
            if matching_rows:
                evidence_id = int(_row_named(matching_rows[0], "MethodologyEvidenceId", index=0))
                connection.commit()
                return {
                    "methodology_evidence_id": evidence_id,
                    "action": "REUSED",
                    "rows_written": 0,
                }

            cursor.execute(
                """
                INSERT INTO ctl.MddMacMethodologyEvidence
                    (EraCode, PanelVersion, CollectionContext, WindowStartUtc,
                     WindowEndUtc, MethodologyVersion, ActualInitialAttempts,
                     HistoricalEquivalentInitialAttempts, ExcessInitialAttempts,
                     RunIdsJson, EvidenceJson)
                OUTPUT INSERTED.MethodologyEvidenceId
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(era_code),
                    str(panel_version),
                    str(collection_context),
                    _utc(window_start_utc) if window_start_utc else None,
                    _utc(window_end_utc) if window_end_utc else None,
                    str(methodology_version),
                    actual_initial_attempts,
                    historical_equivalent_initial_attempts,
                    excess_initial_attempts,
                    run_ids_json,
                    evidence_json,
                ),
            )
            evidence_id = int(_row_value(cursor.fetchone(), "MethodologyEvidenceId", 0))
            connection.commit()
        return {
            "methodology_evidence_id": evidence_id,
            "action": "INSERTED",
            "rows_written": 1,
        }

    def list_stale_claims(self, *, now_utc: datetime) -> list[dict[str, Any]]:
        with self._connection() as connection:
            cursor = self._cursor(connection)
            cursor.execute(
                """
                SELECT PanelVersion, TargetIdentity, LogicalDueBucket, RunId,
                       ClaimToken, ClaimedAtUtc, StaleAfterUtc, Status
                FROM ctl.MddMacTargetClaim
                WHERE Status IN ('CLAIMED', 'RUNNING') AND StaleAfterUtc < %s
                ORDER BY StaleAfterUtc
                """,
                (_utc(now_utc),),
            )
            rows = cursor.fetchall()
        return [dict(row) if isinstance(row, Mapping) else row for row in rows]

    def retain_raw_evidence(self, *, body: bytes, observed_at: datetime | None = None, **_: Any) -> str:
        return self._raw.retain(body, observed_at=_utc(observed_at or datetime.now(timezone.utc)), reason="validation")

    def cleanup_expired_raw_evidence(self, *, now_utc: datetime | None = None) -> Mapping[str, Any]:
        return self._raw.cleanup(now_utc=now_utc)

    def backup_live_database(self, **_: Any) -> Mapping[str, Any]:
        return {"status": "NOT_APPLICABLE", "reason": "the authoritative database backup is managed on the database host"}

    def verify_live_research_heartbeat(self, *, run_id: str, panel_version: str, logical_due_bucket: int, target_id: str) -> Mapping[str, Any]:
        with self._connection() as connection:
            cursor = self._cursor(connection)
            cursor.execute(
                """
                SELECT COUNT_BIG(*) AS snapshots
                FROM stg.MddMacSourceSnapshot AS s
                INNER JOIN ctl.MddMacTargetAudit AS a ON a.AuditId = s.AuditId
                WHERE s.RunId = %s AND a.PanelVersion = %s
                  AND a.LogicalDueBucket = %s AND a.TargetIdentity = %s
                """,
                (run_id, panel_version, int(logical_due_bucket), target_id),
            )
            row = cursor.fetchone()
        return {"status": "COMPLETE", "run_id": run_id, "snapshot_count": int(_row_value(row, "snapshots", 0) or 0)}

    def close(self) -> None:
        """Connections are short-lived; retained for persistence-port symmetry."""


__all__ = [
    "FULL_SCHEMA_ROUTINES",
    "FULL_SCHEMA_TABLES",
    "MAC_COLLECTION_CONTEXT",
    "MssqlClaimResult",
    "MssqlConnectionSettings",
    "MssqlDependencyError",
    "MssqlPersistence",
    "MssqlSafetyError",
    "MssqlSchemaError",
    "RUNTIME_SCHEMA_ROUTINES",
    "RUNTIME_SCHEMA_TABLES",
    "TIERED50_RUNTIME_SCHEMA_TABLES",
    "TIERED50_TARGET_TABLE",
    "WINDOWS_RUNTIME_UNCONFIRMED",
    "build_skipped_target_audit_merge_statement",
    "build_mssql_snapshot_rows",
    "connect_mssql",
]
