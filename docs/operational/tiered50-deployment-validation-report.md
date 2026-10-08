# Tiered50 deployment validation report

Status: **BLOCKED — TIERED50 STOPPED AFTER FAILED FIRST SCHEDULED HEARTBEAT; LEGACY7 RETIRED**

The approved `TIERED50_PANEL_V1` configuration remains unchanged. The SQL
Server migration and exact Tiered50 materialization are applied: all 50 panel
records are represented, historical IDs 1–7 are preserved, and new IDs 8–50
are present in the additive target table. Administrator activation committed
the Tiered50 target state and sampling era, but the scheduled runtime was
stopped after its first heartbeat failed the cadence gate. The separate
versioned LaunchAgent is disabled and zero stations are currently collecting.

Fresh validation confirms the final Legacy7 run, six consecutive expected
Legacy7 buckets with accounted and successful SQL persistence, current SQL
capacity, the latest checksum-verified native full backup, launchd state,
Mac power/uptime evidence, and an observable VMware guest. Legacy7 was then
unloaded and disabled. No Windows collector or competing Legacy7 writer is
active, and no manual MDD or Heumarkt request was issued.

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
fail-closed until an exact provider reset contract is verified and registered.
The October policy stop remains `2026-10-30T23:00:00Z` and is not extended by
rollover.

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
October component through the explicit approval cutoff. No provider measurement
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

Power BI and analytics permissions were not modified. No manual Heumarkt
request was issued. Full sanitized machine-readable evidence is in
[`tiered50-production-evidence.json`](tiered50-production-evidence.json),
[`mdd-october-conservative-bootstrap-evidence.json`](mdd-october-conservative-bootstrap-evidence.json),
and [`tiered50-deployment-validation-evidence.json`](tiered50-deployment-validation-evidence.json).

The review artifact is `/Users/admin/Documents/cologne-transit-intelligence-review.zip`;
its final SHA-256, byte size, member count, and forbidden-metadata count are
returned with the handoff.
