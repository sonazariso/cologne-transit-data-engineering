# Changelog

## Unreleased

### Added
- Stage 12 fixed 50-parent-station selection and versioning closeout:
  `TIERED50_PANEL_V1` with exactly 50 unique validated VRS parent stations,
  all seven Legacy7 continuity anchors, deterministic operational/geographic
  selection evidence, current core-mode coverage, concentration review, a
  bounded 50-request MDD compatibility probe, reserve candidates, and an
  explicit fixed-membership/versioning contract.
- Stage 13 Tiered Collector Readiness audit and machine-readable evidence:
  provisional 10/20/20 cadence and 30/31-day quota math; labeled legacy retry
  sensitivity; repository-controlled MDD consumer inventory; bounded N5/N20
  result-limit saturation evidence with explicit
  `PROVIDER_MAXIMUM_NOT_ESTABLISHED`; separate duration populations; missing
  Stage 12 panel/static-density evidence; deterministic 5/10/30 phase
  reconciliation; atomic target/bucket claim and no-replay contract; failure
  isolation; Legacy7 continuity; and a conditional bounded-concurrency
  direction. Stage 12 now has an approved fixed panel; production activation
  remains blocked, and Stage 14 was not started.
- Stage 11 approved Power BI semantic-model design: separate static schedule,
  HIGH-only operational evidence, and all-confidence matching/data-quality
  subject areas; explicit table grains and single-direction relationships;
  an explicit physical Stage 6 fact versus semantic Scheduled Trip boundary;
  governed Date strategy; static VRS versus canonical operational identity
  boundary; DAX measures with explicit denominators and NULL semantics;
  service-date-scoped matching coverage with separate missing-service-date
  evidence; estimated-delay threshold policy; bay/Situation terminology;
  sampling and executive/technical reporting boundaries; and a deferred
  PBIP/TMDL/PBIR implementation contract. `Dim Sampling Era` currently filters
  only the `Fact Matching Decision` population; Data Quality classifications
  carried by those decisions inherit that context, while the independent `Fact
  Data Quality Issue` and `Data Quality Issue Count` are not filtered by
  Sampling Era in the base model. Sampling Era is not an operational KPI slicer
  until validated operational-event lineage exists. No Power BI runtime
  artifacts or Desktop execution were created; Stage 10 remains
  runtime-blocked and Stage 12 was not started.
- Stage 10 operational-fact correction: the derived
  `dw.fact_operational_service_event` uses the canonical
  `(service_date, canonical_service_event_key)` grain, explicit
  `matching_run_key`/`matching_version` selection, HIGH-only Stage 9
  eligibility, separate durable-source operational evidence, deterministic
  first/latest observation ordering, latest-row NULL timing semantics,
  PlannedBay/EstimatedBay evidence, non-causal Situation-link evidence, and
  refresh #1/snapshot A/refresh #2/snapshot B repeatability validation. The
  current Stage 10 status is **NOT COMPLETE — IMPLEMENTATION CORRECTED; SOURCE/DATABASE VALIDATION BLOCKED** because the current checkout has no
  usable PostgreSQL/source-observation population and the current Stage 9
  evidence contains zero HIGH rows.
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
