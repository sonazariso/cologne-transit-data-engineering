# Offline reproducibility, development environment and source review

The document release contains **source and offline validations**, not credentials or a complete executable SQL Server fixture. Full production replay is not possible with these files alone and would be inappropriate given current disabled status.

## Environment

- Python 3 (historically the macOS command-line-tools Python and `pymssql` were used).
- SQL Server 2022/2025 compatible T-SQL assumptions must be validated against the actual instance; source contracts are in [`sql/mdd-mac-collector/`](../sql/mdd-mac-collector/).
- Mac Keychain-backed MDD/SQL credentials and a Windows SQL VM are *optional external operator dependencies only*; offline checks must not require them.
- Install Python requirements inside a disposable virtualenv if necessary; `requirements-mssql.txt` captures historical package dependencies. Do not run the enabled collector to check this repository.

## Safe local source inspection

```bash
python3 -m compileall -q src scripts
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py'
```

These commands inspect Python syntax and run offline tests **provided tests are coded to remain offline**; review local environment variables and any individual test setup before running. Tests may depend on optional packages and current host permissions. No test command is proof of live SQL, network reliability or provider completeness.

## Exact panel audit with only Python standard library

```bash
python3 - <<'PYCODE'
import json, collections
m = json.load(open('config/tiered50_runtime_manifest.json', encoding='utf-8'))
t = m['targets']
print('panel:', m['panel_version'], 'count:', len(t), 'unique:', len({x['source_parent_station_id'] for x in t}))
print('tiers:', dict(collections.Counter(x['tier'] for x in t)))
print('phase layout:', dict(collections.Counter((x['tier'], x['phase_index']) for x in t)))
PYCODE
```

## Current SQL verification (manual, read-only)

Read the preservation plan in [`TEN_SNAPSHOT_RETENTION_MANIFEST.md`](TEN_SNAPSHOT_RETENTION_MANIFEST.md). Do not execute a mutation script as a test. For a conservative exact-ID / seven-family count check run [`sql/portfolio/verify-final-retained-sample-readonly.sql`](../sql/portfolio/verify-final-retained-sample-readonly.sql) from SSMS, independently inspect the results and preserve verification output privately. A fresh full backup/restore check is a separate operator exercise requiring access to a safe off-C destination.

## Source organization principles

Implementation stays under `src`, guarded scripts under `scripts`, SQL schema and historical migrations under `sql`, structured offline tests under `tests`, and approved panel under `config`. Historical experiments are not promoted to live capabilities. Package archives, local logs, unreviewed admin JSON, credential material, and raw request payloads were intentionally excluded from the public-ready folder.
