# External source contracts and data dictionary

## MDD NRW / TRIAS realtime API

The provider integration is a controlled HTTP request/response contract (TRIAS 1.2 semantics). Its exact request envelope, authentication field, version handling, observed response shape and retention rules are documented in [`source-contracts`](source-contracts/README.md). An API key is necessary for live access; it is **not included** in the repository. A validated provider `HTTP 200` is not a blanket guarantee of populated event fields. Quotas apply to HTTP transmissions/attempts as defined by the provider; do not confuse `NumberOfResults` (per-response requested entries) with count of HTTP requests.

Bounded Stage 12 compatibility probing used all 50 source parent StopPointRefs with `NumberOfResults=1` and `MaxAttempts=1`; all 50 yielded HTTP 200 and parse success in that test. Production intended N=20/30 was approved separately by static-density evidence and limited response trials; no long-term completeness guarantee was established.

## GTFS static feed

The VRS static feed supplies versioned agency, routes, trips, service dates, stop/station identities and stop times. `location_type=1` parent identities and child stops were validated before selection. Stage 12 used static snapshot `VERSION__20260930_0053` to select among **864 Cologne parent candidates** with **1,146,295 scheduled parent-level events**, from available feed days, not passenger activity. The seven Legacy7 anchors were retained. GTFS schedule identities do not automatically equal MDD vehicle-journey identities.

## Relational dictionary (representative)

| Field/group | Meaning | Prohibited inference |
| --- | --- | --- |
| `RunId` | one logical collector execution context | Complete station coverage |
| `AuditId` / `SamplingTargetId` | target outcome and its panel/era | Station was always active |
| `SnapshotId` | persisted response observation | Unique real-world trip |
| `ObservedAtUtc` | collector capture timestamp | Actual arrival/departure |
| `TimetabledTimeSourceText` | source scheduled time | Event completed |
| `EstimatedTimeSourceText` | forecast from provider | Recorded actual delay |
| `RecordedAtTimeSourceText` | actual/recorded field if provided | Non-NULL value where absent |
| `CallScope` | `THIS` / `PREVIOUS` / `ONWARD` | Identical stop event context |
| `TimingContext` | `ARRIVAL` / `DEPARTURE` | Interchangeable measurements |
| `SourceEventIdentityStatus` | observed identity sufficiency | Fully confirmed GTFS alignment |
| `ParserVersion` / payload hash | source processing/provenance metadata | Permission to republish raw data |
| `ErrorCategory` / `ParserGap` | known failure or extraction limitation | Zero failures in unobserved time |

Canonical definitions and individual SQL column types are in the checked-in schema SQL; avoid treating this summary as an exhaustive generated schema export. Upstream schema/version changes require a new contract inspection and compatible migration. Preserve raw/source timestamps and information available at extraction time as different dimensions of truth.

## Rights and privacy

Publicly accessible timetable information is **not** a general redistribution license for raw MDD responses. Review specific GTFS/MDD terms, retention permissions, data minimization and any embedded user/person identifiers **before** sharing source observations or sample exports. The checked-in examples are non-secret configuration/evidence; intentionally no provider payload dump or DB backup is committed.
