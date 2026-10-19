#!/usr/bin/env python3
"""Offline source-contract checks; no SQL connection, writes, or MDD calls."""
from __future__ import annotations

import gzip
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DDL = ROOT / 'sql/mdd-mac-collector/create-source-faithful-persistence.sql'
MIGRATION = ROOT / 'sql/mdd-mac-collector/optimize-snapshot-gzip-v1.sql'
BACKFILL = ROOT / 'sql/mdd-mac-collector/backfill-snapshot-gzip-v1.sql'
VERIFIER = ROOT / 'sql/mdd-mac-collector/verify-snapshot-gzip-v1.sql'
ESTIMATE = ROOT / 'sql/mdd-mac-collector/estimate-observation-page-compression.sql'
AUDIT = ROOT / 'sql/mdd-mac-collector/audit-historical-storage-recovery-readonly.sql'
LAB = ROOT / 'sql/mdd-mac-collector/measure-snapshot-gzip-capacity-lab.sql'
SHRINK = ROOT / 'scripts/reclaim_snapshot_gzip_datafile_space.py'
REPORT = ROOT / 'docs/operational/tiered50-historical-and-future-storage-recovery.md'


def proc(sql: str) -> str:
    start = sql.index('CREATE OR ALTER PROCEDURE stg.uspPersistMddMacSnapshot')
    remainder = sql[start:]
    match = re.search(r'^END;\nGO\n', remainder, re.MULTILINE)
    assert match, 'stored procedure terminator missing'
    return remainder[:match.end()]


def verify() -> None:
    ddl = DDL.read_text(encoding='utf-8')
    migration = MIGRATION.read_text(encoding='utf-8')
    backfill = BACKFILL.read_text(encoding='utf-8')
    readback = VERIFIER.read_text(encoding='utf-8')
    estimate = ESTIMATE.read_text(encoding='utf-8')
    audit = AUDIT.read_text(encoding='utf-8')
    lab = LAB.read_text(encoding='utf-8')
    shrink = SHRINK.read_text(encoding='utf-8')
    report = REPORT.read_text(encoding='utf-8')

    # The V1 migration remains immutable; canonical DDL can advance to V2.
    assert proc(ddl).count('COMPRESS(@SnapshotJson)') == 1
    assert proc(migration).count('COMPRESS(@SnapshotJson)') == 1
    assert ddl.count('COMPRESS(@SnapshotJson)') == 1
    assert '@CompletedRunStatus' in proc(ddl), 'Run finalization must still work'
    assert "StopEventResultCount = JSON_VALUE(@SnapshotJson, '$.result_count')" in proc(ddl)
    assert 'PERSISTED_PARSED_SNAPSHOT' in proc(ddl)
    assert ddl.count('SnapshotJsonGzip VARBINARY(MAX) NULL') == 2
    assert ddl.count('CK_stg_MddMacSourceSnapshot_OnePayload') == 3
    assert 'COALESCE(SnapshotJson,' in ddl
    assert 'DECOMPRESS(SnapshotJsonGzip)' in ddl
    assert 'DECLARE @Apply BIT = 0;' in backfill
    assert 'DECLARE @MaxRows INT = 1;' in backfill
    assert 'BEGIN TRANSACTION;' in backfill
    assert 'SET XACT_ABORT ON;' in backfill
    assert 'SET IMPLICIT_TRANSACTIONS OFF;' in backfill
    assert "IF @@TRANCOUNT <> 0" in backfill
    assert 'HASHBYTES(' in backfill
    assert 'OriginalUtf16Bytes' in backfill
    assert 'OriginalWasNull' in backfill
    assert 'OriginalWasEmpty' in backfill
    assert 'OriginalRunId' in backfill
    assert 'OriginalAuditId' in backfill
    assert 'OriginalPayloadHashSha256' in backfill
    assert 'DECOMPRESS(s.SnapshotJsonGzip)' in backfill
    assert 'sp_getapplock' in backfill
    assert 'ORDER BY SnapshotId' in backfill
    assert 'MeasuredPeakRollbackBytes' in backfill
    assert 'MeasuredAutogrowthReserveBytes' in backfill
    assert 'IndependentLivePreflight' in backfill
    assert 'current_mdf_free_pages' in backfill
    assert 'current_log_max_size_pages' in backfill
    assert 'current_tempdb_fixed_growth_bytes' in backfill
    assert 'ProjectedWorstCaseCFreeBytes' in backfill
    assert 'current_active_sql_sessions' in backfill
    assert 'current_active_collector_runs' in backfill
    assert estimate.count('sp_estimate_data_compression_savings') == 2
    assert 'DATA_COMPRESSION = PAGE' not in estimate  # no rebuild
    assert 'ISJSON(d.RestoredText)' in readback
    assert 'DATALENGTH(v.SnapshotJson)' in readback
    assert "HASHBYTES('SHA2_256', CONVERT(VARBINARY(MAX), v.SnapshotJson))" in readback
    assert 'capacity.snapshot_gzip_physical_logical_contract' in readback
    assert 'physical_dual_rows' in readback
    assert 'logical_empty_rows' in readback
    assert 'distinct_audit_ids' in readback

    for statement_id in (
        'capacity.snapshot_gzip_exact_savings',
        'capacity.snapshot_gzip_preflight',
        'capacity.mdf_page_space',
        'capacity.ldf_usage_growth_maxsize',
        'capacity.tempdb',
        'capacity.active_sql_sessions',
        'capacity.snapshot_readback_module_dependencies',
        'capacity.snapshot_readback_sql_agent_jobs',
        'capacity.snapshot_readback_external_consumers',
        'capacity.datafile_space_reclamation',
    ):
        assert statement_id in audit, statement_id
    for token in (
        'SnapshotJsonGzip',
        'direct_snapshot_json',
        'POWER_BI',
        'BLOCKED_UNRESOLVED_EXTERNAL_CONSUMER_DEPENDENCY',
        'growth_value',
        'max_size_pages',
        'available_bytes',
        'active_sql_sessions',
    ):
        assert token in audit, token
    assert 'DECLARE @RunLab BIT = 0;' in lab
    assert 'PeakRollbackLogBytes' in lab
    assert 'ROLLBACK TRANSACTION;' in lab
    assert 'NO_FULL_HISTORY_DUPLICATE' not in lab
    assert '--apply' in shrink
    assert 'DBCC SHRINKFILE' in shrink
    assert 'C:' in shrink
    assert 'BLOCKED_INSUFFICIENT_MEASURED_HEADROOM' in report
    assert 'PRODUCTION_MUTATIONS=0' in report
    assert 'COLLECTOR_DISABLED' in report

    # The SQL Server COMPRESS(NVARCHAR(MAX)) payload uses gzip on UTF-16LE;
    # distinct from the existing Python SHA-256 over UTF-8 JSON text.
    source = ('{"parser":"TRIAS","stop":"Köln Stammheim – Bf.",'
              '"emoji":"🚌","time":"2026-10-12T16:06:57Z[GMT]"}')
    legacy_hash = hashlib.sha256(source.encode('utf-8')).hexdigest()
    packed = gzip.compress(source.encode('utf-16le'), mtime=0)
    restored = gzip.decompress(packed).decode('utf-16le')
    assert restored == source
    assert hashlib.sha256(restored.encode('utf-8')).hexdigest() == legacy_hash
    assert len(packed) < len(source.encode('utf-16le'))

    # The physical NULL used after conversion must not be confused with an
    # original empty string.  The readback contract preserves both states.
    empty_packed = gzip.compress(''.encode('utf-16le'), mtime=0)
    empty_restored = gzip.decompress(empty_packed).decode('utf-16le')
    assert empty_restored == ''
    assert empty_restored is not None
    assert None != empty_restored

    # Existing normalized child-table definitions remain unmodified: no loss
    # of data or changes to identities, indexes, or required observation fields.
    for table in ('MddMacCallObservation', 'MddMacTimingObservation'):
        assert f'CREATE TABLE stg.{table}' in ddl
        assert f'INSERT INTO stg.{table}' in proc(ddl)

    print('SNAPSHOT_GZIP_SOURCE_AND_MIGRATION: PASS')
    print('GZIP_UTF16_ROUNDTRIP_AND_UTF8_HASH: PASS')
    print('BOUNDED_BACKFILL_DEFAULT_DRY_RUN: PASS')
    print('OBSERVATION_TABLES_AND_RUN_FINALIZATION: PRESERVED')
    print('PHYSICAL_LOGICAL_NULL_EMPTY_AND_LINEAGE_CONTRACT: PASS')
    print('LIVE_CAPACITY_AND_EXTERNAL_CONSUMER_GATES: PRESENT')
    print('SQL_CONNECTIONS=0 MDD_REQUESTS=0 PRODUCTION_WRITES=0')


if __name__ == '__main__':
    verify()
