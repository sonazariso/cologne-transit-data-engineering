# Stage 14 — Collector Implementation and Offline Cutover Readiness

## Authoritative status

Overall Stage 14 status:

`IN PROGRESS — PRODUCTION CUTOVER DEFERRED`

Implementation status:

`COLLECTOR IMPLEMENTATION — CORRECTIONS REQUIRED`

The implementation closeout is deliberately narrower than a production
cutover. The final PostgreSQL host/runtime, production persistence adapter,
real target materialization, sampling-era cutover timestamp, deployment
platform, controlled live dry-run, Tiered50 activation, and post-activation
validation remain open work. Stage 15 remains `NOT STARTED`.

This document records the Stage 14 Python collector implementation requested
by S73. It does not reopen the approved Stage 13 panel, tiers, cadence, result
limits, retry boundary, concurrency, or phase assignments.

## Strategy and boundaries

The collector is implemented first as a reusable, scheduler-neutral Python
package. The core is cross-platform and uses only the standard library. A
future deployment system may invoke one heartbeat every five minutes through
cron, a system timer, a container scheduler, a cloud scheduler, or another
platform without changing collector policy code.

No recurring scheduler loop, deployment resource, database server, database
driver, production target row, or live MDD invocation is part of this
iteration. PostgreSQL remains the approved future authoritative direction,
but host and runtime selection are deferred. SQLite is only a disposable
local development backend.

The manifest preserves the approved historical Legacy7 lineage IDs 1 through 7
as `legacy_sampling_target_id`. These are continuity metadata only, not future
production primary keys. Production IDs remain unresolved for all targets and
are not invented for the 43 new targets.

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
| `quota.py` | Shared-credential initial-request and automatic-retry policy |
| `persistence.py` | Disposable SQLite durability, claims, audit, snapshots, and usage accounting |
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

SQLite state is disposable local development state under `.local/` and is
ignored by Git. It is not a PostgreSQL schema, production database, or
production migration.

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
references source-faithful. It does not derive RecordedAtTime,
actual physical delay, cancellation, or platform state. Parsed snapshots are
separate from transport and persistence. The existing raw-response policy is
unchanged: sanitized fixtures are allowed offline; production raw payload
retention is not enabled by this implementation. The backend-neutral
`retain_raw_evidence` hook is present for the approved future-live bounded
forensic policy and is explicitly disabled by SQLite.

## Execution modes

`python scripts/stage_14_collector.py` defaults to `plan`.

| Mode | Behavior |
|---|---|
| `plan` | Reads and validates the manifest, prints the current bucket and due targets, mutates no durable state, makes no request |
| `dry-run` | Uses a sanitized fixture client and SQLite to exercise claims, audit, parsing, persistence, quota, and concurrency |
| `simulate` | Uses deterministic fault injection; `--fault` selects an approved scenario |
| `live` | Explicitly recognized but blocked until production persistence, safety state, cutover, and later authorization exist |

The fault harness covers all-success, 5xx retry success/exhaustion, 401, 403,
429 Retry-After fit/exceed, timeout, parser failure, persistence failure,
quota retry denial, initial/retry budget skips, duplicate and concurrent
duplicate invocation, crash-before-completion, stale claims, and unrelated
target failure isolation. None of these scenarios opens a network connection.

## Sampling-era and continuity contract

The domain represents `Legacy7` and `Tiered50` as distinct sampling eras.
The runtime manifest is Tiered50 configuration but remains inactive and has no
real cutover timestamp. SQLite can record explicitly synthetic test era times
for simulations; no production timestamp is fabricated. Legacy history is
not deleted or rewritten. Future production materialization must retain the
seven existing target IDs once the authoritative mapping is available and add
only the approved new targets.

## Deferred work required for real Stage 14 completion

The following are intentional deferrals, not implementation defects:

1. choose the final PostgreSQL host/runtime and implement the production adapter;
2. materialize the production schema and map/retain real `SamplingTargetId` values;
3. perform the approved seven-to-50 target migration without rewriting Legacy7 history;
4. record the real Tiered50 sampling-era cutover timestamp;
5. determine the final nonzero heartbeat persistence/cleanup safety margin;
6. activate a platform-neutral deployment/scheduler after the platform decision;
7. run a separately authorized controlled live MDD dry-run with shared-credential audit state;
8. activate Tiered50 only after the dry-run gate passes; and
9. execute post-activation smoke, quota, persistence, and continuity validation.

No item above is performed by the current implementation.
