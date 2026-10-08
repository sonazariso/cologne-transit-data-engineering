#!/usr/bin/env python3
"""Apply the code-backed least-privilege grants for the macOS runtime role.

This is an administrator-only, object-scoped operation. It does not rotate a
login password, touch Keychain, enable launchd, or issue an MDD request.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cologne_transit_collector.mssql_persistence import MssqlConnectionSettings, connect_mssql  # noqa: E402
from provision_macos_runtime_login import GRANT_STATEMENTS, RUNTIME_LOGIN, RUNTIME_ROLE  # noqa: E402


def apply_grants() -> dict[str, object]:
    settings = MssqlConnectionSettings.from_environment()
    connection = connect_mssql(settings)
    try:
        cursor = connection.cursor()
        cursor.execute(
            f"""
            IF USER_ID(N'{RUNTIME_LOGIN}') IS NULL
                THROW 51300, 'Dedicated runtime database user is missing.', 1;
            IF DATABASE_PRINCIPAL_ID(N'{RUNTIME_ROLE}') IS NULL
                THROW 51301, 'Dedicated runtime role is missing.', 1;
            IF NOT EXISTS
            (
                SELECT 1
                FROM sys.database_role_members AS drm
                INNER JOIN sys.database_principals AS role_principal
                    ON role_principal.principal_id = drm.role_principal_id
                INNER JOIN sys.database_principals AS member_principal
                    ON member_principal.principal_id = drm.member_principal_id
                WHERE role_principal.name = N'{RUNTIME_ROLE}'
                  AND member_principal.name = N'{RUNTIME_LOGIN}'
            )
                ALTER ROLE [{RUNTIME_ROLE}] ADD MEMBER [{RUNTIME_LOGIN}];
            """
        )
        for statement in GRANT_STATEMENTS:
            cursor.execute(statement)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "status": "PASS",
        "runtime_login": RUNTIME_LOGIN,
        "runtime_role": RUNTIME_ROLE,
        "grant_count": len(GRANT_STATEMENTS),
        "mdd_requests_issued": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply the explicit object-scoped grants")
    args = parser.parse_args(argv)
    if not args.apply:
        print(json.dumps({"status": "READY", "grant_count": len(GRANT_STATEMENTS)}))
        return 0
    try:
        result = apply_grants()
    except Exception as exc:
        result = {"status": "FAIL", "error_type": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
