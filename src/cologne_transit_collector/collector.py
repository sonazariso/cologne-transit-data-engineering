"""Single-heartbeat dispatcher with bounded execution and failure isolation."""

from __future__ import annotations

import email.utils
import logging
import os
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Sequence

from .domain import (
    APPROVED_TRANSIENT_HTTP_STATUS_CODES,
    APPROVED_TRANSIENT_TRANSPORT_CATEGORIES,
    HEARTBEAT_SECONDS,
    AUTHORIZATION_EXPIRED_BEFORE_NETWORK,
    COMPLETED_WITH_COVERAGE_GAP,
    INSUFFICIENT_BUDGET,
    IN_FLIGHT_OPERATION_OVERRUN,
    INTERRUPTED_SHUTDOWN,
    LATE_DISPATCH,
    LIVE_RESEARCH_COLLECTION_CONTEXT,
    LIVE_RESEARCH_TARGET_ID,
    MAC_SQL_CUTOVER_APPROVED_ENV,
    MAC_SQL_ENABLED_ENV,
    MAC_SQL_MODE_ENV,
    MAC_SQL_REQUIRED_RUNTIME_STATE,
    MAC_SQL_REQUIRED_TARGET_SCOPE,
    MAC_SQL_RUNTIME_STATE_ENV,
    MAC_SQL_TARGET_SCOPE_ENV,
    MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT,
    MAC_TIERED50_CUTOVER_APPROVED_ENV,
    MAC_TIERED50_ENABLED_ENV,
    MAC_TIERED50_REQUIRED_TARGET_SCOPE,
    MAC_TIERED50_REQUIRED_VALIDATION_POLICY,
    MAC_TIERED50_TARGET_SCOPE_ENV,
    MAC_TIERED50_VALIDATION_POLICY_ENV,
    SamplingEra,
    MAX_ATTEMPTS,
    MAXIMUM_CONCURRENCY,
    RETRY_SKIPPED_HEARTBEAT_BUDGET,
    RETRY_SKIPPED_QUOTA,
    REQUEST_TIMEOUT_SECONDS,
    SKIPPED_HEARTBEAT_BUDGET,
    SKIPPED_QUOTA,
    CollectorRunResult,
    HttpResponse,
    Plan,
    PersistenceProfile,
    Target,
    TargetOutcome,
    TransportFailure,
)
from .manifest import RuntimeManifest
from .ports import MddClient, PersistencePort
from .quota import (
    CONSERVATIVE_LIVE_ATTEMPT,
    HTTP_PROVIDER_ATTEMPT_CONFIRMED,
    RELEASED_NO_NETWORK,
    QuotaDecision,
    QuotaGuard,
)
from .schedule import bucket_for, plan_for
from .trias import TriasApplicationError, TriasParserError, parse_trias_response
from .transport import validate_api_key_format


LOGGER = logging.getLogger(__name__)
CLIENT_EXECUTION_FAILURE = "CLIENT_EXECUTION_FAILURE"


def _parse_utc_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _mdd_credential_status(client: object) -> str:
    """Return credential status without requiring production secrets in env."""

    status_method = getattr(client, "credential_status", None)
    if callable(status_method):
        return str(status_method())
    return validate_api_key_format(os.environ.get("MDD_API_KEY"))


def replace_run_maintenance(result: CollectorRunResult, maintenance: Mapping[str, Any]) -> CollectorRunResult:
    return replace(result, maintenance=dict(maintenance))


class LiveModeBlocked(RuntimeError):
    """Live activation remains intentionally unavailable in this iteration."""


class LiveResearchModeBlocked(RuntimeError):
    """The explicit single-station research mode failed closed."""


class HeartbeatBudget:
    """One immutable execution budget bounded by an absolute UTC bucket end.

    ``started_at_utc`` identifies the logical observation used to select the
    bucket.  ``current_at_utc`` is the actual wall-clock instant at which the
    budget is established.  Production callers must provide the latter after
    preflight; using the logical bucket time again would give startup and SQL
    initialization time back to the heartbeat.

    The monotonic deadline is established once.  A wall-clock check is also
    applied on every read so a clock crossing the UTC bucket boundary can
    never be hidden by a stalled or reset monotonic measurement.
    """

    def __init__(
        self,
        *,
        bucket_end_utc: datetime,
        started_at_utc: datetime,
        current_at_utc: datetime | None = None,
        execution_started_at_utc: datetime | None = None,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
        safety_margin_seconds: float = 5.0,
        monotonic_fn: Callable[[], float] = time.monotonic,
        monotonic_origin: float | None = None,
        wall_clock_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._monotonic = monotonic_fn
        self._lock = threading.Lock()
        self.bucket_end_utc = bucket_end_utc.astimezone(timezone.utc)
        self.started_at_utc = started_at_utc.astimezone(timezone.utc)
        self.current_at_utc = (current_at_utc or self.started_at_utc).astimezone(timezone.utc)
        self.execution_started_at_utc = (
            execution_started_at_utc or self.started_at_utc
        ).astimezone(timezone.utc)
        self.safety_margin_seconds = max(0.0, float(safety_margin_seconds))
        self.heartbeat_seconds = max(0.0, float(heartbeat_seconds))
        self._wall_clock = wall_clock_fn
        creation_monotonic = monotonic_fn()
        self._started_monotonic = (
            float(monotonic_origin) if monotonic_origin is not None else creation_monotonic
        )
        wall_remaining = max(0.0, (self.bucket_end_utc - self.current_at_utc).total_seconds())
        self._budget_seconds = max(0.0, min(float(heartbeat_seconds), wall_remaining) - float(safety_margin_seconds))
        # The deadline is anchored to the monotonic instant at which this
        # object is created, while elapsed_seconds may be anchored to process
        # startup through ``monotonic_origin``.  Reconstructing a budget later
        # cannot extend this already-established deadline.
        self._deadline_monotonic = creation_monotonic + self._budget_seconds
        self._last_remaining_seconds = self._budget_seconds

    @property
    def budget_seconds(self) -> float:
        return self._budget_seconds

    @property
    def remaining_seconds(self) -> float:
        monotonic_remaining = max(0.0, self._deadline_monotonic - self._monotonic())
        if self._wall_clock is None:
            with self._lock:
                self._last_remaining_seconds = min(self._last_remaining_seconds, monotonic_remaining)
                return self._last_remaining_seconds
        try:
            observed = self._wall_clock()
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            observed = observed.astimezone(timezone.utc)
            wall_remaining = max(
                0.0,
                min(
                    self.heartbeat_seconds,
                    (self.bucket_end_utc - observed).total_seconds(),
                )
                - self.safety_margin_seconds,
            )
        except (AttributeError, TypeError, ValueError):
            # A malformed wall-clock read is not permission to continue
            # network work.  Treat it as an expired real-time budget.
            wall_remaining = 0.0
        with self._lock:
            # Never allow a wall-clock correction or a host/SQL disagreement
            # to give time back to the current invocation.
            self._last_remaining_seconds = min(
                self._last_remaining_seconds,
                monotonic_remaining,
                wall_remaining,
            )
            return self._last_remaining_seconds

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, self._monotonic() - self._started_monotonic)

    @property
    def deadline_reached(self) -> bool:
        return self.remaining_seconds <= 0.0

    def can_start(self, *, operation_seconds: float = REQUEST_TIMEOUT_SECONDS, sleep_seconds: float = 0.0) -> bool:
        return self.remaining_seconds >= max(0.0, float(operation_seconds)) + max(0.0, float(sleep_seconds))


def heartbeat_budget_decision(
    remaining_seconds: float,
    *,
    operation_seconds: float = REQUEST_TIMEOUT_SECONDS,
    retry: bool = False,
    retry_after_seconds: float = 0.0,
) -> dict[str, Any]:
    """Pure budget decision helper used by offline validation and tests."""

    enough = float(remaining_seconds) >= float(operation_seconds) + (float(retry_after_seconds) if retry else 0.0)
    return {
        "start_allowed": enough,
        "outcome": None if enough else (RETRY_SKIPPED_HEARTBEAT_BUDGET if retry else SKIPPED_HEARTBEAT_BUDGET),
        "remaining_seconds": float(remaining_seconds),
        "required_seconds": float(operation_seconds) + (float(retry_after_seconds) if retry else 0.0),
        "retry_after_respected": not retry or enough,
    }


def retry_eligible(*, response: HttpResponse | None = None, transport_failure: TransportFailure | None = None) -> bool:
    if response is not None and response.status_code in APPROVED_TRANSIENT_HTTP_STATUS_CODES:
        return True
    return bool(transport_failure and transport_failure.category in APPROVED_TRANSIENT_TRANSPORT_CATEGORIES)


def _retry_after_seconds(response: HttpResponse) -> float:
    value = response.retry_after
    if value is None:
        return 0.0
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0.0, (parsed.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0.0


def _error_category_for_response(response: HttpResponse) -> str | None:
    if response.status_code is None:
        return "HTTP_ERROR"
    if 200 <= response.status_code < 300:
        return None
    return "HTTP_ERROR"


def _transport_metadata(result: HttpResponse | TransportFailure) -> tuple[bool, bool, str, bool]:
    """Read explicit transport metadata without inspecting the client class."""

    return (
        bool(getattr(result, "network_attempted", False)),
        bool(getattr(result, "http_response_received", False)),
        str(getattr(result, "transport_kind", "UNKNOWN") or "UNKNOWN"),
        bool(getattr(result, "live_client_invocation", False)),
    )


def _quota_finalization_for_result(result: HttpResponse | TransportFailure) -> str:
    network_attempted, http_response_received, _transport_kind, _live_client_invocation = _transport_metadata(result)
    if network_attempted and http_response_received:
        return HTTP_PROVIDER_ATTEMPT_CONFIRMED
    if network_attempted or (
        bool(getattr(result, "live_client_invocation", False))
        and bool(getattr(result, "transport_execution_entered", False))
    ):
        # An ambiguous live open/read/close failure is not proof that bytes
        # were transmitted, but it is also not proof that they were not.  Keep
        # the permit conservatively accounted without inventing a physical
        # transport outcome.
        return CONSERVATIVE_LIVE_ATTEMPT
    return RELEASED_NO_NETWORK


def _client_execution_failure(error: BaseException, *, mdd_client: object | None = None) -> TransportFailure:
    """Convert a client exception while preserving transport uncertainty.

    A structured exception may prove a pre-network failure or a transmitted
    request.  Without that metadata, an exception from a live client is
    ambiguous once ``fetch`` has been entered, so its permit remains
    conservatively accounted.  Offline clients are known not to reach the
    provider and retain the no-network release behavior.
    """

    network_attempted = getattr(error, "network_attempted", None) is True
    http_response_received = network_attempted and getattr(error, "http_response_received", None) is True
    explicit_live = getattr(error, "live_client_invocation", None)
    live_client_invocation = (
        explicit_live is True
        or bool(getattr(mdd_client, "live_network", False))
        or (mdd_client is not None and getattr(mdd_client, "offline_safe", None) is False)
    )
    explicit_execution_entry = getattr(error, "transport_execution_entered", None)
    if explicit_execution_entry is None:
        transport_execution_entered = live_client_invocation
    else:
        transport_execution_entered = bool(explicit_execution_entry)
    if not live_client_invocation and explicit_execution_entry is None:
        transport_execution_entered = False
    transport_kind = str(getattr(error, "transport_kind", "LIVE_CLIENT" if live_client_invocation else "UNKNOWN"))
    return TransportFailure(
        CLIENT_EXECUTION_FAILURE,
        "client execution failure",
        network_attempted=network_attempted,
        http_response_received=http_response_received,
        transport_kind=transport_kind,
        live_client_invocation=live_client_invocation,
        transport_execution_entered=transport_execution_entered,
    )


class Collector:
    """Orchestrates exactly one current heartbeat."""

    def __init__(
        self,
        *,
        manifest: RuntimeManifest,
        persistence: PersistencePort,
        mdd_client: MddClient,
        quota_guard: QuotaGuard,
        max_concurrency: int = MAXIMUM_CONCURRENCY,
        max_attempts: int = MAX_ATTEMPTS,
        request_timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
        safety_margin_seconds: float = 5.0,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
        wall_clock_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        shutdown_event: threading.Event | None = None,
    ) -> None:
        if max_concurrency != MAXIMUM_CONCURRENCY:
            raise ValueError("MaximumConcurrency is approved and fixed at 2")
        if max_attempts != MAX_ATTEMPTS:
            raise ValueError("MaxAttempts is approved and fixed at 2")
        if request_timeout_seconds != REQUEST_TIMEOUT_SECONDS:
            raise ValueError("request timeout is approved and fixed at 30 seconds")
        self.manifest = manifest
        self.persistence = persistence
        self.mdd_client = mdd_client
        self.quota_guard = quota_guard
        self.max_concurrency = max_concurrency
        self.max_attempts = max_attempts
        self.request_timeout_seconds = request_timeout_seconds
        self.safety_margin_seconds = safety_margin_seconds
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn
        self.wall_clock_fn = wall_clock_fn
        self.shutdown_event = shutdown_event or threading.Event()
        self._active_lock = threading.Lock()
        self._worker_slots = threading.BoundedSemaphore(self.max_concurrency)
        self._active_count = 0
        self._max_observed_concurrency = 0

    def request_shutdown(self) -> None:
        self.shutdown_event.set()

    def plan(self, *, at_utc: datetime | None = None) -> Plan:
        return plan_for(self.manifest.targets, at_utc or datetime.now(timezone.utc))

    def live_research_plan(self, *, at_utc: datetime | None = None) -> Plan:
        """Return exactly the approved Heumarkt target for one current bucket."""

        now = (at_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
        try:
            target = self.manifest.live_research_target()
        except (KeyError, ValueError) as exc:
            raise LiveResearchModeBlocked(
                "approved manifest does not contain the fixed Heumarkt pilot target"
            ) from exc
        bucket = bucket_for(now)
        return Plan(bucket=bucket, due_targets=(target,), distribution_index=bucket.index % 6)

    def _live_research_preflight(self) -> None:
        if getattr(self.mdd_client, "offline_safe", True) is not False:
            raise LiveResearchModeBlocked(
                "live-research requires a live MDD client; offline clients are not eligible"
            )
        if _mdd_credential_status(self.mdd_client) != "VALID":
            raise LiveResearchModeBlocked(
                "MDD_API_KEY is missing or has an invalid format; zero network requests performed"
            )
        persistence_profile = getattr(self.persistence, "profile", None)
        if not (
            persistence_profile == PersistenceProfile.LIVE_RESEARCH
            or getattr(persistence_profile, "value", persistence_profile)
            == PersistenceProfile.LIVE_RESEARCH.value
        ):
            raise LiveResearchModeBlocked(
                "live-research requires the explicit LIVE_RESEARCH SQLite profile"
            )
        preflight = getattr(self.persistence, "preflight_storage", None)
        if not callable(preflight):
            raise LiveResearchModeBlocked("live-research storage preflight is unavailable")
        try:
            result = preflight()
        except Exception as exc:
            raise LiveResearchModeBlocked(
                "live-research storage preflight failed; zero network requests performed"
            ) from exc
        if not bool(result.get("ready", False)):
            raise LiveResearchModeBlocked(
                "live-research storage is not ready; zero network requests performed"
            )

    def run_once(
        self,
        *,
        mode: str = "dry-run",
        at_utc: datetime | None = None,
        run_id: str | None = None,
        allow_live: bool = False,
        pilot_target: str | None = None,
        runtime_timing: Mapping[str, Any] | None = None,
        execution_now_utc: datetime | None = None,
        execution_started_at_utc: datetime | None = None,
        execution_started_monotonic: float | None = None,
        logical_bucket_at_utc: datetime | None = None,
    ) -> CollectorRunResult:
        if mode not in {"dry-run", "simulate", "live", "live-research", "mac-sql", "mac-tiered50"}:
            raise ValueError(
                "run_once mode must be dry-run, simulate, live, live-research, mac-sql, or mac-tiered50"
            )
        if mode == "live":
            raise LiveModeBlocked(
                "live mode is blocked in Stage 14 implementation: production persistence and cutover are deferred"
            )
        if mode == "live-research":
            if not allow_live:
                raise LiveResearchModeBlocked(
                    "live-research requires explicit allow_live=True opt-in"
                )
            if pilot_target != LIVE_RESEARCH_TARGET_ID:
                raise LiveResearchModeBlocked(
                    "live-research requires the explicit Heumarkt pilot target"
                )
            self._live_research_preflight()
        if mode == "mac-sql":
            raise LiveModeBlocked("Legacy7 production runtime is retired; no network request performed")
        if mode == "mac-tiered50":
            if at_utc is not None:
                raise LiveModeBlocked(
                    "mac-tiered50 production time must come from the verified runtime clock; "
                    "no historical or future timestamp is accepted"
                )
            if not allow_live:
                raise LiveModeBlocked("mac-tiered50 requires explicit cutover approval and allow_live=True")
            if os.environ.get(MAC_SQL_MODE_ENV) != "TIERED50":
                raise LiveModeBlocked("mac-tiered50 requires the explicit TIERED50 runtime mode")
            if os.environ.get(MAC_TIERED50_ENABLED_ENV) != "1":
                raise LiveModeBlocked("mac-tiered50 is disabled; no network request performed")
            if os.environ.get(MAC_SQL_RUNTIME_STATE_ENV) != MAC_SQL_REQUIRED_RUNTIME_STATE:
                raise LiveModeBlocked(
                    "current Windows collector runtime is not confirmed retired; no network request performed"
                )
            if os.environ.get(MAC_TIERED50_CUTOVER_APPROVED_ENV) != "1":
                raise LiveModeBlocked("explicit Tiered50 cutover approval is missing; no network request performed")
            if os.environ.get(MAC_TIERED50_TARGET_SCOPE_ENV) != MAC_TIERED50_REQUIRED_TARGET_SCOPE:
                raise LiveModeBlocked("explicit approved Tiered50 target scope is missing; no network request performed")
            if os.environ.get(MAC_TIERED50_VALIDATION_POLICY_ENV) != MAC_TIERED50_REQUIRED_VALIDATION_POLICY:
                raise LiveModeBlocked(
                    "Tiered50 requires completed offline validation before live collection; no network request performed"
                )
            if _mdd_credential_status(self.mdd_client) != "VALID":
                raise LiveModeBlocked("MDD_API_KEY is missing or invalid; no network request performed")
            if not getattr(self.persistence, "is_production_ready", False):
                raise LiveModeBlocked("mac-tiered50 requires a production-ready direct persistence adapter")
            if getattr(self.persistence, "collection_context", None) != MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT:
                raise LiveModeBlocked("mac-tiered50 requires the dedicated Tiered50 persistence context")
            if not self.manifest.targets or any(
                target.sampling_era is not SamplingEra.TIERED50
                or target.production_sampling_target_id is None
                for target in self.manifest.targets
            ):
                raise LiveModeBlocked("mac-tiered50 requires all 50 SQL-bound Tiered50 targets")
            self.manifest.validate_production_binding()
        if mode not in {"live-research", "mac-tiered50"} and getattr(self.mdd_client, "offline_safe", True) is False:
            raise LiveModeBlocked("offline modes cannot use a live-network MDD client")

        production_mode = mode == "mac-tiered50"

        # Capture dispatcher entry inside the dispatcher.  A runner timestamp
        # taken before this call describes preflight completion and can make
        # timing diagnostics (and the initial budget) optimistic.  Offline
        # callers may continue to provide a deterministic execution timestamp.
        dispatcher_entry_monotonic = self.monotonic_fn()
        dispatcher_entry_observed = (
            self.wall_clock_fn()
            if production_mode
            else (
                execution_now_utc
                or at_utc
                or logical_bucket_at_utc
                or execution_started_at_utc
                or self.wall_clock_fn()
            )
        )
        if dispatcher_entry_observed.tzinfo is None:
            dispatcher_entry_observed = dispatcher_entry_observed.replace(tzinfo=timezone.utc)
        dispatcher_entry_observed = dispatcher_entry_observed.astimezone(timezone.utc)

        logical_observed = logical_bucket_at_utc or at_utc or execution_started_at_utc or dispatcher_entry_observed
        if logical_observed.tzinfo is None:
            logical_observed = logical_observed.replace(tzinfo=timezone.utc)
        logical_now = logical_observed.astimezone(timezone.utc)
        execution_now = dispatcher_entry_observed
        execution_started = (execution_started_at_utc or logical_now).astimezone(timezone.utc)
        plan = (
            self.live_research_plan(at_utc=logical_now)
            if mode == "live-research"
            else self.plan(at_utc=logical_now)
        )
        collection_context = (
            LIVE_RESEARCH_COLLECTION_CONTEXT
            if mode == "live-research"
            else MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT
            if mode == "mac-tiered50"
            else "OFFLINE_DEVELOPMENT"
        )
        identifier = run_id or str(uuid.uuid4())
        started_monotonic = self.monotonic_fn()
        run_timing: dict[str, Any] = {
            "run_started_at_utc": execution_now.isoformat().replace("+00:00", "Z"),
            "dispatcher_entry_at_utc": execution_now.isoformat().replace("+00:00", "Z"),
            "dispatcher_entry_time_source": (
                "COLLECTOR_WALL_CLOCK_UTC" if production_mode else "OFFLINE_INJECTED_OR_WALL_CLOCK_UTC"
            ),
            "logical_bucket_observed_at_utc": logical_now.isoformat().replace("+00:00", "Z"),
            "process_started_at_utc": execution_started.isoformat().replace("+00:00", "Z"),
            "bucket_start_utc": plan.bucket.start_utc.isoformat().replace("+00:00", "Z"),
            "bucket_end_utc": plan.bucket.end_utc.isoformat().replace("+00:00", "Z"),
            "expected_launchd_calendar_boundary_at_utc": plan.bucket.start_utc.isoformat().replace("+00:00", "Z"),
            "expected_launchd_boundary_time_source": "UTC_BUCKET_BOUNDARY_DERIVED_NOT_LAUNCHD_EVIDENCE",
            "launchd_trigger_at_utc": None,
            "launchd_trigger_time_source": "NOT_INDEPENDENTLY_OBSERVED",
            "launch_jitter_ms": max(0.0, (execution_started - plan.bucket.start_utc).total_seconds() * 1000.0),
            "startup_delay_ms": max(0.0, (execution_now - execution_started).total_seconds() * 1000.0),
            "preflight_to_dispatcher_delay_ms": None,
            "late_dispatch": execution_now >= plan.bucket.end_utc,
            "heartbeat_budget_seconds": None,
            "safety_margin_seconds": float(self.safety_margin_seconds),
            "injected_logical_time": at_utc is not None and not production_mode,
            "clock_source_provenance": {
                "logical_bucket": "OBSERVED_PROCESS_ENTRY_UTC" if production_mode else "OFFLINE_LOGICAL_INPUT",
                "dispatcher_entry": "COLLECTOR_WALL_CLOCK_FN_UTC" if production_mode else "OFFLINE_CLOCK_FN",
                "deadline": "ABSOLUTE_UTC_BUCKET_END_WITH_SAFETY_MARGIN",
                "elapsed_duration": "MONOTONIC_CLOCK",
                "launchd_trigger": "NOT_INDEPENDENTLY_OBSERVED",
            },
        }
        if runtime_timing:
            run_timing.update({str(key): value for key, value in runtime_timing.items()})
        preflight_completed = _parse_utc_timestamp(run_timing.get("preflight_completed_at_utc"))
        run_timing["dispatcher_entry_at_utc"] = execution_now.isoformat().replace("+00:00", "Z")
        run_timing["dispatcher_entry_time_source"] = (
            "COLLECTOR_WALL_CLOCK_UTC" if production_mode else "OFFLINE_INJECTED_OR_WALL_CLOCK_UTC"
        )
        run_timing["preflight_to_dispatcher_delay_ms"] = (
            max(0.0, (execution_now - preflight_completed).total_seconds() * 1000.0)
            if preflight_completed is not None
            else None
        )
        run_timing["dispatcher_entry_monotonic_elapsed_ms"] = max(
            0.0,
            (self.monotonic_fn() - dispatcher_entry_monotonic) * 1000.0,
        )
        budget = HeartbeatBudget(
            bucket_end_utc=plan.bucket.end_utc,
            started_at_utc=logical_now,
            current_at_utc=execution_now,
            execution_started_at_utc=execution_started,
            safety_margin_seconds=self.safety_margin_seconds,
            monotonic_fn=self.monotonic_fn,
            monotonic_origin=(
                execution_started_monotonic
                if execution_started_monotonic is not None
                else started_monotonic
            ),
            wall_clock_fn=self.wall_clock_fn if production_mode else None,
        )
        run_timing["heartbeat_budget_seconds"] = budget.budget_seconds
        run_timing["remaining_real_deadline_budget_seconds"] = budget.remaining_seconds
        run_timing["initial_remaining_budget_seconds"] = budget.remaining_seconds
        run_timing["deadline_expired_at_dispatch"] = budget.deadline_reached
        if production_mode:
            writer_guard = getattr(self.persistence, "assert_no_competing_writer", None)
            if callable(writer_guard):
                writer_guard(collection_context=collection_context)
        event_now = execution_now if production_mode else logical_now
        self.persistence.start_collector_run(
            run_id=identifier,
            panel_version=self.manifest.panel_version,
            logical_due_bucket=plan.bucket.index,
            mode=mode,
            started_at=event_now,
            collection_context=collection_context,
        )
        outcomes: list[TargetOutcome] = []

        # This is the outer atomic boundary. It must be won before target
        # claims or a ThreadPoolExecutor are created, so a duplicate
        # dispatcher invocation cannot race the winning dispatcher.
        dispatcher_claim_started = self.monotonic_fn()
        dispatcher_claim = self.persistence.claim_dispatcher_bucket(
            run_id=identifier,
            panel_version=self.manifest.panel_version,
            logical_due_bucket=plan.bucket.index,
            claimed_at=event_now,
        )
        run_timing["dispatcher_claim_duration_ms"] = max(
            0.0, (self.monotonic_fn() - dispatcher_claim_started) * 1000.0
        )
        if not dispatcher_claim.won:
            duplicate_event = getattr(self.persistence, "record_duplicate_target_claim_event", None)
            if callable(duplicate_event) and dispatcher_claim.existing_status in {
                "COMPLETE",
                "COMPLETED_WITH_FAILURES",
                COMPLETED_WITH_COVERAGE_GAP,
                "INTERRUPTED",
            }:
                for target in plan.due_targets:
                    duplicate_event(
                        panel_version=self.manifest.panel_version,
                        target=target,
                        logical_due_bucket=plan.bucket.index,
                        run_id=identifier,
                        event_at=event_now,
                    )
            outcomes = [
                TargetOutcome(
                    target_id=target.source_parent_station_id,
                    station_name=target.station_name,
                    tier=target.tier,
                    phase_index=target.phase_index,
                    logical_due_bucket=plan.bucket.index,
                    status="DUPLICATE_DISPATCHER_CLAIM",
                    claim_won=False,
                )
                for target in plan.due_targets
            ]
            status = "DUPLICATE_NO_WORK"
            result = CollectorRunResult(
                run_id=identifier,
                mode=mode,
                bucket=plan.bucket,
                due_count=plan.due_count,
                outcomes=tuple(outcomes),
                status=status,
                max_observed_concurrency=0,
                live_network_used=False,
                dispatcher_claim_won=False,
                dispatcher_claim_status="DUPLICATE_DISPATCHER_CLAIM",
                collection_context=collection_context,
                timing=run_timing,
            )
            maintenance = self._finish_live_research_maintenance(
                mode=mode,
                verify=False,
            )
            result = replace_run_maintenance(result, maintenance)
            duplicate_completed_at = self._event_time(
                logical_time=logical_now,
                collection_context=collection_context,
            )
            run_timing["run_completed_at_utc"] = duplicate_completed_at.isoformat().replace("+00:00", "Z")
            run_timing["first_target_claim_at_utc"] = None
            run_timing["first_http_transmission_at_utc"] = None
            finalization_started = self.monotonic_fn()
            finalization_started_at = self._event_time(
                logical_time=logical_now,
                collection_context=collection_context,
            )
            run_timing["run_finalization_started_at_utc"] = finalization_started_at.isoformat().replace(
                "+00:00", "Z"
            )
            self.persistence.complete_collector_run(
                run_id=identifier,
                status=status,
                completed_at=duplicate_completed_at,
                summary=result.to_dict(),
            )
            finalization_completed_at = self._event_time(
                logical_time=logical_now,
                collection_context=collection_context,
            )
            run_timing["run_finalization_completed_at_utc"] = finalization_completed_at.isoformat().replace(
                "+00:00", "Z"
            )
            run_timing["run_finalization_duration_ms"] = max(
                0.0,
                (self.monotonic_fn() - finalization_started) * 1000.0,
            )
            return result

        futures: dict[Future[TargetOutcome], Target] = {}
        pending = list(plan.due_targets)
        with ThreadPoolExecutor(max_workers=self.max_concurrency, thread_name_prefix="stage14-target") as executor:
            while pending or futures:
                while pending and len(futures) < self.max_concurrency and not self.shutdown_event.is_set():
                    if not budget.can_start(operation_seconds=self.request_timeout_seconds):
                        # Once the deadline cannot accommodate one complete
                        # request, every still-pending target has the same
                        # auditable no-network outcome.  Persist the bounded
                        # set in one transaction rather than spending one SQL
                        # round trip per target after useful work is over.
                        skipped_targets = tuple(pending)
                        pending.clear()
                        skip_started = self.monotonic_fn()
                        skipped_outcomes, skip_audit_status = self._budget_skipped_outcomes(
                            skipped_targets,
                            plan.bucket.index,
                            budget=budget,
                            run_id=identifier,
                            collection_context=collection_context,
                            logical_time=logical_now,
                        )
                        outcomes.extend(skipped_outcomes)
                        run_timing.setdefault("budget_skip_audit_batches", []).append(
                            {
                                "target_count": len(skipped_targets),
                                "status": skip_audit_status,
                                "duration_ms": max(
                                    0.0,
                                    (self.monotonic_fn() - skip_started) * 1000.0,
                                ),
                            }
                        )
                        continue
                    target = pending.pop(0)
                    # The target claim is deliberately just in time: targets
                    # still waiting in ``pending`` are not marked claimed.
                    claim_started = self.monotonic_fn()
                    claim_time = (
                        logical_now
                        if not production_mode
                        else self.wall_clock_fn()
                    )
                    if claim_time.tzinfo is None:
                        claim_time = claim_time.replace(tzinfo=timezone.utc)
                    claim = self.persistence.claim_target(
                        run_id=identifier,
                        target=target,
                        logical_due_bucket=plan.bucket.index,
                        claimed_at=claim_time.astimezone(timezone.utc),
                    )
                    claim_duration_ms = max(0.0, (self.monotonic_fn() - claim_started) * 1000.0)
                    run_timing.setdefault("target_claim_durations_ms", []).append(claim_duration_ms)
                    if not claim.won:
                        outcomes.append(
                            TargetOutcome(
                                target_id=target.source_parent_station_id,
                                station_name=target.station_name,
                                tier=target.tier,
                                phase_index=target.phase_index,
                                logical_due_bucket=plan.bucket.index,
                                status="DUPLICATE_CLAIM",
                                claim_won=False,
                            )
                        )
                        continue
                    future = executor.submit(
                        self._execute_target,
                        identifier,
                        target,
                        plan.bucket.index,
                        budget,
                        logical_now,
                        collection_context,
                        claim_duration_ms,
                        claim_time.astimezone(timezone.utc),
                    )
                    futures[future] = target
                if self.shutdown_event.is_set():
                    for target in pending:
                        outcomes.append(
                            self._interrupted_outcome(
                                target,
                                plan.bucket.index,
                                run_id=identifier,
                                collection_context=collection_context,
                            )
                        )
                    pending.clear()
                if not futures:
                    break
                done, _ = wait(tuple(futures), return_when=FIRST_COMPLETED)
                for future in done:
                    target = futures.pop(future)
                    try:
                        outcomes.append(future.result())
                    except BaseException:
                        outcomes.append(
                            TargetOutcome(
                                target_id=target.source_parent_station_id,
                                station_name=target.station_name,
                                tier=target.tier,
                                phase_index=target.phase_index,
                                logical_due_bucket=plan.bucket.index,
                                status="FAILED",
                                error_category="SANITIZED_ERROR",
                            )
                        )

        outcomes.sort(key=lambda item: item.target_id)
        if self.shutdown_event.is_set():
            status = "INTERRUPTED"
        elif outcomes and all(item.status in {"DUPLICATE_CLAIM", "DUPLICATE_DISPATCHER_CLAIM"} for item in outcomes):
            status = "DUPLICATE_NO_WORK"
        elif any(item.status == "FAILED" for item in outcomes):
            status = "COMPLETED_WITH_FAILURES"
        elif (
            not all(item.status == "SUCCEEDED" for item in outcomes)
            or len(outcomes) != plan.due_count
            or any(item.deadline_outcome is not None for item in outcomes)
        ):
            status = COMPLETED_WITH_COVERAGE_GAP
        else:
            status = "COMPLETE"
        run_timing["target_claim_count"] = len(run_timing.get("target_claim_durations_ms", []))
        run_timing["target_claim_duration_max_ms"] = max(
            run_timing.get("target_claim_durations_ms", [0.0]), default=0.0
        )
        run_timing["remaining_real_deadline_budget_seconds"] = budget.remaining_seconds
        claim_timestamps = [
            parsed
            for item in outcomes
            for parsed in [_parse_utc_timestamp(item.timing.get("target_claimed_at_utc"))]
            if parsed is not None
        ]
        http_timestamps = [
            parsed
            for item in outcomes
            for parsed in [_parse_utc_timestamp(item.timing.get("network_attempt_started_at_utc"))]
            if parsed is not None
        ]
        run_timing["first_target_claim_at_utc"] = (
            min(claim_timestamps).isoformat().replace("+00:00", "Z")
            if claim_timestamps
            else None
        )
        run_timing["first_http_transmission_at_utc"] = (
            min(http_timestamps).isoformat().replace("+00:00", "Z")
            if http_timestamps
            else None
        )
        run_timing["first_http_transmission_time_source"] = (
            "TARGET_ATTEMPT_WALL_CLOCK_UTC" if http_timestamps else "NO_HTTP_TRANSMISSION"
        )
        run_completed_at = self._event_time(
            logical_time=logical_now,
            collection_context=collection_context,
        )
        run_timing["run_completed_at_utc"] = run_completed_at.isoformat().replace("+00:00", "Z")
        run_timing["run_duration_ms"] = max(0.0, (self.monotonic_fn() - started_monotonic) * 1000.0)
        run_timing["targets_processed_after_deadline"] = sum(
            1
            for item in outcomes
            if item.timing.get("processed_after_deadline")
        )
        result = CollectorRunResult(
            run_id=identifier,
            mode=mode,
            bucket=plan.bucket,
            due_count=plan.due_count,
            outcomes=tuple(outcomes),
            status=status,
            max_observed_concurrency=self._max_observed_concurrency,
            live_network_used=any(item.live_network_calls > 0 for item in outcomes),
            dispatcher_claim_won=True,
            dispatcher_claim_status="CLAIMED",
            collection_context=collection_context,
            timing=run_timing,
        )
        maintenance = self._finish_live_research_maintenance(
            mode=mode,
            run_id=identifier,
            panel_version=self.manifest.panel_version,
            logical_due_bucket=plan.bucket.index,
            target_id=LIVE_RESEARCH_TARGET_ID,
        )
        result = replace_run_maintenance(result, maintenance)
        finalization_started = self.monotonic_fn()
        finalization_started_at = self._event_time(
            logical_time=logical_now,
            collection_context=collection_context,
        )
        run_timing["run_finalization_started_at_utc"] = finalization_started_at.isoformat().replace(
            "+00:00", "Z"
        )
        self.persistence.complete_collector_run(
            run_id=identifier,
            status=status,
            completed_at=run_completed_at,
            summary=result.to_dict(),
        )
        finalization_completed_at = self._event_time(
            logical_time=logical_now,
            collection_context=collection_context,
        )
        run_timing["run_finalization_completed_at_utc"] = finalization_completed_at.isoformat().replace(
            "+00:00", "Z"
        )
        run_timing["run_finalization_duration_ms"] = max(
            0.0,
            (self.monotonic_fn() - finalization_started) * 1000.0,
        )
        return result

    def _finish_live_research_maintenance(
        self,
        *,
        mode: str,
        verify: bool = True,
        run_id: str | None = None,
        panel_version: str | None = None,
        logical_due_bucket: int | None = None,
        target_id: str | None = None,
    ) -> Mapping[str, Any]:
        if mode != "live-research":
            return {}
        cleanup = getattr(self.persistence, "cleanup_expired_raw_evidence", None)
        backup = getattr(self.persistence, "backup_live_database", None)
        try:
            cleanup_summary = cleanup() if callable(cleanup) else {
                "status": "UNAVAILABLE",
                "reason": "raw cleanup hook is unavailable",
            }
        except Exception as exc:
            cleanup_summary = {
                "status": "FAILED",
                "error": type(exc).__name__,
                "reason": "expired raw cleanup failed",
            }
        try:
            backup_summary = backup() if callable(backup) else {
                "status": "UNAVAILABLE",
                "reason": "SQLite backup hook is unavailable",
            }
        except Exception as exc:
            backup_summary = {
                "status": "FAILED",
                "error": type(exc).__name__,
                "reason": "daily SQLite backup failed",
            }
        verification_summary: Mapping[str, Any]
        verifier = getattr(self.persistence, "verify_live_research_heartbeat", None)
        if verify and callable(verifier) and run_id is not None and panel_version is not None and logical_due_bucket is not None and target_id is not None:
            try:
                verification_summary = verifier(
                    run_id=run_id,
                    panel_version=panel_version,
                    logical_due_bucket=logical_due_bucket,
                    target_id=target_id,
                )
            except Exception as exc:
                verification_summary = {
                    "status": "FAILED",
                    "error": type(exc).__name__,
                    "reason": "post-heartbeat evidence verification failed",
                }
        elif verify:
            verification_summary = {
                "status": "UNAVAILABLE",
                "reason": "post-heartbeat evidence verification hook is unavailable",
            }
        else:
            verification_summary = {
                "status": "NOT_APPLICABLE",
                "reason": "dispatcher duplicate produced no pilot target audit",
            }
        return {
            "raw_cleanup": dict(cleanup_summary),
            "backup": dict(backup_summary),
            "verification": dict(verification_summary),
            "status": "COMPLETE"
            if cleanup_summary.get("status") == "COMPLETE"
            and backup_summary.get("status") == "COMPLETE"
            and verification_summary.get("status") in {"COMPLETE", "NOT_APPLICABLE"}
            else "FAILED",
        }

    def _event_time(self, *, logical_time: datetime, collection_context: str) -> datetime:
        """Use live wall time for SQL authorization/audit, injected time offline."""

        if collection_context == MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT:
            value = self.wall_clock_fn()
        else:
            value = logical_time
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _deadline_classification(budget: HeartbeatBudget, observed_at: datetime) -> str:
        return LATE_DISPATCH if observed_at >= budget.bucket_end_utc else INSUFFICIENT_BUDGET

    def _interrupted_outcome(
        self,
        target: Target,
        bucket_index: int,
        *,
        run_id: str | None = None,
        collection_context: str = "OFFLINE_DEVELOPMENT",
    ) -> TargetOutcome:
        outcome = TargetOutcome(
            target_id=target.source_parent_station_id,
            station_name=target.station_name,
            tier=target.tier,
            phase_index=target.phase_index,
            logical_due_bucket=bucket_index,
            status="SKIPPED",
            skip_reason=INTERRUPTED_SHUTDOWN,
        )
        if run_id is not None:
            try:
                audit_id = self.persistence.start_target_audit(
                    run_id=run_id,
                    target=target,
                    logical_due_bucket=bucket_index,
                    started_at=datetime.now(timezone.utc),
                    collection_context=collection_context,
                )
                self.persistence.complete_target_audit(
                    audit_id=audit_id,
                    outcome=outcome.to_dict(),
                    completed_at=datetime.now(timezone.utc),
                )
            except BaseException:
                pass
        return outcome

    def _budget_skipped_outcome(
        self,
        target: Target,
        bucket_index: int,
        *,
        budget: HeartbeatBudget,
        run_id: str,
        collection_context: str,
        logical_time: datetime,
    ) -> TargetOutcome:
        """Record one due target skipped before a claim or network operation."""

        outcome, observed_at = self._make_budget_skipped_outcome(
            target,
            bucket_index,
            budget=budget,
            collection_context=collection_context,
            logical_time=logical_time,
        )
        try:
            self._persist_single_budget_skip(
                run_id=run_id,
                target=target,
                outcome=outcome,
                started_at=observed_at,
                completed_at=observed_at,
                collection_context=collection_context,
            )
        except BaseException:
            LOGGER.warning("could not persist pre-claim budget skip", exc_info=True)
        return outcome

    def _make_budget_skipped_outcome(
        self,
        target: Target,
        bucket_index: int,
        *,
        budget: HeartbeatBudget,
        collection_context: str,
        logical_time: datetime,
    ) -> tuple[TargetOutcome, datetime]:
        """Build a no-network outcome without changing durable claim state."""

        observed_at = self._event_time(logical_time=logical_time, collection_context=collection_context)
        classification = self._deadline_classification(budget, observed_at)
        timing = {
            "processed_after_deadline": budget.deadline_reached,
            "deadline_remaining_seconds": budget.remaining_seconds,
            "deadline_classification": classification,
            "target_started": False,
            "audit_started_at_utc": observed_at.isoformat().replace("+00:00", "Z"),
            "audit_completed_at_utc": observed_at.isoformat().replace("+00:00", "Z"),
        }
        outcome = TargetOutcome(
            target_id=target.source_parent_station_id,
            station_name=target.station_name,
            tier=target.tier,
            phase_index=target.phase_index,
            logical_due_bucket=bucket_index,
            status="SKIPPED",
            skip_reason=SKIPPED_HEARTBEAT_BUDGET,
            timing=timing,
            deadline_outcome=classification,
        )
        return outcome, observed_at

    def _persist_single_budget_skip(
        self,
        *,
        run_id: str,
        target: Target,
        outcome: TargetOutcome,
        started_at: datetime,
        completed_at: datetime,
        collection_context: str,
    ) -> None:
        """Persist one skip through the no-claim additive path.

        Even the compatibility fallback must not call ``start_target_audit``:
        that method transitions a target claim to RUNNING and would turn a
        late pre-claim skip into a retroactive claim.
        """

        batch = getattr(self.persistence, "persist_skipped_target_audits", None)
        if not callable(batch):
            raise RuntimeError("persistence adapter lacks the no-claim skip-audit path")
        batch(
            run_id=run_id,
            logical_due_bucket=outcome.logical_due_bucket,
            skipped_audits=(
                {
                    "target": target,
                    "outcome": outcome.to_dict(),
                    "started_at": started_at,
                    "completed_at": completed_at,
                },
            ),
            collection_context=collection_context,
        )

    def _budget_skipped_outcomes(
        self,
        targets: Sequence[Target],
        bucket_index: int,
        *,
        budget: HeartbeatBudget,
        run_id: str,
        collection_context: str,
        logical_time: datetime,
    ) -> tuple[tuple[TargetOutcome, ...], str]:
        """Build and persist a bounded batch of no-network skip audits.

        The batch hook is additive.  Adapters that cannot complete the whole
        batch may use a bounded one-row version of the same no-claim path; no
        fallback calls the normal target-audit starter.  The method reports
        the audit path separately so a missing audit can never be mistaken for
        a successful collection outcome.
        """

        entries: list[dict[str, Any]] = []
        outcomes: list[TargetOutcome] = []
        for target in targets:
            outcome, observed_at = self._make_budget_skipped_outcome(
                target,
                bucket_index,
                budget=budget,
                collection_context=collection_context,
                logical_time=logical_time,
            )
            outcomes.append(outcome)
            entries.append(
                {
                    "target": target,
                    "outcome": outcome.to_dict(),
                    "started_at": observed_at,
                    "completed_at": observed_at,
                }
            )
        if not entries:
            return tuple(outcomes), "NO_WORK"

        batch = getattr(self.persistence, "persist_skipped_target_audits", None)
        if callable(batch):
            try:
                batch(
                    run_id=run_id,
                    logical_due_bucket=bucket_index,
                    skipped_audits=tuple(entries),
                    collection_context=collection_context,
                )
                return tuple(outcomes), "BATCH_APPLIED_OR_IDEMPOTENT"
            except BaseException:
                # A conforming batch adapter must be transactional.  Keep a
                # safe fallback for older adapters and ambiguous connection
                # failures; every fallback row remains a SKIPPED audit.
                LOGGER.warning("budget-skip batch persistence failed; using single-row fallback", exc_info=True)

        fallback_failures = 0
        for entry, outcome in zip(entries, outcomes):
            try:
                self._persist_single_budget_skip(
                    run_id=run_id,
                    target=entry["target"],
                    outcome=outcome,
                    started_at=entry["started_at"],
                    completed_at=entry["completed_at"],
                    collection_context=collection_context,
                )
            except BaseException:
                fallback_failures += 1
                LOGGER.warning("could not persist fallback pre-claim budget skip", exc_info=True)
        return tuple(outcomes), "SINGLE_ROW_FALLBACK" if fallback_failures == 0 else "AUDIT_PERSISTENCE_FAILED"

    def _enter_worker(self) -> None:
        # ThreadPoolExecutor already bounds submissions, while this slot also
        # makes direct worker coordination deterministic and never raises just
        # because another invocation is active.
        self._worker_slots.acquire()
        with self._active_lock:
            self._active_count += 1
            self._max_observed_concurrency = max(self._max_observed_concurrency, self._active_count)

    def _leave_worker(self) -> None:
        with self._active_lock:
            self._active_count -= 1
        self._worker_slots.release()

    def _execute_target(
        self,
        run_id: str,
        target: Target,
        bucket_index: int,
        budget: HeartbeatBudget,
        quota_at_utc: datetime,
        collection_context: str,
        target_claim_duration_ms: float = 0.0,
        target_claimed_at_utc: datetime | None = None,
    ) -> TargetOutcome:
        self._enter_worker()
        started = self._event_time(logical_time=quota_at_utc, collection_context=collection_context)
        target_timing: dict[str, Any] = {
            "target_started_at_utc": started.isoformat().replace("+00:00", "Z"),
            "target_claim_duration_ms": float(target_claim_duration_ms),
            "target_claimed_at_utc": (
                target_claimed_at_utc.isoformat().replace("+00:00", "Z")
                if target_claimed_at_utc is not None
                else None
            ),
        }
        audit_id: Any = None
        attempts = 0
        last_status: int | None = None
        parser_outcome: str | None = None
        persistence_outcome: str | None = None
        retry_outcome: str | None = None
        retry_decision: str | None = None
        error_category: str | None = None
        result_count: int | None = None
        pending_retry_after = 0.0
        simulated_transport_calls = 0
        live_network_calls = 0
        live_client_invocations = 0
        try:
            audit_started = self.monotonic_fn()
            target_timing["target_audit_started_at_utc"] = started.isoformat().replace("+00:00", "Z")
            audit_id = self.persistence.start_target_audit(
                run_id=run_id,
                target=target,
                logical_due_bucket=bucket_index,
                started_at=started,
                collection_context=collection_context,
            )
            target_timing["target_audit_start_duration_ms"] = max(
                0.0, (self.monotonic_fn() - audit_started) * 1000.0
            )
            target_timing["target_audit_start_completed_at_utc"] = self._event_time(
                logical_time=quota_at_utc,
                collection_context=collection_context,
            ).isoformat().replace("+00:00", "Z")
            while attempts < self.max_attempts:
                retrying = attempts > 0
                retry_after = pending_retry_after if retrying else 0.0
                if not budget.can_start(operation_seconds=self.request_timeout_seconds):
                    reason = RETRY_SKIPPED_HEARTBEAT_BUDGET if retrying else SKIPPED_HEARTBEAT_BUDGET
                    target_timing.update({
                        "processed_after_deadline": budget.deadline_reached,
                        "deadline_remaining_seconds": budget.remaining_seconds,
                        "deadline_classification": self._deadline_classification(budget, started),
                    })
                    outcome = TargetOutcome(target.source_parent_station_id, target.station_name, target.tier, target.phase_index, bucket_index, "SKIPPED", http_attempts=attempts, skip_reason=reason, retry_outcome=reason, timing=target_timing, deadline_outcome=self._deadline_classification(budget, started))
                    self.persistence.complete_target_audit(audit_id=audit_id, outcome=outcome.to_dict(), completed_at=self._event_time(logical_time=quota_at_utc, collection_context=collection_context))
                    return outcome
                if retrying:
                    if retry_after and not budget.can_start(operation_seconds=self.request_timeout_seconds, sleep_seconds=retry_after):
                        retry_decision = "RETRY_SKIPPED_HEARTBEAT_BUDGET"
                        retry_outcome = RETRY_SKIPPED_HEARTBEAT_BUDGET
                        target_timing.update({"processed_after_deadline": budget.deadline_reached, "deadline_remaining_seconds": budget.remaining_seconds, "deadline_classification": self._deadline_classification(budget, started)})
                        outcome = TargetOutcome(target.source_parent_station_id, target.station_name, target.tier, target.phase_index, bucket_index, "SKIPPED", http_status=last_status, http_attempts=attempts, error_category=error_category, retry_outcome=retry_outcome, skip_reason=retry_outcome, timing=target_timing, deadline_outcome=self._deadline_classification(budget, started))
                        self.persistence.complete_target_audit(audit_id=audit_id, outcome=outcome.to_dict(), completed_at=self._event_time(logical_time=quota_at_utc, collection_context=collection_context))
                        return outcome
                    if retry_after:
                        self.sleep_fn(retry_after)
                        if not budget.can_start(operation_seconds=self.request_timeout_seconds):
                            retry_decision = "RETRY_SKIPPED_HEARTBEAT_BUDGET"
                            retry_outcome = RETRY_SKIPPED_HEARTBEAT_BUDGET
                            target_timing.update({"processed_after_deadline": budget.deadline_reached, "deadline_remaining_seconds": budget.remaining_seconds, "deadline_classification": self._deadline_classification(budget, started)})
                            outcome = TargetOutcome(target.source_parent_station_id, target.station_name, target.tier, target.phase_index, bucket_index, "SKIPPED", http_status=last_status, http_attempts=attempts, error_category=error_category, retry_outcome=retry_outcome, skip_reason=retry_outcome, timing=target_timing, deadline_outcome=self._deadline_classification(budget, started))
                            self.persistence.complete_target_audit(audit_id=audit_id, outcome=outcome.to_dict(), completed_at=self._event_time(logical_time=quota_at_utc, collection_context=collection_context))
                            return outcome

                # Offline simulations retain their injected logical timestamp;
                # live SQL authorization uses the current wall clock at the
                # reservation point, after any preceding SQL or retry delay.
                decision_at = self._event_time(
                    logical_time=quota_at_utc,
                    collection_context=collection_context,
                )
                authorization_key = f"{run_id}:{audit_id}:{attempts + 1}"
                quota_started = self.monotonic_fn()
                quota_decision = (
                    self.quota_guard.allow_retry(
                        at_utc=decision_at,
                        authorization_key=authorization_key,
                    )
                    if retrying
                    else self.quota_guard.allow_initial(
                        at_utc=decision_at,
                        authorization_key=authorization_key,
                    )
                )
                quota_duration_ms = max(0.0, (self.monotonic_fn() - quota_started) * 1000.0)
                target_timing["quota_authorization_duration_ms"] = quota_duration_ms
                target_timing["quota_authorization_at_utc"] = quota_decision.authorization_at_utc
                target_timing["quota_authorization_valid_until_utc"] = quota_decision.valid_until_utc
                self._record_quota(
                    run_id,
                    target,
                    bucket_index,
                    "RETRY" if retrying else "INITIAL",
                    quota_decision.to_dict(),
                    decision_at,
                    collection_context,
                )
                if not quota_decision.allowed:
                    self.quota_guard.release(quota_decision)
                    if retrying:
                        retry_decision = "RETRY_DENIED_QUOTA"
                        retry_outcome = RETRY_SKIPPED_QUOTA
                        outcome = TargetOutcome(
                            target.source_parent_station_id,
                            target.station_name,
                            target.tier,
                            target.phase_index,
                            bucket_index,
                            "FAILED",
                            http_status=last_status,
                            http_attempts=attempts,
                            parser_outcome=parser_outcome,
                            persistence_outcome=persistence_outcome,
                            error_category="QUOTA_DENIED",
                            retry_outcome=retry_outcome,
                            simulated_transport_calls=simulated_transport_calls,
                            live_network_calls=live_network_calls,
                            timing=target_timing,
                        )
                    else:
                        outcome = TargetOutcome(
                            target.source_parent_station_id,
                            target.station_name,
                            target.tier,
                            target.phase_index,
                            bucket_index,
                            "SKIPPED",
                            skip_reason=SKIPPED_QUOTA,
                            error_category="QUOTA_DENIED",
                            simulated_transport_calls=simulated_transport_calls,
                            live_network_calls=live_network_calls,
                            timing=target_timing,
                        )
                    self.persistence.complete_target_audit(
                        audit_id=audit_id,
                        outcome=outcome.to_dict(),
                        completed_at=self._event_time(logical_time=quota_at_utc, collection_context=collection_context),
                    )
                    return outcome

                target_timing["budget_remaining_after_quota_seconds"] = budget.remaining_seconds
                authorized_at = _parse_utc_timestamp(quota_decision.authorization_at_utc) or decision_at
                target_timing["permit_granted_before_bucket_boundary"] = authorized_at < budget.bucket_end_utc
                if not budget.can_start(operation_seconds=self.request_timeout_seconds):
                    self.quota_guard.release(quota_decision)
                    target_timing.update({
                        "processed_after_deadline": True,
                        "deadline_remaining_seconds": budget.remaining_seconds,
                        "deadline_classification": self._deadline_classification(budget, decision_at),
                    })
                    outcome = TargetOutcome(
                        target.source_parent_station_id,
                        target.station_name,
                        target.tier,
                        target.phase_index,
                        bucket_index,
                        "SKIPPED",
                        http_attempts=attempts,
                        skip_reason=SKIPPED_HEARTBEAT_BUDGET,
                        error_category=self._deadline_classification(budget, decision_at),
                        timing=target_timing,
                        deadline_outcome=self._deadline_classification(budget, decision_at),
                    )
                    self.persistence.complete_target_audit(
                        audit_id=audit_id,
                        outcome=outcome.to_dict(),
                        completed_at=self._event_time(logical_time=quota_at_utc, collection_context=collection_context),
                    )
                    return outcome

                attempts += 1
                authorization_started_at = self._event_time(
                    logical_time=quota_at_utc,
                    collection_context=collection_context,
                )
                target_timing["network_authorization_started_at_utc"] = authorization_started_at.isoformat().replace(
                    "+00:00", "Z"
                )
                authorization_check_at = authorization_started_at
                if (
                    collection_context == MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT
                ):
                    # The permit was reserved using SQL UTC. Always read that
                    # same authoritative clock again immediately before the
                    # network call, even when a provider-period expiry was not
                    # returned. A missing final clock read is never permission
                    # to transmit.
                    authorization_clock = getattr(self.persistence, "authoritative_utc_now", None)
                    if not callable(authorization_clock):
                        attempts -= 1
                        self.quota_guard.release(quota_decision)
                        target_timing.update({
                            "processed_after_deadline": False,
                            "authorization_clock_recheck_failed": True,
                            "deadline_classification": "AUTHORIZATION_CLOCK_UNAVAILABLE",
                        })
                        outcome = TargetOutcome(
                            target.source_parent_station_id,
                            target.station_name,
                            target.tier,
                            target.phase_index,
                            bucket_index,
                            "SKIPPED",
                            http_attempts=attempts,
                            skip_reason=SKIPPED_QUOTA,
                            error_category="AUTHORIZATION_CLOCK_UNAVAILABLE",
                            timing=target_timing,
                            deadline_outcome="AUTHORIZATION_CLOCK_UNAVAILABLE",
                        )
                        self.persistence.complete_target_audit(
                            audit_id=audit_id,
                            outcome=outcome.to_dict(),
                            completed_at=self._event_time(logical_time=quota_at_utc, collection_context=collection_context),
                        )
                        return outcome
                    authorization_clock_started = self.monotonic_fn()
                    try:
                        authorization_check_at = _parse_utc_timestamp(authorization_clock())
                        if authorization_check_at is None:
                            raise ValueError("authoritative SQL UTC time was invalid")
                    except BaseException:
                        attempts -= 1
                        self.quota_guard.release(quota_decision)
                        target_timing.update({
                            "processed_after_deadline": False,
                            "authorization_clock_recheck_failed": True,
                            "authorization_clock_recheck_duration_ms": max(
                                0.0, (self.monotonic_fn() - authorization_clock_started) * 1000.0
                            ),
                            "deadline_classification": "AUTHORIZATION_CLOCK_UNAVAILABLE",
                        })
                        outcome = TargetOutcome(
                            target.source_parent_station_id,
                            target.station_name,
                            target.tier,
                            target.phase_index,
                            bucket_index,
                            "SKIPPED",
                            http_attempts=attempts,
                            skip_reason=SKIPPED_QUOTA,
                            error_category="AUTHORIZATION_CLOCK_UNAVAILABLE",
                            timing=target_timing,
                            deadline_outcome="AUTHORIZATION_CLOCK_UNAVAILABLE",
                        )
                        self.persistence.complete_target_audit(
                            audit_id=audit_id,
                            outcome=outcome.to_dict(),
                            completed_at=self._event_time(logical_time=quota_at_utc, collection_context=collection_context),
                        )
                        return outcome
                    target_timing["authorization_clock_recheck_at_utc"] = authorization_check_at.isoformat().replace("+00:00", "Z")
                    target_timing["authorization_clock_recheck_duration_ms"] = max(
                        0.0, (self.monotonic_fn() - authorization_clock_started) * 1000.0
                    )
                    target_timing["authorization_clock_source"] = "SQL_SYSUTCDATETIME"
                    local_pre_network_at = self._event_time(
                        logical_time=quota_at_utc,
                        collection_context=collection_context,
                    )
                    target_timing["local_wall_clock_before_transmission_at_utc"] = local_pre_network_at.isoformat().replace(
                        "+00:00", "Z"
                    )
                    # The SQL clock protects quota validity; the local wall
                    # clock and monotonic budget still protect the current UTC
                    # heartbeat boundary. Neither source may extend the
                    # other source's deadline.
                    if local_pre_network_at >= budget.bucket_end_utc:
                        attempts -= 1
                        self.quota_guard.release(quota_decision)
                        target_timing.update({
                            "processed_after_deadline": True,
                            "deadline_classification": LATE_DISPATCH,
                        })
                        outcome = TargetOutcome(
                            target.source_parent_station_id,
                            target.station_name,
                            target.tier,
                            target.phase_index,
                            bucket_index,
                            "SKIPPED",
                            http_attempts=attempts,
                            skip_reason=SKIPPED_HEARTBEAT_BUDGET,
                            error_category=LATE_DISPATCH,
                            timing=target_timing,
                            deadline_outcome=LATE_DISPATCH,
                        )
                        self.persistence.complete_target_audit(
                            audit_id=audit_id,
                            outcome=outcome.to_dict(),
                            completed_at=self._event_time(logical_time=quota_at_utc, collection_context=collection_context),
                        )
                        return outcome
                if quota_decision.valid_until_utc is not None:
                    authorization_expiry = _parse_utc_timestamp(quota_decision.valid_until_utc)
                    if authorization_expiry is None:
                        attempts -= 1
                        self.quota_guard.release(quota_decision)
                        target_timing.update({
                            "processed_after_deadline": False,
                            "authorization_expiry_invalid": True,
                            "deadline_classification": "AUTHORIZATION_EXPIRY_INVALID",
                        })
                        outcome = TargetOutcome(
                            target.source_parent_station_id,
                            target.station_name,
                            target.tier,
                            target.phase_index,
                            bucket_index,
                            "SKIPPED",
                            http_attempts=attempts,
                            skip_reason=SKIPPED_QUOTA,
                            error_category="AUTHORIZATION_EXPIRY_INVALID",
                            timing=target_timing,
                            deadline_outcome="AUTHORIZATION_EXPIRY_INVALID",
                        )
                        self.persistence.complete_target_audit(
                            audit_id=audit_id,
                            outcome=outcome.to_dict(),
                            completed_at=self._event_time(logical_time=quota_at_utc, collection_context=collection_context),
                        )
                        return outcome
                    if authorization_check_at >= authorization_expiry:
                        attempts -= 1
                        self.quota_guard.release(quota_decision)
                        target_timing.update({
                            "processed_after_deadline": authorization_check_at >= budget.bucket_end_utc,
                            "authorization_expired_before_network_at_utc": authorization_check_at.isoformat().replace("+00:00", "Z"),
                            "deadline_classification": "AUTHORIZATION_EXPIRED_BEFORE_NETWORK",
                        })
                        outcome = TargetOutcome(
                            target.source_parent_station_id,
                            target.station_name,
                            target.tier,
                            target.phase_index,
                            bucket_index,
                            "SKIPPED",
                            http_attempts=attempts,
                            skip_reason=SKIPPED_QUOTA,
                            error_category="QUOTA_EXPIRED_BEFORE_NETWORK",
                            timing=target_timing,
                            deadline_outcome=AUTHORIZATION_EXPIRED_BEFORE_NETWORK,
                        )
                        self.persistence.complete_target_audit(
                            audit_id=audit_id,
                            outcome=outcome.to_dict(),
                            completed_at=self._event_time(logical_time=quota_at_utc, collection_context=collection_context),
                        )
                        return outcome
                if authorization_check_at >= budget.bucket_end_utc:
                    attempts -= 1
                    self.quota_guard.release(quota_decision)
                    target_timing.update({
                        "processed_after_deadline": True,
                        "deadline_classification": self._deadline_classification(budget, authorization_check_at),
                    })
                    outcome = TargetOutcome(
                        target.source_parent_station_id,
                        target.station_name,
                        target.tier,
                        target.phase_index,
                        bucket_index,
                        "SKIPPED",
                        http_attempts=attempts,
                        skip_reason=SKIPPED_HEARTBEAT_BUDGET,
                        error_category=LATE_DISPATCH,
                        timing=target_timing,
                        deadline_outcome=LATE_DISPATCH,
                    )
                    self.persistence.complete_target_audit(
                        audit_id=audit_id,
                        outcome=outcome.to_dict(),
                        completed_at=self._event_time(logical_time=quota_at_utc, collection_context=collection_context),
                    )
                    return outcome
                if not budget.can_start(operation_seconds=self.request_timeout_seconds):
                    attempts -= 1
                    self.quota_guard.release(quota_decision)
                    classification = self._deadline_classification(budget, authorization_check_at)
                    target_timing.update({
                        "processed_after_deadline": budget.deadline_reached,
                        "deadline_remaining_seconds": budget.remaining_seconds,
                        "deadline_classification": classification,
                    })
                    outcome = TargetOutcome(
                        target.source_parent_station_id,
                        target.station_name,
                        target.tier,
                        target.phase_index,
                        bucket_index,
                        "SKIPPED",
                        http_attempts=attempts,
                        skip_reason=SKIPPED_HEARTBEAT_BUDGET,
                        error_category=classification,
                        timing=target_timing,
                        deadline_outcome=classification,
                    )
                    self.persistence.complete_target_audit(
                        audit_id=audit_id,
                        outcome=outcome.to_dict(),
                        completed_at=self._event_time(logical_time=quota_at_utc, collection_context=collection_context),
                    )
                    return outcome
                # This timestamp is captured immediately before invoking the
                # client.  It is intentionally separate from SQL
                # authorization time and quota reservation time.
                attempt_started = self._event_time(
                    logical_time=quota_at_utc,
                    collection_context=collection_context,
                )
                target_timing["network_attempt_started_at_utc"] = attempt_started.isoformat().replace(
                    "+00:00", "Z"
                )
                target_timing["network_attempt_started_after_bucket_boundary"] = attempt_started >= budget.bucket_end_utc
                if attempt_started >= budget.bucket_end_utc:
                    attempts -= 1
                    self.quota_guard.release(quota_decision)
                    target_timing.update({
                        "processed_after_deadline": True,
                        "deadline_classification": LATE_DISPATCH,
                    })
                    outcome = TargetOutcome(
                        target.source_parent_station_id,
                        target.station_name,
                        target.tier,
                        target.phase_index,
                        bucket_index,
                        "SKIPPED",
                        http_attempts=attempts,
                        skip_reason=SKIPPED_HEARTBEAT_BUDGET,
                        error_category=LATE_DISPATCH,
                        timing=target_timing,
                        deadline_outcome=LATE_DISPATCH,
                    )
                    self.persistence.complete_target_audit(
                        audit_id=audit_id,
                        outcome=outcome.to_dict(),
                        completed_at=self._event_time(logical_time=quota_at_utc, collection_context=collection_context),
                    )
                    return outcome
                attempt_monotonic = self.monotonic_fn()
                try:
                    response_or_failure = self.mdd_client.fetch(
                        target,
                        request_timestamp_utc=attempt_started,
                        timeout_seconds=self.request_timeout_seconds,
                        attempt_number=attempts,
                    )
                except Exception as exc:
                    # A live client exception without explicit pre-network
                    # metadata is ambiguous after entering ``fetch``.  Keep
                    # its permit conservative; an offline adapter remains a
                    # proven no-network path.
                    response_or_failure = _client_execution_failure(exc, mdd_client=self.mdd_client)
                attempt_completed = self._event_time(
                    logical_time=quota_at_utc,
                    collection_context=collection_context,
                )
                target_timing["network_attempt_completed_at_utc"] = attempt_completed.isoformat().replace(
                    "+00:00", "Z"
                )
                target_timing["network_attempt_duration_ms"] = max(
                    0.0, (self.monotonic_fn() - attempt_monotonic) * 1000.0
                )
                if attempt_completed >= budget.bucket_end_utc and attempt_started < budget.bucket_end_utc:
                    target_timing["in_flight_operation_overrun"] = True
                    target_timing["deadline_classification"] = "IN_FLIGHT_OPERATION_OVERRUN"
                network_attempted, _http_response_received, transport_kind, live_client_invocation = _transport_metadata(response_or_failure)
                if network_attempted:
                    live_network_calls += 1
                if live_client_invocation:
                    live_client_invocations += 1
                elif transport_kind == "SIMULATED_TRANSPORT":
                    simulated_transport_calls += 1
                if isinstance(response_or_failure, TransportFailure):
                    error_category = response_or_failure.category
                    self._record_attempt_with_permit(
                        audit_id=audit_id,
                        attempt_number=attempts,
                        started_at=attempt_started,
                        completed_at=attempt_completed,
                        transport_failure=response_or_failure,
                        retry_decision="RETRY_CANDIDATE" if retry_eligible(transport_failure=response_or_failure) and attempts < self.max_attempts else "NO_RETRY",
                        transport_kind=transport_kind,
                        live_client_invocation=live_client_invocation,
                        quota_decision=quota_decision,
                    )
                    if retry_eligible(transport_failure=response_or_failure) and attempts < self.max_attempts:
                        retry_decision = "RETRY_CANDIDATE"
                        retry_outcome = "RETRY_PENDING"
                        continue
                    break

                response = response_or_failure
                last_status = response.status_code
                retry_after = _retry_after_seconds(response)
                pending_retry_after = retry_after
                can_retry = retry_eligible(response=response) and attempts < self.max_attempts
                self._record_attempt_with_permit(
                    audit_id=audit_id,
                    attempt_number=attempts,
                    started_at=attempt_started,
                    completed_at=attempt_completed,
                    response=response,
                    retry_decision="RETRY_CANDIDATE" if can_retry else "NO_RETRY",
                    retry_after_seconds=retry_after or None,
                    transport_kind=transport_kind,
                    live_client_invocation=live_client_invocation,
                    quota_decision=quota_decision,
                )
                if response.status_code is None or not 200 <= response.status_code < 300:
                    error_category = _error_category_for_response(response)
                    if response.body:
                        self._retain_live_response(
                            audit_id=audit_id,
                            run_id=run_id,
                            target=target,
                            bucket_index=bucket_index,
                            response=response,
                            reason="HTTP_PROVIDER_ERROR_RESPONSE",
                            collection_context=collection_context,
                        )
                    if can_retry:
                        if not budget.can_start(operation_seconds=self.request_timeout_seconds, sleep_seconds=retry_after):
                            retry_decision = "RETRY_SKIPPED_HEARTBEAT_BUDGET"
                            retry_outcome = RETRY_SKIPPED_HEARTBEAT_BUDGET
                            break
                        retry_decision = "RETRY_CANDIDATE"
                        retry_outcome = "RETRY_PENDING"
                        continue
                    break
                try:
                    parse_started = self.monotonic_fn()
                    parse_started_at = self._event_time(
                        logical_time=quota_at_utc,
                        collection_context=collection_context,
                    )
                    target_timing["response_parsing_started_at_utc"] = parse_started_at.isoformat().replace(
                        "+00:00", "Z"
                    )
                    parsed = parse_trias_response(response.body, self._content_type(response))
                    parse_completed_at = self._event_time(
                        logical_time=quota_at_utc,
                        collection_context=collection_context,
                    )
                    target_timing["response_parsing_completed_at_utc"] = parse_completed_at.isoformat().replace(
                        "+00:00", "Z"
                    )
                    target_timing["response_parsing_duration_ms"] = max(
                        0.0, (self.monotonic_fn() - parse_started) * 1000.0
                    )
                except (TriasParserError, ValueError):
                    parser_outcome = "PARSER_FAILURE"
                    error_category = "PARSER_FAILURE"
                    self._retain_live_response(
                        audit_id=audit_id,
                        run_id=run_id,
                        target=target,
                        bucket_index=bucket_index,
                        response=response,
                        reason="PARSER_FAILURE",
                        collection_context=collection_context,
                    )
                    break
                parser_outcome = "TRIAS_APPLICATION_ERROR" if parsed.application_error else "PARSED"
                if parsed.application_error:
                    error_category = "TRIAS_APPLICATION_ERROR"
                if attempts > 1:
                    retry_outcome = "RETRY_SUCCEEDED"
                result_count = parsed.result_count
                try:
                    persistence_started = self.monotonic_fn()
                    persistence_started_at = self._event_time(
                        logical_time=quota_at_utc,
                        collection_context=collection_context,
                    )
                    target_timing["snapshot_persistence_started_at_utc"] = persistence_started_at.isoformat().replace(
                        "+00:00", "Z"
                    )
                    persisted_at = self._event_time(
                        logical_time=quota_at_utc,
                        collection_context=collection_context,
                    )
                    self.persistence.persist_snapshot(
                        audit_id=audit_id,
                        target=target,
                        logical_due_bucket=bucket_index,
                        parsed_snapshot=parsed.to_dict(),
                        persisted_at=persisted_at,
                        collection_context=collection_context,
                    )
                    snapshot_persisted_at = self._event_time(
                        logical_time=quota_at_utc,
                        collection_context=collection_context,
                    )
                    target_timing["snapshot_persisted_at_utc"] = snapshot_persisted_at.isoformat().replace(
                        "+00:00", "Z"
                    )
                    target_timing["http_to_snapshot_start_delay_ms"] = max(
                        0.0,
                        (persistence_started_at - attempt_completed).total_seconds() * 1000.0,
                    )
                    target_timing["http_to_snapshot_persisted_duration_ms"] = max(
                        0.0,
                        (snapshot_persisted_at - attempt_completed).total_seconds() * 1000.0,
                    )
                    target_timing["snapshot_persistence_duration_ms"] = max(
                        0.0, (self.monotonic_fn() - persistence_started) * 1000.0
                    )
                    persistence_outcome = "PERSISTED_PARSED_SNAPSHOT"
                    reasons: list[str] = []
                    if parsed.recorded_at_time_occurrences:
                        reasons.append("SOURCE_RECORDED_AT_TIME")
                    if parsed.parser_gaps:
                        reasons.append("PARSER_GAP")
                    if parsed.application_error:
                        reasons.append("TRIAS_APPLICATION_ERROR")
                    if reasons:
                        self._retain_live_response(
                            audit_id=audit_id,
                            run_id=run_id,
                            target=target,
                            bucket_index=bucket_index,
                            response=response,
                            reason="+".join(reasons),
                            collection_context=collection_context,
                        )
                except BaseException:
                    persistence_outcome = "PERSISTENCE_FAILURE"
                    error_category = "PERSISTENCE_FAILURE"
                    break
                break

            status = "SUCCEEDED" if parser_outcome == "PARSED" and persistence_outcome == "PERSISTED_PARSED_SNAPSHOT" else ("SKIPPED" if retry_outcome in {RETRY_SKIPPED_HEARTBEAT_BUDGET, RETRY_SKIPPED_QUOTA} or attempts == 0 else "FAILED")
            completed_at = self._event_time(logical_time=quota_at_utc, collection_context=collection_context)
            target_timing["target_audit_finalization_duration_ms"] = 0.0
            target_timing["target_completed_at_utc"] = completed_at.isoformat().replace("+00:00", "Z")
            target_timing["target_duration_ms"] = max(0.0, (completed_at - started).total_seconds() * 1000.0)
            deadline_outcome = (
                IN_FLIGHT_OPERATION_OVERRUN
                if target_timing.get("in_flight_operation_overrun")
                else None
            )
            outcome = TargetOutcome(
                target.source_parent_station_id,
                target.station_name,
                target.tier,
                target.phase_index,
                bucket_index,
                status,
                http_status=last_status,
                http_attempts=attempts,
                stop_event_result_count=result_count,
                parser_outcome=parser_outcome,
                persistence_outcome=persistence_outcome,
                duration_seconds=(completed_at - started).total_seconds(),
                error_category=error_category,
                retry_decision=retry_decision,
                retry_outcome=retry_outcome,
                skip_reason=retry_outcome if status == "SKIPPED" else None,
                simulated_transport_calls=simulated_transport_calls,
                live_network_calls=live_network_calls,
                live_client_invocations=live_client_invocations,
                timing=target_timing,
                deadline_outcome=deadline_outcome,
            )
            finalize_started = self.monotonic_fn()
            finalize_started_at = self._event_time(
                logical_time=quota_at_utc,
                collection_context=collection_context,
            )
            target_timing["target_audit_finalization_started_at_utc"] = finalize_started_at.isoformat().replace(
                "+00:00", "Z"
            )
            self.persistence.complete_target_audit(audit_id=audit_id, outcome=outcome.to_dict(), completed_at=completed_at)
            finalize_completed_at = self._event_time(
                logical_time=quota_at_utc,
                collection_context=collection_context,
            )
            target_timing["target_audit_finalization_duration_ms"] = max(
                0.0, (self.monotonic_fn() - finalize_started) * 1000.0
            )
            target_timing["target_audit_finalization_completed_at_utc"] = finalize_completed_at.isoformat().replace(
                "+00:00", "Z"
            )
            return outcome
        except BaseException:
            if audit_id is not None:
                try:
                    self.persistence.complete_target_audit(audit_id=audit_id, outcome={"status": "FAILED", "http_attempts": attempts, "error_category": "SANITIZED_ERROR", "persistence_outcome": "PERSISTENCE_FAILURE", "timing": target_timing}, completed_at=self._event_time(logical_time=quota_at_utc, collection_context=collection_context))
                except BaseException:
                    pass
            return TargetOutcome(target.source_parent_station_id, target.station_name, target.tier, target.phase_index, bucket_index, "FAILED", http_attempts=attempts, error_category="SANITIZED_ERROR", simulated_transport_calls=simulated_transport_calls, live_network_calls=live_network_calls, live_client_invocations=live_client_invocations, timing=target_timing)
        finally:
            self._leave_worker()

    def _retain_live_response(
        self,
        *,
        audit_id: Any,
        run_id: str,
        target: Target,
        bucket_index: int,
        response: HttpResponse,
        reason: str,
        collection_context: str,
    ) -> None:
        if collection_context != LIVE_RESEARCH_COLLECTION_CONTEXT:
            return
        retention_hook = getattr(self.persistence, "retain_raw_evidence", None)
        if not callable(retention_hook):
            return
        retention_hook(
            audit_id=audit_id,
            run_id=run_id,
            target=target,
            logical_due_bucket=bucket_index,
            body=response.body,
            reason=reason,
            content_type=self._content_type(response),
            response_status=response.status_code,
            observed_at=datetime.now(timezone.utc),
        )

    def _record_attempt_with_permit(
        self,
        *,
        audit_id: Any,
        attempt_number: int,
        started_at: datetime,
        completed_at: datetime,
        response: HttpResponse | None = None,
        transport_failure: TransportFailure | None = None,
        retry_decision: str | None = None,
        retry_after_seconds: float | None = None,
        transport_kind: str = "UNKNOWN",
        live_client_invocation: bool = False,
        quota_decision: QuotaDecision | None,
    ) -> None:
        result = response if response is not None else transport_failure
        if result is None:
            finalization = RELEASED_NO_NETWORK
        else:
            finalization = _quota_finalization_for_result(result)
        kwargs: dict[str, Any] = {
            "audit_id": audit_id,
            "attempt_number": attempt_number,
            "started_at": started_at,
            "completed_at": completed_at,
            "response": response,
            "transport_failure": transport_failure,
            "retry_decision": retry_decision,
            "retry_after_seconds": retry_after_seconds,
            "transport_kind": transport_kind,
            "live_client_invocation": live_client_invocation,
        }
        atomic_recorder = getattr(self.persistence, "record_http_attempt_and_finalize_quota", None)
        atomic_supported = bool(
            getattr(self.persistence, "supports_atomic_quota_finalization", False)
            or self.quota_guard.usage_provider is self.persistence
        )
        if callable(atomic_recorder) and atomic_supported:
            atomic_recorder(
                **kwargs,
                quota_permit_id=quota_decision.permit_id if quota_decision else None,
                quota_finalization=finalization,
                finalized_at=completed_at,
            )
            return

        # An adapter with separate usage and persistence ports must retain the
        # pending permit until the attempt row is durable. If the process dies
        # here, the permit remains conservatively unavailable rather than
        # being returned to the headroom pool.
        self.persistence.record_http_attempt(**kwargs)
        if finalization == HTTP_PROVIDER_ATTEMPT_CONFIRMED:
            self.quota_guard.finalize_http_response(
                quota_decision,
                live_client_invocation=live_client_invocation,
            )
        elif finalization == CONSERVATIVE_LIVE_ATTEMPT:
            self.quota_guard.finalize_conservative(
                quota_decision,
                live_client_invocation=live_client_invocation,
            )
        else:
            self.quota_guard.finalize_no_network(
                quota_decision,
                live_client_invocation=live_client_invocation,
            )

    @staticmethod
    def _content_type(response: HttpResponse) -> str | None:
        for key, value in response.headers.items():
            if key.lower() == "content-type":
                return value
        return None

    def _record_quota(
        self,
        run_id: str,
        target: Target,
        bucket_index: int,
        kind: str,
        decision: Mapping[str, Any],
        at: datetime,
        collection_context: str,
    ) -> None:
        recorder = getattr(self.persistence, "record_quota_decision", None)
        if callable(recorder):
            recorder(
                run_id=run_id,
                source_parent_station_id=target.source_parent_station_id,
                logical_due_bucket=bucket_index,
                decision_kind=kind,
                decision=decision,
                recorded_at=at,
                collection_context=collection_context,
            )

def build_logger(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
