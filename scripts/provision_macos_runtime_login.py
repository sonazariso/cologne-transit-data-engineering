#!/usr/bin/env python3
"""Provision and verify the dedicated macOS Tiered50 SQL runtime login.

The current administrator credential is used only for this one-time action.
The generated password is first anchored in a dedicated pending macOS Keychain
item before SQL changes, then promoted to the approved item only after the
dedicated login and least-privilege checks pass. It is never printed, placed
in SQL source, or written to the plist/runtime JSON.
"""

from __future__ import annotations

import argparse
import json
import secrets
import string
import sys
from dataclasses import dataclass
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
    KEYCHAIN_ACCOUNT_MSSQL_PENDING,
    KEYCHAIN_SERVICE_MSSQL,
    KEYCHAIN_SERVICE_MSSQL_PENDING,
    load_local_runtime_values,
    MacOSKeychainSecretProvider,
    merge_non_secret_runtime_values,
    RuntimeConfigurationError,
    delete_keychain_secret,
    store_keychain_secret,
    write_local_runtime_config,
)


RUNTIME_LOGIN = "CologneTransitCollector"
RUNTIME_ROLE = "CologneTransitCollectorRuntime"
PASSWORD_ALPHABET = string.ascii_letters + string.digits + "!@#$%^&*()-_=+[]{}:,.?"


@dataclass(frozen=True)
class RuntimePermission:
    """One permission required by a production runtime operation."""

    operation: str
    object_name: str
    permission: str


# Code-backed permission contract for one ordinary macOS Tiered50 heartbeat,
# including its startup/schema checks, durable writer lease, and atomic quota
# finalization fallback.  Legacy7 is historical and receives no activation
# path or separate production grant.
# Administrator/import/diagnostic reads are intentionally not in this matrix
# and must use an administrator login.
RUNTIME_PERMISSION_MATRIX = (
    RuntimePermission("read approved target definitions", "ctl.MddRealtimeSamplingTarget", "SELECT"),
    RuntimePermission("read approved ten-slot mapping", "ctl.MddRealtimeSamplingSlot", "SELECT"),
    RuntimePermission("read active Tiered50 materialization", "ctl.MddMacTiered50ProductionTarget", "SELECT"),
    RuntimePermission("execute Legacy7 selector", "ctl.uspGetMddRealtimeSamplingTarget", "EXECUTE"),
    RuntimePermission("inspect Legacy7 selector definition", "ctl.uspGetMddRealtimeSamplingTarget", "VIEW DEFINITION"),
    RuntimePermission("inspect required audit lineage columns", "ctl.MddMacTargetAudit", "VIEW DEFINITION"),
    RuntimePermission("read competing production writer status", "ctl.MddMacCollectorRun", "SELECT"),
    RuntimePermission("create collector run", "ctl.MddMacCollectorRun", "INSERT"),
    RuntimePermission("complete collector run", "ctl.MddMacCollectorRun", "UPDATE"),
    RuntimePermission("read durable production lease", "ctl.MddMacProductionLease", "SELECT"),
    RuntimePermission("acquire or release durable production lease", "ctl.MddMacProductionLease", "INSERT"),
    RuntimePermission("acquire or release durable production lease", "ctl.MddMacProductionLease", "UPDATE"),
    RuntimePermission("record durable production lease event", "ctl.MddMacProductionLeaseEvent", "INSERT"),
    RuntimePermission("claim/read dispatcher bucket", "ctl.MddMacDispatcherClaim", "SELECT"),
    RuntimePermission("claim dispatcher bucket", "ctl.MddMacDispatcherClaim", "INSERT"),
    RuntimePermission("complete dispatcher bucket", "ctl.MddMacDispatcherClaim", "UPDATE"),
    RuntimePermission("record dispatcher claim event", "ctl.MddMacDispatcherClaimEvent", "INSERT"),
    RuntimePermission("claim/read target bucket", "ctl.MddMacTargetClaim", "SELECT"),
    RuntimePermission("claim target bucket", "ctl.MddMacTargetClaim", "INSERT"),
    RuntimePermission("complete target bucket", "ctl.MddMacTargetClaim", "UPDATE"),
    RuntimePermission("record target claim event", "ctl.MddMacTargetClaimEvent", "INSERT"),
    RuntimePermission("create/read/update target audit", "ctl.MddMacTargetAudit", "SELECT"),
    RuntimePermission("create target audit", "ctl.MddMacTargetAudit", "INSERT"),
    RuntimePermission("complete target audit", "ctl.MddMacTargetAudit", "UPDATE"),
    RuntimePermission("record HTTP attempt", "ctl.MddMacHttpAttempt", "INSERT"),
    RuntimePermission("reserve quota permit", "ctl.MddMacQuotaPermit", "INSERT"),
    RuntimePermission("finalize quota permit", "ctl.MddMacQuotaPermit", "UPDATE"),
    RuntimePermission("verify quota permit finalization", "ctl.MddMacQuotaPermit", "SELECT"),
    RuntimePermission("read provider-wide shared quota baseline", "ctl.MddMacSharedQuotaAccounting", "SELECT"),
    RuntimePermission(
        "read bounded October bootstrap authorization",
        "ctl.MddMacConservativeBootstrapAuthorization",
        "SELECT",
    ),
    RuntimePermission(
        "read the verified automatic quota reset contract",
        "ctl.MddMacQuotaResetContract",
        "SELECT",
    ),
    RuntimePermission(
        "create an authorized future quota period atomically",
        "ctl.uspEnsureMddMacQuotaPeriod",
        "EXECUTE",
    ),
    RuntimePermission("record quota decision", "ctl.MddMacQuotaAudit", "INSERT"),
    RuntimePermission("persist parsed MDD snapshot", "stg.uspPersistMddMacSnapshot", "EXECUTE"),
)


def expected_grant_statements() -> tuple[str, ...]:
    """Render the canonical SQL grant list from the permission matrix."""

    permission_order = {
        "SELECT": 0,
        "INSERT": 1,
        "UPDATE": 2,
        "EXECUTE": 3,
        "VIEW DEFINITION": 4,
    }
    grouped: dict[str, list[str]] = {}
    for item in RUNTIME_PERMISSION_MATRIX:
        grouped.setdefault(item.object_name, [])
        if item.permission not in grouped[item.object_name]:
            grouped[item.object_name].append(item.permission)
    return tuple(
        "GRANT "
        + ", ".join(sorted(permissions, key=lambda item: permission_order[item]))
        + f" ON OBJECT::{object_name} TO [{RUNTIME_ROLE}]"
        for object_name, permissions in grouped.items()
    )


# Keep an explicit exported list for provisioning and for documentation/tests;
# the consistency test compares it to expected_grant_statements().
GRANT_STATEMENTS = expected_grant_statements()

REQUIRED_POSITIVE_OBJECTS = tuple(
    dict.fromkeys(item.object_name for item in RUNTIME_PERMISSION_MATRIX)
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


def _login_exists(cursor: Any) -> bool:
    cursor.execute(
        f"SELECT CASE WHEN SUSER_ID(N'{RUNTIME_LOGIN}') IS NULL THEN 0 ELSE 1 END AS login_exists"
    )
    row = cursor.fetchone()
    return bool(row.get("login_exists") if isinstance(row, Mapping) else row[0])


def _create_login(
    cursor: Any,
    password: str,
    *,
    rotate_existing: bool,
    reuse_existing_password: bool = False,
) -> bool:
    """Create or update only the server-level login.

    SQL Server does not allow ``sp_addlogin`` (and can reject password
    rotation procedures) inside an explicit transaction.  The caller runs
    this function on an autocommit connection, then commits the database-user,
    role, and grant changes separately.
    """

    exists = _login_exists(cursor)
    if exists and not rotate_existing and not reuse_existing_password:
        raise RuntimeError(
            "dedicated runtime login already exists; rerun with --rotate-existing only if rotation is intended"
        )
    if not exists:
        cursor.execute(
            "EXEC master.dbo.sp_addlogin @loginame=%s, @passwd=%s, "
            "@defdb=%s, @deflanguage=%s",
            (RUNTIME_LOGIN, password, "CologneTransitIntelligence", "us_english"),
        )
        cursor.execute(
            f"ALTER LOGIN [{RUNTIME_LOGIN}] WITH CHECK_POLICY = ON, CHECK_EXPIRATION = OFF"
        )
    elif rotate_existing:
        # CREATE/ALTER LOGIN does not accept a DB-API parameter in its
        # PASSWORD clause.  The parameterized system procedure keeps the
        # candidate out of SQL text, process arguments, and diagnostics.
        cursor.execute(
            "EXEC master.dbo.sp_password @new=%s, @loginame=%s",
            (password, RUNTIME_LOGIN),
        )
        cursor.execute(
            f"ALTER LOGIN [{RUNTIME_LOGIN}] WITH CHECK_POLICY = ON, CHECK_EXPIRATION = OFF"
        )
    return exists


def _ensure_runtime_database_principal(cursor: Any) -> None:
    """Create the database principal and apply the exact runtime grants."""

    cursor.execute(
        f"""
        IF USER_ID(N'{RUNTIME_LOGIN}') IS NULL
            CREATE USER [{RUNTIME_LOGIN}] FOR LOGIN [{RUNTIME_LOGIN}];
        IF DATABASE_PRINCIPAL_ID(N'{RUNTIME_ROLE}') IS NULL
            CREATE ROLE [{RUNTIME_ROLE}];
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
            ALTER ROLE [{RUNTIME_ROLE}]
                ADD MEMBER [{RUNTIME_LOGIN}];
        """
    )
    for statement in GRANT_STATEMENTS:
        cursor.execute(statement)


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
        for key, statement in (
            (
                "unrelated_import_read_denied",
                "SELECT TOP (1) ImportBatchId FROM ctl.MddImportBatch",
            ),
            (
                "unrelated_source_evidence_read_denied",
                "SELECT TOP (1) SnapshotId FROM stg.MddMacSourceSnapshot",
            ),
            (
                "unrelated_analytics_read_denied",
                "SELECT TOP (1) SnapshotId FROM analytics.vwMddMacSourceFieldCoverage",
            ),
        ):
            try:
                cursor.execute(statement)
                cursor.fetchone()
                checks[key] = False
            except Exception:
                checks[key] = True
    finally:
        connection.close()
    checks["status"] = "PASS" if all(checks.values()) else "FAIL"
    return checks


def _read_pending_password(provider: Any) -> str | None:
    return provider.get(service=KEYCHAIN_SERVICE_MSSQL_PENDING, account=KEYCHAIN_ACCOUNT_MSSQL_PENDING)


def _runtime_local_values(target_config: Path) -> dict[str, str]:
    local_values, _path, _loaded = load_local_runtime_values(
        {"COLLECTOR_RUNTIME_CONFIG": str(target_config)}
    )
    if not local_values:
        local_values = {
            key: value
            for key, value in merge_non_secret_runtime_values().values.items()
            if key in LOCAL_RUNTIME_KEYS and key not in {"MSSQL_USER", "SQL_SERVER_USER"}
        }
    local_values["MSSQL_USER"] = RUNTIME_LOGIN
    local_values.pop("SQL_SERVER_USER", None)
    return local_values


def _finalize_runtime_credential(
    *,
    password: str,
    target_config: Path,
    provider: Any,
) -> Path:
    """Promote the pending secret, write non-secrets, then remove pending."""

    # The exact candidate is still in memory and is copied to the final item.
    # The read-back check prevents deleting the only recoverable credential if
    # the native Keychain write did not persist the expected value.
    store_keychain_secret(
        service=KEYCHAIN_SERVICE_MSSQL,
        account=KEYCHAIN_ACCOUNT_MSSQL,
        secret=password,
    )
    final_value = provider.get(service=KEYCHAIN_SERVICE_MSSQL, account=KEYCHAIN_ACCOUNT_MSSQL)
    if final_value != password:
        raise RuntimeConfigurationError("final approved Keychain credential could not be verified")
    written = write_local_runtime_config(target_config, _runtime_local_values(target_config))
    # The pending item is deliberately removed last.  If this exact cleanup
    # fails, the caller reports recovery-required and a deterministic resume
    # can retry finalization with the same credential.
    delete_keychain_secret(
        service=KEYCHAIN_SERVICE_MSSQL_PENDING,
        account=KEYCHAIN_ACCOUNT_MSSQL_PENDING,
    )
    return written


def _recovery_required(*, phase: str, error: BaseException) -> dict[str, Any]:
    """Return a sanitized post-commit recovery state."""

    return {
        "status": "CREDENTIAL_FINALIZATION_RECOVERY_REQUIRED",
        "phase": phase,
        "error_type": type(error).__name__,
        "runtime_login": RUNTIME_LOGIN,
        "runtime_role": RUNTIME_ROLE,
        "sql_password_committed": True,
        "pending_keychain_item_retained": True,
        "launchagent_enabled": False,
        "password_printed": False,
        "password_written_to_source_or_plist": False,
    }


def provision(
    *,
    rotate_existing: bool = False,
    resume: bool = False,
    runtime_config_path: Path | None = None,
    keychain_provider: Any | None = None,
) -> dict[str, Any]:
    """Provision or safely resume the dedicated runtime credential.

    The pending Keychain item is created before a SQL password change.  A
    post-commit failure never generates a replacement password and never
    removes the pending item; ``--resume`` retries validation/finalization with
    that exact recoverable candidate.
    """

    if rotate_existing and resume:
        raise RuntimeError("--resume and --rotate-existing cannot be combined")
    admin_settings = MssqlConnectionSettings.from_environment()
    if str(admin_settings.user or "").lower() == RUNTIME_LOGIN.lower():
        raise RuntimeError("one-time provisioning must use the administrator credential, not the runtime login")
    provider = keychain_provider or MacOSKeychainSecretProvider()
    target_config = (runtime_config_path or merge_non_secret_runtime_values().config_path).expanduser().resolve()
    pending_password = _read_pending_password(provider)
    if resume and not pending_password:
        raise RuntimeError("no pending runtime credential is available for --resume")

    password: str
    existed: bool
    committed = False
    connection = connect_mssql(admin_settings)
    try:
        cursor = connection.cursor()
        # ``sp_addlogin`` is a server-level procedure and SQL Server rejects
        # it inside a transaction.  Keep the login operation in autocommit
        # mode; the pending Keychain item makes this non-rollbackable step
        # recoverable without generating another credential.  Test doubles
        # need not implement autocommit, so the helper is intentionally
        # capability-based.
        autocommit = getattr(connection, "autocommit", None)
        if callable(autocommit):
            autocommit(True)
        existed = _login_exists(cursor)
        if existed and not rotate_existing and not resume:
            if pending_password:
                raise RuntimeError("pending runtime credential exists; rerun with --resume")
            raise RuntimeError(
                "dedicated runtime login already exists; rerun with --rotate-existing only if rotation is intended"
            )
        if pending_password and not resume and not rotate_existing:
            raise RuntimeError("pending runtime credential exists; rerun with --resume")
        if resume:
            password = str(pending_password)
            reuse_existing_password = existed
        else:
            password = _generate_password()
            # This is the recovery anchor.  It must succeed before SQL is
            # changed, and the value is never printed or put in process args.
            store_keychain_secret(
                service=KEYCHAIN_SERVICE_MSSQL_PENDING,
                account=KEYCHAIN_ACCOUNT_MSSQL_PENDING,
                secret=password,
            )
            reuse_existing_password = False
        existed_from_create = _create_login(
            cursor,
            password,
            rotate_existing=rotate_existing,
            reuse_existing_password=reuse_existing_password,
        )
        if existed_from_create != existed:
            raise RuntimeError("dedicated runtime login existence changed during provisioning")

        # The server login now exists.  Switch back to an explicit transaction
        # for the database user, role membership, and object-level grants.
        if callable(autocommit):
            autocommit(False)
        cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
        _ensure_runtime_database_principal(cursor)
        cursor.execute("COMMIT TRANSACTION;")
        connection.commit()
        committed = True
    except BaseException:
        try:
            connection.rollback()
        finally:
            connection.close()
        raise
    connection.close()

    runtime_settings = replace(admin_settings, user=RUNTIME_LOGIN, password=password)
    validation_finished = False
    try:
        positive = MssqlPersistence(settings=runtime_settings, raw_evidence_directory=None)
        identity = positive.runtime_login_status()
        schema = positive.validate_runtime_schema()
        targets = positive.load_existing_sampling_targets()
        if len(targets) != 7:
            raise RuntimeError("dedicated login could not read the approved seven-target scope")
        selected = positive.select_existing_legacy7_target(
            at_utc=datetime.now(timezone.utc),
            targets=targets,
        )
        negative = _negative_permission_checks(runtime_settings)
        if negative["status"] != "PASS":
            raise RuntimeError("dedicated login negative permission boundary failed")
        validation_finished = True
        written = _finalize_runtime_credential(
            password=password,
            target_config=target_config,
            provider=provider,
        )
    except BaseException as exc:
        # SQL is already committed.  Never replace or discard the pending
        # candidate here: the operator must be able to resume deterministically.
        if committed:
            phase = "FINALIZATION" if validation_finished else "POST_COMMIT_VALIDATION"
            return _recovery_required(phase=phase, error=exc)
        raise

    return {
        "status": "PASS",
        "runtime_login": RUNTIME_LOGIN,
        "runtime_role": RUNTIME_ROLE,
        "login_previously_existed": existed,
        "resumed_pending_credential": bool(resume),
        "identity": identity,
        "schema": schema,
        "positive_target_count": len(targets),
        "positive_selected_sampling_slot": selected.selected_sampling_slot,
        "positive_selected_sampling_target_id": selected.legacy_sampling_target_id,
        "negative_permission_checks": negative,
        "runtime_config_path": str(written),
        "keychain_account": KEYCHAIN_ACCOUNT_MSSQL,
        "pending_keychain_account": KEYCHAIN_ACCOUNT_MSSQL_PENDING,
        "pending_keychain_removed": True,
        "launchagent_enabled": False,
        "password_printed": False,
        "password_written_to_source_or_plist": False,
        "grants": list(GRANT_STATEMENTS),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rotate-existing", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="resume post-commit validation/finalization from the pending Keychain item",
    )
    parser.add_argument("--runtime-config", type=Path)
    args = parser.parse_args(argv)
    try:
        result = provision(
            rotate_existing=args.rotate_existing,
            resume=args.resume,
            runtime_config_path=args.runtime_config.expanduser().resolve() if args.runtime_config else None,
        )
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result.get("status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
