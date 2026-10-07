# NRWTransportDW removal evidence

- Operation time (UTC): `2026-10-07T12:01:38.159073Z`
- Dependency classification: `LEGACY_SAFE_TO_ARCHIVE`
- Cologne asset migration: `NO_NRWTRANSPORTDW_ASSET_REQUIRED_BY_ACTIVE_COLOGNE_PROJECT`
- Verified archive .bak preserved: `True`; SHA-256 match: `True`
- Verified archive ZIP preserved: `True`; SHA-256 match: `True`

## Pre-drop

- NRWTransportDW allocated bytes: `40550531072`
- C: free bytes before: `8212660224`
- Recorded files: `C:\Program Files\Microsoft SQL Server\MSSQL17.MSSQLSERVER\MSSQL\DATA\NRWTransportDW.mdf, C:\Program Files\Microsoft SQL Server\MSSQL17.MSSQLSERVER\MSSQL\DATA\NRWTransportDW_log.ldf`

## Drop

- ALTER DATABASE SINGLE_USER: `PASS`
- DROP DATABASE: `PASS`

## Post-drop

- NRWTransportDW absent from sys.databases: `True`
- Previous files absent at recorded paths: `True`
- C: free bytes after: `48453799936`
- Actual space recovered: `40241139712` bytes (`37.477` GiB)
- CologneTransitIntelligence: `ONLINE` / `MULTI_USER`
- Current active MDF/LDF allocation: `CologneTransitIntelligence: 4217634816 bytes (4022.25 MB), CologneTransitIntelligence_log: 1082130432 bytes (1032.0 MB)`
- DBCC CHECKDB PHYSICAL_ONLY: `PASS`
- SamplingTargetIds 1-7 present: `True`
- Latest realtime observation: `2026-10-07T11:58:55.000000`

Unexpected issues: none recorded
