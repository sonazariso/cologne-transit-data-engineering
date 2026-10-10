from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cologne_transit_collector.admin_evidence import (  # noqa: E402
    AdminEvidenceError,
    EVIDENCE_ARTIFACT_VERSION,
    REQUIRED_CHECK_IDS,
    json_safe,
    load_current_evidence_artifact,
    require_current_evidence_path,
    resolve_current_evidence_path,
    sign_payload,
    validate_admin_evidence_artifact,
    validate_windows_audit_evidence,
    utc_isoformat,
    verify_signature,
    WINDOWS_AUDIT_QUERY_IDS,
    WINDOWS_TLS_VALIDATION_METHOD,
)
from cologne_transit_collector.mssql_persistence import MssqlConnectionSettings  # noqa: E402
from provision_macos_runtime_login import (  # noqa: E402
    PROHIBITED_RUNTIME_PERMISSION_CHECKS,
    PROHIBITED_RUNTIME_ROLE_CHECKS,
    RUNTIME_PERMISSION_MATRIX,
    inspect_runtime_permissions,
)


NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


class CurrentEvidencePathTests(unittest.TestCase):
    def _repository(self, directory: str) -> Path:
        repository = Path(directory) / "repository"
        (repository / "docs" / "operational").mkdir(parents=True, mode=0o700)
        return repository

    def _keypair(self, directory: str) -> tuple[Path, Path]:
        private = Path(directory) / "operator-private.pem"
        public = Path(directory) / "operator-public.pem"
        subprocess.run(
            ["/usr/bin/openssl", "genrsa", "-out", str(private), "2048"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        private.chmod(0o600)
        subprocess.run(
            ["/usr/bin/openssl", "rsa", "-in", str(private), "-pubout", "-out", str(public)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return private, public

    def _evidence(self, generated_at: datetime = NOW, server_name: str = "DataAnalyst-VM") -> dict[str, object]:
        identity = {
            "server_name": server_name,
            "machine_name": "DataAnalyst-VM",
            "instance_name": "MSSQLSERVER",
            "database_name": "CologneTransitIntelligence",
        }
        heartbeat_run_id = "tiered50-first-run"
        heartbeat_target_audit_rows = [
            {"audit_id": 101, "run_id": heartbeat_run_id, "target_identity": "target-1"},
            {"audit_id": 102, "run_id": heartbeat_run_id, "target_identity": "target-2"},
        ]
        heartbeat_http_attempt_rows = [
            {
                "http_attempt_id": 201,
                "audit_id": 101,
                "run_id": heartbeat_run_id,
                "target_identity": "target-1",
                "http_status": 200,
                "outcome": "SUCCEEDED",
            },
            {
                "http_attempt_id": 202,
                "audit_id": 102,
                "run_id": heartbeat_run_id,
                "target_identity": "target-2",
                "http_status": 200,
                "outcome": "SUCCEEDED",
            },
        ]
        rows_by_query = {
            "collector.historical_runs": [{"run_id": "legacy-run"}],
            "collector.historical_target_audits": heartbeat_target_audit_rows,
            "collector.http_attempt_history": heartbeat_http_attempt_rows,
            "collector.tiered50_first_heartbeat": [{"run_id": heartbeat_run_id}],
            "collector.tiered50_first_heartbeat_target_audits": heartbeat_target_audit_rows,
            "collector.tiered50_first_heartbeat_http_attempts": heartbeat_http_attempt_rows,
            "collector.historical_outage": [{"cutover_id": 1}],
            "collector.tiered50_later_runs": [],
        }
        audit_provenance = {}
        for query_id in WINDOWS_AUDIT_QUERY_IDS:
            rows = rows_by_query[query_id]
            entry = {
                "query": {
                    "statement_id": query_id,
                    "status": "PASS",
                    "executed": True,
                    "result_count": len(rows),
                    "captured_at_utc": generated_at.isoformat().replace("+00:00", "Z"),
                },
                "check_status": "PASS",
                "rows": rows,
            }
            if query_id == "collector.http_attempt_history":
                entry["linked_to_target_audit"] = True
            if query_id in {
                "collector.tiered50_first_heartbeat_target_audits",
                "collector.tiered50_first_heartbeat_http_attempts",
            }:
                entry["linked_to_first_heartbeat_run"] = True
                entry["run_id"] = heartbeat_run_id
            if query_id == "collector.tiered50_first_heartbeat_http_attempts":
                entry["linked_to_target_audit"] = True
            audit_provenance[query_id] = entry
        return {
            "artifact_version": EVIDENCE_ARTIFACT_VERSION,
            "generated_at_utc": generated_at.isoformat().replace("+00:00", "Z"),
            "capture_provenance": {
                "status": "PASS",
                "read_only": True,
                "execution_path": "WINDOWS_INTEGRATED_AUTH",
                "server_name": server_name,
                "sql_instance_identity": identity,
                "database_name": "CologneTransitIntelligence",
                "effective_login": "operator-admin",
                "captured_at_utc": generated_at.isoformat().replace("+00:00", "Z"),
                "required_check_ids": sorted(REQUIRED_CHECK_IDS | WINDOWS_AUDIT_QUERY_IDS),
                "tls": {
                    "encryption_mode": "Mandatory",
                    "actual_encryption": "TRUE",
                    "actual_transport": "ENCRYPTED",
                    "trust_server_certificate": False,
                    "certificate_validation_required": True,
                    "certificate_validation_observed": True,
                    "certificate_validation_status": "VALIDATED",
                    "certificate_validation_method": WINDOWS_TLS_VALIDATION_METHOD,
                },
            },
            "sql": {
                "admin_authorization": {"status": "PASS"},
                "runtime_health": {"status": "PASS"},
                "backup_gate": {"status": "PASS"},
                "capacity_gate": {"status": "PASS"},
                "log_space_gate": {"status": "PASS"},
                "quota_integrity": {"status": "PASS", "state_available": True},
                "tiered50_audit_history_evidence": {"status": "PASS"},
                "recent_http_attempts_evidence": {"status": "PASS"},
                "audit_provenance": audit_provenance,
                "windows_vm_disk": {
                    "status": "PASS",
                    "volumes": [
                        {
                            "drive": "C:",
                            "mount_point": "C:\\",
                            "total_bytes": 100 * 1024 * 1024 * 1024,
                            "free_bytes": 20 * 1024 * 1024 * 1024,
                            "free_percent": 20.0,
                        }
                    ],
                    "sql_volume_headroom": [
                        {
                            "volume": "C:\\",
                            "total_bytes": 100 * 1024 * 1024 * 1024,
                            "available_bytes": 20 * 1024 * 1024 * 1024,
                            "available_percent": 20.0,
                            "minimum_required_free_bytes": 1024 * 1024 * 1024,
                            "headroom_bytes": 19 * 1024 * 1024 * 1024,
                            "headroom_status": "PASS",
                            "database_file_types": ["LOG", "ROWS"],
                        }
                    ],
                },
            },
        }

    def test_one_stable_path_rejects_daily_and_conflicting_locations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = self._repository(directory)
            stable = repository / "docs" / "operational" / "tiered50-production-evidence-current.json"
            self.assertEqual(resolve_current_evidence_path(repository_root=repository), stable.resolve())
            self.assertEqual(require_current_evidence_path(stable, repository_root=repository), stable.resolve())
            for conflicting in (
                repository / "docs" / "operational" / "tiered50-production-evidence-current-20261008.json",
                repository / "docs" / "operational" / "tiered50-production-evidence-current-20261009.json",
                Path(directory) / "arbitrary-current.json",
            ):
                with self.subTest(conflicting=conflicting), self.assertRaisesRegex(
                    AdminEvidenceError, "stable|conflicting|absolute|approved"
                ):
                    require_current_evidence_path(conflicting, repository_root=repository)

    def test_missing_unsigned_stale_and_wrong_instance_artifacts_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as repository_directory, tempfile.TemporaryDirectory() as key_directory:
            repository = self._repository(repository_directory)
            stable = resolve_current_evidence_path(repository_root=repository)
            private, public = self._keypair(key_directory)
            with patch.dict(os.environ, {"MSSQL_ADMIN_EVIDENCE_PATH": ""}):
                with self.assertRaisesRegex(AdminEvidenceError, "missing or unreadable"):
                    load_current_evidence_artifact(
                        stable,
                        repository_root=repository,
                        now_utc=NOW,
                        expected_database="CologneTransitIntelligence",
                        public_key_path=public,
                    )

                signed_current = self._evidence()
                signed_current["artifact_integrity"] = sign_payload(signed_current, signing_key_path=private)
                stable.write_text(json.dumps(signed_current), encoding="utf-8")
                stable.chmod(0o600)
                loaded, authenticated, loaded_path = load_current_evidence_artifact(
                    stable,
                    repository_root=repository,
                    now_utc=NOW,
                    expected_database="CologneTransitIntelligence",
                    public_key_path=public,
                )
                self.assertEqual(loaded_path, stable)
                self.assertEqual(authenticated["status"], "PASS")
                self.assertEqual(loaded["artifact_integrity"]["status"], "SIGNED")  # type: ignore[index]

                unsigned_old = self._evidence(NOW - timedelta(minutes=1))
                stable.write_text(json.dumps(unsigned_old), encoding="utf-8")
                with self.assertRaisesRegex(AdminEvidenceError, "cryptographically signed"):
                    load_current_evidence_artifact(
                        stable,
                        repository_root=repository,
                        now_utc=NOW,
                        expected_database="CologneTransitIntelligence",
                        public_key_path=public,
                    )

                stale = self._evidence(NOW - timedelta(minutes=31))
                stale["artifact_integrity"] = sign_payload(stale, signing_key_path=private)
                stable.write_text(json.dumps(stale), encoding="utf-8")
                with self.assertRaisesRegex(AdminEvidenceError, "stale"):
                    load_current_evidence_artifact(
                        stable,
                        repository_root=repository,
                        now_utc=NOW,
                        expected_database="CologneTransitIntelligence",
                        public_key_path=public,
                    )

                wrong_instance = self._evidence(server_name="Other-SQL-VM")
                wrong_instance["artifact_integrity"] = sign_payload(wrong_instance, signing_key_path=private)
                stable.write_text(json.dumps(wrong_instance), encoding="utf-8")
                with self.assertRaisesRegex(AdminEvidenceError, "different SQL instance"):
                    load_current_evidence_artifact(
                        stable,
                        repository_root=repository,
                        now_utc=NOW,
                        expected_database="CologneTransitIntelligence",
                        public_key_path=public,
                        expected_sql_identity={
                            "server_name": "DataAnalyst-VM",
                            "machine_name": "DataAnalyst-VM",
                            "instance_name": "MSSQLSERVER",
                            "database_name": "CologneTransitIntelligence",
                        },
                    )

    def test_bom_free_and_windows_powershell_bom_json_are_both_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as repository_directory, tempfile.TemporaryDirectory() as key_directory:
            repository = self._repository(repository_directory)
            stable = resolve_current_evidence_path(repository_root=repository)
            private, public = self._keypair(key_directory)
            evidence = self._evidence()
            evidence["artifact_integrity"] = sign_payload(evidence, signing_key_path=private)
            serialized = json.dumps(evidence).encode("utf-8")
            for label, raw in (("powershell-7", serialized), ("windows-powershell-5.1", b"\xef\xbb\xbf" + serialized)):
                with self.subTest(output=label):
                    stable.write_bytes(raw)
                    stable.chmod(0o600)
                    loaded, authenticated, _ = load_current_evidence_artifact(
                        stable,
                        repository_root=repository,
                        now_utc=NOW,
                        expected_database="CologneTransitIntelligence",
                        public_key_path=public,
                    )
                    self.assertEqual(loaded["artifact_integrity"]["status"], "SIGNED")  # type: ignore[index]
                    self.assertEqual(authenticated["status"], "PASS")

    def test_invalid_json_is_rejected_without_relaxing_schema_validation(self) -> None:
        with tempfile.TemporaryDirectory() as repository_directory, tempfile.TemporaryDirectory() as key_directory:
            repository = self._repository(repository_directory)
            stable = resolve_current_evidence_path(repository_root=repository)
            _, public = self._keypair(key_directory)
            stable.write_bytes(b"\xef\xbb\xbf{not-json")
            stable.chmod(0o600)
            with self.assertRaisesRegex(AdminEvidenceError, "missing or unreadable"):
                load_current_evidence_artifact(
                    stable,
                    repository_root=repository,
                    now_utc=NOW,
                    expected_database="CologneTransitIntelligence",
                    public_key_path=public,
                )

    def test_windows_tls_claims_require_live_encryption_and_validation_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as key_directory:
            private, public = self._keypair(key_directory)
            for mutation, message in (
                ({"encryption_mode": "Disabled"}, "unsupported encryption mode"),
                ({"actual_encryption": "FALSE"}, "encrypted SQL transport"),
                ({"certificate_validation_status": "NOT_PROVEN"}, "certificate validation"),
                ({"trust_server_certificate": True}, "Trust Server Certificate"),
            ):
                with self.subTest(mutation=mutation):
                    evidence = self._evidence()
                    evidence["capture_provenance"]["tls"].update(mutation)  # type: ignore[index]
                    evidence["artifact_integrity"] = sign_payload(evidence, signing_key_path=private)
                    with self.assertRaisesRegex(AdminEvidenceError, message):
                        validate_admin_evidence_artifact(
                            evidence,
                            now_utc=NOW,
                            expected_database="CologneTransitIntelligence",
                            public_key_path=public,
                        )

    def test_successful_signature_is_independently_verified(self) -> None:
        with tempfile.TemporaryDirectory() as key_directory:
            private, public = self._keypair(key_directory)
            evidence = self._evidence()
            integrity = sign_payload(evidence, signing_key_path=private)
            evidence["artifact_integrity"] = integrity
            self.assertTrue(verify_signature(evidence, public_key_path=public, integrity=integrity))
            evidence["sql"]["windows_vm_disk"]["sql_volume_headroom"][0]["headroom_bytes"] = 0  # type: ignore[index]
            self.assertFalse(verify_signature(evidence, public_key_path=public, integrity=integrity))

    def test_windows_disk_headroom_is_not_satisfied_by_nonzero_free_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as key_directory:
            private, public = self._keypair(key_directory)
            evidence = self._evidence()
            headroom = evidence["sql"]["windows_vm_disk"]["sql_volume_headroom"][0]  # type: ignore[index]
            headroom["available_bytes"] = 512 * 1024 * 1024
            headroom["available_percent"] = 0.5
            headroom["headroom_bytes"] = -512 * 1024 * 1024
            headroom["headroom_status"] = "FAIL"
            evidence["artifact_integrity"] = sign_payload(evidence, signing_key_path=private)
            with self.assertRaisesRegex(AdminEvidenceError, "headroom"):
                validate_admin_evidence_artifact(
                    evidence,
                    now_utc=NOW,
                    expected_database="CologneTransitIntelligence",
                    public_key_path=public,
                )

    def test_windows_capture_script_uses_bom_safe_output_and_live_tls_checks(self) -> None:
        script = (ROOT / "scripts" / "capture_windows_sql_admin_evidence.ps1").read_text(encoding="utf-8")
        self.assertIn("System.Text.UTF8Encoding", script)
        self.assertIn("ArgumentList $false", script)
        self.assertNotIn("Set-Content -Encoding UTF8", script)
        self.assertIn("EncryptOption", script)
        self.assertIn("TrustServerCertificate", script)
        self.assertIn("Win32_Volume", script)
        self.assertIn("sql_volume_headroom", script)
        self.assertIn("SpecifyKind", script)
        self.assertIn("InvariantCulture", script)
        self.assertIn("collector.http_attempt_history", script)
        self.assertIn("collector.historical_target_audits", script)
        self.assertIn("collector.tiered50_first_heartbeat_target_audits", script)
        self.assertIn("collector.tiered50_first_heartbeat_http_attempts", script)
        self.assertIn("ORDER BY AuditId DESC", script)
        self.assertIn("ORDER BY h.CompletedAtUtc DESC, h.HttpAttemptId DESC", script)
        self.assertIn("audit_provenance", script)
        self.assertNotIn("$history[0].Tiered50Runs", script)

    def test_recent_samples_keep_late_tiered50_rows_after_large_legacy7_history(self) -> None:
        script = (ROOT / "scripts" / "capture_windows_sql_admin_evidence.ps1").read_text(encoding="utf-8")
        target_sample = re.search(
            r"\$historicalTargetAuditsQuery.*?ORDER BY AuditId DESC;",
            script,
            flags=re.DOTALL,
        )
        http_sample = re.search(
            r"\$httpAttemptHistoryQuery.*?ORDER BY h\.CompletedAtUtc DESC, h\.HttpAttemptId DESC;",
            script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(target_sample)
        self.assertIsNotNone(http_sample)

        evidence = self._evidence()
        target_entry = evidence["sql"]["audit_provenance"]["collector.historical_target_audits"]  # type: ignore[index]
        target_rows = [
            {"audit_id": index, "run_id": f"legacy-{index}"}
            for index in range(1, 99)
        ] + [
            {"audit_id": 1001, "run_id": "tiered50-later-run"},
            {"audit_id": 1002, "run_id": "tiered50-first-run"},
        ]
        target_entry["rows"] = target_rows
        target_entry["query"]["result_count"] = len(target_rows)  # type: ignore[index]
        target_entry["check_status"] = "PASS"

        http_entry = evidence["sql"]["audit_provenance"]["collector.http_attempt_history"]  # type: ignore[index]
        http_rows = [
            {"http_attempt_id": index, "audit_id": index, "run_id": f"legacy-{index}"}
            for index in range(1, 99)
        ] + [
            {"http_attempt_id": 2001, "audit_id": 1002, "run_id": "tiered50-first-run"},
            {"http_attempt_id": 2002, "audit_id": 1002, "run_id": "tiered50-first-run"},
        ]
        http_entry["rows"] = http_rows
        http_entry["query"]["result_count"] = len(http_rows)  # type: ignore[index]
        http_entry["check_status"] = "PASS"

        self.assertEqual(
            validate_windows_audit_evidence(evidence["sql"])["status"],  # type: ignore[arg-type,index]
            "PASS",
        )

    def test_first_heartbeat_cross_check_requires_matching_run_and_audit_ids(self) -> None:
        evidence = self._evidence()
        linked_http = evidence["sql"]["audit_provenance"]["collector.tiered50_first_heartbeat_http_attempts"]  # type: ignore[index]
        linked_http["rows"][0]["run_id"] = "different-run"  # type: ignore[index]
        with self.assertRaisesRegex(AdminEvidenceError, "not linked by RunId"):
            validate_windows_audit_evidence(evidence["sql"])  # type: ignore[arg-type,index]

        evidence = self._evidence()
        linked_http = evidence["sql"]["audit_provenance"]["collector.tiered50_first_heartbeat_http_attempts"]  # type: ignore[index]
        linked_http["rows"][0]["audit_id"] = 9999  # type: ignore[index]
        with self.assertRaisesRegex(AdminEvidenceError, "not linked to its target audits"):
            validate_windows_audit_evidence(evidence["sql"])  # type: ignore[arg-type,index]

    def test_sql_datetime2_utc_survives_standard_and_dst_host_timezones(self) -> None:
        # The SQL value is a UTC clock value with no timezone metadata.  Both
        # Berlin offsets must preserve its fields rather than subtracting one
        # or two hours as a local-time conversion would.
        for local_zone, local_date in (
            (ZoneInfo("Europe/Berlin"), datetime(2026, 1, 15, 12, 34, 56, 123456)),
            (ZoneInfo("Europe/Berlin"), datetime(2026, 7, 15, 12, 34, 56, 123456)),
        ):
            with self.subTest(zone=local_zone, date=local_date):
                local_value = local_date.replace(tzinfo=local_zone)
                self.assertEqual(utc_isoformat(local_date), "2026-" + ("01-15" if local_date.month == 1 else "07-15") + "T12:34:56.123456Z")
                self.assertEqual(json_safe(local_date), "2026-" + ("01-15" if local_date.month == 1 else "07-15") + "T12:34:56.123456Z")
                self.assertNotEqual(utc_isoformat(local_value), utc_isoformat(local_date))

    def test_windows_audit_contract_distinguishes_zero_rows_from_failed_queries(self) -> None:
        with tempfile.TemporaryDirectory() as key_directory:
            private, public = self._keypair(key_directory)
            zero_rows = self._evidence()
            later = zero_rows["sql"]["audit_provenance"]["collector.tiered50_later_runs"]  # type: ignore[index]
            self.assertEqual(later["query"]["status"], "PASS")  # type: ignore[index]
            self.assertEqual(later["query"]["result_count"], 0)  # type: ignore[index]
            zero_rows["artifact_integrity"] = sign_payload(zero_rows, signing_key_path=private)
            self.assertEqual(
                validate_windows_audit_evidence(zero_rows["sql"])["status"],  # type: ignore[arg-type,index]
                "PASS",
            )

            failed = self._evidence()
            query = failed["sql"]["audit_provenance"]["collector.http_attempt_history"]["query"]  # type: ignore[index]
            query.update({"status": "NOT_PROVEN", "reason": "permission denied", "result_count": 0})  # type: ignore[union-attr]
            entry = failed["sql"]["audit_provenance"]["collector.http_attempt_history"]  # type: ignore[index]
            entry["rows"] = []  # type: ignore[index]
            entry["check_status"] = "NOT_PROVEN"  # type: ignore[index]
            failed["sql"]["recent_http_attempts_evidence"] = {"status": "NOT_PROVEN"}  # type: ignore[index]
            failed["sql"]["admin_authorization"] = {"status": "PASS"}  # type: ignore[index]
            failed["artifact_integrity"] = sign_payload(failed, signing_key_path=private)
            with self.assertRaisesRegex(AdminEvidenceError, "not fully proven|HTTP"):
                validate_admin_evidence_artifact(
                    failed,
                    now_utc=NOW,
                    expected_database="CologneTransitIntelligence",
                    public_key_path=public,
                )

    def test_windows_audit_contract_rejects_missing_target_audits_and_unlinked_http(self) -> None:
        with tempfile.TemporaryDirectory() as key_directory:
            private, public = self._keypair(key_directory)
            evidence = self._evidence()
            target_entry = evidence["sql"]["audit_provenance"]["collector.historical_target_audits"]  # type: ignore[index]
            target_entry["query"]["result_count"] = 0  # type: ignore[index]
            target_entry["rows"] = []
            target_entry["check_status"] = "NOT_PROVEN"
            evidence["sql"]["tiered50_audit_history_evidence"] = {"status": "PASS"}  # type: ignore[index]
            evidence["artifact_integrity"] = sign_payload(evidence, signing_key_path=private)
            with self.assertRaisesRegex(AdminEvidenceError, "not fully proven"):
                validate_admin_evidence_artifact(
                    evidence,
                    now_utc=NOW,
                    expected_database="CologneTransitIntelligence",
                    public_key_path=public,
                )

            unlinked = self._evidence()
            http_entry = unlinked["sql"]["audit_provenance"]["collector.http_attempt_history"]  # type: ignore[index]
            http_entry["linked_to_target_audit"] = False
            unlinked["artifact_integrity"] = sign_payload(unlinked, signing_key_path=private)
            with self.assertRaisesRegex(AdminEvidenceError, "inaccurate check status"):
                validate_admin_evidence_artifact(
                    unlinked,
                    now_utc=NOW,
                    expected_database="CologneTransitIntelligence",
                    public_key_path=public,
                )


class _PermissionCursor:
    def __init__(self, *, null_positive: bool = False) -> None:
        self.last_query = ""
        self.executed: list[tuple[str, object]] = []
        self.null_positive = null_positive

    def execute(self, statement: str, params=None) -> None:
        self.last_query = statement
        self.executed.append((statement, params))

    def fetchone(self):
        if "DB_NAME()" in self.last_query:
            return {
                "database_name": "CologneTransitIntelligence",
                "login_name": "CologneTransitCollector",
                "server_name": "DataAnalyst-VM",
                "machine_name": "DataAnalyst-VM",
                "instance_name": "MSSQLSERVER",
                "configured_server_name": "DataAnalyst-VM",
                **{f"is_{role}": 0 for role in PROHIBITED_RUNTIME_ROLE_CHECKS},
            }
        if self.null_positive and ", N'OBJECT', %s" in self.last_query:
            return {"permission_state": None}
        if ", N'OBJECT', %s" in self.last_query:
            return {"permission_state": 1}
        return {"permission_state": 0}


class _PermissionConnection:
    def __init__(self, *, null_positive: bool = False) -> None:
        self.cursor_value = _PermissionCursor(null_positive=null_positive)
        self.closed = False

    def cursor(self):
        return self.cursor_value

    def close(self) -> None:
        self.closed = True


class RuntimePermissionInspectionTests(unittest.TestCase):
    def test_matrix_is_checked_and_prohibited_metadata_is_denied(self) -> None:
        connection = _PermissionConnection()
        settings = MssqlConnectionSettings(
            host="sql",
            database="CologneTransitIntelligence",
            user="CologneTransitCollector",
            password="not-used",
        )
        with patch("provision_macos_runtime_login.connect_mssql", return_value=connection):
            result = inspect_runtime_permissions(settings)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(result["positive_permissions"]), len(RUNTIME_PERMISSION_MATRIX))
        self.assertEqual(
            len(connection.cursor_value.executed),
            1 + len(RUNTIME_PERMISSION_MATRIX) + len(PROHIBITED_RUNTIME_PERMISSION_CHECKS),
        )
        self.assertTrue(connection.closed)

    def test_null_permission_metadata_is_unknown_and_fails_closed(self) -> None:
        connection = _PermissionConnection(null_positive=True)
        settings = MssqlConnectionSettings(
            host="sql",
            database="CologneTransitIntelligence",
            user="CologneTransitCollector",
            password="not-used",
        )
        with patch("provision_macos_runtime_login.connect_mssql", return_value=connection):
            result = inspect_runtime_permissions(settings)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(result["unknown_permissions"])
        self.assertFalse(result["missing_permissions"])


if __name__ == "__main__":
    unittest.main()
