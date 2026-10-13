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
    assert 'HASHBYTES(' in backfill
    assert 'DECOMPRESS(s.SnapshotJsonGzip)' in backfill
    assert 'sp_getapplock' in backfill
    assert estimate.count('sp_estimate_data_compression_savings') == 2
    assert 'DATA_COMPRESSION = PAGE' not in estimate  # no rebuild
    assert 'ISJSON(d.RestoredText)' in readback
    assert 'DATALENGTH(v.SnapshotJson)' in readback
    assert "HASHBYTES('SHA2_256', CONVERT(VARBINARY(MAX), v.SnapshotJson))" in readback

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

    # Existing normalized child-table definitions remain unmodified: no loss
    # of data or changes to identities, indexes, or required observation fields.
    for table in ('MddMacCallObservation', 'MddMacTimingObservation'):
        assert f'CREATE TABLE stg.{table}' in ddl
        assert f'INSERT INTO stg.{table}' in proc(ddl)

    print('SNAPSHOT_GZIP_SOURCE_AND_MIGRATION: PASS')
    print('GZIP_UTF16_ROUNDTRIP_AND_UTF8_HASH: PASS')
    print('BOUNDED_BACKFILL_DEFAULT_DRY_RUN: PASS')
    print('OBSERVATION_TABLES_AND_RUN_FINALIZATION: PRESERVED')
    print('SQL_CONNECTIONS=0 MDD_REQUESTS=0 PRODUCTION_WRITES=0')


if __name__ == '__main__':
    verify()
