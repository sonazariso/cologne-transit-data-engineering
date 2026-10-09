"""Authenticated, sanitized administrative-evidence envelopes.

Administrative SQL evidence may be captured on the authoritative database host through a
separate login or Windows Authentication.  The resulting report is useful to
the macOS gates only when its provenance is explicit, its canonical payload is
hash-bound, and an operator-controlled signing key authenticates the capture.
Private keys and credentials stay outside the repository; only the detached
signature metadata is retained in the sanitized artifact.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping


EVIDENCE_ARTIFACT_VERSION = "TIERED50_ADMIN_EVIDENCE_V3"
SIGNATURE_ALGORITHM = "OPENSSL_DIGEST_SHA256"
ADMIN_EVIDENCE_MAX_AGE = timedelta(minutes=30)
ADMIN_EVIDENCE_CLOCK_SKEW = timedelta(seconds=5)
CURRENT_EVIDENCE_FILENAME = "tiered50-production-evidence-current.json"
CURRENT_EVIDENCE_PATH_ENV = "MSSQL_ADMIN_EVIDENCE_PATH"
WINDOWS_ENCRYPTION_MODES = frozenset({"Mandatory", "Optional", "Strict"})
WINDOWS_TLS_VALIDATION_METHOD = (
    "INVOKE_SQLCMD_TRUST_SERVER_CERTIFICATE_FALSE_AND_DM_EXEC_CONNECTIONS_ENCRYPT_OPTION_TRUE"
)
WINDOWS_MIN_FREE_PERCENT = 5.0
WINDOWS_MIN_FREE_RESERVE_BYTES = 1024 * 1024 * 1024
_APPROVED_EXTERNAL_EVIDENCE_ROOT = Path.home() / "Library" / "Application Support" / "CologneTransitIntelligence"
_APPROVED_OPERATOR_EVIDENCE_ROOT = Path("/operator-secure")
REQUIRED_CHECK_IDS = frozenset(
    {
        "admin.identity",
        "admin.permissions",
        "admin.sql_runtime_health",
        "capacity.database_files",
        "capacity.volume_capacity_dm_os_volume_stats",
        "capacity.database_log_space_dm_db_log_space_usage",
        "backup.latest_full_backup_history",
        "backup.restore_verifyonly_with_checksum",
        "collector_runtime_history_or_materialization",
        "collector.production_cutover_history",
        "collector.competing_production_writers",
        "quota.current_shared_ledger",
    }
)


class AdminEvidenceError(ValueError):
    """The administrative evidence envelope is not trustworthy or fresh."""


def _path_is_within(path: Path, root: Path) -> bool:
    """Return whether ``path`` is below ``root`` after both are resolved."""

    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _validate_controlled_evidence_location(path: Path) -> Path:
    """Reject symlinked or group/world-writable current-evidence locations."""

    resolved = path.expanduser().resolve(strict=False)
    if resolved.name != CURRENT_EVIDENCE_FILENAME:
        raise AdminEvidenceError(
            f"current administrative evidence must use the stable filename {CURRENT_EVIDENCE_FILENAME}"
        )
    if path.expanduser().is_symlink():
        raise AdminEvidenceError("current administrative evidence may not be a symlink")
    parent = resolved.parent
    if not parent.exists() or not parent.is_dir():
        raise AdminEvidenceError("current administrative evidence parent directory is unavailable")
    try:
        parent_mode = stat.S_IMODE(parent.stat().st_mode)
    except OSError as exc:
        raise AdminEvidenceError("current administrative evidence parent directory is not inspectable") from exc
    if parent_mode & 0o022:
        raise AdminEvidenceError("current administrative evidence parent directory is group/world writable")
    if resolved.exists():
        if not resolved.is_file() or resolved.is_symlink():
            raise AdminEvidenceError("current administrative evidence is not a regular file")
        try:
            file_mode = stat.S_IMODE(resolved.stat().st_mode)
        except OSError as exc:
            raise AdminEvidenceError("current administrative evidence file is not inspectable") from exc
        if file_mode & 0o022:
            raise AdminEvidenceError("current administrative evidence file is group/world writable")
    return resolved


def resolve_current_evidence_path(
    *,
    repository_root: str | Path,
    environ: Mapping[str, Any] | None = None,
) -> Path:
    """Resolve the one stable evidence path used by capture, signing, and Resume.

    The default is a repository-local stable artifact.  An operator may move
    the artifact to an approved owner-controlled application-support or
    operator-secure location, but a relative path, a daily filename, a
    temporary path, or a writable location is rejected.
    """

    root = Path(repository_root).expanduser().resolve()
    values = os.environ if environ is None else environ
    configured = str(values.get(CURRENT_EVIDENCE_PATH_ENV) or "").strip()
    default_path = root / "docs" / "operational" / CURRENT_EVIDENCE_FILENAME
    if not configured:
        return _validate_controlled_evidence_location(default_path)

    candidate = Path(configured).expanduser()
    if not candidate.is_absolute():
        raise AdminEvidenceError(f"{CURRENT_EVIDENCE_PATH_ENV} must be an absolute path")
    resolved = candidate.resolve(strict=False)
    approved_roots = (
        (root / "docs" / "operational").resolve(strict=False),
        _APPROVED_EXTERNAL_EVIDENCE_ROOT.expanduser().resolve(strict=False),
        _APPROVED_OPERATOR_EVIDENCE_ROOT.resolve(strict=False),
    )
    if not any(_path_is_within(resolved, approved_root) for approved_root in approved_roots):
        raise AdminEvidenceError(
            f"{CURRENT_EVIDENCE_PATH_ENV} is outside the approved administrative-evidence locations"
        )
    return _validate_controlled_evidence_location(candidate)


def require_current_evidence_path(
    path: str | Path,
    *,
    repository_root: str | Path,
    environ: Mapping[str, Any] | None = None,
) -> Path:
    """Require a supplied path to be exactly the configured current artifact."""

    expected = resolve_current_evidence_path(repository_root=repository_root, environ=environ)
    supplied = Path(path).expanduser()
    if not supplied.is_absolute():
        raise AdminEvidenceError("administrative evidence output path must be absolute")
    supplied_resolved = supplied.resolve(strict=False)
    if supplied_resolved != expected:
        raise AdminEvidenceError(
            "conflicting administrative evidence path; the final signed artifact must be written to "
            + str(expected)
        )
    return _validate_controlled_evidence_location(supplied)


def require_operator_staging_path(
    path: str | Path,
    *,
    repository_root: str | Path,
) -> Path:
    """Require a temporary signed-evidence staging file in an approved location."""

    root = Path(repository_root).expanduser().resolve()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise AdminEvidenceError("administrative evidence staging path must be absolute")
    resolved = candidate.resolve(strict=False)
    if _path_is_within(resolved, root):
        raise AdminEvidenceError("intermediate administrative evidence must remain outside the repository")
    approved_roots = (
        _APPROVED_EXTERNAL_EVIDENCE_ROOT.expanduser().resolve(strict=False),
        _APPROVED_OPERATOR_EVIDENCE_ROOT.resolve(strict=False),
    )
    if not any(_path_is_within(resolved, approved_root) for approved_root in approved_roots):
        raise AdminEvidenceError("administrative evidence staging path is outside approved operator locations")
    if candidate.is_symlink():
        raise AdminEvidenceError("administrative evidence staging path may not be a symlink")
    parent = resolved.parent
    if not parent.exists() or not parent.is_dir():
        raise AdminEvidenceError("administrative evidence staging parent directory is unavailable")
    if stat.S_IMODE(parent.stat().st_mode) & 0o022:
        raise AdminEvidenceError("administrative evidence staging parent directory is group/world writable")
    if resolved.exists():
        if not resolved.is_file() or resolved.is_symlink():
            raise AdminEvidenceError("administrative evidence staging path is not a regular file")
        if stat.S_IMODE(resolved.stat().st_mode) & 0o077:
            raise AdminEvidenceError("administrative evidence staging file must be owner-readable only")
    return resolved


def normalize_sql_instance_identity(identity: Mapping[str, Any] | None) -> dict[str, str]:
    """Return the non-secret identity fields used for runtime binding.

    ``@@SERVERNAME`` can reflect a SQL Server registration name and a client
    may connect through an alias.  The server name returned by
    ``SERVERPROPERTY('ServerName')`` is the authoritative instance identity
    used here; optional machine and instance fields strengthen the comparison
    when SQL Server provides them.  Connection aliases are deliberately not
    compared as if they were instance identities.
    """

    if not isinstance(identity, Mapping):
        return {}
    normalized: dict[str, str] = {}
    for field in ("server_name", "machine_name", "instance_name", "database_name"):
        value = identity.get(field)
        if value is None:
            continue
        text = re.sub(r"\s+", " ", str(value)).strip().casefold()
        if text:
            normalized[field] = text
    return normalized


def sql_instance_identities_match(
    observed: Mapping[str, Any] | None,
    expected: Mapping[str, Any] | None,
) -> bool:
    """Compare actual SQL instance identities without accepting aliases blindly."""

    observed_normalized = normalize_sql_instance_identity(observed)
    expected_normalized = normalize_sql_instance_identity(expected)
    if not observed_normalized.get("server_name") or not expected_normalized.get("server_name"):
        return False
    if observed_normalized["server_name"] != expected_normalized["server_name"]:
        return False
    for field in ("machine_name", "instance_name", "database_name"):
        expected_value = expected_normalized.get(field)
        observed_value = observed_normalized.get(field)
        if expected_value and observed_value and expected_value != observed_value:
            return False
    return True


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Mapping):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(child) for child in value]
    return value


def canonical_payload(evidence: Mapping[str, Any]) -> bytes:
    """Serialize the signed payload without mutable integrity metadata."""

    if not isinstance(evidence, Mapping):
        raise AdminEvidenceError("administrative evidence must be an object")
    payload = {str(key): value for key, value in evidence.items() if key != "artifact_integrity"}
    return json.dumps(
        _json_safe(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def payload_sha256(evidence: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_payload(evidence)).hexdigest()


def _run_openssl(arguments: list[str], *, input_bytes: bytes | None = None) -> bytes:
    openssl = shutil.which("openssl") or "/usr/bin/openssl"
    try:
        completed = subprocess.run(
            [openssl, *arguments],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise AdminEvidenceError("OpenSSL is unavailable for administrative-evidence authentication") from exc
    if completed.returncode != 0:
        raise AdminEvidenceError("OpenSSL rejected the administrative-evidence signature operation")
    return completed.stdout


def _temporary_bytes(value: bytes) -> Path:
    descriptor, name = tempfile.mkstemp(prefix="mdd-admin-evidence-", suffix=".sig")
    os.close(descriptor)
    path = Path(name)
    path.write_bytes(value)
    path.chmod(0o600)
    return path


def sign_payload(evidence: Mapping[str, Any], *, signing_key_path: str | Path) -> dict[str, Any]:
    """Sign the canonical evidence payload with an operator-held key."""

    key = Path(signing_key_path).expanduser().resolve()
    if not key.is_file():
        raise AdminEvidenceError("administrative-evidence signing key is unavailable")
    project_root = Path(__file__).resolve().parents[2]
    if key == project_root or project_root in key.parents:
        raise AdminEvidenceError("administrative-evidence signing key must remain outside the repository")
    if stat.S_IMODE(key.stat().st_mode) & 0o077:
        raise AdminEvidenceError("administrative-evidence signing key must be owner-readable only")
    signature = _run_openssl(["dgst", "-sha256", "-sign", str(key)], input_bytes=canonical_payload(evidence))
    return {
        "status": "SIGNED",
        "algorithm": SIGNATURE_ALGORITHM,
        "payload_sha256": payload_sha256(evidence),
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }


def verify_signature(
    evidence: Mapping[str, Any],
    *,
    public_key_path: str | Path,
    integrity: Mapping[str, Any] | None = None,
) -> bool:
    """Verify a signature using a public key supplied outside the artifact."""

    metadata = integrity or evidence.get("artifact_integrity")
    if not isinstance(metadata, Mapping):
        return False
    if metadata.get("status") != "SIGNED" or metadata.get("algorithm") != SIGNATURE_ALGORITHM:
        return False
    try:
        signature = base64.b64decode(str(metadata.get("signature_base64") or ""), validate=True)
    except (ValueError, TypeError):
        return False
    public_key = Path(public_key_path).expanduser().resolve()
    if not public_key.is_file():
        return False
    openssl = shutil.which("openssl") or "/usr/bin/openssl"
    signature_path = _temporary_bytes(signature)
    payload_path = _temporary_bytes(canonical_payload(evidence))
    try:
        try:
            completed = subprocess.run(
                [
                    openssl,
                    "dgst",
                    "-sha256",
                    "-verify",
                    str(public_key),
                    "-signature",
                    str(signature_path),
                    str(payload_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError:
            return False
        return completed.returncode == 0 and completed.stdout.strip().lower() == b"verified ok"
    finally:
        for path in (signature_path, payload_path):
            try:
                path.unlink()
            except OSError:
                pass


def _parse_utc(value: Any, *, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise AdminEvidenceError(f"{field} is not a valid timestamp") from exc
    if parsed.tzinfo is None:
        raise AdminEvidenceError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_windows_tls_evidence(tls: Mapping[str, Any] | None) -> dict[str, Any]:
    """Require live encrypted transport and a non-bypassed certificate check.

    ``-Encrypt`` and ``-TrustServerCertificate`` are client requests, not
    observations.  The Windows capture therefore also records the SQL Server
    session's ``sys.dm_exec_connections.encrypt_option`` result.  The
    certificate-validation method is intentionally a fixed contract so a
    configured flag cannot be relabeled as authenticated TLS on its own.
    """

    if not isinstance(tls, Mapping):
        raise AdminEvidenceError("Windows administrative evidence is missing TLS transport evidence")
    mode = str(tls.get("encryption_mode") or "")
    if mode not in WINDOWS_ENCRYPTION_MODES:
        raise AdminEvidenceError("Windows administrative evidence uses an unsupported encryption mode")
    if tls.get("trust_server_certificate") is not False:
        raise AdminEvidenceError("Windows administrative evidence must disable Trust Server Certificate")
    if tls.get("certificate_validation_required") is not True:
        raise AdminEvidenceError("Windows administrative evidence does not require certificate validation")
    actual_encryption = str(tls.get("actual_encryption") or "").strip().upper()
    if actual_encryption not in {"TRUE", "YES", "1"}:
        raise AdminEvidenceError("Windows administrative evidence does not prove encrypted SQL transport")
    if str(tls.get("actual_transport") or "").strip().upper() != "ENCRYPTED":
        raise AdminEvidenceError("Windows administrative evidence has no encrypted transport classification")
    if str(tls.get("certificate_validation_status") or "").strip().upper() != "VALIDATED":
        raise AdminEvidenceError("Windows administrative evidence does not prove certificate validation")
    if tls.get("certificate_validation_method") != WINDOWS_TLS_VALIDATION_METHOD:
        raise AdminEvidenceError("Windows administrative evidence has an unsupported certificate-validation claim")
    return dict(tls)


def validate_windows_vm_disk_evidence(sql: Mapping[str, Any] | None) -> dict[str, Any]:
    """Require measured fixed volumes and meaningful SQL data/log headroom."""

    if not isinstance(sql, Mapping):
        raise AdminEvidenceError("Windows VM disk evidence is missing SQL evidence")
    disk = sql.get("windows_vm_disk")
    if not isinstance(disk, Mapping) or disk.get("status") != "PASS":
        raise AdminEvidenceError("Windows VM disk capacity is not proven")
    volumes = disk.get("volumes")
    if not isinstance(volumes, list) or not volumes:
        raise AdminEvidenceError("Windows VM fixed-disk measurements are missing")
    for volume in volumes:
        if not isinstance(volume, Mapping):
            raise AdminEvidenceError("Windows VM fixed-disk measurements are malformed")
        try:
            total_bytes = int(volume.get("total_bytes", 0))
            free_bytes = int(volume.get("free_bytes", -1))
        except (TypeError, ValueError) as exc:
            raise AdminEvidenceError("Windows VM fixed-disk measurements are not numeric") from exc
        if total_bytes <= 0 or free_bytes <= 0 or free_bytes > total_bytes:
            raise AdminEvidenceError("Windows VM fixed-disk measurements are invalid")

    sql_headroom = disk.get("sql_volume_headroom")
    if not isinstance(sql_headroom, list) or not sql_headroom:
        raise AdminEvidenceError("SQL data/log volume headroom is not proven")
    observed_file_types: set[str] = set()
    for volume in sql_headroom:
        if not isinstance(volume, Mapping) or volume.get("headroom_status") != "PASS":
            raise AdminEvidenceError("SQL data/log volume headroom is inadequate or unproven")
        file_types = volume.get("database_file_types")
        if not isinstance(file_types, list):
            raise AdminEvidenceError("SQL data/log volume file bindings are missing")
        observed_file_types.update(str(value).upper() for value in file_types)
        try:
            total_bytes = int(volume.get("total_bytes", 0))
            free_bytes = int(volume.get("available_bytes", -1))
            required_bytes = int(volume.get("minimum_required_free_bytes", 0))
            headroom_bytes = int(volume.get("headroom_bytes", -1))
        except (TypeError, ValueError) as exc:
            raise AdminEvidenceError("SQL data/log volume headroom is not numeric") from exc
        if (
            total_bytes <= 0
            or free_bytes <= 0
            or required_bytes < WINDOWS_MIN_FREE_RESERVE_BYTES
            or headroom_bytes <= 0
            or free_bytes < required_bytes
        ):
            raise AdminEvidenceError("SQL data/log volume does not have meaningful usable headroom")
        try:
            free_percent = float(volume.get("available_percent", -1))
        except (TypeError, ValueError) as exc:
            raise AdminEvidenceError("SQL data/log volume free percentage is not numeric") from exc
        if free_percent < WINDOWS_MIN_FREE_PERCENT:
            raise AdminEvidenceError("SQL data/log volume free percentage is below the approved reserve")
    if "ROWS" not in observed_file_types or "LOG" not in observed_file_types:
        raise AdminEvidenceError("SQL data and log volumes were not both bound to measured headroom")
    return {
        "status": "PASS",
        "fixed_volume_count": len(volumes),
        "sql_volume_count": len(sql_headroom),
        "database_file_types": sorted(observed_file_types),
    }


def validate_admin_evidence_artifact(
    evidence: Mapping[str, Any],
    *,
    now_utc: datetime,
    expected_database: str,
    public_key_path: str | Path | None,
    expected_sql_identity: Mapping[str, Any] | None = None,
    max_age: timedelta = ADMIN_EVIDENCE_MAX_AGE,
) -> dict[str, Any]:
    """Validate freshness, provenance, signature, and required checks."""

    if str(evidence.get("artifact_version") or "") != EVIDENCE_ARTIFACT_VERSION:
        raise AdminEvidenceError("administrative evidence is not a signed V3 artifact")
    generated_at = _parse_utc(evidence.get("generated_at_utc"), field="generated_at_utc")
    now = _parse_utc(now_utc, field="now_utc")
    age = now - generated_at
    if age < -ADMIN_EVIDENCE_CLOCK_SKEW or age > max_age:
        raise AdminEvidenceError("administrative evidence is stale or from the future")

    provenance = evidence.get("capture_provenance")
    if not isinstance(provenance, Mapping) or provenance.get("status") != "PASS":
        raise AdminEvidenceError("authenticated SQL capture provenance is not proven")
    if provenance.get("read_only") is not True:
        raise AdminEvidenceError("administrative evidence capture is not marked read-only")
    if provenance.get("database_name") != expected_database:
        raise AdminEvidenceError("administrative evidence is bound to the wrong database")
    if not str(provenance.get("server_name") or "").strip():
        raise AdminEvidenceError("administrative evidence is not bound to a SQL instance")
    if not str(provenance.get("effective_login") or "").strip():
        raise AdminEvidenceError("administrative evidence does not identify the effective administrator")
    observed_sql_identity = provenance.get("sql_instance_identity")
    if isinstance(observed_sql_identity, Mapping):
        normalized_identity = normalize_sql_instance_identity(observed_sql_identity)
        provenance_server = normalize_sql_instance_identity(
            {"server_name": provenance.get("server_name")}
        ).get("server_name")
        provenance_database = normalize_sql_instance_identity(
            {"database_name": provenance.get("database_name")}
        ).get("database_name")
        if (
            not normalized_identity.get("server_name")
            or provenance_server != normalized_identity["server_name"]
            or provenance_database != normalized_identity.get("database_name")
        ):
            raise AdminEvidenceError("administrative evidence has inconsistent SQL instance provenance")
    if expected_sql_identity is not None:
        if not isinstance(observed_sql_identity, Mapping):
            raise AdminEvidenceError(
                "administrative evidence does not contain the authoritative SQL instance identity"
            )
        if not sql_instance_identities_match(observed_sql_identity, expected_sql_identity):
            raise AdminEvidenceError("administrative evidence is bound to a different SQL instance")
    if provenance.get("execution_path") not in {"SEPARATE_SQL_LOGIN", "WINDOWS_INTEGRATED_AUTH"}:
        raise AdminEvidenceError("administrative evidence has no authorized execution path")
    if provenance.get("execution_path") == "WINDOWS_INTEGRATED_AUTH":
        validate_windows_tls_evidence(provenance.get("tls"))
    try:
        captured_at = _parse_utc(provenance.get("captured_at_utc"), field="capture_provenance.captured_at_utc")
    except AdminEvidenceError:
        raise
    if abs(captured_at - generated_at) > timedelta(minutes=5):
        raise AdminEvidenceError("administrative evidence capture time is not bound to the artifact")

    required = set(provenance.get("required_check_ids") or ())
    if not REQUIRED_CHECK_IDS.issubset(required):
        missing = sorted(REQUIRED_CHECK_IDS - required)
        raise AdminEvidenceError("administrative evidence omits required checks: " + ", ".join(missing))

    integrity = evidence.get("artifact_integrity")
    if not isinstance(integrity, Mapping) or integrity.get("status") != "SIGNED":
        raise AdminEvidenceError("administrative evidence is not cryptographically signed")
    if integrity.get("payload_sha256") != payload_sha256(evidence):
        raise AdminEvidenceError("administrative evidence payload hash does not match")
    if public_key_path is not None:
        public_key = Path(public_key_path).expanduser().resolve()
        project_root = Path(__file__).resolve().parents[2]
        if project_root == public_key or project_root in public_key.parents:
            raise AdminEvidenceError("administrative-evidence public key must remain outside the repository")
    if public_key_path is None or not verify_signature(evidence, public_key_path=public_key_path, integrity=integrity):
        raise AdminEvidenceError("administrative evidence signature is not verified")

    sql = evidence.get("sql")
    if not isinstance(sql, Mapping):
        raise AdminEvidenceError("administrative SQL evidence is missing")
    if (sql.get("admin_authorization") or {}).get("status") != "PASS":
        raise AdminEvidenceError("administrative SQL authorization is not proven")
    if (sql.get("runtime_health") or {}).get("status") != "PASS":
        raise AdminEvidenceError("authoritative database runtime health is not proven")
    if (sql.get("backup_gate") or {}).get("status") != "PASS":
        raise AdminEvidenceError("SQL backup VERIFYONLY gate is not proven")
    if (sql.get("capacity_gate") or {}).get("status") != "PASS":
        raise AdminEvidenceError("SQL capacity gate is not proven")
    if (sql.get("log_space_gate") or {}).get("status") != "PASS":
        raise AdminEvidenceError("SQL log-space gate is not proven")
    quota = sql.get("quota_integrity") or sql.get("quota_usage")
    if not isinstance(quota, Mapping) or quota.get("status") != "PASS" or not quota.get("state_available", True):
        raise AdminEvidenceError("current SQL quota ledger is not proven")
    if provenance.get("execution_path") == "WINDOWS_INTEGRATED_AUTH":
        validate_windows_vm_disk_evidence(sql)
    return {
        "status": "PASS",
        "generated_at_utc": generated_at.isoformat().replace("+00:00", "Z"),
        "server_name": str(provenance["server_name"]),
        "database_name": str(provenance["database_name"]),
        "sql_instance_identity": dict(observed_sql_identity) if isinstance(observed_sql_identity, Mapping) else None,
        "effective_login": str(provenance["effective_login"]),
        "execution_path": str(provenance["execution_path"]),
        "signature_verified": True,
        "required_checks": sorted(required),
    }


def load_current_evidence_artifact(
    path: str | Path,
    *,
    repository_root: str | Path,
    now_utc: datetime,
    expected_database: str,
    public_key_path: str | Path | None,
    expected_sql_identity: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    """Read and authenticate the exact stable artifact consumed by Resume."""

    current_path = require_current_evidence_path(path, repository_root=repository_root)
    try:
        # The legacy Windows UTF8 writer emits a BOM.  The utf-8-sig codec
        # accepts both that valid transport and BOM-free output without
        # relaxing JSON/schema validation.
        loaded = json.loads(current_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AdminEvidenceError("current administrative evidence is missing or unreadable") from exc
    if not isinstance(loaded, dict):
        raise AdminEvidenceError("current administrative evidence must be a JSON object")
    authenticated = validate_admin_evidence_artifact(
        loaded,
        now_utc=now_utc,
        expected_database=expected_database,
        public_key_path=public_key_path,
        expected_sql_identity=expected_sql_identity,
    )
    return loaded, authenticated, current_path


__all__ = [
    "ADMIN_EVIDENCE_CLOCK_SKEW",
    "ADMIN_EVIDENCE_MAX_AGE",
    "AdminEvidenceError",
    "CURRENT_EVIDENCE_FILENAME",
    "CURRENT_EVIDENCE_PATH_ENV",
    "EVIDENCE_ARTIFACT_VERSION",
    "REQUIRED_CHECK_IDS",
    "WINDOWS_ENCRYPTION_MODES",
    "WINDOWS_MIN_FREE_PERCENT",
    "WINDOWS_MIN_FREE_RESERVE_BYTES",
    "WINDOWS_TLS_VALIDATION_METHOD",
    "canonical_payload",
    "load_current_evidence_artifact",
    "payload_sha256",
    "require_current_evidence_path",
    "require_operator_staging_path",
    "resolve_current_evidence_path",
    "sign_payload",
    "normalize_sql_instance_identity",
    "sql_instance_identities_match",
    "validate_admin_evidence_artifact",
    "validate_windows_tls_evidence",
    "validate_windows_vm_disk_evidence",
    "verify_signature",
]
