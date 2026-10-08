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

Activation is blocked because the complete provider-wide October
shared-credential quota baseline is unavailable. The durable SQL quota
contract therefore denies both INITIAL and RETRY permits rather than treating
unknown shared usage as zero. The cutover is recorded as an auditable outage;
Tiered50 remains inactive and Legacy7 is not a fallback.

Offline validation passed for the 50-target manifest, exact phase sequence
`[19, 18, 18, 18, 19, 18]`, 110 nominal requests per 30-minute cycle, 220 per
hour, concurrency 2, maximum attempts 2, UTC bucket selection, no catch-up,
durable claims and leases, quota boundary behavior, source-faithful parsing
and SQL persistence, RecordedAtTime lineage, Situation/Attribute evidence,
and zero MDD requests. The first real Tiered50 cycle is **NOT RUN** because
activation is blocked.

Power BI and analytics permissions were not modified. No manual Heumarkt
request was issued. Full sanitized machine-readable evidence is in
[`tiered50-production-evidence.json`](tiered50-production-evidence.json) and
[`tiered50-deployment-validation-evidence.json`](tiered50-deployment-validation-evidence.json).

The review artifact is `/Users/admin/Documents/cologne-transit-intelligence-review.zip`;
its final SHA-256, byte size, member count, and forbidden-metadata count are
returned with the handoff.
