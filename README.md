# Cologne Transport Intelligence

Business Intelligence and data-engineering portfolio project for understanding the **system performance of public transport within Cologne (Köln), Germany**.

This repository is a clean restart. The previous `cologne-public-transport-intelligence` repository is retained as historical evidence only; its architecture and implementation are not automatically authoritative here.

## Primary objective

Build a management-oriented analytical system that helps a non-technical decision maker understand whether public transport within Cologne is operating well during the period under analysis, where problems are concentrated, how performance changes over time, and what source evidence supports those conclusions.

The primary timing objective is **Actual Delay derived from validated `RecordedAtTime` evidence**. `EstimatedTime` is secondary and may later support estimated-delay and prediction-accuracy analysis. The project must not rename estimated delay as actual delay if the source does not provide sufficient recorded/actual timing evidence.

## Current status

- Stage 0 — Project Definition: **Approved**
- Stage 1 — Source Inventory & Permission: **Approved**
- Stage 2 — MDD/TRIAS Source-Contract Audit: **In progress**
- Stage 02A.2 remains **Blocked** pending successful controlled MDD response
  evidence. See [Project Status](docs/PROJECT-STATUS.md) for the detailed
  blocker.
- No new database, collector, or Power BI implementation is authoritative yet.
- The legacy collector/database must remain untouched until explicitly reviewed.

See [Project Status](docs/PROJECT-STATUS.md), [Stage 0 Project Definition](docs/stages/00-project-definition.md), and [Roadmap](docs/ROADMAP.md).

## Repository principles

1. Review before implementation.
2. Source evidence before assumptions.
3. Approval gate at the end of every stage.
4. No invented operational facts.
5. Preserve unknown/NULL semantics.
6. Do not delete legacy realtime history.
7. Do not start new MDD polling until quota and permission constraints are understood.
8. Keep secrets, database dumps, large raw payload archives, PBIX files, and local runtime state out of Git.

## Working environment

The authoritative Git working copy is intended to live on macOS:

`/Users/admin/Documents/cologne-transit-intelligence/`

Windows accesses the same working tree through VMware Shared Folders:

`\\vmware-host\Shared Folders\cologne-transit-intelligence`

Windows should not maintain a second Git clone for this project.

## Repository layout

- `docs/` — authoritative project documentation and stage decisions
- `references/` — pointers/checksums for legacy evidence; not copied legacy implementation
- `src/` — application/collector source when an implementation stage is approved
- `sql/` — database source when database architecture is approved
- `powerbi/` — Power BI source when the BI model stage is approved
- `tests/` — automated/validation tests when implementation begins

Empty implementation directories intentionally contain only `.gitkeep` files at project foundation.
