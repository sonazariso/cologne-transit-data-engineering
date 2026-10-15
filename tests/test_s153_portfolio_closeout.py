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


class S153PortfolioCloseoutTests(unittest.TestCase):
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

    def test_manifest_is_fail_closed_until_real_sql_evidence(self) -> None:
        manifest = (ROOT / "docs/TEN_SNAPSHOT_RETENTION_MANIFEST.md").read_text(encoding="utf-8")
        self.assertIn("BLOCKED_UNTIL_READ_ONLY_OPERATOR_EVIDENCE", manifest)
        self.assertIn("ACTUAL_SNAPSHOT_IDS: PENDING_READ_ONLY_SQL_RESULT", manifest)
        self.assertEqual(len(re.findall(r"^\| (?:[1-9]|10) \|", manifest, flags=re.MULTILINE)), 10)
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
                    "DECLARE @BatchSize INT = 1000",
                    "IF @Apply = 0",
                    "Exactly ten literal manifest rows are required.",
                    "12 GiB",
                    "ctl history is preserved",
                ),
            ),
            (
                shrink,
                (
                    "DECLARE @ApplyShrink BIT = 0",
                    "REPLACE_WITH_EXACT_LOGICAL_DATA_FILE_NAME",
                    "DBCC SHRINKFILE",
                    "ProjectedRecoveryPercent",
                    "12 GiB",
                    "80%",
                ),
            ),
        ):
            for token in tokens:
                self.assertIn(token, sql)

    def test_inventory_and_recovery_review_are_read_only(self) -> None:
        for relative in (
            "sql/portfolio/retention-inventory-readonly.sql",
            "sql/portfolio/select-ten-snapshots-readonly.sql",
            "sql/portfolio/recover-project-space-readonly.sql",
            "sql/portfolio/retention-dry-run.sql",
        ):
            sql = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotRegex(sql, r"(?im)^\\s*(DELETE|DROP|TRUNCATE|ALTER)\\b", relative)

    def test_closeout_keeps_collector_and_publication_boundaries(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        status = (ROOT / "docs/PROJECT-STATUS.md").read_text(encoding="utf-8")
        self.assertIn("collectors are intentionally disabled", readme)
        self.assertIn("Collectors remain disabled and unloaded.", status)
        self.assertIn("GITHUB_PUSH: NOT_PERFORMED", status)
        self.assertFalse((ROOT / "powerbi/README.md").exists())


if __name__ == "__main__":
    unittest.main()
