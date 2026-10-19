"""The source-faithful Legacy7 ten-slot selection contract.

The production Mac runner asks the existing database selector for the target
for the current bucket. This module is the offline contract used to test the
same deterministic rule and to reject any selector response that would drift
from the preserved seven-target/ten-slot lineage.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping, Sequence

from .domain import LEGACY7_SELECTION_POLICY_VERSION, Target


LEGACY7_SLOT_COUNT = 10
LEGACY7_REQUESTS_PER_HOUR = 12
LEGACY7_REQUESTS_PER_30_DAY_MONTH = 8_640
LEGACY7_REQUESTS_PER_31_DAY_MONTH = 8_928
LEGACY7_SLOT_TO_SAMPLING_TARGET_ID: Mapping[int, int] = {
    1: 2,
    2: 7,
    3: 1,
    4: 4,
    5: 6,
    6: 2,
    7: 7,
    8: 1,
    9: 5,
    10: 3,
}

# The SQL procedure's expected bucket arithmetic is represented as a pure UTC
# rule for offline reconciliation.  The live probe must confirm this rule
# against OBJECT_DEFINITION before reactivation. It uses the integer
# five-minute epoch bucket and has no mutable process counter, so a restart or
# a missed bucket cannot shift the sequence.
LEGACY7_BUCKET_SLOT_RULE = "UTC_EPOCH_BUCKET_MOD_10_PLUS_1"


@dataclass(frozen=True)
class Legacy7Selection:
    logical_due_bucket: int
    sampling_slot: int
    sampling_target_id: int
    selection_policy_version: str = LEGACY7_SELECTION_POLICY_VERSION


def legacy7_sampling_slot_for_bucket(bucket_index: int) -> int:
    """Return the one-based historical slot for a UTC five-minute bucket."""

    return int(bucket_index) % LEGACY7_SLOT_COUNT + 1


def legacy7_sampling_target_id_for_bucket(bucket_index: int) -> int:
    slot = legacy7_sampling_slot_for_bucket(bucket_index)
    return LEGACY7_SLOT_TO_SAMPLING_TARGET_ID[slot]


def legacy7_selection_for_bucket(bucket_index: int) -> Legacy7Selection:
    slot = legacy7_sampling_slot_for_bucket(bucket_index)
    return Legacy7Selection(
        logical_due_bucket=int(bucket_index),
        sampling_slot=slot,
        sampling_target_id=LEGACY7_SLOT_TO_SAMPLING_TARGET_ID[slot],
    )


def select_legacy7_target_for_bucket(targets: Sequence[Target], bucket_index: int) -> Target:
    """Select and annotate exactly one target for an offline heartbeat.

    The returned target carries the selected slot so the persistence adapter
    can write source-faithful lineage.  The input sequence is never reordered
    or mutated.
    """

    selection = legacy7_selection_for_bucket(bucket_index)
    candidates = [
        target
        for target in targets
        if target.legacy_sampling_target_id == selection.sampling_target_id
    ]
    if len(candidates) != 1:
        raise ValueError(
            "Legacy7 fixture must contain exactly one target for each approved SamplingTargetId"
        )
    target = candidates[0]
    if target.legacy_sampling_slots and selection.sampling_slot not in target.legacy_sampling_slots:
        raise ValueError("Legacy7 target is not assigned to the selected historical SamplingSlot")
    return replace(
        target,
        selected_sampling_slot=selection.sampling_slot,
        selection_policy_version=selection.selection_policy_version,
    )


def validate_legacy7_slot_mapping(slot_to_target: Mapping[int, int]) -> None:
    observed = {int(slot): int(target_id) for slot, target_id in slot_to_target.items()}
    if observed != dict(LEGACY7_SLOT_TO_SAMPLING_TARGET_ID):
        raise ValueError("Legacy7 SQL slot mapping differs from the preserved ten-slot contract")


__all__ = [
    "LEGACY7_BUCKET_SLOT_RULE",
    "LEGACY7_REQUESTS_PER_30_DAY_MONTH",
    "LEGACY7_REQUESTS_PER_31_DAY_MONTH",
    "LEGACY7_REQUESTS_PER_HOUR",
    "LEGACY7_SELECTION_POLICY_VERSION",
    "LEGACY7_SLOT_COUNT",
    "LEGACY7_SLOT_TO_SAMPLING_TARGET_ID",
    "Legacy7Selection",
    "legacy7_sampling_slot_for_bucket",
    "legacy7_sampling_target_id_for_bucket",
    "legacy7_selection_for_bucket",
    "select_legacy7_target_for_bucket",
    "validate_legacy7_slot_mapping",
]
