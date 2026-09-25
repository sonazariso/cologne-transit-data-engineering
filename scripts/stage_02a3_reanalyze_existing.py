#!/usr/bin/env python3
"""Offline-only reanalysis of the existing Stage 02A.3 raw artifacts.

This script reads exactly five caller-selected ``*.raw`` files, parses JSON,
and reuses the pure analysis functions from the historical probe. It does not
read credentials, perform network I/O, construct requests, or write files.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# The imported module exposes the shared pure JSON-analysis functions. Its
# network probe runs only under its own ``if __name__ == "__main__"`` guard;
# this offline entry point never calls that main function or any HTTP helper.
import stage_02a3_historical_actual_time_probe as probe


EXPECTED_ARTIFACTS = (
    (1, "01-1d.raw"),
    (7, "02-7d.raw"),
    (30, "03-30d.raw"),
    (90, "04-90d.raw"),
    (180, "05-180d.raw"),
)
SCOPE_JSON_KEYS = {
    "thiscall": "thisCall",
    "previouscall": "previousCall",
    "onwardcall": "onwardCall",
}
TIMING_JSON_KEYS = {
    "timetabled": probe.TIMING_FIELD_ALIASES["timetabled"],
    "estimated": probe.TIMING_FIELD_ALIASES["estimated"],
    "recorded": probe.TIMING_FIELD_ALIASES["recorded"],
}
FIELD_MARKERS = (
    "actual",
    "recorded",
    "realtime",
    "prediction",
    "predicted",
    "estimated",
    "cancel",
    "delay",
)
STATUS_MARKERS = (
    "status",
    "cancel",
    "deviation",
    "notserviced",
    "unplanned",
    "boarding",
    "alighting",
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reanalyse existing Stage 02A.3 raw JSON without network access."
    )
    parser.add_argument(
        "--raw-directory",
        required=True,
        type=Path,
        help="Directory containing 01-1d.raw through 05-180d.raw.",
    )
    parser.add_argument(
        "--execution-date",
        required=True,
        type=date.fromisoformat,
        help="Europe/Berlin local date on which the original five requests ran.",
    )
    return parser.parse_args()


def load_artifacts(raw_directory: Path) -> tuple[list[dict[str, Any]], dict[str, tuple[bytes, Any]]]:
    verification: list[dict[str, Any]] = []
    loaded: dict[str, tuple[bytes, Any]] = {}
    for _horizon_days, filename in EXPECTED_ARTIFACTS:
        path = raw_directory / filename
        record: dict[str, Any] = {
            "filename": filename,
            "readable": False,
            "byte_size": None,
            "json_parse_success": False,
            "result_count": None,
        }
        try:
            content = path.read_bytes()
        except OSError as error:
            record["read_error"] = type(error).__name__
            verification.append(record)
            continue
        record["readable"] = True
        record["byte_size"] = len(content)
        try:
            parsed = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, TypeError, ValueError) as error:
            record["parse_error"] = type(error).__name__
            verification.append(record)
            continue
        record["json_parse_success"] = True
        record["result_count"] = len(probe.named_result_values(parsed))
        loaded[filename] = (content, parsed)
        verification.append(record)
    return verification, loaded


def bounded_timing_samples(
    parsed: Any,
    scope_name: str,
    timing_name: str,
    limit: int,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    seen_values: set[str] = set()
    scope_json_key = SCOPE_JSON_KEYS[scope_name]
    scope_prefix = (
        "$.serviceDelivery.deliveryPayload.stopEventResponse.stopEventResult[*].stopEvent."
        f"{scope_json_key}"
    )
    for result in probe.named_result_values(parsed):
        for call_object in probe.scope_calls(result, scope_name):
            for relative_path, key, value in probe.iter_dict_entries(call_object):
                if key not in TIMING_JSON_KEYS[timing_name]:
                    continue
                if not probe.has_nonempty_scalar(value):
                    continue
                lexical_value = str(value)
                if lexical_value in seen_values:
                    continue
                seen_values.add(lexical_value)
                suffix = relative_path[1:] if relative_path.startswith("$") else relative_path
                samples.append(
                    {
                        "json_path": f"{scope_prefix}{suffix}",
                        "json_value_type": probe.json_value_type(value),
                        "value": value,
                    }
                )
                if len(samples) >= limit:
                    return samples
    return samples


def field_inventory(parsed: Any) -> dict[str, Any]:
    related_fields: dict[str, dict[str, Any]] = {}
    status_fields: dict[str, dict[str, Any]] = {}
    response_timestamp_paths: list[str] = []
    calc_time_paths: list[str] = []
    for path, key, value in probe.iter_dict_entries(parsed):
        lowered_key = key.lower()
        populated = (
            (isinstance(value, str) and bool(value.strip()))
            or (isinstance(value, bool) and value)
            or (isinstance(value, (int, float)) and not isinstance(value, bool))
            or probe.has_populated_error_message(value)
        )
        if key in {"responseTimestamp", "ResponseTimestamp"}:
            response_timestamp_paths.append(path)
        if key in {"calcTime", "CalcTime"}:
            calc_time_paths.append(path)
        if any(marker in lowered_key for marker in FIELD_MARKERS):
            related_fields.setdefault(
                key,
                {
                    "field_name": key,
                    "json_value_type": probe.json_value_type(value),
                    "nonempty_observation": populated,
                },
            )
        if any(marker in lowered_key for marker in STATUS_MARKERS):
            status_fields.setdefault(
                key,
                {
                    "field_name": key,
                    "json_value_type": probe.json_value_type(value),
                    "nonempty_observation": populated,
                },
            )
    return {
        "actual_or_realtime_related_fields": sorted(related_fields.values(), key=lambda x: x["field_name"]),
        "operational_status_related_fields": sorted(status_fields.values(), key=lambda x: x["field_name"]),
        "response_timestamp_paths": response_timestamp_paths[:3],
        "calc_time_paths": calc_time_paths[:3],
    }


def request_metadata(
    horizon_days: int,
    execution_date: date,
    berlin_timezone: ZoneInfo,
) -> dict[str, Any]:
    requested_date = execution_date - timedelta(days=horizon_days)
    requested_local = datetime.combine(
        requested_date,
        datetime_time(hour=12, minute=0),
        tzinfo=berlin_timezone,
    )
    requested_utc = requested_local.astimezone(timezone.utc)
    return {
        "requested_date": requested_date,
        "requested_local_datetime": requested_local.isoformat(timespec="seconds"),
        "requested_utc_datetime": probe.format_utc_xs_datetime(requested_utc),
    }


def original_parser_failure_diagnostic(values: list[str]) -> dict[str, Any]:
    suffix_values = [value for value in values if value.endswith("[GMT]")]
    if not suffix_values:
        return {
            "observed": False,
            "reason": None,
        }
    return {
        "observed": True,
        "reason": (
            "The previous parser passed the complete lexical value to "
            "datetime.fromisoformat() after only translating a terminal Z; "
            "the trailing provider suffix [GMT] therefore caused ValueError."
        ),
        "sample": suffix_values[0],
    }


def reanalyze_one(
    *,
    horizon_days: int,
    filename: str,
    content: bytes,
    parsed: Any,
    artifact_path: Path,
    execution_date: date,
    berlin_timezone: ZoneInfo,
) -> dict[str, Any]:
    metadata = request_metadata(horizon_days, execution_date, berlin_timezone)
    analysis = probe.analyze_response(content, None, "")
    summary = probe.response_summary(
        horizon_days=horizon_days,
        requested_local_datetime=metadata["requested_local_datetime"],
        requested_utc_datetime=metadata["requested_utc_datetime"],
        request_timestamp_utc=None,
        http_status=200,
        elapsed_ms=0.0,
        response_content_type=None,
        response_bytes=len(content),
        raw_artifact_path=str(artifact_path),
        analysis=analysis,
        requested_local_date=metadata["requested_date"],
        execution_local_date=execution_date,
        berlin_timezone=berlin_timezone,
        failure=None,
        api_key="",
    )
    thiscall_values = analysis["thiscall_timing"]["timetabled"]
    all_timetabled_values = probe.merge_timing_values(
        analysis["thiscall_timing"],
        analysis["previouscall_timing"],
        analysis["onwardcall_timing"],
    )["timetabled"]
    related_inventory = field_inventory(parsed)
    summary.update(
        {
            "analysis_mode": "OFFLINE_EXISTING_RAW",
            "artifact_filename": filename,
            "artifact_readable": True,
            "artifact_json_parse_success": analysis["json_parse_success"],
            "artifact_result_count": analysis["result_count"],
            "http_status_source": "previous host-run summary; not inferred from JSON body",
            "thiscall_timetabled_lexical_samples": bounded_timing_samples(
                parsed, "thiscall", "timetabled", 5
            ),
            "previouscall_timetabled_lexical_samples": bounded_timing_samples(
                parsed, "previouscall", "timetabled", 3
            ),
            "onwardcall_timetabled_lexical_samples": bounded_timing_samples(
                parsed, "onwardcall", "timetabled", 3
            ),
            "timestamp_parser_failure_diagnostic": original_parser_failure_diagnostic(
                thiscall_values
            ),
            "all_timetabled_timestamp_semantics": sorted(
                {probe.timestamp_semantics(value) for value in all_timetabled_values}
            ),
            "provider_timestamp_semantics": (
                "EXPLICIT UTC via terminal Z; [GMT] is a provider lexical suffix"
                if any(value.endswith("Z[GMT]") for value in all_timetabled_values)
                else "NO_TIMETABLED_VALUES"
            ),
            "date_alignment_rule": probe.DATE_ALIGNMENT_SEMANTICS,
            "field_inventory": related_inventory,
        }
    )
    return summary


def main() -> int:
    arguments = parse_arguments()
    raw_directory = arguments.raw_directory
    verification, loaded = load_artifacts(raw_directory)
    print(
        json.dumps(
            {
                "analysis_mode": "OFFLINE_EXISTING_RAW",
                "raw_directory": str(raw_directory),
                "execution_local_date": arguments.execution_date.isoformat(),
                "artifact_verification": verification,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )

    required_filenames = {filename for _horizon_days, filename in EXPECTED_ARTIFACTS}
    verified_filenames = {
        record["filename"]
        for record in verification
        if record["readable"] and record["json_parse_success"]
    }
    if verified_filenames != required_filenames:
        print("Offline analysis stopped: all five readable JSON artifacts are required.")
        return 2
    if any(
        filename not in loaded or not loaded[filename][1]
        for _horizon_days, filename in EXPECTED_ARTIFACTS[:3]
    ):
        print("Offline analysis stopped: 1D/7D/30D artifacts are unavailable or invalid.")
        return 2

    try:
        berlin_timezone = ZoneInfo(probe.BERLIN_TIMEZONE_NAME)
    except ZoneInfoNotFoundError:
        print(json.dumps({"offline_error": "Europe/Berlin timezone unavailable"}))
        return 2

    summaries: list[dict[str, Any]] = []
    for horizon_days, filename in EXPECTED_ARTIFACTS:
        content, parsed = loaded[filename]
        summary = reanalyze_one(
            horizon_days=horizon_days,
            filename=filename,
            content=content,
            parsed=parsed,
            artifact_path=raw_directory / filename,
            execution_date=arguments.execution_date,
            berlin_timezone=berlin_timezone,
        )
        summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))

    print(
        json.dumps(
            {
                "analysis_mode": "OFFLINE_EXISTING_RAW",
                "horizons_analyzed": [summary["horizon_days"] for summary in summaries],
                "recorded_at_time_observed_anywhere": any(
                    summary["recorded_at_time_observed"] for summary in summaries
                ),
                "estimated_time_observed_anywhere": any(
                    summary["thiscall_estimated_count"]
                    or summary["previouscall_estimated_count"]
                    or summary["onwardcall_estimated_count"]
                    for summary in summaries
                ),
                "historical_classifications": {
                    str(summary["horizon_days"]): summary["historical_classification"]
                    for summary in summaries
                },
                "mdd_http_requests_in_s16": 0,
                "mdd_probe_executions_in_s16": 0,
                "api_key_required": False,
                "network_access": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
