# Dataset boundary and permitted engineering claims

This is a **data-contract** and **source-quality** artifact, not an invitation to build a dashboard or publish transit performance KPIs. For field-by-field meaning, see [Data contracts](DATA-CONTRACTS.md). For lineage and measurement definitions, see [Data quality](DATA-QUALITY-AND-OBSERVABILITY.md).

- A snapshot is a response observation, not a unique completed transit event.
- Observed capture time is not actual source event time; `EstimatedTime` is not `RecordedAtTime`.
- The 50 selected stations are a limited fixed panel; neither full Cologne network coverage nor passenger numbers can be inferred from them.
- Sample windows are discontinuous and contain historical research plus distinct Legacy7/Tiered50 sampling eras. Never compare them without sampling-method controls.
- Source Situation evidence is not confirmed cause; a missing platform value is unknown, not unchanged.
- The ten-snapshot SQL sample preserves an engineering example, **not** a statistically complete dataset.

No business intelligence, charts, public reporting product or actual-delay score is delivered in this repository.
