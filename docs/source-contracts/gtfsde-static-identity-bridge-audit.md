# GTFS.de Static Identity Bridge Audit

**Status:** `PARTIAL — S18.1 host evidence recorded; Köln Hbf identity resolution pending`
**Stage position:** Stage 0 — **APPROVED**; Stage 1 — **APPROVED**; Stage 2 —
**APPROVED**; Stage 3 — **NOT STARTED**
**Overall bridge status:** `PARTIAL` until Köln Hbf is resolved.
**Production decision:** none; this audit does not approve GTFS-Realtime as a
production source or replace the project's existing static GTFS.

## Purpose

The successful S18 current-feed inventory proved that the public GTFS.de
realtime stream is reachable and parses as GTFS-Realtime, but its stop IDs are
numeric examples rather than the project's legacy VRS IFOPT identifiers. A
zero match for `de:05315:11201` or `de:05315:11111` therefore cannot establish
that Cologne is absent.

This audit creates a read-only identity bridge for:

- Köln Hbf; and
- Köln Neumarkt.

It maps the public GTFS.de realtime namespace to matching GTFS.de static stop
IDs so that S18 lifecycle discovery can use exact IDs. It does not integrate
the GTFS.de static feed into the warehouse, replace the project's VRS/
go.Rheinland static GTFS, or claim that those feeds are interchangeable.

## Source and host-only execution

The static source is the official current free Germany local/public-transit
feed:

<https://download.gtfs.de/germany/nv_free/latest.zip>

GTFS.de documents that its public realtime stream matches the static feeds it
offers. That fact supports testing the two GTFS.de namespaces together; it is
not evidence that either feed matches the project's existing VRS/go.Rheinland
source.

The ZIP is large and must not be downloaded by Codex or committed to Git. The
S18.1 host session already downloaded the current ZIP; the S18.2 diagnostic
uses that existing local artifact and must not redownload it. The script still
supports downloading only when a future host operator explicitly runs
`download-and-inspect`. Downloaded ZIPs are stored under a private temporary
directory of the form
`/private/tmp/cologne-transit-gtfsde-static-XXXXXXXX/`, with directory mode 700
and file mode 600 where the host permits it. The archive is inspected directly
with `zipfile`; it is not extracted wholesale.

The validated host runtime for the diagnostic tooling is Python 3.9.6 with
`gtfs-realtime-bindings==2.2.0` and `protobuf==6.33.6`. The bridge uses Python
standard-library code; realtime verification reuses the installed GTFS-RT
bindings. No system Python or pip upgrade is required.

The script is:

[`scripts/gtfsde_static_identity_bridge_probe.py`](../../scripts/gtfsde_static_identity_bridge_probe.py)

It supports:

```bash
python3 scripts/gtfsde_static_identity_bridge_probe.py \
  inspect-local \
  --gtfs-zip /private/tmp/gtfsde-nv-latest.zip

# S18.2: bounded Köln Hbf discovery against the already-downloaded ZIP
python3 scripts/gtfsde_static_identity_bridge_probe.py \
  inspect-local \
  --gtfs-zip /private/tmp/cologne-transit-gtfsde-static-9euwatid/gtfsde-nv-latest.zip \
  --station hbf \
  --diagnostic-candidates

# S18.3: local-only railway-service/topology diagnostic against the same ZIP
python3 scripts/gtfsde_static_identity_bridge_probe.py \
  inspect-local \
  --gtfs-zip /private/tmp/cologne-transit-gtfsde-static-9euwatid/gtfsde-nv-latest.zip \
  --station hbf \
  --rail-diagnostic

python3 scripts/gtfsde_static_identity_bridge_probe.py \
  download-and-inspect
```

The S18.2 command is local-only: it reads the supplied ZIP, does not use
`--verify-realtime`, and makes zero HTTP requests. The temporary path above is
for the current host diagnostic session only; it is not a durable source path.

The `--rail-diagnostic` command is also local-only. It streams `stops.txt`,
`stop_times.txt`, `trips.txt`, `routes.txt`, and `agency.txt` directly from the
ZIP. It uses a bounded 600-metre radius around the source coordinates of the
known `106448` and `437741` parent stops as supporting candidate evidence; it
does not use coordinates alone, online geocoding, fuzzy matching, or any HTTP
request. It reports exact stop-time, trip, route-type, route-label, agency, and
parent/child evidence in deterministic JSON.

The optional `--verify-realtime` flag is off by default. If explicitly used,
the script makes exactly one GET request to
<https://realtime.gtfs.de/realtime-free.pb>, then checks exact static stop IDs
against current `StopTimeUpdate.stop_id` values. There are no retries and no
redirect following.

## Conservative station resolution

Only `stops.txt` is required for the first bridge pass. The script streams it
from the ZIP and preserves these source fields for every reported candidate:

`stop_id`, `stop_name`, `parent_station`, `location_type`, `platform_code`,
`stop_lat`, and `stop_lon`.

Resolution proceeds in bounded, reviewable steps:

1. normalize Unicode and whitespace safely and search exact `stop_name` values;
2. report all exact candidates;
3. inspect `parent_station` and `location_type`;
4. inspect child/platform stops under a supported parent candidate; and
5. report variant-name matches separately, without silently treating them as
   exact-name identity.

When exact and configured-variant matching fails for Köln Hbf, the opt-in
`--diagnostic-candidates` path performs a bounded token-based search in
`stops.txt`. It requires a `Köln` token together with an `Hbf` or
`Hauptbahnhof` token, tolerates punctuation, brackets, platform qualifiers,
and suffixes, and does not use unrestricted fuzzy matching. Each reported
candidate includes its normalized name and match reason. The diagnostic then
identifies station-typed candidates, groups children by `parent_station`,
reports child counts and all child IDs for plausible parents, and preserves the
source names. A parent is selected only when one clearly matching station
parent has consistent child evidence; otherwise the result is
`STATION_MAPPING_AMBIGUOUS`.

The script does not make an automatic fuzzy choice. Multiple plausible parent
stations produce `STATION_MAPPING_AMBIGUOUS`. A clear exact or parent/child
mapping produces `STATION_MAPPING_RESOLVED`; no supported candidate produces
`STATION_MAPPING_NOT_FOUND`.

Resolved lifecycle IDs are the child stop IDs when a selected parent has
children. If no children are present, the clearly supported stop/station ID is
retained. Candidate details and the selected parent are emitted together so the
host operator can review the identity before lifecycle execution.

## S18.3 railway Hbf service/topology diagnostic

The new `--rail-diagnostic` mode keeps the two known adjacent KVB parents
separate from the railway identity:

- `106448` is `Köln Dom/Hbf`.
- `437741` is `Köln Breslauer Platz/Hbf`.
- They are distinct adjacent KVB stop complexes, not interchangeable railway
  station identities.
- Neither parent is approved or selected as equivalent to railway Köln Hbf.
- Neumarkt remains resolved separately through parent `493082` and its five
  child IDs; this diagnostic does not change that result.

The host run against the existing ZIP used a 600-metre radius around the
coordinates of `106448` and `437741` as supporting evidence and found `35`
bounded candidates. It streamed the static topology and service joins through
`stops.txt`, `stop_times.txt`, `trips.txt`, `routes.txt`, and `agency.txt`.
No network request was made.

The strongest current railway-service evidence is attached to the excluded
KVB parent `437741`, not to a separate approved railway parent:

| parent | rail-served child stop IDs | observed source route labels | agencies |
| --- | --- | --- | --- |
| `437741` — Köln Breslauer Platz/Hbf | `160367`, `201741`, `398532`, `590767`, `84746` | S-Bahn: `S11`, `S12`, `S19`; regional: `RB26`, `RB27`, `RB38`, `RB48`, `RE5`, `RE5X`, `RE7`, `RE8`, `RE9`, `RE9X`, `RE1X`, `RE22`, `RE22X` | `100` — National Express; `264` — Nahreisezug |

The same parent also has non-rail child IDs `104653`, `246230`, `294170`,
`379472`, `492942`, `508538`, and `594735`; those are retained separately.
Parent `106448` has no S-Bahn/RE/RB evidence in this run; its child IDs
`134639`, `578073`, and `84484` remain non-rail transit-side evidence.
The source route records for the rail-labeled services carried `route_type=3`,
so the diagnostic used the explicit S-Bahn/RE/RB source labels rather than
assuming that `route_type=2` was the only possible rail signal. Stadtbahn and
bus services remain classified separately.

The run returned:

```text
classification: HBF_RAIL_EVIDENCE_INSUFFICIENT
candidate_count: 35
rail_served_candidate_count: 5
plausible_rail_parent_count: 0
```

This is intentionally not a railway-Hbf selection: the observed rail service
is under the explicitly excluded adjacent KVB parent `437741`, and no
non-excluded railway parent topology was found. Railway identity therefore
remains unresolved and requires further independent evidence.

## Output classifications

Per station, the bridge emits one of:

- `STATION_MAPPING_RESOLVED_AND_RT_CONFIRMED`
- `STATION_MAPPING_RESOLVED_RT_NOT_CHECKED`
- `STATION_MAPPING_RESOLVED_RT_NOT_OBSERVED`
- `STATION_MAPPING_AMBIGUOUS`
- `STATION_MAPPING_NOT_FOUND`
- `INCONCLUSIVE`

The aggregate result is one of:

- `GTFSDE_IDENTITY_BRIDGE_CONFIRMED`
- `GTFSDE_STATIC_MAPPING_RESOLVED_RT_PENDING`
- `GTFSDE_IDENTITY_BRIDGE_PARTIAL`
- `GTFSDE_IDENTITY_BRIDGE_NOT_ESTABLISHED`
- `INCONCLUSIVE`

`RT_CONFIRMED` means only that a resolved GTFS.de static stop ID occurred in
the current feed's `StopTimeUpdate` records. It does not establish actual
arrival semantics, historical validity, provider-wide coverage, or
compatibility with the project's VRS/go.Rheinland feed.

The local railway diagnostic uses a separate classification set:

- `HBF_RAIL_MAPPING_RESOLVED`
- `HBF_RAIL_MAPPING_AMBIGUOUS`
- `HBF_RAIL_MAPPING_NOT_FOUND`
- `HBF_RAIL_EVIDENCE_INSUFFICIENT`
- `INCONCLUSIVE`

`HBF_RAIL_MAPPING_RESOLVED` is permitted only when one non-excluded railway
parent is supported by static service and parent/child topology. The two known
KVB parents are never silently promoted by the diagnostic.

## Relationship to S18 lifecycle

After reviewing the bridge output, use the exact resolved IDs explicitly:

```bash
python3 scripts/gtfs_rt_feasibility_probe.py lifecycle \
  --station-name "Köln Hbf" \
  --stop-id <resolved_gtfsde_stop_id>
```

Repeat `--stop-id` for multiple child/platform IDs. When explicit IDs are
supplied, S18 matches exact GTFS.de IDs only; it does not use the legacy VRS
IFOPT prefixes as a fallback. The legacy `--station hbf` and
`--station neumarkt` forms remain available only for the original prefix-based
discovery path.

The bridge is a diagnostic identity step, not a warehouse source. S18 lifecycle
semantic guardrails remain unchanged: `StopTimeEvent.time` is estimated-or-
actual by schema, time stability is not actual-arrival proof, and explicit
provider semantics or independent evidence would be required before any
Actual Delay KPI could be considered.

## S18.1 evidence, S18.2 discovery, and S18.3 status

The S18.1 host evidence is:

**Neumarkt**

- mapping resolved;
- parent station: `493082`;
- five child stop IDs found: `106842`, `207108`, `441164`, `581903`, and
  `96309`;
- four child IDs were observed in the current GTFS-Realtime snapshot;
- those matches covered `252` matching TripUpdates; and
- one child ID was not observed in that one snapshot.

**Köln Hbf**

- the current exact/configured-variant name resolver did not find a mapping;
- this is an identity-resolution gap, **not** evidence that Hbf is absent from
  GTFS.de static or GTFS-Realtime; and
- focused static name, service, and parent/child discovery was required and is
  now recorded above.

The bridge therefore remains **`PARTIAL`** until Hbf is resolved. The host
inventory evidence remains separate from station identity proof: HTTP 200,
valid GTFS-RT 2.0 protobuf, numeric stop IDs, and no legacy VRS IFOPT prefix
matches were observed in that snapshot.

The S18.2 authorized existing-ZIP diagnostic (no download, no realtime request)
was:

```bash
cd /Users/admin/Documents/cologne-transit-intelligence
python3 scripts/gtfsde_static_identity_bridge_probe.py \
  inspect-local \
  --gtfs-zip /private/tmp/cologne-transit-gtfsde-static-9euwatid/gtfsde-nv-latest.zip \
  --station hbf \
  --diagnostic-candidates
```

The S18.3 local-only rail diagnostic used for the service/topology result was:

```bash
cd /Users/admin/Documents/cologne-transit-intelligence
python3 scripts/gtfsde_static_identity_bridge_probe.py \
  inspect-local \
  --gtfs-zip /private/tmp/cologne-transit-gtfsde-static-9euwatid/gtfsde-nv-latest.zip \
  --station hbf \
  --rail-diagnostic
```

Do not run `download-and-inspect`, S18 lifecycle, or historical S19 tooling
automatically, and do not interpret current GTFS.de IDs as valid for historical
snapshots without date-compatible static identity evidence.
