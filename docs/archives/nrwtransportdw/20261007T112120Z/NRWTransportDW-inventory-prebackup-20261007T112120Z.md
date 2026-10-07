# NRWTransportDW inventory and archive review

- Inventory captured (UTC): `2026-10-07T11:27:03.512706Z`
- Database: `NRWTransportDW`
- Dependency classification: `LEGACY_SAFE_TO_ARCHIVE`
- Migration result: `NO_NRWTRANSPORTDW_ASSET_REQUIRED_BY_ACTIVE_COLOGNE_PROJECT`
- Secrets included: `false`

## Capacity preflight

| Measure | Result |
|---|---:|
| SQL Server C: free | 8400379904 bytes (7.823 GiB) |
| Mac/shared archive free | 76891283456 bytes (71.611 GiB) |
| Estimated destination requirement for .bak + ZIP | 41355747328 bytes |
| Capacity gate | `True` |

## Active database file state

| Logical file | Type | Allocated MB | Used MB | Physical path |
|---|---|---:|---:|---|
| `CologneTransitIntelligence` | `ROWS` | 4022.25 | 3599.75 | `C:\Program Files\Microsoft SQL Server\MSSQL17.MSSQLSERVER\MSSQL\DATA\CologneTransitIntelligence.mdf` |
| `CologneTransitIntelligence_log` | `LOG` | 1032.0 | 1.2578125 | `C:\Program Files\Microsoft SQL Server\MSSQL17.MSSQLSERVER\MSSQL\DATA\CologneTransitIntelligence_log.ldf` |

## Object inventory

Schemas: **17**; tables: **21**; views: **7**; stored procedures: **1**; functions: **0**; synonyms: **0**; foreign keys: **4**; indexes: **25**.

| Schema | Table | Rows | Reserved MB | Used MB |
|---|---|---:|---:|---:|
| `cfg` | `DbRealtimeStation` | 2 | 0.070312 | 0.015625 |
| `dw` | `DimDate` | 165 | 0.140625 | 0.046875 |
| `dw` | `DimRoute` | 65 | 0.140625 | 0.03125 |
| `dw` | `DimStop` | 1236 | 0.578125 | 0.3125 |
| `dw` | `FactRealtimeStopPerformance` | 4656 | 2.328125 | 1.929687 |
| `dw` | `FactTripStop` | 8029550 | 842.835937 | 842.59375 |
| `stg` | `Agency` | 76 | 0.070312 | 0.03125 |
| `stg` | `Calendar` | 9430 | 0.632812 | 0.585937 |
| `stg` | `CalendarDates` | 406747 | 22.320312 | 22.28125 |
| `stg` | `DbRealtimeStopObservation` | 59687 | 22.03125 | 20.695312 |
| `stg` | `FeedInfo` | 0 | 0.0 | 0.0 |
| `stg` | `Routes` | 5949 | 1.445312 | 1.414062 |
| `stg` | `Stops` | 104632 | 17.570312 | 17.539062 |
| `stg` | `StopTimes` | 10907141 | 3187.84375 | 3187.265625 |
| `stg` | `StopTimesImport` | 10907141 | 1946.882812 | 1946.84375 |
| `stg` | `Trips` | 474978 | 87.507812 | 87.5 |
| `wrk` | `DbStationGtfsMapping` | 2 | 0.140625 | 0.03125 |
| `wrk` | `Phase1TripStops` | 8029550 | 1009.265625 | 1009.125 |
| `wrk` | `RouteClassification` | 175 | 0.070312 | 0.054687 |
| `wrk` | `ServiceDates` | 406747 | 15.945312 | 15.796875 |
| `wrk` | `TripInstances` | 26356341 | 11708.226562 | 11707.890625 |

### Views

`dw.vTripStopAnalytics`, `wrk.vDbRealtimeGtfsStrongMatch`, `wrk.vDbRealtimeLatestObservation`, `wrk.vDbRealtimeObservationChangeAnalytics`, `wrk.vDbRealtimeObservationChanges`, `wrk.vDbRealtimeStopPerformanceLoadSource`, `wrk.vPhase1TripStopsNormalized`

### Stored procedures

`dw.usp_LoadFactRealtimeStopPerformance`

### Functions

None

### Synonyms

None

## Date coverage

| Object | Column | Min | Max |
|---|---|---|---|
| `dw.DimDate` | `FullDate` | 2026-07-01 | 2026-12-12 |
| `wrk.ServiceDates` | `ServiceDate` | 2026-07-01 | 2026-12-31 |
| `wrk.TripInstances` | `ServiceDate` | 2026-07-01 | 2026-12-31 |
| `wrk.Phase1TripStops` | `ServiceDate` | 2026-07-01 | 2026-12-12 |
| `stg.DbRealtimeStopObservation` | `CapturedAt` | 2026-08-22T13:42:44.000000 | 2026-09-08T10:42:07.000000 |
| `dw.FactRealtimeStopPerformance` | `FirstCapturedAt` | 2026-08-22T13:42:44.000000 | 2026-09-08T10:05:22.000000 |
| `dw.FactRealtimeStopPerformance` | `LastCapturedAt` | 2026-08-22T13:42:44.000000 | 2026-09-08T10:42:07.000000 |
| `stg.Stops` | `LoadDateTime` | 2026-08-19T22:00:45.751814 | 2026-08-19T22:00:45.751814 |
| `stg.Routes` | `LoadDateTime` | 2026-08-19T21:57:55.947076 | 2026-08-19T21:57:55.947076 |

## Köln candidate decisions

| Source object | Relevant rows | Classification | Decision |
|---|---:|---|---|
| `NRWTransportDW.stg.Stops` | 2394 | `DO_NOT_MIGRATE_OUT_OF_SCOPE` | The active GTFS feed is authoritative for the current project; the 228 unmatched legacy IDs are feed-version/parent-record differences and no approved active target is missing. |
| `NRWTransportDW.dw.DimStop` | 70 | `DO_NOT_MIGRATE_DUPLICATE` | The station entities are already represented by the authoritative current GTFS warehouse and target model; copying the old feed would create parallel static structures. |
| `NRWTransportDW.wrk.DbStationGtfsMapping` | 1 | `DO_NOT_MIGRATE_DUPLICATE` | The current target is explicitly configured as de:05315:11201 (Köln Hbf); the legacy mapping adds no required active lineage. |
| `NRWTransportDW.stg.DbRealtimeStopObservation` | 33883 | `DO_NOT_MIGRATE_OUT_OF_SCOPE` | This is unique legacy rail-realtime history, but its DbEventId/StationEva grain is not the active source-faithful MDD contract; no current analytical consumer requires importing it, and migration would create a parallel legacy evidence structure. |
| `NRWTransportDW.dw.FactRealtimeStopPerformance` | 1932 | `DO_NOT_MIGRATE_OUT_OF_SCOPE` | It is a derived legacy fact layer with no active repository dependency; retaining the source backup preserves it without duplicating the active warehouse. |
| `NRWTransportDW.stg.Routes / wrk route logic` | 5949 | `DO_NOT_MIGRATE_OUT_OF_SCOPE` | The active Cologne project uses the current authoritative GTFS load and its own matching/warehouse logic; the legacy all-NRW route corpus is not a required active dependency. |
| `NRWTransportDW situation/attribute/service-section/parser-gap objects` | 0 | `DO_NOT_MIGRATE_OUT_OF_SCOPE` | No NRWTransportDW object provides a missing situation, attribute, service-section, or parser-gap asset. |

The review found no clearly required NRWTransportDW asset for the active Cologne project; no data or logic was transferred.

## Dependency review

Active repository/runtime/configuration references: `[]`.
SQL module references: `{'nrw_cross_database_dependencies': [], 'nrw_module_external_references': [], 'active_cross_database_dependencies': [], 'active_module_nrw_references': []}`.
SQL Agent NRW references: `[]`.
Linked servers: `[]`.
Windows Task Scheduler direct inspection: `UNAVAILABLE_FROM_MACOS`.
