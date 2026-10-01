"""Platform-neutral non-secret runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .domain import (
    HEARTBEAT_SECONDS,
    LIVE_RESEARCH_COLLECTION_CONTEXT,
    MAX_ATTEMPTS,
    MAXIMUM_CONCURRENCY,
    PersistenceProfile,
    REQUEST_TIMEOUT_SECONDS,
)


DEFAULT_ENDPOINT = "https://mdd.gorheinland.com/delfi"
DEFAULT_REQUESTOR_REF = "CologneTransitIntelligence"
DEFAULT_SAFETY_MARGIN_SECONDS = 5.0
SAFETY_MARGIN_DEFAULT_STATUS = "DEVELOPMENT_NON_PRODUCTION_DEFAULT"
LIVE_RESEARCH_ROOT_ENV = "COLLECTOR_LIVE_RESEARCH_ROOT"
LIVE_RESEARCH_DB_ENV = "COLLECTOR_LIVE_RESEARCH_DB"
DEFAULT_LIVE_RESEARCH_ROOT = Path.home() / "cologne-transit-data" / "live-research"
LIVE_RESEARCH_DATABASE_NAME = "collector.sqlite3"
LIVE_RESEARCH_BACKUP_DIRECTORY = "backups"
LIVE_RESEARCH_RAW_DIRECTORY = "raw-evidence"


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
    persistence_profile: PersistenceProfile = PersistenceProfile.DEVELOPMENT
    backup_path: Path | None = None
    raw_evidence_path: Path | None = None
    collection_context: str = "OFFLINE_DEVELOPMENT"

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

    @classmethod
    def for_live_research(
        cls,
        repository_root: str | Path,
        *,
        database_path: str | Path | None = None,
        data_root: str | Path | None = None,
        safety_margin_seconds: float | None = None,
    ) -> "CollectorSettings":
        """Build an explicit live-research profile.

        There is deliberately no implicit fallback to ``.local`` or to the
        development ``COLLECTOR_STATE_PATH`` value.  Callers must provide a
        database or an approved live-research root, either directly or via the
        dedicated environment variables.
        """

        root = Path(repository_root).resolve()
        configured_db = database_path or os.environ.get(LIVE_RESEARCH_DB_ENV)
        configured_root = data_root or os.environ.get(LIVE_RESEARCH_ROOT_ENV)
        if configured_db is None and configured_root is None:
            raise ValueError(
                "live-research requires an explicit database path or "
                f"{LIVE_RESEARCH_ROOT_ENV}"
            )
        if configured_db is not None:
            state = Path(configured_db).expanduser()
            if not state.is_absolute():
                state = root / state
            state = state.resolve()
            live_root = (
                Path(configured_root).expanduser().resolve()
                if configured_root is not None
                else state.parent
            )
        else:
            live_root = Path(configured_root).expanduser().resolve()  # type: ignore[arg-type]
            state = live_root / LIVE_RESEARCH_DATABASE_NAME

        margin_value = (
            float(safety_margin_seconds)
            if safety_margin_seconds is not None
            else float(
                os.environ.get(
                    "COLLECTOR_HEARTBEAT_SAFETY_MARGIN_SECONDS",
                    DEFAULT_SAFETY_MARGIN_SECONDS,
                )
            )
        )
        return cls(
            repository_root=root,
            manifest_path=root / "config" / "tiered50_runtime_manifest.json",
            stage13_evidence_path=root / "docs" / "stages" / "13-tiered-collector-readiness-evidence.json",
            state_path=state,
            endpoint=os.environ.get("COLLECTOR_MDD_ENDPOINT", DEFAULT_ENDPOINT),
            safety_margin_seconds=margin_value,
            persistence_profile=PersistenceProfile.LIVE_RESEARCH,
            backup_path=live_root / LIVE_RESEARCH_BACKUP_DIRECTORY,
            raw_evidence_path=live_root / LIVE_RESEARCH_RAW_DIRECTORY,
            collection_context=LIVE_RESEARCH_COLLECTION_CONTEXT,
        )
