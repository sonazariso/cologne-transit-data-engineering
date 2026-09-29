# Power BI implementation boundary

Stage 11 is complete as an approved semantic design:

`COMPLETE — APPROVED DESIGN; PBIP/DESKTOP IMPLEMENTATION DEFERRED`

The authoritative contract is [the Stage 11 model design](../docs/stages/11-power-bi-model-design.md), with the [completion decision](../docs/stages/11-power-bi-model-completion-decision.md).

This directory intentionally contains no `.pbip`, `.tmdl`, `.pbir`, `.pbix`,
`model.bim`, datasource, or Desktop-generated metadata. The historical Power
BI project is not copied here.

## Later authorized workflow

After explicit implementation authorization, build a source-controlled
`PBIP + TMDL + PBIR` project from the Stage 11 contract. The future workflow
must:

1. bind only to an approved analytics/source projection;
2. preserve the documented table grains and single-direction relationships;
3. keep static VRS identities separate from canonical operational identities;
4. disable Power BI Auto Date/Time and use the governed Date dimension;
5. create the explicit measures and disconnected estimated-delay threshold;
6. keep HIGH-only operational evidence separate from all-confidence matching
   and Data Quality decisions;
7. use `Dim Sampling Era` only for the `Fact Matching Decision` population;
   Data Quality classifications carried by those decisions inherit that
   context. Do not treat the independent `Fact Data Quality Issue` or `Data
   Quality Issue Count` as Sampling-Era-filterable in the base model. Add a
   future relationship to that issue fact only after explicit `SamplingEraKey`
   lineage at its grain is established and projection grain, key uniqueness,
   historical-era semantics, and lineage are validated. Do not add an
   operational KPI relationship until a governed operational-event
   projection/bridge has met the same validation requirements, including
   events crossing era boundaries;
8. label Date-filtered matching coverage as service-date-scoped and warn that
   decisions without a valid service date remain separate Data Quality evidence;
   and
9. use Power BI Desktop for datasource binding, refresh, render QA,
   interaction QA, and save round-trip validation.

Do not copy the legacy model or hand-edit generated Desktop-owned metadata
without necessity. PostgreSQL/runtime work, data loading, and Stage 12 remain
outside this directory's current scope.
