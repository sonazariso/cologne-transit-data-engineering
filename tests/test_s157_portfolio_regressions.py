from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RETENTION = ROOT / "sql/portfolio/retention-apply.sql"
RECOVERY = ROOT / "sql/portfolio/recover-project-space-apply.sql"


class S157PortfolioRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.retention = RETENTION.read_text(encoding="utf-8")
        self.recovery = RECOVERY.read_text(encoding="utf-8")

    def test_retention_uses_reviewed_per_table_batches(self) -> None:
        self.assertNotIn("DECLARE @BatchSize INT = 50", self.retention)
        self.assertIn(
            "SELECT SchemaName, TableName, RetentionPredicate, BatchSize",
            self.retention,
        )
        self.assertNotIn("CASE WHEN BatchSize > @BatchSize", self.retention)
        self.assertIn("BatchSize INT NOT NULL", self.retention)
        self.assertIn("@CountBatchSize", self.retention)

    def test_retention_reserve_is_max_payload_based_not_average_page_based(self) -> None:
        for token in (
            "MaxNonRetainedRowBytes",
            "DATALENGTH(x.",
            "WorstCaseBatchReserveBytes",
            "IndexCount",
            "@BatchReserveDecimal",
        ):
            self.assertIn(token, self.retention)
        for forbidden in ("ReservedPages", "RowsObserved", "reserved_page_count"):
            self.assertNotIn(forbidden, self.retention)

    def test_retention_verifies_totals_fk_closure_hierarchy_and_dbcc_fail_closed(self) -> None:
        for token in (
            "retention_after_counts",
            "OR @PostTotalRows <> @ExpectedRows",
            "retention_fk_row_closure",
            "ViolationRows > 0",
            "#RetainedDimStopHierarchy",
            "The retained DimStop hierarchy requires an unapproved ancestor.",
            "DimStop deletion is incomplete; an unapproved child remains.",
            "#DbccCheckConstraints",
            "DBCC CHECKCONSTRAINTS found violations; apply cannot succeed.",
        ):
            self.assertIn(token, self.retention)
        self.assertRegex(
            self.retention,
            r"IF EXISTS \(SELECT 1 FROM #DbccCheckConstraints\)\s+THROW",
        )

    def test_retention_application_lock_cleanup_covers_pre_cursor_failures(self) -> None:
        try_start = self.retention.index("BEGIN TRY")
        lock_call = self.retention.index("sys.sp_getapplock")
        catch_start = self.retention.index("BEGIN CATCH", lock_call)
        release_in_catch = self.retention.index(
            "sys.sp_releaseapplock", catch_start
        )
        self.assertLess(try_start, lock_call)
        self.assertLess(lock_call, catch_start)
        self.assertLess(catch_start, release_in_catch)

    def test_recovery_has_one_nullable_plan_target_and_complete_footprint_gate(self) -> None:
        self.assertNotIn("DECLARE @TargetSizeMB", self.recovery)
        self.assertIn("TargetSizeMB BIGINT NULL", self.recovery)
        self.assertIn("VALUES (@LogicalDataFileName, NULL)", self.recovery)
        self.assertNotIn("VALUES (@LogicalDataFileName, 0)", self.recovery)
        for token in (
            "@OriginalProjectFootprintBytes",
            "@ProjectedProjectFootprintBytes",
            "complete original MDF plus LDF footprint",
            "sys.dm_db_log_space_usage",
            "@MinimumSafeTargetSizeMB",
            "@ActualBeforeMdfBytes",
            "@ActualAfterMdfBytes",
            "@MinimumSafeTargetPages",
        ):
            self.assertIn(token, self.recovery)
        self.assertRegex(
            self.recovery,
            re.compile(
                r"\(@ActualBeforeMdfBytes - @ActualAfterMdfBytes\) \* 100\.0"
            ),
        )
        self.assertNotRegex(
            self.recovery,
            re.compile(r"@DataAllocatedBytes\s*-\s*@ActualAfterBytes"),
        )

    def test_recovery_keeps_fresh_capacity_checks_and_strict_floor(self) -> None:
        self.assertGreaterEqual(
            self.recovery.count("@RequiredCFreeBytes"),
            5,
        )
        for token in (
            "@LogFreeBytes <= 0",
            "@TempdbFreeBytes <= 0",
            "A collector run became active",
            "An active production lease exists; space recovery is stopped.",
            "actual_project_footprint_recovery_percent",
        ):
            self.assertIn(token, self.recovery)


if __name__ == "__main__":
    unittest.main()
