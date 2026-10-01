"""Thin command-line entry point for one scheduler-neutral heartbeat."""

from __future__ import annotations

import argparse
import json
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

from .collector import Collector, LiveModeBlocked
from .configuration import CollectorSettings
from .manifest import RuntimeManifest, load_default_manifest
from .persistence import SQLitePersistence
from .quota import QuotaGuard
from .simulation import SCENARIO_NAMES, run_simulation
from .transport import FixtureMddClient


def _parse_utc(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("--at must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run or inspect one offline-safe Stage 14 collector heartbeat.")
    parser.add_argument("mode", nargs="?", choices=("plan", "dry-run", "simulate", "live"), default="plan")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--state-path", type=Path)
    parser.add_argument("--at", dest="at_utc", help="UTC ISO-8601 time for deterministic offline validation")
    parser.add_argument("--fault", choices=SCENARIO_NAMES, default="all-success")
    parser.add_argument("--safety-margin-seconds", type=float)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--allow-live", action="store_true", help="reserved explicit opt-in; live activation is still blocked")
    return parser


def _load_manifest(args: argparse.Namespace, root: Path) -> RuntimeManifest:
    manifest_path = args.manifest or root / "config" / "tiered50_runtime_manifest.json"
    evidence_path = args.evidence or root / "docs" / "stages" / "13-tiered-collector-readiness-evidence.json"
    return RuntimeManifest.load(manifest_path, stage13_evidence_path=evidence_path)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    try:
        manifest = _load_manifest(args, root)
        at_utc = _parse_utc(args.at_utc)
        if args.mode == "plan":
            from .schedule import plan_for, schedule_reconciliation

            plan = plan_for(manifest.targets, at_utc or datetime.now(timezone.utc))
            print(json.dumps({
                "mode": "plan",
                "panel_version": manifest.panel_version,
                "sampling_era": manifest.sampling_era.value,
                "activation_status": manifest.activation_status,
                "logical_due_bucket": plan.bucket.index,
                "logical_due_bucket_identity": plan.bucket.identity,
                "due_count": plan.due_count,
                "due_targets": [
                    {
                        "source_parent_station_id": target.source_parent_station_id,
                        "station_display_name": target.station_name,
                        "tier": target.tier,
                        "phase_index": target.phase_index,
                        "number_of_results": target.number_of_results,
                    }
                    for target in plan.due_targets
                ],
                "schedule_reconciliation": schedule_reconciliation(manifest.targets),
                "simulated_transport_calls": 0,
                "live_network_calls": 0,
            }, ensure_ascii=False, sort_keys=True))
            return 0

        if args.mode == "live":
            raise LiveModeBlocked(
                "live mode is blocked: no production persistence adapter, cutover timestamp, or activation exists in Stage 14"
            )

        settings = CollectorSettings.for_repository(
            root,
            state_path=args.state_path,
            safety_margin_seconds=args.safety_margin_seconds,
        )
        if args.mode == "simulate":
            settings.state_path.parent.mkdir(parents=True, exist_ok=True)
            print(json.dumps(run_simulation(manifest, settings.state_path, scenario=args.fault, at_utc=at_utc), ensure_ascii=False, sort_keys=True, default=str))
            return 0

        persistence = SQLitePersistence(settings.state_path)
        try:
            persistence.materialize_manifest(manifest)
            persistence.record_sampling_era(era_code="Tiered50", panel_version=manifest.panel_version, timestamp_kind="NOT_RECORDED", is_active=False)
            fixture = root / "tests" / "fixtures" / "stage14_trias_success.json"
            collector = Collector(
                manifest=manifest,
                persistence=persistence,
                mdd_client=FixtureMddClient(fixture),
                quota_guard=QuotaGuard(persistence, reservation_scope="OFFLINE_DRY_RUN"),
                safety_margin_seconds=settings.safety_margin_seconds,
            )
            previous_handlers = {}
            for signal_name in ("SIGINT", "SIGTERM"):
                signal_number = getattr(signal, signal_name, None)
                if signal_number is not None:
                    previous_handlers[signal_number] = signal.getsignal(signal_number)
                    signal.signal(signal_number, lambda _signum, _frame: collector.request_shutdown())
            try:
                result = collector.run_once(mode="dry-run", at_utc=at_utc)
            finally:
                for signal_number, handler in previous_handlers.items():
                    signal.signal(signal_number, handler)
            print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True, default=str))
            return 0
        finally:
            persistence.close()
    except LiveModeBlocked as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "simulated_transport_calls": 0, "live_network_calls": 0}), file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "ERROR", "reason": str(exc)}), file=sys.stderr)
        return 1
