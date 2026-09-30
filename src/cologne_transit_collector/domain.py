"""Small domain types and immutable approved Stage 13 policy values."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional


PANEL_VERSION = "TIERED50_PANEL_V1"
HEARTBEAT_MINUTES = 5
HEARTBEAT_SECONDS = HEARTBEAT_MINUTES * 60
MAXIMUM_CONCURRENCY = 2
MAX_ATTEMPTS = 2
MAX_AUTOMATIC_RETRIES = 1
REQUEST_TIMEOUT_SECONDS = 30
AUTOMATIC_RETRY_PROTECTION_BOUNDARY = 207_500
NORMAL_OPERATIONAL_MAXIMUM = 210_000
ABSOLUTE_PROVIDER_CEILING = 250_000
SKIPPED_HEARTBEAT_BUDGET = "SKIPPED_HEARTBEAT_BUDGET"
RETRY_SKIPPED_HEARTBEAT_BUDGET = "RETRY_SKIPPED_HEARTBEAT_BUDGET"
RETRY_SKIPPED_QUOTA = "RETRY_SKIPPED_QUOTA"
SKIPPED_QUOTA = "SKIPPED_QUOTA"
INTERRUPTED_SHUTDOWN = "INTERRUPTED_SHUTDOWN"

TIER_CADENCE_MINUTES: Mapping[str, int] = {"A": 5, "B": 15, "C": 30}
TIER_NUMBER_OF_RESULTS: Mapping[str, int] = {"A": 20, "B": 30, "C": 30}
TIER_COUNTS: Mapping[str, int] = {"A": 8, "B": 20, "C": 22}
PHASE_CAPACITIES: Mapping[str, tuple[int, ...]] = {
    "A": (8,),
    "B": (7, 7, 6),
    "C": (4, 3, 4, 3, 4, 4),
}
APPROVED_SUPER_CYCLE_COUNTS = (19, 18, 18, 18, 19, 18)
APPROVED_TRANSIENT_HTTP_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
APPROVED_TRANSIENT_TRANSPORT_CATEGORIES = frozenset(
    {
        "CONNECTION_REFUSED",
        "NETWORK_UNREACHABLE",
        "PROXY_OR_ENVIRONMENT_FAILURE",
        "TIMEOUT",
        "TLS_FAILURE",
        "OTHER_NETWORK_FAILURE",
    }
)


class SamplingEra(str, Enum):
    """Sampling-policy eras used for lineage, not for activation state."""

    LEGACY7 = "Legacy7"
    TIERED50 = "Tiered50"


@dataclass(frozen=True)
class Target:
    """One immutable approved runtime target from the versioned manifest."""

    panel_version: str
    source_parent_station_id: str
    station_name: str
    tier: str
    cadence_minutes: int
    number_of_results: int
    phase_index: int
    phase_count: int
    continuity_classification: str
    sampling_era: SamplingEra = SamplingEra.TIERED50
    production_sampling_target_id: Optional[str] = None
    phase_capacity: Optional[int] = None

    @property
    def is_legacy7_member(self) -> bool:
        return self.continuity_classification == "Legacy7 member"

    @property
    def development_identity(self) -> tuple[str, str]:
        """Stable local identity used before production IDs exist."""

        return self.panel_version, self.source_parent_station_id

    def claim_key(self, logical_due_bucket: int) -> tuple[str, str, int]:
        """Development claim key; production uses SamplingTargetId instead."""

        return self.panel_version, self.source_parent_station_id, int(logical_due_bucket)


@dataclass(frozen=True)
class LogicalBucket:
    """Deterministic UTC five-minute logical bucket."""

    index: int
    start_utc: datetime
    end_utc: datetime

    @property
    def identity(self) -> str:
        return self.start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class HttpResponse:
    status_code: Optional[int]
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)
    elapsed_seconds: Optional[float] = None

    @property
    def retry_after(self) -> Optional[str]:
        for key, value in self.headers.items():
            if key.lower() == "retry-after":
                return value
        return None


@dataclass(frozen=True)
class TransportFailure:
    category: str
    detail: str = ""


@dataclass(frozen=True)
class TargetOutcome:
    target_id: str
    station_name: str
    tier: str
    phase_index: int
    logical_due_bucket: int
    status: str
    http_status: Optional[int] = None
    http_attempts: int = 0
    stop_event_result_count: Optional[int] = None
    parser_outcome: Optional[str] = None
    persistence_outcome: Optional[str] = None
    duration_seconds: Optional[float] = None
    skip_reason: Optional[str] = None
    error_category: Optional[str] = None
    retry_decision: Optional[str] = None
    retry_outcome: Optional[str] = None
    claim_won: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "station_name": self.station_name,
            "tier": self.tier,
            "phase_index": self.phase_index,
            "logical_due_bucket": self.logical_due_bucket,
            "status": self.status,
            "http_status": self.http_status,
            "http_attempts": self.http_attempts,
            "stop_event_result_count": self.stop_event_result_count,
            "parser_outcome": self.parser_outcome,
            "persistence_outcome": self.persistence_outcome,
            "duration_seconds": self.duration_seconds,
            "skip_reason": self.skip_reason,
            "error_category": self.error_category,
            "retry_decision": self.retry_decision,
            "retry_outcome": self.retry_outcome,
            "claim_won": self.claim_won,
        }


@dataclass(frozen=True)
class Plan:
    bucket: LogicalBucket
    due_targets: tuple[Target, ...]
    distribution_index: int

    @property
    def due_count(self) -> int:
        return len(self.due_targets)


@dataclass(frozen=True)
class CollectorRunResult:
    run_id: str
    mode: str
    bucket: LogicalBucket
    due_count: int
    outcomes: tuple[TargetOutcome, ...]
    status: str
    max_observed_concurrency: int = 0
    live_network_used: bool = False

    @property
    def http_attempt_count(self) -> int:
        return sum(item.http_attempts for item in self.outcomes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "logical_due_bucket": self.bucket.index,
            "logical_due_bucket_identity": self.bucket.identity,
            "due_count": self.due_count,
            "status": self.status,
            "max_observed_concurrency": self.max_observed_concurrency,
            "http_attempt_count": self.http_attempt_count,
            "live_network_used": self.live_network_used,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
        }
