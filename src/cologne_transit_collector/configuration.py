"""Platform-neutral non-secret runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .domain import HEARTBEAT_SECONDS, MAX_ATTEMPTS, MAXIMUM_CONCURRENCY, REQUEST_TIMEOUT_SECONDS


DEFAULT_ENDPOINT = "https://mdd.gorheinland.com/delfi"
DEFAULT_REQUESTOR_REF = "CologneTransitIntelligence"
DEFAULT_SAFETY_MARGIN_SECONDS = 5.0
SAFETY_MARGIN_DEFAULT_STATUS = "DEVELOPMENT_NON_PRODUCTION_DEFAULT"


@dataclass(frozen=True)
class CollectorSettings:
    repository_root: Path
    manifest_path: Path
    stage13_evidence_path: Path
    state_path: Path
    endpoint: str = DEFAULT_ENDPOINT
    requestor_ref: str = DEFAULT_REQUESTOR_REF
    heartbeat_seconds: int = HEARTBEAT_SECONDS
    safety_margin_seconds: float = DEFAULT_SAFETY_MARGIN_SECONDS
    request_timeout_seconds: int = REQUEST_TIMEOUT_SECONDS
    max_attempts: int = MAX_ATTEMPTS
    max_concurrency: int = MAXIMUM_CONCURRENCY

    def __post_init__(self) -> None:
        if self.max_concurrency != MAXIMUM_CONCURRENCY:
            raise ValueError("Stage 13 MaximumConcurrency is fixed at 2")
        if self.max_attempts != MAX_ATTEMPTS:
            raise ValueError("Stage 13 MaxAttempts is fixed at 2")
        if self.request_timeout_seconds != REQUEST_TIMEOUT_SECONDS:
            raise ValueError("Stage 13 request timeout is fixed at 30 seconds")
        if self.heartbeat_seconds != HEARTBEAT_SECONDS:
            raise ValueError("logical heartbeat duration is fixed at five minutes")
        if self.safety_margin_seconds < 0:
            raise ValueError("heartbeat safety margin cannot be negative")

    @classmethod
    def for_repository(
        cls,
        repository_root: str | Path,
        *,
        state_path: str | Path | None = None,
        safety_margin_seconds: float | None = None,
    ) -> "CollectorSettings":
        root = Path(repository_root).resolve()
        configured_state = state_path or os.environ.get("COLLECTOR_STATE_PATH")
        local_state = Path(configured_state) if configured_state else root / ".local" / "stage14" / "collector.sqlite3"
        if not local_state.is_absolute():
            local_state = root / local_state
        margin_value = (
            float(safety_margin_seconds)
            if safety_margin_seconds is not None
            else float(os.environ.get("COLLECTOR_HEARTBEAT_SAFETY_MARGIN_SECONDS", DEFAULT_SAFETY_MARGIN_SECONDS))
        )
        return cls(
            repository_root=root,
            manifest_path=root / "config" / "tiered50_runtime_manifest.json",
            stage13_evidence_path=root / "docs" / "stages" / "13-tiered-collector-readiness-evidence.json",
            state_path=local_state,
            endpoint=os.environ.get("COLLECTOR_MDD_ENDPOINT", DEFAULT_ENDPOINT),
            safety_margin_seconds=margin_value,
        )
