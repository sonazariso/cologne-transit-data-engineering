# Data dictionary and source-quality boundaries

This document describes the available source-data contract, not a continuous high-quality analytical dataset.

## Representative field meanings

| SQL field | Meaning | Analysis caveat |
| --- | --- | --- |
| `SnapshotId` | source snapshot surrogate identifier | Not a vehicle event ID |
| `RunId`, `AuditId` | collector run and target-audit lineage | Enables quality and failure auditing |
| `ObservedAtUtc` | collector observation/capture time | Not a planned or actual stop event time |
| `SamplingTargetId`, `TargetIdentity` | selected station/target lineage | Panel coverage must be validated per era |
| `CallScope` | `THIS`, `PREVIOUS`, `ONWARD` | Do not merge call contexts indiscriminately |
| `TimingContext` | `ARRIVAL`, `DEPARTURE` | Cannot compare without aligned context |
| `TimetabledTimeSourceText` | source-provided planned stop time | Compare only on a validated service date/event identity |
| `EstimatedTimeSourceText` | source-provided prediction | Changes over time; *not* actual arrival |
| `RecordedAtTimeSourceText` | source-provided recorded/actual time if explicitly supplied | Missing in some observations; do not infer |
| `SourceEventIdentity`, `SourceEventIdentityStatus` | event grouping and validation status | Ambiguity/missingness constrain deduplication |
| `SourcePathsJson` / `...Gzip` | source-path provenance | Physical representation may be compressed; use logical view |
| `ParserVersion`, `PayloadHashSha256` | reproducibility / provenance | Hash checks should be computed on original agreed bytes |
| `ErrorCategory`, `ParserOutcome`, `PersistenceOutcome` | pipeline outcome / data-quality evidence | Errors/skips are not zero delays |

## What an analyst may present from a qualified sample

- API acquisition, ingestion attempt success and failure distribution.
- Completeness percentages by field, source event context, panel and observed interval.
- Example extraction and SQL joins from snapshot to child records, with verified multiplicity.
- A limited exploratory comparison of source **estimated** versus **timetabled** stop times, clearly described as a biased or nonrepresentative sample when appropriate.

## What cannot be claimed

- Full Cologne transport network performance, punctuality, statistically valid citywide station rankings or trend changes from this fragmented population.
- Confirmed actual arrival/departure delays without validated source `RecordedAtTime` evidence and matching.
- A completed or validated SQL Server dimensional warehouse from this repository.
- Continuous 50-station daily collection.

## Demonstration sample manifest requirements

The curated sample should include: collection context and observation UTC range; exact SnapshotIds and count; per-family source counts; source identities/status; SHA-256 of retained export; verification commands; a record of removed identifiers or fields and licensing/privacy approval. Choose **exactly 10 complete snapshots** rather than orphaning timing rows or sampling unmatched scalar fields. Prefer successful historically documented Legacy7 windows. Do not cherry-pick and then present sample frequencies as representative prevalence.

