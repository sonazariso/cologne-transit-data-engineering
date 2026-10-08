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
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping


EVIDENCE_ARTIFACT_VERSION = "TIERED50_ADMIN_EVIDENCE_V3"
SIGNATURE_ALGORITHM = "OPENSSL_DIGEST_SHA256"
ADMIN_EVIDENCE_MAX_AGE = timedelta(minutes=30)
ADMIN_EVIDENCE_CLOCK_SKEW = timedelta(seconds=5)
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
    }
)


class AdminEvidenceError(ValueError):
    """The administrative evidence envelope is not trustworthy or fresh."""


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


def validate_admin_evidence_artifact(
    evidence: Mapping[str, Any],
    *,
    now_utc: datetime,
    expected_database: str,
    public_key_path: str | Path | None,
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
    if provenance.get("execution_path") not in {"SEPARATE_SQL_LOGIN", "WINDOWS_INTEGRATED_AUTH"}:
        raise AdminEvidenceError("administrative evidence has no authorized execution path")
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
    return {
        "status": "PASS",
        "generated_at_utc": generated_at.isoformat().replace("+00:00", "Z"),
        "server_name": str(provenance["server_name"]),
        "database_name": str(provenance["database_name"]),
        "effective_login": str(provenance["effective_login"]),
        "execution_path": str(provenance["execution_path"]),
        "signature_verified": True,
        "required_checks": sorted(required),
    }


__all__ = [
    "ADMIN_EVIDENCE_CLOCK_SKEW",
    "ADMIN_EVIDENCE_MAX_AGE",
    "AdminEvidenceError",
    "EVIDENCE_ARTIFACT_VERSION",
    "REQUIRED_CHECK_IDS",
    "canonical_payload",
    "payload_sha256",
    "sign_payload",
    "validate_admin_evidence_artifact",
    "verify_signature",
]
