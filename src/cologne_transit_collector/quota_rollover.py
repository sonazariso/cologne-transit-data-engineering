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
AUTOMATIC_ROLLOVER_ACCOUNTING_MODEL = "AUTOMATIC_MONTHLY_ROLLOVER"
AUTOMATIC_ROLLOVER_STATUS = "ACTIVE"
AUTOMATIC_ROLLOVER_DEFAULT_DELAY_SECONDS = 1800
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


def _contract_values(contract: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(contract, Mapping):
        raise QuotaRolloverError("provider reset contract is required")
    if str(contract.get("ContractStatus", contract.get("contract_status", "")) or "") != AUTOMATIC_ROLLOVER_STATUS:
        raise QuotaRolloverError("provider reset contract is not active")
    mode = str(contract.get("ResetModel", contract.get("reset_model", "")) or "")
    if mode != AUTOMATIC_ROLLOVER_MODE:
        raise QuotaRolloverError("provider reset contract does not establish a calendar-month boundary")
    timezone_name = _text(
        contract.get("ResetTimezone", contract.get("reset_timezone")),
        field="provider reset timezone",
        maximum=80,
    )
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise QuotaRolloverError("provider reset timezone is not a recognized IANA timezone") from exc
    if int(contract.get("ProviderQuotaCeiling", contract.get("provider_quota_ceiling", 0)) or 0) != ABSOLUTE_PROVIDER_CEILING:
        raise QuotaRolloverError("provider reset contract does not preserve the approved quota ceiling")
    if not bool(contract.get("CredentialExclusivityConfirmed", contract.get("credential_exclusivity_confirmed"))):
        raise QuotaRolloverError("provider reset contract lacks exclusive-credential confirmation")
    if not bool(contract.get("ProviderResetBoundaryVerified", contract.get("provider_reset_boundary_verified"))):
        raise QuotaRolloverError("provider reset boundary is not verified")
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
    return {
        "contract_key": key,
        "reset_model": mode,
        "timezone_name": timezone_name,
        "source_evidence_hash": source_hash,
        "effective_from_utc": effective_from,
        "activation_delay_seconds": delay,
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
    delay = int(activation_delay_seconds)
    if delay < 0 or delay > 86_400:
        raise QuotaRolloverError("provider reset activation delay must be between 0 and 86400 seconds")
    local = at.astimezone(zone)
    local_start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0, fold=0)
    if local.month == 12:
        next_local = local_start.replace(year=local.year + 1, month=1)
    else:
        next_local = local_start.replace(month=local.month + 1)
    start_utc = local_start.astimezone(timezone.utc)
    end_utc = next_local.astimezone(timezone.utc)
    return ProviderQuotaPeriod(
        month_key=local_start.strftime("%Y-%m"),
        timezone_name=timezone_name,
        start_utc=start_utc,
        end_utc=end_utc,
        activation_not_before_utc=start_utc + timedelta(seconds=delay),
    )


def build_reset_contract(
    *,
    evidence: Mapping[str, Any],
    contract_key: str,
    reset_timezone: str,
    effective_from_utc: datetime,
    approved_at_utc: datetime,
    activation_delay_seconds: int = AUTOMATIC_ROLLOVER_DEFAULT_DELAY_SECONDS,
) -> dict[str, Any]:
    """Build the administrator-only SQL contract from verified evidence."""

    if not isinstance(evidence, Mapping):
        raise QuotaRolloverError("provider reset evidence is required")
    if str(evidence.get("reset_contract_status") or "") != "VERIFIED":
        raise QuotaRolloverError("provider reset evidence is not marked VERIFIED")
    if evidence.get("provider_reset_boundary_verified") is not True:
        raise QuotaRolloverError("provider reset evidence does not verify the boundary")
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
    timezone_name = _text(reset_timezone, field="reset_timezone", maximum=80)
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise QuotaRolloverError("reset_timezone is not a recognized IANA timezone") from exc
    delay = int(activation_delay_seconds)
    if delay < 0 or delay > 86_400:
        raise QuotaRolloverError("activation delay must be between 0 and 86400 seconds")
    source_hash = str(evidence.get("source_evidence_hash") or "")
    if not _SHA256.fullmatch(source_hash):
        source_hash = canonical_evidence_hash(evidence)
    return {
        "contract_key": key,
        "provider_name": "MDD NRW",
        "reset_model": AUTOMATIC_ROLLOVER_MODE,
        "reset_timezone": timezone_name,
        "effective_from_utc": _iso(effective),
        "activation_delay_seconds": delay,
        "provider_quota_ceiling": ABSOLUTE_PROVIDER_CEILING,
        "credential_exclusivity_confirmed": True,
        "provider_reset_boundary_verified": True,
        "source_evidence_hash": source_hash,
        "evidence_json": json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        "contract_status": AUTOMATIC_ROLLOVER_STATUS,
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
    period = provider_period_for_instant(
        at,
        timezone_name=values["timezone_name"],
        activation_delay_seconds=values["activation_delay_seconds"],
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
        "provider_period_start_utc": _iso(period.start_utc),
        "provider_period_end_utc": _iso(period.end_utc),
        "local_usage_included_through_utc": _iso(period.start_utc),
        "at_utc": _iso(at),
        "source_evidence_hash": values["source_evidence_hash"],
        "reconciliation_key": reconciliation_key,
        "accounting_model": AUTOMATIC_ROLLOVER_ACCOUNTING_MODEL,
        "authorization_mode": AUTOMATIC_ROLLOVER_ACCOUNTING_MODEL,
        "provider_quota_ceiling": ABSOLUTE_PROVIDER_CEILING,
        "normal_operational_maximum": 210_000,
        "retry_protection_boundary": 207_500,
        "activation_not_before_utc": _iso(period.activation_not_before_utc),
        "provider_metadata_verified": True,
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
    "ProviderQuotaPeriod",
    "QuotaRolloverError",
    "build_reset_contract",
    "build_automatic_rollover_period",
    "provider_period_for_instant",
]
