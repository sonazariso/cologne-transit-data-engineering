#!/usr/bin/env python3
"""Apply the additive compact-storage SQL migration through the admin path.

The SQL file itself remains a reviewable, fail-closed source artifact.  This
runner is the only supported apply path: without ``--apply`` it performs only
static validation and never opens a database connection.  With ``--apply`` it
replaces the source file's explicit no-op marker, executes every batch in the
DB-API transaction, drains procedure result sets, and validates the installed
object contract before committing.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlPersistence,
    connect_mssql,
)
from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    MacOSKeychainSecretProvider,
    resolve_administrator_connection_settings,
)
from validate_mssql_migration import split_batches  # noqa: E402


MIGRATION = ROOT / "sql" / "mdd-mac-collector" / "compact-observation-storage-v1.sql"
APPLY_MARKER = "DECLARE @ApplyCompactStorage BIT = 0;"
REQUIRED_MARKERS = (
    "stg.MddMacCompactStorageState",
    "stg.MddMacCompactObservationEnvelope",
    "stg.uspMddMacCompactStoreHistoricalEnvelope",
    "stg.uspMddMacCompactMarkUnit",
    "stg.uspPersistMddMacSnapshotCompact",
)
COLLECTOR_LABELS = (
    "com.cologne-transit-intelligence.mdd-collector",
    "com.cologne-transit-intelligence.mdd-tiered50-collector",
)


def _collector_gate() -> dict[str, Any]:
    if platform.system() != "Darwin":
        return {"status": "NOT_PROVEN", "reason": "launchd state is only inspectable on macOS"}
    try:
        disabled_result = subprocess.run(
            ["/bin/launchctl", "print-disabled", f"user/{os.getuid()}"],
            capture_output=True,
            text=True,
            check=False,
        )
        disabled_text = disabled_result.stdout + disabled_result.stderr
        disabled = all(f'"{label}" => disabled' in disabled_text for label in COLLECTOR_LABELS)
        loaded_exit_codes = {
            label: subprocess.run(
                ["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"],
                capture_output=True,
                text=True,
                check=False,
            ).returncode
            for label in COLLECTOR_LABELS
        }
        unloaded = all(code != 0 for code in loaded_exit_codes.values())
        return {
            "status": "PASS" if disabled and unloaded else "NOT_PROVEN",
            "disabled": disabled,
            "loaded_exit_codes": loaded_exit_codes,
        }
    except OSError as exc:
        return {"status": "NOT_PROVEN", "reason": str(exc)[:300]}


def _drain(cursor: Any) -> None:
    fetchall = getattr(cursor, "fetchall", None)
    if callable(fetchall):
        try:
            fetchall()
        except Exception:
            pass
    nextset = getattr(cursor, "nextset", None)
    if callable(nextset):
        while True:
            try:
                has_next = nextset()
            except Exception:
                break
            if not has_next:
                break
            if callable(fetchall):
                try:
                    fetchall()
                except Exception:
                    pass


def _source_text() -> str:
    text = MIGRATION.read_text(encoding="utf-8")
    missing = [marker for marker in REQUIRED_MARKERS if marker not in text]
    if APPLY_MARKER not in text:
        raise RuntimeError("compact migration is missing its fail-closed apply marker")
    if missing:
        raise RuntimeError("compact migration is missing required markers: " + ", ".join(missing))
    return text


def _batches(*, apply: bool) -> list[str]:
    text = _source_text()
    if not apply:
        return split_batches(text)
    return split_batches(text.replace(APPLY_MARKER, "DECLARE @ApplyCompactStorage BIT = 1;", 1))


def _static_result() -> dict[str, Any]:
    text = _source_text()
    return {
        "status": "PASS",
        "migration": str(MIGRATION.relative_to(ROOT)),
        "fail_closed_default": APPLY_MARKER in text,
        "required_markers": list(REQUIRED_MARKERS),
        "batch_count": len(_batches(apply=False)),
    }


def _apply() -> dict[str, Any]:
    collector_gate = _collector_gate()
    if collector_gate.get("status") != "PASS":
        raise RuntimeError("collector stopped/disabled gate is not proven")
    settings, role_source = resolve_administrator_connection_settings(
        os.environ,
        secret_provider=MacOSKeychainSecretProvider(),
    )
    adapter = MssqlPersistence(settings)
    connection = connect_mssql(settings)
    executed = 0
    try:
        cursor = connection.cursor()
        cursor.execute("SET XACT_ABORT ON;")
        _drain(cursor)
        for batch in _batches(apply=True):
            cursor.execute(batch)
            _drain(cursor)
            executed += 1
        connection.commit()
    except BaseException:
        try:
            connection.rollback()
        finally:
            connection.close()
        raise
    connection.close()
    schema = adapter.validate_compact_storage_schema()
    return {
        "status": "PASS",
        "collector_gate": collector_gate,
        "admin_role_source": role_source,
        "executed_batches": executed,
        "schema": schema,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="execute the additive migration")
    args = parser.parse_args(argv)
    try:
        result = _apply() if args.apply else _static_result()
        if not args.apply:
            result["collector_gate"] = _collector_gate()
    except Exception as exc:
        result = {"status": "BLOCKED", "error_type": type(exc).__name__, "error": str(exc)[:800]}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
