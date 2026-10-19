from __future__ import annotations

import json
import sys
import unittest
from collections import Counter
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.domain import LEGACY7_SELECTION_POLICY_VERSION  # noqa: E402
from cologne_transit_collector.legacy7_schedule import (  # noqa: E402
    LEGACY7_BUCKET_SLOT_RULE,
    LEGACY7_REQUESTS_PER_30_DAY_MONTH,
    LEGACY7_REQUESTS_PER_31_DAY_MONTH,
    LEGACY7_REQUESTS_PER_HOUR,
    LEGACY7_SLOT_TO_SAMPLING_TARGET_ID,
    legacy7_sampling_slot_for_bucket,
    select_legacy7_target_for_bucket,
)
from cologne_transit_collector.manifest import load_default_manifest  # noqa: E402
from cologne_transit_collector.schedule import due_targets  # noqa: E402


class Legacy7ScheduleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_default_manifest(ROOT)
        fixture = json.loads(
            (ROOT / "tests" / "fixtures" / "legacy7_sql_schedule.json").read_text(encoding="utf-8")
        )
        cls.slot_mapping = {int(slot): int(target_id) for slot, target_id in fixture["slot_to_sampling_target_id"].items()}
        cls.targets = tuple(
            replace(
                target,
                legacy_sampling_slots=tuple(
                    slot for slot, target_id in cls.slot_mapping.items() if target_id == target.legacy_sampling_target_id
                ),
            )
            for target in cls.manifest.legacy7_targets
        )

    def test_fixture_preserves_exact_slots_and_target_ids(self) -> None:
        self.assertEqual(self.slot_mapping, dict(LEGACY7_SLOT_TO_SAMPLING_TARGET_ID))
        self.assertEqual(sorted(self.slot_mapping.values()), [1, 1, 2, 2, 3, 4, 5, 6, 7, 7])
        self.assertEqual(sorted(target.legacy_sampling_target_id for target in self.targets), list(range(1, 8)))
        self.assertEqual(LEGACY7_BUCKET_SLOT_RULE, "UTC_EPOCH_BUCKET_MOD_10_PLUS_1")

    def test_one_target_is_due_for_each_heartbeat_over_one_ten_slot_cycle(self) -> None:
        start_bucket = 6_000_000
        due_ids = []
        slots = []
        for offset in range(10):
            bucket = start_bucket + offset
            due = due_targets(self.targets, bucket)
            self.assertEqual(len(due), 1)
            due_ids.append(due[0].legacy_sampling_target_id)
            slots.append(legacy7_sampling_slot_for_bucket(bucket))
        self.assertEqual(slots, list(range(1, 11)))
        self.assertEqual(due_ids, [self.slot_mapping[slot] for slot in range(1, 11)])
        self.assertEqual(len(due_ids), 10)
        self.assertEqual(due_ids.count(1), 2)
        self.assertEqual(due_ids.count(2), 2)
        self.assertEqual(due_ids.count(7), 2)
        self.assertEqual(due_ids.count(3), 1)
        self.assertEqual(due_ids.count(4), 1)
        self.assertEqual(due_ids.count(5), 1)
        self.assertEqual(due_ids.count(6), 1)

        names = Counter(
            due[0].station_name
            for due in (due_targets(self.targets, start_bucket + offset) for offset in range(10))
        )
        self.assertEqual(names["Köln Hbf"], 2)
        self.assertEqual(names["Köln Bf Mülheim"], 2)
        self.assertEqual(names["Köln Heumarkt"], 2)
        self.assertEqual(names["Köln Bf Ehrenfeld"], 1)
        self.assertEqual(names["Köln Porz Markt"], 1)
        self.assertEqual(names["Köln Worringen S-Bahn"], 1)
        self.assertEqual(names["Köln Rodenkirchen Bf"], 1)

    def test_selected_heartbeat_carries_slot_and_policy_lineage(self) -> None:
        selected = select_legacy7_target_for_bucket(self.targets, 6_000_000)
        self.assertEqual(selected.legacy_sampling_target_id, 2)
        self.assertEqual(selected.selected_sampling_slot, 1)
        self.assertEqual(selected.selection_policy_version, LEGACY7_SELECTION_POLICY_VERSION)
        self.assertEqual(selected.number_of_results, self.manifest.target_by_id("de:05315:11201").number_of_results)
        self.assertEqual(len(due_targets((selected,), 6_000_000)), 1)

    def test_skipped_bucket_is_not_replayed_and_restart_keeps_selection(self) -> None:
        first = select_legacy7_target_for_bucket(self.targets, 6_000_000)
        skipped = select_legacy7_target_for_bucket(self.targets, 6_000_002)
        restarted = select_legacy7_target_for_bucket(self.targets, 6_000_000)
        self.assertEqual(first.selected_sampling_slot, 1)
        self.assertEqual(skipped.selected_sampling_slot, 3)
        self.assertEqual(restarted.selected_sampling_slot, first.selected_sampling_slot)
        self.assertEqual(restarted.legacy_sampling_target_id, first.legacy_sampling_target_id)
        self.assertNotEqual(skipped.selected_sampling_slot, 2)

    def test_all_seven_targets_cannot_become_due_in_one_legacy7_heartbeat(self) -> None:
        for bucket in range(6_000_000, 6_000_010):
            self.assertEqual(len(due_targets(self.targets, bucket)), 1)

    def test_nominal_legacy7_volume_is_not_tiered50_volume(self) -> None:
        self.assertEqual(LEGACY7_REQUESTS_PER_HOUR, 12)
        self.assertEqual(LEGACY7_REQUESTS_PER_30_DAY_MONTH, 8_640)
        self.assertEqual(LEGACY7_REQUESTS_PER_31_DAY_MONTH, 8_928)


if __name__ == "__main__":
    unittest.main()
