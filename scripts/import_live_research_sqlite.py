#!/usr/bin/env python3
"""Import one proven LIVE_RESEARCH SQLite database into SQL Server.

The source path is mandatory and is never auto-discovered.  Default execution
is read-only/dry-run.  ``--apply`` is the only mode that writes to the
authoritative SQL Server database; the same source SHA-256 can be applied a
second time because durable SQL lineage keys classify every source row as a
duplicate instead of inserting another evidence row.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.mssql_persistence import MssqlConnectionSettings  # noqa: E402
from cologne_transit_collector.sqlite_import import (  # noqa: E402
    EXPECTED_SQL_DATABASE,
    SQLiteImportError,
    SQLiteToMssqlImporter,
    inspect_sqlite_source,
    read_only_overlap_audit,
)


def _redact(message: str) -> str:
    message = " ".join(str(message).split())
    return re.sub(
        r"(?i)(password|token|secret|authorization|api[_-]?key)\s*[=:]\s*[^,; ]+",
        r"\1=<redacted>",
        message,
    )[:2000]


def _settings(args: argparse.Namespace) -> MssqlConnectionSettings:
    values = dict(os.environ)
    if args.mssql_host:
        values["MSSQL_HOST"] = args.mssql_host
    if args.mssql_port is not None:
        values["MSSQL_PORT"] = str(args.mssql_port)
    if args.mssql_database:
        values["MSSQL_DATABASE"] = args.mssql_database
    if args.mssql_user:
        values["MSSQL_USER"] = args.mssql_user
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
    parser.add_argument("--source", type=Path, required=True, help="explicit durable LIVE_RESEARCH SQLite path")
    parser.add_argument("--apply", action="store_true", help="write the proven source into SQL Server")
    parser.add_argument("--overlap-only", action="store_true", help="run the read-only Windows-history overlap audit only")
    parser.add_argument("--report", type=Path, help="optional JSON report path")
    parser.add_argument("--mssql-host", help="SQL Server host; otherwise MSSQL_HOST/SQL_SERVER_HOST")
    parser.add_argument("--mssql-port", type=int, help="SQL Server port; otherwise MSSQL_PORT")
    parser.add_argument("--mssql-database", default=EXPECTED_SQL_DATABASE, help="must remain CologneTransitIntelligence")
    parser.add_argument("--mssql-user", help="SQL Server user; otherwise MSSQL_USER/SQL_SERVER_USER")
    parser.add_argument(
        "--mssql-password-env",
        default="MSSQL_PASSWORD",
        help="environment variable supplying the SQL password; its value is never printed",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.overlap_only and args.apply:
            raise SQLiteImportError("--overlap-only and --apply are mutually exclusive")
        if args.overlap_only:
            proof, _inventory = inspect_sqlite_source(args.source)
            result = {
                "status": "OVERLAP_AUDIT_ONLY",
                "source": proof.to_dict(),
                "overlap_audit": read_only_overlap_audit(_settings(args), proof),
            }
        else:
            settings = _settings(args) if args.apply else None
            result = SQLiteToMssqlImporter(args.source, settings, apply=args.apply).run()
        if args.report:
            report = args.report.expanduser().resolve()
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
        return 0
    except (SQLiteImportError, ValueError, OSError) as exc:
        result = {"status": "FAIL", "error_type": type(exc).__name__, "reason": _redact(str(exc))}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1
    except Exception as exc:
        # DB-API exceptions can contain server diagnostics; keep the command's
        # failure contract concise and credential-safe.
        result = {"status": "FAIL", "error_type": type(exc).__name__, "reason": _redact(str(exc))}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
