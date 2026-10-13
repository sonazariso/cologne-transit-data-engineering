#!/usr/bin/env python3
"""Offline consistency checks for future-write observation GZIP V2.

No database connection, SQL writes, MDD calls or real data migration.
"""
from __future__ import annotations

import gzip
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DDL = ROOT / 'sql/mdd-mac-collector/create-source-faithful-persistence.sql'
MIGRATION = ROOT / 'sql/mdd-mac-collector/optimize-observation-gzip-v2.sql'


def persisted_proc(sql: str) -> str:
    marker = 'CREATE OR ALTER PROCEDURE stg.uspPersistMddMacSnapshot\n'
    start = sql.index(marker)
    end = re.search(r'^END;\nGO\n', sql[start:], flags=re.MULTILINE)
    assert end, 'procedure terminator missing'
    return sql[start:start + end.end()]


def verify() -> None:
    ddl = DDL.read_text(encoding='utf-8')
    migration = MIGRATION.read_text(encoding='utf-8')
    proc = persisted_proc(ddl)
    assert proc == persisted_proc(migration), 'Canonical DDL / V2 migration drift'

    gzip_fields = {
        'MddMacCallObservation': ('SourcePathsJson', 'SourceEventJson'),
        'MddMacTimingObservation': ('SourcePathsJson', 'SourceEventIdentityBasisJson'),
    }
    for table, fields in gzip_fields.items():
        assert f'CREATE TABLE stg.{table}' in ddl
        assert f'INSERT INTO stg.{table}' in proc
        assert f'CREATE OR ALTER VIEW stg.v{table}Json' in migration
        for field in fields:
            compressed = f'{field}Gzip'
            assert ddl.count(f'{compressed} VARBINARY(MAX) NULL') >= 1
            assert f"COL_LENGTH(N'stg.{table}', '{compressed}')" in migration
            assert f'COALESCE([{field}], CONVERT(NVARCHAR(MAX), DECOMPRESS([{compressed}])))' in migration
            assert f'COMPRESS({field})' in proc

    for must_preserve in (
        'COMPRESS(@SnapshotJson)', 'OPENJSON(@CallsJson)',
        'OPENJSON(@TimingsJson)', 'PERSISTED_PARSED_SNAPSHOT',
        '@CompletedRunStatus',
        "StopEventResultCount = JSON_VALUE(@SnapshotJson, '$.result_count')",
    ):
        assert must_preserve in proc, must_preserve

    assert "WHERE Status = 'ACTIVE'" in migration
    assert 'DECLARE @Apply BIT = 1' not in migration
    assert 'UPDATE stg.MddMacTimingObservation' not in migration
    assert 'UPDATE stg.MddMacCallObservation' not in migration
    assert 'DELETE FROM stg.' not in migration
    assert 'DROP TABLE' not in migration
    assert not re.search(r'\bDBCC\s+SHRINK(?:FILE|DATABASE)\b', migration, flags=re.I)

    values = (
        '{"source_path":"$.result[0].call[2]","stop":"Köln 🚌",'
        '"street":"Sürther Straße","symbol":"😀"}',
        '{"source_event": {"time": "2026-10-12T16:36:00Z[GMT]",'
        '"path": "$.result[0].call[2]", "isCancelled": false}}',
    )
    for source in values:
        original_bytes = source.encode('utf-16le')
        packed = gzip.compress(original_bytes, mtime=0)
        restored = gzip.decompress(packed).decode('utf-16le')
        assert restored == source
        assert hashlib.sha256(restored.encode('utf-8')).digest() == hashlib.sha256(source.encode('utf-8')).digest()

    print('OBSERVATION_GZIP_V2_SCHEMA_CONTRACT: PASS')
    print('OBSERVATION_GZIP_V2_PROCEDURE_PARITY: PASS')
    print('OBSERVATION_GZIP_V2_UTF16_ROUNDTRIP: PASS')
    print('OBSERVATION_GZIP_V2_LEGACY_READBACK: PASS')
    print('OBSERVATION_GZIP_V2_NO_BACKFILL: PASS')
    print('SQL_CONNECTIONS=0 MDD_REQUESTS=0 PRODUCTION_WRITES=0')


if __name__ == '__main__':
    verify()
