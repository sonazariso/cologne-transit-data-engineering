# Changelog

## Unreleased

### Added
- Stage 10 operational-fact contract and semantic validator: the derived fact
  uses the approved `(DateKey, ScheduledStopEventKey)` grain, deterministic
  first/latest observation ordering, latest-row NULL timing semantics,
  conservative platform evidence, non-causal situation-link evidence, exact
  Stage 9 reconciliation checks, and repeat-refresh repeatability checks. The
  stage remains incomplete because the current checkout has no physical
  database, loaded source history, or approved Stage 9 operational-input view.
- Stage 9 row-level evidence-integrity correction: audit fields are now
  serialized losslessly from the in-memory decisions, malformed/null/truncated
  decision artifacts are blocked by an explicit JSONL integrity gate, and seven
  offline regression tests cover the failure and reconciliation paths. The one
  corrected bounded live run passed with 60 retained rows, 53 independently
  established service dates, 51 core deterministic matches, 51 `MEDIUM` / 9
  `UNMATCHED` decisions, and row-level integrity `PASS`; the current VRS
  snapshot is `VERSION__20260929_0106` with SHA-256
  `ca64753223d3908a86b74fc322103b9e4c6c9c90275cfc5cb2c4c624b0082ee0`.
- Stage 9 source-backed acceptance correction: preliminary THIS-call selection
  now adapts raw Stage 8 stop-event records before inspecting the adapter's
  selected scope, with parser-versus-adapter diagnostics per MDD request. The
  single bounded live rerun passed with 3 HTTP 200 responses, 60 retained
  observations, 54 independently established service dates, and 51 core
  deterministic matches. The current VRS snapshot changed from the approved
  Stage 4 reference and passed all 17 Stage 4 revalidation checks.
- Stage 9 ranking hardening and bounded source-backed acceptance probe: raw
  StopSeqNumber/direction/operator/endpoint coincidences no longer change
  candidate rank or tie resolution without validated semantics; the SQL
  overlap view now compares effective ranges across mapping versions. The
  initial bounded probe was invoked with zero MDD attempts because safe
  preflight reported `MDD_API_KEY: MISSING`; that historical attempt made no
  source-backed acceptance claim.
- Stage 9 review correction: explicit Stage 8-to-Stage 9 projection, timezone-aware Europe/Berlin service-day alignment, separate `OperatingDayRef` metrics, effective mapping validity, precise static/scope classifications, validated-correlation gating, SQL semantic checks, and a source-classified evidence artifact. Synthetic regression passes; the initial source-backed gate was blocked before the later D-039 probe correction.
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
