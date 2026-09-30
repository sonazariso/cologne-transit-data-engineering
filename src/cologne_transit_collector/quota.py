"""Shared-credential quota policy, kept independent from transport and storage."""

from __future__ import annotations

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

    actual_attempts: int = 0
    reserved_shared_usage: int = 0
    projected_non_retry_attempts: int = 0
    state_available: bool = True
    source: str = "injected"

    @property
    def known_total(self) -> int:
        return self.actual_attempts + self.reserved_shared_usage + self.projected_non_retry_attempts

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "UsageSnapshot":
        return cls(
            actual_attempts=int(value.get("actual_attempts", value.get("actual_http_attempts", 0))),
            reserved_shared_usage=int(value.get("reserved_shared_usage", value.get("reserved_attempts", 0))),
            projected_non_retry_attempts=int(value.get("projected_non_retry_attempts", value.get("projected_attempts", 0))),
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "decision": self.decision,
            "proposed_attempts": self.proposed_attempts,
            "known_total": self.known_total,
            "boundary": self.boundary,
            "reason": self.reason,
        }


class InMemoryUsageProvider:
    """Explicitly injected development/shared-usage state for simulations."""

    def __init__(self, snapshot: UsageSnapshot | None = None) -> None:
        self.snapshot = snapshot or UsageSnapshot()

    def current_month_usage(self, *, at_utc: datetime | None = None) -> Mapping[str, Any] | None:
        return {
            "actual_attempts": self.snapshot.actual_attempts,
            "reserved_shared_usage": self.snapshot.reserved_shared_usage,
            "projected_non_retry_attempts": self.snapshot.projected_non_retry_attempts,
            "state_available": self.snapshot.state_available,
            "source": self.snapshot.source,
        }

    def add_attempt(self, count: int = 1) -> None:
        self.snapshot = UsageSnapshot(
            actual_attempts=self.snapshot.actual_attempts + int(count),
            reserved_shared_usage=self.snapshot.reserved_shared_usage,
            projected_non_retry_attempts=self.snapshot.projected_non_retry_attempts,
            state_available=self.snapshot.state_available,
            source=self.snapshot.source,
        )


class QuotaGuard:
    """Separate initial-request and automatic-retry decisions."""

    def __init__(
        self,
        usage_provider: UsageProvider,
        *,
        retry_boundary: int = AUTOMATIC_RETRY_PROTECTION_BOUNDARY,
        normal_maximum: int = NORMAL_OPERATIONAL_MAXIMUM,
        provider_ceiling: int = ABSOLUTE_PROVIDER_CEILING,
    ) -> None:
        self.usage_provider = usage_provider
        self.retry_boundary = int(retry_boundary)
        self.normal_maximum = int(normal_maximum)
        self.provider_ceiling = int(provider_ceiling)

    def _snapshot(self, at_utc: datetime | None) -> UsageSnapshot | None:
        value = self.usage_provider.current_month_usage(at_utc=at_utc)
        if value is None:
            return None
        snapshot = UsageSnapshot.from_mapping(value)
        if not snapshot.state_available:
            return None
        if min(snapshot.actual_attempts, snapshot.reserved_shared_usage, snapshot.projected_non_retry_attempts) < 0:
            return None
        return snapshot

    def allow_initial(self, *, at_utc: datetime | None = None, proposed_attempts: int = 1) -> QuotaDecision:
        """Initial scheduled work uses normal operating headroom, not retry headroom."""

        snapshot = self._snapshot(at_utc)
        if snapshot is None:
            return QuotaDecision(
                allowed=True,
                decision="INITIAL_ALLOWED_WITHOUT_RETRY_STATE",
                proposed_attempts=proposed_attempts,
                known_total=None,
                boundary=self.normal_maximum,
                reason="initial scheduled request is not silently converted into a retry",
            )
        projected = snapshot.known_total + int(proposed_attempts)
        if projected > self.provider_ceiling:
            return QuotaDecision(False, "INITIAL_DENIED_PROVIDER_CEILING", proposed_attempts, snapshot.known_total, self.provider_ceiling, "provider ceiling would be exceeded")
        if projected > self.normal_maximum:
            return QuotaDecision(False, "INITIAL_DENIED_NORMAL_OPERATIONAL_MAXIMUM", proposed_attempts, snapshot.known_total, self.normal_maximum, "normal operational maximum would be exceeded")
        return QuotaDecision(True, "INITIAL_ALLOWED", proposed_attempts, snapshot.known_total, self.normal_maximum, "within normal scheduled headroom")

    def allow_retry(self, *, at_utc: datetime | None = None, proposed_attempts: int = 1) -> QuotaDecision:
        """Automatic retries fail closed when shared-credential state is unavailable."""

        snapshot = self._snapshot(at_utc)
        if snapshot is None:
            return QuotaDecision(False, "RETRY_DENIED_QUOTA_STATE_UNAVAILABLE", proposed_attempts, None, self.retry_boundary, "safe shared-credential state is unavailable")
        projected = snapshot.known_total + int(proposed_attempts)
        if projected > self.retry_boundary:
            return QuotaDecision(False, "RETRY_DENIED_PROTECTION_BOUNDARY", proposed_attempts, snapshot.known_total, self.retry_boundary, "automatic-retry protection boundary would be exceeded")
        if projected > self.provider_ceiling:
            return QuotaDecision(False, "RETRY_DENIED_PROVIDER_CEILING", proposed_attempts, snapshot.known_total, self.provider_ceiling, "provider ceiling would be exceeded")
        return QuotaDecision(True, "RETRY_ALLOWED", proposed_attempts, snapshot.known_total, self.retry_boundary, "within automatic-retry protection boundary")

    retry_allowed = allow_retry
