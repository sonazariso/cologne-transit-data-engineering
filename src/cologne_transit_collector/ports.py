"""Infrastructure ports used by the domain collector."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence

from .domain import HttpResponse, Target, TransportFailure


class MddClient(Protocol):
    """One request abstraction; implementations may be live or deterministic fake."""

    def fetch(
        self,
        target: Target,
        *,
        request_timestamp_utc: datetime,
        timeout_seconds: float,
        attempt_number: int,
    ) -> HttpResponse | TransportFailure:
        ...


class UsageProvider(Protocol):
    def current_month_usage(self, *, at_utc: datetime | None = None) -> Mapping[str, Any] | None:
        ...


class PersistencePort(Protocol):
    """Behavioral persistence contract for a future production adapter."""

    is_production_ready: bool

    def start_collector_run(
        self, *, run_id: str, panel_version: str, logical_due_bucket: int, mode: str, started_at: datetime
    ) -> None:
        ...

    def claim_target(self, *, run_id: str, target: Target, logical_due_bucket: int, claimed_at: datetime) -> Any:
        ...

    def start_target_audit(self, *, run_id: str, target: Target, logical_due_bucket: int, started_at: datetime) -> Any:
        ...

    def record_http_attempt(self, *, audit_id: Any, attempt_number: int, started_at: datetime, completed_at: datetime, response: HttpResponse | None = None, transport_failure: TransportFailure | None = None, retry_decision: str | None = None, retry_after_seconds: float | None = None) -> None:
        ...

    def persist_snapshot(self, *, audit_id: Any, target: Target, logical_due_bucket: int, parsed_snapshot: Mapping[str, Any], persisted_at: datetime) -> None:
        ...

    def complete_target_audit(self, *, audit_id: Any, outcome: Mapping[str, Any], completed_at: datetime) -> None:
        ...

    def complete_collector_run(self, *, run_id: str, status: str, completed_at: datetime, summary: Mapping[str, Any]) -> None:
        ...

    def fail_collector_run(self, *, run_id: str, completed_at: datetime, summary: Mapping[str, Any]) -> None:
        ...

    def record_run_skip(self, *, run_id: str, reason: str, completed_at: datetime) -> None:
        ...

    def record_attempt(self, **kwargs: Any) -> None:
        ...

    def persist_parsed_snapshot(self, **kwargs: Any) -> None:
        ...

    def record_quota_decision(self, **kwargs: Any) -> None:
        ...

    def list_stale_claims(self, *, now_utc: datetime) -> Sequence[Mapping[str, Any]]:
        ...

    def record_sampling_era_cutover(self, **kwargs: Any) -> None:
        ...

    def current_month_usage(self, *, at_utc: datetime | None = None) -> Mapping[str, Any] | None:
        ...
