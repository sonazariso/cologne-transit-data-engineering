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
)
from .ports import UsageProvider


@dataclass(frozen=True)
class UsageSnapshot:
    """Known shared-credential usage for the current calendar month."""

    confirmed_provider_attempts: int = 0
    reserved_shared_usage: int = 0
    projected_non_retry_attempts: int = 0
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

    @property
    def known_total(self) -> int:
        return (
            self.confirmed_provider_attempts
            + self.reserved_shared_usage
            + self.projected_non_retry_attempts
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "UsageSnapshot":
        return cls(
            confirmed_provider_attempts=int(
                value.get(
                    "confirmed_provider_attempts",
                    value.get("actual_attempts", value.get("actual_http_attempts", 0)),
                )
            ),
            reserved_shared_usage=int(value.get("reserved_shared_usage", value.get("reserved_attempts", 0))),
            projected_non_retry_attempts=int(
                value.get("projected_non_retry_attempts", value.get("projected_attempts", 0))
            ),
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


class InMemoryUsageProvider:
    """Explicitly injected usage state with an atomic permit implementation."""

    def __init__(self, snapshot: UsageSnapshot | None = None) -> None:
        self.snapshot = snapshot or UsageSnapshot()
        self._lock = threading.Lock()
        self._permits: dict[str, tuple[str, int]] = {}

    def current_month_usage(self, *, at_utc: datetime | None = None) -> Mapping[str, Any] | None:
        with self._lock:
            active_reserved = sum(amount for _scope, amount in self._permits.values())
            return {
                "confirmed_provider_attempts": self.snapshot.confirmed_provider_attempts,
                "actual_attempts": self.snapshot.confirmed_provider_attempts,
                "reserved_shared_usage": self.snapshot.reserved_shared_usage + active_reserved,
                "projected_non_retry_attempts": self.snapshot.projected_non_retry_attempts,
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
    ) -> Mapping[str, Any]:
        amount = int(proposed_attempts)
        with self._lock:
            if not self.snapshot.state_available:
                return {
                    "allowed": decision_kind == "INITIAL",
                    "decision": (
                        "INITIAL_ALLOWED_WITHOUT_RETRY_STATE"
                        if decision_kind == "INITIAL"
                        else "RETRY_DENIED_QUOTA_STATE_UNAVAILABLE"
                    ),
                    "proposed_attempts": amount,
                    "known_total": None,
                    "boundary": int(boundary),
                    "reason": "shared-credential state is unavailable",
                    "permit_id": None,
                }
            active = sum(
                permit_amount
                for scope, permit_amount in self._permits.values()
                if scope == reservation_scope
            )
            known_total = self.snapshot.known_total + active
            projected = known_total + amount
            if projected > int(boundary):
                return {
                    "allowed": False,
                    "decision": (
                        "INITIAL_DENIED_NORMAL_OPERATIONAL_MAXIMUM"
                        if decision_kind == "INITIAL"
                        else "RETRY_DENIED_PROTECTION_BOUNDARY"
                    ),
                    "proposed_attempts": amount,
                    "known_total": known_total,
                    "boundary": int(boundary),
                    "reason": "quota boundary would be exceeded",
                    "permit_id": None,
                }
            permit_id = str(uuid.uuid4())
            self._permits[permit_id] = (reservation_scope, amount)
            return {
                "allowed": True,
                "decision": "INITIAL_ALLOWED" if decision_kind == "INITIAL" else "RETRY_ALLOWED",
                "proposed_attempts": amount,
                "known_total": known_total,
                "boundary": int(boundary),
                "reason": "within atomic quota headroom",
                "permit_id": permit_id,
            }

    def release_quota_attempt(self, *, permit_id: Any) -> None:
        if permit_id is None:
            return
        with self._lock:
            self._permits.pop(str(permit_id), None)

    authorize_attempt = reserve_quota_attempt

    def add_attempt(self, count: int = 1) -> None:
        """Compatibility helper for explicit injected provider usage only."""

        with self._lock:
            self.snapshot = UsageSnapshot(
                confirmed_provider_attempts=self.snapshot.confirmed_provider_attempts + int(count),
                reserved_shared_usage=self.snapshot.reserved_shared_usage,
                projected_non_retry_attempts=self.snapshot.projected_non_retry_attempts,
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
        self.retry_boundary = int(retry_boundary)
        self.normal_maximum = int(normal_maximum)
        self.provider_ceiling = int(provider_ceiling)
        self.reservation_scope = str(reservation_scope)

    def _snapshot(self, at_utc: datetime | None) -> UsageSnapshot | None:
        value = self.usage_provider.current_month_usage(at_utc=at_utc)
        if value is None:
            return None
        snapshot = UsageSnapshot.from_mapping(value)
        if not snapshot.state_available:
            return None
        if min(
            snapshot.confirmed_provider_attempts,
            snapshot.reserved_shared_usage,
            snapshot.projected_non_retry_attempts,
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
    ) -> QuotaDecision:
        reserve = getattr(self.usage_provider, "reserve_quota_attempt", None)
        if callable(reserve):
            # Keep the atomic backend contract safe even if a future adapter
            # is configured with a boundary above the absolute provider cap.
            effective_boundary = min(int(boundary), self.provider_ceiling)
            result = reserve(
                decision_kind=decision_kind,
                boundary=effective_boundary,
                proposed_attempts=int(proposed_attempts),
                at_utc=at_utc,
                reservation_scope=self.reservation_scope,
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
                boundary=self.normal_maximum,
                reason="initial scheduled request is not silently converted into a retry",
            )
        if decision_kind == "RETRY" and snapshot is None:
            return QuotaDecision(
                allowed=False,
                decision="RETRY_DENIED_QUOTA_STATE_UNAVAILABLE",
                proposed_attempts=proposed_attempts,
                known_total=None,
                boundary=self.retry_boundary,
                reason="safe shared-credential state is unavailable",
            )
        assert snapshot is not None
        projected = snapshot.known_total + int(proposed_attempts)
        if projected > self.provider_ceiling:
            return QuotaDecision(
                False,
                "INITIAL_DENIED_PROVIDER_CEILING" if decision_kind == "INITIAL" else "RETRY_DENIED_PROVIDER_CEILING",
                proposed_attempts,
                snapshot.known_total,
                self.provider_ceiling,
                "provider ceiling would be exceeded",
            )
        if projected > boundary:
            return QuotaDecision(
                False,
                "INITIAL_DENIED_NORMAL_OPERATIONAL_MAXIMUM" if decision_kind == "INITIAL" else "RETRY_DENIED_PROTECTION_BOUNDARY",
                proposed_attempts,
                snapshot.known_total,
                boundary,
                "quota boundary would be exceeded",
            )
        return QuotaDecision(
            True,
            "INITIAL_ALLOWED" if decision_kind == "INITIAL" else "RETRY_ALLOWED",
            proposed_attempts,
            snapshot.known_total,
            boundary,
            "within quota headroom",
        )

    def allow_initial(self, *, at_utc: datetime | None = None, proposed_attempts: int = 1) -> QuotaDecision:
        """Atomically authorize one scheduled initial attempt."""

        return self._atomic_or_snapshot(
            decision_kind="INITIAL",
            boundary=self.normal_maximum,
            at_utc=at_utc,
            proposed_attempts=proposed_attempts,
        )

    def allow_retry(self, *, at_utc: datetime | None = None, proposed_attempts: int = 1) -> QuotaDecision:
        """Atomically authorize one automatic retry, failing closed without state."""

        return self._atomic_or_snapshot(
            decision_kind="RETRY",
            boundary=self.retry_boundary,
            at_utc=at_utc,
            proposed_attempts=proposed_attempts,
        )

    def release(self, decision: QuotaDecision | None) -> None:
        if decision is None or decision.permit_id is None:
            return
        release = getattr(self.usage_provider, "release_quota_attempt", None)
        if callable(release):
            release(permit_id=decision.permit_id)

    retry_allowed = allow_retry
