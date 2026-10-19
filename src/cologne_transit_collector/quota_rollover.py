"""Pure rules for safe, SQL-backed monthly provider-quota rollover.

The MDD portal exposes a monthly quota, but the public material reviewed for
this repository does not establish the provider's exact reset timezone or
boundary.  Runtime rollover therefore requires an administrator-registered
reset contract.  Once that contract is present, these rules calculate the
period with ``zoneinfo`` (including DST and month-length changes) and require a
deliberate post-boundary delay before SQL can create the zero-based period.
"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .domain import ABSOLUTE_PROVIDER_CEILING
from .quota_baseline import canonical_evidence_hash


AUTOMATIC_ROLLOVER_MODE = "CALENDAR_MONTH_PROVIDER_TIMEZONE"
CONSERVATIVE_ROLLOVER_MODE = "CALENDAR_MONTH_BOUNDED_TIMEZONE_UNCERTAINTY"
AUTOMATIC_ROLLOVER_ACCOUNTING_MODEL = "AUTOMATIC_MONTHLY_ROLLOVER"
CONSERVATIVE_ROLLOVER_ACCOUNTING_MODEL = "AUTOMATIC_MONTHLY_ROLLOVER_BOUNDED"
AUTOMATIC_ROLLOVER_STATUS = "ACTIVE"
AUTOMATIC_ROLLOVER_DEFAULT_DELAY_SECONDS = 1800
BOUNDED_TIMEZONE_ENVELOPE_MARKER = "BOUNDED_TIMEZONE_ENVELOPE"

# Evidence provenance is intentionally separate from the durable SQL row's
# lifecycle.  In particular, an operator-approved conservative assumption is
# never described as provider verification.
RESET_CONTRACT_STATUS_VERIFIED_PROVIDER = "VERIFIED_PROVIDER_CONTRACT"
RESET_CONTRACT_STATUS_APPROVED_OPERATOR_ASSUMPTION = "APPROVED_OPERATOR_ASSUMPTION"
RESET_CONTRACT_STATUS_UNVERIFIED = "UNVERIFIED"
RESET_CONTRACT_STATUS_NOT_REGISTERED = "NOT_REGISTERED"
RESET_CONTRACT_STATUS_REVOKED = "REVOKED"
RESET_CONTRACT_STATUS_EXPIRED = "EXPIRED"

# The database engine's AT TIME ZONE uses Windows identifiers while the
# provider-facing contract uses IANA identifiers.  This is a representation
# mapping only; it does not infer which timezone the provider uses.  Unknown
# exact contracts must carry an explicit server identifier from authoritative
# evidence.
IANA_TO_SQL_SERVER_TIMEZONE = {
    "Europe/Berlin": "W. Europe Standard Time",
}
SQL_RESET_TIMEZONE_FIELD = "Sql" "ServerResetTimezone"
# POSIX ``Etc/GMT`` names reverse their sign: GMT+12 is UTC-12 and GMT-14 is
# UTC+14.  The endpoints are sufficient for a calendar-month bound because
# every supported civil offset lies between them; zoneinfo still supplies the
# month-length/DST-aware conversion used by the general bounded policy.
GLOBAL_UTC_OFFSET_ENVELOPE_TIMEZONES = ("Etc/GMT+12", "Etc/GMT-14")
GLOBAL_UTC_OFFSET_ENVELOPE_LABEL = "UTC-12 through UTC+14"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MONTH_KEY = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_KEY_MARKERS = ("password", "secret", "token", "apikey", "credential")


class QuotaRolloverError(ValueError):
    """The provider reset contract cannot safely authorize a new period."""


def _utc(value: Any, *, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise QuotaRolloverError(
                f"{field} must be an ISO-8601 timestamp with an explicit timezone"
            ) from exc
    if parsed.tzinfo is None:
        raise QuotaRolloverError(f"{field} must include an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _text(value: Any, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QuotaRolloverError(f"{field} is required")
    result = value.strip()
    if len(result) > maximum:
        raise QuotaRolloverError(f"{field} exceeds its maximum length")
    return result


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _stored_utc(value: Any, *, field: str) -> datetime:
    """Interpret a SQL DATETIME2 value from a column explicitly named UTC."""

    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return _utc(value, field=field)


def _timezone_names(value: Any, *, field: str = "supported_reset_timezones") -> tuple[str, ...]:
    if isinstance(value, str):
        candidates = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, (list, tuple, set, frozenset)):
        candidates = [str(item).strip() for item in value if str(item).strip()]
    else:
        candidates = []
    if not candidates or len(candidates) > 64 or len(set(candidates)) != len(candidates):
        raise QuotaRolloverError(f"{field} must contain 1 to 64 unique IANA timezones")
    for timezone_name in candidates:
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise QuotaRolloverError(f"{field} contains an unrecognized IANA timezone") from exc
    return tuple(sorted(candidates))


def _bounded_timezone_marker(timezone_names: tuple[str, ...]) -> str:
    marker = BOUNDED_TIMEZONE_ENVELOPE_MARKER
    if len(marker) > 80:
        raise QuotaRolloverError("bounded timezone marker is too long")
    return marker


def classify_reset_contract_status(
    contract: Mapping[str, Any] | None,
    *,
    now_utc: datetime | None = None,
) -> str:
    """Return durable contract provenance without calling assumptions verified.

    ``ContractStatus`` describes SQL lifecycle. The evidence flags describe
    provenance. Both are considered so reporting can distinguish an inactive,
    revoked, expired, exact-provider, and operator-assumption contract.
    """

    if not isinstance(contract, Mapping):
        return RESET_CONTRACT_STATUS_NOT_REGISTERED
    lifecycle = str(contract.get("ContractStatus", contract.get("contract_status", "")) or "").upper()
    if lifecycle == "REVOKED":
        return RESET_CONTRACT_STATUS_REVOKED
    if lifecycle == "EXPIRED":
        return RESET_CONTRACT_STATUS_EXPIRED
    expiry = contract.get(
        "ExpiresAtUtc",
        contract.get("expires_at_utc", contract.get("ExpiredAtUtc", contract.get("expired_at_utc"))),
    )
    if expiry is not None:
        try:
            if _utc(expiry, field="contract expiry") <= _utc(
                now_utc or datetime.now(timezone.utc),
                field="now_utc",
            ):
                return RESET_CONTRACT_STATUS_EXPIRED
        except QuotaRolloverError:
            return RESET_CONTRACT_STATUS_UNVERIFIED
    if lifecycle != AUTOMATIC_ROLLOVER_STATUS:
        return RESET_CONTRACT_STATUS_NOT_REGISTERED
    mode = str(contract.get("ResetModel", contract.get("reset_model", "")) or "")
    if mode == AUTOMATIC_ROLLOVER_MODE and bool(
        contract.get("ProviderResetBoundaryVerified", contract.get("provider_reset_boundary_verified"))
    ):
        return RESET_CONTRACT_STATUS_VERIFIED_PROVIDER
    if mode == CONSERVATIVE_ROLLOVER_MODE and bool(
        contract.get("OperatorAssumptionApproved", contract.get("operator_assumption_approved"))
    ) and not bool(contract.get("ProviderResetBoundaryVerified", contract.get("provider_reset_boundary_verified"))):
        return RESET_CONTRACT_STATUS_APPROVED_OPERATOR_ASSUMPTION
    return RESET_CONTRACT_STATUS_UNVERIFIED


@dataclass(frozen=True)
class ProviderQuotaPeriod:
    """One provider calendar-month period expressed in UTC and IANA time."""

    month_key: str
    timezone_name: str
    start_utc: datetime
    end_utc: datetime
    activation_not_before_utc: datetime

    def as_mapping(self) -> dict[str, Any]:
        return {
            "month_key": self.month_key,
            "provider_period_timezone": self.timezone_name,
            "provider_period_start_utc": _iso(self.start_utc),
            "provider_period_end_utc": _iso(self.end_utc),
            "activation_not_before_utc": _iso(self.activation_not_before_utc),
        }


def _month_parts(month_key: str, *, field: str = "month_key") -> tuple[int, int]:
    value = _text(month_key, field=field, maximum=7)
    if not _MONTH_KEY.fullmatch(value):
        raise QuotaRolloverError(f"{field} must be a canonical YYYY-MM month")
    year, month = (int(part) for part in value.split("-"))
    return year, month


def _next_month_start(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _period_for_local_month(
    month_key: str,
    *,
    timezone_name: str,
    activation_delay_seconds: int,
) -> ProviderQuotaPeriod:
    """Calculate one complete civil month without requiring an instant inside it."""

    year, month = _month_parts(month_key)
    timezone_name = _text(timezone_name, field="provider_period_timezone", maximum=80)
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise QuotaRolloverError("provider period timezone is not a recognized IANA timezone") from exc
    delay = int(activation_delay_seconds)
    if delay < 0 or delay > 86_400:
        raise QuotaRolloverError("provider reset activation delay must be between 0 and 86400 seconds")
    next_year, next_month = _next_month_start(year, month)
    local_start = datetime(year, month, 1, tzinfo=zone, fold=0)
    local_end = datetime(next_year, next_month, 1, tzinfo=zone, fold=0)
    start_utc = local_start.astimezone(timezone.utc)
    end_utc = local_end.astimezone(timezone.utc)
    return ProviderQuotaPeriod(
        month_key=month_key,
        timezone_name=timezone_name,
        start_utc=start_utc,
        end_utc=end_utc,
        activation_not_before_utc=start_utc + timedelta(seconds=delay),
    )


def _contract_values(contract: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(contract, Mapping):
        raise QuotaRolloverError("provider reset contract is required")
    if str(contract.get("ContractStatus", contract.get("contract_status", "")) or "") != AUTOMATIC_ROLLOVER_STATUS:
        raise QuotaRolloverError("provider reset contract is not active")
    mode = str(contract.get("ResetModel", contract.get("reset_model", "")) or "")
    if mode not in {AUTOMATIC_ROLLOVER_MODE, CONSERVATIVE_ROLLOVER_MODE}:
        raise QuotaRolloverError("provider reset contract does not establish a calendar-month boundary")
    timezone_value = contract.get("ResetTimezone", contract.get("reset_timezone"))
    timezone_name = _text(timezone_value, field="provider reset timezone", maximum=80)
    timezone_names: tuple[str, ...]
    if mode == AUTOMATIC_ROLLOVER_MODE:
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise QuotaRolloverError("provider reset timezone is not a recognized IANA timezone") from exc
        timezone_names = (timezone_name,)
        if not bool(contract.get("ProviderResetBoundaryVerified", contract.get("provider_reset_boundary_verified"))):
            raise QuotaRolloverError("provider reset boundary is not verified")
    else:
        if timezone_name != BOUNDED_TIMEZONE_ENVELOPE_MARKER:
            raise QuotaRolloverError("bounded timezone policy must use the authorized timezone-envelope marker")
        supported_timezones = contract.get(
            "SupportedResetTimezones",
            contract.get("supported_reset_timezones"),
        )
        if supported_timezones is None:
            supported_timezones = contract.get(
                "SupportedResetTimezonesJson",
                contract.get("supported_reset_timezones_json"),
            )
            if isinstance(supported_timezones, str):
                try:
                    supported_timezones = json.loads(supported_timezones)
                except json.JSONDecodeError as exc:
                    raise QuotaRolloverError(
                        "registered bounded reset contract has invalid timezone-envelope JSON"
                    ) from exc
        timezone_names = _timezone_names(supported_timezones)
        if bool(contract.get("ProviderResetBoundaryVerified", contract.get("provider_reset_boundary_verified"))):
            raise QuotaRolloverError("bounded timezone policy must not claim an exact provider boundary")
        if not bool(
            contract.get(
                "CalendarMonthResetModelEstablished",
                contract.get("calendar_month_reset_model_established"),
            )
        ):
            raise QuotaRolloverError("calendar-month reset model is not established")
        if not bool(
            contract.get(
                "OperatorAssumptionApproved",
                contract.get("operator_assumption_approved"),
            )
        ):
            raise QuotaRolloverError("bounded timezone policy lacks explicit operator approval")
    if int(contract.get("ProviderQuotaCeiling", contract.get("provider_quota_ceiling", 0)) or 0) != ABSOLUTE_PROVIDER_CEILING:
        raise QuotaRolloverError("provider reset contract does not preserve the approved quota ceiling")
    if not bool(contract.get("CredentialExclusivityConfirmed", contract.get("credential_exclusivity_confirmed"))):
        raise QuotaRolloverError("provider reset contract lacks exclusive-credential confirmation")
    source_hash = _text(
        contract.get("SourceEvidenceHash", contract.get("source_evidence_hash")),
        field="provider reset evidence hash",
        maximum=64,
    )
    if not _SHA256.fullmatch(source_hash):
        raise QuotaRolloverError("provider reset evidence hash is malformed")
    key = _text(
        contract.get("ContractKey", contract.get("contract_key")),
        field="provider reset contract key",
        maximum=240,
    )
    if any(marker in key.casefold() for marker in _KEY_MARKERS):
        raise QuotaRolloverError("provider reset contract key contains a prohibited marker")
    effective_from = _stored_utc(
        contract.get("EffectiveFromUtc", contract.get("effective_from_utc")),
        field="provider reset contract effective_from_utc",
    )
    delay = int(
        contract.get(
            "ActivationDelaySeconds",
            contract.get("activation_delay_seconds", AUTOMATIC_ROLLOVER_DEFAULT_DELAY_SECONDS),
        )
        or 0
    )
    if delay < 0 or delay > 86_400:
        raise QuotaRolloverError("provider reset activation delay must be between 0 and 86400 seconds")
    sql_server_timezone = contract.get(
        SQL_RESET_TIMEZONE_FIELD,
        contract.get("sql_server_reset_timezone"),
    )
    if sql_server_timezone is not None:
        sql_server_timezone = _text(
            sql_server_timezone,
            field="server reset timezone",
            maximum=128,
        )
    return {
        "contract_key": key,
        "reset_model": mode,
        "timezone_name": timezone_name,
        "timezone_names": timezone_names,
        "bounded_timezone_policy": mode == CONSERVATIVE_ROLLOVER_MODE,
        "source_evidence_hash": source_hash,
        "effective_from_utc": effective_from,
        "activation_delay_seconds": delay,
        "sql_server_reset_timezone": sql_server_timezone,
    }


def provider_period_for_instant(
    at_utc: datetime,
    *,
    timezone_name: str,
    activation_delay_seconds: int = AUTOMATIC_ROLLOVER_DEFAULT_DELAY_SECONDS,
) -> ProviderQuotaPeriod:
    """Calculate the containing provider calendar month without assuming UTC."""

    at = _utc(at_utc, field="at_utc")
    timezone_name = _text(timezone_name, field="provider_period_timezone", maximum=80)
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise QuotaRolloverError("provider period timezone is not a recognized IANA timezone") from exc
    local = at.astimezone(zone)
    return _period_for_local_month(
        local.strftime("%Y-%m"),
        timezone_name=timezone_name,
        activation_delay_seconds=activation_delay_seconds,
    )


def provider_period_for_month(
    month_key: str,
    *,
    timezone_name: str,
    activation_delay_seconds: int = AUTOMATIC_ROLLOVER_DEFAULT_DELAY_SECONDS,
) -> ProviderQuotaPeriod:
    """Calculate an exact provider calendar month from its explicit key."""

    return _period_for_local_month(
        month_key,
        timezone_name=timezone_name,
        activation_delay_seconds=activation_delay_seconds,
    )


def _bounded_period_for_month(
    month_key: str,
    *,
    timezone_names: tuple[str, ...] | list[str],
    activation_delay_seconds: int,
) -> ProviderQuotaPeriod:
    """Calculate a complete conservative envelope period for one month."""

    year, month = _month_parts(month_key)
    zones = _timezone_names(timezone_names)
    delay = int(activation_delay_seconds)
    if delay < 0 or delay > 86_400:
        raise QuotaRolloverError("provider reset activation delay must be between 0 and 86400 seconds")
    next_year, next_month = _next_month_start(year, month)
    starts: list[datetime] = []
    ends: list[datetime] = []
    for timezone_name in zones:
        zone = ZoneInfo(timezone_name)
        local_start = datetime(year, month, 1, tzinfo=zone, fold=0)
        local_end = datetime(next_year, next_month, 1, tzinfo=zone, fold=0)
        starts.append(local_start.astimezone(timezone.utc))
        ends.append(local_end.astimezone(timezone.utc))
    latest_start = max(starts)
    earliest_end = min(ends)
    activation_not_before = latest_start + timedelta(seconds=delay)
    if earliest_end <= latest_start or activation_not_before >= earliest_end:
        raise QuotaRolloverError("bounded timezone envelope leaves no safe activation interval")
    return ProviderQuotaPeriod(
        month_key=month_key,
        timezone_name=_bounded_timezone_marker(zones),
        start_utc=latest_start,
        end_utc=earliest_end,
        activation_not_before_utc=activation_not_before,
    )


def bounded_provider_period_for_instant(
    at_utc: datetime,
    *,
    timezone_names: tuple[str, ...] | list[str],
    activation_delay_seconds: int = AUTOMATIC_ROLLOVER_DEFAULT_DELAY_SECONDS,
) -> ProviderQuotaPeriod:
    """Return a safe interval after every supported timezone entered a month.

    This policy is deliberately opt-in.  It does not identify the provider's
    actual timezone; it waits until the latest possible calendar-month start
    and stops using the bounded interval before the earliest possible next
    month start.  DST and month length are calculated with ``zoneinfo``.
    """

    at = _utc(at_utc, field="at_utc")
    zones = _timezone_names(timezone_names)
    local_keys = {at.astimezone(ZoneInfo(name)).strftime("%Y-%m") for name in zones}
    if len(local_keys) != 1:
        raise QuotaRolloverError("bounded rollover is not yet after one common provider calendar month")
    month_key = next(iter(local_keys))
    period = _bounded_period_for_month(
        month_key,
        timezone_names=zones,
        activation_delay_seconds=activation_delay_seconds,
    )
    if at < period.activation_not_before_utc:
        raise QuotaRolloverError("provider reset period is inside the conservative activation delay")
    return period


def bounded_provider_period_for_month(
    month_key: str,
    *,
    timezone_names: tuple[str, ...] | list[str],
    activation_delay_seconds: int = AUTOMATIC_ROLLOVER_DEFAULT_DELAY_SECONDS,
) -> ProviderQuotaPeriod:
    """Calculate a conservative envelope period from an explicit month key."""

    return _bounded_period_for_month(
        month_key,
        timezone_names=timezone_names,
        activation_delay_seconds=activation_delay_seconds,
    )


def derive_collection_gap(
    previous_period_end_utc: datetime | str,
    next_period_activation_not_before_utc: datetime | str,
) -> dict[str, Any]:
    """Derive an inter-period collection gap from two UTC boundaries.

    The human-readable duration is produced from the same instants as the
    numeric values, so documentation cannot silently drift from the executable
    boundary calculation.
    """

    previous_end = _utc(previous_period_end_utc, field="previous_period_end_utc")
    next_activation = _utc(
        next_period_activation_not_before_utc,
        field="next_period_activation_not_before_utc",
    )
    delta = next_activation - previous_end
    if delta.total_seconds() < 0:
        raise QuotaRolloverError("next period activation must not precede the previous period end")
    total_seconds = int(delta.total_seconds())
    if delta.total_seconds() != total_seconds:
        raise QuotaRolloverError("collection gap boundaries must have whole-second precision")
    total_hours, remainder = divmod(total_seconds, 3_600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if total_hours:
        parts.append(f"{total_hours} hour" + ("s" if total_hours != 1 else ""))
    if minutes or total_hours:
        parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
    if seconds or not parts:
        parts.append(f"{seconds} second" + ("s" if seconds != 1 else ""))
    return {
        "previous_period_end_utc": _iso(previous_end),
        "next_period_activation_not_before_utc": _iso(next_activation),
        "collection_gap_seconds": total_seconds,
        "collection_gap_minutes": total_seconds // 60,
        "collection_gap_hours": total_seconds / 3_600,
        "collection_gap_duration": " ".join(parts),
    }


def derive_period_transition_gap(
    previous_period: ProviderQuotaPeriod,
    next_period: ProviderQuotaPeriod,
) -> dict[str, Any]:
    """Derive the gap between two executable provider-period objects."""

    return derive_collection_gap(
        previous_period.end_utc,
        next_period.activation_not_before_utc,
    )


def global_utc_offset_envelope_period_for_instant(
    at_utc: datetime,
    *,
    activation_delay_seconds: int = AUTOMATIC_ROLLOVER_DEFAULT_DELAY_SECONDS,
) -> ProviderQuotaPeriod:
    """Calculate the conservative period for the full UTC-12..UTC+14 envelope.

    This is a policy candidate, not provider evidence.  It waits until the
    latest possible month start (UTC-12) plus the safety delay and stops at the
    earliest possible following-month start (UTC+14).  The policy remains
    fail-closed until an operator explicitly approves it and SQL registers the
    resulting contract.
    """

    return bounded_provider_period_for_instant(
        at_utc,
        timezone_names=GLOBAL_UTC_OFFSET_ENVELOPE_TIMEZONES,
        activation_delay_seconds=activation_delay_seconds,
    )


def global_utc_offset_envelope_period_for_month(
    month_key: str,
    *,
    activation_delay_seconds: int = AUTOMATIC_ROLLOVER_DEFAULT_DELAY_SECONDS,
) -> ProviderQuotaPeriod:
    """Calculate a complete global-envelope period from an explicit month key."""

    return bounded_provider_period_for_month(
        month_key,
        timezone_names=GLOBAL_UTC_OFFSET_ENVELOPE_TIMEZONES,
        activation_delay_seconds=activation_delay_seconds,
    )


def build_reset_contract(
    *,
    evidence: Mapping[str, Any],
    contract_key: str,
    reset_timezone: str,
    effective_from_utc: datetime,
    approved_at_utc: datetime,
    activation_delay_seconds: int = AUTOMATIC_ROLLOVER_DEFAULT_DELAY_SECONDS,
    supported_reset_timezones: list[str] | tuple[str, ...] | None = None,
    sql_server_reset_timezone: str | None = None,
    approve_bounded_assumption: bool = False,
) -> dict[str, Any]:
    """Build the administrator-only SQL contract from verified evidence."""

    if not isinstance(evidence, Mapping):
        raise QuotaRolloverError("provider reset evidence is required")
    evidence_status = str(evidence.get("reset_contract_status") or "")
    exact_verified = evidence_status in {
        "VERIFIED",
        RESET_CONTRACT_STATUS_VERIFIED_PROVIDER,
    } and evidence.get("provider_reset_boundary_verified") is True
    bounded_approved = (
        evidence_status in {
            "APPROVED_ASSUMPTION",
            RESET_CONTRACT_STATUS_APPROVED_OPERATOR_ASSUMPTION,
        }
        and evidence.get("provider_monthly_reset_model_established") is True
        and approve_bounded_assumption
    )
    if not exact_verified and not bounded_approved:
        raise QuotaRolloverError("provider reset evidence is neither verified nor explicitly approved for bounded rollover")
    if evidence.get("credential_exclusivity_confirmed") is not True:
        raise QuotaRolloverError("provider reset evidence lacks exclusive-credential confirmation")
    approved = _utc(approved_at_utc, field="approved_at_utc")
    effective = _utc(effective_from_utc, field="effective_from_utc")
    now = datetime.now(timezone.utc)
    if approved > now:
        raise QuotaRolloverError("approved_at_utc cannot be in the future")
    if effective < approved:
        raise QuotaRolloverError("effective_from_utc cannot precede the explicit approval")
    key = _text(contract_key, field="contract_key", maximum=240)
    if any(marker in key.casefold() for marker in _KEY_MARKERS):
        raise QuotaRolloverError("contract_key contains a prohibited marker")
    if exact_verified:
        timezone_name = _text(reset_timezone, field="reset_timezone", maximum=80)
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise QuotaRolloverError("reset_timezone is not a recognized IANA timezone") from exc
        timezone_names = (timezone_name,)
        reset_model = AUTOMATIC_ROLLOVER_MODE
        sql_timezone = str(
            sql_server_reset_timezone
            or evidence.get("sql_server_reset_timezone")
            or IANA_TO_SQL_SERVER_TIMEZONE.get(timezone_name)
            or ""
        ).strip()
        if not sql_timezone:
            raise QuotaRolloverError(
                "exact reset contracts require an explicit server timezone identifier"
            )
        sql_timezone = _text(sql_timezone, field="sql_server_reset_timezone", maximum=128)
    else:
        timezone_names = _timezone_names(
            supported_reset_timezones or evidence.get("supported_reset_timezones")
        )
        timezone_name = _bounded_timezone_marker(timezone_names)
        reset_model = CONSERVATIVE_ROLLOVER_MODE
        sql_timezone = None
    delay = int(activation_delay_seconds)
    if delay < 0 or delay > 86_400:
        raise QuotaRolloverError("activation delay must be between 0 and 86400 seconds")
    source_hash = str(evidence.get("source_evidence_hash") or "")
    if not _SHA256.fullmatch(source_hash):
        source_hash = canonical_evidence_hash(evidence)
    approval_provenance = {
        "approved_at_utc": _iso(approved),
        "approval_basis": (
            RESET_CONTRACT_STATUS_VERIFIED_PROVIDER
            if exact_verified
            else RESET_CONTRACT_STATUS_APPROVED_OPERATOR_ASSUMPTION
        ),
        "bounded_assumption_approved": bounded_approved,
        "contract_key": key,
        "source_evidence_hash": source_hash,
    }
    evidence_payload = dict(evidence)
    evidence_payload["registration_approval"] = approval_provenance
    return {
        "contract_key": key,
        "provider_name": "MDD NRW",
        "reset_model": reset_model,
        "reset_timezone": timezone_name,
        "sql_server_reset_timezone": sql_timezone,
        "effective_from_utc": _iso(effective),
        "activation_delay_seconds": delay,
        "provider_quota_ceiling": ABSOLUTE_PROVIDER_CEILING,
        "credential_exclusivity_confirmed": True,
        "provider_reset_boundary_verified": exact_verified,
        "calendar_month_reset_model_established": exact_verified or bounded_approved,
        "operator_assumption_approved": bounded_approved,
        "supported_reset_timezones": list(timezone_names),
        "supported_reset_timezones_json": json.dumps(list(timezone_names), ensure_ascii=False, sort_keys=True),
        "source_evidence_hash": source_hash,
        "evidence_json": json.dumps(evidence_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        "approval_provenance": approval_provenance,
        "contract_status": AUTOMATIC_ROLLOVER_STATUS,
        "contract_verification_status": (
            RESET_CONTRACT_STATUS_VERIFIED_PROVIDER
            if exact_verified
            else RESET_CONTRACT_STATUS_APPROVED_OPERATOR_ASSUMPTION
        ),
        "approved_at_utc": _iso(approved),
    }


def build_automatic_rollover_period(
    *,
    contract: Mapping[str, Any],
    at_utc: datetime,
) -> dict[str, Any]:
    """Return the SQL procedure arguments for one verified future period.

    A contract can be registered once and then reused by every later month.
    The SQL procedure still locks the contract and the accounting row before
    inserting, so repeated launchd heartbeats and concurrent workers are
    idempotent.
    """

    values = _contract_values(contract)
    at = _utc(at_utc, field="at_utc")
    period = (
        bounded_provider_period_for_instant(
            at,
            timezone_names=values["timezone_names"],
            activation_delay_seconds=values["activation_delay_seconds"],
        )
        if values["bounded_timezone_policy"]
        else provider_period_for_instant(
            at,
            timezone_name=values["timezone_name"],
            activation_delay_seconds=values["activation_delay_seconds"],
        )
    )
    if period.start_utc < values["effective_from_utc"]:
        raise QuotaRolloverError("provider reset contract is not effective for this period")
    if at < period.activation_not_before_utc:
        raise QuotaRolloverError("provider reset period is inside the conservative activation delay")
    month_key = period.month_key
    if not _MONTH_KEY.fullmatch(month_key):
        raise QuotaRolloverError("calculated provider period key is malformed")
    reconciliation_key = f"AUTO-ROLLOVER:{values['contract_key']}:{month_key}"
    if len(reconciliation_key) > 240:
        raise QuotaRolloverError("automatic rollover reconciliation key is too long")
    return {
        "contract_key": values["contract_key"],
        "month_key": month_key,
        "provider_period_timezone": values["timezone_name"],
        "sql_server_reset_timezone": values["sql_server_reset_timezone"],
        "provider_period_start_utc": _iso(period.start_utc),
        "provider_period_end_utc": _iso(period.end_utc),
        "local_usage_included_through_utc": _iso(period.start_utc),
        "at_utc": _iso(at),
        "source_evidence_hash": values["source_evidence_hash"],
        "reconciliation_key": reconciliation_key,
        "accounting_model": (
            CONSERVATIVE_ROLLOVER_ACCOUNTING_MODEL
            if values["bounded_timezone_policy"]
            else AUTOMATIC_ROLLOVER_ACCOUNTING_MODEL
        ),
        "authorization_mode": (
            CONSERVATIVE_ROLLOVER_ACCOUNTING_MODEL
            if values["bounded_timezone_policy"]
            else AUTOMATIC_ROLLOVER_ACCOUNTING_MODEL
        ),
        "provider_quota_ceiling": ABSOLUTE_PROVIDER_CEILING,
        "normal_operational_maximum": 210_000,
        "retry_protection_boundary": 207_500,
        "activation_not_before_utc": _iso(period.activation_not_before_utc),
        "provider_metadata_verified": not values["bounded_timezone_policy"],
        "provider_metadata_source": (
            "OPERATOR_APPROVED_BOUNDED_ASSUMPTION"
            if values["bounded_timezone_policy"]
            else "PROVIDER_VERIFIED_EXACT_CONTRACT"
        ),
        "contract_verification_status": (
            RESET_CONTRACT_STATUS_APPROVED_OPERATOR_ASSUMPTION
            if values["bounded_timezone_policy"]
            else RESET_CONTRACT_STATUS_VERIFIED_PROVIDER
        ),
        "provider_reset_model_established": True,
        "bounded_timezone_uncertainty": values["bounded_timezone_policy"],
        "supported_reset_timezones": list(values["timezone_names"]),
        "baseline_components": {
            "confirmed": 0,
            "unknown": 0,
            "reserved": 0,
            "total": 0,
        },
        "stop_conditions": [
            "the reset contract is revoked or its evidence becomes stale",
            "a prior-period permit remains pending or an attempt has no permit",
            "SQL connectivity or atomic period creation is unavailable",
            "the provider-period boundary is ambiguous or overlaps another accounting row",
            "the operational or retry boundary is reached",
        ],
    }


__all__ = [
    "AUTOMATIC_ROLLOVER_ACCOUNTING_MODEL",
    "AUTOMATIC_ROLLOVER_DEFAULT_DELAY_SECONDS",
    "AUTOMATIC_ROLLOVER_MODE",
    "AUTOMATIC_ROLLOVER_STATUS",
    "BOUNDED_TIMEZONE_ENVELOPE_MARKER",
    "RESET_CONTRACT_STATUS_APPROVED_OPERATOR_ASSUMPTION",
    "RESET_CONTRACT_STATUS_EXPIRED",
    "RESET_CONTRACT_STATUS_NOT_REGISTERED",
    "RESET_CONTRACT_STATUS_REVOKED",
    "RESET_CONTRACT_STATUS_UNVERIFIED",
    "RESET_CONTRACT_STATUS_VERIFIED_PROVIDER",
    "CONSERVATIVE_ROLLOVER_ACCOUNTING_MODEL",
    "CONSERVATIVE_ROLLOVER_MODE",
    "GLOBAL_UTC_OFFSET_ENVELOPE_LABEL",
    "GLOBAL_UTC_OFFSET_ENVELOPE_TIMEZONES",
    "ProviderQuotaPeriod",
    "QuotaRolloverError",
    "bounded_provider_period_for_instant",
    "bounded_provider_period_for_month",
    "build_reset_contract",
    "build_automatic_rollover_period",
    "classify_reset_contract_status",
    "derive_collection_gap",
    "derive_period_transition_gap",
    "global_utc_offset_envelope_period_for_instant",
    "global_utc_offset_envelope_period_for_month",
    "provider_period_for_month",
    "provider_period_for_instant",
]
