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
    HttpResponse,
    LEGACY7_SELECTION_POLICY_VERSION,
    MAC_SQL_COLLECTION_CONTEXT,
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
from .manifest import LEGACY7_SAMPLING_TARGET_IDS
from .quota import (
    CONSERVATIVE_LIVE_ATTEMPT,
    HTTP_PROVIDER_ATTEMPT_CONFIRMED,
    PENDING_AUTHORIZATION,
    RELEASED_NO_NETWORK,
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


# These lists are deliberately separate.  The full list belongs to an
# administrator/migration context; the runtime list is the smallest object
# surface used by one normal Mac Legacy7 heartbeat and its read-only startup
# validation.  In particular, the runtime list does not contain import,
# historical realtime, source-evidence, or analytics objects.
FULL_SCHEMA_TABLES = (
    "ctl.MddRealtimeSamplingTarget",
    "ctl.MddRealtimeSamplingSlot",
    "ctl.MddMacCollectorRun",
    "ctl.MddMacDispatcherClaim",
    "ctl.MddMacDispatcherClaimEvent",
    "ctl.MddMacTargetClaim",
    "ctl.MddMacTargetClaimEvent",
    "ctl.MddMacTargetAudit",
    "ctl.MddMacHttpAttempt",
    "ctl.MddMacQuotaPermit",
    "ctl.MddMacQuotaAudit",
    "ctl.MddMacSamplingEra",
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
    "stg.uspPersistMddMacSnapshot",
    "analytics.vwMddMacRecordedAtTimeHistory",
    "analytics.vwMddMacSourceFieldCoverage",
)

RUNTIME_SCHEMA_TABLES = (
    "ctl.MddRealtimeSamplingTarget",
    "ctl.MddRealtimeSamplingSlot",
    "ctl.MddMacCollectorRun",
    "ctl.MddMacDispatcherClaim",
    "ctl.MddMacDispatcherClaimEvent",
    "ctl.MddMacTargetClaim",
    "ctl.MddMacTargetClaimEvent",
    "ctl.MddMacTargetAudit",
    "ctl.MddMacHttpAttempt",
    "ctl.MddMacQuotaPermit",
    "ctl.MddMacQuotaAudit",
)

RUNTIME_SCHEMA_ROUTINES = (
    "ctl.uspGetMddRealtimeSamplingTarget",
    "stg.uspPersistMddMacSnapshot",
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


def _month_key(value: datetime) -> str:
    return _utc(value).strftime("%Y-%m")


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
    ) -> None:
        self.settings = settings or MssqlConnectionSettings.from_environment()
        self._connection_factory = connection_factory or (lambda: connect_mssql(self.settings))
        self.collection_context = collection_context
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
                       IS_ROLEMEMBER(N'db_datawriter') AS is_db_datawriter
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
        }

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
        with self._connection() as connection:
            cursor = self._cursor(connection)
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
                    str(collection_context or self.collection_context),
                    _utc(started_at),
                ),
            )
            connection.commit()

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
                    SELECT Status
                    FROM ctl.MddMacDispatcherClaim WITH (UPDLOCK, HOLDLOCK)
                    WHERE PanelVersion = %s AND LogicalDueBucket = %s
                    """,
                    key,
                )
                existing = cursor.fetchone()
                won = existing is None
                existing_status = _row_value(existing, "Status", 0) if existing else None
                if won:
                    cursor.execute(
                        """
                        INSERT INTO ctl.MddMacDispatcherClaim
                            (PanelVersion, LogicalDueBucket, RunId, ClaimToken,
                             ClaimedAtUtc, StaleAfterUtc, Status)
                        VALUES (%s, %s, %s, %s, %s, %s, 'CLAIMED')
                        """,
                        (*key, run_id, token, claimed, stale_after),
                    )
                cursor.execute(
                    """
                    INSERT INTO ctl.MddMacDispatcherClaimEvent
                        (PanelVersion, LogicalDueBucket, RunId, EventAtUtc, Outcome, ExistingStatus)
                    VALUES (%s, %s, %s, %s, %s, %s);
                    SELECT CONVERT(BIGINT, SCOPE_IDENTITY()) AS DispatcherClaimEventId;
                    """,
                    (*key, run_id, claimed, "CLAIMED" if won else "DUPLICATE_DISPATCHER_CLAIM", existing_status),
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
        key = (target.panel_version, target.source_parent_station_id, int(logical_due_bucket))
        with self._connection() as connection:
            cursor = self._cursor(connection)
            try:
                cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
                cursor.execute(
                    """
                    SELECT Status
                    FROM ctl.MddMacTargetClaim WITH (UPDLOCK, HOLDLOCK)
                    WHERE PanelVersion = %s AND TargetIdentity = %s AND LogicalDueBucket = %s
                    """,
                    key,
                )
                existing = cursor.fetchone()
                won = existing is None
                existing_status = _row_value(existing, "Status", 0) if existing else None
                if won:
                    cursor.execute(
                        """
                        INSERT INTO ctl.MddMacTargetClaim
                            (PanelVersion, TargetIdentity, LogicalDueBucket, RunId, ClaimToken,
                             ClaimedAtUtc, StaleAfterUtc, Status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 'CLAIMED')
                        """,
                        (*key, run_id, token, claimed, stale_after),
                    )
                cursor.execute(
                    """
                    INSERT INTO ctl.MddMacTargetClaimEvent
                        (PanelVersion, TargetIdentity, LogicalDueBucket, RunId,
                         EventAtUtc, Outcome, ExistingStatus)
                    VALUES (%s, %s, %s, %s, %s, %s, %s);
                    SELECT CONVERT(BIGINT, SCOPE_IDENTITY()) AS TargetClaimEventId;
                    """,
                    (*key, run_id, claimed, "CLAIMED" if won else "DUPLICATE_CLAIM", existing_status),
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
        with self._connection() as connection:
            cursor = self._cursor(connection)
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
                cursor.execute(
                    """
                    UPDATE ctl.MddMacTargetClaim
                    SET Status = 'RUNNING', StartedAtUtc = %s
                    WHERE PanelVersion = %s AND TargetIdentity = %s AND LogicalDueBucket = %s
                    """,
                    (_utc(started_at), target.panel_version, target.source_parent_station_id, int(logical_due_bucket)),
                )
                cursor.execute("COMMIT TRANSACTION;")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return audit_id

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
        conservative = network_attempted and not http_received
        return {
            "http_status": http_status,
            "category": category,
            "outcome": outcome,
            "duration_ms": max(0.0, duration * 1000.0),
            "transport_kind": resolved_kind,
            "network_attempted": network_attempted,
            "http_response_received": http_received,
            "live_client_invocation": resolved_live,
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

    def current_month_usage(self, *, at_utc: datetime | None = None) -> Mapping[str, Any] | None:
        at = _utc(at_utc or datetime.now(timezone.utc))
        month = _month_key(at)
        with self._connection() as connection:
            cursor = self._cursor(connection)
            cursor.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN Status = %s THEN Amount ELSE 0 END), 0) AS confirmed,
                    COALESCE(SUM(CASE WHEN Status = %s THEN Amount ELSE 0 END), 0) AS conservative,
                    COALESCE(SUM(CASE WHEN Status = %s THEN Amount ELSE 0 END), 0) AS pending
                FROM ctl.MddMacQuotaPermit
                WHERE MonthKey = %s
                """,
                (HTTP_PROVIDER_ATTEMPT_CONFIRMED, CONSERVATIVE_LIVE_ATTEMPT, PENDING_AUTHORIZATION, month),
            )
            row = cursor.fetchone()
        return {
            "confirmed_provider_attempts": int(_row_value(row, "confirmed", 0) or 0),
            "reserved_shared_usage": int(_row_value(row, "pending", 2) or 0),
            "conservative_live_attempts": int(_row_value(row, "conservative", 1) or 0),
            "live_client_invocations": 0,
            "http_responses_received": int(_row_value(row, "confirmed", 0) or 0),
            "provider_quota_unknown_attempts": int(_row_value(row, "conservative", 1) or 0),
            "state_available": False,
            "source": WINDOWS_RUNTIME_UNCONFIRMED,
        }

    def reserve_quota_attempt(
        self,
        *,
        decision_kind: str,
        boundary: int,
        proposed_attempts: int = 1,
        at_utc: datetime | None = None,
        reservation_scope: str = "LIVE_PROVIDER",
    ) -> Mapping[str, Any]:
        amount = int(proposed_attempts)
        if amount <= 0:
            raise ValueError("proposed_attempts must be positive")
        at = _utc(at_utc or datetime.now(timezone.utc))
        if decision_kind != "INITIAL":
            return {
                "allowed": False,
                "decision": "RETRY_DENIED_QUOTA_STATE_UNAVAILABLE",
                "proposed_attempts": amount,
                "known_total": None,
                "boundary": int(boundary),
                "reason": "Windows provider-wide usage is not reconciled",
                "permit_id": None,
            }
        permit_id = str(uuid.uuid4())
        with self._connection() as connection:
            cursor = self._cursor(connection)
            cursor.execute(
                """
                INSERT INTO ctl.MddMacQuotaPermit
                    (PermitId, MonthKey, DecisionKind, ReservationScope, Amount,
                     Boundary, ReservedAtUtc, Status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (permit_id, _month_key(at), decision_kind, reservation_scope, amount, int(boundary), at, PENDING_AUTHORIZATION),
            )
            connection.commit()
        return {
            "allowed": True,
            "decision": "INITIAL_ALLOWED_WITHOUT_RETRY_STATE",
            "proposed_attempts": amount,
            "known_total": None,
            "boundary": int(boundary),
            "reason": "Windows provider-wide usage is not reconciled",
            "permit_id": permit_id,
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
            SET Status = %s, FinalizedAtUtc = %s, LiveClientInvocation = %s
            WHERE PermitId = %s AND Status = %s
            """,
            (finalization, _utc(finalized_at), int(live_client_invocation), str(permit_id), PENDING_AUTHORIZATION),
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
        if is_active and normalized_era_code.casefold() == "tiered50":
            raise MssqlSafetyError("Tiered50 activation is not permitted by the prepared collector contract")

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
            connection.commit()
        return {
            "sampling_era_id": era_id,
            "action": "INSERTED",
            "rows_written": 1,
        }

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
    "WINDOWS_RUNTIME_UNCONFIRMED",
    "build_mssql_snapshot_rows",
    "connect_mssql",
]
