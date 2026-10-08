"""Validation and reconciliation rules for the shared MDD quota baseline.

The provider-wide baseline is an administrative input.  This module contains
the pure validation used by the operator tool and by the production SQL read
path so a missing, stale, or ambiguous provider report can never be converted
to zero usage.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .domain import (
    ABSOLUTE_PROVIDER_CEILING,
    AUTOMATIC_RETRY_PROTECTION_BOUNDARY,
    NORMAL_OPERATIONAL_MAXIMUM,
)


ACCOUNTING_MODELS = frozenset(
    {
        "EXCLUSIVE_CREDENTIAL",
        "CENTRAL_AUTHORIZATION",
        "REFRESHED_PROVIDER_ACCOUNTING",
        "AUTOMATIC_MONTHLY_ROLLOVER",
        "AUTOMATIC_MONTHLY_ROLLOVER_BOUNDED",
    }
)
_MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SECRET_KEY_PARTS = frozenset({"apikey", "authorization", "password", "secret", "token", "credential"})
_MAX_SOURCE_DESCRIPTION = 1000


class QuotaBaselineError(ValueError):
    """The supplied provider-wide quota evidence is not registrable."""


def _utc(value: Any, *, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise QuotaBaselineError(f"{field} must be an ISO-8601 timestamp with an explicit timezone") from exc
    if parsed.tzinfo is None:
        raise QuotaBaselineError(f"{field} must include an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _nonnegative(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise QuotaBaselineError(f"{field} must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise QuotaBaselineError(f"{field} must be a non-negative integer") from exc
    if parsed < 0:
        raise QuotaBaselineError(f"{field} must be non-negative")
    return parsed


def _required_text(value: Any, *, field: str, maximum: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QuotaBaselineError(f"{field} is required")
    result = value.strip()
    if maximum is not None and len(result) > maximum:
        raise QuotaBaselineError(f"{field} exceeds its maximum length")
    return result


def _contains_secret_shape(value: Any, *, path: str = "evidence") -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            compact = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if compact in _SECRET_KEY_PARTS:
                return True
            if _contains_secret_shape(child, path=f"{path}.{key}"):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_secret_shape(child, path=f"{path}[]") for child in value)
    if isinstance(value, str):
        compact = value.casefold()
        return any(marker in compact for marker in ("mdd_api_key=", "mssql_password=", "sql_server_password="))
    return False


def canonical_evidence_hash(document: Mapping[str, Any]) -> str:
    """Hash the exact sanitized evidence document used for reconciliation."""

    try:
        payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise QuotaBaselineError("provider evidence must be JSON-serializable") from exc
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _local_count(local: Mapping[str, Any], *names: str, field: str) -> int:
    for name in names:
        if name in local:
            return _nonnegative(local[name], field=field)
    return 0


def _provider_period(provider: Mapping[str, Any], *, month_key: str) -> dict[str, Any]:
    timezone_name = _required_text(
        provider.get("period_timezone", provider.get("reset_timezone")),
        field="provider.period_timezone",
        maximum=80,
    )
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise QuotaBaselineError("provider.period_timezone is not a recognized IANA timezone") from exc
    start = _utc(provider.get("period_start_utc"), field="provider.period_start_utc")
    end = _utc(provider.get("period_end_utc"), field="provider.period_end_utc")
    if start >= end:
        raise QuotaBaselineError("provider quota period must have a positive duration")
    if start.astimezone(zone).strftime("%Y-%m") != month_key:
        raise QuotaBaselineError("provider quota period does not map to the supplied month_key")
    return {
        "timezone": timezone_name,
        "start_utc": start,
        "end_utc": end,
    }


def provider_period_month_key(at_utc: datetime, *, timezone_name: str) -> str:
    """Return the provider billing-period key for a UTC instant.

    This helper deliberately requires the provider's verified IANA timezone.
    The collector must not substitute the host or UTC calendar timezone when
    deciding which monthly accounting row authorizes a request.
    """

    try:
        zone = ZoneInfo(_required_text(timezone_name, field="provider_period_timezone", maximum=80))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise QuotaBaselineError("provider_period_timezone is not a recognized IANA timezone") from exc
    return _utc(at_utc, field="at_utc").astimezone(zone).strftime("%Y-%m")


def reconcile_provider_evidence(
    document: Mapping[str, Any],
    *,
    local_usage: Mapping[str, Any],
    now_utc: datetime | None = None,
    require_current_month: bool = True,
) -> dict[str, Any]:
    """Validate evidence and return a sanitized, SQL-ready reconciliation.

    ``local_usage`` is read from the authoritative database by the administrative tool.
    The three persisted baseline components represent the complete reconciled
    amount through ``local_usage_included_through_utc``.  A later authorization
    therefore has one unambiguous boundary: it adds only permit rows strictly
    after that stored local cutoff.  When the provider cutoff is older than the
    local cutoff, local permits in that interval are part of the persisted
    baseline rather than being left in a transient reconciliation total.
    """

    if not isinstance(document, Mapping):
        raise QuotaBaselineError("provider evidence must be a JSON object")
    if _contains_secret_shape(document):
        raise QuotaBaselineError("provider evidence contains a credential-shaped field")
    if not isinstance(local_usage, Mapping):
        raise QuotaBaselineError("SQL local usage evidence is required")

    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    month_key = _required_text(document.get("month_key"), field="month_key", maximum=7)
    if not _MONTH_PATTERN.fullmatch(month_key):
        raise QuotaBaselineError("month_key must use YYYY-MM format")
    accounting_model = _required_text(document.get("accounting_model"), field="accounting_model", maximum=40)
    if accounting_model not in ACCOUNTING_MODELS:
        raise QuotaBaselineError("accounting_model is not one of the approved shared-consumer models")
    if document.get("includes_all_shared_consumers") is not True:
        raise QuotaBaselineError("provider evidence must explicitly account for all shared consumers")
    model_evidence = _required_text(
        document.get("operating_model_evidence"),
        field="operating_model_evidence",
        maximum=2000,
    )
    if accounting_model == "EXCLUSIVE_CREDENTIAL" and document.get("credential_exclusivity_confirmed") is not True:
        raise QuotaBaselineError("exclusive-credential accounting requires explicit exclusivity confirmation")
    if accounting_model == "CENTRAL_AUTHORIZATION" and not document.get("authorization_mechanism"):
        raise QuotaBaselineError("central-authorization accounting requires mechanism evidence")
    if accounting_model == "REFRESHED_PROVIDER_ACCOUNTING" and not document.get("refresh_commitment"):
        raise QuotaBaselineError("refreshed provider accounting requires a refresh commitment")

    provider = document.get("provider")
    if not isinstance(provider, Mapping):
        raise QuotaBaselineError("provider evidence is required")
    source_description = _required_text(
        provider.get("source_description", document.get("source_description")),
        field="provider.source_description",
        maximum=_MAX_SOURCE_DESCRIPTION,
    )
    source_reference = _required_text(
        provider.get("source_reference", document.get("source_reference")),
        field="provider.source_reference",
        maximum=1000,
    )
    measured_at = _utc(provider.get("measured_at_utc"), field="provider.measured_at_utc")
    provider_cutoff = _utc(
        provider.get("usage_through_utc", provider.get("measurement_cutoff_utc")),
        field="provider.usage_through_utc",
    )
    if measured_at > now or provider_cutoff > now:
        raise QuotaBaselineError("provider timestamps cannot be in the future")
    if provider_cutoff > measured_at:
        raise QuotaBaselineError("provider usage_through_utc cannot be after measured_at_utc")
    period = _provider_period(provider, month_key=month_key)
    if not period["start_utc"] <= provider_cutoff < period["end_utc"]:
        raise QuotaBaselineError("provider usage cutoff is outside the declared billing period")
    if not period["start_utc"] <= measured_at < period["end_utc"]:
        raise QuotaBaselineError("provider measured_at_utc is outside the declared billing period")
    if require_current_month:
        provider_month_now = provider_period_month_key(now, timezone_name=period["timezone"])
        if (
            month_key != provider_month_now
            or not period["start_utc"] <= now < period["end_utc"]
        ):
            raise QuotaBaselineError(
                "provider evidence is not for the current provider billing period "
                "(current calendar month in the verified provider timezone)"
            )

    cumulative = _nonnegative(
        provider.get("cumulative_attempts", provider.get("reported_cumulative_usage")),
        field="provider.cumulative_attempts",
    )
    provider_confirmed_value = provider.get("confirmed_attempts")
    provider_unknown_value = provider.get("unknown_attempts")
    if (provider_confirmed_value is None) != (provider_unknown_value is None):
        raise QuotaBaselineError("provider confirmed_attempts and unknown_attempts must be supplied together")
    if provider_confirmed_value is None:
        provider_confirmed = cumulative
        provider_unknown = 0
        provider_classification = "PROVIDER_CUMULATIVE_TOTAL"
    else:
        provider_confirmed = _nonnegative(provider_confirmed_value, field="provider.confirmed_attempts")
        provider_unknown = _nonnegative(provider_unknown_value, field="provider.unknown_attempts")
        if provider_confirmed + provider_unknown != cumulative:
            raise QuotaBaselineError("provider attempt components do not reconcile to cumulative_attempts")
        provider_classification = "PROVIDER_COMPONENTS_RECONCILED"
    if cumulative > ABSOLUTE_PROVIDER_CEILING:
        raise QuotaBaselineError("provider cumulative usage exceeds the absolute provider ceiling")

    local_cutoff = _utc(
        document.get("local_usage_included_through_utc"),
        field="local_usage_included_through_utc",
    )
    if local_cutoff > now or local_cutoff < provider_cutoff:
        raise QuotaBaselineError(
            "local_usage_included_through_utc must cover the provider usage cutoff and not be in the future"
        )
    if local_cutoff >= period["end_utc"]:
        raise QuotaBaselineError(
            "local_usage_included_through_utc must be inside the provider billing period"
        )
    provider_includes_local = provider.get("includes_local_usage")
    if not isinstance(provider_includes_local, bool):
        raise QuotaBaselineError("provider.includes_local_usage must be explicitly true or false")

    local_through_confirmed = _local_count(
        local_usage,
        "confirmed_through_cutoff",
        "confirmed_at_or_before_cutoff",
        field="local_usage.confirmed_through_cutoff",
    )
    local_through_unknown = _local_count(
        local_usage,
        "unknown_through_cutoff",
        "conservative_through_cutoff",
        field="local_usage.unknown_through_cutoff",
    )
    local_through_pending = _local_count(
        local_usage,
        "pending_through_cutoff",
        "pending_at_or_before_cutoff",
        field="local_usage.pending_through_cutoff",
    )
    local_after_confirmed = _local_count(
        local_usage,
        "confirmed_after_cutoff",
        field="local_usage.confirmed_after_cutoff",
    )
    local_after_unknown = _local_count(
        local_usage,
        "unknown_after_cutoff",
        "conservative_after_cutoff",
        field="local_usage.unknown_after_cutoff",
    )
    local_after_pending = _local_count(
        local_usage,
        "pending_after_cutoff",
        field="local_usage.pending_after_cutoff",
    )

    if provider_includes_local and local_through_pending:
        raise QuotaBaselineError(
            "outstanding local permits at or before the provider cutoff are unresolved; refusing double counting"
        )

    external_reserved = _nonnegative(
        document.get("explicit_shared_consumer_reserved_attempts", 0),
        field="explicit_shared_consumer_reserved_attempts",
    )
    external_unknown = _nonnegative(
        document.get("conservative_unknown_attempts", document.get("possible_unknown_attempts", 0)),
        field="conservative_unknown_attempts",
    )
    unexplained_discrepancies = _nonnegative(
        document.get("unexplained_discrepancy_attempts"),
        field="unexplained_discrepancy_attempts",
    )

    if provider_includes_local:
        # The provider total covers local usage only through the provider
        # cutoff.  The local SQL observation may extend beyond that cutoff;
        # those permits must be moved into the durable baseline components.
        baseline_confirmed = provider_confirmed + local_after_confirmed
        baseline_unknown = (
            provider_unknown + local_after_unknown + external_unknown + unexplained_discrepancies
        )
        baseline_reserved = external_reserved + local_after_pending
        local_included_note = (
            "provider_total_includes_local_usage_through_provider_cutoff;"
            " post_provider_cutoff_local_usage_added_through_local_cutoff"
        )
    else:
        # The provider total excludes local usage, so every local permit
        # through the stored local cutoff belongs in the persisted baseline.
        baseline_confirmed = provider_confirmed + local_through_confirmed + local_after_confirmed
        baseline_unknown = (
            provider_unknown
            + local_through_unknown
            + local_after_unknown
            + external_unknown
            + unexplained_discrepancies
        )
        baseline_reserved = external_reserved + local_through_pending + local_after_pending
        local_included_note = "provider_total_excludes_local_usage; all_local_sql_counts_added_through_local_cutoff"

    # This is intentionally the same sum that is persisted to
    # ctl.MddMacSharedQuotaAccounting.  Keeping the invariant explicit makes a
    # reconciliation with a later SQL cutoff durable instead of relying on the
    # display-only total returned by this function.
    baseline_component_total = baseline_confirmed + baseline_unknown + baseline_reserved
    current_total = baseline_component_total
    if current_total > ABSOLUTE_PROVIDER_CEILING:
        raise QuotaBaselineError("conservative accounted usage exceeds the absolute provider ceiling")

    stale_after = _utc(document.get("stale_after_utc"), field="stale_after_utc")
    if stale_after <= now:
        raise QuotaBaselineError("stale_after_utc must be in the future")
    if stale_after > period["end_utc"]:
        raise QuotaBaselineError("stale_after_utc may not extend beyond the provider billing period")

    reconciliation_key = _required_text(
        document.get("reconciliation_key"),
        field="reconciliation_key",
        maximum=240,
    )
    if any(marker in reconciliation_key.casefold() for marker in ("password", "secret", "token", "apikey")):
        raise QuotaBaselineError("reconciliation_key contains a prohibited credential marker")
    evidence_hash = canonical_evidence_hash(document)

    return {
        "month_key": month_key,
        "provider_quota_ceiling": ABSOLUTE_PROVIDER_CEILING,
        "normal_operational_maximum": NORMAL_OPERATIONAL_MAXIMUM,
        "retry_protection_boundary": AUTOMATIC_RETRY_PROTECTION_BOUNDARY,
        "confirmed_provider_attempts": baseline_confirmed,
        "conservative_unknown_attempts": baseline_unknown,
        "explicit_shared_consumer_reserved_attempts": baseline_reserved,
        "accounting_status": "COMPLETE",
        "includes_all_shared_consumers": True,
        "baseline_as_of_utc": _iso(measured_at),
        "local_usage_included_through_utc": _iso(local_cutoff),
        "provider_measurement_cutoff_utc": _iso(provider_cutoff),
        "stale_after_utc": _iso(stale_after),
        "provider_period_timezone": period["timezone"],
        "provider_period_start_utc": _iso(period["start_utc"]),
        "provider_period_end_utc": _iso(period["end_utc"]),
        "accounting_model": accounting_model,
        "source_description": source_description,
        "source_reference": source_reference,
        "source_evidence_hash": evidence_hash,
        "reconciliation_key": reconciliation_key,
        "provider_reported_cumulative_attempts": cumulative,
        "provider_reported_confirmed_attempts": provider_confirmed,
        "provider_reported_unknown_attempts": provider_unknown,
        "unexplained_discrepancy_attempts": unexplained_discrepancies,
        "provider_attempt_classification": provider_classification,
        "provider_includes_local_usage": provider_includes_local,
        "operating_model_evidence": model_evidence,
        "external_shared_consumer_reserved_attempts": external_reserved,
        "external_unknown_attempts": external_unknown,
        "local_usage": {
            "confirmed_through_cutoff": local_through_confirmed,
            "unknown_through_cutoff": local_through_unknown,
            "pending_through_cutoff": local_through_pending,
            "confirmed_after_cutoff": local_after_confirmed,
            "unknown_after_cutoff": local_after_unknown,
            "pending_after_cutoff": local_after_pending,
        },
        "local_usage_boundary_rule": local_included_note,
        "baseline_component_total": baseline_component_total,
        "post_provider_cutoff_local_usage_registered": {
            "confirmed": local_after_confirmed,
            "unknown": local_after_unknown,
            "pending": local_after_pending,
        },
        "current_conservative_accounted_total": current_total,
        "remaining_headroom": {
            "normal_operational_maximum": NORMAL_OPERATIONAL_MAXIMUM - current_total,
            "retry_protection_boundary": AUTOMATIC_RETRY_PROTECTION_BOUNDARY - current_total,
            "provider_quota_ceiling": ABSOLUTE_PROVIDER_CEILING - current_total,
        },
        "evidence_json": json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    }


def _contract_value(contract: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in contract:
            return contract[name]
    return None


def _flag_is(value: Any, expected: bool) -> bool:
    if isinstance(value, bool):
        return value is expected
    return isinstance(value, int) and value == (1 if expected else 0)


def _contract_timezones(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise QuotaBaselineError("registered reset contract timezone envelope is malformed") from exc
    if not isinstance(value, (list, tuple)) or not value:
        raise QuotaBaselineError("registered reset contract timezone envelope is missing")
    values = tuple(str(item).strip() for item in value)
    if any(not item for item in values) or len(set(values)) != len(values):
        raise QuotaBaselineError("registered reset contract timezone envelope is malformed")
    return tuple(sorted(values))


def _validate_registered_exact_contract(
    row: Mapping[str, Any],
    contract: Mapping[str, Any],
    *,
    month_key: str,
    provider_period_timezone: str,
    period_start: datetime,
) -> None:
    """Validate optional exact-contract lineage without weakening baseline checks."""

    if str(_contract_value(contract, "ContractStatus", "contract_status") or "") != "ACTIVE":
        raise QuotaBaselineError("registered exact reset contract is not active")
    if str(_contract_value(contract, "ProviderName", "provider_name") or "") != "MDD NRW":
        raise QuotaBaselineError("registered exact reset contract provider identity is invalid")
    if str(_contract_value(contract, "ResetModel", "reset_model") or "") != "CALENDAR_MONTH_PROVIDER_TIMEZONE":
        raise QuotaBaselineError("registered exact reset contract model is not exact")
    if str(_contract_value(contract, "ResetTimezone", "reset_timezone") or "") != provider_period_timezone:
        raise QuotaBaselineError("baseline timezone does not match the registered exact reset contract")
    if not _flag_is(_contract_value(contract, "CredentialExclusivityConfirmed", "credential_exclusivity_confirmed"), True):
        raise QuotaBaselineError("registered exact reset contract lacks exclusive-credential confirmation")
    if not _flag_is(
        _contract_value(contract, "CalendarMonthResetModelEstablished", "calendar_month_reset_model_established"),
        True,
    ):
        raise QuotaBaselineError("registered exact reset contract has no established calendar-month model")
    if not _flag_is(_contract_value(contract, "ProviderResetBoundaryVerified", "provider_reset_boundary_verified"), True):
        raise QuotaBaselineError("registered exact reset contract is not provider-verified")
    if not _flag_is(_contract_value(contract, "OperatorAssumptionApproved", "operator_assumption_approved"), False):
        raise QuotaBaselineError("exact reset contract is marked as an operator assumption")
    if int(_contract_value(contract, "ProviderQuotaCeiling", "provider_quota_ceiling") or 0) != ABSOLUTE_PROVIDER_CEILING:
        raise QuotaBaselineError("registered exact reset contract does not preserve the approved quota ceiling")
    contract_hash = str(_contract_value(contract, "SourceEvidenceHash", "source_evidence_hash") or "")
    if not _SHA256_PATTERN.fullmatch(contract_hash):
        raise QuotaBaselineError("registered exact reset contract evidence hash is malformed")
    if contract_hash != str(row.get("SourceEvidenceHash") or ""):
        raise QuotaBaselineError("baseline evidence hash does not match the registered exact reset contract")
    contract_key = _required_text(
        _contract_value(contract, "ContractKey", "contract_key"),
        field="registered exact reset contract key",
        maximum=240,
    )
    expected_key = f"AUTO-ROLLOVER:{contract_key}:{month_key}"
    if str(row.get("ReconciliationKey") or "") != expected_key:
        raise QuotaBaselineError("baseline reconciliation identity does not match the registered exact reset contract")
    effective_from = _utc(
        _contract_value(contract, "EffectiveFromUtc", "effective_from_utc"),
        field="registered exact reset contract effective_from_utc",
    )
    if period_start < effective_from:
        raise QuotaBaselineError("registered exact reset contract is not effective for the baseline period")


def _validate_registered_bounded_contract(
    row: Mapping[str, Any],
    contract: Mapping[str, Any] | None,
    *,
    at: datetime,
    month_key: str,
    period_start: datetime,
    period_end: datetime,
) -> None:
    """Validate the active operator-approved global-envelope contract."""

    if not isinstance(contract, Mapping):
        raise QuotaBaselineError("registered bounded reset contract is required")
    from .quota_rollover import (
        BOUNDED_TIMEZONE_ENVELOPE_MARKER,
        GLOBAL_UTC_OFFSET_ENVELOPE_TIMEZONES,
        bounded_provider_period_for_month,
    )

    if str(_contract_value(contract, "ContractStatus", "contract_status") or "") != "ACTIVE":
        raise QuotaBaselineError("registered bounded reset contract is not active")
    if str(_contract_value(contract, "ProviderName", "provider_name") or "") != "MDD NRW":
        raise QuotaBaselineError("registered bounded reset contract provider identity is invalid")
    if str(_contract_value(contract, "ResetModel", "reset_model") or "") != "CALENDAR_MONTH_BOUNDED_TIMEZONE_UNCERTAINTY":
        raise QuotaBaselineError("registered bounded reset contract model is not approved")
    if str(_contract_value(contract, "ResetTimezone", "reset_timezone") or "") != BOUNDED_TIMEZONE_ENVELOPE_MARKER:
        raise QuotaBaselineError("bounded baseline does not use the authorized timezone-envelope marker")
    if not _flag_is(_contract_value(contract, "ProviderResetBoundaryVerified", "provider_reset_boundary_verified"), False):
        raise QuotaBaselineError("bounded reset contract must not claim provider boundary verification")
    if not _flag_is(_contract_value(contract, "CalendarMonthResetModelEstablished", "calendar_month_reset_model_established"), True):
        raise QuotaBaselineError("bounded reset contract has no established calendar-month model")
    if not _flag_is(_contract_value(contract, "OperatorAssumptionApproved", "operator_assumption_approved"), True):
        raise QuotaBaselineError("bounded reset contract lacks explicit operator approval")
    if not _flag_is(_contract_value(contract, "CredentialExclusivityConfirmed", "credential_exclusivity_confirmed"), True):
        raise QuotaBaselineError("bounded reset contract lacks exclusive-credential confirmation")
    if int(_contract_value(contract, "ProviderQuotaCeiling", "provider_quota_ceiling") or 0) != ABSOLUTE_PROVIDER_CEILING:
        raise QuotaBaselineError("registered bounded reset contract does not preserve the approved quota ceiling")

    supported = _contract_value(
        contract,
        "SupportedResetTimezones",
        "supported_reset_timezones",
        "SupportedResetTimezonesJson",
        "supported_reset_timezones_json",
    )
    if _contract_timezones(supported) != tuple(sorted(GLOBAL_UTC_OFFSET_ENVELOPE_TIMEZONES)):
        raise QuotaBaselineError("bounded reset contract timezone envelope is not the authorized UTC-12 through UTC+14 set")
    if _contract_value(contract, "Sql" "ServerResetTimezone", "sql_server_reset_timezone") not in (None, ""):
        raise QuotaBaselineError("bounded reset contract must not carry an exact provider-server timezone")

    contract_hash = str(_contract_value(contract, "SourceEvidenceHash", "source_evidence_hash") or "")
    if not _SHA256_PATTERN.fullmatch(contract_hash):
        raise QuotaBaselineError("registered bounded reset contract evidence hash is malformed")
    if contract_hash != str(row.get("SourceEvidenceHash") or ""):
        raise QuotaBaselineError("baseline evidence hash does not match the registered bounded reset contract")
    contract_key = _required_text(
        _contract_value(contract, "ContractKey", "contract_key"),
        field="registered bounded reset contract key",
        maximum=240,
    )
    expected_key = f"AUTO-ROLLOVER:{contract_key}:{month_key}"
    if str(row.get("ReconciliationKey") or "") != expected_key:
        raise QuotaBaselineError("baseline reconciliation identity does not match the registered bounded reset contract")
    effective_from = _utc(
        _contract_value(contract, "EffectiveFromUtc", "effective_from_utc"),
        field="registered bounded reset contract effective_from_utc",
    )
    if period_start < effective_from:
        raise QuotaBaselineError("registered bounded reset contract is not effective for the baseline period")
    expiry = _contract_value(contract, "ExpiresAtUtc", "expires_at_utc", "ExpiredAtUtc", "expired_at_utc")
    if expiry is not None and _utc(expiry, field="registered bounded reset contract expiry") <= at:
        raise QuotaBaselineError("registered bounded reset contract is expired")
    try:
        delay = int(_contract_value(contract, "ActivationDelaySeconds", "activation_delay_seconds") or 0)
    except (TypeError, ValueError, OverflowError) as exc:
        raise QuotaBaselineError("registered bounded reset contract activation delay is malformed") from exc
    if delay < 0 or delay > 86_400:
        raise QuotaBaselineError("registered bounded reset contract activation delay is outside the approved bound")

    expected = bounded_provider_period_for_month(
        month_key,
        timezone_names=GLOBAL_UTC_OFFSET_ENVELOPE_TIMEZONES,
        activation_delay_seconds=delay,
    )
    if period_start != expected.start_utc or period_end != expected.end_utc:
        raise QuotaBaselineError("bounded baseline UTC boundaries do not match the authorized envelope")
    if at < expected.activation_not_before_utc:
        raise QuotaBaselineError("bounded baseline is inside the required activation delay")


def validate_stored_baseline_row(
    row: Mapping[str, Any] | None,
    *,
    at_utc: datetime,
    registered_contract: Mapping[str, Any] | None = None,
    reset_contract: Mapping[str, Any] | None = None,
    contract: Mapping[str, Any] | None = None,
) -> tuple[bool, str]:
    """Return whether a current SQL baseline is safe for authorization.

    Exact provider-period rows retain strict IANA and calendar-boundary
    validation.  Bounded automatic rows carry a synthetic marker rather than a
    provider timezone, so they are accepted only with the active, explicitly
    approved global-envelope reset contract that produced their evidence.
    """

    if not isinstance(row, Mapping):
        return False, "baseline row is missing"
    supplied_contracts = [item for item in (registered_contract, reset_contract, contract) if item is not None]
    if supplied_contracts and any(item != supplied_contracts[0] for item in supplied_contracts[1:]):
        return False, "conflicting registered reset contracts were supplied"
    registered = supplied_contracts[0] if supplied_contracts else None
    try:
        at = _utc(at_utc, field="at_utc")
        month_key = str(row.get("MonthKey") or "").strip()
        if not _MONTH_PATTERN.fullmatch(month_key):
            return False, "baseline month key is missing or malformed"
        model = str(row.get("AccountingModel") or "")
        if model not in ACCOUNTING_MODELS:
            return False, "accounting model is missing or unknown"
        if _nonnegative(row.get("ProviderQuotaCeiling", 0), field="ProviderQuotaCeiling") != ABSOLUTE_PROVIDER_CEILING:
            return False, "provider ceiling is not the approved value"
        if _nonnegative(row.get("NormalOperationalMaximum", 0), field="NormalOperationalMaximum") != NORMAL_OPERATIONAL_MAXIMUM:
            return False, "normal operational maximum is not the approved value"
        if _nonnegative(row.get("RetryProtectionBoundary", 0), field="RetryProtectionBoundary") != AUTOMATIC_RETRY_PROTECTION_BOUNDARY:
            return False, "retry protection boundary is not the approved value"
        accounted_total = sum(
            _nonnegative(row.get(name, 0), field=name)
            for name in (
                "ConfirmedProviderAttempts",
                "ConservativeUnknownAttempts",
                "ExplicitSharedConsumerReservedAttempts",
            )
        )
        if accounted_total > ABSOLUTE_PROVIDER_CEILING:
            return False, "baseline conservative usage exceeds the absolute provider ceiling"
        if str(row.get("AccountingStatus") or "") != "COMPLETE":
            return False, "accounting status is not COMPLETE"
        if not _flag_is(row.get("IncludesAllSharedConsumers"), True):
            return False, "shared consumers are not all accounted for"
        baseline_as_of = _utc(row.get("BaselineAsOfUtc"), field="BaselineAsOfUtc")
        local_cutoff = _utc(row.get("LocalUsageIncludedThroughUtc"), field="LocalUsageIncludedThroughUtc")
        measurement_cutoff = _utc(
            row.get("ProviderMeasurementCutoffUtc"),
            field="ProviderMeasurementCutoffUtc",
        )
        stale_after = _utc(row.get("StaleAfterUtc"), field="StaleAfterUtc")
        provider_period_timezone = _required_text(
            row.get("ProviderPeriodTimezone"),
            field="ProviderPeriodTimezone",
            maximum=80,
        )
        period_start = _utc(row.get("ProviderPeriodStartUtc"), field="ProviderPeriodStartUtc")
        period_end = _utc(row.get("ProviderPeriodEndUtc"), field="ProviderPeriodEndUtc")
        if period_start >= period_end:
            return False, "provider billing period is invalid"

        from .quota_rollover import (
            BOUNDED_TIMEZONE_ENVELOPE_MARKER,
            AUTOMATIC_ROLLOVER_ACCOUNTING_MODEL,
            provider_period_for_month,
        )

        bounded = model == "AUTOMATIC_MONTHLY_ROLLOVER_BOUNDED"
        if bounded:
            if provider_period_timezone != BOUNDED_TIMEZONE_ENVELOPE_MARKER:
                return False, "bounded accounting requires the authorized timezone-envelope marker"
            _validate_registered_bounded_contract(
                row,
                registered,
                at=at,
                month_key=month_key,
                period_start=period_start,
                period_end=period_end,
            )
            expected_start = period_start
            expected_end = period_end
        else:
            try:
                period_zone = ZoneInfo(provider_period_timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise QuotaBaselineError("ProviderPeriodTimezone is not a recognized IANA timezone") from exc
            expected_period = provider_period_for_month(
                month_key,
                timezone_name=provider_period_timezone,
                activation_delay_seconds=0,
            )
            expected_start = expected_period.start_utc
            expected_end = expected_period.end_utc
            if model == AUTOMATIC_ROLLOVER_ACCOUNTING_MODEL and registered is None:
                return False, "registered exact reset contract is required for automatic rollover"
            if registered is not None:
                _validate_registered_exact_contract(
                    row,
                    registered,
                    month_key=month_key,
                    provider_period_timezone=provider_period_timezone,
                    period_start=period_start,
                )
            if period_start.astimezone(period_zone).strftime("%Y-%m") != month_key:
                return False, "provider billing period does not map to the baseline month"
            if at.astimezone(period_zone).strftime("%Y-%m") != month_key:
                return False, "baseline month key does not match the provider timezone at authorization time"
        if period_start != expected_start or period_end != expected_end:
            return False, "provider UTC period boundaries do not match the declared calendar month"
        if not period_start <= at < period_end:
            return False, "baseline does not cover the current provider billing period"
        if not period_start <= measurement_cutoff < period_end:
            return False, "provider measurement cutoff is outside the billing period"
        if not period_start <= baseline_as_of < period_end:
            return False, "baseline timestamp is outside the provider billing period"
        if not period_start <= local_cutoff < period_end:
            return False, "local usage cutoff is outside the provider billing period"
        if stale_after > period_end:
            return False, "baseline staleness deadline extends beyond the billing period"
        if baseline_as_of > at or local_cutoff > at or measurement_cutoff > at:
            return False, "baseline timestamps are in the future"
        if measurement_cutoff > baseline_as_of:
            return False, "provider measurement cutoff is after the baseline timestamp"
        if local_cutoff < measurement_cutoff:
            return False, "local usage cutoff does not cover provider measurement cutoff"
        if stale_after <= at:
            return False, "baseline is stale"
        if model in {AUTOMATIC_ROLLOVER_ACCOUNTING_MODEL, "AUTOMATIC_MONTHLY_ROLLOVER_BOUNDED"}:
            if accounted_total != 0:
                return False, "automatic rollover baseline must be zero-based"
            if local_cutoff != period_start or measurement_cutoff != period_start:
                return False, "automatic rollover cutoffs must equal the approved period start"
            if stale_after != period_end:
                return False, "automatic rollover staleness deadline must equal the approved period end"
        source_hash = str(row.get("SourceEvidenceHash") or "")
        if not _SHA256_PATTERN.fullmatch(source_hash):
            return False, "source evidence hash is missing or malformed"
        reconciliation_key = str(row.get("ReconciliationKey") or "").strip()
        if not reconciliation_key:
            return False, "reconciliation key is missing"
        if any(
            marker in reconciliation_key.casefold()
            for marker in ("password", "secret", "token", "apikey", "credential")
        ):
            return False, "reconciliation key contains a prohibited credential marker"
        _required_text(row.get("SourceDescription"), field="SourceDescription", maximum=_MAX_SOURCE_DESCRIPTION)
    except (QuotaBaselineError, TypeError, ValueError, OverflowError) as exc:
        return False, str(exc)
    return True, "complete current provider-wide baseline"


def load_evidence_file(path: str | Path) -> Mapping[str, Any]:
    """Load one JSON evidence document without accepting arbitrary files."""

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise QuotaBaselineError("provider evidence file does not exist")
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QuotaBaselineError("provider evidence file is not valid UTF-8 JSON") from exc
    if not isinstance(document, Mapping):
        raise QuotaBaselineError("provider evidence file must contain one JSON object")
    return document


__all__ = [
    "ACCOUNTING_MODELS",
    "QuotaBaselineError",
    "canonical_evidence_hash",
    "load_evidence_file",
    "provider_period_month_key",
    "reconcile_provider_evidence",
    "validate_stored_baseline_row",
]
