#!/usr/bin/env python3
"""Stage 02A.2 diagnostic-only MDD raw-response probe.

This script is intentionally limited to one HTTP POST per execution. It does
not import or invoke the Legacy Collector, retry, persist to SQL, or retain a
raw response in the repository. The raw response is written only to a
permission-restricted temporary artifact for this controlled investigation.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from email.message import Message
from http.client import HTTPResponse
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


ENDPOINT = "https://mdd.gorheinland.com/delfi"
STOP_POINT_REF = "de:05315:11201"
NUMBER_OF_RESULTS = 5
REQUEST_TIMEOUT_SECONDS = 30


class NoRedirectHandler(HTTPRedirectHandler):
    """Prevent urllib from turning one logical probe into multiple requests."""

    def redirect_request(self, req: Request, fp: HTTPResponse, code: int, msg: str,
                         headers: Message, newurl: str) -> None:
        return None


def build_request_body(request_timestamp: str) -> bytes:
    """Build the request from the verified Legacy request-builder contract."""

    body = f'''<?xml version="1.0" encoding="UTF-8"?>
<Trias xmlns="http://www.vdv.de/trias"
       xmlns:siri="http://www.siri.org.uk/siri"
       version="1.2">
  <ServiceRequest>
    <siri:RequestTimestamp>{request_timestamp}</siri:RequestTimestamp>
    <siri:RequestorRef>CologneTransitIntelligence</siri:RequestorRef>
    <RequestPayload>
      <StopEventRequest>
        <Location>
          <LocationRef>
            <StopPointRef>{STOP_POINT_REF}</StopPointRef>
          </LocationRef>
          <DepArrTime>{request_timestamp}</DepArrTime>
        </Location>
        <Params>
          <NumberOfResults>{NUMBER_OF_RESULTS}</NumberOfResults>
          <StopEventType>arrival</StopEventType>
          <IncludePreviousCalls>true</IncludePreviousCalls>
          <IncludeOnwardCalls>true</IncludeOnwardCalls>
          <IncludeRealtimeData>true</IncludeRealtimeData>
        </Params>
      </StopEventRequest>
    </RequestPayload>
  </ServiceRequest>
</Trias>
'''
    return body.encode("utf-8")


def response_charset(content_type: str | None) -> str:
    if content_type:
        parts = [part.strip() for part in content_type.split(";")]
        for part in parts[1:]:
            if "=" in part:
                name, value = part.split("=", 1)
                if name.strip().lower() == "charset":
                    return value.strip().strip('"') or "utf-8"
    return "utf-8"


def read_response(response: Any) -> tuple[int, Message, bytes]:
    status = int(response.status)
    headers = response.headers
    content = response.read()
    return status, headers, content


def write_transient_raw_artifact(content: bytes, api_key: str) -> str | None:
    """Keep one local diagnostic copy, never a source-controlled artifact."""

    if api_key.encode("utf-8") in content:
        return None

    fd, raw_path = tempfile.mkstemp(
        prefix="cologne-transit-stage-02a2-",
        suffix=".raw",
        dir="/private/tmp",
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as raw_file:
            raw_file.write(content)
    except Exception:
        os.close(fd)
        raise
    return raw_path


def main() -> int:
    api_key = os.environ.get("MDD_API_KEY")
    if not api_key:
        print(json.dumps({"preflight": "MDD_API_KEY visible: NO", "http_attempts": 0}))
        return 2

    request_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = build_request_body(request_timestamp)
    request = Request(
        ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/xml",
            "x-api-key": api_key,
        },
    )

    started = time.perf_counter()
    request_utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    status_code: int | None = None
    content_type: str | None = None
    content = b""
    failure: str | None = None

    # This is the sole MDD HTTP operation in this script. There is no loop,
    # retry, redirect follow-up, or second request path.
    opener = build_opener(NoRedirectHandler())
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status_code, headers, content = read_response(response)
            content_type = headers.get("Content-Type")
    except HTTPError as error:
        status_code = int(error.code)
        content_type = error.headers.get("Content-Type") if error.headers else None
        content = error.read()
        failure = f"HTTP {status_code}"
    except (URLError, TimeoutError, OSError) as error:
        failure = f"{type(error).__name__}: {error}"

    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    raw_artifact = None
    if content and api_key.encode("utf-8") not in content:
        raw_artifact = write_transient_raw_artifact(content, api_key)
    elif content:
        failure = "response contained the authentication secret; raw artifact was not written"

    decoded = content.decode(response_charset(content_type), errors="replace")
    result = {
        "preflight": "MDD_API_KEY visible: YES",
        "http_attempts": 1,
        "request_timestamp_utc": request_utc,
        "endpoint": ENDPOINT,
        "method": "POST",
        "request_content_type": "application/xml",
        "station": "Köln Hbf",
        "stop_point_ref": STOP_POINT_REF,
        "number_of_results": NUMBER_OF_RESULTS,
        "stop_event_type": "arrival",
        "include_previous_calls": True,
        "include_onward_calls": True,
        "include_realtime_data": True,
        "http_status": status_code,
        "response_content_type": content_type,
        "response_bytes": len(content),
        "response_characters": len(decoded),
        "elapsed_ms": elapsed_ms,
        "raw_artifact": raw_artifact,
        "failure": failure,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    if status_code is None or status_code < 200 or status_code >= 300 or failure:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
