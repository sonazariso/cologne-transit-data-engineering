"""Approved TRIAS 1.2 request construction without credential material."""

from __future__ import annotations

from datetime import datetime, timezone
from xml.sax.saxutils import escape as xml_escape

from .domain import Target


TRIAS_VERSION = "1.2"
STOP_EVENT_TYPE = "arrival"
REQUEST_TIMEOUT_SECONDS = 30


def trias_utc(value: datetime) -> str:
    utc_value = value.astimezone(timezone.utc).replace(microsecond=0)
    return utc_value.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_request_body(
    target: Target,
    *,
    request_timestamp_utc: datetime,
    dep_arr_time: datetime | None = None,
    requestor_ref: str = "CologneTransitIntelligence",
) -> bytes:
    """Build the already-approved arrival StopEvent request shape."""

    request_timestamp = xml_escape(trias_utc(request_timestamp_utc))
    anchor = xml_escape(trias_utc(dep_arr_time or request_timestamp_utc))
    stop_point_ref = xml_escape(target.source_parent_station_id)
    requestor = xml_escape(requestor_ref)
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Trias xmlns="http://www.vdv.de/trias"\n'
        '       xmlns:siri="http://www.siri.org.uk/siri"\n'
        f'       version="{TRIAS_VERSION}">\n'
        '  <ServiceRequest>\n'
        f'    <siri:RequestTimestamp>{request_timestamp}</siri:RequestTimestamp>\n'
        f'    <siri:RequestorRef>{requestor}</siri:RequestorRef>\n'
        '    <RequestPayload>\n'
        '      <StopEventRequest>\n'
        '        <Location>\n'
        '          <LocationRef>\n'
        f'            <StopPointRef>{stop_point_ref}</StopPointRef>\n'
        '          </LocationRef>\n'
        f'          <DepArrTime>{anchor}</DepArrTime>\n'
        '        </Location>\n'
        '        <Params>\n'
        f'          <NumberOfResults>{target.number_of_results}</NumberOfResults>\n'
        f'          <StopEventType>{STOP_EVENT_TYPE}</StopEventType>\n'
        '          <IncludePreviousCalls>true</IncludePreviousCalls>\n'
        '          <IncludeOnwardCalls>true</IncludeOnwardCalls>\n'
        '          <IncludeRealtimeData>true</IncludeRealtimeData>\n'
        '        </Params>\n'
        '      </StopEventRequest>\n'
        '    </RequestPayload>\n'
        '  </ServiceRequest>\n'
        '</Trias>\n'
    )
    return body.encode("utf-8")
