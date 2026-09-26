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

python3 scripts/gtfsde_static_identity_bridge_probe.py \
  download-and-inspect
```

The S18.2 command is local-only: it reads the supplied ZIP, does not use
`--verify-realtime`, and makes zero HTTP requests. The temporary path above is
for the current host diagnostic session only; it is not a durable source path.

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

## S18.1 evidence and S18.2 next action

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
- focused static name and parent/child discovery is required.

The bridge therefore remains **`PARTIAL`** until Hbf is resolved. The host
inventory evidence remains separate from station identity proof: HTTP 200,
valid GTFS-RT 2.0 protobuf, numeric stop IDs, and no legacy VRS IFOPT prefix
matches were observed in that snapshot.

The next authorized host command is the existing-ZIP diagnostic (no download,
no realtime request):

```bash
cd /Users/admin/Documents/cologne-transit-intelligence
python3 scripts/gtfsde_static_identity_bridge_probe.py \
  inspect-local \
  --gtfs-zip /private/tmp/cologne-transit-gtfsde-static-9euwatid/gtfsde-nv-latest.zip \
  --station hbf \
  --diagnostic-candidates
```

Do not run `download-and-inspect`, S18 lifecycle, or historical S19 tooling
automatically, and do not interpret current GTFS.de IDs as valid for historical
snapshots without date-compatible static identity evidence.
