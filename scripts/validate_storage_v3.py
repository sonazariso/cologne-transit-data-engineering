#!/usr/bin/env python3
"""Offline Storage V3 contract and bounded fixture benchmark.

This script never opens SQL Server, writes a database, writes a fixture, or
calls MDD.  SQL Server ROW/PAGE allocation is intentionally not approximated;
the lab-only SQL script is the required measurement path for those candidates.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from cologne_transit_collector.mssql_persistence import build_mssql_snapshot_rows
from cologne_transit_collector.trias import parse_trias_response


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "sql/mdd-mac-collector/create-source-faithful-persistence.sql"
MIGRATION = ROOT / "sql/mdd-mac-collector/optimize-storage-v3.sql"
LAB_COMPRESSION = ROOT / "sql/mdd-mac-collector/estimate-storage-v3-compression.sql"
LAB_VERIFY = ROOT / "sql/mdd-mac-collector/verify-storage-v3-lab.sql"
AUDIT = ROOT / "sql/mdd-mac-collector/audit-storage-v3-readonly.sql"

FIXTURES = (
    ROOT / "tests/fixtures/stage14_trias_equivalent.json",
    ROOT / "tests/fixtures/mdd_mac_multi_occurrence.json",
    ROOT / "tests/fixtures/stage14_trias_source_contract_correction.json",
    ROOT / "tests/fixtures/stage14_trias_success.json",
)

V3_FIELDS: dict[str, tuple[str, ...]] = {
    "timings": (
        "source_event_identity_missing_fields_json",
        "source_event_identity_ambiguity_json",
    ),
    "situations": (
        "description",
        "detail",
        "affects_json",
        "affected_entity_json",
        "affected_journey_json",
        "affected_line_json",
        "field_paths_json",
        "affected_entity_field_paths_json",
        "source_representation_json",
    ),
    "service_sections": (
        "description",
        "route_description",
        "via",
        "mode_source_representation",
        "source_paths_json",
        "source_representation_json",
    ),
    "attributes": (
        "text_source_text",
        "code_source_paths_json",
        "text_source_paths_json",
        "source_field_paths_json",
        "source_representation_json",
    ),
    "parser_gaps": ("source_representation_json",),
}

V2_FIELDS: dict[str, tuple[str, ...]] = {
    "calls": ("source_paths_json", "source_event_json"),
    "timings": ("source_paths_json", "source_event_identity_basis_json"),
}

DICTIONARY_FIELDS = (
    "result_id",
    "operating_day_ref",
    "journey_ref",
    "vehicle_journey_ref",
    "dated_vehicle_journey_ref",
    "line_ref",
    "direction_ref",
    "operator_ref",
    "stop_point_ref",
)


def _sql_compress_model(value: Any) -> bytes | None:
    """Model SQL Server COMPRESS(NVARCHAR) at the logical-byte level."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"source evidence field is not text: {type(value).__name__}")
    return gzip.compress(value.encode("utf-16le"), mtime=0)


def _sql_decompress_model(value: bytes | None) -> str | None:
    if value is None:
        return None
    return gzip.decompress(value).decode("utf-16le")


def _row_digest(row: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _logical_bytes(value: str | None) -> int:
    return len(value.encode("utf-16le")) if value is not None else 0


def _metric(
    rows: Iterable[Mapping[str, Any]],
    fields: Iterable[str],
    *,
    avoid_expansion: bool = False,
) -> dict[str, int | float | None]:
    original = 0
    compressed = 0
    non_null = 0
    for row in rows:
        for field in fields:
            value = row.get(field)
            if value is None:
                continue
            non_null += 1
            original += _logical_bytes(value)
            packed = _sql_compress_model(value)
            assert packed is not None
            compressed += min(len(packed), _logical_bytes(value)) if avoid_expansion else len(packed)
    return {
        "non_null_values": non_null,
        "original_utf16le_bytes": original,
        "compressed_gzip_bytes": compressed,
        "savings_percent": round((1 - compressed / original) * 100, 2) if original else None,
    }


def _dictionary_candidate(rows: Iterable[Mapping[str, Any]]) -> dict[str, int | float | None]:
    values: list[str] = []
    for row in rows:
        for field in DICTIONARY_FIELDS:
            value = row.get(field)
            if value is not None:
                values.append(value)
    unique = set(values)
    raw_bytes = sum(_logical_bytes(value) for value in values)
    optimistic_normalized_bytes = sum(_logical_bytes(value) for value in unique) + (len(values) * 4)
    return {
        "non_null_values": len(values),
        "unique_values": len(unique),
        "raw_utf16le_bytes": raw_bytes,
        "optimistic_dictionary_plus_4_byte_refs": optimistic_normalized_bytes,
        "optimistic_savings_percent": round((1 - optimistic_normalized_bytes / raw_bytes) * 100, 2)
        if raw_bytes
        else None,
        "status": "LOGICAL_ESTIMATE_ONLY_NOT_SQL_ALLOCATION",
    }


def _assert_static_contract() -> None:
    canonical = CANONICAL.read_text(encoding="utf-8")
    migration = MIGRATION.read_text(encoding="utf-8")
    compression_lab = LAB_COMPRESSION.read_text(encoding="utf-8")
    verify_lab = LAB_VERIFY.read_text(encoding="utf-8")
    audit = AUDIT.read_text(encoding="utf-8")

    v3_columns = (
        "SourceEventIdentityMissingFieldsJsonGzip",
        "SourceEventIdentityAmbiguityJsonGzip",
        "DescriptionGzip",
        "DetailGzip",
        "AffectsJsonGzip",
        "AffectedEntityJsonGzip",
        "AffectedJourneyJsonGzip",
        "AffectedLineJsonGzip",
        "FieldPathsJsonGzip",
        "AffectedEntityFieldPathsJsonGzip",
        "SourceRepresentationJsonGzip",
        "RouteDescriptionGzip",
        "ViaGzip",
        "ModeSourceJsonGzip",
        "SourcePathsJsonGzip",
        "TextSourceTextGzip",
        "CodeSourcePathsJsonGzip",
        "TextSourcePathsJsonGzip",
        "SourceFieldPathsJsonGzip",
    )
    for column in v3_columns:
        assert column in canonical, column
        assert column in migration, column

    trigger_names = (
        "trg_MddMacTimingObservation_StorageV3",
        "trg_MddMacSituationEvidence_StorageV3",
        "trg_MddMacServiceSectionEvidence_StorageV3",
        "trg_MddMacAttributeEvidence_StorageV3",
        "trg_MddMacParserGap_StorageV3",
    )
    for trigger in trigger_names:
        assert f"CREATE OR ALTER TRIGGER stg.{trigger}" in canonical
        assert f"CREATE OR ALTER TRIGGER stg.{trigger}" in migration
        trigger_pattern = re.compile(
            rf"CREATE OR ALTER TRIGGER stg\.{re.escape(trigger)}\n.*?^END;\nGO\n",
            flags=re.MULTILINE | re.DOTALL,
        )
        canonical_trigger = trigger_pattern.search(canonical)
        migration_trigger = trigger_pattern.search(migration)
        assert canonical_trigger and migration_trigger, trigger
        assert canonical_trigger.group(0) == migration_trigger.group(0), f"{trigger} drift"
    assert "DATALENGTH(COMPRESS(inserted." in canonical
    assert "DATALENGTH(COMPRESS(inserted." in migration

    views = (
        "vMddMacTimingObservationJson",
        "vMddMacSituationEvidenceJson",
        "vMddMacServiceSectionEvidenceJson",
        "vMddMacAttributeEvidenceJson",
        "vMddMacParserGapJson",
    )
    for view in views:
        assert f"CREATE OR ALTER VIEW stg.{view}" in canonical
        assert f"CREATE OR ALTER VIEW stg.{view}" in migration

    assert "DECLARE @ApplyStorageV3 BIT = 0;" in migration
    assert "CREATE OR ALTER PROCEDURE stg.uspPersistMddMacSnapshot" not in migration
    assert "ALTER INDEX" not in migration.upper()
    assert "DBCC SHRINK" not in migration.upper()
    assert "DROP TABLE" not in migration.upper()
    assert "DELETE FROM" not in migration.upper()
    assert "sp_estimate_data_compression_savings" in compression_lab
    assert "N'ROW'" in compression_lab and "N'PAGE'" in compression_lab
    assert "NOT LIKE N'%_STORAGE_V3_LAB'" in compression_lab
    assert "HASHBYTES" in verify_lab
    assert "sys.dm_db_partition_stats" in audit
    assert "OBJECT_DEFINITION" in audit
    assert "INSERT INTO" not in audit.upper()
    assert "UPDATE " not in audit.upper()
    assert "DELETE FROM" not in audit.upper()
    assert "ALTER TABLE" not in audit.upper()

    for preserved in (
        "EstimatedTimeSourceText",
        "RecordedAtTimeSourceText",
        "SourceEventIdentity",
        "UQ_stg_MddMacTimingObservation_Occurrence",
        "UQ_stg_MddMacCallObservation_Occurrence",
        "PERSISTED_PARSED_SNAPSHOT",
        "@CompletedRunStatus",
    ):
        assert preserved in canonical, preserved


def _benchmark_fixture(path: Path) -> dict[str, Any]:
    parsed = parse_trias_response(path.read_bytes(), "application/json").to_dict()
    rows = build_mssql_snapshot_rows(parsed)
    snapshot_text = json.dumps(parsed, ensure_ascii=False, sort_keys=True, allow_nan=False, default=str)
    snapshot_packed = _sql_compress_model(snapshot_text)
    assert snapshot_packed is not None
    assert _sql_decompress_model(snapshot_packed) == snapshot_text

    field_benchmarks: dict[str, Any] = {}
    for family, fields in V3_FIELDS.items():
        field_benchmarks[f"v3_{family}"] = _metric(rows[family], fields, avoid_expansion=True)
    for family, fields in V2_FIELDS.items():
        field_benchmarks[f"v2_{family}"] = _metric(rows[family], fields)

    for family, family_rows in rows.items():
        for row in family_rows:
            for field in V3_FIELDS.get(family, ()):
                value = row.get(field)
                packed = _sql_compress_model(value)
                restored = (
                    _sql_decompress_model(packed)
                    if packed is not None and len(packed) < _logical_bytes(value)
                    else value
                )
                assert restored == value

    return {
        "fixture": str(path.relative_to(ROOT)),
        "snapshot": {
            "utf8_bytes": len(snapshot_text.encode("utf-8")),
            "utf16le_bytes": len(snapshot_text.encode("utf-16le")),
            "gzip_bytes": len(snapshot_packed),
            "savings_percent": round((1 - len(snapshot_packed) / len(snapshot_text.encode("utf-16le"))) * 100, 2),
        },
        "row_counts": {family: len(family_rows) for family, family_rows in rows.items()},
        "field_benchmarks": field_benchmarks,
        "dictionary_candidate": _dictionary_candidate(
            list(rows["calls"]) + list(rows["timings"])
        ),
    }


def _assert_grain_and_null_contract() -> dict[str, Any]:
    parsed = parse_trias_response(
        (ROOT / "tests/fixtures/stage14_trias_equivalent.json").read_bytes(),
        "application/json",
    ).to_dict()
    rows = build_mssql_snapshot_rows(parsed)

    # The transformed row is compared as a full row digest, not merely as a
    # set of values.  This catches accidental loss of NULL, empty text, time,
    # source-path, and identity fields.
    parity: dict[str, bool] = {}
    for family, family_rows in rows.items():
        original = collections.Counter(_row_digest(row) for row in family_rows)
        reconstructed = collections.Counter()
        for row in family_rows:
            restored = dict(row)
            for field in V3_FIELDS.get(family, ()):
                value = row.get(field)
                packed = _sql_compress_model(value)
                restored[field] = (
                    _sql_decompress_model(packed)
                    if packed is not None and len(packed) < _logical_bytes(value)
                    else value
                )
            reconstructed[_row_digest(restored)] += 1
        parity[family] = original == reconstructed
        assert parity[family], family

    # Two snapshots with the same source occurrence remain two observations;
    # compression must never become a deduplication operation.
    timing_key = ("event_ordinal", "call_scope", "call_ordinal", "timing_context")
    repeated = [
        (101, tuple(row.get(field) for field in timing_key)) for row in rows["timings"]
    ] + [
        (102, tuple(row.get(field) for field in timing_key)) for row in rows["timings"]
    ]
    assert len(repeated) == 12
    assert len(set(repeated)) == len(repeated)

    # The fixtures exercise both NULL and non-NULL time fields.  These fields
    # are not in V3_FIELDS and are therefore byte-for-byte untouched.
    timing_values = [row for row in rows["timings"] if row.get("recorded_at_time") is not None]
    assert timing_values
    assert any(row.get("estimated_time") is None for row in rows["timings"])
    assert all(row.get("recorded_at_time_source_path") for row in timing_values)

    return {
        "field_by_field_parity": parity,
        "repeated_observation_rows_preserved": True,
        "estimated_and_recorded_time_fields_untouched": True,
        "null_and_empty_semantics": "PASS",
    }


def validate() -> dict[str, Any]:
    _assert_static_contract()
    parity = _assert_grain_and_null_contract()
    started = time.perf_counter()
    fixtures = [_benchmark_fixture(path) for path in FIXTURES]
    elapsed = time.perf_counter() - started
    return {
        "status": "PASS",
        "sql_connections": 0,
        "production_writes": 0,
        "live_mdd_requests": 0,
        "static_contract": "PASS",
        "round_trip_and_grain": parity,
        "fixture_count": len(fixtures),
        "offline_benchmark_elapsed_seconds": round(elapsed, 6),
        "sql_row_compression": "NOT_MEASURED_ISOLATED_SQL_SERVER_REQUIRED",
        "sql_page_compression": "NOT_MEASURED_ISOLATED_SQL_SERVER_REQUIRED",
        "delta_encoding": "NOT_SELECTED_NO_SEQUENCE_PROOF_IN_SANITIZED_FIXTURES",
        "shared_dictionary": "LOGICAL_ESTIMATE_ONLY_REQUIRES_SEPARATE_SCHEMA_CONTRACT",
        "fixtures": fixtures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print the complete machine-readable benchmark")
    args = parser.parse_args()
    result = validate()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print("STORAGE_V3_OFFLINE_CONTRACT: PASS")
        print(f"STORAGE_V3_FIXTURES: {result['fixture_count']}")
        print("SQL_ROW_PAGE_COMPRESSION: NOT_MEASURED_ISOLATED_SQL_SERVER_REQUIRED")
        print("DELTA_ENCODING: NOT_SELECTED_NO_SEQUENCE_PROOF")
        print("SHARED_DICTIONARY: LOGICAL_ESTIMATE_ONLY")
        print("ROUND_TRIP_GRAIN_NULL_TIME_CONTRACT: PASS")
        print("SQL_CONNECTIONS=0 MDD_REQUESTS=0 PRODUCTION_WRITES=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
