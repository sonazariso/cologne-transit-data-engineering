from __future__ import annotations

import json
import random
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

import stage_12_50_station_selection as stage12  # noqa: E402


class Stage12StationSelectionTests(unittest.TestCase):
    def _candidate(
        self,
        station_id: str,
        *,
        events: int,
        routes: int,
        modes: list[str],
        cell: str,
        latitude: float = 50.9,
        longitude: float = 6.9,
    ) -> dict[str, object]:
        return {
            "source_parent_station_id": station_id,
            "station_name": station_id,
            "latitude": latitude,
            "longitude": longitude,
            "geographic_cell": cell,
            "parent_location_type": "1",
            "source_identity_validated": True,
            "valid_source_hierarchy": True,
            "static_child_stop_count": 2,
            "static_child_stop_ids": [f"{station_id}:1", f"{station_id}:2"],
            "scheduled_event_count": events,
            "distinct_route_count": routes,
            "route_ids": [f"route-{index}" for index in range(routes)],
            "observed_mode_set": modes,
            "distinct_approved_mode_count": len(set(modes) & set(stage12.CORE_MODES)),
            "active_child_stop_count": 2,
            "scheduled_service_day_count": 100,
            "service_id_count": 1,
            "scheduled_trip_count": events,
            "event_count_by_mode": {mode: events for mode in modes},
            "route_ids_by_mode": {mode: [f"route-{index}" for index in range(routes)] for mode in modes},
            "service_ids": ["service-1"],
            "active_service_dates": ["2026-09-30"],
            "continuity_anchor": station_id in stage12.LEGACY7_IDS,
            "mdd_compatibility_status": "NOT_RUN",
        }

    def _fixture_candidates(self) -> list[dict[str, object]]:
        core_modes = list(stage12.CORE_MODES)
        candidates = []
        for index, (_name, station_id) in enumerate(stage12.LEGACY7):
            candidates.append(
                self._candidate(
                    station_id,
                    events=1000 - index,
                    routes=5,
                    modes=[core_modes[index % len(core_modes)]],
                    cell=f"anchor-{index}",
                )
            )
        for index in range(60):
            candidates.append(
                self._candidate(
                    f"de:05315:test:{index:03d}",
                    events=500 - index,
                    routes=4,
                    modes=[core_modes[index % len(core_modes)]],
                    cell=f"cell-{index:02d}",
                    latitude=50.8 + index / 1000,
                    longitude=6.8 + index / 1000,
                )
            )
        return candidates

    def test_selection_is_deterministic_and_has_exactly_50_unique_parents(self) -> None:
        candidates = self._fixture_candidates()
        first, first_reserves, metadata = stage12.select_panel(candidates)
        shuffled = list(candidates)
        random.Random(42).shuffle(shuffled)
        second, second_reserves, second_metadata = stage12.select_panel(shuffled)

        self.assertEqual(
            [row["source_parent_station_id"] for row in first],
            [row["source_parent_station_id"] for row in second],
        )
        self.assertEqual(len(first), 50)
        self.assertEqual(len({row["source_parent_station_id"] for row in first}), 50)
        self.assertEqual([row["SelectionRank"] for row in first], list(range(1, 51)))
        self.assertEqual(
            {row["source_parent_station_id"] for row in first[:7]},
            set(stage12.LEGACY7_IDS),
        )
        self.assertEqual(metadata, second_metadata)
        self.assertEqual(
            [row["source_parent_station_id"] for row in first_reserves[:10]],
            [row["source_parent_station_id"] for row in second_reserves[:10]],
        )
        self.assertGreaterEqual(metadata["selected_geographic_cells"], metadata["geographic_cell_definition"]["target_distinct_cells"])

    def test_missing_legacy_anchor_blocks_selection(self) -> None:
        candidates = self._fixture_candidates()
        candidates = [row for row in candidates if row["source_parent_station_id"] != stage12.LEGACY7_IDS[0]]
        with self.assertRaises(stage12.Stage12Blocked):
            stage12.select_panel(candidates)

    def test_evidence_artifact_is_sanitized_and_all_gates_pass(self) -> None:
        artifact = REPOSITORY_ROOT / "docs/stages/12-50-station-selection-evidence.json"
        self.assertTrue(artifact.is_file())
        evidence = json.loads(artifact.read_text(encoding="utf-8"))
        stage12.validate_evidence(evidence)
        self.assertEqual(evidence["status"], "COMPLETE — APPROVED FIXED 50-STATION PANEL")
        self.assertEqual(evidence["panel_version"], "TIERED50_PANEL_V1")
        self.assertEqual(len(evidence["panel"]), 50)
        self.assertEqual(evidence["mdd_compatibility_probe"]["http_attempts_consumed"], 50)
        self.assertEqual(evidence["mdd_compatibility_probe"]["final_panel_probe_count"], 50)
        self.assertTrue(all(gate["passed"] for gate in evidence["acceptance_gates"]))
        rendered = artifact.read_text(encoding="utf-8").lower()
        self.assertNotIn("x-api-key:", rendered)
        self.assertNotIn("mdd-key.txt", rendered)


if __name__ == "__main__":
    unittest.main()
