#!/usr/bin/env python3
"""Stage 8-only differential authorization/request-contract diagnostic.

This script is deliberately separate from the one-hour Stage 8 pilot.  It
executes only the controls required by the Stage 8 authorization decision tree:
Control A, then either Control B or Control C.  Requests are sequential,
non-concurrent, redirect-free, zero-retry, and use the shared 30-second MDD
transport boundary and TRIAS request builder.

The API-key value is used only in the in-memory request header and safety
check.  It is never printed, logged, hashed, fingerprinted, compared, or
written to an artifact.  Response bodies and sanitized control metadata are
held only in a protected temporary directory outside Git.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import stage_08_mdd_realtime_pilot as pilot


SCRIPT_VERSION = "stage-08-auth-request-contract-diagnostic-1.0"
DIAGNOSTIC_POLICY = "MDD_STAGE_8_AUTH_REQUEST_CONTRACT_DIAGNOSTIC_V1"
CONTROL_A_STATION = "Köln Hbf"
CONTROL_A_STOP_POINT_REF = "de:05315:11201"
CONTROL_C_STATION = "Köln Heumarkt"
CONTROL_C_STOP_POINT_REF = "de:05315:11110"
CONTROL_RESULT_COUNT = 5
BASELINE_RESULT_COUNT = 20
SUPPLIER_ACCESS_PATTERNS = (
    re.compile(r"access\s+to\s+(?:this|the|relevant)\s+data\s+provider\s+is\s+not\s+enabled", re.I),
    re.compile(r"data\s+(?:provider|supplier).*not\s+enabled", re.I),
    re.compile(r"(?:provider|supplier).*access.*not\s+enabled", re.I),
)
ERROR_FIELD_NAMES = {
    "error",
    "errormessage",
    "message",
    "detail",
    "description",
    "fault",
}


def scalar_error_text(value: Any, api_key: str) -> Optional[str]:
    """Extract a short sanitized provider-facing error value."""

    if isinstance(value, (dict, list)):
        excerpt = pilot.source_excerpt(value, api_key)
        return pilot.clean_error(excerpt, api_key) if excerpt is not None else None
    if value is None:
        return None
    return pilot.clean_error(value, api_key)


def provider_error_text(
    result: pilot.HTTPResult,
    api_key: str,
    parsed: Optional[pilot.ParsedResponse],
) -> Optional[str]:
    """Return only sanitized provider-error text, never a raw credential."""

    if not result.body:
        return None

    decoded = pilot.decode_response(result.body, result.content_type)
    try:
        decoded_value = json.loads(decoded)
    except (TypeError, ValueError):
        decoded_value = None

    candidates = []
    if decoded_value is not None:
        for path, key, child, _parent_path, _parent in pilot.iter_dict_entries(decoded_value):
            if pilot.normalized_key(key) not in ERROR_FIELD_NAMES:
                continue
            text = scalar_error_text(child, api_key)
            if text and text not in candidates:
                candidates.append(text)
            if len(candidates) >= 5:
                break

    if candidates:
        return pilot.clean_error(" | ".join(candidates), api_key)

    if parsed is not None and pilot.application_error(parsed):
        return pilot.clean_error(decoded, api_key)

    if result.http_status is not None and not 200 <= result.http_status < 300:
        return pilot.clean_error(decoded, api_key)
    return None


def contains_supplier_access_message(text: Optional[str]) -> bool:
    if not text:
        return False
    return any(pattern.search(text) for pattern in SUPPLIER_ACCESS_PATTERNS)


def parse_control_response(
    result: pilot.HTTPResult,
) -> Tuple[Optional[bool], Optional[pilot.ParsedResponse], Optional[str]]:
    """Parse with the existing Stage 8 parser without changing its contract."""

    if result.http_status is None:
        return None, None, None
    if not result.body:
        return False, None, "EMPTY_RESPONSE_BODY"
    try:
        return True, pilot.parse_response(result.body, result.content_type), None
    except pilot.PilotParserError as error:
        return False, None, pilot.clean_error(error, "")


def run_control(
    *,
    control_name: str,
    station: str,
    stop_point_ref: str,
    number_of_results: int,
    api_key: str,
    directory: Path,
    evidence_writer: pilot.JsonlWriter,
) -> Dict[str, Any]:
    """Execute exactly one control and persist only sanitized evidence."""

    # Passing no anchor makes the shared transport use the same current UTC
    # timestamp for RequestTimestamp and DepArrTime, as required by S40.
    result = pilot.perform_http_request(
        api_key,
        None,
        stop_point_ref=stop_point_ref,
        number_of_results=number_of_results,
    )

    raw_artifact_path: Optional[Path] = None
    raw_artifact_error: Optional[str] = None
    if result.body:
        try:
            raw_artifact_path = pilot.write_raw_artifact(
                directory,
                f"control-{control_name.casefold()}-response.payload",
                result.body,
                api_key,
            )
        except Exception as error:  # pragma: no cover - defensive safety boundary
            raw_artifact_error = pilot.clean_error(error, api_key)

    parseable, parsed, parse_error = parse_control_response(result)
    sanitized_error = provider_error_text(result, api_key, parsed)
    record: Dict[str, Any] = {
        "record_type": "stage_08_authorization_control",
        "script_version": SCRIPT_VERSION,
        "diagnostic_policy": DIAGNOSTIC_POLICY,
        "diagnostic_run_id": diagnostic_run_id,
        "control": control_name,
        "station": station,
        "stop_point_ref": stop_point_ref,
        "request_timestamp_utc": result.request_timestamp_utc,
        "dep_arr_time": result.request_timestamp_utc,
        "request_contract": {
            "endpoint": pilot.ENDPOINT,
            "method": "POST",
            "content_type": "application/xml; charset=utf-8",
            "trias_version": pilot.TRIAS_VERSION,
            "requestor_ref": pilot.REQUESTOR_REF,
            "number_of_results": number_of_results,
            "stop_event_type": pilot.STOP_EVENT_TYPE,
            "include_previous_calls": pilot.INCLUDE_PREVIOUS_CALLS,
            "include_onward_calls": pilot.INCLUDE_ONWARD_CALLS,
            "include_realtime_data": pilot.INCLUDE_REALTIME_DATA,
            "time_window": "OMITTED",
            "include_operating_days": "OMITTED",
            "timeout_seconds": pilot.REQUEST_TIMEOUT_SECONDS,
            "retries": 0,
            "redirects": "DISABLED",
            "concurrency": "none",
        },
        "http_status": result.http_status,
        "response_content_type": result.content_type,
        "elapsed_ms": round(result.elapsed_ms, 3),
        "response_byte_count": len(result.body),
        "sanitized_provider_error_text": sanitized_error,
        "response_parseable": parseable,
        "parse_error": parse_error,
        "transport_failure": result.failure,
        "transport_failure_detail": pilot.clean_error(result.failure_detail, api_key)
        if result.failure_detail
        else None,
        "existing_stage_8_failure_classification": pilot.classification_for_http(result),
        "data_supplier_access_message": contains_supplier_access_message(sanitized_error),
        "raw_artifact_path": str(raw_artifact_path) if raw_artifact_path else None,
        "raw_artifact_error": raw_artifact_error,
        "recorded_at_utc": pilot.iso_utc(pilot.utc_now()),
    }
    evidence_writer.append(pilot.sanitize_for_artifact(record, api_key))
    return record


def decision_for_controls(
    controls: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Apply S40's decision tree without issuing any additional request."""

    control_a = controls.get("A")
    if control_a is None:
        return {
            "classification": "CONTROL_A_NOT_EXECUTED",
            "full_pilot_permitted": False,
            "request_parameter_change": None,
        }

    status_a = control_a.get("http_status")
    if status_a == 200:
        control_b = controls.get("B")
        if control_b is None:
            return {
                "classification": "CONTROL_B_NOT_EXECUTED",
                "full_pilot_permitted": False,
                "request_parameter_change": None,
            }
        status_b = control_b.get("http_status")
        if status_b == 200:
            return {
                "classification": "CURRENT_CREDENTIAL_AND_STAGE_8_REQUEST_BREADTH_WORKING",
                "full_pilot_permitted": True,
                "request_parameter_change": None,
            }
        if status_b == 401 and control_b.get("data_supplier_access_message"):
            return {
                "classification": "RESULT_SET_BREADTH_PROVIDER_ACCESS_INTERACTION",
                "full_pilot_permitted": True,
                "request_parameter_change": {
                    "parameter": "NumberOfResults",
                    "from": BASELINE_RESULT_COUNT,
                    "to": CONTROL_RESULT_COUNT,
                    "policy_action": "VERSION_STAGE_8_POLICY_BEFORE_PILOT",
                },
            }
        return {
            "classification": "CONTROL_B_NON_SUPPLIER_ACCESS_FAILURE_AFTER_A_200",
            "full_pilot_permitted": False,
            "request_parameter_change": None,
        }

    if status_a == 401:
        control_c = controls.get("C")
        if control_c is None:
            return {
                "classification": "CONTROL_C_NOT_EXECUTED",
                "full_pilot_permitted": False,
                "request_parameter_change": None,
            }
        status_c = control_c.get("http_status")
        if status_c == 200:
            return {
                "classification": "KOELN_HBF_OR_PROVIDER_PATH_SPECIFIC_AUTHORIZATION",
                "full_pilot_permitted": True,
                "request_parameter_change": {
                    "parameters": {
                        "station": {
                            "from": CONTROL_A_STATION,
                            "to": CONTROL_C_STATION,
                        },
                        "StopPointRef": {
                            "from": CONTROL_A_STOP_POINT_REF,
                            "to": CONTROL_C_STOP_POINT_REF,
                        },
                        "NumberOfResults": {
                            "from": BASELINE_RESULT_COUNT,
                            "to": CONTROL_RESULT_COUNT,
                        },
                    },
                    "policy_action": "VERSION_STAGE_8_POLICY_BEFORE_PILOT",
                },
            }
        if status_c == 401:
            return {
                "classification": "CURRENT_CREDENTIAL_OR_GENERAL_AUTHORIZATION_NOT_PROVEN",
                "full_pilot_permitted": False,
                "request_parameter_change": None,
            }
        return {
            "classification": "CONTROL_C_NON_401_FAILURE_AFTER_A_401",
            "full_pilot_permitted": False,
            "request_parameter_change": None,
        }

    return {
        "classification": control_a.get("existing_stage_8_failure_classification"),
        "full_pilot_permitted": False,
        "request_parameter_change": None,
    }


def run_diagnostic() -> Dict[str, Any]:
    global diagnostic_run_id

    api_key = os.environ.get(pilot.MDD_API_KEY_ENV)
    print("MDD_API_KEY: PRESENT" if api_key else "MDD_API_KEY: MISSING", flush=True)
    diagnostic_run_id = f"stage08-auth-{uuid.uuid4().hex}"
    if not api_key:
        return {
            "record_type": "stage_08_authorization_diagnostic_summary",
            "script_version": SCRIPT_VERSION,
            "diagnostic_policy": DIAGNOSTIC_POLICY,
            "diagnostic_run_id": diagnostic_run_id,
            "preflight": "MDD_API_KEY: MISSING",
            "controls": {},
            "classification": "DIAGNOSTIC_NOT_RUN_CREDENTIAL_MISSING",
            "full_pilot_permitted": False,
            "request_parameter_change": None,
            "evidence_directory": None,
            "evidence_retention": "NOT_CREATED",
        }

    directory = pilot.create_temp_directory()
    evidence_path = directory / "authorization_request_contract_diagnostic.jsonl"
    evidence_writer = pilot.JsonlWriter(evidence_path)
    controls: Dict[str, Dict[str, Any]] = {}
    try:
        controls["A"] = run_control(
            control_name="A",
            station=CONTROL_A_STATION,
            stop_point_ref=CONTROL_A_STOP_POINT_REF,
            number_of_results=CONTROL_RESULT_COUNT,
            api_key=api_key,
            directory=directory,
            evidence_writer=evidence_writer,
        )
        if controls["A"].get("http_status") == 200:
            controls["B"] = run_control(
                control_name="B",
                station=CONTROL_A_STATION,
                stop_point_ref=CONTROL_A_STOP_POINT_REF,
                number_of_results=BASELINE_RESULT_COUNT,
                api_key=api_key,
                directory=directory,
                evidence_writer=evidence_writer,
            )
        elif controls["A"].get("http_status") == 401:
            controls["C"] = run_control(
                control_name="C",
                station=CONTROL_C_STATION,
                stop_point_ref=CONTROL_C_STOP_POINT_REF,
                number_of_results=CONTROL_RESULT_COUNT,
                api_key=api_key,
                directory=directory,
                evidence_writer=evidence_writer,
            )
        decision = decision_for_controls(controls)
    finally:
        evidence_writer.close()

    recorded_at = pilot.utc_now()
    return pilot.sanitize_for_artifact(
        {
            "record_type": "stage_08_authorization_diagnostic_summary",
            "script_version": SCRIPT_VERSION,
            "diagnostic_policy": DIAGNOSTIC_POLICY,
            "diagnostic_run_id": diagnostic_run_id,
            "preflight": "MDD_API_KEY: PRESENT",
            "controls": controls,
            **decision,
            "evidence_directory": str(directory),
            "evidence_jsonl": str(evidence_path),
            "evidence_retention": "PENDING_MAX_24_HOURS",
            "evidence_retention_deadline_utc": pilot.iso_utc(recorded_at + timedelta(hours=24)),
            "stage_8_pilot_started": False,
            "stage_9_or_later_work_performed": False,
        },
        api_key,
    )


diagnostic_run_id = "UNINITIALIZED"


def main() -> int:
    summary = run_diagnostic()
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if summary.get("full_pilot_permitted") else 2


if __name__ == "__main__":
    raise SystemExit(main())
