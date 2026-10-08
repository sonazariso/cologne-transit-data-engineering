from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cologne_transit_collector.collector import (  # noqa: E402
    Collector,
    _client_execution_failure,
    _quota_finalization_for_result,
)
from cologne_transit_collector.domain import TargetOutcome, TransportFailure  # noqa: E402
from cologne_transit_collector.manifest import load_default_manifest  # noqa: E402
from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MAX_SKIPPED_TARGET_AUDITS_PER_STATEMENT,
    MSSQL_PARAMETER_LIMIT,
    MssqlConnectionSettings,
    MssqlPersistence,
    MssqlSafetyError,
    SKIPPED_TARGET_AUDIT_PARAMETERS_PER_ROW,
)
from cologne_transit_collector.transport import LiveMddClient  # noqa: E402
from cologne_transit_collector.quota import InMemoryUsageProvider, QuotaGuard  # noqa: E402
from tiered50_production_evidence import (  # noqa: E402
    _SafeAdminCursor,
    _evaluate_admin_permissions,
    _query_evidence,
    _role_environment,
    _safe_backup_verify,
)


class _CapturingCursor:
    def __init__(self, connection: "_CapturingConnection") -> None:
        self.connection = connection
        self.rowcount = 1

    def execute(self, statement, params=None):  # type: ignore[no-untyped-def]
        if self.connection.fail_next:
            self.connection.fail_next = False
            raise RuntimeError("simulated interrupted SQL persistence")
        parameters = tuple(params or ())
        placeholder_count = str(statement).count("%s")
        if placeholder_count != len(parameters):
            raise AssertionError((placeholder_count, len(parameters)))
        self.connection.statements.append((str(statement), parameters))
        if "MERGE ctl.MddMacTargetAudit" in str(statement):
            width = SKIPPED_TARGET_AUDIT_PARAMETERS_PER_ROW
            if len(parameters) % width:
                raise AssertionError("parameter stream is not row aligned")
            for offset in range(0, len(parameters), width):
                row = parameters[offset : offset + width]
                # Check the DB-API values against the deployed SQL types at
                # the boundary where pymssql receives them.
                if not isinstance(row[5], int) or not isinstance(row[6], int):
                    raise AssertionError("integer audit fields were not bound as integers")
                if not isinstance(row[14], datetime) or not isinstance(row[15], datetime):
                    raise AssertionError("audit timestamps were not bound as datetimes")
                if not isinstance(row[23], float) or not isinstance(row[27], str):
                    raise AssertionError("decimal/json audit fields were not bound as expected")
                key = (row[0], row[2], row[7])
                self.connection.audit_rows.setdefault(key, row)

    def fetchone(self):  # type: ignore[no-untyped-def]
        return None


class _CapturingConnection:
    def __init__(self) -> None:
        self.cursor_instance = _CapturingCursor(self)
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self.audit_rows: dict[tuple[object, ...], tuple[object, ...]] = {}
        self.claim_rows: dict[tuple[object, ...], object] = {("panel", "target", 1): "RUNNING"}
        self.fail_next = False
        self.commit_count = 0
        self.rollback_count = 0

    def cursor(self):  # type: ignore[no-untyped-def]
        return self.cursor_instance

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        return None


class _Response:
    status = 200
    headers = {}

    def __init__(self, *, read_error: BaseException | None = None, close_error: BaseException | None = None) -> None:
        self.read_error = read_error
        self.close_error = close_error

    def read(self, *_args):  # type: ignore[no-untyped-def]
        if self.read_error is not None:
            raise self.read_error
        return b"{}"

    def close(self) -> None:
        if self.close_error is not None:
            raise self.close_error


class _DeniedCursor:
    def execute(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("(297, b'The user does not have permission to perform this action')")

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class _DeniedConnection:
    def cursor(self):
        return _DeniedCursor()


class Tiered50SqlAuditAndTransportRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_default_manifest(ROOT)
        production_ids: dict[str, int] = {}
        for target in cls.manifest.targets:
            if target.legacy_sampling_target_id is not None:
                production_ids[target.source_parent_station_id] = int(target.legacy_sampling_target_id)
        for index, target in enumerate(cls.manifest.new_tiered50_targets):
            production_ids[target.source_parent_station_id] = 8 + index
        bound = cls.manifest.bind_production_sampling_target_ids(production_ids)
        cls.targets = bound.new_tiered50_targets
        cls.now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)

    def _persistence(self, connection: _CapturingConnection) -> MssqlPersistence:
        return MssqlPersistence(
            settings=MssqlConnectionSettings(host="sql.example", user="admin", password="not-used"),
            connection_factory=lambda: connection,
            collection_context="OFFLINE_DEVELOPMENT",
        )

    def _entry(self, target, index: int) -> dict[str, object]:  # type: ignore[no-untyped-def]
        outcome = TargetOutcome(
            target_id=target.source_parent_station_id,
            station_name=target.station_name,
            tier=target.tier,
            phase_index=target.phase_index,
            logical_due_bucket=12345,
            status="SKIPPED",
            skip_reason="SKIPPED_HEARTBEAT_BUDGET",
            duration_seconds=0.25,
        )
        return {
            "target": target,
            "outcome": outcome.to_dict(),
            "started_at": self.now,
            "completed_at": self.now,
        }

    def test_supported_batch_sizes_bind_exactly_28_parameters_per_row(self) -> None:
        for count in (1, 16, 18, 19):
            with self.subTest(count=count):
                connection = _CapturingConnection()
                persistence = self._persistence(connection)
                persistence.persist_skipped_target_audits(
                    run_id=f"run-{count}",
                    logical_due_bucket=12345,
                    skipped_audits=tuple(self._entry(target, index) for index, target in enumerate(self.targets[:count])),
                    collection_context="MAC_TIERED50_PRODUCTION",
                )
                statement, parameters = connection.statements[-1]
                self.assertEqual(statement.count("%s"), count * 28)
                self.assertEqual(len(parameters), count * 28)
                self.assertEqual(len(connection.audit_rows), count)
                self.assertNotIn("WHEN MATCHED", statement.upper())
                self.assertIn("WITH (HOLDLOCK)", statement)

    def test_batch_limit_matches_sql_server_parameter_limit_and_overflow_fails_closed(self) -> None:
        self.assertEqual(MAX_SKIPPED_TARGET_AUDITS_PER_STATEMENT * 28, MSSQL_PARAMETER_LIMIT)
        self.assertEqual(MAX_SKIPPED_TARGET_AUDITS_PER_STATEMENT, 75)
        connection = _CapturingConnection()
        persistence = self._persistence(connection)
        with self.assertRaisesRegex(MssqlSafetyError, "2,100-parameter limit"):
            persistence.persist_skipped_target_audits(
                run_id="overflow",
                logical_due_bucket=12345,
                skipped_audits=tuple(
                    self._entry(target, index)
                    for index, target in enumerate(self.targets)
                ) + tuple(self._entry(self.targets[0], 999) for _ in range(33)),
                collection_context="MAC_TIERED50_PRODUCTION",
            )

    def test_repeated_retry_and_conflicting_existing_rows_are_idempotent_and_unclaimed(self) -> None:
        connection = _CapturingConnection()
        persistence = self._persistence(connection)
        entries = tuple(self._entry(target, index) for index, target in enumerate(self.targets[:19]))
        persistence.persist_skipped_target_audits(
            run_id="retryable",
            logical_due_bucket=12345,
            skipped_audits=entries,
            collection_context="MAC_TIERED50_PRODUCTION",
        )
        first_rows = dict(connection.audit_rows)
        persistence.persist_skipped_target_audits(
            run_id="retryable",
            logical_due_bucket=12345,
            skipped_audits=entries,
            collection_context="MAC_TIERED50_PRODUCTION",
        )
        self.assertEqual(connection.audit_rows, first_rows)
        self.assertEqual(connection.claim_rows, {("panel", "target", 1): "RUNNING"})

        existing_key = ("conflict", self.targets[0].source_parent_station_id, 12345)
        connection.audit_rows[existing_key] = (
            "conflict",
            self.targets[0].panel_version,
            self.targets[0].source_parent_station_id,
            self.targets[0].station_name,
            self.targets[0].tier,
            self.targets[0].phase_index,
            self.targets[0].number_of_results,
            12345,
            "Tiered50",
            self.targets[0].production_sampling_target_id,
            None,
            self.targets[0].selection_policy_version,
            self.targets[0].continuity_classification,
            "MAC_TIERED50_PRODUCTION",
            self.now,
            self.now,
            "SUCCEEDED",
            1,
            200,
            1,
            "PARSED",
            "PERSISTED_PARSED_SNAPSHOT",
            None,
            10.0,
            None,
            None,
            None,
            "authoritative success",
        )
        before = connection.audit_rows[existing_key]
        persistence.persist_skipped_target_audits(
            run_id="conflict",
            logical_due_bucket=12345,
            skipped_audits=(self._entry(self.targets[0], 0),),
            collection_context="MAC_TIERED50_PRODUCTION",
        )
        self.assertEqual(connection.audit_rows[existing_key], before)

    def test_interrupted_batch_can_be_retried_without_duplicate_rows(self) -> None:
        connection = _CapturingConnection()
        persistence = self._persistence(connection)
        entries = tuple(self._entry(target, index) for index, target in enumerate(self.targets[:18]))
        connection.fail_next = True
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            persistence.persist_skipped_target_audits(
                run_id="interrupted",
                logical_due_bucket=12345,
                skipped_audits=entries,
                collection_context="MAC_TIERED50_PRODUCTION",
            )
        self.assertGreaterEqual(connection.rollback_count, 1)
        self.assertEqual(connection.audit_rows, {})
        persistence.persist_skipped_target_audits(
            run_id="interrupted",
            logical_due_bucket=12345,
            skipped_audits=entries,
            collection_context="MAC_TIERED50_PRODUCTION",
        )
        self.assertEqual(len(connection.audit_rows), 18)

    def test_live_transport_exceptions_are_conservative_after_transport_entry(self) -> None:
        target = self.targets[0]
        request_time = self.now
        cases = {
            "opening": lambda: (_ for _ in ()).throw(RuntimeError("open failed")),
            "reading": lambda *_args, **_kwargs: _Response(read_error=RuntimeError("read failed")),
            "closing": lambda *_args, **_kwargs: _Response(close_error=RuntimeError("close failed")),
        }
        for name, opener in cases.items():
            with self.subTest(name=name):
                client = LiveMddClient(
                    "https://example.invalid/mdd",
                    api_key="safe-test-key",
                    urlopen_fn=opener,
                    enabled=True,
                )
                result = client.fetch(
                    target,
                    request_timestamp_utc=request_time,
                    timeout_seconds=30,
                    attempt_number=1,
                )
                self.assertTrue(result.live_client_invocation)
                self.assertTrue(result.transport_execution_entered)
                self.assertEqual(_quota_finalization_for_result(result), "CONSERVATIVE_LIVE_ATTEMPT")
                self.assertFalse(result.http_response_received)

    def test_proven_pre_network_error_is_the_only_live_release_path(self) -> None:
        client = LiveMddClient(
            "https://example.invalid/mdd",
            api_key="",
            enabled=True,
        )
        result = client.fetch(
            self.targets[0],
            request_timestamp_utc=self.now,
            timeout_seconds=30,
            attempt_number=1,
        )
        self.assertFalse(result.transport_execution_entered)
        self.assertEqual(_quota_finalization_for_result(result), "RELEASED_NO_NETWORK")

        error = RuntimeError("unexpected live client error")
        ambiguous = _client_execution_failure(error, mdd_client=client)
        self.assertTrue(ambiguous.transport_execution_entered)
        self.assertEqual(_quota_finalization_for_result(ambiguous), "CONSERVATIVE_LIVE_ATTEMPT")

    def test_denied_admin_query_is_not_proven_and_preserves_error_code(self) -> None:
        failures: list[dict[str, object]] = []
        cursor = _SafeAdminCursor(_DeniedConnection(), failures)
        cursor.execute("SELECT total_log_size_in_bytes FROM sys.dm_db_log_space_usage")
        self.assertIsNone(cursor.fetchone())
        evidence = _query_evidence(cursor, statement_id="capacity.database_log_space_dm_db_log_space_usage")
        self.assertEqual(evidence["status"], "NOT_PROVEN")
        self.assertEqual(evidence["error_code"], 297)
        self.assertEqual(len(failures), 1)

    def test_admin_permission_row_must_prove_each_required_capability(self) -> None:
        denied = _evaluate_admin_permissions(
            {
                "product_major_version": 16,
                "has_view_definition": 0,
                "has_view_database_state": 0,
                "has_view_server_state": 0,
                "has_view_server_performance_state": 0,
                "has_backup_database": 0,
                "has_msdb_backupset_select": 0,
                "has_msdb_backupmediafamily_select": 0,
                "has_database_select": 0,
                "has_create_any_database": 0,
                "is_sysadmin": 0,
                "is_dbcreator": 0,
                "is_db_owner": 0,
            }
        )
        self.assertEqual(denied["status"], "NOT_PROVEN")
        self.assertEqual(denied["server_dmv_required_permission"], "VIEW SERVER PERFORMANCE STATE")
        self.assertIn("server_dmv_state", denied["missing_permissions"])
        self.assertIn("backup_verifyonly_authorization", denied["missing_permissions"])

        approved = _evaluate_admin_permissions(
            {
                "product_major_version": 16,
                "has_view_definition": 1,
                "has_view_database_state": 1,
                "has_view_server_state": 0,
                "has_view_server_performance_state": 1,
                "has_backup_database": 1,
                "has_msdb_backupset_select": 1,
                "has_msdb_backupmediafamily_select": 1,
                "has_database_select": 1,
                "has_create_any_database": 1,
                "is_sysadmin": 0,
                "is_dbcreator": 0,
                "is_db_owner": 0,
            }
        )
        self.assertEqual(approved["status"], "PASS")

    def test_admin_environment_never_inherits_runtime_identity_or_secret(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MSSQL_HOST": "runtime-host",
                "MSSQL_USER": "CologneTransitCollector",
                "MSSQL_PASSWORD": "runtime-secret",
                "MSSQL_ADMIN_HOST": "admin-host",
            },
            clear=True,
        ):
            partial, source = _role_environment("admin")
        self.assertEqual(source, "MSSQL_ADMIN_*_REQUIRED")
        self.assertEqual(partial.get("MSSQL_HOST"), "admin-host")
        self.assertNotIn("MSSQL_USER", partial)
        self.assertNotIn("MSSQL_PASSWORD", partial)
        self.assertIn("MSSQL_ADMIN_USER", partial["__ADMIN_MISSING_FIELDS__"])

        with patch.dict(
            os.environ,
            {
                "MSSQL_HOST": "runtime-host",
                "MSSQL_USER": "CologneTransitCollector",
                "MSSQL_PASSWORD": "runtime-secret",
                "MSSQL_ADMIN_HOST": "admin-host",
                "MSSQL_ADMIN_DATABASE": "CologneTransitIntelligence",
                "MSSQL_ADMIN_USER": "separate-admin",
                "MSSQL_ADMIN_PASSWORD": "admin-secret",
            },
            clear=True,
        ):
            explicit, source = _role_environment("admin")
        self.assertEqual(source, "MSSQL_ADMIN_*_EXPLICIT")
        self.assertEqual(explicit["MSSQL_USER"], "separate-admin")
        self.assertEqual(explicit["MSSQL_PASSWORD"], "admin-secret")

    def test_backup_3013_does_not_claim_corruption_or_permission_cause(self) -> None:
        class _BackupCursor:
            def execute(self, statement):  # type: ignore[no-untyped-def]
                raise RuntimeError("(3013, backup path /private/secret.bak is inaccessible)")

            def fetchall(self):
                return []

        class _BackupConnection:
            def cursor(self):
                return _BackupCursor()

        result = _safe_backup_verify(_BackupConnection(), "/private/secret.bak")
        self.assertEqual(result["status"], "NOT_PROVEN")
        self.assertEqual(result["error_code"], 3013)
        self.assertEqual(result["cause_classification"], "UNDETERMINED")
        self.assertNotIn("/private/secret.bak", result["reason"])

    def test_attempt_persistence_failure_keeps_quota_permit_pending(self) -> None:
        class _FailingAttemptPersistence:
            supports_atomic_quota_finalization = False

            def record_http_attempt(self, **_kwargs):  # type: ignore[no-untyped-def]
                raise RuntimeError("attempt row unavailable")

        provider = InMemoryUsageProvider()
        quota_guard = QuotaGuard(provider)
        decision = quota_guard.allow_initial(at_utc=self.now)
        self.assertTrue(decision.allowed)
        collector = Collector(
            manifest=self.manifest,
            persistence=_FailingAttemptPersistence(),
            mdd_client=object(),
            quota_guard=quota_guard,
        )
        failure = TransportFailure(
            category="CLIENT_EXECUTION_FAILURE",
            network_attempted=False,
            transport_kind="LIVE_CLIENT",
            live_client_invocation=True,
            transport_execution_entered=True,
        )
        with self.assertRaisesRegex(RuntimeError, "attempt row unavailable"):
            collector._record_attempt_with_permit(
                audit_id="audit-1",
                attempt_number=1,
                started_at=self.now,
                completed_at=self.now,
                transport_failure=failure,
                transport_kind="LIVE_CLIENT",
                live_client_invocation=True,
                quota_decision=decision,
            )
        usage = provider.current_month_usage(at_utc=self.now)
        self.assertEqual(usage["reserved_shared_usage"], 1)
        self.assertEqual(usage["conservative_live_attempts"], 0)


if __name__ == "__main__":
    unittest.main()
