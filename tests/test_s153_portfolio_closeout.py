from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FINAL_CLOSEOUT_SENTENCE = (
    "The project was stopped due to the high volume of collected data and "
    "insufficient available storage capacity. Successful bounded ingestion "
    "and SQL Server persistence were demonstrated; continuous coverage was "
    "not achieved."
)


class S156PortfolioCloseoutTests(unittest.TestCase):
    def test_public_closeout_documents_end_with_required_statement(self) -> None:
        paths = (
            ROOT / "README.md",
            ROOT / "docs/DATABASE_RIGHTSIZING_PLAN.md",
            ROOT / "docs/PORTFOLIO_CLOSEOUT.md",
            ROOT / "docs/PROJECT-STATUS.md",
            ROOT / "docs/TEN_SNAPSHOT_RETENTION_MANIFEST.md",
            ROOT / "docs/operational/tiered50-historical-and-future-storage-recovery.md",
        )
        for path in paths:
            self.assertTrue(path.exists(), path)
            self.assertTrue(path.read_text(encoding="utf-8").rstrip().endswith(FINAL_CLOSEOUT_SENTENCE), path)

    def test_manifest_contains_live_evidence_and_locks_operator_ids(self) -> None:
        manifest = (ROOT / "docs/TEN_SNAPSHOT_RETENTION_MANIFEST.md").read_text(encoding="utf-8")
        self.assertIn("LIVE_READ_ONLY_EVIDENCE_CAPTURED_2026-10-15", manifest)
        self.assertIn("No production `DELETE`, `SHRINK`, `DROP`", manifest)
        self.assertNotIn("ATTACH LIVE SQL", manifest)
        self.assertEqual(len(re.findall(r"^\| (?:[1-9]|10) \|", manifest, flags=re.MULTILINE)), 10)
        for snapshot_id in (22, 93, 185, 189, 207, 224, 225, 226, 228, 229):
            self.assertRegex(
                manifest,
                re.compile(rf"^\| \d+ \| {snapshot_id} \|", re.MULTILINE),
                msg=str(snapshot_id),
            )
        for family in (
            "MddMacTimingObservation",
            "MddMacCallObservation",
            "MddMacSituationEvidence",
            "MddMacServiceSectionEvidence",
            "MddMacAttributeEvidence",
            "MddMacResponseError",
            "MddMacParserGap",
        ):
            self.assertIn(family, manifest)

    def test_candidate_sql_has_exact_ten_evidence_gate(self) -> None:
        sql = (ROOT / "sql/portfolio/select-ten-snapshots-readonly.sql").read_text(encoding="utf-8")
        for token in (
            "DECLARE @EligibleCount BIGINT",
            "IF @EligibleCount < 10",
            "PERSISTED_PARSED_SNAPSHOT",
            "ROW_NUMBER() OVER",
            "PARTITION BY c.TargetIdentity",
            "WHERE r.RetentionSlot <= 10",
            "COUNT_BIG(*) OVER () AS SelectedCount",
            "OriginalPayloadSha256",
            "PhysicalGzipSha256",
            "DECOMPRESS",
            "HASHBYTES",
        ):
            self.assertIn(token, sql)
        for family in (
            "MddMacTimingObservation",
            "MddMacCallObservation",
            "MddMacSituationEvidence",
            "MddMacServiceSectionEvidence",
            "MddMacAttributeEvidence",
            "MddMacResponseError",
            "MddMacParserGap",
        ):
            self.assertIn(family, sql)
        self.assertNotRegex(sql, r"(?im)^\\s*(DELETE|DROP|TRUNCATE|ALTER)\\b")

    def test_apply_and_shrink_files_are_disabled_by_default(self) -> None:
        retention = (ROOT / "sql/portfolio/retention-apply.sql").read_text(encoding="utf-8")
        shrink = (ROOT / "sql/portfolio/recover-project-space-apply.sql").read_text(encoding="utf-8")
        for sql, tokens in (
            (
                retention,
                (
                    "DECLARE @Apply BIT = 0",
                    "DECLARE @ManifestApproved BIT = 0",
                    "BatchSize INT NOT NULL",
                    "MaxNonRetainedRowBytes BIGINT NOT NULL",
                    "WorstCaseBatchReserveBytes BIGINT NOT NULL",
                    "IF @Apply = 0",
                    "Exactly ten literal manifest rows are required.",
                    "12 GiB",
                    "DELETE TOP (@BatchSize)",
                    "sys.dm_os_volume_stats",
                    "sys.dm_db_log_space_usage",
                    "retention_after_counts",
                    "retention_fk_row_closure",
                    "DBCC CHECKCONSTRAINTS",
                ),
            ),
            (
                shrink,
                (
                    "DECLARE @ApplyShrink BIT = 0",
                    "CologneTransitIntelligence",
                    "recovery_live_capacity_preflight",
                    "DBCC SHRINKFILE",
                    "@ProjectedRecoveryPercent",
                    "12 GiB",
                    "80%",
                ),
            ),
        ):
            for token in tokens:
                self.assertIn(token, sql)

    def test_s154_read_only_replacement_and_inventory_boundaries(self) -> None:
        replacement = (ROOT / "sql/portfolio/replacement-database-dry-run.sql").read_text(encoding="utf-8")
        inventory = (ROOT / "sql/portfolio/retention-inventory-readonly.sql").read_text(encoding="utf-8")
        for sql in (replacement, inventory):
            for token in (
                "12 GiB",
                "backup_verification",
                "external_consumer_review",
                "before_after_measurement_contract",
                "gtfs_table_review",
                "warehouse_table_review",
                "NO_AUTOMATIC_DISCARD",
            ):
                self.assertIn(token, sql)
            self.assertNotRegex(sql, r"(?im)^\s*(DELETE|DROP|TRUNCATE|ALTER)\b")
        self.assertIn("OPTIONAL_HISTORICAL_FEASIBILITY_REVIEW", replacement)

    def test_dry_run_and_apply_lock_the_s154_id_set(self) -> None:
        expected = ("22", "93", "185", "189", "207", "224", "225", "226", "228", "229")
        for relative in (
            "sql/portfolio/retention-dry-run.sql",
            "sql/portfolio/retention-apply.sql",
            "sql/portfolio/replacement-database-dry-run.sql",
        ):
            sql = (ROOT / relative).read_text(encoding="utf-8")
            for snapshot_id in expected:
                self.assertIn(snapshot_id, sql, relative)
        dry_run = (ROOT / "sql/portfolio/retention-dry-run.sql").read_text(encoding="utf-8")
        self.assertIn("DRY_RUN_ONLY_EVIDENCE", dry_run)
        self.assertIn("No base rows were changed", dry_run)

    def test_inventory_and_recovery_review_are_read_only(self) -> None:
        for relative in (
            "sql/portfolio/retention-inventory-readonly.sql",
            "sql/portfolio/select-ten-snapshots-readonly.sql",
            "sql/portfolio/recover-project-space-readonly.sql",
            "sql/portfolio/retention-dry-run.sql",
            "sql/portfolio/replacement-database-dry-run.sql",
        ):
            sql = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotRegex(sql, r"(?im)^\\s*(DELETE|DROP|TRUNCATE|ALTER)\\b", relative)

    def test_closeout_keeps_collector_and_publication_boundaries(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        status = (ROOT / "docs/PROJECT-STATUS.md").read_text(encoding="utf-8")
        self.assertIn("both Tiered50 and Legacy7 were subsequently disabled", readme)
        self.assertIn("STOPPED / DISABLED", status)
        self.assertIn("push via GitHub Desktop", (ROOT / "docs/GITHUB_RELEASE_CHECKLIST.md").read_text(encoding="utf-8"))
        self.assertFalse((ROOT / "powerbi/README.md").exists())


if __name__ == "__main__":
    unittest.main()
