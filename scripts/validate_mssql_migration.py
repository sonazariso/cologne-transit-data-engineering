#!/usr/bin/env python3
"""Validate the additive collector migration without changing the database.

With ``--live`` this performs read-only object and baseline checks through the
same DB-API settings as the collector.  Applying the migration is a separate
explicit command so a validation run cannot mutate the authoritative database.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    MssqlPersistence,
)


MIGRATION = ROOT / "sql" / "mdd-mac-collector" / "create-source-faithful-persistence.sql"
FORBIDDEN_MUTATIONS = re.compile(
    r"\b(?:DROP\s+TABLE|DROP\s+DATABASE|TRUNCATE\s+TABLE|DBCC\s+SHRINK|DBCC\s+CHECKIDENT|DELETE\s+FROM)\b",
    re.IGNORECASE,
)
REQUIRED_MARKERS = (
    "ctl.MddMacCollectorRun",
    "stg.MddMacSourceSnapshot",
    "stg.MddMacTimingObservation",
    "stg.MddMacSituationEvidence",
    "stg.MddMacServiceSectionEvidence",
    "stg.MddMacAttributeEvidence",
    "stg.MddMacParserGap",
    "ctl.MddImportBatch",
    "ctl.MddImportTableAudit",
    "ctl.MddImportEvidenceAudit",
    "ctl.MddImportSourceRow",
    "ctl.MddImportOverlap",
    "stg.uspPersistMddMacSnapshot",
    "analytics.vwMddMacRecordedAtTimeHistory",
)


def split_batches(sql: str) -> list[str]:
    """Split sqlcmd-style GO separators without interpreting SQL."""

    return [batch.strip() for batch in re.split(r"(?im)^\s*GO\s*(?:--.*)?$", sql) if batch.strip()]


def validate_static(path: Path = MIGRATION) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    if FORBIDDEN_MUTATIONS.search(text):
        errors.append("migration contains a destructive statement")
    for marker in REQUIRED_MARKERS:
        if marker not in text:
            errors.append(f"migration is missing required marker: {marker}")
    batches = split_batches(text)
    if len(batches) < 20:
        errors.append("migration does not contain the expected additive batch structure")
    if "CREATE OR ALTER PROCEDURE stg.uspPersistMddMacSnapshot" not in text:
        errors.append("persistence procedure is not idempotent")
    if "CREATE OR ALTER VIEW analytics.vwMddMacRecordedAtTimeHistory" not in text:
        errors.append("RecordedAtTime reporting view is not idempotent")
    if "RecordedAtTimeSourceText" not in text or "RecordedAtTimeSourcePath" not in text:
        errors.append("RecordedAtTime lexical value/path columns are missing")
    if "CHECK (Status IN ('IN_PROGRESS', 'COMPLETE', 'FAILED'))" not in text:
        errors.append("import-batch status lifecycle is not explicit")
    if "UQ_ctl_MddImportSourceRow_Source_Lineage" not in text:
        errors.append("durable source-lineage uniqueness is missing")
    return {
        "status": "PASS" if not errors else "FAIL",
        "migration": str(path),
        "batch_count": len(batches),
        "errors": errors,
    }


def validate_live() -> dict[str, object]:
    adapter = MssqlPersistence(MssqlConnectionSettings.from_environment())
    schema = adapter.validate_schema()
    baseline = adapter.baseline()
    return {"status": "PASS", "schema": schema, "baseline": baseline}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="perform read-only checks against configured database")
    args = parser.parse_args(argv)
    result = validate_static()
    if result["status"] == "PASS" and args.live:
        try:
            result["live"] = validate_live()
        except Exception as exc:  # diagnostics must not expose credentials
            result = {
                **result,
                "status": "FAIL",
                "live": {"status": "FAIL", "error_type": type(exc).__name__},
            }
    import json

    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
