# Tiered50 deployment validation report

Status: **BLOCKED — TIERED50 INACTIVE; LEGACY7 RETIRED**

The approved `TIERED50_PANEL_V1` configuration remains unchanged. The SQL
Server migration and exact Tiered50 materialization are applied: all 50 panel
records are represented, historical IDs 1–7 are preserved, and new IDs 8–50
are prepared but disabled. The Tiered50-only macOS runtime is installed in a
separate versioned bundle and its LaunchAgent remains disabled until every
activation gate passes.

Fresh validation confirms the final Legacy7 run, six consecutive expected
Legacy7 buckets with accounted and successful SQL persistence, current SQL
capacity, the latest checksum-verified native full backup, launchd state,
Mac power/uptime evidence, and an observable VMware guest. Legacy7 was then
unloaded and disabled. No Windows collector or competing Legacy7 writer is
active, and no Tiered50 MDD request has been issued.

Activation remains blocked because the supplied official Monitoring screenshot
does not establish a registrable provider-wide October shared-credential
baseline. The dashboard visibly reports `554` cumulative requests against a
`250000` monthly limit for `Nutzungszweck: Auskunfts- und
Informationssysteme`, which is a nominal display headroom of `249446`. It does
not establish the exact measurement cutoff or refresh time, credential and
consumer scope, reset timezone/bounds, accounting model, post-cutoff usage,
outstanding permits, or external reservations. The durable SQL quota contract
therefore denies both INITIAL and RETRY permits rather than treating those
unknowns as zero. The cutover is recorded as an auditable outage; Tiered50
remains inactive and Legacy7 is not a fallback.

Offline validation passed for the 50-target manifest, exact phase sequence
`[19, 18, 18, 18, 19, 18]`, 110 nominal requests per 30-minute cycle, 220 per
hour, concurrency 2, maximum attempts 2, UTC bucket selection, no catch-up,
durable claims and leases, quota boundary behavior, source-faithful parsing
and SQL persistence, RecordedAtTime lineage, Situation/Attribute evidence,
and zero MDD requests. The first real Tiered50 cycle is **NOT RUN** because
activation is blocked.

## October quota-baseline reconciliation

The screenshot is recorded as observed-facts-only evidence in
[`mdd-october-monitoring-dashboard-evidence.json`](mdd-october-monitoring-dashboard-evidence.json).
Its visible `554` and `250000` values are retained; the nominal display
headroom is not used as operational headroom. The original image file is not
available in this checkout, so no image SHA-256 is claimed. No provider scope,
measurement cutoff, reset period/timezone, external-consumer reservation, or
operational headroom has been fabricated. The existing SQL HTTP attempts,
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

The recorded SQL identity is `sa` and is administrative evidence only; it is
not proof that the deployed runtime can use `CologneTransitCollector`. The
actual limited-login, schema, privilege, quota, and LaunchAgent checks are
activation gates and were not claimed as passed here. Until the provider
report and those gates are verified, the baseline remains unregistered, both
permit classes fail closed, and Tiered50 remains inactive.

Power BI and analytics permissions were not modified. No manual Heumarkt
request was issued. Full sanitized machine-readable evidence is in
[`tiered50-production-evidence.json`](tiered50-production-evidence.json) and
[`tiered50-deployment-validation-evidence.json`](tiered50-deployment-validation-evidence.json).

The review artifact is `/Users/admin/Documents/cologne-transit-intelligence-review.zip`;
its final SHA-256, byte size, member count, and forbidden-metadata count are
returned with the handoff.
