"""Bounded authorization rules for the operator-approved October bootstrap.

The October dashboard does not establish a provider measurement cutoff or a
provider billing timezone.  It therefore cannot be represented as a normal
``COMPLETE`` provider-period baseline.  This module keeps the interim policy
separate and makes its deliberately conservative accounting explicit:

* the observed dashboard value is a lower-bound seed;
* every locally observed October permit through approval is added, even when
  that may overcount requests already shown by the dashboard;
* subsequent authorization counts only permits after the stored local cutoff;
* the authorization expires at the fixed October 30 UTC policy stop.

No provider reset timestamp is inferred here.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from .domain import (
    ABSOLUTE_PROVIDER_CEILING,
    AUTOMATIC_RETRY_PROTECTION_BOUNDARY,
    NORMAL_OPERATIONAL_MAXIMUM,
)
from .quota_baseline import canonical_evidence_hash


BOOTSTRAP_AUTHORIZATION_MODE = "CONSERVATIVE_OCTOBER_BOOTSTRAP"
BOOTSTRAP_ACCOUNTING_MODEL = "EXCLUSIVE_CREDENTIAL"
BOOTSTRAP_MONTH_KEY = "2026-10"
BOOTSTRAP_LATEST_STOP_UTC = datetime(2026, 10, 30, 23, 0, tzinfo=timezone.utc)
BOOTSTRAP_DASHBOARD_REQUESTS = 554
BOOTSTRAP_MONTHLY_LIMIT = ABSOLUTE_PROVIDER_CEILING
BOOTSTRAP_SCREENSHOT_SHA256 = "9b25d4d28cf57f77feccb3b1da30e7e2c8629cec2319ff5a46877e3fac23088f"
KNOWN_PREBOOTSTRAP_RESEARCH_CONTEXT = "LIVE_RESEARCH_SINGLE_1"
BOOTSTRAP_APPROVAL_STATEMENT = (
    "I explicitly approve the bounded October 2026 conservative MDD quota bootstrap: "
    "use the observed dashboard value of 554 requests with the operator-confirmed "
    "exclusive credential model, conservatively include every locally observed October "
    "permit through approval, stop authorization no later than 2026-10-30 23:00 UTC, "
    "fail closed on SQL, accounting, permit, external-usage, or expiry anomalies, "
    "and never reactivate Legacy7."
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_KEY_MARKERS = ("password", "secret", "token", "apikey", "credential")
_LOCAL_FIELDS = (
    "confirmed",
    "unknown",
    "pending",
)
_PERMIT_STATUS_FIELDS = {
    "confirmed": (
        "confirmed",
        "confirmed_attempts",
        "http_provider_attempt_confirmed",
    ),
    "unknown": (
        "unknown",
        "unknown_attempts",
        "conservative",
        "conservative_attempts",
        "conservative_live_attempts",
    ),
    "pending": (
        "pending",
        "pending_attempts",
        "pending_reservations",
    ),
}


class ConservativeBootstrapError(ValueError):
    """The bounded October bootstrap is not safe to authorize."""


def _utc(value: Any, *, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ConservativeBootstrapError(
                f"{field} must be an ISO-8601 timestamp with an explicit timezone"
            ) from exc
    if parsed.tzinfo is None:
        raise ConservativeBootstrapError(f"{field} must include an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _stored_utc(value: Any, *, field: str) -> datetime:
    """Interpret SQL DATETIME2 columns whose names explicitly end in ``Utc``."""

    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return _utc(value, field=field)


def _nonnegative(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ConservativeBootstrapError(f"{field} must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConservativeBootstrapError(f"{field} must be a non-negative integer") from exc
    if parsed < 0:
        raise ConservativeBootstrapError(f"{field} must be non-negative")
    return parsed


def _text(value: Any, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConservativeBootstrapError(f"{field} is required")
    result = value.strip()
    if len(result) > maximum:
        raise ConservativeBootstrapError(f"{field} exceeds its maximum length")
    return result


def _local_count(local_usage: Mapping[str, Any], name: str) -> int:
    for candidate in _PERMIT_STATUS_FIELDS[name]:
        if candidate in local_usage:
            return _nonnegative(local_usage[candidate], field=f"local_usage.{name}")
    permit_counts = local_usage.get("permit_counts")
    if isinstance(permit_counts, Mapping):
        for candidate in _PERMIT_STATUS_FIELDS[name]:
            if candidate in permit_counts:
                return _nonnegative(permit_counts[candidate], field=f"local_usage.permit_counts.{name}")
    return 0


def bootstrap_local_counts(local_usage: Mapping[str, Any]) -> dict[str, int]:
    """Normalize the three local permit classes used by the bootstrap."""

    if not isinstance(local_usage, Mapping):
        raise ConservativeBootstrapError("local SQL permit evidence is required")
    counts = {name: _local_count(local_usage, name) for name in _LOCAL_FIELDS}
    unexpected = local_usage.get("unexpected_statuses")
    if unexpected:
        raise ConservativeBootstrapError("local SQL permit evidence contains an unsupported status")
    if bool(local_usage.get("unexpected_external_usage")):
        raise ConservativeBootstrapError("local SQL permit evidence reports unexpected external usage")
    attempts_without_permit = _nonnegative(
        local_usage.get("attempts_without_permit", 0),
        field="local_usage.attempts_without_permit",
    )
    if attempts_without_permit:
        raise ConservativeBootstrapError("local SQL permit evidence contains an untracked network attempt")
    known_research = _nonnegative(
        local_usage.get("known_untracked_research_attempts", 0),
        field="local_usage.known_untracked_research_attempts",
    )
    if known_research:
        contexts = local_usage.get("untracked_research_attempt_contexts", ())
        if tuple(str(item) for item in contexts) != (KNOWN_PREBOOTSTRAP_RESEARCH_CONTEXT,):
            raise ConservativeBootstrapError("known untracked research attempts have an unsupported context")
        counts["unknown"] += known_research
    counts["known_untracked_research_attempts"] = known_research
    counts["total"] = counts["confirmed"] + counts["unknown"] + counts["pending"]
    return counts


def approval_statement_hash(statement: str = BOOTSTRAP_APPROVAL_STATEMENT) -> str:
    """Return the hash recorded with an explicit operator acceptance."""

    return hashlib.sha256(_text(statement, field="approval_statement", maximum=2000).encode("utf-8")).hexdigest()


def _validate_source_evidence(proposal: Mapping[str, Any], dashboard: Mapping[str, Any]) -> None:
    if str(proposal.get("authorization_mode") or "") != BOOTSTRAP_AUTHORIZATION_MODE:
        raise ConservativeBootstrapError("proposal does not select the bounded October bootstrap mode")
    if str(proposal.get("status") or "") not in {"OPERATOR_APPROVAL_REQUIRED", "APPROVED"}:
        raise ConservativeBootstrapError("bootstrap proposal has an unsupported status")
    scope = proposal.get("scope")
    if not isinstance(scope, Mapping):
        raise ConservativeBootstrapError("bootstrap proposal scope is missing")
    if str(scope.get("selected_year")) != "2026" or str(scope.get("selected_month_label")) != "Oktober":
        raise ConservativeBootstrapError("bootstrap proposal is not for the October 2026 dashboard selection")
    validity = proposal.get("validity_policy")
    if not isinstance(validity, Mapping):
        raise ConservativeBootstrapError("bootstrap validity policy is missing")
    if str(validity.get("latest_stop_utc") or "") != _iso(BOOTSTRAP_LATEST_STOP_UTC):
        raise ConservativeBootstrapError("bootstrap stop boundary differs from the approved October policy")
    if validity.get("latest_stop_is_provider_metadata") is not False:
        raise ConservativeBootstrapError("bootstrap stop boundary must not be represented as provider metadata")

    if not isinstance(dashboard, Mapping):
        raise ConservativeBootstrapError("dashboard evidence is required")
    observed = dashboard.get("observed")
    source = dashboard.get("source")
    attestation = dashboard.get("operator_attestation")
    if not isinstance(observed, Mapping) or not isinstance(source, Mapping) or not isinstance(attestation, Mapping):
        raise ConservativeBootstrapError("dashboard evidence is missing observed facts or operator attestation")
    if int(observed.get("cumulative_requests", -1)) != BOOTSTRAP_DASHBOARD_REQUESTS:
        raise ConservativeBootstrapError("dashboard cumulative request count is not the observed 554")
    if int(observed.get("monthly_limit", -1)) != BOOTSTRAP_MONTHLY_LIMIT:
        raise ConservativeBootstrapError("dashboard monthly limit is not the approved provider ceiling")
    if str(source.get("original_image_sha256") or "") != BOOTSTRAP_SCREENSHOT_SHA256:
        raise ConservativeBootstrapError("dashboard screenshot provenance hash does not match the original evidence")
    if str(attestation.get("status") or "") != "OPERATOR_CONFIRMED_NOT_INDEPENDENTLY_VERIFIED":
        raise ConservativeBootstrapError("exclusive-credential attestation is not the accepted operator status")
    if str(attestation.get("scope") or "") != "credential_exclusivity_for_this_account_and_application":
        raise ConservativeBootstrapError("exclusive-credential attestation scope is unsupported")
    not_established = {str(item) for item in dashboard.get("not_established", ())}
    required_unknowns = {
        "exact provider measurement timestamp or usage cutoff",
        "provider billing or reset timezone and exact UTC period boundaries",
    }
    if not required_unknowns.issubset(not_established):
        raise ConservativeBootstrapError("bootstrap requires provider cutoff and reset metadata to remain explicitly unverified")


def _default_key(approved_at: datetime) -> str:
    return f"MDD-OCTOBER-CONSERVATIVE-BOOTSTRAP:{_iso(approved_at)}"


def build_conservative_bootstrap_authorization(
    *,
    proposal: Mapping[str, Any],
    dashboard: Mapping[str, Any],
    local_usage: Mapping[str, Any],
    approved_at_utc: datetime,
    now_utc: datetime | None = None,
    approval_statement: str = BOOTSTRAP_APPROVAL_STATEMENT,
    authorization_key: str | None = None,
    unexpected_external_usage: bool = False,
) -> dict[str, Any]:
    """Build a sanitized, SQL-ready bootstrap authorization.

    The function is intentionally strict about the missing provider metadata.
    It never fills in a provider timestamp, timezone, or reset boundary.
    """

    _validate_source_evidence(proposal, dashboard)
    approved = _utc(approved_at_utc, field="approved_at_utc")
    now = _utc(now_utc or datetime.now(timezone.utc), field="now_utc")
    if approved > now:
        raise ConservativeBootstrapError("approved_at_utc cannot be in the future")
    if approved >= BOOTSTRAP_LATEST_STOP_UTC:
        raise ConservativeBootstrapError("bootstrap approval is at or after the hard stop boundary")
    if now >= BOOTSTRAP_LATEST_STOP_UTC:
        raise ConservativeBootstrapError("bootstrap validity interval has expired")
    if approved.strftime("%Y-%m") != BOOTSTRAP_MONTH_KEY or now.strftime("%Y-%m") != BOOTSTRAP_MONTH_KEY:
        raise ConservativeBootstrapError("bootstrap authorization is limited to October 2026 UTC")
    if unexpected_external_usage:
        raise ConservativeBootstrapError("unexpected external usage or a competing consumer was reported")
    if bool(local_usage.get("unexpected_external_usage")) or int(local_usage.get("attempts_without_permit", 0) or 0) > 0:
        raise ConservativeBootstrapError("local evidence reports an untracked or externally consumed request")
    statement = _text(approval_statement, field="approval_statement", maximum=2000)
    counts = bootstrap_local_counts(local_usage)
    accounted_total = BOOTSTRAP_DASHBOARD_REQUESTS + counts["total"]
    if accounted_total > ABSOLUTE_PROVIDER_CEILING:
        raise ConservativeBootstrapError("conservative bootstrap accounting exceeds the provider ceiling")

    key = _text(authorization_key or _default_key(approved), field="authorization_key", maximum=240)
    if any(marker in key.casefold() for marker in _KEY_MARKERS):
        raise ConservativeBootstrapError("authorization_key contains a prohibited credential marker")
    evidence_document = {
        "authorization_mode": BOOTSTRAP_AUTHORIZATION_MODE,
        "month_key": BOOTSTRAP_MONTH_KEY,
        "dashboard_cumulative_requests": BOOTSTRAP_DASHBOARD_REQUESTS,
        "dashboard_monthly_limit": BOOTSTRAP_MONTHLY_LIMIT,
        "screenshot_sha256": BOOTSTRAP_SCREENSHOT_SHA256,
        "operator_attestation_status": "OPERATOR_CONFIRMED_NOT_INDEPENDENTLY_VERIFIED",
        "provider_measurement_cutoff": "NOT_ESTABLISHED",
        "provider_period_timezone": "NOT_ESTABLISHED",
        "local_usage": counts,
        "approved_at_utc": _iso(approved),
        "stop_at_utc": _iso(BOOTSTRAP_LATEST_STOP_UTC),
        "approval_statement": statement,
        "authorization_key": key,
    }
    source_hash = canonical_evidence_hash(evidence_document)
    return {
        "authorization_mode": BOOTSTRAP_AUTHORIZATION_MODE,
        "accounting_model": BOOTSTRAP_ACCOUNTING_MODEL,
        "month_key": BOOTSTRAP_MONTH_KEY,
        "screenshot_cumulative_requests": BOOTSTRAP_DASHBOARD_REQUESTS,
        "screenshot_monthly_limit": BOOTSTRAP_MONTHLY_LIMIT,
        "screenshot_sha256": BOOTSTRAP_SCREENSHOT_SHA256,
        "confirmed_local_attempts": counts["confirmed"],
        "conservative_unknown_local_attempts": counts["unknown"],
        "pending_local_reservations": counts["pending"],
        "accounted_total_at_authorization": accounted_total,
        "provider_quota_ceiling": ABSOLUTE_PROVIDER_CEILING,
        "normal_operational_maximum": NORMAL_OPERATIONAL_MAXIMUM,
        "retry_protection_boundary": AUTOMATIC_RETRY_PROTECTION_BOUNDARY,
        "valid_from_utc": _iso(approved),
        "local_usage_included_through_utc": _iso(approved),
        "stop_at_utc": _iso(BOOTSTRAP_LATEST_STOP_UTC),
        "operator_approval_at_utc": _iso(approved),
        "operator_approval_statement_hash": approval_statement_hash(statement),
        "credential_exclusivity_confirmed": True,
        "provider_scope_verified": False,
        "provider_measurement_cutoff_established": False,
        "provider_reset_timezone_established": False,
        "unexpected_external_usage": False,
        "authorization_status": "ACTIVE",
        "source_evidence_hash": source_hash,
        "authorization_key": key,
        "evidence_json": json.dumps(evidence_document, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        "current_conservative_accounted_total": accounted_total,
        "remaining_headroom": {
            "normal_operational_maximum": NORMAL_OPERATIONAL_MAXIMUM - accounted_total,
            "retry_protection_boundary": AUTOMATIC_RETRY_PROTECTION_BOUNDARY - accounted_total,
            "provider_quota_ceiling": ABSOLUTE_PROVIDER_CEILING - accounted_total,
        },
        "stop_conditions": [
            "SQL connectivity or durable permit finalization is unavailable",
            "the bootstrap validity interval expires",
            "the next authorization would reach or exceed an approved boundary",
            "the SQL ledger or quota counters are inconsistent",
            "unexpected provider-wide usage or another consumer is discovered",
            "a pending permit cannot be conservatively recovered",
            "the 2026-10-30T23:00:00Z policy stop is reached",
        ],
    }


def validate_stored_bootstrap_row(row: Mapping[str, Any] | None, *, at_utc: datetime) -> tuple[bool, str]:
    """Validate a persisted bootstrap row at one authorization instant."""

    if not isinstance(row, Mapping):
        return False, "bootstrap authorization row is missing"
    try:
        at = _utc(at_utc, field="at_utc")
        if str(row.get("MonthKey") or "") != BOOTSTRAP_MONTH_KEY:
            return False, "bootstrap month key is not October 2026"
        if str(row.get("AuthorizationMode") or "") != BOOTSTRAP_AUTHORIZATION_MODE:
            return False, "authorization mode is not the bounded October bootstrap"
        if str(row.get("AccountingModel") or "") != BOOTSTRAP_ACCOUNTING_MODEL:
            return False, "accounting model is not exclusive credential accounting"
        if str(row.get("AuthorizationStatus") or "") != "ACTIVE":
            return False, "bootstrap authorization is not active"
        if int(row.get("ScreenshotCumulativeRequests", -1) or -1) != BOOTSTRAP_DASHBOARD_REQUESTS:
            return False, "dashboard cumulative request count is not the approved 554"
        if int(row.get("ScreenshotMonthlyLimit", 0) or 0) != BOOTSTRAP_MONTHLY_LIMIT:
            return False, "dashboard monthly limit is not the approved provider ceiling"
        if str(row.get("ScreenshotSha256") or "") != BOOTSTRAP_SCREENSHOT_SHA256:
            return False, "dashboard screenshot provenance hash does not match the approved evidence"
        if int(row.get("ProviderQuotaCeiling", 0) or 0) != ABSOLUTE_PROVIDER_CEILING:
            return False, "provider ceiling is not the approved value"
        if int(row.get("NormalOperationalMaximum", 0) or 0) != NORMAL_OPERATIONAL_MAXIMUM:
            return False, "normal operational maximum is not the approved value"
        if int(row.get("RetryProtectionBoundary", 0) or 0) != AUTOMATIC_RETRY_PROTECTION_BOUNDARY:
            return False, "retry protection boundary is not the approved value"
        components = (
            "ScreenshotCumulativeRequests",
            "ConfirmedLocalAttempts",
            "ConservativeUnknownLocalAttempts",
            "PendingLocalReservations",
        )
        values = [_nonnegative(row.get(name, 0), field=name) for name in components]
        accounted = _nonnegative(row.get("AccountedTotalAtAuthorization", -1), field="AccountedTotalAtAuthorization")
        if sum(values) != accounted:
            return False, "bootstrap components do not equal the persisted accounted total"
        if accounted > ABSOLUTE_PROVIDER_CEILING:
            return False, "bootstrap accounted usage exceeds the provider ceiling"
        valid_from = _stored_utc(row.get("ValidFromUtc"), field="ValidFromUtc")
        local_cutoff = _stored_utc(row.get("LocalUsageIncludedThroughUtc"), field="LocalUsageIncludedThroughUtc")
        stop_at = _stored_utc(row.get("StopAtUtc"), field="StopAtUtc")
        approval_at = _stored_utc(row.get("OperatorApprovalAtUtc"), field="OperatorApprovalAtUtc")
        if valid_from != approval_at or local_cutoff != approval_at:
            return False, "bootstrap approval and local cutoff timestamps are inconsistent"
        if valid_from >= stop_at or stop_at != BOOTSTRAP_LATEST_STOP_UTC:
            return False, "bootstrap validity interval is invalid"
        if at < valid_from or at >= stop_at:
            return False, "bootstrap authorization is outside its approved validity interval"
        if valid_from.strftime("%Y-%m") != BOOTSTRAP_MONTH_KEY:
            return False, "bootstrap approval is outside October 2026"
        if not bool(row.get("CredentialExclusivityConfirmed")):
            return False, "exclusive-credential attestation is not confirmed"
        if bool(row.get("ProviderScopeVerified")) or bool(row.get("ProviderMeasurementCutoffEstablished")):
            return False, "bootstrap row incorrectly claims verified provider metadata"
        if bool(row.get("ProviderResetTimezoneEstablished")):
            return False, "bootstrap row incorrectly claims a provider reset timezone"
        if bool(row.get("UnexpectedExternalUsage")):
            return False, "bootstrap row reports unexpected external usage"
        for name in ("SourceEvidenceHash", "OperatorApprovalStatementHash"):
            if not _SHA256.fullmatch(str(row.get(name) or "")):
                return False, f"{name} is missing or malformed"
        if str(row.get("OperatorApprovalStatementHash") or "") != approval_statement_hash():
            return False, "operator approval statement hash does not match the bounded S115 approval"
        key = str(row.get("AuthorizationKey") or "")
        if not key or any(marker in key.casefold() for marker in _KEY_MARKERS):
            return False, "authorization key is missing or contains a prohibited marker"
    except (ConservativeBootstrapError, TypeError, ValueError) as exc:
        return False, str(exc)
    return True, "active bounded October bootstrap authorization"


__all__ = [
    "BOOTSTRAP_ACCOUNTING_MODEL",
    "BOOTSTRAP_APPROVAL_STATEMENT",
    "BOOTSTRAP_AUTHORIZATION_MODE",
    "BOOTSTRAP_DASHBOARD_REQUESTS",
    "BOOTSTRAP_LATEST_STOP_UTC",
    "BOOTSTRAP_MONTH_KEY",
    "BOOTSTRAP_MONTHLY_LIMIT",
    "BOOTSTRAP_SCREENSHOT_SHA256",
    "KNOWN_PREBOOTSTRAP_RESEARCH_CONTEXT",
    "ConservativeBootstrapError",
    "approval_statement_hash",
    "bootstrap_local_counts",
    "build_conservative_bootstrap_authorization",
    "validate_stored_bootstrap_row",
]
