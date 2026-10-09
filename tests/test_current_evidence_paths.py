from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cologne_transit_collector.admin_evidence import (  # noqa: E402
    AdminEvidenceError,
    EVIDENCE_ARTIFACT_VERSION,
    REQUIRED_CHECK_IDS,
    load_current_evidence_artifact,
    require_current_evidence_path,
    resolve_current_evidence_path,
    sign_payload,
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
                "required_check_ids": sorted(REQUIRED_CHECK_IDS),
                "tls": {
                    "encryption_mode": "Mandatory",
                    "trust_server_certificate": False,
                    "certificate_validation_required": True,
                },
            },
            "sql": {
                "admin_authorization": {"status": "PASS"},
                "runtime_health": {"status": "PASS"},
                "backup_gate": {"status": "PASS"},
                "capacity_gate": {"status": "PASS"},
                "log_space_gate": {"status": "PASS"},
                "quota_integrity": {"status": "PASS", "state_available": True},
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
