"""Backend-neutral, read-only Stage 15A validation.

The package consumes an approved manifest and evidence adapter.  It never
claims a bucket, changes collector state, sends transport, or writes parsed
evidence.  SQLite support is a read-only adapter for disposable development
and durable live-research evidence; the :class:`EvidenceAdapter` protocol is
retained for backend-neutral analysis.
"""

from .analyzer import (
    STATUS_VOCABULARY,
    Stage15Analyzer,
    duration_statistics,
)
from .evidence import (
    EvidenceAdapter,
    EvidenceAdapterError,
    EvidenceData,
    InMemoryEvidenceAdapter,
    SQLiteEvidenceAdapter,
    evidence_from_mappings,
)
from .status import (
    STAGE_14_COLLECTOR_STATUS,
    STAGE_14_OVERALL_STATUS,
    STAGE_15_LIVE_STATUS,
    STAGE_15_READINESS_STATUS,
)

__all__ = [
    "EvidenceAdapter",
    "EvidenceAdapterError",
    "EvidenceData",
    "InMemoryEvidenceAdapter",
    "SQLiteEvidenceAdapter",
    "evidence_from_mappings",
    "STATUS_VOCABULARY",
    "Stage15Analyzer",
    "duration_statistics",
    "STAGE_14_OVERALL_STATUS",
    "STAGE_14_COLLECTOR_STATUS",
    "STAGE_15_READINESS_STATUS",
    "STAGE_15_LIVE_STATUS",
]
