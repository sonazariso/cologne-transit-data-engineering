#!/usr/bin/env python3
"""Offline Stage 15A validation/readiness analyzer.

This command reads only the approved manifest, the version-controlled
validation contract, and optional evidence through a read-only adapter.  It
does not send MDD requests, perform DNS/live-network activity, use
PostgreSQL, claim buckets, mutate target state, finalize quota permits, or
write parsed snapshots.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from cologne_transit_collector.manifest import RuntimeManifest  # noqa: E402
from cologne_transit_stage15.analyzer import Stage15Analyzer  # noqa: E402
from cologne_transit_stage15.evidence import SQLiteEvidenceAdapter  # noqa: E402


def _parse_utc(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamps must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the offline-safe Stage 15A validation/readiness analyzer."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPOSITORY_ROOT / "config" / "tiered50_runtime_manifest.json",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        help="Optional Stage 14 SQLite evidence file; it is opened read-only.",
    )
    parser.add_argument(
        "--contract",
        type=Path,
        default=REPOSITORY_ROOT / "config" / "stage15_validation_contract.json",
    )
    parser.add_argument("--window-start", type=str)
    parser.add_argument("--window-end", type=str)
    parser.add_argument(
        "--simulate",
        metavar="SCENARIO",
        help="Create deterministic temporary Stage 14 simulation evidence, then analyze it offline.",
    )
    parser.add_argument("--pretty", action="store_true", help="Indent the authoritative JSON report.")
    parser.add_argument("--output", type=Path, help="Optional path for a JSON copy of the report.")
    return parser


def _load_manifest(path: Path) -> RuntimeManifest:
    evidence = REPOSITORY_ROOT / "docs" / "stages" / "13-tiered-collector-readiness-evidence.json"
    return RuntimeManifest.load(path, stage13_evidence_path=evidence)


def _analyze(args: argparse.Namespace) -> dict[str, Any]:
    manifest = _load_manifest(args.manifest)
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    start = _parse_utc(args.window_start)
    end = _parse_utc(args.window_end)
    adapter = SQLiteEvidenceAdapter(args.evidence) if args.evidence else None

    if args.simulate and args.evidence:
        raise ValueError("--simulate and --evidence are mutually exclusive")
    if args.simulate:
        # The collector simulation is an explicit deterministic fixture path.
        # The analyzer itself still consumes the resulting SQLite file through
        # the same read-only adapter as a supplied evidence file.
        from cologne_transit_collector.simulation import run_simulation

        with tempfile.TemporaryDirectory(prefix="stage15-validation-") as directory:
            state_path = Path(directory) / "simulation.sqlite3"
            run_simulation(manifest, state_path, scenario=args.simulate, at_utc=start)
            adapter = SQLiteEvidenceAdapter(state_path)
            analyzer = Stage15Analyzer(
                manifest,
                contract=contract,
                evidence_adapter=adapter,
                window_start_utc=start,
                window_end_utc=end,
            )
            return analyzer.analyze()

    analyzer = Stage15Analyzer(
        manifest,
        contract=contract,
        evidence_adapter=adapter,
        window_start_utc=start,
        window_end_utc=end,
    )
    return analyzer.analyze()


def run_checks(argv: list[str] | None = None) -> dict[str, Any]:
    """Return the machine-readable report for tests and callers."""

    args = _parser().parse_args(argv)
    return _analyze(args)


def main(argv: list[str] | None = None) -> int:
    requested_args = argv if argv is not None else sys.argv[1:]
    args = _parser().parse_args(requested_args)
    try:
        report = _analyze(args)
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        report = {
            "schema_version": "stage15-validation-report-1.0",
            "stage": "15A",
            "status": "FAIL",
            "stage15_live_status": "NOT STARTED — AWAITS TIERED50 CUTOVER",
            "error": {"type": type(exc).__name__, "reason": str(exc)},
        }
    indent = 2 if "--pretty" in requested_args else None
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=indent, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    # A missing live evidence adapter is an expected readiness limitation and
    # therefore does not make the offline command fail. Structural failures do.
    return 1 if report.get("status") == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
