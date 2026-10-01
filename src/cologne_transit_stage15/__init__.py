"""Backend-neutral, read-only Stage 15A validation.

The package consumes an approved manifest and evidence adapter.  It never
claims a bucket, changes collector state, sends transport, or writes parsed
evidence.  SQLite support is deliberately a read-only development adapter;
the :class:`EvidenceAdapter` protocol is the seam for a future production
evidence backend.
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
]
