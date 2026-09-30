"""Stage 14 collector-first runtime.

The package is intentionally persistence-neutral at its core.  SQLite is
provided as disposable local development infrastructure; a production
database adapter and deployment platform remain deferred.
"""

from .domain import (
    APPROVED_TRANSIENT_HTTP_STATUS_CODES,
    HEARTBEAT_SECONDS,
    MAX_ATTEMPTS,
    MAXIMUM_CONCURRENCY,
    PANEL_VERSION,
    REQUEST_TIMEOUT_SECONDS,
    SamplingEra,
    Target,
)

__all__ = [
    "APPROVED_TRANSIENT_HTTP_STATUS_CODES",
    "HEARTBEAT_SECONDS",
    "MAX_ATTEMPTS",
    "MAXIMUM_CONCURRENCY",
    "PANEL_VERSION",
    "REQUEST_TIMEOUT_SECONDS",
    "SamplingEra",
    "Target",
]
