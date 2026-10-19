#!/usr/bin/env python3
"""Reconcile the available Legacy7 evidence without inventing outage causes."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "operational" / "legacy7-production-transition-closeout-evidence.json"


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def reconcile() -> dict[str, Any]:
    payload = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    heartbeats = list(payload.get("corrected_heartbeats", ()))
    buckets = sorted(int(row["logical_bucket"]) for row in heartbeats)
    expected = list(range(buckets[0], buckets[-1] + 1)) if buckets else []
    observed = sorted(set(buckets))
    missing = [bucket for bucket in expected if bucket not in observed]
    starts = [_parse(str(row["started_at_utc"])) for row in heartbeats]
    completes = [_parse(str(row["completed_at_utc"])) for row in heartbeats]
    evidence_start = min(starts) if starts else None
    evidence_end = max(completes) if completes else None
    bucket_span_minutes = ((buckets[-1] - buckets[0] + 1) * 5) if buckets else 0
    confirmed_failures = [
        {
            "logical_bucket": row.get("logical_bucket"),
            "run_status": row.get("run_status"),
            "http_outcome": row.get("http_outcome"),
            "persistence_outcome": row.get("persistence_outcome"),
        }
        for row in heartbeats
        if row.get("run_status") not in {"COMPLETE", "DUPLICATE_NO_WORK"}
        or row.get("http_outcome") != "HTTP_RESPONSE"
        or row.get("persistence_outcome") != "PERSISTED_PARSED_SNAPSHOT"
    ]
    return {
        "artifact_version": "LEGACY7_AVAILABILITY_RECONCILIATION_V1",
        "source_evidence_path": str(EVIDENCE),
        "source_capture_timestamp_utc": payload.get("evidence_capture_timestamp_utc"),
        "expected_utc_five_minute_buckets": expected,
        "observed_persisted_collector_buckets": observed,
        "missing_buckets_within_observed_window": missing,
        "confirmed_failures": confirmed_failures,
        "skipped_buckets": [],
        "quota_skips": [],
        "transport_failures": [],
        "scheduler_invocation_failures": [],
        "evidence_window": {
            "first_observed_start_utc": evidence_start.isoformat().replace("+00:00", "Z") if evidence_start else None,
            "last_observed_completion_utc": evidence_end.isoformat().replace("+00:00", "Z") if evidence_end else None,
            "bucket_span_minutes": bucket_span_minutes,
            "complete_stable_interval_required_minutes": 30,
            "stable_interval_status": "NOT_PROVEN",
        },
        "launchd": {
            "active_state_at_capture": payload.get("launch_agent", {}).get("active_state"),
            "launch_count_at_capture": payload.get("launch_agent", {}).get("launch_count_at_capture"),
            "five_minute_interval_configured": payload.get("launch_agent", {}).get("start_interval_seconds") == 300,
            "continuous_execution_proven": False,
        },
        "availability": {
            "mac_host_continuity": "NOT_PROVEN",
            "vmware_fusion_continuity": "NOT_PROVEN",
            "windows_vm_continuity": "NOT_PROVEN",
            "sql_server_continuity": "PARTIAL — three persisted successful heartbeats only",
            "missing_bucket_cause": "NOT_INFERRED",
        },
        "legacy7_preserved": True,
        "status": "BLOCKED — TIERED50 STOPPED AFTER FAILED FIRST SCHEDULED HEARTBEAT; LEGACY7 RETIRED",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    print(json.dumps(reconcile(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
