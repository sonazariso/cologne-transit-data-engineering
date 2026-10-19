# Stage 12 Completion Decision

> **Historical engineering record:** This page preserves an earlier design, experiment, incident or point-in-time operational result. For the current project name, final state and safety boundaries, use [Project Status](../PROJECT-STATUS.md). Archived instructions here are **not** authorization to run a collector, apply SQL, or change production resources.


**Decision:** `COMPLETE — APPROVED FIXED 50-STATION PANEL`

**Panel version:** `TIERED50_PANEL_V1`

**Decision date:** 2026-09-30

**Evidence:** [12-50-station-selection-evidence.json](12-50-station-selection-evidence.json)

**Candidate universe:** [12-50-station-candidate-evidence.json](12-50-station-candidate-evidence.json)

## Decision

Approve the fixed 50-parent-station monitoring panel documented in [Stage 12 —
50-Station Selection](12-50-station-selection.md). The panel contains exactly
50 unique validated VRS parent-station identities and preserves all seven
Legacy7 continuity anchors.

The panel is a monitored sample, not the whole Cologne network. Scheduled
stop-event counts describe timetable/service richness only; they are not
passenger demand. The selection rank is not a production collection tier.

Stage 4 semantics were approved against reference snapshot
`VERSION__20260928_0042`; Stage 12 selection used the later exact snapshot
`VERSION__20260930_0053`. Count differences are snapshot evolution, while
Stage 4 identity and classification semantics were re-applied to Stage 12.

## Acceptance gates

All applicable gates passed in the machine-readable evidence:

| Gate | Result |
|---|---|
| Source provenance, current feed hash, size, version, date range, and row counts | PASS |
| Validated Cologne parent-station population and source hierarchy | PASS |
| Exactly 50 unique parent stations | PASS |
| Deterministic algorithm reproducibility and durable exact-input replayability | PASS |
| Complete sanitized candidate-universe replay verification | PASS |
| Selection evidence for every station | PASS |
| Current core-mode representation measured | PASS |
| Geographic concentration reviewed | PASS |
| All valid Legacy7 continuity anchors retained | PASS |
| Bounded MDD compatibility completed for the final panel | PASS |
| No unresolved duplicate final identities | PASS |
| Fixed panel version created and versioning policy approved | PASS |
| No production cadence/tier decisions made | PASS |
| Stage 13 production work and Stage 14 cutover remain NOT STARTED | PASS |
| No secret or raw source payload committed | PASS |

## Evidence summary

- Current feed: `VERSION__20260930_0053`.
- ZIP SHA-256: `288c26f23f91a2069078be82167cfa70eb5b2948e06cfeee9e23584910817147`.
- Candidate population: 864 valid Cologne parent stations; all 864 have
  scheduled child-stop evidence.
- Durable candidate universe: `docs/stages/12-50-station-candidate-evidence.json`,
  exactly 864 unique candidates; exact-source hash and selection policy recorded.
- Offline replay: the authoritative `select_panel` function reproduced the
  same 50 IDs/ranks/phases, first 25 reserves, 25 geographic cells, all seven
  Legacy7 anchors, and the same core-mode coverage outcome without network,
  original ZIP, MDD, or an MDD credential.
- Panel: 50 unique parent stations; 25 of 57 fixed geographic cells represented.
- Legacy7: 7/7 verified and preserved.
- MDD: 50 sequential HTTP attempts; 50 HTTP 200; 50 parse successes; 50 valid
  TRIAS application responses; 50 returned events; 50 source `StopPointRef`
  evidence matches; zero replacements; zero retries; zero raw responses
  persisted.

The correction added no MDD request and did not alter the approved panel.

## Versioning approval

After this decision, `TIERED50_PANEL_V1` membership is fixed. A dynamic station
replacement is not permitted. Any later membership change must create a new
panel version with effective timestamp, reason, and predecessor version, while
retaining historical panel identities and distinguishing reporting eras.

## Explicit non-approval

This decision does not approve Tier A/B/C membership, production cadence,
`NumberOfResults`, concurrency, phase staggering, retry budgets, overlap
claiming, a scheduler, Stage 14 cutover, Power BI artifacts, or any other later
stage. Stage 13 production work remains `NOT STARTED`; pre-existing Stage 13
readiness notes remain conditionally blocked.
