"""Lossless compact-observation envelope primitives.

The SQL Server compact storage migration stores one immutable envelope per
source snapshot.  This module is deliberately independent of a database
client so that the serialization contract, hashes, and row-parity checks can
be exercised in bounded offline tests.

The envelope is a fallback representation (Option B in S143): it retains the
complete normalized logical rows when the source snapshot cannot prove exact
reconstruction of identities, derived metadata, NULL/empty distinctions, or
historical multiplicity.
"""

from __future__ import annotations

import collections
import datetime as _datetime
import decimal
import gzip
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import zlib
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


COMPACT_STORAGE_FORMAT_VERSION = 1
COMPACT_ENVELOPE_TYPE = "COLOGNE_TRANSIT_COMPACT_OBSERVATION_ENVELOPE"
COMPACT_SERIALIZATION = "CTI_CANONICAL_JSON_UTF16LE_V1"
COMPACT_HASH_ALGORITHM = "SHA2_256_UTF16LE"

FAMILY_NAMES = (
    "source_snapshot",
    "calls",
    "timings",
    "situations",
    "service_sections",
    "attributes",
    "response_errors",
    "parser_gaps",
)

# The row families passed by the existing persistence adapter.  Keeping the
# names here makes the compact procedure and the offline serializer share a
# visible, versioned contract without changing parser behavior.
ROW_SET_FAMILY_NAMES = (
    "calls",
    "timings",
    "situations",
    "service_sections",
    "attributes",
    "response_errors",
    "parser_gaps",
)

COLLECTOR_LAUNCHD_LABELS = (
    "com.cologne-transit-intelligence.mdd-collector",
    "com.cologne-transit-intelligence.mdd-tiered50-collector",
)

# These markers are intentionally narrow.  The safety gate must detect a
# collector that is still running outside launchd, without echoing complete
# command lines (which could contain credentials or payloads).
COLLECTOR_PROCESS_MARKERS = (
    "stage_14_collector.py",
    "cologne_transit_collector.cli",
    "com.cologne-transit-intelligence.mdd-collector",
    "com.cologne-transit-intelligence.mdd-tiered50-collector",
)

STORAGE_OPERATION_PROCESS_MARKERS = (
    "apply_compact_storage_migration.py",
    "migrate_historical_compact_observations.py",
    "verify_current_sql_backup.py",
    "reclaim_sql_datafile_space.py",
)


class CompactEnvelopeError(ValueError):
    """Raised when a compact envelope is malformed or fails integrity checks."""


def _process_gate(markers: Sequence[str], *, exclude_pid: int | None = None) -> dict[str, Any]:
    """Return a secret-free process gate for the local safety checks."""

    try:
        completed = subprocess.run(
            ["/bin/ps", "-axo", "pid=,command="],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        return {"status": "NOT_PROVEN", "reason": f"process listing unavailable: {type(exc).__name__}"}

    ignored = {os.getpid()}
    if exclude_pid is not None:
        ignored.add(int(exclude_pid))
    active_pids: list[int] = []
    for line in completed.stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        parts = text.split(None, 1)
        try:
            pid = int(parts[0])
        except (IndexError, ValueError):
            continue
        if pid in ignored:
            continue
        command = parts[1] if len(parts) > 1 else ""
        executable = command.split(None, 1)[0].rsplit("/", 1)[-1].casefold() if command else ""
        # A shell command used to launch this check can contain the script
        # name in its arguments. Only inspect the actual interpreter/process,
        # while the child Python process remains visible to the gate.
        if executable in {"sh", "bash", "zsh", "-zsh", "fish", "dash", "sudo"}:
            continue
        if any(marker in command for marker in markers):
            active_pids.append(pid)
    return {
        "status": "PASS" if not active_pids else "BLOCKED",
        "active_process_count": len(active_pids),
        "active_process_pids": sorted(active_pids),
    }


def local_collector_safety_gate() -> dict[str, Any]:
    """Verify launchd state and unmanaged collector processes read-only."""

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
        disabled = all(
            f'"{label}" => disabled' in disabled_text
            for label in COLLECTOR_LAUNCHD_LABELS
        )
        loaded_exit_codes = {
            label: subprocess.run(
                ["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"],
                capture_output=True,
                text=True,
                check=False,
            ).returncode
            for label in COLLECTOR_LAUNCHD_LABELS
        }
        unloaded = all(code != 0 for code in loaded_exit_codes.values())
        process_gate = _process_gate(COLLECTOR_PROCESS_MARKERS)
        passed = disabled and unloaded and process_gate.get("status") == "PASS"
        return {
            "status": "PASS" if passed else "NOT_PROVEN",
            "disabled": disabled,
            "loaded_exit_codes": loaded_exit_codes,
            "process_gate": process_gate,
        }
    except OSError as exc:
        return {"status": "NOT_PROVEN", "reason": str(exc)[:300]}


def local_storage_operation_gate() -> dict[str, Any]:
    """Detect another compact-schema or historical-migration process."""

    process_gate = _process_gate(STORAGE_OPERATION_PROCESS_MARKERS)
    return {
        "status": "PASS" if process_gate.get("status") == "PASS" else "NOT_PROVEN",
        **process_gate,
    }


def _reject_non_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise CompactEnvelopeError("NaN and infinity are not valid envelope values")


def _canonical_json(value: Any) -> str:
    """Serialize with the repository's stable logical-envelope contract."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CompactEnvelopeError(f"value is not canonically serializable: {exc}") from exc


def _json_safe(value: Any) -> Any:
    """Convert common DB/API scalar types without changing logical text."""

    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        _reject_non_finite(value)
        return value
    if isinstance(value, decimal.Decimal):
        # Decimal values are not part of the current observation rowset, but
        # retaining their lexical value is safer than silently rounding them.
        return format(value, "f")
    if isinstance(value, _datetime.datetime):
        return value.isoformat()
    if isinstance(value, _datetime.date):
        return value.isoformat()
    if isinstance(value, _datetime.time):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__binary_hex__": bytes(value).hex()}
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    raise CompactEnvelopeError(
        "unsupported envelope scalar type: " + type(value).__name__
    )


def _copy_row(row: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise CompactEnvelopeError("every logical observation row must be an object")
    return {str(key): _json_safe(value) for key, value in row.items()}


def build_compact_envelope(
    *,
    source_snapshot: Mapping[str, Any],
    row_sets: Mapping[str, Sequence[Mapping[str, Any]]],
    storage_format_version: int = COMPACT_STORAGE_FORMAT_VERSION,
) -> dict[str, Any]:
    """Build the logical envelope used by future compact writes.

    ``source_snapshot`` is the complete logical snapshot metadata plus the
    original source JSON under ``snapshot_json``.  Row IDs are intentionally
    not fabricated here: future SQL writes allocate stable IDs in a durable
    per-family identity range, while historical SQL migration envelopes carry
    the original identity values verbatim.
    """

    if (
        isinstance(storage_format_version, bool)
        or not isinstance(storage_format_version, int)
        or storage_format_version != COMPACT_STORAGE_FORMAT_VERSION
    ):
        raise CompactEnvelopeError("unsupported compact storage format version")
    missing = [name for name in ROW_SET_FAMILY_NAMES if name not in row_sets]
    if missing:
        raise CompactEnvelopeError("row-set families are missing: " + ", ".join(missing))
    envelope = {
        "envelope_type": COMPACT_ENVELOPE_TYPE,
        "storage_format_version": COMPACT_STORAGE_FORMAT_VERSION,
        "serialization": COMPACT_SERIALIZATION,
        "hash_algorithm": COMPACT_HASH_ALGORITHM,
        "source_snapshot": _copy_row(source_snapshot),
    }
    for family in ROW_SET_FAMILY_NAMES:
        values = row_sets[family]
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
            raise CompactEnvelopeError(f"row-set {family} must be a sequence")
        envelope[family] = [_copy_row(row) for row in values]
    # Force validation before the caller persists the object.  This catches
    # non-finite numbers, unsupported values, and incomplete envelopes before
    # a SQL transaction.
    _validate_envelope_structure(envelope)
    _canonical_json(envelope)
    return envelope


@dataclass(frozen=True)
class EncodedCompactEnvelope:
    """Canonical text, integrity metadata, and deterministic offline bytes."""

    envelope: Mapping[str, Any]
    canonical_json: str
    utf16le_bytes: bytes
    gzip_bytes: bytes
    sha256: str

    @property
    def logical_bytes(self) -> int:
        return len(self.utf16le_bytes)

    @property
    def compressed_bytes(self) -> int:
        return len(self.gzip_bytes)


def encode_compact_envelope(envelope: Mapping[str, Any]) -> EncodedCompactEnvelope:
    safe_envelope = _json_safe(envelope)
    _validate_envelope_structure(safe_envelope)
    canonical = _canonical_json(safe_envelope)
    raw = canonical.encode("utf-16le")
    packed = gzip.compress(raw, mtime=0)
    return EncodedCompactEnvelope(
        envelope=envelope,
        canonical_json=canonical,
        utf16le_bytes=raw,
        gzip_bytes=packed,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def nested_gzip_hex_benchmark(value: str) -> dict[str, int | float | bool]:
    """Measure the representation cost of nesting an existing GZIP as hex.

    This is deliberately a logical-byte benchmark, not SQL Server allocation
    evidence.  It compares the outer GZIP size of a normal text field with an
    outer GZIP containing the inner GZIP bytes serialized as hexadecimal.  A
    caller must still measure row/page/LOB allocation on the target SQL Server.
    """

    if not isinstance(value, str):
        raise TypeError("nested GZIP benchmark input must be text")
    inner_gzip = gzip.compress(value.encode("utf-16le"), mtime=0)
    plain_wrapper = _canonical_json({"value": value}).encode("utf-16le")
    nested_wrapper = _canonical_json({"value_gzip_hex": inner_gzip.hex()}).encode("utf-16le")
    plain_outer = gzip.compress(plain_wrapper, mtime=0)
    nested_outer = gzip.compress(nested_wrapper, mtime=0)
    return {
        "plain_wrapper_utf16le_bytes": len(plain_wrapper),
        "inner_gzip_bytes": len(inner_gzip),
        "nested_hex_wrapper_utf16le_bytes": len(nested_wrapper),
        "plain_outer_gzip_bytes": len(plain_outer),
        "nested_outer_gzip_bytes": len(nested_outer),
        "nested_overhead_bytes": len(nested_outer) - len(plain_outer),
        "nested_representation_is_smaller": len(nested_outer) < len(plain_outer),
    }


def decode_compact_envelope(
    compressed_bytes: bytes,
    *,
    expected_sha256: str | None = None,
    max_logical_bytes: int = 512 * 1024 * 1024,
) -> dict[str, Any]:
    """Decode and verify one envelope without returning raw SQL payloads."""

    try:
        raw = gzip.decompress(bytes(compressed_bytes))
    except (OSError, EOFError, TypeError, ValueError, zlib.error) as exc:
        raise CompactEnvelopeError("compact envelope is not valid GZIP") from exc
    if len(raw) > max_logical_bytes:
        raise CompactEnvelopeError("compact envelope exceeds the bounded readback limit")
    actual = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and actual.casefold() != str(expected_sha256).casefold():
        raise CompactEnvelopeError("compact envelope SHA-256 does not match metadata")
    try:
        value = json.loads(raw.decode("utf-16le"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise CompactEnvelopeError("compact envelope JSON is invalid") from exc
    _validate_envelope_structure(value)
    return value


def _validate_envelope_structure(value: Any) -> None:
    """Validate the complete versioned envelope shape before persistence/readback."""

    if not isinstance(value, dict):
        raise CompactEnvelopeError("compact envelope root must be an object")
    if value.get("envelope_type") != COMPACT_ENVELOPE_TYPE:
        raise CompactEnvelopeError("compact envelope type is not recognized")
    version = value.get("storage_format_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != COMPACT_STORAGE_FORMAT_VERSION:
        raise CompactEnvelopeError("compact envelope version is not recognized")
    if value.get("serialization") != COMPACT_SERIALIZATION:
        raise CompactEnvelopeError("compact envelope serialization is not recognized")
    if value.get("hash_algorithm") != COMPACT_HASH_ALGORITHM:
        raise CompactEnvelopeError("compact envelope hash algorithm is not recognized")
    missing = [name for name in FAMILY_NAMES if name not in value]
    if missing:
        raise CompactEnvelopeError("compact envelope families are missing: " + ", ".join(missing))
    if not isinstance(value["source_snapshot"], dict):
        raise CompactEnvelopeError("source_snapshot must be an object")
    for family in ROW_SET_FAMILY_NAMES:
        if not isinstance(value[family], list) or any(not isinstance(row, dict) for row in value[family]):
            raise CompactEnvelopeError(f"compact envelope family {family} is not an object array")


def canonical_row(row: Mapping[str, Any]) -> str:
    """Canonicalize one row while retaining NULL and empty values distinctly."""

    return _canonical_json(_json_safe(row))


def row_multiset_digest(rows: Iterable[Mapping[str, Any]]) -> str:
    """Hash a row multiset, preserving duplicate observations."""

    encoded = sorted(canonical_row(row) for row in rows)
    payload = ("\n".join(encoded)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def row_sequence_digest(rows: Iterable[Mapping[str, Any]]) -> str:
    """Hash an ordered row sequence for arrays where ordinal is meaningful."""

    payload = ("\n".join(canonical_row(row) for row in rows)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class RowParityResult:
    family: str
    expected_count: int
    actual_count: int
    expected_digest: str
    actual_digest: str
    missing_rows: tuple[str, ...] = ()
    unexpected_rows: tuple[str, ...] = ()
    changed_rows: tuple[Mapping[str, Any], ...] = ()

    @property
    def passed(self) -> bool:
        return (
            self.expected_count == self.actual_count
            and self.expected_digest == self.actual_digest
            and not self.missing_rows
            and not self.unexpected_rows
            and not self.changed_rows
        )


def _row_identity(row: Mapping[str, Any], ordinal: int) -> str:
    for key in (
        "timing_observation_id",
        "call_observation_id",
        "situation_evidence_id",
        "service_section_evidence_id",
        "attribute_evidence_id",
        "response_error_id",
        "parser_gap_id",
        "snapshot_id",
    ):
        if key in row and row[key] is not None:
            return f"{key}={row[key]}"
    return f"ordinal={ordinal}"


def compare_row_family(
    family: str,
    expected_rows: Sequence[Mapping[str, Any]],
    actual_rows: Sequence[Mapping[str, Any]],
    *,
    max_differences: int = 20,
) -> RowParityResult:
    """Compare counts, multiplicity, IDs, and every field in a bounded unit."""

    expected = [dict(row) for row in expected_rows]
    actual = [dict(row) for row in actual_rows]
    expected_by_id: dict[str, list[str]] = collections.defaultdict(list)
    actual_by_id: dict[str, list[str]] = collections.defaultdict(list)
    for index, row in enumerate(expected):
        expected_by_id[_row_identity(row, index)].append(canonical_row(row))
    for index, row in enumerate(actual):
        actual_by_id[_row_identity(row, index)].append(canonical_row(row))

    missing: list[str] = []
    unexpected: list[str] = []
    changed: list[Mapping[str, Any]] = []
    for identity in sorted(set(expected_by_id) | set(actual_by_id)):
        left = collections.Counter(expected_by_id.get(identity, ()))
        right = collections.Counter(actual_by_id.get(identity, ()))
        if not left:
            unexpected.extend([identity] * sum(right.values()))
            continue
        if not right:
            missing.extend([identity] * sum(left.values()))
            continue
        if left != right:
            # A row with the same stable ID but a different field is reported
            # as changed, not hidden behind an aggregate count mismatch.
            changed.append(
                {
                    "identity": identity,
                    "expected": list(left.elements())[:max_differences],
                    "actual": list(right.elements())[:max_differences],
                }
            )
    return RowParityResult(
        family=family,
        expected_count=len(expected),
        actual_count=len(actual),
        expected_digest=row_multiset_digest(expected),
        actual_digest=row_multiset_digest(actual),
        missing_rows=tuple(missing[:max_differences]),
        unexpected_rows=tuple(unexpected[:max_differences]),
        changed_rows=tuple(changed[:max_differences]),
    )


_CAMEL_BOUNDARY_RE = re.compile(r"(?<!^)(?=[A-Z])")


def sql_column_to_envelope_key(column_name: str) -> str:
    """Map a deployed SQL column name to the lower-snake envelope key."""

    name = str(column_name)
    if name.endswith("Gzip"):
        name = name[:-4] + "GzipHex"
    return _CAMEL_BOUNDARY_RE.sub("_", name).lower()


_SQL_ROW_KEY_ALIASES: dict[str, dict[str, str]] = {
    "timings": {
        "timetabled_time_source_text": "timetabled_time",
        "estimated_time_source_text": "estimated_time",
        "estimated_time_low_source_text": "estimated_time_low",
        "estimated_time_high_source_text": "estimated_time_high",
        "recorded_at_time_source_text": "recorded_at_time",
        "timing_source_path": "source_path",
    },
    "situations": {
        "provider_source": "source",
        "provider_source_type": "source_type",
    },
    "service_sections": {
        "published_line_name": "published_line_name_text",
        "mode_source_json": "mode_source_representation",
    },
    "response_errors": {
        "error_value": "value",
    },
}


def normalize_sql_row(row: Mapping[str, Any], *, family: str | None = None) -> dict[str, Any]:
    """Normalize a DB-API row for exact comparison with a SQL envelope."""

    result: dict[str, Any] = {}
    for column, value in row.items():
        key = sql_column_to_envelope_key(str(column))
        if family is not None:
            key = _SQL_ROW_KEY_ALIASES.get(family, {}).get(key, key)
        if isinstance(value, memoryview):
            value = value.tobytes()
        if isinstance(value, (bytes, bytearray)):
            result[key] = bytes(value).hex()
        elif isinstance(value, _datetime.datetime):
            # SQL Server FOR JSON emits seven fractional digits for datetime2;
            # DB-API commonly exposes six.  Normalize to the exact JSON form.
            if value.tzinfo is None:
                result[key] = value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond:06d}0"
            else:
                result[key] = value.isoformat()
        elif isinstance(value, decimal.Decimal):
            result[key] = format(value, "f")
        else:
            result[key] = _json_safe(value)
    return result


def capacity_projection(
    *,
    requests_per_hour: int,
    hours: int,
    bytes_per_snapshot: int,
    weekly_budget_gib: float,
    windows_free_gib: float,
    windows_reserve_gib: float,
    operational_floor_gib: float = 12.0,
) -> dict[str, float | int | bool]:
    """Calculate bounded storage projections without making policy changes."""

    integer_inputs = {
        "requests_per_hour": requests_per_hour,
        "hours": hours,
        "bytes_per_snapshot": bytes_per_snapshot,
    }
    for name, value in integer_inputs.items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an integer")
    if requests_per_hour <= 0 or hours <= 0:
        raise ValueError("requests_per_hour and hours must be positive")
    if bytes_per_snapshot < 0:
        raise ValueError("bytes_per_snapshot cannot be negative")
    numeric_inputs = {
        "weekly_budget_gib": weekly_budget_gib,
        "windows_free_gib": windows_free_gib,
        "windows_reserve_gib": windows_reserve_gib,
        "operational_floor_gib": operational_floor_gib,
    }
    if any(not isinstance(value, (int, float)) or not math.isfinite(float(value)) for value in numeric_inputs.values()):
        raise ValueError("capacity thresholds must be finite numbers")
    if any(float(value) < 0 for value in numeric_inputs.values()):
        raise ValueError("capacity thresholds cannot be negative")
    requests = requests_per_hour * hours
    growth_bytes = requests * bytes_per_snapshot
    gib = 1024**3
    growth_gib = growth_bytes / gib
    budget_bytes = float(weekly_budget_gib) * gib
    per_request_budget = budget_bytes / max(requests, 1)
    return {
        "requests": requests,
        "growth_bytes": growth_bytes,
        "growth_gib": growth_gib,
        "bytes_per_request": bytes_per_snapshot,
        "per_request_budget_bytes": per_request_budget,
        "weekly_budget_gib": float(weekly_budget_gib),
        "windows_free_after_gib": float(windows_free_gib) - growth_gib,
        "windows_reserve_gib": float(windows_reserve_gib),
        "operational_floor_gib": float(operational_floor_gib),
        "weekly_budget_pass": growth_gib <= float(weekly_budget_gib),
        "windows_reserve_pass": float(windows_free_gib) - growth_gib >= float(windows_reserve_gib),
        "operational_floor_pass": float(windows_free_gib) - growth_gib >= float(operational_floor_gib),
    }

