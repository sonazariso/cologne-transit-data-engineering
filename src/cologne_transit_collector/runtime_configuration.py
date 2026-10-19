"""Secure macOS runtime configuration for the direct SQL collector.

The tracked launchd template contains only gates and non-secret paths.  This
module supplies the remaining runtime values from a user-owned JSON file and
the macOS Keychain.  Environment variables remain the highest-precedence
explicit override so tests, CI, and one-off diagnostics keep working.

Secret values are returned only to the caller that needs them.  They are
never included in diagnostics, exception text, or the installed plist.
"""

from __future__ import annotations

import json
import ctypes
import ctypes.util
import os
import pty
import select
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol


APPLICATION_SUPPORT_DIRECTORY = Path.home() / "Library" / "Application Support" / "CologneTransitIntelligence"
DEFAULT_RUNTIME_CONFIG_PATH = APPLICATION_SUPPORT_DIRECTORY / "runtime.json"
RUNTIME_CONFIG_PATH_ENV = "COLLECTOR_RUNTIME_CONFIG"
EXPECTED_SQL_DATABASE = "CologneTransitIntelligence"
REQUIRED_PYTHON_INTERPRETER = "/Library/Developer/CommandLineTools/usr/bin/python3"

MDD_API_KEY_ENV = "MDD_API_KEY"
MSSQL_PASSWORD_ENV = "MSSQL_PASSWORD"
SQL_SERVER_PASSWORD_ENV = "SQL_SERVER_PASSWORD"

KEYCHAIN_SERVICE_MDD_API = "com.cologne-transit-intelligence.mdd-api-key"
KEYCHAIN_ACCOUNT_MDD_API = MDD_API_KEY_ENV
KEYCHAIN_SERVICE_MSSQL = "com.cologne-transit-intelligence.mssql-password"
KEYCHAIN_ACCOUNT_MSSQL = MSSQL_PASSWORD_ENV
KEYCHAIN_SERVICE_MSSQL_PENDING = "com.cologne-transit-intelligence.mssql-password.pending"
KEYCHAIN_ACCOUNT_MSSQL_PENDING = "MSSQL_PASSWORD_PENDING"
ADMIN_KEYCHAIN_SERVICE = "com.cologne-transit-intelligence.mssql-admin-password"
ISOLATED_LAB_ADMIN_KEYCHAIN_SERVICE = "com.cologne-transit-intelligence.mssql-lab-admin-password"
ISOLATED_LAB_ADMIN_KEYCHAIN_ACCOUNT = "CTI_LAB_MSSQL_PASSWORD"
ADMIN_MISSING_FIELDS_MARKER = "__ADMIN_MISSING_FIELDS__"
ISOLATED_LAB_MISSING_FIELDS_MARKER = "__ISOLATED_LAB_MISSING_FIELDS__"

MDD_API_CREDENTIAL = "MDD_API_KEY"
MSSQL_CREDENTIAL = "MSSQL_PASSWORD"

# The local file is deliberately narrower than the process environment.  In
# particular, it cannot carry either secret or a gate that could accidentally
# open production collection outside the installed LaunchAgent artifact.
LOCAL_RUNTIME_KEYS = frozenset(
    {
        "MSSQL_HOST",
        "SQL_SERVER_HOST",
        "MSSQL_PORT",
        "SQL_SERVER_CONTAINER_PORT",
        "MSSQL_DATABASE",
        "SQL_SERVER_DB",
        "MSSQL_USER",
        "SQL_SERVER_USER",
        "MSSQL_LOGIN_TIMEOUT_SECONDS",
        "MSSQL_QUERY_TIMEOUT_SECONDS",
        "MSSQL_RAW_EVIDENCE_DIR",
        "COLLECTOR_MDD_ENDPOINT",
        "COLLECTOR_REQUESTOR_REF",
        "COLLECTOR_HEARTBEAT_SAFETY_MARGIN_SECONDS",
    }
)


class RuntimeConfigurationError(RuntimeError):
    """The approved user-local runtime configuration is invalid."""


class SecretRequired(RuntimeError):
    """A required credential was not available from an approved source."""

    def __init__(self, credential_type: str) -> None:
        self.credential_type = credential_type
        super().__init__(f"SECRET_REQUIRED {credential_type}")


class SecretProvider(Protocol):
    """Minimal provider interface used by production code and unit tests."""

    def get(self, *, service: str, account: str) -> str | None:
        ...


class MacOSKeychainSecretProvider:
    """Read one generic-password item through the native ``security`` tool."""

    security_path = "/usr/bin/security"

    def get(self, *, service: str, account: str) -> str | None:
        # The secret is emitted by ``security -w`` into a captured pipe.  It
        # is never placed in argv, inherited as a diagnostic, or forwarded to
        # stdout/stderr by this method.
        try:
            completed = subprocess.run(
                [self.security_path, "find-generic-password", "-a", account, "-s", service, "-w"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0:
            return None
        try:
            value = completed.stdout.decode("utf-8").rstrip("\r\n")
        except (AttributeError, UnicodeDecodeError):
            return None
        return value if value else None


_ERR_SEC_SUCCESS = 0
_ERR_SEC_ITEM_NOT_FOUND = -25300


def _security_framework() -> Any | None:
    if sys.platform != "darwin":
        return None
    try:
        path = ctypes.util.find_library("Security") or "/System/Library/Frameworks/Security.framework/Security"
        return ctypes.CDLL(path)
    except OSError:
        return None


def _native_keychain_get(*, service: str, account: str) -> str | None:
    """Read a generic password with Security.framework, without subprocesses."""

    framework = _security_framework()
    if framework is None:
        return None
    service_bytes = service.encode("utf-8")
    account_bytes = account.encode("utf-8")
    password_length = ctypes.c_uint32(0)
    password_data = ctypes.c_void_p()
    item_ref = ctypes.c_void_p()
    find = framework.SecKeychainFindGenericPassword
    find.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    find.restype = ctypes.c_int32
    status = find(
        None,
        len(service_bytes),
        service_bytes,
        len(account_bytes),
        account_bytes,
        ctypes.byref(password_length),
        ctypes.byref(password_data),
        ctypes.byref(item_ref),
    )
    try:
        if status != _ERR_SEC_SUCCESS or password_length.value == 0 or not password_data.value:
            return None
        raw = ctypes.string_at(password_data, password_length.value)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    finally:
        free_content = getattr(framework, "SecKeychainItemFreeContent", None)
        if free_content is not None and password_data.value:
            free_content.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            free_content.restype = ctypes.c_int32
            free_content(None, password_data)
        _release_keychain_item(item_ref)


def _release_keychain_item(item_ref: ctypes.c_void_p) -> None:
    if not item_ref.value or sys.platform != "darwin":
        return
    try:
        core_foundation = ctypes.CDLL(
            ctypes.util.find_library("CoreFoundation") or "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
        core_foundation.CFRelease.restype = None
        core_foundation.CFRelease(item_ref)
    except OSError:
        pass


def _native_keychain_store(*, service: str, account: str, secret: str) -> int | None:
    """Create/update a generic password with Security.framework."""

    framework = _security_framework()
    if framework is None:
        return None
    service_bytes = service.encode("utf-8")
    account_bytes = account.encode("utf-8")
    secret_buffer = ctypes.create_string_buffer(secret.encode("utf-8"))
    item_ref = ctypes.c_void_p()
    try:
        find = framework.SecKeychainFindGenericPassword
        find.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        find.restype = ctypes.c_int32
        existing_length = ctypes.c_uint32(0)
        existing_data = ctypes.c_void_p()
        status = find(
            None,
            len(service_bytes),
            service_bytes,
            len(account_bytes),
            account_bytes,
            ctypes.byref(existing_length),
            ctypes.byref(existing_data),
            ctypes.byref(item_ref),
        )
        if status == _ERR_SEC_SUCCESS and item_ref.value:
            modify = framework.SecKeychainItemModifyAttributesAndData
            modify.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
            modify.restype = ctypes.c_int32
            return int(
                modify(
                    item_ref,
                    None,
                    len(secret.encode("utf-8")),
                    ctypes.cast(secret_buffer, ctypes.c_void_p),
                )
            )
        if status != _ERR_SEC_ITEM_NOT_FOUND:
            return int(status)
        add = framework.SecKeychainAddGenericPassword
        add.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        add.restype = ctypes.c_int32
        return int(
            add(
                None,
                len(service_bytes),
                service_bytes,
                len(account_bytes),
                account_bytes,
                len(secret.encode("utf-8")),
                ctypes.cast(secret_buffer, ctypes.c_void_p),
                ctypes.byref(item_ref),
            )
        )
    finally:
        for index in range(len(secret_buffer)):
            secret_buffer[index] = 0
        _release_keychain_item(item_ref)


def _native_keychain_delete(*, service: str, account: str) -> int | None:
    """Delete one generic password with Security.framework when available."""

    framework = _security_framework()
    if framework is None:
        return None
    service_bytes = service.encode("utf-8")
    account_bytes = account.encode("utf-8")
    password_length = ctypes.c_uint32(0)
    password_data = ctypes.c_void_p()
    item_ref = ctypes.c_void_p()
    find = framework.SecKeychainFindGenericPassword
    find.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    find.restype = ctypes.c_int32
    status = find(
        None,
        len(service_bytes),
        service_bytes,
        len(account_bytes),
        account_bytes,
        ctypes.byref(password_length),
        ctypes.byref(password_data),
        ctypes.byref(item_ref),
    )
    try:
        if status == _ERR_SEC_ITEM_NOT_FOUND:
            return _ERR_SEC_SUCCESS
        if status != _ERR_SEC_SUCCESS or not item_ref.value:
            return int(status)
        delete = framework.SecKeychainItemDelete
        delete.argtypes = [ctypes.c_void_p]
        delete.restype = ctypes.c_int32
        return int(delete(item_ref))
    finally:
        free_content = getattr(framework, "SecKeychainItemFreeContent", None)
        if free_content is not None and password_data.value:
            free_content.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            free_content.restype = ctypes.c_int32
            free_content(None, password_data)
        _release_keychain_item(item_ref)


@dataclass(frozen=True)
class RuntimeConfiguration:
    """Merged environment view used by one production process.

    The mapping is kept in memory only.  ``redacted`` is the only public
    diagnostic representation and never includes values.
    """

    values: Mapping[str, str]
    config_path: Path
    config_loaded: bool

    def redacted(self) -> dict[str, object]:
        return {
            "config_path": str(self.config_path),
            "config_loaded": self.config_loaded,
            "mssql_host_configured": bool(self.values.get("MSSQL_HOST") or self.values.get("SQL_SERVER_HOST")),
            "mssql_database": self.values.get("MSSQL_DATABASE") or self.values.get("SQL_SERVER_DB"),
            "mssql_user_configured": bool(self.values.get("MSSQL_USER") or self.values.get("SQL_SERVER_USER")),
            "mssql_password_configured": bool(
                self.values.get(MSSQL_PASSWORD_ENV) or self.values.get(SQL_SERVER_PASSWORD_ENV)
            ),
            "mdd_api_key_configured": bool(self.values.get(MDD_API_KEY_ENV)),
        }


def runtime_config_path(environ: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environ is None else environ
    configured = values.get(RUNTIME_CONFIG_PATH_ENV)
    if configured and configured.strip():
        return Path(configured).expanduser().resolve()
    return DEFAULT_RUNTIME_CONFIG_PATH


def _require_owner_only(path: Path, *, description: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise RuntimeConfigurationError(f"{description} must be owner-only")


def _normalize_local_value(key: str, value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise RuntimeConfigurationError("local runtime configuration values must be scalar")
    normalized = str(value)
    if "\x00" in normalized or "\r" in normalized or "\n" in normalized:
        raise RuntimeConfigurationError("local runtime configuration contains an invalid value")
    if key in {"MSSQL_DATABASE", "SQL_SERVER_DB"} and normalized != EXPECTED_SQL_DATABASE:
        raise RuntimeConfigurationError(f"selected database must be {EXPECTED_SQL_DATABASE}")
    return normalized


def load_local_runtime_values(environ: Mapping[str, str] | None = None) -> tuple[dict[str, str], Path, bool]:
    """Load and validate the non-secret user-local JSON configuration."""

    path = runtime_config_path(environ)
    source = os.environ if environ is None else environ
    explicitly_configured = bool(source.get(RUNTIME_CONFIG_PATH_ENV, "").strip())
    if not path.exists():
        if explicitly_configured:
            raise RuntimeConfigurationError("configured local runtime file does not exist")
        return {}, path, False
    if not path.is_file():
        raise RuntimeConfigurationError("configured local runtime path is not a file")
    _require_owner_only(path, description="local runtime configuration")
    try:
        with path.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeConfigurationError("local runtime configuration cannot be read") from exc
    if not isinstance(document, dict):
        raise RuntimeConfigurationError("local runtime configuration must be a JSON object")
    values: dict[str, str] = {}
    for key, value in document.items():
        if not isinstance(key, str) or key not in LOCAL_RUNTIME_KEYS:
            raise RuntimeConfigurationError("local runtime configuration contains an unsupported field")
        values[key] = _normalize_local_value(key, value)
    return values, path, True


def merge_non_secret_runtime_values(environ: Mapping[str, str] | None = None) -> RuntimeConfiguration:
    """Merge local non-secrets below explicit process environment values."""

    explicit = dict(os.environ if environ is None else environ)
    local, path, loaded = load_local_runtime_values(explicit)
    merged = dict(local)
    # Empty explicit values mean “not supplied”, allowing a valid local
    # non-secret value to remain effective.  Unknown environment values are
    # retained because existing scripts use them for their own gates/options.
    for key, value in explicit.items():
        if value is None:
            continue
        if key in LOCAL_RUNTIME_KEYS and not str(value).strip():
            continue
        merged[key] = str(value)
    return RuntimeConfiguration(values=merged, config_path=path, config_loaded=loaded)


def _first_nonempty(values: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = values.get(name)
        if value is not None and str(value).strip():
            return str(value)
    return None


def resolve_secret(
    values: Mapping[str, str],
    *,
    credential_type: str,
    provider: SecretProvider | None = None,
) -> str:
    """Resolve one secret from explicit environment first, then Keychain."""

    if credential_type == MDD_API_CREDENTIAL:
        explicit = _first_nonempty(values, MDD_API_KEY_ENV)
        service = KEYCHAIN_SERVICE_MDD_API
        account = KEYCHAIN_ACCOUNT_MDD_API
    elif credential_type == MSSQL_CREDENTIAL:
        explicit = _first_nonempty(values, MSSQL_PASSWORD_ENV, SQL_SERVER_PASSWORD_ENV)
        service = KEYCHAIN_SERVICE_MSSQL
        account = KEYCHAIN_ACCOUNT_MSSQL
    else:
        raise ValueError("unknown runtime credential type")
    if explicit is not None:
        return explicit
    keychain = provider or MacOSKeychainSecretProvider()
    resolved = keychain.get(service=service, account=account)
    if resolved is None:
        raise SecretRequired(credential_type)
    return resolved


def resolve_administrator_environment(
    environ: Mapping[str, str] | None = None,
    *,
    secret_provider: SecretProvider | None = None,
) -> tuple[dict[str, str], str]:
    """Resolve the explicitly configured administrator connection contract.

    Administrator access is intentionally a separate credential domain.  The
    resolver only reads ``MSSQL_ADMIN_*`` values and the dedicated Keychain
    item; it never merges the collector's runtime login or password into the
    returned mapping.  The returned mapping is suitable for
    ``MssqlConnectionSettings.from_environment`` and is kept in memory only.

    A partial configuration is returned with a secret-free missing-field
    marker so read-only evidence workflows can report the exact blocker
    without attempting a runtime fallback.
    """

    source = os.environ if environ is None else environ
    values: dict[str, str] = {}
    missing: list[str] = []
    for suffix in ("HOST", "DATABASE", "USER"):
        name = f"MSSQL_ADMIN_{suffix}"
        value = source.get(name)
        if value is None or not str(value).strip():
            missing.append(name)
        else:
            values[f"MSSQL_{suffix}"] = str(value).strip()

    password = source.get("MSSQL_ADMIN_PASSWORD")
    keychain_used = False
    if password is None or not str(password).strip():
        account = str(source.get("MSSQL_ADMIN_KEYCHAIN_ACCOUNT") or values.get("MSSQL_USER") or "").strip()
        service = str(source.get("MSSQL_ADMIN_KEYCHAIN_SERVICE") or ADMIN_KEYCHAIN_SERVICE).strip()
        password = (secret_provider or MacOSKeychainSecretProvider()).get(
            service=service,
            account=account,
        )
        keychain_used = password is not None and bool(str(password).strip())
    if password is None or not str(password).strip():
        missing.append("MSSQL_ADMIN_PASSWORD_OR_KEYCHAIN_ITEM")
    else:
        values["MSSQL_PASSWORD"] = str(password)

    for suffix in ("PORT", "LOGIN_TIMEOUT_SECONDS", "QUERY_TIMEOUT_SECONDS"):
        name = f"MSSQL_ADMIN_{suffix}"
        value = source.get(name)
        if value is not None and str(value).strip():
            values[f"MSSQL_{suffix}"] = str(value).strip()

    if missing:
        values[ADMIN_MISSING_FIELDS_MARKER] = ",".join(missing)
        return values, "MSSQL_ADMIN_*_REQUIRED"
    return values, "MSSQL_ADMIN_*_KEYCHAIN" if keychain_used else "MSSQL_ADMIN_*_EXPLICIT"


def resolve_administrator_connection_settings(
    environ: Mapping[str, str] | None = None,
    *,
    secret_provider: SecretProvider | None = None,
    expected_database: str = EXPECTED_SQL_DATABASE,
) -> tuple[Any, str]:
    """Return validated admin SQL settings and the non-secret resolution source.

    The local import avoids a module cycle because the SQL adapter imports
    this module for ordinary runtime secret resolution.
    """

    values, source = resolve_administrator_environment(environ, secret_provider=secret_provider)
    missing = str(values.get(ADMIN_MISSING_FIELDS_MARKER) or "")
    if missing:
        raise RuntimeConfigurationError(
            "MISSING_ADMIN_CREDENTIALS_OR_PERMISSIONS: explicitly configure " + missing
        )
    from .mssql_persistence import MssqlConnectionSettings

    clean_values = {key: value for key, value in values.items() if not key.startswith("__")}
    settings = MssqlConnectionSettings.from_environment(clean_values, secret_provider=secret_provider)
    if settings.database != expected_database:
        raise RuntimeConfigurationError(f"target database must be {expected_database}")
    if str(settings.user or "").casefold() == "colognetransitcollector":
        raise RuntimeConfigurationError(
            "administrator SQL access requires a separate login from CologneTransitCollector"
        )
    return settings, source


def _normalized_sql_host(value: str) -> str:
    """Normalize a host label for the lab/production separation check."""

    host = str(value or "").strip().casefold()
    if host.startswith("tcp:"):
        host = host[4:]
    if "," in host:
        host = host.split(",", 1)[0]
    if "\\" in host:
        host = host.split("\\", 1)[0]
    return host.strip("[]")


def resolve_isolated_lab_environment(
    environ: Mapping[str, str] | None = None,
    *,
    secret_provider: SecretProvider | None = None,
) -> tuple[dict[str, str], str]:
    """Resolve the deliberately separate SQL identity used by the lab path.

    The lab resolver does not read ``MSSQL_ADMIN_*`` or the production
    administrator Keychain item.  Requiring a lab instance identifier and a
    lab-prefixed host/user/password prevents a copied production command from
    silently changing its target merely because the normal runtime variables
    are present on the Mac.
    """

    source = os.environ if environ is None else environ
    values: dict[str, str] = {}
    missing: list[str] = []
    for suffix in ("HOST", "DATABASE", "USER", "INSTANCE_ID"):
        name = f"CTI_LAB_MSSQL_{suffix}"
        value = source.get(name)
        if value is None or not str(value).strip():
            missing.append(name)
        else:
            values[f"MSSQL_{suffix if suffix != 'INSTANCE_ID' else 'LAB_INSTANCE_ID'}"] = str(value).strip()

    host = str(source.get("CTI_LAB_MSSQL_HOST") or "").strip()
    normalized_host = _normalized_sql_host(host)
    production_host = _normalized_sql_host(str(source.get("MSSQL_ADMIN_HOST") or "DataAnalyst-VM"))
    if host and normalized_host == production_host:
        raise RuntimeConfigurationError(
            "isolated-lab SQL host must be different from the production administrator host"
        )
    instance_id = str(source.get("CTI_LAB_MSSQL_INSTANCE_ID") or "").strip()
    if instance_id and "production" in instance_id.casefold():
        raise RuntimeConfigurationError("isolated-lab instance identifier cannot identify production")

    password = source.get("CTI_LAB_MSSQL_PASSWORD")
    keychain_used = False
    if password is None or not str(password).strip():
        account = str(source.get("CTI_LAB_MSSQL_KEYCHAIN_ACCOUNT") or ISOLATED_LAB_ADMIN_KEYCHAIN_ACCOUNT).strip()
        service = str(
            source.get("CTI_LAB_MSSQL_KEYCHAIN_SERVICE") or ISOLATED_LAB_ADMIN_KEYCHAIN_SERVICE
        ).strip()
        if service == ADMIN_KEYCHAIN_SERVICE:
            raise RuntimeConfigurationError(
                "isolated-lab credentials may not use the production administrator Keychain service"
            )
        password = (secret_provider or MacOSKeychainSecretProvider()).get(
            service=service,
            account=account,
        )
        keychain_used = password is not None and bool(str(password).strip())
    if password is None or not str(password).strip():
        missing.append("CTI_LAB_MSSQL_PASSWORD_OR_LAB_KEYCHAIN_ITEM")
    else:
        values["MSSQL_PASSWORD"] = str(password)

    for suffix in ("PORT", "LOGIN_TIMEOUT_SECONDS", "QUERY_TIMEOUT_SECONDS"):
        name = f"CTI_LAB_MSSQL_{suffix}"
        value = source.get(name)
        if value is not None and str(value).strip():
            values[f"MSSQL_{suffix}"] = str(value).strip()

    if missing:
        values[ISOLATED_LAB_MISSING_FIELDS_MARKER] = ",".join(missing)
        return values, "CTI_LAB_MSSQL_*_REQUIRED"
    return values, "CTI_LAB_MSSQL_*_KEYCHAIN" if keychain_used else "CTI_LAB_MSSQL_*_EXPLICIT"


def resolve_isolated_lab_connection_settings(
    environ: Mapping[str, str] | None = None,
    *,
    secret_provider: SecretProvider | None = None,
    expected_database: str = EXPECTED_SQL_DATABASE,
) -> tuple[Any, str]:
    """Return SQL settings for an explicitly isolated, non-production lab."""

    values, source = resolve_isolated_lab_environment(environ, secret_provider=secret_provider)
    missing = str(values.get(ISOLATED_LAB_MISSING_FIELDS_MARKER) or "")
    if missing:
        raise RuntimeConfigurationError(
            "MISSING_ISOLATED_LAB_CREDENTIALS: explicitly configure " + missing
        )
    from .mssql_persistence import MssqlConnectionSettings

    clean_values = {key: value for key, value in values.items() if not key.startswith("__")}
    settings = MssqlConnectionSettings.from_environment(clean_values, secret_provider=secret_provider)
    if settings.database != expected_database:
        raise RuntimeConfigurationError(f"isolated-lab target database must be {expected_database}")
    if _normalized_sql_host(settings.host) == _normalized_sql_host("DataAnalyst-VM"):
        raise RuntimeConfigurationError("isolated-lab SQL host is the production host")
    if str(settings.user or "").casefold() == "colognetransitcollector":
        raise RuntimeConfigurationError("isolated-lab SQL access requires a separate administrator login")
    return settings, source


def resolve_required_secrets(
    configuration: RuntimeConfiguration,
    *,
    provider: SecretProvider | None = None,
) -> RuntimeConfiguration:
    """Return an in-memory merged view with both required secrets resolved."""

    values = dict(configuration.values)
    values[MDD_API_KEY_ENV] = resolve_secret(
        values,
        credential_type=MDD_API_CREDENTIAL,
        provider=provider,
    )
    values[MSSQL_PASSWORD_ENV] = resolve_secret(
        values,
        credential_type=MSSQL_CREDENTIAL,
        provider=provider,
    )
    return RuntimeConfiguration(values=values, config_path=configuration.config_path, config_loaded=configuration.config_loaded)


def _run_keychain_prompt(command: list[str], secret: str) -> int:
    """Run a prompt-based security command through a private pseudo-terminal."""

    pid, master_fd = pty.fork()
    if pid == 0:  # pragma: no cover - the child execs the native macOS tool
        os.execv(command[0], command)
    send_count = 0
    deadline = time.monotonic() + 10.0
    output = bytearray()
    status = None
    try:
        while time.monotonic() < deadline:
            finished, status_value = os.waitpid(pid, os.WNOHANG)
            if finished == pid:
                status = status_value
                break
            readable, _, _ = select.select([master_fd], [], [], 0.25)
            if readable:
                try:
                    output.extend(os.read(master_fd, 4096))
                except OSError:
                    pass
            output_lower = bytes(output).lower()
            if send_count == 0 and (b"password" in output_lower or time.monotonic() >= deadline - 9.5):
                os.write(master_fd, secret.encode("utf-8") + b"\n")
                send_count = 1
            elif send_count == 1 and b"retype" in output_lower:
                os.write(master_fd, secret.encode("utf-8") + b"\n")
                send_count = 2
        if status is None:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
            _, status = os.waitpid(pid, 0)
        if send_count == 0 and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0:
            # A future macOS security implementation may suppress the prompt;
            # treat a successful no-prompt update as successful without
            # disclosing the captured output.
            send_count = 1
        return os.waitstatus_to_exitcode(status) if send_count else 1
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass


def store_keychain_secret(*, service: str, account: str, secret: str) -> None:
    """Create/update a Keychain item without putting its value in argv."""

    if not secret:
        raise SecretRequired(account)
    command = [
        MacOSKeychainSecretProvider.security_path,
        "add-generic-password",
        "-U",
        "-a",
        account,
        "-s",
        service,
        "-w",
    ]
    try:
        exit_code = _run_keychain_prompt(command, secret)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeConfigurationError("Keychain update failed") from exc
    if exit_code != 0:
        raise RuntimeConfigurationError("Keychain update failed")


def delete_keychain_secret(*, service: str, account: str) -> None:
    """Delete one exact generic-password item without exposing a secret."""

    native_status = _native_keychain_delete(service=service, account=account)
    if native_status is not None:
        if native_status != _ERR_SEC_SUCCESS:
            raise RuntimeConfigurationError("Keychain pending-item cleanup failed")
        return
    try:
        completed = subprocess.run(
            [
                MacOSKeychainSecretProvider.security_path,
                "delete-generic-password",
                "-a",
                account,
                "-s",
                service,
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeConfigurationError("Keychain pending-item cleanup failed") from exc
    if completed.returncode != 0:
        raise RuntimeConfigurationError("Keychain pending-item cleanup failed")


def write_local_runtime_config(path: str | Path, values: Mapping[str, object]) -> Path:
    """Write only approved non-secret values with owner-only permissions."""

    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.chmod(0o700)
    normalized: dict[str, str] = {}
    for key, value in values.items():
        if key not in LOCAL_RUNTIME_KEYS:
            raise RuntimeConfigurationError("refusing to write unsupported runtime field")
        normalized[key] = _normalize_local_value(key, value)
    temporary = destination.with_name(destination.name + ".tmp")
    payload = json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(payload)
        temporary.chmod(0o600)
        os.replace(temporary, destination)
    except OSError as exc:
        raise RuntimeConfigurationError("local runtime configuration could not be written") from exc
    finally:
        # Do not remove arbitrary paths; this is only the exact temporary file
        # created above and is best-effort cleanup after a failed write.
        if temporary.exists() and temporary != destination:
            try:
                temporary.unlink()
            except OSError:
                pass
    destination.chmod(0o600)
    return destination


__all__ = [
    "APPLICATION_SUPPORT_DIRECTORY",
    "DEFAULT_RUNTIME_CONFIG_PATH",
    "EXPECTED_SQL_DATABASE",
    "KEYCHAIN_ACCOUNT_MDD_API",
    "KEYCHAIN_ACCOUNT_MSSQL",
    "KEYCHAIN_ACCOUNT_MSSQL_PENDING",
    "ADMIN_KEYCHAIN_SERVICE",
    "ISOLATED_LAB_ADMIN_KEYCHAIN_SERVICE",
    "ISOLATED_LAB_ADMIN_KEYCHAIN_ACCOUNT",
    "ADMIN_MISSING_FIELDS_MARKER",
    "ISOLATED_LAB_MISSING_FIELDS_MARKER",
    "KEYCHAIN_SERVICE_MDD_API",
    "KEYCHAIN_SERVICE_MSSQL",
    "KEYCHAIN_SERVICE_MSSQL_PENDING",
    "MDD_API_CREDENTIAL",
    "MDD_API_KEY_ENV",
    "MSSQL_CREDENTIAL",
    "MSSQL_PASSWORD_ENV",
    "MacOSKeychainSecretProvider",
    "REQUIRED_PYTHON_INTERPRETER",
    "RuntimeConfiguration",
    "RuntimeConfigurationError",
    "SecretRequired",
    "SecretProvider",
    "load_local_runtime_values",
    "merge_non_secret_runtime_values",
    "resolve_required_secrets",
    "resolve_administrator_connection_settings",
    "resolve_administrator_environment",
    "resolve_isolated_lab_connection_settings",
    "resolve_isolated_lab_environment",
    "resolve_secret",
    "runtime_config_path",
    "delete_keychain_secret",
    "store_keychain_secret",
    "write_local_runtime_config",
]
