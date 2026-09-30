"""MDD client ports and standard-library implementations."""

from __future__ import annotations

import os
import socket
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .domain import HttpResponse, Target, TransportFailure
from .request import build_request_body


MDD_API_KEY_ENV = "MDD_API_KEY"
DEFAULT_MAX_RESPONSE_BYTES = 25 * 1024 * 1024


class MddCredentialError(RuntimeError):
    """The live client cannot start without the inherited secret environment value."""


class MddLiveDisabled(RuntimeError):
    """Live HTTP requires an explicit opt-in even when used as a library."""


def _read_bounded(stream: object, maximum_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(min(1024 * 1024, maximum_bytes - total))  # type: ignore[attr-defined]
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum_bytes:
            raise ValueError("response exceeded bounded size")
    return b"".join(chunks)


def _headers(response: object) -> dict[str, str]:
    raw = getattr(response, "headers", None)
    if raw is None:
        return {}
    try:
        return {str(key): str(value) for key, value in raw.items()}
    except AttributeError:
        return {}


def _transport_category(error: BaseException) -> str:
    if isinstance(error, (TimeoutError, socket.timeout)):
        return "TIMEOUT"
    if isinstance(error, socket.gaierror):
        return "DNS_RESOLUTION_FAILED"
    reason = getattr(error, "reason", None)
    if isinstance(reason, socket.gaierror):
        return "DNS_RESOLUTION_FAILED"
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "TIMEOUT"
    if isinstance(error, ConnectionRefusedError):
        return "CONNECTION_REFUSED"
    if isinstance(error, URLError):
        return "OTHER_NETWORK_FAILURE"
    return "OTHER_NETWORK_FAILURE"


class LiveMddClient:
    """Real POST client; network use occurs only when ``fetch`` is called."""

    live_network = True

    def __init__(
        self,
        endpoint: str,
        *,
        requestor_ref: str = "CologneTransitIntelligence",
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        urlopen_fn: Callable[..., object] = urlopen,
        enabled: bool = False,
    ) -> None:
        self.endpoint = endpoint
        self.requestor_ref = requestor_ref
        self.max_response_bytes = max_response_bytes
        self._urlopen = urlopen_fn
        self.enabled = bool(enabled)

    def fetch(
        self,
        target: Target,
        *,
        request_timestamp_utc,
        timeout_seconds: float,
        attempt_number: int,
    ) -> HttpResponse | TransportFailure:
        if not self.enabled:
            raise MddLiveDisabled("live MDD HTTP requires explicit enabled=True opt-in")
        api_key = os.environ.get(MDD_API_KEY_ENV)
        if not api_key:
            raise MddCredentialError("MDD_API_KEY is not available")
        body = build_request_body(
            target,
            request_timestamp_utc=request_timestamp_utc,
            requestor_ref=self.requestor_ref,
        )
        request = Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/xml; charset=utf-8",
                "Accept": "application/json, application/xml",
                "x-api-key": api_key,
            },
        )
        started = time.monotonic()
        try:
            response = self._urlopen(request, timeout=float(timeout_seconds))
            try:
                content = _read_bounded(response, self.max_response_bytes)
                status = int(getattr(response, "status", getattr(response, "code", 200)))
                return HttpResponse(status, content, _headers(response), time.monotonic() - started)
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        except HTTPError as error:
            try:
                content = _read_bounded(error, self.max_response_bytes)
            except Exception:
                content = b""
            return HttpResponse(int(error.code), content, _headers(error), time.monotonic() - started)
        except (OSError, URLError, TimeoutError, ValueError) as error:
            if isinstance(error, ValueError) and str(error) == "response exceeded bounded size":
                return TransportFailure("OTHER_NETWORK_FAILURE", "bounded response failure")
            return TransportFailure(_transport_category(error), "transport failure")


class FakeMddClient:
    """Thread-safe scripted client used by dry-run and simulation modes."""

    live_network = False

    def __init__(
        self,
        responses: Mapping[str, Sequence[HttpResponse | TransportFailure | BaseException]] | None = None,
        *,
        default_response: HttpResponse | None = None,
    ) -> None:
        self._scripts = {key: deque(values) for key, values in (responses or {}).items()}
        self.default_response = default_response or HttpResponse(200, b'{"serviceDelivery":{"status":"OK"}}', {"Content-Type": "application/json"})
        self.calls: list[dict[str, object]] = []
        self._lock = threading.Lock()

    def fetch(
        self,
        target: Target,
        *,
        request_timestamp_utc,
        timeout_seconds: float,
        attempt_number: int,
    ) -> HttpResponse | TransportFailure:
        with self._lock:
            self.calls.append(
                {
                    "source_parent_station_id": target.source_parent_station_id,
                    "attempt_number": attempt_number,
                    "number_of_results": target.number_of_results,
                    "request_timestamp_utc": request_timestamp_utc,
                }
            )
            script = self._scripts.get(target.source_parent_station_id)
            action = script.popleft() if script else self.default_response
        if isinstance(action, BaseException):
            raise action
        return action

    def call_count(self, source_parent_station_id: str | None = None) -> int:
        with self._lock:
            if source_parent_station_id is None:
                return len(self.calls)
            return sum(item["source_parent_station_id"] == source_parent_station_id for item in self.calls)


class FixtureMddClient(FakeMddClient):
    """Deterministic fixture-backed client; it never opens a network socket."""

    def __init__(self, fixture_path: str | Path, *, content_type: str = "application/json") -> None:
        body = Path(fixture_path).read_bytes()
        super().__init__(default_response=HttpResponse(200, body, {"Content-Type": content_type}))
