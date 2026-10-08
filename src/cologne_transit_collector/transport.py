"""MDD client ports and standard-library implementations."""

from __future__ import annotations

import os
import errno
import socket
import ssl
import threading
import time
from collections import defaultdict, deque
from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .domain import HttpResponse, Target, TransportFailure
from .request import build_request_body


MDD_API_KEY_ENV = "MDD_API_KEY"
DEFAULT_MAX_RESPONSE_BYTES = 25 * 1024 * 1024
RESPONSE_SIZE_EXCEEDED = "RESPONSE_SIZE_EXCEEDED"
CREDENTIAL_MISSING = "CREDENTIAL_MISSING"
CREDENTIAL_FORMAT_INVALID = "CREDENTIAL_FORMAT_INVALID"
REQUEST_CONTRACT_FAILURE = "REQUEST_CONTRACT_FAILURE"
LIVE_TRANSPORT_KIND = "LIVE_CLIENT"
SIMULATED_TRANSPORT_KIND = "SIMULATED_TRANSPORT"


class MddCredentialError(RuntimeError):
    """The live client cannot start without the inherited secret environment value."""


class MddLiveDisabled(RuntimeError):
    """Live HTTP requires an explicit opt-in even when used as a library."""

    network_attempted = False
    http_response_received = False
    live_client_invocation = True
    transport_execution_entered = False


class NoRedirectHandler(HTTPRedirectHandler):
    """Surface 3xx responses instead of following them implicitly."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class ResponseTooLarge(ValueError):
    """The bounded reader observed bytes beyond the configured maximum."""


def _read_bounded(stream: object, maximum_bytes: int) -> bytes:
    if maximum_bytes < 0:
        raise ValueError("maximum response size must not be negative")
    chunks: list[bytes] = []
    total = 0
    while True:
        remaining_plus_probe = maximum_bytes - total + 1
        if remaining_plus_probe <= 0:
            raise ResponseTooLarge("response exceeded bounded byte limit")
        chunk = stream.read(min(1024 * 1024, remaining_plus_probe))  # type: ignore[attr-defined]
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum_bytes:
            raise ResponseTooLarge("response exceeded bounded byte limit")
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
    candidates: list[BaseException] = [error]
    reason = getattr(error, "reason", None)
    if isinstance(reason, BaseException) and reason is not error:
        candidates.insert(0, reason)
    for candidate in candidates:
        if isinstance(candidate, (TimeoutError, socket.timeout)):
            return "TIMEOUT"
        if isinstance(candidate, (ssl.SSLError, ssl.CertificateError)):
            return "TLS_FAILURE"
        if isinstance(candidate, (ConnectionRefusedError,)):
            return "CONNECTION_REFUSED"
        candidate_errno = getattr(candidate, "errno", None)
        if candidate_errno == errno.ECONNREFUSED:
            return "CONNECTION_REFUSED"
        if candidate_errno in {errno.ENETUNREACH, errno.EHOSTUNREACH}:
            return "NETWORK_UNREACHABLE"
        if isinstance(candidate, socket.gaierror):
            return "DNS_RESOLUTION_FAILED"
        if candidate_errno in {
            getattr(socket, "EAI_AGAIN", object()),
            getattr(socket, "EAI_FAIL", object()),
            getattr(socket, "EAI_NONAME", object()),
            getattr(socket, "EAI_NODATA", object()),
        }:
            return "DNS_RESOLUTION_FAILED"
        type_name = type(candidate).__name__.casefold()
        safe_marker = str(candidate).casefold()
        if "proxy" in type_name or "proxy" in safe_marker or "tunnel connection failed" in safe_marker:
            return "PROXY_OR_ENVIRONMENT_FAILURE"
        if any(marker in safe_marker for marker in (
            "name or service not known",
            "nodename nor servname",
            "temporary failure in name resolution",
            "no such host",
            "getaddrinfo failed",
        )):
            return "DNS_RESOLUTION_FAILED"
        if "connection refused" in safe_marker:
            return "CONNECTION_REFUSED"
        if any(marker in safe_marker for marker in ("network is unreachable", "host is unreachable")):
            return "NETWORK_UNREACHABLE"
        if "tls" in type_name or "ssl" in type_name or "certificate" in type_name:
            return "TLS_FAILURE"
    return "OTHER_NETWORK_FAILURE"


def validate_api_key_format(api_key: object) -> str:
    """Validate a header-safe key without returning or logging its value."""

    if not api_key:
        return CREDENTIAL_MISSING
    if not isinstance(api_key, str):
        return CREDENTIAL_FORMAT_INVALID
    try:
        encoded = api_key.encode("ascii")
    except UnicodeEncodeError:
        return CREDENTIAL_FORMAT_INVALID
    if not encoded or any(byte < 0x21 or byte > 0x7E for byte in encoded):
        return CREDENTIAL_FORMAT_INVALID
    return "VALID"


class LiveMddClient:
    """Real POST client; network use occurs only when ``fetch`` is called."""

    live_network = True
    offline_safe = False

    def __init__(
        self,
        endpoint: str,
        *,
        requestor_ref: str = "CologneTransitIntelligence",
        api_key: str | None = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        urlopen_fn: Callable[..., object] | None = None,
        opener_factory: Callable[[], object] | None = None,
        enabled: bool = False,
    ) -> None:
        self.endpoint = endpoint
        self.requestor_ref = requestor_ref
        # Production launchd resolves the credential from Keychain and keeps
        # it in this process object.  ``None`` preserves the existing
        # environment-variable path for tests, CI, and explicit diagnostics.
        self._api_key = api_key
        self.max_response_bytes = max_response_bytes
        self._urlopen = urlopen_fn
        self._opener_factory = opener_factory or (lambda: build_opener(NoRedirectHandler()))
        self.enabled = bool(enabled)

    def credential_status(self) -> str:
        """Return only the API credential status, never the credential."""

        api_key = self._api_key if self._api_key is not None else os.environ.get(MDD_API_KEY_ENV)
        return validate_api_key_format(api_key)

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
        api_key = self._api_key if self._api_key is not None else os.environ.get(MDD_API_KEY_ENV)
        credential_status = self.credential_status()
        if credential_status != "VALID":
            return TransportFailure(
                credential_status,
                "credential preflight failed",
                network_attempted=False,
                http_response_received=False,
                transport_kind=LIVE_TRANSPORT_KIND,
                live_client_invocation=True,
                transport_execution_entered=False,
            )
        try:
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
        except Exception:
            return TransportFailure(
                REQUEST_CONTRACT_FAILURE,
                "request contract preflight failed",
                network_attempted=False,
                http_response_received=False,
                transport_kind=LIVE_TRANSPORT_KIND,
                live_client_invocation=True,
                transport_execution_entered=False,
            )
        started = time.monotonic()
        transport_execution_entered = False
        try:
            transport_execution_entered = True
            if self._urlopen is not None:
                # Injected callables are test seams; the production default
                # always goes through the no-redirect opener below.
                response = self._urlopen(request, timeout=float(timeout_seconds))
            else:
                opener = self._opener_factory()
                response = opener.open(request, timeout=float(timeout_seconds))  # type: ignore[attr-defined]
            content: bytes | None = None
            response_status: int | None = None
            read_failure: BaseException | None = None
            try:
                try:
                    content = _read_bounded(response, self.max_response_bytes)
                    response_status = int(getattr(response, "status", getattr(response, "code", 200)))
                except ResponseTooLarge:
                    return TransportFailure(
                        RESPONSE_SIZE_EXCEEDED,
                        "bounded response failure",
                        network_attempted=True,
                        http_response_received=True,
                        transport_kind=LIVE_TRANSPORT_KIND,
                        live_client_invocation=True,
                        transport_execution_entered=True,
                    )
                except Exception as error:
                    read_failure = error
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception as error:
                        # The body may or may not have been complete when the
                        # close failed.  Preserve quota conservatism without
                        # claiming a successful response.
                        return TransportFailure(
                            "RESPONSE_CLOSE_FAILURE",
                            "response close failure",
                            network_attempted=True,
                            http_response_received=False,
                            transport_kind=LIVE_TRANSPORT_KIND,
                            live_client_invocation=True,
                            transport_execution_entered=True,
                        )
            if read_failure is not None:
                return TransportFailure(
                    "RESPONSE_READ_FAILURE",
                    "response read failure",
                    network_attempted=True,
                    http_response_received=False,
                    transport_kind=LIVE_TRANSPORT_KIND,
                    live_client_invocation=True,
                    transport_execution_entered=True,
                )
            if content is None or response_status is None:
                return TransportFailure(
                    "RESPONSE_READ_FAILURE",
                    "response body was not available",
                    network_attempted=True,
                    http_response_received=False,
                    transport_kind=LIVE_TRANSPORT_KIND,
                    live_client_invocation=True,
                    transport_execution_entered=True,
                )
            return HttpResponse(
                response_status,
                content,
                _headers(response),
                time.monotonic() - started,
                network_attempted=True,
                http_response_received=True,
                transport_kind=LIVE_TRANSPORT_KIND,
                live_client_invocation=True,
                transport_execution_entered=True,
            )
        except HTTPError as error:
            try:
                content = _read_bounded(error, self.max_response_bytes)
            except ResponseTooLarge:
                return TransportFailure(
                    RESPONSE_SIZE_EXCEEDED,
                    "bounded response failure",
                    network_attempted=True,
                    http_response_received=True,
                    transport_kind=LIVE_TRANSPORT_KIND,
                    live_client_invocation=True,
                    transport_execution_entered=True,
                )
            except Exception:
                content = b""
            return HttpResponse(
                int(error.code),
                content,
                _headers(error),
                time.monotonic() - started,
                network_attempted=True,
                http_response_received=True,
                transport_kind=LIVE_TRANSPORT_KIND,
                live_client_invocation=True,
                transport_execution_entered=True,
            )
        except ResponseTooLarge:
            return TransportFailure(
                RESPONSE_SIZE_EXCEEDED,
                "bounded response failure",
                network_attempted=True,
                http_response_received=True,
                transport_kind=LIVE_TRANSPORT_KIND,
                live_client_invocation=True,
                transport_execution_entered=True,
            )
        except ValueError:
            return TransportFailure(
                REQUEST_CONTRACT_FAILURE,
                "request contract failure",
                # The exception occurred after entering the transport path;
                # it is not evidence that the provider received no bytes.
                network_attempted=False,
                http_response_received=False,
                transport_kind=LIVE_TRANSPORT_KIND,
                live_client_invocation=True,
                transport_execution_entered=transport_execution_entered,
            )
        except (OSError, URLError, TimeoutError) as error:
            return TransportFailure(
                _transport_category(error),
                "transport failure",
                network_attempted=True,
                http_response_received=False,
                transport_kind=LIVE_TRANSPORT_KIND,
                live_client_invocation=True,
                transport_execution_entered=transport_execution_entered,
            )
        except Exception:
            return TransportFailure(
                "CLIENT_EXECUTION_FAILURE",
                "live transport execution failure",
                # A generic error after entering the transport path is
                # ambiguous, not proof of a transmitted request.
                network_attempted=False,
                http_response_received=False,
                transport_kind=LIVE_TRANSPORT_KIND,
                live_client_invocation=True,
                transport_execution_entered=transport_execution_entered,
            )


class FakeMddClient:
    """Thread-safe scripted client used by dry-run and simulation modes."""

    live_network = False
    offline_safe = True

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
        if isinstance(action, HttpResponse):
            return replace(
                action,
                network_attempted=False,
                http_response_received=True,
                transport_kind=SIMULATED_TRANSPORT_KIND,
                live_client_invocation=False,
            )
        if isinstance(action, TransportFailure):
            return replace(
                action,
                network_attempted=False,
                http_response_received=False,
                transport_kind=SIMULATED_TRANSPORT_KIND,
                live_client_invocation=False,
            )
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
