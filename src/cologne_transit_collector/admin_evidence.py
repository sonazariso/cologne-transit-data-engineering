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
WINDOWS_AUDIT_QUERY_IDS = frozenset(
    {
        "collector.historical_runs",
        "collector.historical_target_audits",
        "collector.http_attempt_history",
        "collector.tiered50_first_heartbeat",
        "collector.tiered50_first_heartbeat_target_audits",
        "collector.tiered50_first_heartbeat_http_attempts",
        "collector.historical_outage",
        "collector.tiered50_later_runs",
    }
)
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


def utc_isoformat(value: datetime) -> str:
    """Serialize an evidence timestamp as an unambiguous UTC ISO-8601 value.

    SQL ``datetime2`` values have no timezone metadata.  Database timestamps in
    this project are UTC by contract, so a naive Python value is assigned UTC;
    it is never passed through the Mac host's local timezone first.
    """

    if not isinstance(value, datetime):
        raise AdminEvidenceError("evidence timestamp must be a datetime")
    parsed = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def json_safe(value: Any) -> Any:
    """Recursively normalize datetimes before an evidence artifact is written."""

    return _json_safe(value)


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
            text = str(value).replace("Z", "+00:00")
            text = re.sub(r"(\.\d{6})\d(?=[+-]\d{2}:\d{2}$)", r"\1", text)
            parsed = datetime.fromisoformat(text)
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
    if tls.get("certificate_validation_observed") is not True:
        raise AdminEvidenceError("Windows administrative evidence does not record a successful certificate-validation path")
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


def validate_windows_audit_evidence(sql: Mapping[str, Any] | None) -> dict[str, Any]:
    """Require executed, timestamped Windows audit queries and honest results.

    A successful query returning no rows is a valid SQL execution result.  It
    is not, however, evidence that a required historical record exists.  The
    Windows capture therefore carries both the query result and a semantic
    ``check_status`` for the historical-preservation checks.  In particular,
    the first heartbeat's target-audit ``HttpAttempts`` counters are reconciled
    with the actual attempt rows.  A zero-row attempt query cannot prove a
    known historical request was preserved.
    """

    if not isinstance(sql, Mapping):
        raise AdminEvidenceError("Windows administrative evidence is missing SQL audit evidence")
    audit = sql.get("audit_provenance")
    if not isinstance(audit, Mapping):
        raise AdminEvidenceError("Windows administrative evidence is missing audit provenance")

    expected_semantics = {
        "collector.historical_runs": "historical_rows",
        "collector.historical_target_audits": "historical_rows",
        "collector.http_attempt_history": "linked_query",
        "collector.tiered50_first_heartbeat": "exactly_one",
        "collector.tiered50_first_heartbeat_target_audits": "first_heartbeat_target_audits",
        "collector.tiered50_first_heartbeat_http_attempts": "first_heartbeat_http_attempts",
        "collector.historical_outage": "historical_rows",
        "collector.tiered50_later_runs": "query",
    }
    results: dict[str, Any] = {}
    query_entries: dict[str, Mapping[str, Any]] = {}
    query_rows: dict[str, list[Any]] = {}
    semantic_reasons: dict[str, str] = {}
    for query_id in sorted(WINDOWS_AUDIT_QUERY_IDS):
        entry = audit.get(query_id)
        if not isinstance(entry, Mapping):
            raise AdminEvidenceError(f"Windows administrative evidence is missing audit query {query_id}")
        query = entry.get("query")
        if not isinstance(query, Mapping):
            raise AdminEvidenceError(f"Windows audit query {query_id} has no query result metadata")
        if query.get("statement_id") != query_id:
            raise AdminEvidenceError(f"Windows audit query {query_id} has the wrong statement identifier")
        query_status = str(query.get("status") or "").upper()
        if query_status not in {"PASS", "NOT_PROVEN"}:
            raise AdminEvidenceError(f"Windows audit query {query_id} has an invalid status")
        if not isinstance(query.get("executed"), bool):
            raise AdminEvidenceError(f"Windows audit query {query_id} does not record execution")
        if query_status == "PASS" and query.get("executed") is not True:
            raise AdminEvidenceError(f"Windows audit query {query_id} claims PASS without execution")
        try:
            result_count = int(query.get("result_count", -1))
        except (TypeError, ValueError) as exc:
            raise AdminEvidenceError(f"Windows audit query {query_id} has an invalid result count") from exc
        if result_count < 0:
            raise AdminEvidenceError(f"Windows audit query {query_id} has an invalid result count")
        try:
            captured_at = _parse_utc(
                query.get("captured_at_utc"),
                field=f"sql.audit_provenance.{query_id}.query.captured_at_utc",
            )
        except AdminEvidenceError:
            raise
        rows = entry.get("rows")
        if not isinstance(rows, list):
            raise AdminEvidenceError(f"Windows audit query {query_id} has malformed result rows")
        if query_status == "PASS" and len(rows) != result_count:
            raise AdminEvidenceError(f"Windows audit query {query_id} has an inconsistent result count")
        if query_status == "NOT_PROVEN" and rows:
            raise AdminEvidenceError(f"Windows audit query {query_id} includes rows despite being unproven")
        if query_status == "NOT_PROVEN" and query.get("executed") is not True:
            if not str(query.get("reason") or "").strip():
                raise AdminEvidenceError(f"Windows audit query {query_id} was not executed without a cause")
        if query_status == "NOT_PROVEN" and not str(query.get("reason") or "").strip():
            raise AdminEvidenceError(f"Windows audit query {query_id} is unproven without a sanitized cause")

        query_entries[query_id] = entry
        query_rows[query_id] = rows
        check_status = str(entry.get("check_status") or "").upper()
        semantic = expected_semantics[query_id]
        if semantic == "query":
            expected_check_status = "PASS" if query_status == "PASS" else "NOT_PROVEN"
        elif semantic == "linked_query":
            linked = entry.get("linked_to_target_audit") is True
            expected_check_status = "PASS" if query_status == "PASS" and linked else "NOT_PROVEN"
        elif semantic == "exactly_one":
            expected_check_status = "PASS" if query_status == "PASS" and result_count == 1 else "NOT_PROVEN"
        elif semantic == "first_heartbeat_target_audits":
            linked = entry.get("linked_to_first_heartbeat_run") is True
            expected_check_status = (
                "PASS"
                if query_status == "PASS" and result_count > 0 and linked
                else "NOT_PROVEN"
            )
        elif semantic == "first_heartbeat_http_attempts":
            linked = (
                entry.get("linked_to_first_heartbeat_run") is True
                and entry.get("linked_to_target_audit") is True
            )
            expected_check_status = "PASS" if query_status == "PASS" and linked else "NOT_PROVEN"
        else:
            expected_check_status = "PASS" if query_status == "PASS" and result_count > 0 else "NOT_PROVEN"
        if query_id == "collector.http_attempt_history" and expected_check_status == "PASS":
            orphan_rows = [
                row
                for row in rows
                if isinstance(row, Mapping)
                and (
                    ("joined_audit_id" in row and row.get("joined_audit_id") in (None, ""))
                    or ("run_id" in row and not str(row.get("run_id") or "").strip())
                )
            ]
            if orphan_rows:
                expected_check_status = "NOT_PROVEN"
                semantic_reasons[query_id] = (
                    f"{len(orphan_rows)} HTTP attempt row(s) are orphaned from their target audit"
                )
        if query_id not in {
            "collector.tiered50_first_heartbeat_target_audits",
            "collector.tiered50_first_heartbeat_http_attempts",
        } and check_status != expected_check_status:
            raise AdminEvidenceError(f"Windows audit query {query_id} has an inaccurate check status")
        results[query_id] = {
            "status": query_status,
            "check_status": check_status,
            "result_count": result_count,
            "captured_at_utc": captured_at.isoformat().replace("+00:00", "Z"),
        }

    first_heartbeat = audit["collector.tiered50_first_heartbeat"]
    first_heartbeat_rows = first_heartbeat.get("rows")
    if not isinstance(first_heartbeat_rows, list) or len(first_heartbeat_rows) != 1:
        raise AdminEvidenceError("Windows first-heartbeat provenance is not available for RunId cross-check")
    first_heartbeat_row = first_heartbeat_rows[0]
    if not isinstance(first_heartbeat_row, Mapping):
        raise AdminEvidenceError("Windows first-heartbeat provenance has malformed row data")
    first_heartbeat_run_id = str(first_heartbeat_row.get("run_id") or "").strip()
    if not first_heartbeat_run_id:
        raise AdminEvidenceError("Windows first-heartbeat provenance is missing RunId")

    linked_target_audits = query_entries["collector.tiered50_first_heartbeat_target_audits"]
    linked_target_rows = query_rows["collector.tiered50_first_heartbeat_target_audits"]
    linked_http_attempts = query_entries["collector.tiered50_first_heartbeat_http_attempts"]
    linked_http_rows = query_rows["collector.tiered50_first_heartbeat_http_attempts"]

    target_errors: list[str] = []
    http_errors: list[str] = []
    target_audit_rows_by_id: dict[int, Mapping[str, Any]] = {}
    duplicate_target_audit_ids: set[int] = set()
    declared_attempts_by_audit: dict[int, int] = {}

    if linked_target_audits.get("linked_to_first_heartbeat_run") is not True:
        target_errors.append("first-heartbeat target audits are not linked to the original RunId")
    metadata_run_id = str(linked_target_audits.get("run_id") or "").strip()
    if metadata_run_id and metadata_run_id != first_heartbeat_run_id:
        target_errors.append("first-heartbeat target-audit metadata has a mismatched RunId")
    for row in linked_target_rows:
        if not isinstance(row, Mapping):
            target_errors.append("first-heartbeat target-audit rows are malformed")
            continue
        row_run_id = str(row.get("run_id") or "").strip()
        if row_run_id != first_heartbeat_run_id:
            target_errors.append("first-heartbeat target audits are not linked by RunId")
        try:
            audit_id = int(row["audit_id"])
        except (KeyError, TypeError, ValueError) as exc:
            target_errors.append("first-heartbeat target audits are missing AuditId")
            continue
        if audit_id in target_audit_rows_by_id:
            duplicate_target_audit_ids.add(audit_id)
        else:
            target_audit_rows_by_id[audit_id] = row
        try:
            declared_attempts = row["http_attempts"]
            if isinstance(declared_attempts, bool):
                raise ValueError
            declared_attempts = int(declared_attempts)
        except (KeyError, TypeError, ValueError):
            target_errors.append(f"AuditId {audit_id} is missing a numeric HttpAttempts count")
            continue
        if declared_attempts < 0:
            target_errors.append(f"AuditId {audit_id} has a negative HttpAttempts count")
            continue
        declared_attempts_by_audit[audit_id] = declared_attempts
    if duplicate_target_audit_ids:
        target_errors.append(
            "duplicated target-audit AuditId(s): "
            + ", ".join(str(value) for value in sorted(duplicate_target_audit_ids))
        )
    if not linked_target_rows:
        target_errors.append("the original heartbeat returned no target-audit rows")

    http_attempt_rows_by_audit: dict[int, int] = {}
    http_attempt_ids: set[int] = set()
    duplicate_http_attempt_ids: set[int] = set()
    http_attempt_keys: set[tuple[int, int]] = set()
    duplicate_http_attempt_keys: set[tuple[int, int]] = set()
    orphan_http_attempt_ids: list[str] = []
    for row in linked_http_rows:
        if not isinstance(row, Mapping):
            http_errors.append("first-heartbeat HTTP-attempt rows are malformed")
            continue
        row_run_id = str(row.get("run_id") or "").strip()
        if row_run_id != first_heartbeat_run_id:
            http_errors.append("first-heartbeat HTTP attempts are not linked by RunId")
        try:
            http_attempt_id = int(row["http_attempt_id"])
        except (KeyError, TypeError, ValueError):
            http_errors.append("first-heartbeat HTTP attempts are missing HttpAttemptId")
            http_attempt_id = None
        if http_attempt_id is not None:
            if http_attempt_id in http_attempt_ids:
                duplicate_http_attempt_ids.add(http_attempt_id)
            http_attempt_ids.add(http_attempt_id)
        try:
            audit_id = int(row["audit_id"])
        except (KeyError, TypeError, ValueError):
            http_errors.append("first-heartbeat HTTP attempts are missing AuditId")
            continue
        if audit_id not in target_audit_rows_by_id:
            orphan_http_attempt_ids.append(str(http_attempt_id or audit_id))
        else:
            http_attempt_rows_by_audit[audit_id] = http_attempt_rows_by_audit.get(audit_id, 0) + 1
            target_row = target_audit_rows_by_id[audit_id]
            if "joined_audit_id" in row and row.get("joined_audit_id") not in (None, ""):
                try:
                    joined_audit_id = int(row["joined_audit_id"])
                except (TypeError, ValueError):
                    joined_audit_id = None
                if joined_audit_id != audit_id:
                    http_errors.append(
                        f"HTTP attempt for AuditId {audit_id} has an inconsistent joined AuditId"
                    )
            for field in ("target_identity", "logical_due_bucket", "collection_context"):
                if field not in row or field not in target_row:
                    continue
                left = row.get(field)
                right = target_row.get(field)
                if left is None or right is None or str(left).strip() == "" or str(right).strip() == "":
                    continue
                if field == "logical_due_bucket":
                    try:
                        same = int(left) == int(right)
                    except (TypeError, ValueError):
                        same = False
                else:
                    same = str(left).strip() == str(right).strip()
                if not same:
                    http_errors.append(
                        f"HTTP attempt for AuditId {audit_id} has inconsistently attributed {field}"
                    )
        try:
            attempt_number = row["attempt_number"]
            if isinstance(attempt_number, bool):
                raise ValueError
            attempt_number = int(attempt_number)
            if attempt_number < 1:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            http_errors.append(f"HTTP attempt for AuditId {audit_id} has an invalid AttemptNumber")
            continue
        attempt_key = (audit_id, attempt_number)
        if attempt_key in http_attempt_keys:
            duplicate_http_attempt_keys.add(attempt_key)
        http_attempt_keys.add(attempt_key)
        if "joined_audit_id" in row and row.get("joined_audit_id") in (None, ""):
            orphan_http_attempt_ids.append(str(http_attempt_id or audit_id))

    if duplicate_http_attempt_ids:
        http_errors.append(
            "duplicated HTTP attempt HttpAttemptId(s): "
            + ", ".join(str(value) for value in sorted(duplicate_http_attempt_ids))
        )
    if duplicate_http_attempt_keys:
        http_errors.append(
            "duplicated HTTP attempt AuditId/AttemptNumber pair(s): "
            + ", ".join(f"{audit_id}/{attempt}" for audit_id, attempt in sorted(duplicate_http_attempt_keys))
        )
    if orphan_http_attempt_ids:
        http_errors.append(
            "first-heartbeat HTTP attempts are not linked to its target audits; "
            "orphaned HTTP attempt row(s) reference missing target AuditId(s): "
            + ", ".join(sorted(set(orphan_http_attempt_ids)))
        )

    for audit_id, declared_count in declared_attempts_by_audit.items():
        actual_count = http_attempt_rows_by_audit.get(audit_id, 0)
        if actual_count != declared_count:
            classification = "missing" if actual_count < declared_count else "duplicated or extra"
            http_errors.append(
                f"AuditId {audit_id} declares {declared_count} HTTP attempt(s) but "
                f"{actual_count} actual row(s) were returned ({classification} history)"
            )
    declared_total = sum(declared_attempts_by_audit.values())
    actual_total = len(linked_http_rows)
    if declared_total > 0 and actual_total == 0:
        http_errors.append(
            "known historical HTTP activity has no preserved first-heartbeat HTTP attempt rows"
        )
    if declared_total == 0 and actual_total > 0:
        http_errors.append(
            "HTTP attempt rows exist although the first-heartbeat target audits declare no attempts"
        )

    target_query = query_entries["collector.tiered50_first_heartbeat_target_audits"].get("query")
    http_query = query_entries["collector.tiered50_first_heartbeat_http_attempts"].get("query")
    target_query_pass = (
        isinstance(target_query, Mapping)
        and str(target_query.get("status") or "").upper() == "PASS"
    )
    http_query_pass = (
        isinstance(http_query, Mapping)
        and str(http_query.get("status") or "").upper() == "PASS"
    )
    target_base_pass = (
        target_query_pass
        and bool(linked_target_rows)
        and linked_target_audits.get("linked_to_first_heartbeat_run") is True
    )
    http_base_pass = (
        http_query_pass
        and linked_http_attempts.get("linked_to_first_heartbeat_run") is True
        and linked_http_attempts.get("linked_to_target_audit") is True
    )
    if not target_query_pass:
        target_errors.append(
            str((target_query or {}).get("reason") or "first-heartbeat target-audit query is not proven")
        )
    if not http_query_pass:
        http_errors.append(
            str((http_query or {}).get("reason") or "first-heartbeat HTTP-attempt query is not proven")
        )
    if not http_base_pass:
        http_errors.append("first-heartbeat HTTP attempts are not linked to the authoritative target-audit query")
    if not target_base_pass:
        http_errors.append("first-heartbeat HTTP attempts cannot be proven while target-audit history is incomplete")

    target_status = "PASS" if target_base_pass and not target_errors else "NOT_PROVEN"
    http_status = "PASS" if http_base_pass and target_status == "PASS" and not http_errors else "NOT_PROVEN"
    target_reason = "; ".join(dict.fromkeys(target_errors)) if target_errors else None
    http_reason = "; ".join(dict.fromkeys(http_errors)) if http_errors else None
    reconciliation = {
        "status": "PASS" if target_status == "PASS" and http_status == "PASS" else "NOT_PROVEN",
        "reason": "; ".join(value for value in (target_reason, http_reason) if value) or None,
        "run_id": first_heartbeat_run_id,
        "target_audit_count": len(linked_target_rows),
        "declared_http_attempt_count": declared_total,
        "actual_http_attempt_count": actual_total,
        "http_backed_successful_target_count": sum(
            1
            for audit_id, row in target_audit_rows_by_id.items()
            if str(row.get("status") or "").strip().upper() == "SUCCEEDED"
            and declared_attempts_by_audit.get(audit_id, 0) > 0
        ),
        "skipped_target_count": sum(
            1
            for row in linked_target_rows
            if isinstance(row, Mapping)
            and str(row.get("status") or "").strip().upper() == "SKIPPED"
        ),
        "duplicate_target_audit_ids": sorted(duplicate_target_audit_ids),
        "duplicate_http_attempt_ids": sorted(duplicate_http_attempt_ids),
        "duplicate_http_attempt_keys": [
            {"audit_id": audit_id, "attempt_number": attempt}
            for audit_id, attempt in sorted(duplicate_http_attempt_keys)
        ],
        "orphan_http_attempt_ids": sorted(set(orphan_http_attempt_ids)),
        "target_check_status": target_status,
        "target_check_reason": target_reason,
        "http_check_status": http_status,
        "http_check_reason": http_reason,
    }

    expected_first_heartbeat_statuses = {
        "collector.tiered50_first_heartbeat_target_audits": target_status,
        "collector.tiered50_first_heartbeat_http_attempts": http_status,
    }
    for query_id, expected_check_status in expected_first_heartbeat_statuses.items():
        entry = query_entries[query_id]
        check_status = str(entry.get("check_status") or "").upper()
        if check_status != expected_check_status:
            raise AdminEvidenceError(
                f"Windows audit query {query_id} has an inaccurate check status; "
                f"expected {expected_check_status} ({reconciliation.get('reason') or 'relationship validation failed'})"
            )
        if expected_check_status == "NOT_PROVEN" and not str(entry.get("reason") or "").strip():
            # A failed relationship must be visible in the captured artifact;
            # do not let a bare status hide which historical link was missing.
            raise AdminEvidenceError(
                f"Windows audit query {query_id} is not fully proven and has no accurate reason: "
                f"{reconciliation.get('reason') or 'historical relationship validation failed'}"
            )
        results[query_id]["check_status"] = check_status
        if expected_check_status == "NOT_PROVEN":
            results[query_id]["reason"] = str(entry.get("reason") or reconciliation.get("reason"))

    if semantic_reasons.get("collector.http_attempt_history"):
        raise AdminEvidenceError(
            "Windows administrative audit evidence is not fully proven: "
            + semantic_reasons["collector.http_attempt_history"]
        )
    if any(result["check_status"] != "PASS" for result in results.values()):
        details = [
            f"{query_id}: {query_entries[query_id].get('reason') or query_entries[query_id].get('query', {}).get('reason', 'semantic check is NOT_PROVEN')}"
            for query_id, result in results.items()
            if result["check_status"] != "PASS"
        ]
        raise AdminEvidenceError(
            "Windows administrative audit evidence is not fully proven"
            + (": " + "; ".join(details) if details else "")
        )
    if str((sql.get("admin_authorization") or {}).get("status") or "").upper() != "PASS":
        raise AdminEvidenceError("Windows administrative audit evidence is not fully proven")
    if str((sql.get("tiered50_audit_history_evidence") or {}).get("status") or "").upper() != "PASS":
        raise AdminEvidenceError("Windows target-audit history query is not proven")
    if str((sql.get("recent_http_attempts_evidence") or {}).get("status") or "").upper() != "PASS":
        raise AdminEvidenceError("Windows HTTP attempt history query is not proven")
    return {"status": "PASS", "queries": results, "first_heartbeat_reconciliation": reconciliation}


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
    if provenance.get("execution_path") == "WINDOWS_INTEGRATED_AUTH":
        missing_windows_queries = sorted(WINDOWS_AUDIT_QUERY_IDS - required)
        if missing_windows_queries:
            raise AdminEvidenceError(
                "Windows administrative evidence omits audit queries: " + ", ".join(missing_windows_queries)
            )

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
        validate_windows_audit_evidence(sql)
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
    "WINDOWS_AUDIT_QUERY_IDS",
    "WINDOWS_ENCRYPTION_MODES",
    "WINDOWS_MIN_FREE_PERCENT",
    "WINDOWS_MIN_FREE_RESERVE_BYTES",
    "WINDOWS_TLS_VALIDATION_METHOD",
    "canonical_payload",
    "json_safe",
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
    "validate_windows_audit_evidence",
    "validate_windows_vm_disk_evidence",
    "utc_isoformat",
    "verify_signature",
]
