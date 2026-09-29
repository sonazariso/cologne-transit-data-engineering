# Changelog

## Unreleased

### Added
- Stage 9 ranking hardening and bounded source-backed acceptance probe: raw
  StopSeqNumber/direction/operator/endpoint coincidences no longer change
  candidate rank or tie resolution without validated semantics; the SQL
  overlap view now compares effective ranges across mapping versions. The
  bounded probe was invoked with zero MDD attempts because safe preflight
  reported `MDD_API_KEY: MISSING`, so Stage 9 remains blocked and no static
  snapshot was fabricated.
- Stage 9 review correction: explicit Stage 8-to-Stage 9 projection, timezone-aware Europe/Berlin service-day alignment, separate `OperatingDayRef` metrics, effective mapping validity, precise static/scope classifications, validated-correlation gating, SQL semantic checks, and a source-classified evidence artifact. Synthetic regression passes; source-backed Stage 9 acceptance remains blocked.
- New Cologne Transport Intelligence repository foundation.
- Stage 9 matching/data-quality foundation: deterministic source-qualified
  stop-event matching, versioned identity bridges, explicit ambiguity and
  lineage classifications, PostgreSQL contract/indexes, richer MDD identity
  retention, and offline acceptance tests.
- Stage 8 hardening for secret-free API-key header preflight, bounded positive
  `RecordedAtTime` raw-evidence retention, and offline synthetic validation.
- Approved Stage 0 project definition.
- Restart roadmap and approval-gate workflow.
- Semantic/data guardrails.
- Environment and Git workflow documentation.
- Stage 1 working document for source, permission, and quota audit.
- Legacy evidence reference/checksums without bulk-copying the old implementation.
