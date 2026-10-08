from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cologne_transit_collector.domain import (  # noqa: E402
    ABSOLUTE_PROVIDER_CEILING,
    AUTOMATIC_RETRY_PROTECTION_BOUNDARY,
    NORMAL_OPERATIONAL_MAXIMUM,
    approved_quota_boundary,
)
from cologne_transit_collector.quota_baseline import (  # noqa: E402
    QuotaBaselineError,
    provider_period_month_key,
    reconcile_provider_evidence,
    validate_stored_baseline_row,
)
from package_source_zip import git_visible_paths  # noqa: E402


NOW = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)


def _local_usage() -> dict[str, object]:
    return {
        "confirmed_through_cutoff": 3,
        "unknown_through_cutoff": 2,
        "pending_through_cutoff": 1,
        "confirmed_after_cutoff": 4,
        "unknown_after_cutoff": 1,
        "pending_after_cutoff": 2,
    }


def _evidence(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "month_key": "2026-10",
        "accounting_model": "REFRESHED_PROVIDER_ACCOUNTING",
        "includes_all_shared_consumers": True,
        "operating_model_evidence": "Provider dashboard export covers every consumer of the shared credential.",
        "refresh_commitment": "Refresh before every scheduled activation and before stale_after_utc.",
        "local_usage_included_through_utc": "2026-10-08T08:00:00Z",
        "stale_after_utc": "2026-10-08T11:00:00Z",
        "reconciliation_key": "provider-october-2026-report-20261008T090000Z",
        "explicit_shared_consumer_reserved_attempts": 5,
        "conservative_unknown_attempts": 6,
        "unexplained_discrepancy_attempts": 0,
        "provider": {
            "source_description": "Provider usage dashboard export",
            "source_reference": "provider-report-2026-10-08T09-00Z",
            "measured_at_utc": "2026-10-08T09:00:00Z",
            "usage_through_utc": "2026-10-08T08:00:00Z",
            "period_timezone": "Europe/Berlin",
            "period_start_utc": "2026-10-01T00:00:00+02:00",
            "period_end_utc": "2026-11-01T00:00:00+01:00",
            "cumulative_attempts": 100,
            "confirmed_attempts": 90,
            "unknown_attempts": 10,
            "includes_local_usage": False,
        },
    }
    document.update(overrides)
    return document


class QuotaBaselineTests(unittest.TestCase):
    def test_provider_period_key_uses_verified_timezone_at_dst_boundary(self) -> None:
        self.assertEqual(
            provider_period_month_key(
                datetime(2026, 10, 31, 22, 59, tzinfo=timezone.utc),
                timezone_name="Europe/Berlin",
            ),
            "2026-10",
        )
        self.assertEqual(
            provider_period_month_key(
                datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc),
                timezone_name="Europe/Berlin",
            ),
            "2026-11",
        )

    def test_reconciliation_separates_provider_and_local_cutoff_usage(self) -> None:
        result = reconcile_provider_evidence(
            _evidence(),
            local_usage=_local_usage(),
            now_utc=NOW,
        )
        self.assertEqual(result["confirmed_provider_attempts"], 97)
        self.assertEqual(result["conservative_unknown_attempts"], 19)
        self.assertEqual(result["explicit_shared_consumer_reserved_attempts"], 8)
        self.assertEqual(result["baseline_component_total"], 124)
        self.assertEqual(result["current_conservative_accounted_total"], 124)
        self.assertEqual(result["provider_period_timezone"], "Europe/Berlin")
        self.assertEqual(len(result["source_evidence_hash"]), 64)

    def test_provider_cutoff_before_local_cutoff_is_persisted_as_one_baseline(self) -> None:
        local_usage = {
            "confirmed_through_cutoff": 0,
            "unknown_through_cutoff": 0,
            "pending_through_cutoff": 0,
            "confirmed_after_cutoff": 7,
            "unknown_after_cutoff": 0,
            "pending_after_cutoff": 0,
        }
        result = reconcile_provider_evidence(
            _evidence(
                explicit_shared_consumer_reserved_attempts=0,
                conservative_unknown_attempts=0,
            ),
            local_usage=local_usage,
            now_utc=NOW,
        )
        self.assertEqual(result["current_conservative_accounted_total"], 107)
        self.assertEqual(
            result["baseline_component_total"],
            result["confirmed_provider_attempts"]
            + result["conservative_unknown_attempts"]
            + result["explicit_shared_consumer_reserved_attempts"],
        )
        self.assertEqual(result["confirmed_provider_attempts"], 97)
        self.assertEqual(result["conservative_unknown_attempts"], 10)
        self.assertEqual(result["explicit_shared_consumer_reserved_attempts"], 0)

    def test_provider_includes_local_usage_only_through_provider_cutoff(self) -> None:
        document = _evidence(
            local_usage_included_through_utc="2026-10-08T09:00:00Z",
            explicit_shared_consumer_reserved_attempts=0,
            conservative_unknown_attempts=0,
        )
        provider = dict(document["provider"])  # type: ignore[arg-type]
        provider["includes_local_usage"] = True
        document["provider"] = provider
        local_usage = {
            "confirmed_through_cutoff": 3,
            "unknown_through_cutoff": 2,
            "pending_through_cutoff": 0,
            "confirmed_after_cutoff": 4,
            "unknown_after_cutoff": 1,
            "pending_after_cutoff": 2,
        }
        result = reconcile_provider_evidence(document, local_usage=local_usage, now_utc=NOW)
        self.assertEqual(result["baseline_component_total"], 107)
        self.assertEqual(result["current_conservative_accounted_total"], 107)
        self.assertEqual(result["confirmed_provider_attempts"], 94)
        self.assertEqual(result["conservative_unknown_attempts"], 11)
        self.assertEqual(result["explicit_shared_consumer_reserved_attempts"], 2)

    def test_equal_provider_and_local_cutoff_does_not_create_post_cutoff_usage(self) -> None:
        local_usage = {
            "confirmed_through_cutoff": 3,
            "unknown_through_cutoff": 2,
            "pending_through_cutoff": 0,
            "confirmed_after_cutoff": 0,
            "unknown_after_cutoff": 0,
            "pending_after_cutoff": 0,
        }
        result = reconcile_provider_evidence(
            _evidence(
                local_usage_included_through_utc="2026-10-08T08:00:00Z",
                explicit_shared_consumer_reserved_attempts=0,
                conservative_unknown_attempts=0,
            ),
            local_usage=local_usage,
            now_utc=NOW,
        )
        self.assertEqual(result["baseline_component_total"], 105)
        self.assertEqual(result["current_conservative_accounted_total"], 105)

    def test_provider_included_local_pending_permit_is_a_double_counting_blocker(self) -> None:
        document = _evidence()
        provider = dict(document["provider"])  # type: ignore[arg-type]
        provider["includes_local_usage"] = True
        document["provider"] = provider
        with self.assertRaisesRegex(QuotaBaselineError, "double counting"):
            reconcile_provider_evidence(document, local_usage=_local_usage(), now_utc=NOW)

    def test_local_cutoff_must_cover_provider_cutoff_and_stay_inside_period(self) -> None:
        with self.assertRaisesRegex(QuotaBaselineError, "cover the provider usage cutoff"):
            reconcile_provider_evidence(
                _evidence(local_usage_included_through_utc="2026-10-08T07:59:59Z"),
                local_usage=_local_usage(),
                now_utc=NOW,
            )

    def test_stored_baseline_fails_closed_after_stale_deadline(self) -> None:
        result = reconcile_provider_evidence(_evidence(), local_usage=_local_usage(), now_utc=NOW)
        row = {
            "MonthKey": result["month_key"],
            "ProviderQuotaCeiling": result["provider_quota_ceiling"],
            "NormalOperationalMaximum": result["normal_operational_maximum"],
            "RetryProtectionBoundary": result["retry_protection_boundary"],
            "ConfirmedProviderAttempts": result["confirmed_provider_attempts"],
            "ConservativeUnknownAttempts": result["conservative_unknown_attempts"],
            "ExplicitSharedConsumerReservedAttempts": result["explicit_shared_consumer_reserved_attempts"],
            "AccountingStatus": result["accounting_status"],
            "IncludesAllSharedConsumers": result["includes_all_shared_consumers"],
            "BaselineAsOfUtc": result["baseline_as_of_utc"],
            "LocalUsageIncludedThroughUtc": result["local_usage_included_through_utc"],
            "SourceDescription": result["source_description"],
            "AccountingModel": result["accounting_model"],
            "ProviderMeasurementCutoffUtc": result["provider_measurement_cutoff_utc"],
            "StaleAfterUtc": result["stale_after_utc"],
            "SourceEvidenceHash": result["source_evidence_hash"],
            "ReconciliationKey": result["reconciliation_key"],
            "ProviderPeriodTimezone": result["provider_period_timezone"],
            "ProviderPeriodStartUtc": result["provider_period_start_utc"],
            "ProviderPeriodEndUtc": result["provider_period_end_utc"],
        }
        self.assertEqual(validate_stored_baseline_row(row, at_utc=NOW)[0], True)
        self.assertEqual(
            validate_stored_baseline_row(
                row,
                at_utc=datetime(2026, 10, 8, 11, 0, tzinfo=timezone.utc),
            )[0],
            False,
        )

    def test_current_month_and_secret_shaped_evidence_are_required(self) -> None:
        old_month = _evidence(month_key="2026-09")
        with self.assertRaisesRegex(QuotaBaselineError, "does not map to the supplied month_key|current calendar month"):
            reconcile_provider_evidence(old_month, local_usage=_local_usage(), now_utc=NOW)
        secret_evidence = _evidence(provider={**_evidence()["provider"], "api_key": "redacted"})  # type: ignore[arg-type]
        with self.assertRaisesRegex(QuotaBaselineError, "credential-shaped"):
            reconcile_provider_evidence(secret_evidence, local_usage=_local_usage(), now_utc=NOW)

    def test_approved_boundary_cannot_be_raised_by_caller(self) -> None:
        self.assertEqual(
            approved_quota_boundary("INITIAL", ABSOLUTE_PROVIDER_CEILING),
            NORMAL_OPERATIONAL_MAXIMUM,
        )
        self.assertEqual(
            approved_quota_boundary("RETRY", ABSOLUTE_PROVIDER_CEILING),
            AUTOMATIC_RETRY_PROTECTION_BOUNDARY,
        )

    def test_source_tree_fallback_does_not_require_git_metadata(self) -> None:
        with tempfile.TemporaryDirectory(prefix="source-tree-no-git-") as directory:
            root = Path(directory).resolve()
            (root / "README.md").write_text("review", encoding="utf-8")
            (root / ".env").write_text("secret", encoding="utf-8")
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("metadata", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "module.py").write_text("pass", encoding="utf-8")
            visible = {path.relative_to(root).as_posix() for path in git_visible_paths(root)}
        self.assertEqual(visible, {"README.md", "src/module.py"})


if __name__ == "__main__":
    unittest.main()
