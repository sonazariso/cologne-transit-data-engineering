# Tiered50 deployment validation report

Status: **BLOCKED — TIERED50 STOPPED AFTER FAILED FIRST SCHEDULED HEARTBEAT; LEGACY7 RETIRED**

This report preserves the pre-repair deployment-validation snapshot. The
authoritative 2026-10-09 administrator refresh and schema-repair decision
record are in
[`tiered50-schema-repair-readiness-20261009.md`](tiered50-schema-repair-readiness-20261009.md);
its reported SQL Skip Audit result and explicit DDL/signing gates supersede
the older `NOT_PROVEN` wording below without changing this historical report.

The approved `TIERED50_PANEL_V1` configuration remains unchanged. The SQL
Server migration and exact Tiered50 materialization are applied: all 50 panel
records are represented, historical IDs 1–7 are preserved, and new IDs 8–50
are present in the additive target table. Administrator activation committed
the Tiered50 target state and sampling era, but the scheduled runtime was
stopped after its first heartbeat failed the cadence gate. The separate
versioned LaunchAgent is disabled and zero stations are currently collecting.

The historical deployment evidence captured before this correction pass records
the final Legacy7 run, six consecutive expected Legacy7 buckets with accounted
and successful SQL persistence, SQL capacity, the latest checksum-verified
native full backup, launchd state, Mac power/uptime evidence, and an observable
VMware guest. Legacy7 was then unloaded and disabled. No Windows collector or
competing Legacy7 writer was active in that evidence, and no manual MDD or
Heumarkt request was issued.

## Current source and operational review — 2026-10-10

The Windows evidence source now reports bounded recent target-audit and HTTP
samples newest-first. The original first Tiered50 heartbeat and outage remain
separate authoritative queries; the first heartbeat's target audits and HTTP
attempts are separately queried and cross-checked by RunId and AuditId. Query
execution status remains distinct from proof that historical rows are present,
and zero rows remain valid only for queries whose contract permits them. The
first-heartbeat validator now reconciles every target audit's declared
`HttpAttempts` count with actual HTTP-attempt rows, detects missing, duplicate,
orphaned, and inconsistently attributed attempts, and derives the target,
successful HTTP-backed, and skipped counts from the authoritative rows. Thus
the preserved 18/2/16 history remains reportable when present without becoming
a universal requirement for another installation. The Windows SQL helper now
also retains `Invoke-Sqlcmd` `System.Data.DataRow` results as a predictable
non-enumerated collection, so one-row identity and permission queries cannot
accidentally index a column; empty results and terminating SQL errors remain
honest. No SQL history, target, quota, cutover, or Power BI data was changed.

The complete repository suite passed five consecutive times with
`ResourceWarning` promoted to an error, with 388 tests in each run. The
offline migration, architecture, runtime-permission, and read-only production
preflight validators passed; the Stage 15 result remains
`INSUFFICIENT_EVIDENCE`, and the skip-audit validator remains static-only
(`NOT_RUN`) because no new live SQL transaction was authorized.

No actual Windows PowerShell capture, detached signing, Mac evidence merge,
Resume, or six-bucket collection cycle was performed in this environment. The
running VMware configuration maps the host `/Users/admin/Documents` as the
guest `powerbi` shared folder, so the current script is available at the
documented guest path; however, in-guest execution/output could not be
observed because VMware guest operations require the encrypted VM password.
There is no local PowerShell executable or operator signing key/public-key pair
in the approved locations. Windows-side `Invoke-Sqlcmd`, TLS validation,
Windows disk measurements, and SQL evidence therefore remain unobserved;
Python tests do not substitute for that proof. Tiered50 therefore remains
stopped with 43 active materialized Tiered50 targets (panel records 8–50),
zero stations collecting, and no proven full six-bucket result; future rollover
remains unverified and fail-closed.

The single remaining operator action is to use the existing interactive
Windows 11 VM under the authorized Windows identity: open the latest
`scripts/capture_windows_sql_admin_evidence.ps1` from
`\\vmware-host\powerbi\cologne-transit-intelligence\scripts\`; if that share is
unavailable, copy only that script to a secure Windows local directory, verify
the supported `SqlServer` module exposes `Invoke-Sqlcmd` with encrypted,
certificate-validating parameters, run the read-only capture against the
approved SQL instance/database, and transfer only the sanitized unsigned
payload for the existing signing/merge workflow. No password is requested or
stored in chat.

## Current operational refresh — 2026-10-09

The separate administrator path proved the actual `DataAnalyst-VM` /
`CologneTransitIntelligence` target, permissions, backup checksum verification,
database/log/volume capacity, and absence of competing collectors or leases.
The read-only `CologneTransitCollector` inspection also passed all 34 positive
matrix permissions and denied every prohibited broad or unrelated privilege.
The only missing source object was `ctl.MddMacProductionResumeEvent`.

The exact scoped repair command committed that table from the authoritative
source-faithful DDL. Fresh administrator verification confirms the ten ordered
columns, identity, nullability, primary and unique keys, foreign key,
check/default constraints, indexes, permissions, and `ctl` ownership. The
former history-query SQL error 208 is resolved. No ResumeEvent row, quota
rewrite, cutover repeat, collector activation, or MDD request was performed.
Legacy7, its history, Power BI, and the Tiered50 materialization were preserved.

The real SQL Server Skip Audit validator passed and rolled back all probe rows;
zero rows remained and historical production audit rows were unchanged. The
relevant regression set passed 95 tests. The full sanitized repair record is
[`tiered50-schema-repair-evidence-20261009.json`](tiered50-schema-repair-evidence-20261009.json).

The fresh administrator evidence is
[`tiered50-production-evidence-current.json`](tiered50-production-evidence-current.json).
It is intentionally unsigned because the operator-controlled signing key was
not supplied. The current artifact still lacks `sql.windows_vm_disk`: VMware
guest operations are blocked by the running VM's encryption-password prompt,
so Windows disk capacity cannot be inferred from Mac or SQL measurements.
Tiered50 remains stopped, the LaunchAgent remains disabled, zero stations are
collecting, and the separate Resume authorization was not executed. Future
monthly rollover remains unverified, unapproved, unregistered, and fail-closed.

## Latest correction and gate reconciliation — 2026-10-08

The monthly authorization correction now branches validation by accounting
model. Exact provider-timezone rows still require a strict IANA timezone,
provider-calendar month mapping, independently derived UTC boundaries, and an
active provider-verified contract. Bounded rows require the literal
`BOUNDED_TIMEZONE_ENVELOPE` marker, an active matching operator-approved
contract, the exact UTC-12 through UTC+14 endpoint envelope, matching evidence
identity and delay, and independently derived start/end boundaries; the marker
is never treated as a provider timezone. `current_month_usage()` now reports
provider verification separately from bounded operator-assumption provenance,
and period-start permits are included exactly once for both automatic models.

The offline persistence regressions exercise both exact and bounded contracts,
SQL-procedure handoff, current usage, reservation/finalization, boundary
permits, conservative pending accounting, and repeated reads. Actual
SQL-Server validation, the rollback-only skip-audit execution, administrator
readiness, Tiered50 resume, and six-bucket live coverage remain unproven until
the documented administrator identity is available. October bootstrap,
Legacy7 retirement, the stopped Tiered50 state, historical evidence, and Power
BI remain unchanged.

The separate no-network live preflight, using only the least-privilege runtime
login, revalidated the runtime schema, 43 active Tiered50 rows (IDs 8–50),
current October quota state, no competing writers, and the observed Windows VM;
it issued zero MDD requests. Privileged full-schema, backup, volume/log
capacity, immutable cutover-history, rollback-only skip-audit, and signed
administrator-evidence gates remain `NOT_PROVEN` or `NOT_RUN`. The resume-only
entry point was checked without `--apply`, so no SQL or LaunchAgent action was
performed.

## Safe resume and evidence gates

The already committed Tiered50 activation now has a separate, explicit resume
path. `scripts/resume_tiered50_production.py --apply` validates the immutable
43-row target set, original activation boundary, real sampling era, current-era
pointer, activation and outage history, competing writers, current quota
headroom, fresh signed administrative evidence, and the installed versioned
bundle before enabling the stopped LaunchAgent. It never accepts a new
effective timestamp, creates a second sampling era, rewrites targets or
cutover history, replays requests, or mutates Legacy7. The append-only
`ctl.MddMacProductionResumeEvent` row records the actual SQL UTC resume time
and the explicit idempotency key; a repeated key is idempotent, while a
partial enablement is disabled and unloaded again.

The current review did not execute resume. The fresh artifact below is a V3
sanitized capture, but it is unsigned and its administrative identity,
permissions, backup, capacity, quota, and SQL-history checks are
`NOT_PROVEN` because no separately configured `MSSQL_ADMIN_*` identity was
available. The controlled Windows Authentication capture and detached signer
are available for the one-time operator evidence setup. The exact live SQL
skip-audit transaction is also not claimed: its validator returns `NOT_RUN`
until an administrator runs the rollback-only `--live` path. The detailed
operator sequence is in the [Tiered50 resume runbook](tiered50-resume-runbook.md).

The source correction pass preserves the historical first-heartbeat record and
does not change `TIERED50_PANEL_V1`, target identities, quota permits, October
bootstrap authorization, Legacy7 retirement, or Power BI. It now accounts for
the absolute UTC bucket deadline from actual post-preflight execution time,
rejects production `--at` timestamps, fails closed when SQL UTC is unavailable,
rechecks permit expiry with SQL UTC immediately before transmission, narrows
the Stage 14 architecture-text exception to the approved SQL adapter and
signed administrative-evidence identity validator, and removes the Tiered50
plist's `ThrottleInterval` override.

The resume lifecycle now obtains SQL UTC from the locked administrator session
and repeats quota, bootstrap/provider-period, pending-permit, competing-writer,
signed-evidence, runtime-instance, LaunchAgent, and session-lock checks directly
before enablement. An existing idempotency event with a stopped LaunchAgent is
reported as `IDEMPOTENT_ALREADY_RECORDED` plus
`IDEMPOTENT_EVENT_RECORDED_SERVICE_STOPPED` and returns a nonzero operational
exit; it does not create a duplicate event or silently reactivate the service.
A loaded service without a validated collection cycle is reported as loaded,
not as `ACTIVE AND VALIDATED`.

The timing contract now distinguishes the derived UTC calendar boundary from
observed Python module/process entry, main-function entry, preflight start and
completion, settings/Collector construction, dispatcher entry, first target
claim, first HTTP transmission, and completion. The launchd trigger itself is
recorded as not independently observed. Wall-clock budget remaining is
monotone non-increasing across clock corrections, while monotonic time remains
the duration source. Late pending targets use one bounded idempotent skip-audit
transaction with no retroactive target claims. The current fixed capacity is
also explicit: 19 due targets at cap-2 and 30 seconds require ten request
batches/300 seconds before the five-second safety margin and SQL, parsing, and
audit overhead, so worst-case full coverage is not guaranteed.
The disposable SQLite benchmark in
[`tiered50-skip-audit-benchmark-20261008.json`](tiered50-skip-audit-benchmark-20261008.json)
measured 19 current batch audits at 1.582 ms versus 7.033 ms for the former
per-target start/complete shape (4.44x in that local run); this is an overhead
comparison, not a production SQL latency percentile.

This correction pass also fixed the SQL Server skip-audit construction defect:
the source now binds all 28 target-audit columns in schema order, emits 28
`%s` parameters per row, and rejects batches above 75 rows so no statement can
exceed SQL Server's 2,100-parameter limit. The `MERGE` is insert-only for
missing audit identities under `HOLDLOCK`; repeated batches are idempotent,
existing running or terminal audits are not rewritten, and no target claim is
created for a pre-claim skip. The offline DB-API statement-path regression
covers 1/16/18/19-row batches, interrupted retries, and existing-audit
conflicts. Actual SQL Server execution of this corrected statement remains
NOT_PROVEN because no separately authorized administrative SQL identity was
available in the current capture.

Unexpected live-client exceptions now carry an explicit transport-entry
boundary. Proven pre-network failures release their permit; exceptions after
the live open/read/close path conservatively retain quota without claiming
that a request was physically transmitted or that an HTTP response succeeded.
Attempt-record persistence remains atomic; a persistence failure leaves the
pending permit unavailable for crash recovery.

The corrected Tiered50 artifact was installed locally in the disabled state and
validated with `plutil`: `RunAtLoad=false`, twelve wall-clock minute entries
`00..55`, no `StartInterval`, no `ThrottleInterval`, and no loaded launchd
service. The read-only live preflight observed 43 materialized active rows
(sampling IDs 8–50), the limited `CologneTransitCollector` login, no competing
writer, current October quota accounting, and the Windows VM. It issued no MDD
requests and performed no SQL writes.

The 2026-10-08 read-only evidence capture at
[`tiered50-production-evidence-current-20261008.json`](tiered50-production-evidence-current-20261008.json)
identified the exact SQL failures instead of aborting at the first exception.
That historical capture at `2026-10-08T19:30:13.551208Z` records separate
connection, identity, permission, DMV, backup, and operational-history states
without converting denied reads into zeros. It used the installed runtime
login `CologneTransitCollector` for least-privilege evidence and reports
`admin_role_source=MSSQL_ADMIN_*_REQUIRED`. No separately configured
`MSSQL_ADMIN_*` identity or matching Keychain item was available, so the
precise blocker is `MISSING_ADMIN_CREDENTIALS_OR_PERMISSIONS`; no privileged
query was falsely marked PASS. The artifact is intentionally unsigned until
the operator captures and signs the separate administrative evidence. `log_space` is
`null`, capacity and backup are `NOT_PROVEN`, and the administrative
identity/permission gate is `NOT_PROVEN`. The earlier
capture's sanitized observations of errors 297, 229, and 3013 remain
historical unresolved findings; they were not reclassified as corruption or
missing permission without a correctly authorized rerun. No SQL writes or MDD
requests were issued. Tiered50 remains stopped, zero stations are currently
collecting, and no catch-up request was issued. The historical first heartbeat
remains the preserved 2-of-18 record below; a new successful six-bucket cycle
has not been claimed.
The permission gate now requires explicit evidence for the version-appropriate
server DMV state permission, database definition/catalog access, `msdb` backup
history reads, operational-history reads, and `CREATE ANY DATABASE` or an
equivalent `RESTORE VERIFYONLY` authorization; a successful permission query
with false grants is not treated as authorization.

The administrator migration validation passed with historical preservation
checks intact. The dedicated `CologneTransitCollector` login received the
runtime permission contract (20 grants), and runtime preflight confirmed the
approved interpreter, keychain resolution, MDD key format, SQL runtime surface,
and absence of broad roles. Activation writes were performed only through the
administrator connection; an attempted runtime-login write was rejected by
least privilege and made no durable change.

The bounded October authorization was explicitly approved at
`2026-10-08T12:19:11Z` and registered in SQL. It uses the dashboard observation
of `554` requests against a `250000` monthly limit, plus `165` confirmed local
permits, `14` uncertain permit rows, `0` pending permits, and `73` explicitly
identified pre-bootstrap `LIVE_RESEARCH_SINGLE_1` attempts: `806` requests
accounted conservatively at approval. Headroom is `209194` at the normal
operational maximum, `206694` at the retry-protection boundary, and `249194`
at the provider ceiling. The original proposal remains `approved: false`; the
separate explicit approval and SQL registration are recorded in the bootstrap
evidence artifact.

The provider cutoff, freshness, reset timezone/bounds, counter semantics,
post-cutoff usage, and external reservations remain unverified. Automatic
future-month rollover is implemented in SQL and the runtime, but it remains
fail-closed until either an exact provider reset contract is verified and
registered or the explicitly approved bounded policy is registered.
The October policy stop remains `2026-10-30T23:00:00Z` and is not extended by
rollover.

One reusable approval candidate is now evaluated explicitly: assume (only with
one-time operator approval) a calendar-month reset somewhere in the global
UTC-12 through UTC+14 envelope, wait 30 minutes after the latest possible
month start, and authorize no earlier than 12:30 UTC on the first day of a new
month. For November 2026 this means a latest safe start of
`2026-11-01T12:00:00Z`, authorization at `2026-11-01T12:30:00Z`, and an
earliest next-period risk boundary/safe-period end of
`2026-11-30T10:00:00Z`. The next authorization is
`2026-12-01T12:30:00Z`, so the actual normal intermonth collection gap is
26 hours 30 minutes (26.5 hours), derived from those two UTC period
boundaries. The already-approved October bootstrap ending at
`2026-10-30T23:00:00Z` would create a 37 hours 30 minutes gap before the
proposed November authorization. The candidate is not provider-confirmed,
not operator-approved, and has not been registered or activated; the full
machine-readable boundary and gap derivation is in
[`mdd-quota-reset-contract.json`](mdd-quota-reset-contract.json).

The first scheduled Tiered50 bucket was `5971541`: 18 target audits ran, 2
succeeded and 16 were skipped by the heartbeat budget, with 2 HTTP responses,
2 persisted target audits, 2 confirmed permits, no retries, no transport
errors, and no missing permits. The cadence/persistence validation therefore
failed, the LaunchAgent was stopped, and an auditable `OUTAGE` record was
written. No further runtime requests were issued; Legacy7 was not reactivated.

Offline validation passed for the 50-target manifest, exact phase sequence
`[19, 18, 18, 18, 19, 18]`, 110 nominal requests per 30-minute cycle, 220 per
hour, concurrency 2, maximum attempts 2, UTC bucket selection, no catch-up,
durable claims and leases, quota boundary behavior, source-faithful parsing
and SQL persistence, RecordedAtTime lineage, Situation/Attribute evidence,
and sanitized evidence handling. A complete successful Tiered50 validation
cycle was not achieved.

## October quota-baseline reconciliation

The screenshot is recorded as observed facts plus a dated operator attestation in
[`mdd-october-monitoring-dashboard-evidence.json`](mdd-october-monitoring-dashboard-evidence.json).
Its visible `554` and `250000` values are retained; the nominal display
headroom is not used as operational headroom. The original image is retained
at `mdd-october-monitoring-dashboard.png` with SHA-256
`9b25d4d28cf57f77feccb3b1da30e7e2c8629cec2319ff5a46877e3fac23088f`. The
registered bootstrap overcounts the dashboard value with every locally observed
October component through the explicit approval cutoff. The latest runtime
ledger read shows `808` conservatively accounted October attempts (`721`
confirmed HTTP responses plus `87` uncertain live attempts), `2` post-cutoff
local attempts, and `0` pending permits; this is not provider-wide usage
proof. No provider measurement
cutoff, freshness, reset period/timezone, external-consumer reservation, or
operational headroom has been fabricated.
The existing SQL HTTP attempts,
quota permits, collector activity, and prior reconciliation history are read
as separate evidence by
[`scripts/reconcile_mdd_shared_quota.py`](../../scripts/reconcile_mdd_shared_quota.py),
but they cannot establish provider-wide consumption by themselves or replace
the missing provider metadata.

The tool requires a sanitized provider report or explicit provider confirmation
containing cumulative usage, confirmed/unknown components, an explicit
unexplained-discrepancy count, measurement cutoff, billing-period timezone and
bounds, shared-consumer coverage, operating-model evidence, and a future
staleness deadline. The reconciliation path selects rows by those persisted
provider-period bounds; it never uses the host UTC calendar month as an
authorization boundary. It is read-only by default;
`--apply --approve` is the only registration path, and SQL registration is
restricted to a separately authorized administrator. The append-only
`ctl.MddMacSharedQuotaReconciliation` history preserves the evidence hash,
timestamps, local cutoff, permit reconciliation, and conservative result.

The recorded SQL identity `sa` is administrative evidence only; it is not the
deployed runtime identity. The limited-login, schema, privilege, and LaunchAgent
checks are recorded separately, and the runtime is currently stopped after the
failed first scheduled heartbeat. The bounded proposal is recorded in
[`mdd-october-conservative-bootstrap-proposal.json`](mdd-october-conservative-bootstrap-proposal.json);
its `approved` field remains false. The separate SQL authorization and
machine-readable registration evidence are in
[`mdd-october-conservative-bootstrap-evidence.json`](mdd-october-conservative-bootstrap-evidence.json).

In the current capture, no administrative identity was accessed: the only
effective SQL identity observed was the restricted runtime login
`CologneTransitCollector`, and the admin evidence status is
`MISSING_ADMIN_CREDENTIALS_OR_PERMISSIONS`.

Power BI and analytics permissions were not modified. No manual Heumarkt
request was issued. Full sanitized machine-readable evidence is in
[`tiered50-production-evidence.json`](tiered50-production-evidence.json),
the historical correction capture
[`tiered50-production-evidence-current-20261008.json`](tiered50-production-evidence-current-20261008.json),
the latest current refresh
[`tiered50-production-evidence-current-20261009.json`](tiered50-production-evidence-current-20261009.json),
[`mdd-october-conservative-bootstrap-evidence.json`](mdd-october-conservative-bootstrap-evidence.json),
and [`tiered50-deployment-validation-evidence.json`](tiered50-deployment-validation-evidence.json).

The review artifact is `/Users/admin/Documents/cologne-transit-intelligence-review.zip`;
its final SHA-256, byte size, member count, and forbidden-metadata count are
returned with the handoff.
