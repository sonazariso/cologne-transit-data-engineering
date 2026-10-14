from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKFILL = ROOT / "sql/mdd-mac-collector/backfill-snapshot-gzip-v1.sql"
SNAPSHOT_LAB = ROOT / "sql/mdd-mac-collector/measure-snapshot-gzip-capacity-lab.sql"
TIMING_LAB = ROOT / "sql/mdd-mac-collector/measure-timing-storage-options-lab.sql"
VERIFY = ROOT / "sql/mdd-mac-collector/verify-snapshot-gzip-v1.sql"
AUDIT = ROOT / "sql/mdd-mac-collector/audit-historical-storage-recovery-readonly.sql"
REPORT = ROOT / "docs/operational/tiered50-historical-and-future-storage-recovery.md"


class S151StorageRecoveryTests(unittest.TestCase):
    def test_backfill_is_exactly_one_row_and_explicitly_approved(self) -> None:
        sql = BACKFILL.read_text(encoding="utf-8")
        self.assertRegex(sql, r"DECLARE\s+@Apply\s+BIT\s*=\s*0")
        self.assertRegex(sql, r"DECLARE\s+@MaxRows\s+INT\s*=\s*1")
        for token in (
            "@ApproveOriginalPhysicalRepresentationChange",
            "@BackupCoverageConfirmed",
            "@ReadbackCompatibilityConfirmed",
            "@PeakPerSnapshotCapacityConfirmed",
            "@SafetyFloorConfirmed",
            "capacity.snapshot_gzip_exact_savings",
            "capacity.snapshot_gzip_one_row_result",
            "capacity.snapshot_gzip_resume_state",
            "COMPRESS(SnapshotJson)",
            "sys.dm_tran_database_transactions",
            "12 GiB Windows safety floor",
            "stg.vMddMacSourceSnapshotJson",
        ):
            self.assertIn(token, sql)
        self.assertIn("IF @Apply = 0", sql)
        self.assertIn("EACH_RUN_COMMITS_AT_MOST_ONE_ROW", sql)

    def test_snapshot_lab_is_disabled_isolated_and_resumable(self) -> None:
        sql = SNAPSHOT_LAB.read_text(encoding="utf-8")
        self.assertRegex(sql, r"DECLARE\s+@RunLab\s+BIT\s*=\s*0")
        for token in (
            "cannot run on the production SQL Server",
            "requires SQL data, log, and tempdb files on non-C:",
            "lab.SnapshotGzipCapacityMeasurement",
            "capacity.snapshot_gzip_lab_per_snapshot",
            "capacity.snapshot_gzip_lab_peak",
            "PeakTransactionLogBytes",
            "COMPRESS(SnapshotJson)",
        ):
            self.assertIn(token, sql)
        self.assertGreater(sql.index("CREATE TABLE lab.SnapshotGzipCapacityMeasurement"), sql.index("IF @RunLab <> 1"))

    def test_timing_lab_is_bounded_by_reclaimed_capacity(self) -> None:
        sql = TIMING_LAB.read_text(encoding="utf-8")
        self.assertRegex(sql, r"DECLARE\s+@RunLab\s+bit\s*=\s*0")
        self.assertIn("@SampleRows", sql)
        self.assertIn("@ReclaimedCapacityBytes", sql)
        self.assertIn("@MaximumSampleRows", sql)
        self.assertIn("#TimingSourceSample", sql)
        self.assertIn("NO_FULL_HISTORY_DUPLICATE", sql)
        self.assertIn("capacity.timing_storage_reclaimed_capacity_gate", sql)
        self.assertIn("@SampleRows >= @SourceTimingRows", sql)

    def test_audit_and_verifier_expose_required_read_only_evidence(self) -> None:
        audit = AUDIT.read_text(encoding="utf-8")
        verify = VERIFY.read_text(encoding="utf-8")
        for token in (
            "capacity.snapshot_gzip_exact_savings",
            "capacity.datafile_space_reclamation",
            "COMPRESS(SnapshotJson)",
            "internal_free_bytes",
            "required_windows_c_floor_bytes",
        ):
            self.assertIn(token, audit)
        for token in (
            "capacity.snapshot_gzip_readback_compatibility",
            "Plaintext SnapshotJson readback parity failed",
            "DECOMPRESS(s.SnapshotJsonGzip)",
        ):
            self.assertIn(token, verify)
        self.assertNotIn("capacity.vmware_relocation_assessment", audit)

    def test_report_is_c_only_and_does_not_claim_physical_recovery(self) -> None:
        report = REPORT.read_text(encoding="utf-8")
        for token in (
            "695",
            "5.51 GiB",
            "exact `COMPRESS`",
            "one-snapshot transaction",
            "12 GiB",
            "DBCC SHRINKFILE",
            "TIMING_FULL_HISTORY_DUPLICATE: NOT_PERMITTED",
            "PROBLEM_SOLVED: NO",
        ):
            self.assertIn(token, report)
        self.assertNotIn("VMware", report)
        self.assertNotIn("MDF", report)
        self.assertNotRegex(report, r"Stage\s+15")

    def test_all_new_lab_paths_remain_disabled_by_source_defaults(self) -> None:
        for path, pattern in (
            (BACKFILL, r"DECLARE\s+@Apply\s+BIT\s*=\s*0"),
            (SNAPSHOT_LAB, r"DECLARE\s+@RunLab\s+BIT\s*=\s*0"),
            (TIMING_LAB, r"DECLARE\s+@RunLab\s+bit\s*=\s*0"),
        ):
            self.assertRegex(path.read_text(encoding="utf-8"), pattern)


if __name__ == "__main__":
    unittest.main()
