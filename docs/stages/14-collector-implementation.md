# Stage 14 — Collector Implementation and Legacy7 SQL Runtime

> **Current status (2026-10-08):** The earlier SQLite-first/deferred-runtime
> position and the corrected Legacy7 SQL runtime are retained as historical
> chronology. The current production design is Tiered50-only; the first
> scheduled Tiered50 heartbeat failed its cadence gate, so the LaunchAgent is
> stopped and disabled. Legacy7 is retired. Future automatic rollover remains
> fail-closed pending verified provider reset-period/timezone evidence or
> explicit approval and registration of the bounded policy.

## Authoritative status

Overall Stage 14 status:

`TIERED50 ACTIVATION OUTAGE — NO ACTIVE STATIONS; LEGACY7 RETIRED`

Implementation status:

`STAGE 14 COLLECTOR IMPLEMENTATION COMPLETE`

The production path is the additive macOS-to-SQL Server Tiered50 runtime. Its
43 new SQL target rows use IDs 8–50 and preserve Legacy7 IDs 1–7. An
administrator activation transaction and sampling era were committed, then the
first scheduled bucket `5971541` completed only 2 of 18 target audits before
the heartbeat cadence gate stopped the runtime; the LaunchAgent is now
disabled and no stations are active. The historical SQLite research profile
and Legacy7 SQL evidence remain immutable, lineage-separated records. The
bounded October bootstrap is registered for 554 dashboard requests plus all
local evidence through approval, while future automatic rollover remains
fail-closed until either the provider reset contract is verified and registered
or the bounded policy is explicitly approved and registered. Stage 15A readiness
remains `IN PROGRESS`.

## Current Legacy7 operational correction

The first three Mac cutover-validation runs were valid source observations but
were oversampled: each run selected all seven targets in one heartbeat. The
affected runs are preserved unchanged and are excluded from simple
Windows-versus-Mac cadence-volume comparisons. The corrected runtime uses
`MAC_LEGACY7_PRODUCTION`, records `SamplingSlot` and
`SelectionPolicyVersion` additively on future target audits, and requires the
dedicated least-privilege `CologneTransitCollector` SQL login. The live slot
definition, additive migration, and bounded 21/3/18 methodology evidence are
validated. Three ordinary scheduled heartbeats completed with one target each,
the expected SQL-selected slot/target lineage, HTTP 200, and durable
completion under `MAC_LEGACY7_PRODUCTION`.

This document records the Stage 14 Python collector implementation requested
by S73, hardened by S74, and source-contract-corrected by S75 and S76. It does not reopen the approved Stage 13 panel, tiers, cadence, result
limits, retry boundary, concurrency, or phase assignments.

## Strategy and boundaries

The collector is implemented first as a reusable, scheduler-neutral Python
package. The core is cross-platform and uses only the standard library. A
future deployment system may invoke one heartbeat every five minutes through
cron, a system timer, a container scheduler, a cloud scheduler, or another
platform without changing collector policy code.

The historical collector package remains scheduler-neutral and offline-safe.
The current production path is a five-minute user-local Tiered50 LaunchAgent
that writes directly to SQL Server; it is installed in a separate versioned
bundle and is currently disabled after its first scheduled heartbeat failed the
cadence gate. The corrected Legacy7 path is historical evidence and is retired.
The explicit `live-research` command
remains a separate historical SQLite mode; it is not production runtime
evidence. SQLite is disposable only in the `DEVELOPMENT` profile, while the
`LIVE_RESEARCH` profile is immutable research evidence outside the repository.

The manifest preserves the approved historical Legacy7 lineage IDs 1 through 7
as `legacy_sampling_target_id`. SQL Server preserves those historical IDs and
materializes the 43 approved new production IDs as 8–50. Their approved panel,
tier, phase, cadence, result-limit, and source-parent identities remain fixed.

## Package structure

The implementation is under `src/cologne_transit_collector/`:

| Module | Responsibility |
|---|---|
| `domain.py` | Immutable target, bucket, outcome, sampling-era, and approved policy values |
| `manifest.py` | Versioned manifest loading, exact panel validation, Stage 13 phase reconciliation |
| `schedule.py` | Pure UTC bucket and due-target calculation |
| `ports.py` | MDD, usage, and persistence interfaces |
| `request.py` | Approved TRIAS 1.2 arrival StopEvent XML request builder |
| `transport.py` | Explicitly opted-in standard-library HTTP client plus fake/fixture clients |
| `trias.py` | Source-faithful JSON/XML parser separated from transport and persistence |
| `quota.py` | Shared-credential policy and durable permit lifecycle |
| `persistence.py` | Explicit DEVELOPMENT/LIVE_RESEARCH SQLite durability, claims, audit, snapshots, append-only timing observations/reporting, raw-evidence index, backups, and usage accounting |
| `collector.py` | One-heartbeat dispatcher, cap-2 execution, retry/deadline/failure isolation |
| `simulation.py` | Deterministic fault-injection scenarios |
| `cli.py` | Thin offline-safe command-line routing |

The thin entry point is `scripts/stage_14_collector.py`. The versioned runtime
manifest is `config/tiered50_runtime_manifest.json`.

## Runtime manifest

The manifest contains exactly 50 unique targets:

- 8 Tier A targets at 5 minutes with `NumberOfResults=20`;
- 20 Tier B targets at 15 minutes with `NumberOfResults=30`;
- 22 Tier C targets at 30 minutes with `NumberOfResults=30`;
- 7 `Legacy7 member` targets and 43 `New Tiered50 member` targets;
- persisted phase index and phase count for every target; and
- `TIERED50_PANEL_V1`, `Tiered50`, and `DEFERRED_NOT_ACTIVE` metadata.

The manifest phase rows are checked against
`docs/stages/13-tiered-collector-readiness-evidence.json` at load time. A
mismatch fails closed. The runtime never recomputes phases from mutable rank,
list order, station name, or a future database ID.

## Schedule contract

`logical_due_bucket()` uses UTC epoch time divided into five-minute buckets.
The same wall-clock heartbeat produces the same bucket after restart and DST
conversion. The command considers only the bucket containing its current UTC
wall-clock value; missed buckets are not replayed and no historical realtime
snapshot is fabricated.

Due selection is pure:

- Tier A targets are due in every bucket;
- Tier B is selected when `bucket_index % 3 == phase_index`;
- Tier C is selected when `bucket_index % 6 == phase_index`.

The persisted approved phase capacities reproduce the six-heartbeat sequence
`[19, 18, 18, 18, 19, 18]`, 110 executions per 30 minutes, and 220 per hour.

For the corrected Mac Legacy7 SQL path, the seven existing target rows are
not expanded into the Tiered50 phase planner. SQL selects exactly one target
for the current bucket through `ctl.uspGetMddRealtimeSamplingTarget`, with the
ten-slot mapping `1→2, 2→7, 3→1, 4→4, 5→6, 6→2, 7→7, 8→1, 9→5, 10→3`.
The offline contract is the one-based rule
`UTC_EPOCH_BUCKET_MOD_10_PLUS_1`; the live stored-procedure definition must
confirm that rule before reactivation. Missed buckets are not replayed and no
in-memory counter is used. The approved nominal Legacy7 load is 12 logical
requests/hour, 288/day, 8,640 per 30-day month, and 8,928 per 31-day month.

## Execution, retry, and deadline policy

Every due target is independent. A `ThreadPoolExecutor` is bounded at exactly
two workers; configuration rejects any other maximum. Each target has at most
two attempts: one initial attempt and one automatic retry.

Automatic retry is permitted only for HTTP 408, 429, 500, 502, 503, 504 and
approved transient transport categories. HTTP 401/403, request-contract
errors, parser/schema failures, and TRIAS application errors are not retried.
HTTP `Retry-After` is parsed and must fit the remaining heartbeat budget.

The heartbeat budget is monotonic and bounded by the current UTC bucket end.
Before an initial attempt, retry, or Retry-After wait, the collector verifies
that the operation can fit. It records `SKIPPED_HEARTBEAT_BUDGET` for an
initial skip and `RETRY_SKIPPED_HEARTBEAT_BUDGET` for a retry skip. The local
default five-second safety margin is explicitly marked a development,
non-production default; the final nonzero persistence/cleanup margin belongs
to the future live dry-run gate.

The corrected production timing record separates the derived calendar
boundary, observed Python process/module entry, main-function entry, preflight
start/completion, settings and Collector construction, dispatcher entry,
first target claim, first HTTP transmission, and run completion. The launchd
trigger is not claimed unless independently observed. The actual budget is
captured inside `run_once()` from the current wall clock and absolute bucket
end; monotonic elapsed time remains the duration source, and clock corrections
cannot give remaining budget back. When no useful request fits, all pending
targets are written as one bounded transactional skip-audit batch without
retroactive claims. Timing includes HTTP-to-snapshot persistence delay and
target/run finalization. With cap-2 concurrency and a 30-second timeout, 19
worst-case targets require ten request batches/300 seconds before margin and
overhead, so fixed configuration cannot guarantee worst-case full coverage.

## Quota guardrail

`QuotaGuard` is injected separately from the dispatcher and accepts a shared
usage provider. Its policy understands confirmed current-month provider
attempts, shared
reservations, projected non-retry consumption, the 207,500 automatic-retry
protection boundary, the 210,000 normal operational maximum, and the 250,000
provider ceiling.

Initial scheduled requests and automatic retries are separate decisions. An
initial request does not silently consume retry headroom. A retry fails closed
when safe shared-credential state is unavailable. The local provider can be
in-memory or SQLite-backed for simulation; it does not assume Tiered50 is the
only user of the credential.

Each authorized attempt has an explicit permit lifecycle:

1. `PENDING_AUTHORIZATION` reserves headroom before the transport boundary;
2. `RELEASED_NO_NETWORK` records credential/request-contract or offline
   completion before any network operation;
3. `CONSERVATIVE_LIVE_ATTEMPT` permanently protects headroom when a live
   network invocation occurred without an HTTP response and provider quota is
   unknown; or
4. `HTTP_PROVIDER_ATTEMPT_CONFIRMED` records a confirmed provider HTTP attempt
   exactly once.

SQLite finalizes the permit and records the HTTP-attempt metadata in one
transaction. A pending live permit is never released between opener
invocation and durable accounting. The accounting keeps client-side live
invocations, HTTP responses, confirmed provider attempts, provider-quota
unknown failures, and conservative live-attempt protection distinct. Fixture
and simulation calls finalize as no-network and cannot become provider usage.

## Persistence port and local backend

The backend-neutral persistence port covers target mapping, atomic dispatcher
and target claims, collector-run start/completion/failure/skip, target audit,
HTTP attempt accounting, parsed snapshot persistence, quota audit, atomic
quota permits, current-month usage, sampling-era lineage, and stale-claim
visibility.

SQLite uses two development claim identities. The dispatcher/bucket claim is:

`(panel_version, logical_due_bucket)`

The target claim identity is:

`(panel_version, source_parent_station_id, logical_due_bucket)`

The future production identity remains:

`(SamplingTargetId, LogicalDueBucket)`

SQLite inserts both claim types under transactions with unique keys. The first
dispatcher claimant wins before a target pool exists; duplicate dispatcher
invocations are audited and issue zero MDD requests. The winning dispatcher
claims each target immediately before worker submission rather than
pre-claiming the full due set. A claimed/running row remains observable after a
crash and is not blindly re-executed on restart. Local parsed snapshots contain
approved source-faithful fields only; raw MDD payloads are not persisted.

Transport outcomes carry explicit `network_attempted` and
`http_response_received` metadata plus a transport kind. Credential and
request-contract preflight failures set both flags false. Opener-invoked
DNS/TLS/connection/timeout failures set only `network_attempted` true. A live
HTTP response sets both true. Collector metrics use these result flags rather
than the presence of a `live_network` client attribute. `run_simulation` owns
and closes its internal SQLite instance in a `finally` block; callers of
`build_simulation` own the returned persistence object and must close it.

SQLite has two explicit profiles. `DEVELOPMENT` state remains disposable
local state under `.local/` and is ignored by Git. `LIVE_RESEARCH` state is
non-disposable, additive-schema, owner-protected state outside the repository;
its metadata identifies the database role, schema version, creation time,
last migration time, and `LIVE_RESEARCH_SINGLE_1` context. The live profile
never resets or recreates an existing database, retains parsed history
indefinitely, and uses SQLite's online backup API for three daily versions.
Neither profile is a Tiered50 production cutover or a PostgreSQL implementation.

## TRIAS and raw-response handling

The real client sends POST requests with the approved TRIAS 1.2 request shape,
arrival StopEvent scope, per-target `NumberOfResults`, previous/onward/realtime
flags, a 30-second per-attempt timeout, and the `MDD_API_KEY` environment
variable as the only secret source. The key is neither logged nor persisted
and never appears in command-line arguments. Live HTTP requires an explicit
client opt-in and the CLI `live` mode remains blocked because the required
production persistence and cutover state do not exist.

The parser accepts JSON and XML, preserves nullable source timing and bay/
stop-flag values for every THIS/PREVIOUS/ONWARD call occurrence, retains source
paths and event/call ordinals, and keeps Situation and result/application error
references source-faithful. Call fields are read only from the current call or
`CallAtStop` scope; missing values are not inherited from `StopEvent` or
`Service`. Journey/service `Cancelled` and `Deviation` remain separate
event-level fields. `JourneyRef`, `VehicleJourneyRef`,
`DatedVehicleJourneyRef`, `DirectionRef`, and `Direction` are independent
nullable source fields. The approved candidate identity extensions
`AffectedVehicleJourney`, `VehicleJourneys`, `OperatingDaysDescription`,
`Origins`, and `Destinations` retain their own source paths. `PtMode`, nullable
submode, `PublishedLineName`, and origin/destination identity and display text
are read only from `Service`; event-level lookalikes remain NULL. Service
`Attribute`/`GeneralAttribute` and `IncidentCall` evidence retains Code,
sibling Text, source representation, association context, and result/event
lineage without causal inference. Situation evidence retains affected-entity
context and field paths. Parser-gap coverage is path-based: recognized names
are not silently suppressed, explicit evidence is not duplicated, and
unrelated envelope fields remain ignored. It does not derive `RecordedAtTime`,
actual physical delay, cancellation, or platform state.

### S79 timing observation history

Each parsed source timing context is appended to `timing_observations` with
nullable `TimetabledTime`, `EstimatedTime`, estimate bounds, and
`RecordedAtTime`, plus independent `ObservedAt`/`CapturedAt` collection
lineage and source paths. `TimetabledTime`, `EstimatedTime`,
`RecordedAtTime`, and collector observation time are never collapsed into a
derived source-time field, and a later NULL source value never overwrites an earlier
non-NULL observation. `recorded_at_time_evidence` remains the append-only
non-NULL source-evidence table; the timing reporting view is a derived helper
over the normalized rows and is not a second evidence count.

The `MDD_SOURCE_EVENT_V2` hash is defined only by the required core:
`OperatingDayRef`, `JourneyRef`, `StopPointRef`, `TimetabledTime`, and
arrival/departure context. Optional source fields such as line, direction,
operator, vehicle-journey, and stop-sequence values are retained only as
lineage/disambiguation evidence; they never alter the hash. `ResultId`,
estimated/recorded values, and observation timestamps are also outside the
identity. Missing required context is explicitly
`SOURCE_EVENT_IDENTITY_INSUFFICIENT` and is never guessed or merged with
another event. The reporting projection exposes `LatestSourceRecordedAtTime`,
`LastObservedRecordedAtTime`, first/last non-NULL lineage, snapshot/audit/path
identifiers, and the non-NULL observation count.

The first-gap audit is recorded in
[14-live-research-first-gap-audit.md](14-live-research-first-gap-audit.md) and
[its JSON artifact](14-live-research-first-gap-audit.json). The historical S78
count is 121: 21 structural-container occurrences and 100 ServiceSection field
occurrences across 20 source sections. Those 100 fields were not retained in
the original S78 normalized snapshot; S80 reconstructed them from retained
sanitized raw evidence and now retains them explicitly at source occurrence
grain with source paths and bounded representations. Unknown
relevant future ServiceSection extensions remain parser-gap evidence.

### S80 identity, ServiceSection, migration, and window corrections

S80 makes the timing identity versioned and stable: the hash uses only
OperatingDayRef, JourneyRef, StopPointRef, TimetabledTime, and timing context.
ResultId, mutable estimates/recorded values, observation timestamps, and
optional lineage fields remain outside the core. If one snapshot contains
multiple rows with the same stable core, each row is explicitly marked
`SOURCE_EVENT_IDENTITY_AMBIGUOUS_CORE` and excluded from reporting-group
aggregation rather than merged.

Existing parsed snapshots are backfillable without rewriting their stored JSON.
The live-research migration is idempotent, enforces the timing uniqueness grain,
reconciles non-NULL RecordedAtTime evidence, and stores multiple
ServiceSection occurrences in `service_section_observations`. The actual S78
file was migrated offline from schema `2` to `4`: 995 timing rows and 20
ServiceSection rows were inserted; integrity/quick checks and reconciliation
passed; and the validated daily backup was refreshed. `created_at_utc` is
preserved, and `last_schema_migration_at_utc` changes only for a real schema
version change.

Stage 15 historical analysis filters raw timing observations to the requested
logical bucket and observation window before grouping. The global SQLite
reporting view remains available for current-state inspection only and cannot
leak future observations into a historical window.

### S81 reporting-lineage and migration hardening

The schema-5 SQLite reporting view uses the exact
`recorded_at_time_source_path` leaf for `LatestSourceRecordedAtTimeSourcePath`,
`LastObservedRecordedAtTimeSourcePath`, and
`FirstObservedRecordedAtTimeSourcePath`. A latest NULL `RecordedAtTime` keeps
the latest path NULL; a historical non-NULL value uses the leaf from its own
observation. The Stage 15 in-memory projection applies the same rule.

The existing live file was migrated offline from schema `4` to `5` at
`2026-10-01T15:15:16.410851Z`. The four required table counts were unchanged:
1 parsed snapshot, 995 timing observations, 0 RecordedAtTime evidence rows,
and 20 ServiceSection rows. Integrity/quick checks, reconciliation, corrected
view validation, and the refreshed current-day backup passed; the temporary
safety backup was removed only after validation. Opening schema 5 preserves
`last_schema_migration_at_utc`.

Parsed snapshots are separate from transport and persistence. Ordinary
successful live responses do not retain raw bodies. The `LIVE_RESEARCH` raw
hook retains only sanitized forensic evidence for RecordedAtTime, parser gaps,
parser/application failures, material anomalies, or useful non-success bodies;
raw files are outside SQLite, owner-protected, indexed with a 24-hour expiry,
and cleaned at startup, after the heartbeat, or by maintenance. The
`DEVELOPMENT` profile keeps the hook disabled.

### Stage 8/Stage 9 semantic reconciliation

The S76 offline comparison against the approved Stage 8/Stage 9 source
semantics passes. `JourneyRef` is retained separately from
`VehicleJourneyRef`/`DatedVehicleJourneyRef`; `DirectionRef` is retained
separately from candidate `Direction`; service mode/display and
origin/destination fields remain service-scoped; candidate identity
extensions retain independent source paths; Attribute/GeneralAttribute and
affected-entity evidence remains source representation rather than causal
interpretation; and every relevant unmapped field is either bounded parser-gap
evidence or an intentionally structural/safe generic field. JSON and XML
correction fixtures produce the same semantic values, while their exact paths
continue to reflect each source representation.

## Execution modes

`python scripts/stage_14_collector.py` defaults to `plan`.

| Mode | Behavior |
|---|---|
| `plan` | Reads and validates the manifest, prints the current bucket and due targets, mutates no durable state, makes no request |
| `dry-run` | Uses a sanitized fixture client and SQLite to exercise claims, audit, parsing, persistence, quota, and concurrency |
| `simulate` | Uses deterministic fault injection; `--fault` selects an approved scenario |
| `live` | Explicitly recognized but blocked until production persistence, safety state, cutover, and later authorization exist |
| `live-research` | Requires `--allow-live`, explicit live root/database, `--pilot-target de:05315:11110`, and inherited `MDD_API_KEY`; executes exactly one Heumarkt target for the current bucket |
| `live-research-maintenance` | Cleans expired raw evidence and refreshes/rotates validated live SQLite backups without transport |

The fault harness covers all-success, 5xx retry success/exhaustion, 401, 403,
429 Retry-After fit/exceed, timeout, parser failure, persistence failure,
quota retry denial, initial/retry budget skips, duplicate and concurrent
duplicate invocation, crash-before-completion, stale claims, and unrelated
target failure isolation. None of these scenarios opens a network connection.

## Sampling-era and continuity contract

The domain represents `Legacy7` and `Tiered50` as distinct sampling eras.
The runtime manifest remains the approved Tiered50 design, while the live pilot
uses the separate operational context `LIVE_RESEARCH_SINGLE_1`; it does not
create an active Tiered50 era or a production cutover timestamp. Live research
observations cannot satisfy Stage 15B continuity. Legacy history is not
deleted or rewritten, and the 43 new production target IDs remain configured
in SQL while the runtime is stopped; no stations are currently active.

## Offline closeout validation

The historical closeout suite passed in five consecutive runs, with 154 tests
reported in each run. In the current cadence-correction review, the complete
repository suite passed in five consecutive runs with 346 tests per run and
`ResourceWarning` promoted to an error; no SQLite resource warning was emitted.
The Stage 14 validator
passed with 50 manifest targets, 7 Legacy7 targets, 43 new targets, schedule
`[19, 18, 18, 18, 19, 18]`, 220 requests/hour, zero live-network calls, and
Stage 15B live validation `NOT STARTED — FIRST SCHEDULED TIERED50 HEARTBEAT
FAILED`; Stage 15A readiness is tracked in the [Stage 15 readiness document](15-post-cutover-validation-readiness.md).
Python compilation, static architecture validation,
and `git diff --check` passed. The parser regression coverage includes
call-scope isolation, event/service-operational-field grain, independent
journey/direction identity retention, service-only identity scope,
Attribute/GeneralAttribute and affected-entity evidence, JSON/XML parity, and
contextual parser-gap coverage. Unexpected
unstructured client exceptions are deterministic non-retryable
`CLIENT_EXECUTION_FAILURE` outcomes with no-network permit finalization.
Offline plan/simulate validation produced 19 due targets, 19 simulated
transport calls, and zero live-network calls; CLI `live` remained blocked
before transport.

The first real Tiered50 scheduled heartbeat issued two MDD requests and then
stopped on its cadence gate; no manual Heumarkt request, PostgreSQL
installation/object change, or Power BI change was performed. The corrected
Legacy7 path is retired after its final boundary. The one authorized
S78 research heartbeat used the
`LIVE_RESEARCH_SINGLE_1` context at `2026-10-01T11:30:00Z` for
`de:05315:11110`, received HTTP 200, parsed 20 results, persisted one snapshot,
recorded one provider attempt without retry, and retained 121 historical
parser-gap fields with sanitized raw evidence. The S80 offline migration then
updated only the additive schema/evidence tables; the stored snapshot JSON was
not rewritten. It remains excluded from Stage 15B.

## Deferred work before Tiered50 activation

The following are intentional deferrals, not implementation defects:

1. establish the complete provider-wide billing-period quota baseline,
   including verified reset timezone/bounds, confirmed, conservative unknown,
   outstanding, and shared-consumer reservations;
2. resolve and register the provider reset contract before any future-month
   automatic rollover can authorize requests;
3. repair the scheduled cadence/persistence gate and perform a fresh bounded
   activation only after every independent cutover gate passes; and
4. begin live Stage 15B validation after six normal Tiered50 buckets exist.

The October bootstrap registration and the first bounded activation attempt are
performed; the reset-contract and successful post-cutover validation items are
not.
