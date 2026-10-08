"""Atomic shared-credential quota policy.

The provider-facing usage state is deliberately separate from local transport
audit rows. A development provider may record simulated calls, but only
explicit confirmed/shared usage and outstanding quota permits participate in a
quota decision.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional

from .domain import (
    ABSOLUTE_PROVIDER_CEILING,
    AUTOMATIC_RETRY_PROTECTION_BOUNDARY,
    NORMAL_OPERATIONAL_MAXIMUM,
    approved_quota_boundary,
)
from .ports import UsageProvider


PENDING_AUTHORIZATION = "PENDING_AUTHORIZATION"
RELEASED_NO_NETWORK = "RELEASED_NO_NETWORK"
CONSERVATIVE_LIVE_ATTEMPT = "CONSERVATIVE_LIVE_ATTEMPT"
HTTP_PROVIDER_ATTEMPT_CONFIRMED = "HTTP_PROVIDER_ATTEMPT_CONFIRMED"

# Longer names make the lifecycle readable to adapters and tests while the
# short constants above keep persisted values manageable.
PERMIT_PENDING_AUTHORIZATION = PENDING_AUTHORIZATION
PERMIT_RELEASED_NO_NETWORK = RELEASED_NO_NETWORK
PERMIT_CONSERVATIVE_LIVE_ATTEMPT = CONSERVATIVE_LIVE_ATTEMPT
PERMIT_HTTP_PROVIDER_ATTEMPT_CONFIRMED = HTTP_PROVIDER_ATTEMPT_CONFIRMED


@dataclass(frozen=True)
class UsageSnapshot:
    """Known shared-credential usage for the active accounting period."""

    confirmed_provider_attempts: int = 0
    reserved_shared_usage: int = 0
    projected_non_retry_attempts: int = 0
    conservative_live_attempts: int = 0
    live_client_invocations: int = 0
    http_responses_received: int = 0
    provider_quota_unknown_attempts: int = 0
    state_available: bool = True
    source: str = "injected"
    actual_attempts: int | None = None

    def __post_init__(self) -> None:
        # ``actual_attempts`` is retained only as a constructor/readback alias
        # for older injected fixtures. It always mirrors confirmed provider
        # usage and never means local simulated transport activity.
        confirmed = int(self.confirmed_provider_attempts)
        if self.actual_attempts is not None and confirmed == 0:
            confirmed = int(self.actual_attempts)
        object.__setattr__(self, "confirmed_provider_attempts", confirmed)
        object.__setattr__(self, "actual_attempts", confirmed)
        for field_name in (
            "reserved_shared_usage",
            "projected_non_retry_attempts",
            "conservative_live_attempts",
            "live_client_invocations",
            "http_responses_received",
            "provider_quota_unknown_attempts",
        ):
            object.__setattr__(self, field_name, int(getattr(self, field_name)))

    @property
    def known_total(self) -> int:
        return (
            self.confirmed_provider_attempts
            + self.reserved_shared_usage
            + self.projected_non_retry_attempts
            + self.conservative_live_attempts
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "UsageSnapshot":
        return cls(
            confirmed_provider_attempts=int(
                value.get(
                    "confirmed_provider_attempts",
                    value.get(
                        "confirmed_provider_http_attempts",
                        value.get("actual_attempts", value.get("actual_http_attempts", 0)),
                    ),
                )
            ),
            reserved_shared_usage=int(value.get("reserved_shared_usage", value.get("reserved_attempts", 0))),
            projected_non_retry_attempts=int(
                value.get("projected_non_retry_attempts", value.get("projected_attempts", 0))
            ),
            conservative_live_attempts=int(
                value.get(
                    "conservative_live_attempts",
                    value.get("conservative_attempts", 0),
                )
            ),
            live_client_invocations=int(value.get("live_client_invocations", 0)),
            http_responses_received=int(value.get("http_responses_received", 0)),
            provider_quota_unknown_attempts=int(value.get("provider_quota_unknown_attempts", 0)),
            state_available=bool(value.get("state_available", True)),
            source=str(value.get("source", "injected")),
        )


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    decision: str
    proposed_attempts: int
    known_total: Optional[int]
    boundary: int
    reason: str
    permit_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "decision": self.decision,
            "proposed_attempts": self.proposed_attempts,
            "known_total": self.known_total,
            "boundary": self.boundary,
            "reason": self.reason,
            "permit_id": self.permit_id,
        }


@dataclass
class _Permit:
    scope: str
    amount: int
    decision_kind: str
    boundary: int
    authorization_key: str | None = None
    status: str = PENDING_AUTHORIZATION


class InMemoryUsageProvider:
    """Explicitly injected usage state with an atomic permit implementation."""

    def __init__(self, snapshot: UsageSnapshot | None = None) -> None:
        self.snapshot = snapshot or UsageSnapshot()
        self._lock = threading.Lock()
        self._permits: dict[str, _Permit] = {}

    def current_month_usage(self, *, at_utc: datetime | None = None) -> Mapping[str, Any] | None:
        with self._lock:
            active_reserved = sum(
                permit.amount
                for permit in self._permits.values()
                if permit.status == PENDING_AUTHORIZATION
            )
            return {
                "confirmed_provider_attempts": self.snapshot.confirmed_provider_attempts,
                "confirmed_provider_http_attempts": self.snapshot.confirmed_provider_attempts,
                "actual_attempts": self.snapshot.confirmed_provider_attempts,
                "reserved_shared_usage": self.snapshot.reserved_shared_usage + active_reserved,
                "projected_non_retry_attempts": self.snapshot.projected_non_retry_attempts,
                "conservative_live_attempts": self.snapshot.conservative_live_attempts,
                "conservative_quota_attempts": self.snapshot.conservative_live_attempts,
                "live_client_invocations": self.snapshot.live_client_invocations,
                "http_responses_received": self.snapshot.http_responses_received,
                "provider_quota_unknown_attempts": self.snapshot.provider_quota_unknown_attempts,
                "provider_quota_consumption_unknown": self.snapshot.provider_quota_unknown_attempts,
                "simulated_transport_attempts": 0,
                "state_available": self.snapshot.state_available,
                "source": self.snapshot.source,
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
        key = str(authorization_key) if authorization_key else None
        if key is not None and len(key) > 240:
            raise ValueError("authorization_key must be at most 240 characters")
        effective_boundary = approved_quota_boundary(decision_kind, boundary)
        with self._lock:
            if key is not None:
                for existing_id, existing_permit in self._permits.items():
                    if existing_permit.authorization_key != key:
                        continue
                    same_contract = (
                        existing_permit.scope == str(reservation_scope)
                        and existing_permit.amount == amount
                        and existing_permit.decision_kind == decision_kind
                    )
                    if not same_contract:
                        return {
                            "allowed": False,
                            "decision": "DUPLICATE_AUTHORIZATION_CONFLICT",
                            "proposed_attempts": amount,
                            "known_total": None,
                            "boundary": effective_boundary,
                            "reason": "authorization key was already used with a different permit contract",
                            "permit_id": None,
                        }
                    if existing_permit.status == PENDING_AUTHORIZATION:
                        return {
                            "allowed": True,
                            "decision": "DUPLICATE_AUTHORIZATION_REUSED",
                            "proposed_attempts": amount,
                            "known_total": None,
                            "boundary": effective_boundary,
                            "reason": "existing pending permit reused idempotently",
                            "permit_id": existing_id,
                        }
                    return {
                        "allowed": False,
                        "decision": "DUPLICATE_AUTHORIZATION_FINALIZED",
                        "proposed_attempts": amount,
                        "known_total": None,
                        "boundary": effective_boundary,
                        "reason": "authorization key already has a finalized permit",
                        "permit_id": None,
                    }
            if not self.snapshot.state_available:
                if decision_kind != "INITIAL":
                    return {
                        "allowed": False,
                        "decision": "RETRY_DENIED_QUOTA_STATE_UNAVAILABLE",
                        "proposed_attempts": amount,
                        "known_total": None,
                        "boundary": effective_boundary,
                        "reason": "safe shared-credential state is unavailable",
                        "permit_id": None,
                    }
                # An initial request remains eligible under the established
                # fail-open policy, but it still needs a durable lifecycle so
                # a later live result cannot silently escape accounting.
                permit_id = str(uuid.uuid4())
                self._permits[permit_id] = _Permit(
                    scope=reservation_scope,
                    amount=amount,
                    decision_kind=decision_kind,
                    boundary=effective_boundary,
                    authorization_key=key,
                )
                return {
                    "allowed": True,
                    "decision": "INITIAL_ALLOWED_WITHOUT_RETRY_STATE",
                    "proposed_attempts": amount,
                    "known_total": None,
                    "boundary": effective_boundary,
                    "reason": "shared-credential state is unavailable",
                    "permit_id": permit_id,
                }
            active = sum(
                permit.amount
                for permit in self._permits.values()
                if permit.scope == reservation_scope and permit.status == PENDING_AUTHORIZATION
            )
            known_total = self.snapshot.known_total + active
            projected = known_total + amount
            permit_id: str | None = None
            if projected > effective_boundary:
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
                    "reason": "quota boundary would be exceeded",
                    "permit_id": None,
                }
            permit_id = str(uuid.uuid4())
            self._permits[permit_id] = _Permit(
                scope=reservation_scope,
                amount=amount,
                decision_kind=decision_kind,
                boundary=effective_boundary,
                authorization_key=key,
            )
            return {
                "allowed": True,
                "decision": "INITIAL_ALLOWED" if decision_kind == "INITIAL" else "RETRY_ALLOWED",
                "proposed_attempts": amount,
                "known_total": known_total,
                "boundary": effective_boundary,
                "reason": "within atomic quota headroom",
                "permit_id": permit_id,
            }

    def _finalize(
        self,
        *,
        permit_id: Any,
        status: str,
        live_client_invocation: bool = False,
    ) -> Mapping[str, Any]:
        if permit_id is None:
            return {"applied": False, "status": status, "permit_id": None}
        with self._lock:
            key = str(permit_id)
            permit = self._permits.get(key)
            if permit is None:
                return {"applied": False, "status": "UNKNOWN_PERMIT", "permit_id": key}
            if permit.status != PENDING_AUTHORIZATION:
                return {"applied": False, "status": permit.status, "permit_id": key}
            if status == CONSERVATIVE_LIVE_ATTEMPT:
                self.snapshot = UsageSnapshot(
                    confirmed_provider_attempts=self.snapshot.confirmed_provider_attempts,
                    reserved_shared_usage=self.snapshot.reserved_shared_usage,
                    projected_non_retry_attempts=self.snapshot.projected_non_retry_attempts,
                    conservative_live_attempts=self.snapshot.conservative_live_attempts + permit.amount,
                    live_client_invocations=self.snapshot.live_client_invocations + permit.amount,
                    http_responses_received=self.snapshot.http_responses_received,
                    provider_quota_unknown_attempts=self.snapshot.provider_quota_unknown_attempts + permit.amount,
                    state_available=self.snapshot.state_available,
                    source=self.snapshot.source,
                )
            elif status == HTTP_PROVIDER_ATTEMPT_CONFIRMED:
                self.snapshot = UsageSnapshot(
                    confirmed_provider_attempts=self.snapshot.confirmed_provider_attempts + permit.amount,
                    reserved_shared_usage=self.snapshot.reserved_shared_usage,
                    projected_non_retry_attempts=self.snapshot.projected_non_retry_attempts,
                    conservative_live_attempts=self.snapshot.conservative_live_attempts,
                    live_client_invocations=self.snapshot.live_client_invocations + permit.amount,
                    http_responses_received=self.snapshot.http_responses_received + permit.amount,
                    provider_quota_unknown_attempts=self.snapshot.provider_quota_unknown_attempts,
                    state_available=self.snapshot.state_available,
                    source=self.snapshot.source,
                )
            elif status == RELEASED_NO_NETWORK and live_client_invocation:
                self.snapshot = UsageSnapshot(
                    confirmed_provider_attempts=self.snapshot.confirmed_provider_attempts,
                    reserved_shared_usage=self.snapshot.reserved_shared_usage,
                    projected_non_retry_attempts=self.snapshot.projected_non_retry_attempts,
                    conservative_live_attempts=self.snapshot.conservative_live_attempts,
                    live_client_invocations=self.snapshot.live_client_invocations + permit.amount,
                    http_responses_received=self.snapshot.http_responses_received,
                    provider_quota_unknown_attempts=self.snapshot.provider_quota_unknown_attempts,
                    state_available=self.snapshot.state_available,
                    source=self.snapshot.source,
                )
            permit.status = status
            return {"applied": True, "status": status, "permit_id": key, "amount": permit.amount}

    def finalize_quota_permit_no_network(
        self,
        *,
        permit_id: Any,
        at_utc: datetime | None = None,
        live_client_invocation: bool = False,
    ) -> Mapping[str, Any]:
        return self._finalize(
            permit_id=permit_id,
            status=RELEASED_NO_NETWORK,
            live_client_invocation=live_client_invocation,
        )

    def finalize_quota_permit_conservative(
        self,
        *,
        permit_id: Any,
        at_utc: datetime | None = None,
        live_client_invocation: bool = True,
    ) -> Mapping[str, Any]:
        return self._finalize(
            permit_id=permit_id,
            status=CONSERVATIVE_LIVE_ATTEMPT,
            live_client_invocation=live_client_invocation,
        )

    def finalize_quota_permit_http_response(
        self,
        *,
        permit_id: Any,
        at_utc: datetime | None = None,
        live_client_invocation: bool = True,
    ) -> Mapping[str, Any]:
        return self._finalize(
            permit_id=permit_id,
            status=HTTP_PROVIDER_ATTEMPT_CONFIRMED,
            live_client_invocation=live_client_invocation,
        )

    def release_quota_attempt(self, *, permit_id: Any) -> None:
        self.finalize_quota_permit_no_network(permit_id=permit_id)

    authorize_attempt = reserve_quota_attempt

    def add_attempt(self, count: int = 1) -> None:
        """Compatibility helper for explicit injected provider usage only."""

        with self._lock:
            self.snapshot = UsageSnapshot(
                confirmed_provider_attempts=self.snapshot.confirmed_provider_attempts + int(count),
                reserved_shared_usage=self.snapshot.reserved_shared_usage,
                projected_non_retry_attempts=self.snapshot.projected_non_retry_attempts,
                conservative_live_attempts=self.snapshot.conservative_live_attempts,
                live_client_invocations=self.snapshot.live_client_invocations,
                http_responses_received=self.snapshot.http_responses_received,
                provider_quota_unknown_attempts=self.snapshot.provider_quota_unknown_attempts,
                state_available=self.snapshot.state_available,
                source=self.snapshot.source,
            )


class QuotaGuard:
    """Separate atomic initial-request and automatic-retry decisions."""

    def __init__(
        self,
        usage_provider: UsageProvider,
        *,
        retry_boundary: int = AUTOMATIC_RETRY_PROTECTION_BOUNDARY,
        normal_maximum: int = NORMAL_OPERATIONAL_MAXIMUM,
        provider_ceiling: int = ABSOLUTE_PROVIDER_CEILING,
        reservation_scope: str = "LIVE_PROVIDER",
    ) -> None:
        self.usage_provider = usage_provider
        self.retry_boundary = approved_quota_boundary("RETRY", retry_boundary)
        self.normal_maximum = approved_quota_boundary("INITIAL", normal_maximum)
        # The provider ceiling is a fixed safety invariant.  A custom lower
        # ceiling remains useful for tests or a stricter operator policy, but
        # no adapter may raise the approved absolute cap.
        self.provider_ceiling = min(int(provider_ceiling), ABSOLUTE_PROVIDER_CEILING)
        if self.provider_ceiling <= 0:
            raise ValueError("provider_ceiling must be positive")
        self.reservation_scope = str(reservation_scope)

    def _snapshot(self, at_utc: datetime | None) -> UsageSnapshot | None:
        value = self.usage_provider.current_month_usage(at_utc=at_utc)
        if value is None:
            return None
        # Do not coerce an unavailable provider-wide baseline into numeric
        # zero before the fail-closed decision is made.
        if not bool(value.get("state_available", True)):
            return None
        snapshot = UsageSnapshot.from_mapping(value)
        if min(
            snapshot.confirmed_provider_attempts,
            snapshot.reserved_shared_usage,
            snapshot.projected_non_retry_attempts,
            snapshot.conservative_live_attempts,
            snapshot.live_client_invocations,
            snapshot.http_responses_received,
            snapshot.provider_quota_unknown_attempts,
        ) < 0:
            return None
        return snapshot

    def _atomic_or_snapshot(
        self,
        *,
        decision_kind: str,
        boundary: int,
        at_utc: datetime | None,
        proposed_attempts: int,
        authorization_key: str | None,
    ) -> QuotaDecision:
        reserve = getattr(self.usage_provider, "reserve_quota_attempt", None)
        if callable(reserve):
            # Keep the atomic backend contract safe even if a future adapter
            # is configured with a boundary above the absolute provider cap.
            effective_boundary = min(
                approved_quota_boundary(decision_kind, boundary),
                self.provider_ceiling,
            )
            result = reserve(
                decision_kind=decision_kind,
                boundary=effective_boundary,
                proposed_attempts=int(proposed_attempts),
                at_utc=at_utc,
                reservation_scope=self.reservation_scope,
                authorization_key=authorization_key,
            )
            if isinstance(result, QuotaDecision):
                return result
            return QuotaDecision(
                allowed=bool(result.get("allowed", False)),
                decision=str(result.get("decision", "QUOTA_DENIED")),
                proposed_attempts=int(result.get("proposed_attempts", proposed_attempts)),
                known_total=(
                    int(result["known_total"])
                    if result.get("known_total") is not None
                    else None
                ),
                boundary=int(result.get("boundary", effective_boundary)),
                reason=str(result.get("reason", "atomic quota decision")),
                permit_id=(str(result["permit_id"]) if result.get("permit_id") is not None else None),
            )

        snapshot = self._snapshot(at_utc)
        if decision_kind == "INITIAL" and snapshot is None:
            return QuotaDecision(
                allowed=True,
                decision="INITIAL_ALLOWED_WITHOUT_RETRY_STATE",
                proposed_attempts=proposed_attempts,
                known_total=None,
                boundary=approved_quota_boundary("INITIAL", self.normal_maximum),
                reason="initial scheduled request is not silently converted into a retry",
            )
        if decision_kind == "RETRY" and snapshot is None:
            return QuotaDecision(
                allowed=False,
                decision="RETRY_DENIED_QUOTA_STATE_UNAVAILABLE",
                proposed_attempts=proposed_attempts,
                known_total=None,
                boundary=approved_quota_boundary("RETRY", self.retry_boundary),
                reason="safe shared-credential state is unavailable",
            )
        assert snapshot is not None
        projected = snapshot.known_total + int(proposed_attempts)
        effective_boundary = min(
            approved_quota_boundary(decision_kind, boundary),
            self.provider_ceiling,
        )
        if projected > self.provider_ceiling:
            return QuotaDecision(
                False,
                "INITIAL_DENIED_PROVIDER_CEILING" if decision_kind == "INITIAL" else "RETRY_DENIED_PROVIDER_CEILING",
                proposed_attempts,
                snapshot.known_total,
                self.provider_ceiling,
                "provider ceiling would be exceeded",
            )
        if projected > effective_boundary:
            return QuotaDecision(
                False,
                "INITIAL_DENIED_NORMAL_OPERATIONAL_MAXIMUM" if decision_kind == "INITIAL" else "RETRY_DENIED_PROTECTION_BOUNDARY",
                proposed_attempts,
                snapshot.known_total,
                effective_boundary,
                "quota boundary would be exceeded",
            )
        return QuotaDecision(
            True,
            "INITIAL_ALLOWED" if decision_kind == "INITIAL" else "RETRY_ALLOWED",
            proposed_attempts,
            snapshot.known_total,
            effective_boundary,
            "within quota headroom",
        )

    def allow_initial(
        self,
        *,
        at_utc: datetime | None = None,
        proposed_attempts: int = 1,
        authorization_key: str | None = None,
    ) -> QuotaDecision:
        """Atomically authorize one scheduled initial attempt."""

        return self._atomic_or_snapshot(
            decision_kind="INITIAL",
            boundary=self.normal_maximum,
            at_utc=at_utc,
            proposed_attempts=proposed_attempts,
            authorization_key=authorization_key,
        )

    def allow_retry(
        self,
        *,
        at_utc: datetime | None = None,
        proposed_attempts: int = 1,
        authorization_key: str | None = None,
    ) -> QuotaDecision:
        """Atomically authorize one automatic retry, failing closed without state."""

        return self._atomic_or_snapshot(
            decision_kind="RETRY",
            boundary=self.retry_boundary,
            at_utc=at_utc,
            proposed_attempts=proposed_attempts,
            authorization_key=authorization_key,
        )

    def release(self, decision: QuotaDecision | None) -> None:
        self.finalize_no_network(decision)

    def _finalize(
        self,
        decision: QuotaDecision | None,
        method_name: str,
        *,
        live_client_invocation: bool = False,
    ) -> Mapping[str, Any]:
        if decision is None or decision.permit_id is None:
            return {"applied": False, "status": "NO_PERMIT", "permit_id": None}
        finalize = getattr(self.usage_provider, method_name, None)
        if callable(finalize):
            return finalize(
                permit_id=decision.permit_id,
                live_client_invocation=live_client_invocation,
            )
        if method_name == "finalize_quota_permit_no_network":
            release = getattr(self.usage_provider, "release_quota_attempt", None)
            if callable(release):
                release(permit_id=decision.permit_id)
                return {"applied": True, "status": RELEASED_NO_NETWORK, "permit_id": decision.permit_id}
        # A provider that cannot finalize a live permit must not be treated as
        # released.  Its conservative reservation remains the safe fallback.
        return {"applied": False, "status": "FINALIZATION_UNAVAILABLE", "permit_id": decision.permit_id}

    def finalize_no_network(
        self,
        decision: QuotaDecision | None,
        *,
        live_client_invocation: bool = False,
    ) -> Mapping[str, Any]:
        return self._finalize(
            decision,
            "finalize_quota_permit_no_network",
            live_client_invocation=live_client_invocation,
        )

    def finalize_conservative(
        self,
        decision: QuotaDecision | None,
        *,
        live_client_invocation: bool = True,
    ) -> Mapping[str, Any]:
        return self._finalize(
            decision,
            "finalize_quota_permit_conservative",
            live_client_invocation=live_client_invocation,
        )

    def finalize_http_response(
        self,
        decision: QuotaDecision | None,
        *,
        live_client_invocation: bool = True,
    ) -> Mapping[str, Any]:
        return self._finalize(
            decision,
            "finalize_quota_permit_http_response",
            live_client_invocation=live_client_invocation,
        )

    retry_allowed = allow_retry
