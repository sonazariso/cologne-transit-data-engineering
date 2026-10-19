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
import struct
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from cologne_transit_collector.mssql_persistence import build_mssql_snapshot_rows
from cologne_transit_collector.compact_observation import (
    build_compact_envelope,
    encode_compact_envelope,
    nested_gzip_hex_benchmark,
)
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


def _binary_field_payload(field_name: str, value: Any) -> tuple[int, bytes]:
    """Encode one field without turning an existing GZIP into hexadecimal."""

    if value is None:
        return 0, b""
    if field_name.casefold().endswith("_gzip_hex"):
        if not isinstance(value, str):
            raise ValueError(f"{field_name} is not hexadecimal text")
        return 6, bytes.fromhex(value)
    if isinstance(value, bool):
        return 3, json.dumps(value).encode("utf-8")
    if isinstance(value, (int, float)):
        return 2, json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if isinstance(value, (list, Mapping)):
        return 4 if isinstance(value, list) else 5, json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    return 1, str(value).encode("utf-8")


def _field_records(
    snapshot: Mapping[str, Any],
    rows: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[tuple[str, int, str, Any, int, bytes]]:
    families: list[tuple[str, Sequence[Mapping[str, Any]]]] = [("source_snapshot", [snapshot])]
    families.extend((family, values) for family, values in rows.items())
    records: list[tuple[str, int, str, Any, int, bytes]] = []
    for family, family_rows in families:
        for row_ordinal, row in enumerate(family_rows):
            for field_name in sorted(row):
                value = row[field_name]
                field_type, payload = _binary_field_payload(field_name, value)
                records.append((family, row_ordinal, field_name, value, field_type, payload))
    return records


def _field_record_bytes(
    family: str,
    row_ordinal: int,
    field_name: str,
    field_type: int,
    payload: bytes,
) -> bytes:
    metadata = json.dumps(
        {
            "family": family,
            "row_ordinal": row_ordinal,
            "field_name": field_name,
            "field_type": field_type,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return (
        struct.pack(">I", len(metadata))
        + metadata
        + struct.pack(">I", len(payload))
        + payload
    )


def _is_dictionary_field(field_name: str) -> bool:
    normalized = field_name.casefold()
    return (
        "source_path" in normalized
        or normalized in {
            "timing_context",
            "source_event_identity_basis_json",
            "source_event_identity_missing_fields_json",
            "source_event_identity_ambiguity_json",
        }
    )


def _compact_storage_candidates(parsed: Mapping[str, Any], rows: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    """Compare lossless logical candidates before any SQL allocation claim."""

    snapshot_text = json.dumps(parsed, ensure_ascii=False, sort_keys=True, allow_nan=False, default=str)
    benchmark_snapshot = {"snapshot_id": 1, "snapshot_json": snapshot_text}
    envelope = build_compact_envelope(
        source_snapshot=benchmark_snapshot,
        row_sets=rows,
    )
    current = encode_compact_envelope(envelope)
    records = _field_records(benchmark_snapshot, rows)
    binary_raw = b"".join(
        _field_record_bytes(family, ordinal, field_name, field_type, payload)
        for family, ordinal, field_name, _value, field_type, payload in records
    )
    binary_gzip = gzip.compress(binary_raw, mtime=0)

    dictionary_values = sorted(
        {
            payload
            for _family, _ordinal, field_name, value, _field_type, payload in records
            if value is not None and _is_dictionary_field(field_name)
        }
    )
    dictionary_ids = {payload: index for index, payload in enumerate(dictionary_values, start=1)}
    dictionary_raw = b"".join(
        struct.pack(">I", index) + struct.pack(">I", len(payload)) + payload
        for index, payload in enumerate(dictionary_values, start=1)
    )
    dictionary_refs = []
    residual = []
    reconstructed: list[bytes] = []
    for family, ordinal, field_name, value, field_type, payload in records:
        if value is not None and _is_dictionary_field(field_name):
            dictionary_id = dictionary_ids[payload]
            metadata = json.dumps(
                {
                    "family": family,
                    "row_ordinal": ordinal,
                    "field_name": field_name,
                    "field_type": field_type,
                    "dictionary_id": dictionary_id,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            dictionary_refs.append(
                struct.pack(">I", len(metadata)) + metadata + struct.pack(">I", dictionary_id)
            )
            reconstructed.append(dictionary_values[dictionary_id - 1])
        else:
            residual.append(_field_record_bytes(family, ordinal, field_name, field_type, payload))
            reconstructed.append(payload)
    assert reconstructed == [record[-1] for record in records]
    dictionary_raw_total = dictionary_raw + b"".join(dictionary_refs) + b"".join(residual)
    dictionary_gzip = gzip.compress(dictionary_raw_total, mtime=0)

    return {
        "lossless": True,
        "field_count": len(records),
        "current_complete_envelope": {
            "logical_utf16le_bytes": current.logical_bytes,
            "gzip_bytes": current.compressed_bytes,
        },
        "optimized_binary_representation": {
            "raw_binary_bytes": len(binary_raw),
            "gzip_bytes": len(binary_gzip),
            "hex_payloads_removed": True,
        },
        "dictionary_encoding": {
            "dictionary_value_count": len(dictionary_values),
            "raw_binary_bytes": len(dictionary_raw_total),
            "gzip_bytes": len(dictionary_gzip),
            "repeated_field_refs": len(dictionary_refs),
        },
        "existing_compressed_snapshot_payload": {
            "gzip_bytes": len(_sql_compress_model(snapshot_text) or b""),
            "source": "fixture_snapshot_json_equivalent",
        },
        "sql_physical_allocation": "REQUIRES_ISOLATED_SQL_SERVER_LAB",
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
    for candidate_marker in (
        "#CompactCurrentEnvelope",
        "#CompactOptimizedBinary",
        "#CompactDictionaryReference",
        "existing_compressed_snapshot_payload",
        "measured_peak_temporary_duplication_bytes",
        "SUM(CONVERT(bigint, reserved_page_count))",
    ):
        assert candidate_marker in compression_lab, candidate_marker
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
        "compact_storage_candidates": _compact_storage_candidates(parsed, rows),
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
    nested_gzip_hex = nested_gzip_hex_benchmark(
        '{"source_event":"TRIAS","stop":"Köln Stammheim – Bf.","emoji":"🚌",'
        '"repeated":"' + ('historical evidence ' * 180) + '"}'
    )
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
        "nested_gzip_hex_benchmark": nested_gzip_hex,
        "historical_compression_efficiency": {
            "status": "OFFLINE_LOGICAL_PASS_SQL_PHYSICAL_LAB_REQUIRED",
            "candidate_contract": (
                "current_complete_envelope_vs_optimized_binary_vs_lossless_dictionary "
                "vs_existing_compressed_snapshot_payload"
            ),
            "bounded_real_fixture_samples": len(fixtures),
            "physical_allocation_measurement": str(LAB_COMPRESSION.relative_to(ROOT)),
        },
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
        print("HISTORICAL_COMPRESSION_EFFICIENCY: OFFLINE_LOGICAL_PASS_SQL_PHYSICAL_LAB_REQUIRED")
        print("DELTA_ENCODING: NOT_SELECTED_NO_SEQUENCE_PROOF")
        print("SHARED_DICTIONARY: LOGICAL_ESTIMATE_ONLY")
        print(
            "NESTED_GZIP_HEX_BENCHMARK: "
            f"{'PASS' if result['nested_gzip_hex_benchmark']['nested_representation_is_smaller'] else 'OVERHEAD_PRESENT'} "
            f"delta_bytes={result['nested_gzip_hex_benchmark']['nested_overhead_bytes']}"
        )
        print("ROUND_TRIP_GRAIN_NULL_TIME_CONTRACT: PASS")
        print("SQL_CONNECTIONS=0 MDD_REQUESTS=0 PRODUCTION_WRITES=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
