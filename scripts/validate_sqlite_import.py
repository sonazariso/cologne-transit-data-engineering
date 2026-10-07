#!/usr/bin/env python3
"""Validate one completed LIVE_RESEARCH SQLite import without writing data.

The validator repeats the source proof, capacity gate, failed-batch audit, and
post-import reconciliation against the explicit source SHA.  It keeps the
durable ``service_section_observations`` table separate from the parsed
snapshot ServiceSection projection: the SQL snapshot procedure may already
have persisted a projection row, while the source table remains the authority
for the complete imported ServiceSection count.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    build_mssql_snapshot_rows,
    connect_mssql,
)
from cologne_transit_collector.sqlite_import import (  # noqa: E402
    EXPECTED_SQL_DATABASE,
    SQLiteImportError,
    inspect_sqlite_source,
    read_only_capacity_audit,
    read_only_failed_import_audit,
)


EXPECTED_SOURCE_PATH = Path("/Users/admin/cologne-transit-data/live-research/collector.sqlite3")
EXPECTED_SOURCE_SIZE = 575_041_536
EXPECTED_SOURCE_SHA256 = "0edc9b1a56cb6128ac5bed6508e5c37b9b4d1ab18c1d78092e6fcb1ac9b674fc"


def _redact(message: str) -> str:
    message = " ".join(str(message).split())
    return re.sub(
        r"(?i)(password|token|secret|authorization|api[_-]?key)\s*[=:]\s*[^,; ]+",
        r"\1=<redacted>",
        message,
    )[:2000]


def _settings(args: argparse.Namespace) -> MssqlConnectionSettings:
    values = dict(os.environ)
    for name, value in (
        ("MSSQL_HOST", args.mssql_host),
        ("MSSQL_DATABASE", args.mssql_database),
        ("MSSQL_USER", args.mssql_user),
    ):
        if value:
            values[name] = value
    if args.mssql_port is not None:
        values["MSSQL_PORT"] = str(args.mssql_port)
    if args.mssql_password_env:
        password = values.get(args.mssql_password_env)
        if password:
            values["MSSQL_PASSWORD"] = password
    settings = MssqlConnectionSettings.from_environment(values)
    if settings.database != EXPECTED_SQL_DATABASE:
        raise SQLiteImportError(f"SQL Server target database must be {EXPECTED_SQL_DATABASE}")
    return settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="explicit proven SQLite source path")
    parser.add_argument("--report", type=Path, help="optional JSON report path")
    parser.add_argument("--mssql-host")
    parser.add_argument("--mssql-port", type=int)
    parser.add_argument("--mssql-database", default=EXPECTED_SQL_DATABASE)
    parser.add_argument("--mssql-user")
    parser.add_argument("--mssql-password-env", default="MSSQL_PASSWORD")
    return parser


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if row is None:
        return None
    if isinstance(row, Mapping):
        return row.get(key)
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return None


def _int_value(value: Any) -> int:
    return int(value or 0)


def _source_evidence(source: Path) -> dict[str, Any]:
    """Count source tables and parsed child projections using read-only SQLite."""

    proof, inventory = inspect_sqlite_source(source)
    connection = sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        table_counts = {row.table_name: row.row_count for row in inventory}
        parsed_children: Counter[str] = Counter()
        attribute_types: Counter[str] = Counter()
        incident_call_count = 0
        info_call_count = 0
        affected_entity_count = 0
        provider_association_count = 0
        root_cause_field_occurrences = 0
        recorded_paths: Counter[str | None] = Counter()
        recorded_values: Counter[str | None] = Counter()
        for row in connection.execute("SELECT snapshot_json FROM parsed_snapshots ORDER BY rowid"):
            parsed = json.loads(str(row["snapshot_json"]))
            row_sets = build_mssql_snapshot_rows(parsed)
            for family, values in row_sets.items():
                parsed_children[family] += len(values)
            for attribute in row_sets["attributes"]:
                attribute_types[str(attribute.get("attribute_type") or "UNKNOWN")] += 1
                incident_call_count += int(bool(attribute.get("incident_call_source_path")))
                info_call_count += int(bool(attribute.get("info_call_source_path")))
            for situation in row_sets["situations"]:
                affected_entity_count += int(bool(situation.get("affected_entity_json")))
                provider_association_count += int(bool(situation.get("provider_source") or situation.get("provider_source_type")))
            root_cause_field_occurrences += sum(
                1 for key in json.dumps(parsed, ensure_ascii=False).lower().split('"') if key == "rootcause" or key == "root_cause"
            )
        for row in connection.execute(
            "SELECT recorded_at_time, recorded_at_time_source_path FROM timing_observations"
        ):
            value = row["recorded_at_time"]
            path = row["recorded_at_time_source_path"]
            if value is not None:
                recorded_values[str(value)] += 1
                recorded_paths[str(path) if path is not None else None] += 1
        source_nonnull_recorded = sum(recorded_values.values())
        return {
            "proof": proof.to_dict(),
            "inventory": [item.to_dict() for item in inventory],
            "table_counts": table_counts,
            "parsed_child_counts": dict(parsed_children),
            "attribute_type_counts": dict(attribute_types),
            "incident_call_occurrences": incident_call_count,
            "info_call_occurrences": info_call_count,
            "affected_entity_occurrences": affected_entity_count,
            "provider_association_occurrences": provider_association_count,
            "root_cause_field_occurrences": root_cause_field_occurrences,
            "recorded_at_time": {
                "source_nonnull_count": source_nonnull_recorded,
                "source_value_counts": dict(recorded_values),
                "source_path_counts": {str(key): count for key, count in recorded_paths.items()},
            },
        }
    finally:
        connection.close()


def _destination_reconciliation(settings: MssqlConnectionSettings, source_sha256: str) -> dict[str, Any]:
    prefix = f"LR_{source_sha256[:10]}_"
    like_pattern = f"{prefix}%"
    connection = connect_mssql(settings)
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            WITH imported_snapshots AS
            (
                SELECT SnapshotId
                FROM stg.MddMacSourceSnapshot
                WHERE RunId LIKE %s
            ), counts AS
            (
                SELECT 'snapshots' AS family, COUNT_BIG(*) AS row_count FROM imported_snapshots
                UNION ALL SELECT 'calls', COUNT_BIG(*)
                    FROM stg.MddMacCallObservation AS t
                    JOIN imported_snapshots AS i ON i.SnapshotId = t.SnapshotId
                UNION ALL SELECT 'timings', COUNT_BIG(*)
                    FROM stg.MddMacTimingObservation AS t
                    JOIN imported_snapshots AS i ON i.SnapshotId = t.SnapshotId
                UNION ALL SELECT 'service_sections', COUNT_BIG(*)
                    FROM stg.MddMacServiceSectionEvidence AS t
                    JOIN imported_snapshots AS i ON i.SnapshotId = t.SnapshotId
                UNION ALL SELECT 'situations', COUNT_BIG(*)
                    FROM stg.MddMacSituationEvidence AS t
                    JOIN imported_snapshots AS i ON i.SnapshotId = t.SnapshotId
                UNION ALL SELECT 'attributes_general_attributes', COUNT_BIG(*)
                    FROM stg.MddMacAttributeEvidence AS t
                    JOIN imported_snapshots AS i ON i.SnapshotId = t.SnapshotId
                UNION ALL SELECT 'response_errors', COUNT_BIG(*)
                    FROM stg.MddMacResponseError AS t
                    JOIN imported_snapshots AS i ON i.SnapshotId = t.SnapshotId
                UNION ALL SELECT 'parser_gaps', COUNT_BIG(*)
                    FROM stg.MddMacParserGap AS t
                    JOIN imported_snapshots AS i ON i.SnapshotId = t.SnapshotId
            )
            SELECT family, row_count FROM counts ORDER BY family
            """,
            (like_pattern,),
        )
        destination_counts = {
            str(_row_value(row, "family", 0)): _int_value(_row_value(row, "row_count", 1))
            for row in cursor.fetchall()
        }
        cursor.execute(
            """
            SELECT
                COUNT_BIG(*) AS timing_rows,
                SUM(CASE WHEN t.SourceEventIdentity IS NOT NULL THEN 1 ELSE 0 END) AS identity_rows,
                SUM(CASE WHEN t.RecordedAtTimeSourceText IS NOT NULL THEN 1 ELSE 0 END) AS recorded_nonnull,
                SUM(CASE WHEN t.RecordedAtTimeSourcePath IS NOT NULL THEN 1 ELSE 0 END) AS recorded_path_nonnull
            FROM stg.MddMacTimingObservation AS t
            JOIN stg.MddMacSourceSnapshot AS s ON s.SnapshotId = t.SnapshotId
            WHERE s.RunId LIKE %s
            """,
            (like_pattern,),
        )
        timing_row = cursor.fetchone()
        timing_recorded = {
            "timing_rows": _int_value(_row_value(timing_row, "timing_rows", 0)),
            "identity_rows": _int_value(_row_value(timing_row, "identity_rows", 1)),
            "recorded_nonnull": _int_value(_row_value(timing_row, "recorded_nonnull", 2)),
            "recorded_path_nonnull": _int_value(_row_value(timing_row, "recorded_path_nonnull", 3)),
        }
        cursor.execute(
            """
            WITH imported_ids AS
            (
                SELECT DISTINCT t.SourceEventIdentity, t.TimingContext
                FROM stg.MddMacTimingObservation AS t
                JOIN stg.MddMacSourceSnapshot AS s ON s.SnapshotId = t.SnapshotId
                WHERE s.RunId LIKE %s AND t.SourceEventIdentity IS NOT NULL
            )
            SELECT
                COUNT_BIG(*) AS imported_view_rows,
                SUM(CASE WHEN v.LatestSourceRecordedAtTime IS NULL THEN 1 ELSE 0 END) AS latest_null,
                SUM(CASE WHEN v.LastObservedRecordedAtTime IS NOT NULL THEN 1 ELSE 0 END) AS last_nonnull,
                SUM(CASE WHEN v.FirstObservedRecordedAtTime IS NOT NULL THEN 1 ELSE 0 END) AS first_nonnull,
                SUM(CASE WHEN v.LatestSourceRecordedAtTime IS NULL
                              AND v.RecordedAtTimeNonNullObservationCount > 0 THEN 1 ELSE 0 END)
                    AS latest_null_history_nonnull
            FROM analytics.vwMddMacRecordedAtTimeHistory AS v
            JOIN imported_ids AS i
              ON i.SourceEventIdentity = v.SourceEventIdentity
             AND i.TimingContext = v.TimingContext
            """,
            (like_pattern,),
        )
        view_row = cursor.fetchone()
        recorded_view = {
            "imported_view_rows": _int_value(_row_value(view_row, "imported_view_rows", 0)),
            "latest_null": _int_value(_row_value(view_row, "latest_null", 1)),
            "last_nonnull": _int_value(_row_value(view_row, "last_nonnull", 2)),
            "first_nonnull": _int_value(_row_value(view_row, "first_nonnull", 3)),
            "latest_null_history_nonnull": _int_value(_row_value(view_row, "latest_null_history_nonnull", 4)),
        }
        cursor.execute(
            """
            SELECT SourceTableName, RowStatus, COUNT_BIG(*) AS row_count
            FROM ctl.MddImportSourceRow
            WHERE SourceDatabaseSha256 = %s
              AND SourceTableName IN
                  ('service_section_observations', 'parsed_snapshots.service_section_observations')
            GROUP BY SourceTableName, RowStatus
            ORDER BY SourceTableName, RowStatus
            """,
            (source_sha256,),
        )
        lineage = [
            {
                "source_table": _row_value(row, "SourceTableName", 0),
                "row_status": _row_value(row, "RowStatus", 1),
                "row_count": _int_value(_row_value(row, "row_count", 2)),
            }
            for row in cursor.fetchall()
        ]
        cursor.execute(
            """
            SELECT ImportBatchId, ImportStartedAtUtc, ImportCompletedAtUtc,
                   Status, InsertedCountsJson, DuplicateCountsJson,
                   OverlapCountsJson, FailureReason
            FROM ctl.MddImportBatch
            WHERE SourceDatabaseSha256 = %s
            ORDER BY ImportStartedAtUtc, ImportBatchId
            """,
            (source_sha256,),
        )
        batches = []
        for row in cursor.fetchall():
            batch_id = str(_row_value(row, "ImportBatchId", 0))
            cursor.execute(
                """
                SELECT
                    COALESCE(SUM(InsertedRowCount), 0) AS inserted_rows,
                    COUNT_BIG(*) AS audit_rows
                FROM ctl.MddImportTableAudit
                WHERE ImportBatchId = %s
                """,
                (batch_id,),
            )
            table_audit = cursor.fetchone()
            cursor.execute(
                """
                SELECT
                    COALESCE(SUM(InsertedRowCount), 0) AS inserted_rows,
                    COUNT_BIG(*) AS audit_rows
                FROM ctl.MddImportEvidenceAudit
                WHERE ImportBatchId = %s
                """,
                (batch_id,),
            )
            evidence_audit = cursor.fetchone()
            table_inserted = _int_value(_row_value(table_audit, "inserted_rows", 0))
            evidence_inserted = _int_value(_row_value(evidence_audit, "inserted_rows", 0))
            batches.append(
                {
                    "import_batch_id": batch_id,
                    "started_at": _row_value(row, "ImportStartedAtUtc", 1),
                    "completed_at": _row_value(row, "ImportCompletedAtUtc", 2),
                    "status": _row_value(row, "Status", 3),
                    "inserted_counts": _json_or_object(_row_value(row, "InsertedCountsJson", 4)),
                    "duplicate_counts": _json_or_object(_row_value(row, "DuplicateCountsJson", 5)),
                    "overlap_counts": _json_or_object(_row_value(row, "OverlapCountsJson", 6)),
                    "failure_reason": _row_value(row, "FailureReason", 7),
                    "table_audit_inserted_rows": table_inserted,
                    "evidence_audit_inserted_rows": evidence_inserted,
                    "all_audit_rows_zero_inserted": table_inserted == 0 and evidence_inserted == 0,
                }
            )
        cursor.execute(
            """
            SELECT SamplingTargetId, StopPointRef, TargetName, IsEnabled
            FROM ctl.MddRealtimeSamplingTarget
            ORDER BY SamplingTargetId
            """
        )
        target_rows = [
            {
                "sampling_target_id": _int_value(_row_value(row, "SamplingTargetId", 0)),
                "stop_point_ref": _row_value(row, "StopPointRef", 1),
                "target_name": _row_value(row, "TargetName", 2),
                "is_enabled": bool(_row_value(row, "IsEnabled", 3)),
            }
            for row in cursor.fetchall()
        ]
        return {
            "source_run_id_prefix": prefix,
            "destination_counts": destination_counts,
            "timing_recorded_at": timing_recorded,
            "recorded_at_time_view": recorded_view,
            "service_section_lineage": lineage,
            "import_batches": batches,
            "exact_idempotency_batch_ids": [
                batch["import_batch_id"]
                for batch in batches
                if batch["status"] == "COMPLETE" and batch["all_audit_rows_zero_inserted"]
            ],
            "sampling_targets": target_rows,
        }
    finally:
        connection.close()


def _json_or_object(value: Any) -> Any:
    if value in (None, ""):
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return {"_unparsed": str(value)}


def _validate(result: dict[str, Any]) -> dict[str, Any]:
    source = result["source_evidence"]
    proof = source["proof"]
    destination = result["destination_reconciliation"]
    source_counts = source["table_counts"]
    parsed = source["parsed_child_counts"]
    dest = destination["destination_counts"]
    comparisons = {
        "snapshots": (source_counts.get("parsed_snapshots", 0), dest.get("snapshots", 0)),
        "calls": (parsed.get("calls", 0), dest.get("calls", 0)),
        "timings": (source_counts.get("timing_observations", 0), dest.get("timings", 0)),
        "service_sections": (source_counts.get("service_section_observations", 0), dest.get("service_sections", 0)),
        "situations": (parsed.get("situations", 0), dest.get("situations", 0)),
        "attributes_general_attributes": (parsed.get("attributes", 0), dest.get("attributes_general_attributes", 0)),
        "response_errors": (parsed.get("response_errors", 0), dest.get("response_errors", 0)),
        "parser_gaps": (parsed.get("parser_gaps", 0), dest.get("parser_gaps", 0)),
    }
    comparison_report = {
        key: {"source": left, "destination": right, "difference": left - right, "status": "PASS" if left == right else "FAIL"}
        for key, (left, right) in comparisons.items()
    }
    recorded = source["recorded_at_time"]
    timing_recorded = destination["timing_recorded_at"]
    recorded_report = {
        "source_nonnull_count": recorded["source_nonnull_count"],
        "destination_nonnull_count": timing_recorded["recorded_nonnull"],
        "destination_nonnull_path_count": timing_recorded["recorded_path_nonnull"],
        "source_path_counts": recorded["source_path_counts"],
        "status": "PASS"
        if recorded["source_nonnull_count"] == timing_recorded["recorded_nonnull"]
        else "FAIL",
        "view": destination["recorded_at_time_view"],
    }
    target_ids = [row["sampling_target_id"] for row in destination["sampling_targets"]]
    target_refs = {row["sampling_target_id"]: row["stop_point_ref"] for row in destination["sampling_targets"]}
    required_target_refs = {
        1: "de:05315:11110",
        2: "de:05315:11201",
        3: "de:05315:12711",
        4: "de:05315:14201",
        5: "de:05315:16601",
        6: "de:05315:17311",
        7: "de:05315:19201",
    }
    service_projection = parsed.get("service_sections", 0)
    service_table = source_counts.get("service_section_observations", 0)
    result["comparisons"] = comparison_report
    result["recorded_at_time_reconciliation"] = recorded_report
    result["service_section_reconciliation"] = {
        "source_table_rows": service_table,
        "parsed_snapshot_projection_rows": service_projection,
        "destination_rows": dest.get("service_sections", 0),
        "source_table_only_rows": max(0, service_table - service_projection),
        "unexplained_difference": service_table - dest.get("service_sections", 0),
        "status": "PASS" if service_table == dest.get("service_sections", 0) else "FAIL",
    }
    result["target_identity_reconciliation"] = {
        "ids": target_ids,
        "expected_ids": list(range(1, 8)),
        "refs": target_refs,
        "expected_refs": required_target_refs,
        "status": "PASS" if target_ids == list(range(1, 8)) and target_refs == required_target_refs else "FAIL",
    }
    blockers = []
    if proof["path"] != str(EXPECTED_SOURCE_PATH):
        blockers.append("source path is not the proven live-research path")
    if proof["byte_size"] != EXPECTED_SOURCE_SIZE or proof["sha256"] != EXPECTED_SOURCE_SHA256:
        blockers.append("source byte size or SHA-256 does not match the proven source")
    if proof["database_role"] != "LIVE_RESEARCH" or proof["collection_context"] != "LIVE_RESEARCH_SINGLE_1":
        blockers.append("source role/context does not match LIVE_RESEARCH_SINGLE_1")
    if any(item["status"] != "PASS" for item in comparison_report.values()):
        blockers.append("one or more source/destination family counts differ")
    if recorded_report["status"] != "PASS":
        blockers.append("RecordedAtTime count differs between source and destination")
    if recorded_report["view"]["latest_null_history_nonnull"] != 0:
        blockers.append("RecordedAtTime view shows latest NULL with non-NULL history")
    if result["service_section_reconciliation"]["status"] != "PASS":
        blockers.append("ServiceSection source-table reconciliation failed")
    if result["target_identity_reconciliation"]["status"] != "PASS":
        blockers.append("existing target IDs or StopPointRefs changed")
    result["validation_status"] = "PASS" if not blockers else "FAIL"
    result["blockers"] = blockers
    return result


def run(argv: list[str] | None = None) -> dict[str, Any]:
    args = _parser().parse_args(argv)
    source = args.source.expanduser().resolve()
    proof, _inventory = inspect_sqlite_source(source)
    settings = _settings(args)
    capacity = read_only_capacity_audit(settings, proof)
    failed = read_only_failed_import_audit(settings, proof.sha256)
    result: dict[str, Any] = {
        "source_evidence": _source_evidence(source),
        "capacity_audit": capacity,
        "previous_failed_import_audit": failed,
        "destination_reconciliation": _destination_reconciliation(settings, proof.sha256),
        "writes_performed": False,
    }
    _validate(result)
    if capacity["status"] != "PASS":
        result["validation_status"] = "FAIL"
        result["blockers"].append("fresh capacity preflight is not PASS")
    if not failed["resume_reconcile_safe"]:
        result["validation_status"] = "FAIL"
        result["blockers"].append("a previous FAILED batch is not resume/reconcile safe")
    if args.report:
        report = args.report.expanduser().resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        result = run(argv)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
        return 0 if result["validation_status"] == "PASS" else 1
    except (SQLiteImportError, ValueError, OSError) as exc:
        print(json.dumps({"validation_status": "FAIL", "error_type": type(exc).__name__, "reason": _redact(str(exc))}, sort_keys=True), file=sys.stderr)
        return 1
    except Exception as exc:
        print(json.dumps({"validation_status": "FAIL", "error_type": type(exc).__name__, "reason": _redact(str(exc))}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
