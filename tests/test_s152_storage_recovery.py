from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKFILL = ROOT / "sql/mdd-mac-collector/backfill-snapshot-gzip-v1.sql"
VERIFY = ROOT / "sql/mdd-mac-collector/verify-snapshot-gzip-v1.sql"
AUDIT = ROOT / "sql/mdd-mac-collector/audit-historical-storage-recovery-readonly.sql"
LAB = ROOT / "sql/mdd-mac-collector/measure-snapshot-gzip-capacity-lab.sql"
REPORT = ROOT / "docs/operational/tiered50-historical-and-future-storage-recovery.md"


class S152StorageRecoveryTests(unittest.TestCase):
    def test_prompt_and_report_are_nonempty_and_block_without_live_evidence(self) -> None:
        report = REPORT.read_text(encoding="utf-8")
        self.assertTrue(REPORT.is_file())
        for token in (
            "BLOCKED_INSUFFICIENT_MEASURED_HEADROOM",
            "PRODUCTION_MUTATIONS=0",
            "COLLECTOR_DISABLED",
            "ARCHIVE_PATH",
            "FINAL_DECISION",
            "5.376 GiB",
            "13.92 GiB",
            "1.92 GiB",
        ):
            self.assertIn(token, report)

    def test_backfill_has_atomic_one_row_exact_and_physical_capacity_gates(self) -> None:
        sql = BACKFILL.read_text(encoding="utf-8")
        for token in (
            "SET XACT_ABORT ON",
            "SET IMPLICIT_TRANSACTIONS OFF",
            "IF @@TRANCOUNT <> 0",
            "DECLARE @Apply BIT = 0",
            "DECLARE @MaxRows INT = 1",
            "TOP (@MaxRows)",
            "ORDER BY SnapshotId",
            "sp_getapplock",
            "OriginalUtf16Bytes",
            "OriginalHash",
            "OriginalWasNull",
            "OriginalWasEmpty",
            "OriginalRunId",
            "OriginalAuditId",
            "OriginalPayloadHashSha256",
            "MeasuredPeakRollbackBytes",
            "MeasuredAutogrowthReserveBytes",
            "IndependentLivePreflightCapturedAtUtc",
            "current_mdf_free_pages",
            "current_log_max_size_pages",
            "current_tempdb_fixed_growth_bytes",
            "current_active_sql_sessions",
            "ProjectedWorstCaseCFreeBytes",
            "12 GiB Windows safety floor",
            "EACH_RUN_COMMITS_AT_MOST_ONE_ROW",
        ):
            self.assertIn(token, sql)
        self.assertNotIn("DECLARE @Apply BIT = 1", sql)

    def test_verifier_separates_physical_and_logical_contracts(self) -> None:
        sql = VERIFY.read_text(encoding="utf-8")
        for token in (
            "DECOMPRESS(s.SnapshotJsonGzip)",
            "CONVERT(VARBINARY(MAX), s.SnapshotJson)",
            "HASHBYTES('SHA2_256'",
            "logical_empty_rows",
            "physical_missing_rows",
            "physical_dual_rows",
            "distinct_snapshot_ids",
            "distinct_audit_ids",
            "SnapshotId or audit-lineage readback parity failed",
            "capacity.snapshot_gzip_physical_logical_contract",
        ):
            self.assertIn(token, sql)
        self.assertIn("DATALENGTH(d.RestoredText) > 0", sql)

    def test_read_only_audit_covers_live_capacity_and_consumer_dependencies(self) -> None:
        sql = AUDIT.read_text(encoding="utf-8")
        for statement_id in (
            "capacity.mdf_page_space",
            "capacity.ldf_usage_growth_maxsize",
            "capacity.tempdb",
            "capacity.rollback",
            "capacity.autogrowth",
            "capacity.active_sql_sessions",
            "capacity.snapshot_readback_module_dependencies",
            "capacity.snapshot_readback_sql_agent_jobs",
            "capacity.snapshot_readback_external_consumers",
            "capacity.snapshot_gzip_exact_savings",
            "capacity.datafile_space_reclamation",
        ):
            self.assertIn(statement_id, sql)
        for token in (
            "allocated_pages",
            "used_pages",
            "free_pages",
            "ldf_used_percent",
            "max_size_pages",
            "direct_snapshot_json",
            "POWER_BI",
            "BLOCKED_UNRESOLVED_EXTERNAL_CONSUMER_DEPENDENCY",
            "sysjobsteps",
            "COL_NAME(dep.referenced_id, dep.referenced_minor_id)",
        ):
            self.assertIn(token, sql)
        self.assertNotIn("ALTER TABLE", sql.upper())

    def test_isolated_lab_is_disabled_and_records_rollback_probe(self) -> None:
        sql = LAB.read_text(encoding="utf-8")
        self.assertIn("DECLARE @RunLab BIT = 0", sql)
        self.assertIn("PeakRollbackLogBytes", sql)
        self.assertIn("ROLLBACK TRANSACTION", sql)
        self.assertIn("capacity.snapshot_gzip_lab_peak", sql)
        self.assertIn("capacity.snapshot_gzip_lab_resume_state", sql)

    def test_shrink_approval_requires_fresh_reserves_and_projection(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT / "scripts"))
        from reclaim_snapshot_gzip_datafile_space import _approval_gate, _capacity_projection_gate

        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as handle:
            json.dump({"approved": True}, handle)
            handle.flush()
            result = _approval_gate(
                Path(handle.name),
                now=datetime(2026, 10, 14, 12, 10, tzinfo=timezone.utc),
            )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("capacity_reserves", result["missing_fields"])
        self.assertEqual(_capacity_projection_gate({})["status"], "BLOCKED")
        self.assertEqual(
            _capacity_projection_gate(
                {
                    "physical_capacity_projection": {
                        "status": "PASS",
                        "current_windows_c_free_bytes": 20,
                        "worst_case_physical_c_reserve_bytes": 7,
                        "projected_worst_case_c_free_bytes": 13,
                        "required_windows_c_floor_bytes": 12,
                    }
                }
            )["status"],
            "PASS",
        )


if __name__ == "__main__":
    unittest.main()
