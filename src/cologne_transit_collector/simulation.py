"""Deterministic offline fault-injection scenarios for Stage 14."""

from __future__ import annotations

import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .collector import Collector
from .domain import HttpResponse, TransportFailure
from .manifest import RuntimeManifest
from .persistence import SQLitePersistence
from .quota import InMemoryUsageProvider, QuotaGuard, UsageSnapshot
from .transport import FakeMddClient


SCENARIO_NAMES = (
    "all-success",
    "http-500-then-success",
    "http-500-twice",
    "http-401",
    "http-403",
    "http-429-retry-after-fits",
    "http-429-retry-after-exceeds-budget",
    "transport-timeout",
    "parser-failure",
    "persistence-failure",
    "quota-retry-denied",
    "heartbeat-budget-initial-skip",
    "heartbeat-budget-retry-skip",
    "duplicate-bucket",
    "concurrent-duplicate",
    "crash-after-claim",
    "stale-claim-visible",
    "failure-isolation",
)


def _success_body(path: str | Path | None = None) -> bytes:
    fixture = Path(path) if path else Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "stage14_trias_success.json"
    return fixture.read_bytes()


class FailingSnapshotPersistence:
    """Delegating fault injector that fails only parsed snapshot persistence."""

    is_production_ready = False
    is_disposable_development_backend = True

    def __init__(self, delegate: SQLitePersistence) -> None:
        self.delegate = delegate

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    def persist_snapshot(self, **kwargs: Any) -> None:
        raise RuntimeError("simulated persistence failure")


def _client_for(name: str, manifest: RuntimeManifest) -> tuple[FakeMddClient, InMemoryUsageProvider]:
    first = sorted(manifest.targets, key=lambda target: target.source_parent_station_id)[0]
    success = HttpResponse(200, _success_body(), {"Content-Type": "application/json"})
    scripts: dict[str, list[object]] = {}
    provider = InMemoryUsageProvider()
    if name == "http-500-then-success":
        scripts[first.source_parent_station_id] = [HttpResponse(500, b"", {}), success]
    elif name == "http-500-twice":
        scripts[first.source_parent_station_id] = [HttpResponse(500, b"", {}), HttpResponse(500, b"", {})]
    elif name == "http-401":
        scripts[first.source_parent_station_id] = [HttpResponse(401, b"", {})]
    elif name == "http-403":
        scripts[first.source_parent_station_id] = [HttpResponse(403, b"", {})]
    elif name == "http-429-retry-after-fits":
        scripts[first.source_parent_station_id] = [HttpResponse(429, b"", {"Retry-After": "1"}), success]
    elif name == "http-429-retry-after-exceeds-budget":
        scripts[first.source_parent_station_id] = [HttpResponse(429, b"", {"Retry-After": "300"}), success]
    elif name == "transport-timeout":
        scripts[first.source_parent_station_id] = [TransportFailure("TIMEOUT", "simulated"), success]
    elif name == "parser-failure":
        scripts[first.source_parent_station_id] = [HttpResponse(200, b"not-json-or-xml", {"Content-Type": "application/json"})]
    elif name == "quota-retry-denied":
        scripts[first.source_parent_station_id] = [HttpResponse(500, b"", {}), success]
        provider = InMemoryUsageProvider(UsageSnapshot(confirmed_provider_attempts=207_500))
    elif name == "heartbeat-budget-retry-skip":
        scripts[first.source_parent_station_id] = [HttpResponse(429, b"", {"Retry-After": "300"}), success]
    elif name == "failure-isolation":
        scripts[first.source_parent_station_id] = [HttpResponse(500, b"", {}), HttpResponse(500, b"", {})]
    return FakeMddClient(scripts, default_response=success), provider


def build_simulation(
    manifest: RuntimeManifest,
    state_path: str | Path,
    *,
    scenario: str = "all-success",
    at_utc: datetime | None = None,
) -> tuple[Collector, FakeMddClient, SQLitePersistence, datetime]:
    """Build an offline simulation and transfer persistence ownership.

    Callers that use this lower-level factory must close the returned
    ``SQLitePersistence``. Use ``run_simulation`` when the result-only helper
    should own and close the state automatically.
    """

    if scenario not in SCENARIO_NAMES:
        raise ValueError(f"unknown simulation scenario: {scenario}")
    now = (at_utc or datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)).astimezone(timezone.utc)
    client, provider = _client_for(scenario, manifest)
    persistence: SQLitePersistence = SQLitePersistence(state_path)
    persistence.materialize_manifest(manifest)
    persistence.set_shared_usage(
        month_key=now.strftime("%Y-%m"),
        confirmed_provider_attempts=provider.snapshot.confirmed_provider_attempts,
        reserved_shared_usage=provider.snapshot.reserved_shared_usage,
        projected_non_retry_attempts=provider.snapshot.projected_non_retry_attempts,
        conservative_live_attempts=provider.snapshot.conservative_live_attempts,
        live_client_invocations=provider.snapshot.live_client_invocations,
        http_responses_received=provider.snapshot.http_responses_received,
        provider_quota_unknown_attempts=provider.snapshot.provider_quota_unknown_attempts,
        state_available=provider.snapshot.state_available,
        source=provider.snapshot.source,
    )
    effective_persistence: Any = FailingSnapshotPersistence(persistence) if scenario == "persistence-failure" else persistence
    if scenario == "heartbeat-budget-initial-skip":
        now = now.replace(minute=4, second=59, microsecond=999000)
    if scenario == "heartbeat-budget-retry-skip":
        now = now.replace(second=0, microsecond=0)
    collector = Collector(
        manifest=manifest,
        persistence=effective_persistence,
        mdd_client=client,
        quota_guard=QuotaGuard(persistence, reservation_scope="OFFLINE_SIMULATION"),
        safety_margin_seconds=5.0,
        sleep_fn=lambda _seconds: None,
    )
    return collector, client, persistence, now


def run_simulation(
    manifest: RuntimeManifest,
    state_path: str | Path,
    *,
    scenario: str = "all-success",
    at_utc: datetime | None = None,
) -> dict[str, Any]:
    """Run an owned simulation and always close its SQLite state.

    ``build_simulation`` intentionally transfers persistence ownership to its
    caller for interactive/test use. This convenience function owns the
    internally created instance and therefore closes it on success or error.
    """

    collector, client, persistence, now = build_simulation(manifest, state_path, scenario=scenario, at_utc=at_utc)
    try:
        return _run_simulation_context(
            manifest=manifest,
            scenario=scenario,
            collector=collector,
            client=client,
            persistence=persistence,
            now=now,
        )
    finally:
        persistence.close()


def _run_simulation_context(
    *,
    manifest: RuntimeManifest,
    scenario: str,
    collector: Collector,
    client: FakeMddClient,
    persistence: SQLitePersistence,
    now: datetime,
) -> dict[str, Any]:
    if scenario == "duplicate-bucket":
        first = collector.run_once(mode="simulate", at_utc=now, run_id="simulation-first")
        second = collector.run_once(mode="simulate", at_utc=now, run_id="simulation-duplicate")
        result = {"first": first.to_dict(), "second": second.to_dict()}
    elif scenario == "concurrent-duplicate":
        results: list[dict[str, Any]] = []

        def invoke(run_id: str) -> None:
            results.append(collector.run_once(mode="simulate", at_utc=now, run_id=run_id).to_dict())

        threads = [threading.Thread(target=invoke, args=(f"simulation-concurrent-{i}",)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        result = {"runs": results}
    elif scenario == "crash-after-claim":
        plan = collector.plan(at_utc=now)
        run_id = "simulation-crashed-before-completion"
        persistence.start_collector_run(run_id=run_id, panel_version=manifest.panel_version, logical_due_bucket=plan.bucket.index, mode="simulate", started_at=now)
        claim = persistence.claim_target(run_id=run_id, target=plan.due_targets[0], logical_due_bucket=plan.bucket.index, claimed_at=now)
        result = {"claim_won": claim.won, "restart": collector.run_once(mode="simulate", at_utc=now, run_id="simulation-after-restart").to_dict()}
    elif scenario == "stale-claim-visible":
        plan = collector.plan(at_utc=now)
        run_id = "simulation-stale-claim"
        persistence.start_collector_run(run_id=run_id, panel_version=manifest.panel_version, logical_due_bucket=plan.bucket.index, mode="simulate", started_at=now)
        old = now.replace(year=2020)
        persistence.claim_target(run_id=run_id, target=plan.due_targets[0], logical_due_bucket=plan.bucket.index, claimed_at=old)
        result = {"stale_claims": persistence.list_stale_claims(now_utc=now)}
    else:
        run = collector.run_once(mode="simulate", at_utc=now, run_id=f"simulation-{scenario}")
        result = run.to_dict()
    result["scenario"] = scenario
    result["simulated_transport_calls"] = len(client.calls)
    result["live_network_calls"] = 0
    result["stale_claim_count"] = len(persistence.list_stale_claims(now_utc=now))
    return result
