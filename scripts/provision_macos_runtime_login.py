#!/usr/bin/env python3
"""Provision and verify the dedicated Mac Legacy7 SQL runtime login.

The current administrator credential is used only for this one-time action.
The generated password exists only in memory until it is written directly to
the approved macOS Keychain item; it is never printed, placed in SQL source,
or written to the plist/runtime JSON.
"""

from __future__ import annotations

import argparse
import json
import secrets
import string
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    MssqlPersistence,
    connect_mssql,
)
from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    LOCAL_RUNTIME_KEYS,
    KEYCHAIN_ACCOUNT_MSSQL,
    KEYCHAIN_SERVICE_MSSQL,
    load_local_runtime_values,
    merge_non_secret_runtime_values,
    store_keychain_secret,
    write_local_runtime_config,
)


RUNTIME_LOGIN = "CologneTransitCollector"
RUNTIME_ROLE = "CologneTransitCollectorRuntime"
PASSWORD_ALPHABET = string.ascii_letters + string.digits + "!@#$%^&*()-_=+[]{}:,.?"
REQUIRED_POSITIVE_OBJECTS = (
    "ctl.MddRealtimeSamplingTarget",
    "ctl.MddRealtimeSamplingSlot",
    "ctl.MddMacCollectorRun",
    "ctl.MddMacTargetAudit",
    "ctl.MddMacQuotaPermit",
    "stg.uspPersistMddMacSnapshot",
)


GRANT_STATEMENTS = (
    "GRANT SELECT ON OBJECT::ctl.MddRealtimeSamplingTarget TO [CologneTransitCollectorRuntime]",
    "GRANT SELECT ON OBJECT::ctl.MddRealtimeSamplingSlot TO [CologneTransitCollectorRuntime]",
    "GRANT SELECT ON OBJECT::stg.MddRealtimeStopObservation TO [CologneTransitCollectorRuntime]",
    "GRANT SELECT ON OBJECT::stg.MddRealtimeSituationObservation TO [CologneTransitCollectorRuntime]",
    "GRANT SELECT ON OBJECT::stg.MddRealtimeStopSituationLink TO [CologneTransitCollectorRuntime]",
    "GRANT EXECUTE ON OBJECT::ctl.uspGetMddRealtimeSamplingTarget TO [CologneTransitCollectorRuntime]",
    "GRANT VIEW DEFINITION ON OBJECT::ctl.uspGetMddRealtimeSamplingTarget TO [CologneTransitCollectorRuntime]",
    "GRANT SELECT, INSERT, UPDATE ON OBJECT::ctl.MddMacCollectorRun TO [CologneTransitCollectorRuntime]",
    "GRANT SELECT, INSERT, UPDATE ON OBJECT::ctl.MddMacDispatcherClaim TO [CologneTransitCollectorRuntime]",
    "GRANT INSERT ON OBJECT::ctl.MddMacDispatcherClaimEvent TO [CologneTransitCollectorRuntime]",
    "GRANT SELECT, INSERT, UPDATE ON OBJECT::ctl.MddMacTargetClaim TO [CologneTransitCollectorRuntime]",
    "GRANT INSERT ON OBJECT::ctl.MddMacTargetClaimEvent TO [CologneTransitCollectorRuntime]",
    "GRANT SELECT, INSERT, UPDATE ON OBJECT::ctl.MddMacTargetAudit TO [CologneTransitCollectorRuntime]",
    "GRANT INSERT ON OBJECT::ctl.MddMacHttpAttempt TO [CologneTransitCollectorRuntime]",
    "GRANT SELECT, INSERT, UPDATE ON OBJECT::ctl.MddMacQuotaPermit TO [CologneTransitCollectorRuntime]",
    "GRANT INSERT ON OBJECT::ctl.MddMacQuotaAudit TO [CologneTransitCollectorRuntime]",
    "GRANT SELECT ON OBJECT::ctl.MddMacSamplingEra TO [CologneTransitCollectorRuntime]",
    "GRANT SELECT ON OBJECT::ctl.MddMacMethodologyEvidence TO [CologneTransitCollectorRuntime]",
    "GRANT EXECUTE ON OBJECT::stg.uspPersistMddMacSnapshot TO [CologneTransitCollectorRuntime]",
    "GRANT SELECT ON OBJECT::analytics.vwMddMacRecordedAtTimeHistory TO [CologneTransitCollectorRuntime]",
    "GRANT SELECT ON OBJECT::analytics.vwMddMacSourceFieldCoverage TO [CologneTransitCollectorRuntime]",
)


def _generate_password() -> str:
    required = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%^&*()-_=+[]{}:,.?"),
    ]
    required.extend(secrets.choice(PASSWORD_ALPHABET) for _ in range(60))
    secrets.SystemRandom().shuffle(required)
    return "".join(required)


def _create_login(cursor: Any, password: str, *, rotate_existing: bool) -> bool:
    cursor.execute(
        "SELECT CASE WHEN SUSER_ID(N'CologneTransitCollector') IS NULL THEN 0 ELSE 1 END AS login_exists"
    )
    row = cursor.fetchone()
    exists = bool(row.get("login_exists") if isinstance(row, Mapping) else row[0])
    if exists and not rotate_existing:
        raise RuntimeError("dedicated runtime login already exists; rerun with --rotate-existing only if rotation is intended")
    cursor.execute(
        """
        DECLARE @RuntimePassword NVARCHAR(128) = %s;
        DECLARE @Sql NVARCHAR(MAX);
        IF SUSER_ID(N'CologneTransitCollector') IS NULL
        BEGIN
            SET @Sql = N'CREATE LOGIN [CologneTransitCollector] WITH PASSWORD = @p, CHECK_POLICY = ON, CHECK_EXPIRATION = OFF';
            EXEC sys.sp_executesql @Sql, N'@p NVARCHAR(128)', @p = @RuntimePassword;
        END
        ELSE
        BEGIN
            SET @Sql = N'ALTER LOGIN [CologneTransitCollector] WITH PASSWORD = @p, CHECK_POLICY = ON, CHECK_EXPIRATION = OFF';
            EXEC sys.sp_executesql @Sql, N'@p NVARCHAR(128)', @p = @RuntimePassword;
        END
        """,
        (password,),
    )
    cursor.execute(
        """
        IF USER_ID(N'CologneTransitCollector') IS NULL
            CREATE USER [CologneTransitCollector] FOR LOGIN [CologneTransitCollector];
        IF DATABASE_PRINCIPAL_ID(N'CologneTransitCollectorRuntime') IS NULL
            CREATE ROLE [CologneTransitCollectorRuntime];
        IF NOT EXISTS
        (
            SELECT 1
            FROM sys.database_role_members AS drm
            INNER JOIN sys.database_principals AS role_principal
                ON role_principal.principal_id = drm.role_principal_id
            INNER JOIN sys.database_principals AS member_principal
                ON member_principal.principal_id = drm.member_principal_id
            WHERE role_principal.name = N'CologneTransitCollectorRuntime'
              AND member_principal.name = N'CologneTransitCollector'
        )
            ALTER ROLE [CologneTransitCollectorRuntime]
                ADD MEMBER [CologneTransitCollector];
        """
    )
    for statement in GRANT_STATEMENTS:
        cursor.execute(statement)
    return exists


def _negative_permission_checks(settings: MssqlConnectionSettings) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    connection = connect_mssql(settings)
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT IS_SRVROLEMEMBER(N'sysadmin') AS is_sysadmin,
                   IS_SRVROLEMEMBER(N'serveradmin') AS is_serveradmin,
                   IS_SRVROLEMEMBER(N'securityadmin') AS is_securityadmin,
                   IS_ROLEMEMBER(N'db_owner') AS is_db_owner,
                   IS_ROLEMEMBER(N'db_datareader') AS is_db_datareader,
                   IS_ROLEMEMBER(N'db_datawriter') AS is_db_datawriter,
                   HAS_PERMS_BY_NAME(NULL, NULL, N'CONTROL SERVER') AS control_server,
                   HAS_PERMS_BY_NAME(NULL, NULL, N'ALTER ANY LOGIN') AS alter_any_login,
                   HAS_PERMS_BY_NAME(NULL, NULL, N'CREATE ANY DATABASE') AS create_any_database,
                   HAS_PERMS_BY_NAME(N'ctl.MddMacTargetAudit', N'OBJECT', N'CONTROL') AS audit_control,
                   HAS_PERMS_BY_NAME(N'ctl.MddMacTargetAudit', N'OBJECT', N'ALTER') AS audit_alter,
                   HAS_PERMS_BY_NAME(N'ctl.MddMacTargetAudit', N'OBJECT', N'DELETE') AS audit_delete
            """
        )
        row = cursor.fetchone()
        values = dict(row) if isinstance(row, Mapping) else dict(enumerate(row))
        checks.update(
            {
                "sysadmin_denied": not bool(values.get("is_sysadmin", values.get(0))),
                "serveradmin_denied": not bool(values.get("is_serveradmin", values.get(1))),
                "securityadmin_denied": not bool(values.get("is_securityadmin", values.get(2))),
                "db_owner_denied": not bool(values.get("is_db_owner", values.get(3))),
                "db_datareader_denied": not bool(values.get("is_db_datareader", values.get(4))),
                "db_datawriter_denied": not bool(values.get("is_db_datawriter", values.get(5))),
                "control_server_denied": not bool(values.get("control_server", values.get(6))),
                "alter_any_login_denied": not bool(values.get("alter_any_login", values.get(7))),
                "create_any_database_denied": not bool(values.get("create_any_database", values.get(8))),
                "audit_control_denied": not bool(values.get("audit_control", values.get(9))),
                "audit_alter_denied": not bool(values.get("audit_alter", values.get(10))),
                "audit_delete_denied": not bool(values.get("audit_delete", values.get(11))),
            }
        )
        try:
            cursor.execute("SELECT TOP (1) name FROM msdb.dbo.sysjobs")
            checks["unrelated_msdb_read_denied"] = False
        except Exception:
            checks["unrelated_msdb_read_denied"] = True
    finally:
        connection.close()
    checks["status"] = "PASS" if all(checks.values()) else "FAIL"
    return checks


def provision(*, rotate_existing: bool = False, runtime_config_path: Path | None = None) -> dict[str, Any]:
    admin_settings = MssqlConnectionSettings.from_environment()
    if str(admin_settings.user or "").lower() == RUNTIME_LOGIN.lower():
        raise RuntimeError("one-time provisioning must use the administrator credential, not the runtime login")
    password = _generate_password()
    connection = connect_mssql(admin_settings)
    try:
        cursor = connection.cursor()
        cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
        existed = _create_login(cursor, password, rotate_existing=rotate_existing)
        cursor.execute("COMMIT TRANSACTION;")
        connection.commit()
    except BaseException:
        try:
            connection.rollback()
        finally:
            connection.close()
        raise
    connection.close()

    runtime_settings = replace(admin_settings, user=RUNTIME_LOGIN, password=password)
    positive = MssqlPersistence(settings=runtime_settings, raw_evidence_directory=None)
    identity = positive.runtime_login_status()
    schema = positive.validate_schema()
    targets = positive.load_existing_sampling_targets()
    if len(targets) != 7:
        raise RuntimeError("dedicated login could not read the approved seven-target scope")
    selected = positive.select_existing_legacy7_target(at_utc=datetime.now(timezone.utc), targets=targets)
    negative = _negative_permission_checks(runtime_settings)
    if negative["status"] != "PASS":
        raise RuntimeError("dedicated login negative permission boundary failed")

    target_config = runtime_config_path or merge_non_secret_runtime_values().config_path
    local_values, _path, _loaded = load_local_runtime_values({"COLLECTOR_RUNTIME_CONFIG": str(target_config)})
    if not local_values:
        local_values = {
            key: value
            for key, value in merge_non_secret_runtime_values().values.items()
            if key in LOCAL_RUNTIME_KEYS and key not in {"MSSQL_USER", "SQL_SERVER_USER"}
        }
    local_values["MSSQL_USER"] = RUNTIME_LOGIN
    local_values.pop("SQL_SERVER_USER", None)
    written = write_local_runtime_config(target_config, local_values)
    store_keychain_secret(service=KEYCHAIN_SERVICE_MSSQL, account=KEYCHAIN_ACCOUNT_MSSQL, secret=password)

    return {
        "status": "PASS",
        "runtime_login": RUNTIME_LOGIN,
        "runtime_role": RUNTIME_ROLE,
        "login_previously_existed": existed,
        "identity": identity,
        "schema": schema,
        "positive_target_count": len(targets),
        "positive_selected_sampling_slot": selected.selected_sampling_slot,
        "positive_selected_sampling_target_id": selected.legacy_sampling_target_id,
        "negative_permission_checks": negative,
        "runtime_config_path": str(written),
        "keychain_account": KEYCHAIN_ACCOUNT_MSSQL,
        "password_printed": False,
        "password_written_to_source_or_plist": False,
        "grants": list(GRANT_STATEMENTS),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rotate-existing", action="store_true")
    parser.add_argument("--runtime-config", type=Path)
    args = parser.parse_args(argv)
    try:
        result = provision(
            rotate_existing=args.rotate_existing,
            runtime_config_path=args.runtime_config.expanduser().resolve() if args.runtime_config else None,
        )
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
