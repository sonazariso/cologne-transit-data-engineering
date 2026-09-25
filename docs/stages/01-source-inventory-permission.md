# Stage 1 — Source Inventory & Permission

**Status:** NOT STARTED

This is the working document for the next stage. No findings below should be marked approved until they are re-verified.

## Goal

Establish the authoritative sources and their operational/legal constraints before new ingestion work begins.

## Required evidence

### Static GTFS
- source owner/publisher
- authoritative download location
- feed identity/version metadata
- geographical/operator coverage
- update frequency
- licence/usage/redistribution conditions
- retention expectations

### MDD / DELFI / TRIAS
- provider/owner
- authoritative endpoint
- protocol/TRIAS version
- authentication method
- current monthly quota
- request/provider limits
- permission for storage/retention of parsed fields
- permission/constraints for raw response retention
- documentation/source-contract references

### Legacy collector quota audit
Before any new MDD polling:
- confirm current task/collector is actually running
- current enabled targets and cadence
- current `NumberOfResults`
- current month logical runs
- `HttpAttempts` distribution
- additional attempts caused by retries
- failures/429s if any
- estimated current-month and full-month request consumption
- safe quota headroom

The legacy documentation records a historical configuration of one logical request every five minutes and a 250,000/month quota, but Stage 1 must not treat that as current live state without read-only verification.

## Constraints

- Prefer documentation, existing logs, SQL audit, and configuration for the first audit.
- Do not issue unnecessary MDD probe requests merely to estimate quota.
- Do not stop/reconfigure the legacy collector during read-only inventory.
- Do not expose API keys or credentials in documentation.

## Approval gate

Stage 1 closes only after source identity, permission/retention, quota, and endpoint/protocol constraints are documented with evidence and explicitly approved.
