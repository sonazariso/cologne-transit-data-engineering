#!/usr/bin/env python3
"""Validate Tiered50 skip-audit persistence against the real SQL Server.

The default command is static-only and never claims SQL execution.  ``--live``
requires the explicit administrator connection, inserts only uniquely named
test rows inside one transaction, checks idempotence/conflicts/claims, and
rolls the entire transaction back.  It never calls MDD and never changes a
historical production audit.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.domain import SamplingEra  # noqa: E402
from cologne_transit_collector.manifest import load_default_manifest  # noqa: E402
from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    MSSQL_PARAMETER_LIMIT,
    MAX_SKIPPED_TARGET_AUDITS_PER_STATEMENT,
    SKIPPED_TARGET_AUDIT_COLUMNS,
    SKIPPED_TARGET_AUDIT_PARAMETERS_PER_ROW,
    build_skipped_target_audit_merge_statement,
    connect_mssql,
)
from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    ADMIN_KEYCHAIN_SERVICE as RUNTIME_ADMIN_KEYCHAIN_SERVICE,
    MacOSKeychainSecretProvider,
    resolve_administrator_connection_settings,
)


ADMIN_KEYCHAIN_SERVICE = RUNTIME_ADMIN_KEYCHAIN_SERVICE


def _row_value(row: Any, name: str, index: int = 0) -> Any:
    if isinstance(row, dict):
        lowered = {str(key).casefold(): value for key, value in row.items()}
        return lowered.get(name.casefold())
    return row[index] if row is not None else None


def _sanitize(value: Any) -> str:
    message = re.sub(r"(?i)(password|secret|token|credential)\s*[=:]\s*[^,;\s]+", r"\1=<redacted>", str(value))
    return re.sub(r"(?i)(?:[A-Za-z]:[\\/]|/)[^\s']+", "<path redacted>", message)[:500]


def _admin_settings() -> MssqlConnectionSettings:
    settings, _source = resolve_administrator_connection_settings(
        os.environ,
        secret_provider=MacOSKeychainSecretProvider(),
    )
    return settings


def _rows(manifest: Any, *, run_id: str, bucket: int, count: int, status: str = "SKIPPED") -> list[tuple[Any, ...]]:
    targets = manifest.bind_production_sampling_target_ids(
        {
            **{
                target.source_parent_station_id: int(target.legacy_sampling_target_id)
                for target in manifest.legacy7_targets
            },
            **{
                target.source_parent_station_id: 8 + index
                for index, target in enumerate(manifest.new_tiered50_targets)
            },
        }
    ).targets
    selected = targets[:count]
    now = datetime.now(timezone.utc)
    rows: list[tuple[Any, ...]] = []
    for target in selected:
        target_id = int(target.production_sampling_target_id or 0)
        outcome = {
            "status": status,
            "skip_reason": "SQL_VALIDATION_ROLLBACK",
            "http_attempts": 0,
            "duration_seconds": 0.0,
        }
        rows.append(
            (
                run_id,
                target.panel_version,
                target.source_parent_station_id,
                target.station_name,
                target.tier,
                int(target.phase_index),
                int(target.number_of_results),
                int(bucket),
                SamplingEra.TIERED50.value,
                target_id,
                None,
                target.selection_policy_version,
                target.continuity_classification,
                "MAC_TIERED50_PRODUCTION",
                now,
                now,
                status,
                0,
                None,
                None,
                None,
                None,
                None,
                0.0,
                outcome["skip_reason"],
                None,
                None,
                json.dumps(outcome, sort_keys=True),
            )
        )
    return rows


def _count(cursor: Any, *, run_id: str, bucket: int) -> int:
    cursor.execute(
        """
        SELECT COUNT_BIG(*) AS row_count
        FROM ctl.MddMacTargetAudit
        WHERE RunId = %s AND LogicalDueBucket = %s
        """,
        (run_id, bucket),
    )
    return int(_row_value(cursor.fetchone(), "row_count", 0) or 0)


def run_live() -> dict[str, Any]:
    """Execute the rollback-only SQL Server validation."""

    settings = _admin_settings()
    manifest = load_default_manifest(ROOT)
    run_id = f"sql-skip-audit-validation-{uuid4().hex}"
    running_conflict_run_id = f"sql-skip-audit-running-{uuid4().hex}"
    conflict_run_id = f"sql-skip-audit-conflict-{uuid4().hex}"
    bucket = 9_000_000_000 + int(uuid4().hex[:8], 16)
    connection = connect_mssql(settings)
    counts: dict[str, int] = {}
    try:
        cursor = connection.cursor()
        cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
        cursor.execute(
            """
            INSERT INTO ctl.MddMacCollectorRun
                (RunId, PanelVersion, LogicalDueBucket, Mode,
                 CollectionContext, StartedAtUtc, Status)
            VALUES (%s, %s, %s, 'sql-validation', 'MAC_TIERED50_PRODUCTION', SYSUTCDATETIME(), 'RUNNING')
            """,
            (run_id, manifest.panel_version, bucket),
        )
        for count in (1, 16, 18, 19):
            rows = _rows(manifest, run_id=run_id, bucket=bucket + count, count=count)
            statement, parameters = build_skipped_target_audit_merge_statement(
                rows,
                transaction_control=False,
            )
            if statement.count("%s") != len(parameters) or len(parameters) != count * 28:
                raise RuntimeError(f"SQL skip-audit parameter contract failed for {count} rows")
            cursor.execute(statement, parameters)
            counts[f"rows_{count}_first_insert"] = _count(cursor, run_id=run_id, bucket=bucket + count)
            cursor.execute(statement, parameters)
            counts[f"rows_{count}_retry"] = _count(cursor, run_id=run_id, bucket=bucket + count)
            if counts[f"rows_{count}_first_insert"] != count or counts[f"rows_{count}_retry"] != count:
                raise RuntimeError(f"SQL skip-audit idempotence failed for {count} rows")

        def insert_validation_run(validation_run_id: str) -> None:
            cursor.execute(
                """
                INSERT INTO ctl.MddMacCollectorRun
                    (RunId, PanelVersion, LogicalDueBucket, Mode,
                     CollectionContext, StartedAtUtc, Status)
                VALUES (%s, %s, %s, 'sql-validation', 'MAC_TIERED50_PRODUCTION', SYSUTCDATETIME(), 'RUNNING')
                """,
                (validation_run_id, manifest.panel_version, bucket),
            )

        # A RUNNING audit is also authoritative: the skip MERGE must not
        # overwrite it or turn it into a terminal outcome.
        insert_validation_run(running_conflict_run_id)
        running_conflict_rows = _rows(
            manifest,
            run_id=running_conflict_run_id,
            bucket=bucket,
            count=1,
            status="RUNNING",
        )
        running_conflict = list(running_conflict_rows[0])
        cursor.execute(
            f"INSERT INTO ctl.MddMacTargetAudit ({', '.join(SKIPPED_TARGET_AUDIT_COLUMNS)}) VALUES ({', '.join('%s' for _ in SKIPPED_TARGET_AUDIT_COLUMNS)})",
            tuple(running_conflict),
        )
        statement, parameters = build_skipped_target_audit_merge_statement(
            _rows(manifest, run_id=running_conflict_run_id, bucket=bucket, count=1),
            transaction_control=False,
        )
        cursor.execute(statement, parameters)
        cursor.execute(
            """
            SELECT Status, HttpAttempts
            FROM ctl.MddMacTargetAudit
            WHERE RunId = %s AND LogicalDueBucket = %s
            """,
            (running_conflict_run_id, bucket),
        )
        running_existing = cursor.fetchone()
        if (
            str(_row_value(running_existing, "Status", 0) or "") != "RUNNING"
            or int(_row_value(running_existing, "HttpAttempts", 1) or 0) != 0
        ):
            raise RuntimeError("SQL skip-audit MERGE overwrote an authoritative RUNNING audit")

        conflict_rows = _rows(manifest, run_id=conflict_run_id, bucket=bucket + 1, count=1)
        conflict = list(conflict_rows[0])
        conflict[16] = "SUCCEEDED"
        conflict[17] = 1
        conflict[18] = 200
        conflict[21] = "PERSISTED_PARSED_SNAPSHOT"
        conflict[27] = json.dumps({"status": "SUCCEEDED", "authoritative": True}, sort_keys=True)
        cursor.execute(
            """
            INSERT INTO ctl.MddMacCollectorRun
                (RunId, PanelVersion, LogicalDueBucket, Mode,
                 CollectionContext, StartedAtUtc, Status)
            VALUES (%s, %s, %s, 'sql-validation', 'MAC_TIERED50_PRODUCTION', SYSUTCDATETIME(), 'RUNNING')
            """,
            (conflict_run_id, manifest.panel_version, bucket + 1),
        )
        columns = ", ".join(SKIPPED_TARGET_AUDIT_COLUMNS)
        placeholders = ", ".join("%s" for _ in SKIPPED_TARGET_AUDIT_COLUMNS)
        cursor.execute(
            f"INSERT INTO ctl.MddMacTargetAudit ({columns}) VALUES ({placeholders})",
            tuple(conflict),
        )
        skip_conflict = _rows(manifest, run_id=conflict_run_id, bucket=bucket + 1, count=1)
        statement, parameters = build_skipped_target_audit_merge_statement(skip_conflict, transaction_control=False)
        cursor.execute(statement, parameters)
        cursor.execute(
            """
            SELECT Status, HttpAttempts, PersistenceOutcome
            FROM ctl.MddMacTargetAudit
            WHERE RunId = %s AND LogicalDueBucket = %s
            """,
            (conflict_run_id, bucket + 1),
        )
        existing = cursor.fetchone()
        if (
            str(_row_value(existing, "Status", 0) or "") != "SUCCEEDED"
            or int(_row_value(existing, "HttpAttempts", 1) or 0) != 1
            or str(_row_value(existing, "PersistenceOutcome", 2) or "") != "PERSISTED_PARSED_SNAPSHOT"
        ):
            raise RuntimeError("SQL skip-audit conflict overwrote an authoritative audit")
        cursor.execute(
            """
            SELECT COUNT_BIG(*) AS claim_count
            FROM ctl.MddMacTargetClaim
            WHERE RunId IN (%s, %s, %s)
            """,
            (run_id, running_conflict_run_id, conflict_run_id),
        )
        if int(_row_value(cursor.fetchone(), "claim_count", 0) or 0) != 0:
            raise RuntimeError("SQL skip-audit validation created a retroactive target claim")

        # Simulate a failure after successful inserts and prove the enclosing
        # transaction can be rolled back without leaving test rows behind.
        try:
            cursor.execute("RAISERROR('simulated skip-audit partial failure', 16, 1);")
        except Exception:
            pass
        connection.rollback()
        cursor.execute(
            "SELECT COUNT_BIG(*) AS row_count FROM ctl.MddMacTargetAudit WHERE RunId IN (%s, %s, %s)",
            (run_id, running_conflict_run_id, conflict_run_id),
        )
        counts["rows_after_rollback"] = int(_row_value(cursor.fetchone(), "row_count", 0) or 0)
        if counts["rows_after_rollback"] != 0:
            raise RuntimeError("SQL skip-audit rollback left test rows behind")
    finally:
        try:
            connection.rollback()
        except Exception:
            pass
        connection.close()
    return {
        "status": "PASS",
        "validation_mode": "REAL_SQL_SERVER_TRANSACTION_ROLLED_BACK",
        "parameter_limit": MSSQL_PARAMETER_LIMIT,
        "parameters_per_row": SKIPPED_TARGET_AUDIT_PARAMETERS_PER_ROW,
        "maximum_rows_per_statement": MAX_SKIPPED_TARGET_AUDITS_PER_STATEMENT,
        "tested_row_counts": [1, 16, 18, 19],
        "counts": counts,
        "mdd_requests_issued": 0,
        "historical_production_audits_modified": False,
        "retroactive_target_claims": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="execute the rollback-only test against SQL Server")
    args = parser.parse_args(argv)
    if not args.live:
        result = {
            "status": "NOT_RUN",
            "validation_mode": "STATIC_ONLY",
            "parameter_limit": MSSQL_PARAMETER_LIMIT,
            "parameters_per_row": SKIPPED_TARGET_AUDIT_PARAMETERS_PER_ROW,
            "maximum_rows_per_statement": MAX_SKIPPED_TARGET_AUDITS_PER_STATEMENT,
            "tested_row_counts": [1, 16, 18, 19],
            "real_sql_server_execution": "NOT_PROVEN",
            "mdd_requests_issued": 0,
        }
        print(json.dumps(result, sort_keys=True))
        return 2
    try:
        result = run_live()
    except Exception as exc:
        result = {
            "status": "BLOCKED",
            "validation_mode": "REAL_SQL_SERVER_TRANSACTION_ROLLED_BACK",
            "error_type": type(exc).__name__,
            "reason": _sanitize(exc),
            "real_sql_server_execution": "FAILED_OR_NOT_PROVEN",
            "mdd_requests_issued": 0,
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result.get("status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
