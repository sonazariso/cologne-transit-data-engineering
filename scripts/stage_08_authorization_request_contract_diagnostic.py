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
import unicodedata
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

import stage_08_mdd_realtime_pilot as pilot


SCRIPT_VERSION = "stage-08-auth-request-contract-diagnostic-1.4"
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
GERMAN_SUPPLIER_ACCESS_PATTERNS = (
    re.compile(r"keinen\s+zugriff\s+auf\s+daten"),
    re.compile(r"datenlieferanten"),
    re.compile(r"weitere\s+datenlieferanten\s+freischalten"),
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
    """Return sanitized provider-error evidence, never normal Situation text.

    A successful, parseable TRIAS response may contain passenger-information
    ``Situation`` summaries/descriptions.  Those belong to the existing
    Situation evidence path, not to diagnostic provider-error metadata.  For a
    successful response, only the parser's explicit response/result error
    entries can contribute to this field.  Non-2xx responses remain on the
    failed-response path so the observed supplier-access body is retained.
    """

    if result.http_status is None or not result.body:
        return None

    decoded = pilot.decode_response(result.body, result.content_type)
    try:
        decoded_value = json.loads(decoded)
    except (TypeError, ValueError):
        decoded_value = None

    if 200 <= result.http_status < 300:
        if parsed is None or not pilot.application_error(parsed):
            return None

        candidates = []
        for item in parsed.response_error_entries + parsed.result_error_entries:
            if not item.get("populated"):
                continue
            if pilot.normalized_key(item.get("field_name")) not in {
                "errormessage",
                "errorcode",
            }:
                continue
            text = scalar_error_text(item.get("value_excerpt"), api_key)
            if text and text not in candidates:
                candidates.append(text)
            if len(candidates) >= 5:
                break
        return pilot.clean_error(" | ".join(candidates), api_key) if candidates else None

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


def control_failure_classification(
    result: pilot.HTTPResult,
    *,
    response_parseable: Optional[bool],
    parse_error: Optional[str],
    parsed: Optional[pilot.ParsedResponse],
) -> Optional[str]:
    """Classify only genuine control failures.

    ``pilot.classification_for_http`` intentionally has no parser result, so a
    successful 2xx response falls through to ``PARSER_FAILURE`` there.  The
    diagnostic has the parse result available and must not emit a failure
    classification for a successful, parseable control.
    """

    if result.failure == "TIMEOUT":
        return "TIMEOUT"
    if result.failure == "NETWORK_OR_DNS_FAILURE":
        return "NETWORK_OR_DNS_FAILURE"
    if result.http_status in {401, 403}:
        return "AUTHENTICATION_OR_AUTHORIZATION_FAILURE"
    if result.http_status == 429:
        return "RATE_LIMIT_RESPONSE"
    if result.http_status is not None and not 200 <= result.http_status < 300:
        return "HTTP_ERROR"
    if result.failure:
        return result.failure
    if result.http_status is not None and 200 <= result.http_status < 300:
        if response_parseable is True and parse_error is None:
            if parsed is not None and pilot.application_error(parsed):
                return "MDD_APPLICATION_ERROR"
            return None
        return "PARSER_FAILURE"
    return "PARSER_FAILURE"


def contains_supplier_access_message(text: Optional[str]) -> bool:
    if not text:
        return False
    normalized = " ".join(
        unicodedata.normalize("NFKC", text).casefold().split()
    )
    if any(pattern.search(normalized) for pattern in SUPPLIER_ACCESS_PATTERNS):
        return True
    german_has_access_phrase = bool(GERMAN_SUPPLIER_ACCESS_PATTERNS[0].search(normalized))
    german_has_supplier_phrase = bool(GERMAN_SUPPLIER_ACCESS_PATTERNS[1].search(normalized))
    german_has_enable_phrase = bool(GERMAN_SUPPLIER_ACCESS_PATTERNS[2].search(normalized))
    return (german_has_access_phrase and german_has_supplier_phrase) or german_has_enable_phrase


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
    diagnostic_anchor_dep_arr_time: Optional[str] = None,
    api_key: str,
    directory: Path,
    evidence_writer: pilot.JsonlWriter,
) -> Dict[str, Any]:
    """Execute exactly one control and persist only sanitized evidence."""

    control_runtime_config = pilot.Stage8RuntimeConfig(
        station=station,
        stop_point_ref=stop_point_ref,
        number_of_results=number_of_results,
        sampling_policy=DIAGNOSTIC_POLICY,
    )
    # The diagnostic isolates only the intended control variable.  Keep the
    # query anchor fixed for every control, while perform_http_request still
    # creates a fresh actual RequestTimestamp for this individual request.
    diagnostic_anchor = diagnostic_anchor_dep_arr_time or pilot.trias_utc(pilot.utc_now())
    result = pilot.perform_http_request(
        api_key,
        diagnostic_anchor,
        runtime_config=control_runtime_config,
    )

    raw_artifact_path: Optional[Path] = None
    raw_artifact_error: Optional[str] = None
    raw_evidence_status = pilot.NO_RECORDED_AT_TIME_RAW_EVIDENCE
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
    if (
        result.http_status is not None
        and 200 <= result.http_status < 300
        and parsed is not None
        and not pilot.application_error(parsed)
        and pilot.parsed_response_has_recorded_at_time(parsed)
    ):
        raw_evidence_status = pilot.RECORDED_AT_TIME_RAW_EVIDENCE
    sanitized_error = provider_error_text(result, api_key, parsed)
    record: Dict[str, Any] = {
        "record_type": "stage_08_authorization_control",
        "script_version": SCRIPT_VERSION,
        "diagnostic_policy": DIAGNOSTIC_POLICY,
        "diagnostic_run_id": diagnostic_run_id,
        "control": control_name,
        "station": station,
        "stop_point_ref": stop_point_ref,
        "effective_runtime_config": pilot.runtime_config_record(control_runtime_config),
        "request_timestamp": pilot.iso_utc(result.request_timestamp),
        "request_timestamp_utc": result.request_timestamp_utc,
        "diagnostic_anchor_dep_arr_time": diagnostic_anchor,
        "dep_arr_time": diagnostic_anchor,
        "request_contract": {
            "endpoint": pilot.ENDPOINT,
            "method": "POST",
            "content_type": "application/xml; charset=utf-8",
            "trias_version": pilot.TRIAS_VERSION,
            "requestor_ref": pilot.REQUESTOR_REF,
            "number_of_results": number_of_results,
            "sampling_policy": DIAGNOSTIC_POLICY,
            "dep_arr_time": diagnostic_anchor,
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
        "existing_stage_8_failure_classification": control_failure_classification(
            result,
            response_parseable=parseable,
            parse_error=parse_error,
            parsed=parsed,
        ),
        "data_supplier_access_message": contains_supplier_access_message(sanitized_error),
        "raw_artifact_path": str(raw_artifact_path) if raw_artifact_path else None,
        "raw_evidence_status": raw_evidence_status,
        "recorded_at_time_raw_evidence_status": raw_evidence_status,
        "raw_artifact_error": raw_artifact_error,
        "recorded_at_utc": pilot.iso_utc(pilot.utc_now()),
    }
    evidence_writer.append(pilot.sanitize_for_artifact(record, api_key))
    return record


def decision_for_controls(
    controls: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Apply S40's decision tree without issuing any additional request."""

    def decision(
        *,
        classification: str,
        full_pilot_permitted: bool,
        request_parameter_change: Optional[Dict[str, Any]],
        runtime_config: Optional[pilot.Stage8RuntimeConfig] = None,
    ) -> Dict[str, Any]:
        return {
            "classification": classification,
            "full_pilot_permitted": full_pilot_permitted,
            "request_parameter_change": request_parameter_change,
            "effective_runtime_config": (
                pilot.runtime_config_record(runtime_config)
                if runtime_config is not None
                else None
            ),
        }

    control_a = controls.get("A")
    if control_a is None:
        return decision(
            classification="CONTROL_A_NOT_EXECUTED",
            full_pilot_permitted=False,
            request_parameter_change=None,
        )

    status_a = control_a.get("http_status")
    if status_a == 200:
        control_b = controls.get("B")
        if control_b is None:
            return decision(
                classification="CONTROL_B_NOT_EXECUTED",
                full_pilot_permitted=False,
                request_parameter_change=None,
            )
        status_b = control_b.get("http_status")
        if status_b == 200:
            return decision(
                classification="CURRENT_CREDENTIAL_AND_STAGE_8_REQUEST_BREADTH_WORKING",
                full_pilot_permitted=True,
                request_parameter_change=None,
                runtime_config=pilot.DEFAULT_RUNTIME_CONFIG,
            )
        if status_b == 401 and control_b.get("data_supplier_access_message"):
            return decision(
                classification="RESULT_SET_BREADTH_PROVIDER_ACCESS_INTERACTION",
                full_pilot_permitted=True,
                request_parameter_change={
                    "parameter": "NumberOfResults",
                    "from": BASELINE_RESULT_COUNT,
                    "to": CONTROL_RESULT_COUNT,
                    "policy_action": "VERSION_STAGE_8_POLICY_BEFORE_PILOT",
                },
                runtime_config=pilot.HBF_N5_RUNTIME_CONFIG,
            )
        return decision(
            classification="CONTROL_B_NON_SUPPLIER_ACCESS_FAILURE_AFTER_A_200",
            full_pilot_permitted=False,
            request_parameter_change=None,
        )

    if status_a == 401:
        control_c = controls.get("C")
        if control_c is None:
            return decision(
                classification="CONTROL_C_NOT_EXECUTED",
                full_pilot_permitted=False,
                request_parameter_change=None,
            )
        status_c = control_c.get("http_status")
        if status_c == 200:
            return decision(
                classification="KOELN_HBF_OR_PROVIDER_PATH_SPECIFIC_AUTHORIZATION",
                full_pilot_permitted=True,
                request_parameter_change={
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
                runtime_config=pilot.HEUMARKT_N5_RUNTIME_CONFIG,
            )
        if status_c == 401:
            return decision(
                classification="CURRENT_CREDENTIAL_OR_GENERAL_AUTHORIZATION_NOT_PROVEN",
                full_pilot_permitted=False,
                request_parameter_change=None,
            )
        return decision(
            classification="CONTROL_C_NON_401_FAILURE_AFTER_A_401",
            full_pilot_permitted=False,
            request_parameter_change=None,
        )

    return decision(
        classification=control_a.get("existing_stage_8_failure_classification"),
        full_pilot_permitted=False,
        request_parameter_change=None,
    )


def run_diagnostic() -> Dict[str, Any]:
    global diagnostic_run_id

    api_key = os.environ.get(pilot.MDD_API_KEY_ENV)
    credential_status = pilot.validate_api_key_format(api_key)
    print(pilot.credential_preflight_output(credential_status), flush=True)
    diagnostic_run_id = f"stage08-auth-{uuid.uuid4().hex}"
    # Select the diagnostic query anchor once, before Control A.  It is
    # intentionally independent from the pilot anchor selected by run_pilot.
    diagnostic_anchor_dep_arr_time = pilot.trias_utc(pilot.utc_now())
    if credential_status != pilot.CREDENTIAL_PREFLIGHT_VALID:
        missing = credential_status == pilot.CREDENTIAL_PREFLIGHT_MISSING
        return {
            "record_type": "stage_08_authorization_diagnostic_summary",
            "script_version": SCRIPT_VERSION,
            "diagnostic_policy": DIAGNOSTIC_POLICY,
            "diagnostic_run_id": diagnostic_run_id,
            "preflight": pilot.credential_preflight_output(credential_status),
            "credential_preflight_status": credential_status,
            "diagnostic_anchor_dep_arr_time": diagnostic_anchor_dep_arr_time,
            "controls": {},
            "classification": (
                "DIAGNOSTIC_NOT_RUN_CREDENTIAL_MISSING"
                if missing
                else "CREDENTIAL_FORMAT_INVALID"
            ),
            "preflight_classification": (
                "MDD_API_KEY_MISSING" if missing else "CREDENTIAL_FORMAT_INVALID"
            ),
            "attempted_http_requests": 0,
            "full_pilot_permitted": False,
            "request_parameter_change": None,
            "effective_runtime_config": None,
            "stage_8_pilot_started": False,
            "stage_8_pilot_status": None,
            "stage_8_complete": False,
            "stage_8_pilot_summary": None,
            "evidence_directory": None,
            "diagnostic_cleanup_result": "NOT_CREATED",
            "diagnostic_cleanup_pending": False,
            "evidence_retention": "NOT_CREATED",
            "diagnostic_cleanup_max_retention_hours": (
                pilot.MAX_RAW_EVIDENCE_RETENTION_HOURS
            ),
            "stage_9_or_later_work_performed": False,
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
            diagnostic_anchor_dep_arr_time=diagnostic_anchor_dep_arr_time,
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
                diagnostic_anchor_dep_arr_time=diagnostic_anchor_dep_arr_time,
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
                diagnostic_anchor_dep_arr_time=diagnostic_anchor_dep_arr_time,
                api_key=api_key,
                directory=directory,
                evidence_writer=evidence_writer,
            )
        decision = decision_for_controls(controls)
    finally:
        evidence_writer.close()

    retained_raw_paths: Set[Path] = {
        Path(record["raw_artifact_path"])
        for record in controls.values()
        if record.get("raw_evidence_status") == pilot.RECORDED_AT_TIME_RAW_EVIDENCE
        and record.get("raw_artifact_path")
    }

    pilot_summary: Optional[Dict[str, Any]] = None
    if decision.get("full_pilot_permitted"):
        selected_config = pilot.runtime_config_from_record(
            decision.get("effective_runtime_config")
        )
        if selected_config is None:  # pragma: no cover - defensive decision-tree guard
            raise RuntimeError("pilot-permitted diagnostic decision had no runtime configuration")
        # The actual pilot creates its own fixed-start anchor at its own start
        # time.  The diagnostic controls' timestamps are never reused.
        pilot_summary = pilot.run_pilot(selected_config)
    pilot_status = pilot_summary.get("stage_8_status") if pilot_summary else None
    stage_8_complete = pilot_status == "COMPLETE"

    # A diagnostic directory is removed only after the pilot itself has
    # completed its evidence extraction/reconciliation gate and its own
    # temporary artifacts have been cleaned.  A failed or blocked handoff
    # retains the diagnostic evidence under the existing 24-hour boundary.
    pilot_execution = pilot_summary.get("execution", {}) if pilot_summary else {}
    if not isinstance(pilot_execution, dict):
        pilot_execution = {}
    pilot_evidence_complete = bool(
        pilot_execution.get("evidence_extraction_and_reconciliation_complete")
    )
    pilot_cleanup_result = pilot_execution.get("cleanup_result")
    if pilot_cleanup_result is None and pilot_summary:
        pilot_cleanup_result = pilot_summary.get("cleanup_result")
    pilot_cleanup_complete = pilot_cleanup_result in {
        "SUCCEEDED",
        pilot.RECORDED_AT_TIME_EVIDENCE_RETAINED_MAX_24_HOURS,
    }

    # Assemble and sanitize the diagnostic summary evidence before deciding
    # whether the directory may be deleted.  The cleanup outcome is added only
    # after this evidence snapshot exists, so a successful cleanup cannot
    # remove the material needed to construct the summary.
    summary = pilot.sanitize_for_artifact(
        {
            "record_type": "stage_08_authorization_diagnostic_summary",
            "script_version": SCRIPT_VERSION,
            "diagnostic_policy": DIAGNOSTIC_POLICY,
            "diagnostic_run_id": diagnostic_run_id,
            "preflight": pilot.credential_preflight_output(credential_status),
            "credential_preflight_status": credential_status,
            "diagnostic_anchor_dep_arr_time": diagnostic_anchor_dep_arr_time,
            "controls": controls,
            **decision,
            "evidence_directory": str(directory),
            "evidence_jsonl": str(evidence_path),
            "stage_8_pilot_started": pilot_summary is not None,
            "stage_8_pilot_status": pilot_status,
            "stage_8_complete": stage_8_complete,
            "stage_8_pilot_summary": pilot_summary,
            "recorded_at_time_raw_evidence_status": (
                pilot.RECORDED_AT_TIME_RAW_EVIDENCE if retained_raw_paths else None
            ),
            "recorded_at_time_raw_evidence_response_count": len(retained_raw_paths),
            "raw_evidence_secret_check": "PENDING",
            "stage_9_or_later_work_performed": False,
        },
        api_key,
    )

    diagnostic_raw_evidence_safe = True
    if retained_raw_paths:
        try:
            pilot.verify_raw_artifacts_secret_free(retained_raw_paths, api_key)
        except Exception:
            diagnostic_raw_evidence_safe = False

    diagnostic_cleanup_result = pilot.TEMP_FAILURE_RETENTION_STATUS
    if (
        decision.get("full_pilot_permitted")
        and stage_8_complete
        and pilot_evidence_complete
        and pilot_cleanup_complete
    ):
        if diagnostic_raw_evidence_safe:
            diagnostic_cleanup_result = pilot.cleanup_directory(
                directory,
                retained_raw_paths=retained_raw_paths or None,
            )
        else:
            diagnostic_cleanup_result = "FAILED_CREDENTIAL_SAFETY_CHECK"

    diagnostic_cleanup_pending = diagnostic_cleanup_result in {
        pilot.TEMP_FAILURE_RETENTION_STATUS,
        pilot.RECORDED_AT_TIME_EVIDENCE_RETAINED_MAX_24_HOURS,
    }
    run_ended = pilot.utc_now()
    cleanup_retention_deadline = (
        pilot.iso_utc(
            run_ended
            + timedelta(hours=pilot.MAX_RAW_EVIDENCE_RETENTION_HOURS)
        )
        if diagnostic_cleanup_pending
        else None
    )
    if (
        diagnostic_cleanup_result
        == pilot.RECORDED_AT_TIME_EVIDENCE_RETAINED_MAX_24_HOURS
    ):
        evidence_retention = pilot.RECORDED_AT_TIME_EVIDENCE_RETAINED_MAX_24_HOURS
    elif not diagnostic_cleanup_pending:
        evidence_retention = "CLEANED"
    else:
        evidence_retention = pilot.TEMP_FAILURE_RETENTION_STATUS
    summary.update(
        {
            "diagnostic_cleanup_result": diagnostic_cleanup_result,
            "diagnostic_cleanup_pending": diagnostic_cleanup_pending,
            "diagnostic_cleanup_max_retention_hours": (
                pilot.MAX_RAW_EVIDENCE_RETENTION_HOURS
            ),
            "diagnostic_cleanup_retention_deadline_utc": cleanup_retention_deadline,
            "evidence_retention": evidence_retention,
            "evidence_retention_deadline_utc": cleanup_retention_deadline,
            "raw_evidence_secret_check": (
                "PASS" if diagnostic_raw_evidence_safe else "FAIL"
            ),
        }
    )
    return summary


diagnostic_run_id = "UNINITIALIZED"


def main() -> int:
    summary = run_diagnostic()
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if summary.get("stage_8_complete") else 2


if __name__ == "__main__":
    raise SystemExit(main())
