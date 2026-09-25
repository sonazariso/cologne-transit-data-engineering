# Decision Log

This file indexes approved project-level decisions. Detailed records may later be added under `docs/decisions/` when a decision needs alternatives/rationale captured separately.

| ID | Date | Decision | Status |
|---|---|---|---|
| D-001 | 2026-09-25 | New project name: **Cologne Transport Intelligence** | Approved |
| D-002 | 2026-09-25 | New GitHub repository: `sonazariso/cologne-transit-intelligence` | Approved |
| D-003 | 2026-09-25 | Legacy repository remains reference/evidence and is not bulk-copied | Approved |
| D-004 | 2026-09-25 | macOS working tree is authoritative; Windows uses VMware shared folder | Approved |
| D-005 | 2026-09-25 | Project objective is management-oriented System Performance BI | Approved |
| D-006 | 2026-09-25 | Actual Delay using validated `RecordedAtTime` evidence is the primary timing objective | Approved |
| D-007 | 2026-09-25 | Estimated timing is secondary and may support prediction-accuracy analysis | Approved |
| D-008 | 2026-09-25 | Core analytical geography is performance inside Cologne, not whole routes merely passing through | Approved |
| D-009 | 2026-09-25 | ICE/IC/NightJet/FlixTrain/Thalys are outside core KPI scope for now | Approved |
| D-010 | 2026-09-25 | No new MDD polling before Stage 1 quota/source review | Approved |
| D-011 | 2026-09-25 | Every stage requires an explicit approval gate | Approved |
| D-012 | 2026-09-25 | TRIAS 1.2 is the semantic contract for the currently audited MDD interface; see the [Stage 02 source-contract package](source-contracts/README.md) | Approved |
| D-013 | 2026-09-25 | `Estimated Delay` may be calculated only as `EstimatedTime - TimetabledTime` and must be labelled explicitly; see the [Stage 02 response contract](source-contracts/mdd-trias-response-contract.md) | Approved |
| D-014 | 2026-09-25 | Actual Delay is not approved until a validated actual-time source is observed and sufficiently validated; see the [Stage 02 field-retention matrix](source-contracts/mdd-trias-field-retention-matrix.md) | Approved |
| D-015 | 2026-09-25 | `RecordedAtTime` is the TRIAS actual/recorded-time candidate, but was not observed in Stage 02 controlled evidence; see the [Stage 02 audit](stages/02-mdd-trias-source-contract-audit.md) | Approved |
| D-016 | 2026-09-25 | `NotServicedStop` is not equivalent to journey cancellation; see the [Stage 02 response contract](source-contracts/mdd-trias-response-contract.md) | Approved |
| D-017 | 2026-09-25 | `PlannedBay` and `EstimatedBay` remain separate source fields; see the [Stage 02 field-retention matrix](source-contracts/mdd-trias-field-retention-matrix.md) | Approved |
| D-018 | 2026-09-25 | Situation references establish association, not causal explanation; see the [Stage 02 response contract](source-contracts/mdd-trias-response-contract.md) | Approved |
| D-019 | 2026-09-25 | The tested historical StopEvent interface cannot be relied on to reconstruct past realtime/actual operational state; see the [Stage 02 audit](stages/02-mdd-trias-source-contract-audit.md) | Approved |
| D-020 | 2026-09-25 | Prospective realtime collection is architecturally important; see the [collector requirements](collector-design/realtime-collector-requirements.md) | Approved |
| D-021 | 2026-09-25 | Permanent/automatic full raw MDD payload retention is not the default source-of-record strategy; see the [raw-retention decision](source-contracts/mdd-trias-raw-retention.md) | Approved |
| D-022 | 2026-09-25 | The conservative documented project request ceiling remains 250,000 requests/month until stronger provider/account evidence supersedes it; see the [Stage 02 request contract](source-contracts/mdd-trias-request-contract.md) | Approved |
