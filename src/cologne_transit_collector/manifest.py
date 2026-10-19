"""Versioned Tiered50 manifest loading and fail-closed validation."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .domain import (
    PANEL_VERSION,
    PHASE_CAPACITIES,
    LIVE_RESEARCH_LEGACY_ID,
    LIVE_RESEARCH_STATION_NAME,
    LIVE_RESEARCH_TARGET_ID,
    TIERED50_SELECTION_POLICY_VERSION,
    TIER_CADENCE_MINUTES,
    TIER_COUNTS,
    TIER_NUMBER_OF_RESULTS,
    SamplingEra,
    Target,
)


LEGACY7_IDS = frozenset(
    {
        "de:05315:11110",
        "de:05315:11201",
        "de:05315:12711",
        "de:05315:14201",
        "de:05315:16601",
        "de:05315:17311",
        "de:05315:19201",
    }
)

LEGACY7_SAMPLING_TARGET_IDS = {
    "de:05315:11110": 1,  # Köln Heumarkt
    "de:05315:11201": 2,  # Köln Hbf
    "de:05315:12711": 3,  # Köln Rodenkirchen Bf
    "de:05315:14201": 4,  # Köln Bf Ehrenfeld
    "de:05315:16601": 5,  # Köln Worringen S-Bahn
    "de:05315:17311": 6,  # Köln Porz Markt
    "de:05315:19201": 7,  # Köln Bf Mülheim
}


class ManifestValidationError(ValueError):
    """Raised when the approved runtime manifest is incomplete or altered."""


@dataclass(frozen=True)
class RuntimeManifest:
    panel_version: str
    status: str
    sampling_era: SamplingEra
    activation_status: str
    cutover_timestamp_utc: str | None
    targets: tuple[Target, ...]
    source_path: Path
    stage13_evidence_path: Path | None = None

    def target_by_id(self, source_parent_station_id: str) -> Target:
        for target in self.targets:
            if target.source_parent_station_id == source_parent_station_id:
                return target
        raise KeyError(source_parent_station_id)

    def live_research_target(self) -> Target:
        """Return the fixed, manifest-owned S78 Heumarkt target."""

        target = self.target_by_id(LIVE_RESEARCH_TARGET_ID)
        if (
            target.station_name != LIVE_RESEARCH_STATION_NAME
            or target.legacy_sampling_target_id != LIVE_RESEARCH_LEGACY_ID
            or target.tier != "A"
            or target.cadence_minutes != 5
            or target.number_of_results != 20
        ):
            raise ManifestValidationError(
                "approved manifest Heumarkt row does not match the S78 pilot contract"
            )
        return target

    @property
    def legacy7_targets(self) -> tuple[Target, ...]:
        return tuple(target for target in self.targets if target.is_legacy7_member)

    @property
    def new_tiered50_targets(self) -> tuple[Target, ...]:
        return tuple(target for target in self.targets if not target.is_legacy7_member)

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        stage13_evidence_path: str | Path | None = None,
        validate: bool = True,
    ) -> "RuntimeManifest":
        manifest_path = Path(path).resolve()
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestValidationError(f"cannot load runtime manifest: {manifest_path}") from exc

        targets: list[Target] = []
        for raw in payload.get("targets", []):
            try:
                targets.append(
                    Target(
                        panel_version=str(raw["panel_version"]),
                        source_parent_station_id=str(raw["source_parent_station_id"]),
                        station_name=str(raw["station_display_name"]),
                        tier=str(raw["tier"]),
                        cadence_minutes=int(raw["cadence_minutes"]),
                        number_of_results=int(raw["number_of_results"]),
                        phase_index=int(raw["phase_index"]),
                        phase_count=int(raw["phase_count"]),
                        continuity_classification=str(raw["continuity_classification"]),
                        sampling_era=SamplingEra(str(raw.get("sampling_era", "Tiered50"))),
                        production_sampling_target_id=raw.get("sampling_target_id"),
                        legacy_sampling_target_id=(
                            int(raw["legacy_sampling_target_id"])
                            if raw.get("legacy_sampling_target_id") is not None
                            else None
                        ),
                        phase_capacity=(
                            int(raw["phase_capacity"])
                            if raw.get("phase_capacity") is not None
                            else None
                        ),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ManifestValidationError("invalid target row in runtime manifest") from exc

        evidence_path = (
            Path(stage13_evidence_path).resolve()
            if stage13_evidence_path is not None
            else _resolve_evidence_path(manifest_path, payload.get("stage13_evidence_path"))
        )
        manifest = cls(
            panel_version=str(payload.get("panel_version", "")),
            status=str(payload.get("manifest_status", "")),
            sampling_era=SamplingEra(str(payload.get("sampling_era", "Tiered50"))),
            activation_status=str(payload.get("activation_status", "")),
            cutover_timestamp_utc=payload.get("cutover_timestamp_utc"),
            targets=tuple(targets),
            source_path=manifest_path,
            stage13_evidence_path=evidence_path,
        )
        if validate:
            manifest.validate()
            if evidence_path is not None:
                manifest.validate_against_stage13(evidence_path)
        return manifest

    def validate(self) -> None:
        if self.panel_version != PANEL_VERSION:
            raise ManifestValidationError("panel version is not the approved TIERED50_PANEL_V1")
        if len(self.targets) != 50:
            raise ManifestValidationError("runtime manifest must contain exactly 50 targets")
        if self.sampling_era is not SamplingEra.TIERED50:
            raise ManifestValidationError("runtime manifest sampling era must be Tiered50")
        if self.activation_status != "DEFERRED_NOT_ACTIVE":
            raise ManifestValidationError("Tiered50 must remain inactive in this iteration")
        if self.cutover_timestamp_utc is not None:
            raise ManifestValidationError("real Tiered50 cutover timestamp must not be fabricated")

        ids = [target.source_parent_station_id for target in self.targets]
        if len(set(ids)) != 50:
            raise ManifestValidationError("runtime manifest contains duplicate source parent identities")
        if any(target.panel_version != PANEL_VERSION for target in self.targets):
            raise ManifestValidationError("target panel version differs from manifest panel version")
        if set(ids) & LEGACY7_IDS != LEGACY7_IDS:
            raise ManifestValidationError("all seven Legacy7 source identities must be present")
        legacy = [target for target in self.targets if target.is_legacy7_member]
        if len(legacy) != 7:
            raise ManifestValidationError("runtime manifest must contain exactly seven Legacy7 members")
        if any(target.source_parent_station_id not in LEGACY7_IDS for target in legacy):
            raise ManifestValidationError("Legacy7 classification is not aligned with approved identities")
        if any(target.source_parent_station_id in LEGACY7_IDS for target in self.targets if not target.is_legacy7_member):
            raise ManifestValidationError("a Legacy7 identity is classified as a new target")
        if any(target.production_sampling_target_id for target in self.new_tiered50_targets):
            raise ManifestValidationError("new targets must not receive future production SamplingTargetIds")
        legacy_ids = {
            target.source_parent_station_id: target.legacy_sampling_target_id
            for target in self.targets
            if target.legacy_sampling_target_id is not None
        }
        if len(legacy_ids) != 7 or set(legacy_ids.values()) != set(range(1, 8)):
            raise ManifestValidationError("exactly seven Legacy7 lineage IDs 1 through 7 are required")
        if legacy_ids != LEGACY7_SAMPLING_TARGET_IDS:
            raise ManifestValidationError("Legacy7 sampling-target lineage mapping is not exact")
        if any(target.legacy_sampling_target_id is not None for target in self.new_tiered50_targets):
            raise ManifestValidationError("new Tiered50 targets must not receive Legacy7 lineage IDs")

        tier_counts: dict[str, int] = {}
        for target in self.targets:
            tier_counts[target.tier] = tier_counts.get(target.tier, 0) + 1
            if target.tier not in TIER_COUNTS:
                raise ManifestValidationError(f"unknown tier: {target.tier}")
            if target.cadence_minutes != TIER_CADENCE_MINUTES[target.tier]:
                raise ManifestValidationError("target cadence differs from approved tier cadence")
            if target.number_of_results != TIER_NUMBER_OF_RESULTS[target.tier]:
                raise ManifestValidationError("target NumberOfResults differs from approved tier policy")
            capacities = PHASE_CAPACITIES[target.tier]
            if target.phase_count != len(capacities):
                raise ManifestValidationError("target phase count differs from approved tier phase count")
            if not 0 <= target.phase_index < target.phase_count:
                raise ManifestValidationError("target phase index is outside its phase count")
            if target.phase_capacity is not None and target.phase_capacity != capacities[target.phase_index]:
                raise ManifestValidationError("target phase capacity differs from approved persisted capacity")
        if tier_counts != dict(TIER_COUNTS):
            raise ManifestValidationError(f"tier counts differ from approved 8/20/22: {tier_counts}")

        for tier, capacities in PHASE_CAPACITIES.items():
            observed = [0] * len(capacities)
            for target in self.targets:
                if target.tier == tier:
                    observed[target.phase_index] += 1
            if tuple(observed) != capacities:
                raise ManifestValidationError(
                    f"persisted {tier} phase capacities differ: {tuple(observed)} != {capacities}"
                )

    def validate_against_stage13(self, evidence_path: str | Path) -> None:
        """Compare persisted phase indexes with Stage 13 evidence; never recompute them."""

        path = Path(evidence_path).resolve()
        try:
            evidence = json.loads(path.read_text(encoding="utf-8"))
            assignments = evidence["phase_staggering"]["phase_assignments"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ManifestValidationError("Stage 13 phase evidence is unavailable") from exc
        expected = {
            str(row["source_parent_station_id"]): (
                str(row["production_tier"]),
                int(row["phase_index"]),
                int(row["phase_count"]),
            )
            for row in assignments
        }
        actual = {
            target.source_parent_station_id: (target.tier, target.phase_index, target.phase_count)
            for target in self.targets
        }
        if len(expected) != 50 or expected != actual:
            raise ManifestValidationError(
                "runtime manifest phases do not exactly match persisted Stage 13 evidence"
            )

    def bind_production_sampling_target_ids(
        self,
        sampling_target_ids: Mapping[str, int],
    ) -> "RuntimeManifest":
        """Bind live SQL identities without changing approved membership/phases.

        The repository manifest intentionally contains no future production IDs.
        A live adapter may bind IDs read back from SQL, but the station set and
        all policy fields still come exclusively from this approved manifest.
        """

        expected = {target.source_parent_station_id for target in self.targets}
        observed = {str(key) for key in sampling_target_ids}
        if observed != expected:
            missing = sorted(expected - observed)
            extra = sorted(observed - expected)
            raise ManifestValidationError(
                f"production target identity binding differs from the approved manifest; "
                f"missing={missing}, extra={extra}"
            )
        bound: list[Target] = []
        for target in self.targets:
            try:
                identity = int(sampling_target_ids[target.source_parent_station_id])
            except (KeyError, TypeError, ValueError) as exc:
                raise ManifestValidationError(
                    "production SamplingTargetId values must be positive integers"
                ) from exc
            if identity <= 0:
                raise ManifestValidationError("production SamplingTargetId values must be positive")
            bound.append(
                replace(
                    target,
                    production_sampling_target_id=str(identity),
                    selection_policy_version=TIERED50_SELECTION_POLICY_VERSION,
                )
            )
        result = replace(self, targets=tuple(bound))
        result.validate_production_binding()
        return result

    def validate_production_binding(self) -> None:
        """Validate SQL-bound IDs while preserving the inactive source manifest."""

        # Validate the source manifest contract after removing runtime-only IDs.
        source_targets = tuple(
            replace(target, production_sampling_target_id=None)
            for target in self.targets
        )
        replace(
            self,
            activation_status="DEFERRED_NOT_ACTIVE",
            cutover_timestamp_utc=None,
            targets=source_targets,
        ).validate()

        ids: list[int] = []
        for target in self.targets:
            try:
                identity = int(target.production_sampling_target_id or 0)
            except (TypeError, ValueError) as exc:
                raise ManifestValidationError(
                    "bound production SamplingTargetId is not an integer"
                ) from exc
            if identity <= 0:
                raise ManifestValidationError("bound production SamplingTargetId must be positive")
            ids.append(identity)
        if len(set(ids)) != 50 or set(ids) != set(range(1, 51)):
            raise ManifestValidationError(
                "bound production SamplingTargetIds must be exactly 1 through 50"
            )
        legacy_ids = {
            target.source_parent_station_id: int(target.production_sampling_target_id or 0)
            for target in self.legacy7_targets
        }
        if legacy_ids != LEGACY7_SAMPLING_TARGET_IDS:
            raise ManifestValidationError(
                "bound production IDs do not preserve Legacy7 identities 1 through 7"
            )
        if any(
            int(target.production_sampling_target_id or 0) <= 7
            for target in self.new_tiered50_targets
        ):
            raise ManifestValidationError(
                "new Tiered50 targets must use production identities 8 through 50"
            )


def _resolve_evidence_path(manifest_path: Path, value: Any) -> Path | None:
    if not value:
        return None
    candidate = Path(str(value))
    if not candidate.is_absolute():
        candidate = manifest_path.parent.parent / candidate
    return candidate.resolve()


def load_default_manifest(repository_root: str | Path) -> RuntimeManifest:
    root = Path(repository_root).resolve()
    return RuntimeManifest.load(
        root / "config" / "tiered50_runtime_manifest.json",
        stage13_evidence_path=root / "docs" / "stages" / "13-tiered-collector-readiness-evidence.json",
    )


def manifest_rows(manifest: RuntimeManifest) -> tuple[Mapping[str, Any], ...]:
    """Return a stable non-secret view suitable for plan output."""

    return tuple(
        {
            "panel_version": target.panel_version,
            "source_parent_station_id": target.source_parent_station_id,
            "station_display_name": target.station_name,
            "tier": target.tier,
            "cadence_minutes": target.cadence_minutes,
            "number_of_results": target.number_of_results,
            "phase_index": target.phase_index,
            "phase_count": target.phase_count,
            "continuity_classification": target.continuity_classification,
            "legacy_sampling_target_id": target.legacy_sampling_target_id,
        }
        for target in manifest.targets
    )


def validate_phase_rows(rows: Iterable[Mapping[str, Any]], expected: Mapping[str, int]) -> None:
    """Generic helper for tests that validates persisted phases without deriving them."""

    actual = {str(row["source_parent_station_id"]): int(row["phase_index"]) for row in rows}
    if actual != dict(expected):
        raise ManifestValidationError("phase rows differ from their approved persisted values")
