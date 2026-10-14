from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "sql/mdd-mac-collector/audit-historical-storage-recovery-readonly.sql"
LAB = ROOT / "sql/mdd-mac-collector/measure-timing-storage-options-lab.sql"
SAMPLER = ROOT / "scripts/sample_timing_storage_peak_lab.py"
REPORT = ROOT / "docs/operational/tiered50-historical-and-future-storage-recovery.md"


class S150StorageRecoveryTests(unittest.TestCase):
    def test_production_audit_measures_timing_columns_and_c_only_reclamation(self) -> None:
        audit = AUDIT.read_text(encoding="utf-8")
        for statement_id in (
            "capacity.timing_column_bytes",
            "capacity.timing_index_design",
            "capacity.snapshot_gzip_exact_savings",
            "capacity.datafile_space_reclamation",
        ):
            self.assertIn(statement_id, audit)
        self.assertIn("DATALENGTH(t.", audit)
        self.assertIn("percent_of_actual_column_bytes", audit)
        self.assertIn("average_non_null_value_bytes", audit)
        self.assertIn("key_index_count", audit)
        self.assertIn("include_index_count", audit)
        self.assertIn("exact_payload_savings_bytes", audit)
        self.assertIn("internal_free_bytes", audit)
        self.assertIn("SHRINKFILE", audit.upper())
        migration = (ROOT / "scripts/migrate_historical_compact_observations.py").read_text(encoding="utf-8")
        self.assertIn('"capacity.timing_column_bytes"', migration)
        self.assertIn('"capacity.timing_index_design"', migration)
        self.assertIn('"capacity.snapshot_gzip_exact_savings"', migration)
        self.assertIn('"capacity.datafile_space_reclamation"', migration)
        self.assertNotIn("vmware_relocation_assessment", migration)

    def test_lab_comparison_is_disabled_and_rejects_production_or_c_drive(self) -> None:
        lab = LAB.read_text(encoding="utf-8")
        self.assertRegex(lab, r"DECLARE\s+@RunLab\s+bit\s*=\s*0\s*;?")
        self.assertIn("Timing storage lab measurement is disabled", lab)
        self.assertIn("cannot run on the production SQL Server", lab)
        self.assertIn("requires SQL data, log, and tempdb files on non-C:", lab)
        self.assertIn("DATA_COMPRESSION = PAGE", lab)
        self.assertIn("TimingDictionaryValue", lab)
        self.assertIn("TimingDictionaryReference", lab)
        self.assertIn("reserved_page_count", lab)
        self.assertIn("SORT_IN_TEMPDB = ON", lab)
        self.assertIn("capacity.timing_storage_option_peak", lab)
        self.assertIn("capacity.timing_storage_option_savings", lab)
        self.assertIn("OBSERVED_CHECKPOINT_MAX", lab)
        self.assertIn("capacity.timing_storage_losslessness", lab)
        self.assertIn("dictionary_roundtrip_mismatch_count", lab)
        self.assertIn("variable_source_null_cell_count", lab)
        self.assertIn("variable_source_zero_length_cell_count", lab)
        self.assertIn("@SampleRows", lab)
        self.assertIn("@ReclaimedCapacityBytes", lab)
        self.assertIn("#TimingSourceSample", lab)
        self.assertIn("NO_FULL_HISTORY_DUPLICATE", lab)

        # The destructive lab DDL must remain after the explicit @RunLab guard.
        guard = lab.index("IF @RunLab <> 1")
        first_drop = lab.index("DROP TABLE")
        self.assertGreater(first_drop, guard)

    def test_report_records_supplied_baseline_and_does_not_claim_savings(self) -> None:
        report = REPORT.read_text(encoding="utf-8")
        for value in ("695", "5.51 GiB", "2,553,690", "12.99 GiB", "11.87 GiB", "13.92 GiB", "1.92 GiB"):
            self.assertIn(value, report)
        self.assertIn("exact `COMPRESS`", report)
        self.assertIn("bounded Timing lab sample", report)
        self.assertIn("existing C: allocation only", report)
        self.assertIn("EXTERNAL_STORAGE_ADDITION: EXCLUDED_FROM_ACTIVE_PLAN", report)
        self.assertNotIn("VMware", report)
        self.assertNotIn("MDF", report)
        self.assertIn("PROBLEM_SOLVED: NO", report)
        self.assertNotRegex(report, r"Stage\s+15")

    def test_lab_script_has_stable_result_sets_for_comparison(self) -> None:
        lab = LAB.read_text(encoding="utf-8")
        statement_ids = re.findall(r"N'((?:capacity\.)[^']+)'\s+AS\s+statement_id", lab)
        self.assertIn("capacity.timing_storage_option_allocation", statement_ids)
        self.assertIn("capacity.timing_storage_option_peak", statement_ids)
        self.assertIn("capacity.timing_storage_losslessness", statement_ids)

    def test_peak_sampler_is_read_only_and_lab_separated(self) -> None:
        sampler = SAMPLER.read_text(encoding="utf-8")
        self.assertIn("resolve_isolated_lab_connection_settings", sampler)
        self.assertIn("peak_measurement_status", sampler)
        self.assertIn("IN_FLIGHT_SAMPLED", sampler)
        self.assertIn("refuses the production SQL Server", sampler)
        self.assertIn("requires SQL data, log, and tempdb files on non-C:", sampler)
        for token in ("INSERT ", "UPDATE ", "DELETE ", "ALTER ", "DROP ", "DBCC "):
            self.assertNotIn(token, sampler.upper())


if __name__ == "__main__":
    unittest.main()
