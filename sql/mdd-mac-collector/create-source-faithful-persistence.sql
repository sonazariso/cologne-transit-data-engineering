/*
    Additive source-faithful persistence for the macOS Python collector.

    This script deliberately creates a parallel, namespaced control/source
    contract.  The existing ctl.MddCollectorRun, sampling tables, realtime
    staging tables, warehouse tables, views, TVPs, and procedures are not
    altered.  The new contract is therefore safe to apply while the existing
    Windows collector is still running and makes the cutover boundary explicit.

    Execute through a SQL Server batch-aware runner.  "GO" is a client batch
    separator; it is not sent to the server as SQL text.
*/

SET NOCOUNT ON;

IF DB_NAME() <> N'CologneTransitIntelligence'
    THROW 51100, 'This migration must run in CologneTransitIntelligence.', 1;
GO

IF SCHEMA_ID(N'ctl') IS NULL OR SCHEMA_ID(N'stg') IS NULL OR SCHEMA_ID(N'analytics') IS NULL
    THROW 51101, 'The established ctl, stg, and analytics schemas are required.', 1;
GO

IF OBJECT_ID(N'ctl.MddMacCollectorRun', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddMacCollectorRun
    (
        MacCollectorRunId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_ctl_MddMacCollectorRun PRIMARY KEY CLUSTERED,
        RunId NVARCHAR(100) NOT NULL,
        PanelVersion NVARCHAR(100) NOT NULL,
        LogicalDueBucket BIGINT NOT NULL,
        Mode VARCHAR(40) NOT NULL,
        CollectionContext VARCHAR(80) NOT NULL,
        StartedAtUtc DATETIME2(7) NOT NULL,
        CompletedAtUtc DATETIME2(7) NULL,
        Status VARCHAR(40) NOT NULL
            CONSTRAINT DF_ctl_MddMacCollectorRun_Status DEFAULT ('RUNNING'),
        SummaryJson NVARCHAR(MAX) NULL,
        CreatedAtUtc DATETIME2(7) NOT NULL
            CONSTRAINT DF_ctl_MddMacCollectorRun_CreatedAtUtc DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT UQ_ctl_MddMacCollectorRun_RunId UNIQUE (RunId)
    );
END;
GO

/*
    A production lease is durable SQL state, not an inference from the
    latest RUNNING row.  The singleton row is acquired/released by the
    runtime inside short SQL transactions; the event table keeps stale-lease
    recovery auditable without rewriting collector history.
*/

IF OBJECT_ID(N'ctl.MddMacProductionLease', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddMacProductionLease
    (
        LeaseName VARCHAR(80) NOT NULL
            CONSTRAINT PK_ctl_MddMacProductionLease PRIMARY KEY CLUSTERED,
        CollectionContext VARCHAR(80) NOT NULL,
        RunId NVARCHAR(100) NULL,
        LeaseToken UNIQUEIDENTIFIER NOT NULL,
        AcquiredAtUtc DATETIME2(7) NOT NULL,
        ExpiresAtUtc DATETIME2(7) NOT NULL,
        LastHeartbeatAtUtc DATETIME2(7) NOT NULL,
        Status VARCHAR(20) NOT NULL,
        ReleasedAtUtc DATETIME2(7) NULL,
        CONSTRAINT CK_ctl_MddMacProductionLease_Status
            CHECK (Status IN ('ACTIVE', 'EXPIRED', 'RELEASED')),
        CONSTRAINT FK_ctl_MddMacProductionLease_Run
            FOREIGN KEY (RunId) REFERENCES ctl.MddMacCollectorRun (RunId)
    );
END;
GO

IF OBJECT_ID(N'ctl.MddMacProductionLeaseEvent', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddMacProductionLeaseEvent
    (
        LeaseEventId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_ctl_MddMacProductionLeaseEvent PRIMARY KEY CLUSTERED,
        LeaseName VARCHAR(80) NOT NULL,
        CollectionContext VARCHAR(80) NOT NULL,
        RunId NVARCHAR(100) NULL,
        EventAtUtc DATETIME2(7) NOT NULL,
        Outcome VARCHAR(40) NOT NULL,
        PreviousRunId NVARCHAR(100) NULL,
        PreviousStatus VARCHAR(20) NULL,
        CONSTRAINT FK_ctl_MddMacProductionLeaseEvent_Run
            FOREIGN KEY (RunId) REFERENCES ctl.MddMacCollectorRun (RunId)
    );
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'ctl.MddMacCollectorRun')
      AND name = N'IX_ctl_MddMacCollectorRun_Status_StartedAtUtc'
)
    CREATE INDEX IX_ctl_MddMacCollectorRun_Status_StartedAtUtc
        ON ctl.MddMacCollectorRun (Status, StartedAtUtc DESC);
GO

IF OBJECT_ID(N'ctl.MddMacDispatcherClaim', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddMacDispatcherClaim
    (
        PanelVersion NVARCHAR(100) NOT NULL,
        LogicalDueBucket BIGINT NOT NULL,
        RunId NVARCHAR(100) NOT NULL,
        ClaimToken UNIQUEIDENTIFIER NOT NULL,
        ClaimedAtUtc DATETIME2(7) NOT NULL,
        StaleAfterUtc DATETIME2(7) NOT NULL,
        Status VARCHAR(40) NOT NULL
            CONSTRAINT DF_ctl_MddMacDispatcherClaim_Status DEFAULT ('CLAIMED'),
        CompletedAtUtc DATETIME2(7) NULL,
        CONSTRAINT PK_ctl_MddMacDispatcherClaim PRIMARY KEY CLUSTERED (PanelVersion, LogicalDueBucket),
        CONSTRAINT FK_ctl_MddMacDispatcherClaim_Run
            FOREIGN KEY (RunId) REFERENCES ctl.MddMacCollectorRun (RunId)
    );
END;
GO

IF OBJECT_ID(N'ctl.MddMacDispatcherClaimEvent', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddMacDispatcherClaimEvent
    (
        DispatcherClaimEventId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_ctl_MddMacDispatcherClaimEvent PRIMARY KEY CLUSTERED,
        PanelVersion NVARCHAR(100) NOT NULL,
        LogicalDueBucket BIGINT NOT NULL,
        RunId NVARCHAR(100) NOT NULL,
        EventAtUtc DATETIME2(7) NOT NULL,
        Outcome VARCHAR(50) NOT NULL,
        ExistingStatus VARCHAR(40) NULL,
        CONSTRAINT FK_ctl_MddMacDispatcherClaimEvent_Run
            FOREIGN KEY (RunId) REFERENCES ctl.MddMacCollectorRun (RunId)
    );
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'ctl.MddMacDispatcherClaimEvent')
      AND name = N'IX_ctl_MddMacDispatcherClaimEvent_Bucket_EventAtUtc'
)
    CREATE INDEX IX_ctl_MddMacDispatcherClaimEvent_Bucket_EventAtUtc
        ON ctl.MddMacDispatcherClaimEvent (PanelVersion, LogicalDueBucket, EventAtUtc DESC);
GO

IF OBJECT_ID(N'ctl.MddMacTargetClaim', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddMacTargetClaim
    (
        PanelVersion NVARCHAR(100) NOT NULL,
        TargetIdentity NVARCHAR(200) NOT NULL,
        LogicalDueBucket BIGINT NOT NULL,
        RunId NVARCHAR(100) NOT NULL,
        ClaimToken UNIQUEIDENTIFIER NOT NULL,
        ClaimedAtUtc DATETIME2(7) NOT NULL,
        StaleAfterUtc DATETIME2(7) NOT NULL,
        Status VARCHAR(40) NOT NULL
            CONSTRAINT DF_ctl_MddMacTargetClaim_Status DEFAULT ('CLAIMED'),
        StartedAtUtc DATETIME2(7) NULL,
        CompletedAtUtc DATETIME2(7) NULL,
        CONSTRAINT PK_ctl_MddMacTargetClaim PRIMARY KEY CLUSTERED
            (PanelVersion, TargetIdentity, LogicalDueBucket),
        CONSTRAINT FK_ctl_MddMacTargetClaim_Run
            FOREIGN KEY (RunId) REFERENCES ctl.MddMacCollectorRun (RunId)
    );
END;
GO

IF OBJECT_ID(N'ctl.MddMacTargetClaimEvent', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddMacTargetClaimEvent
    (
        TargetClaimEventId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_ctl_MddMacTargetClaimEvent PRIMARY KEY CLUSTERED,
        PanelVersion NVARCHAR(100) NOT NULL,
        TargetIdentity NVARCHAR(200) NOT NULL,
        LogicalDueBucket BIGINT NOT NULL,
        RunId NVARCHAR(100) NOT NULL,
        EventAtUtc DATETIME2(7) NOT NULL,
        Outcome VARCHAR(50) NOT NULL,
        ExistingStatus VARCHAR(40) NULL,
        CONSTRAINT FK_ctl_MddMacTargetClaimEvent_Run
            FOREIGN KEY (RunId) REFERENCES ctl.MddMacCollectorRun (RunId)
    );
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'ctl.MddMacTargetClaimEvent')
      AND name = N'IX_ctl_MddMacTargetClaimEvent_Target_Bucket_EventAtUtc'
)
    CREATE INDEX IX_ctl_MddMacTargetClaimEvent_Target_Bucket_EventAtUtc
        ON ctl.MddMacTargetClaimEvent
            (PanelVersion, TargetIdentity, LogicalDueBucket, EventAtUtc DESC);
GO

/* Tiered50 claims carry the immutable numeric production identity in addition
   to the historical text identity.  The nullable additive columns preserve
   all existing Legacy7 claim rows. */

IF COL_LENGTH(N'ctl.MddMacTargetClaim', N'SamplingTargetId') IS NULL
    ALTER TABLE ctl.MddMacTargetClaim ADD SamplingTargetId INT NULL;
GO

IF COL_LENGTH(N'ctl.MddMacTargetClaimEvent', N'SamplingTargetId') IS NULL
    ALTER TABLE ctl.MddMacTargetClaimEvent ADD SamplingTargetId INT NULL;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'ctl.MddMacTargetClaim')
      AND name = N'UX_ctl_MddMacTargetClaim_SamplingTarget_Bucket'
)
    CREATE UNIQUE INDEX UX_ctl_MddMacTargetClaim_SamplingTarget_Bucket
        ON ctl.MddMacTargetClaim (PanelVersion, SamplingTargetId, LogicalDueBucket)
        WHERE SamplingTargetId IS NOT NULL;
GO

IF OBJECT_ID(N'ctl.MddMacTargetAudit', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddMacTargetAudit
    (
        AuditId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_ctl_MddMacTargetAudit PRIMARY KEY CLUSTERED,
        RunId NVARCHAR(100) NOT NULL,
        PanelVersion NVARCHAR(100) NOT NULL,
        TargetIdentity NVARCHAR(200) NOT NULL,
        StationName NVARCHAR(400) NOT NULL,
        Tier VARCHAR(10) NOT NULL,
        PhaseIndex INT NOT NULL,
        NumberOfResults INT NOT NULL,
        LogicalDueBucket BIGINT NOT NULL,
        SamplingEra VARCHAR(40) NOT NULL,
        SamplingTargetId INT NULL,
        SamplingSlot INT NULL,
        SelectionPolicyVersion VARCHAR(80) NULL,
        ContinuityClassification VARCHAR(80) NOT NULL,
        CollectionContext VARCHAR(80) NOT NULL,
        StartedAtUtc DATETIME2(7) NOT NULL,
        CompletedAtUtc DATETIME2(7) NULL,
        Status VARCHAR(40) NOT NULL
            CONSTRAINT DF_ctl_MddMacTargetAudit_Status DEFAULT ('RUNNING'),
        HttpAttempts INT NOT NULL
            CONSTRAINT DF_ctl_MddMacTargetAudit_HttpAttempts DEFAULT (0),
        HttpStatus INT NULL,
        StopEventResultCount BIGINT NULL,
        ParserOutcome VARCHAR(80) NULL,
        PersistenceOutcome VARCHAR(80) NULL,
        ErrorCategory VARCHAR(80) NULL,
        OutcomeJson NVARCHAR(MAX) NULL,
        CONSTRAINT UQ_ctl_MddMacTargetAudit_Run_Target_Bucket
            UNIQUE (RunId, TargetIdentity, LogicalDueBucket),
        CONSTRAINT FK_ctl_MddMacTargetAudit_Run
            FOREIGN KEY (RunId) REFERENCES ctl.MddMacCollectorRun (RunId)
    );
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'ctl.MddMacTargetAudit')
      AND name = N'IX_ctl_MddMacTargetAudit_Status_StartedAtUtc'
)
    CREATE INDEX IX_ctl_MddMacTargetAudit_Status_StartedAtUtc
        ON ctl.MddMacTargetAudit (Status, StartedAtUtc DESC);
GO

IF OBJECT_ID(N'ctl.MddMacHttpAttempt', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddMacHttpAttempt
    (
        HttpAttemptId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_ctl_MddMacHttpAttempt PRIMARY KEY CLUSTERED,
        AuditId BIGINT NOT NULL,
        AttemptNumber INT NOT NULL,
        StartedAtUtc DATETIME2(7) NOT NULL,
        CompletedAtUtc DATETIME2(7) NOT NULL,
        HttpStatus INT NULL,
        TransportFailureCategory VARCHAR(80) NULL,
        Outcome VARCHAR(40) NOT NULL,
        RetryDecision VARCHAR(60) NULL,
        RetryAfterSeconds DECIMAL(18,6) NULL,
        DurationMs DECIMAL(18,6) NOT NULL,
        TransportKind VARCHAR(80) NOT NULL,
        NetworkAttempted BIT NOT NULL,
        HttpResponseReceived BIT NOT NULL,
        LiveClientInvocation BIT NOT NULL,
        ConservativeQuotaAccounted BIT NOT NULL,
        ProviderQuotaStatus VARCHAR(30) NOT NULL,
        ProviderAttemptConfirmed BIT NOT NULL,
        QuotaPermitId UNIQUEIDENTIFIER NULL,
        CONSTRAINT UQ_ctl_MddMacHttpAttempt_Audit_Attempt UNIQUE (AuditId, AttemptNumber),
        CONSTRAINT FK_ctl_MddMacHttpAttempt_Audit
            FOREIGN KEY (AuditId) REFERENCES ctl.MddMacTargetAudit (AuditId)
    );
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'ctl.MddMacHttpAttempt')
      AND name = N'IX_ctl_MddMacHttpAttempt_Audit_CompletedAtUtc'
)
    CREATE INDEX IX_ctl_MddMacHttpAttempt_Audit_CompletedAtUtc
        ON ctl.MddMacHttpAttempt (AuditId, CompletedAtUtc);
GO

IF OBJECT_ID(N'ctl.MddMacQuotaPermit', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddMacQuotaPermit
    (
        PermitId UNIQUEIDENTIFIER NOT NULL
            CONSTRAINT PK_ctl_MddMacQuotaPermit PRIMARY KEY CLUSTERED,
        MonthKey CHAR(7) NOT NULL,
        DecisionKind VARCHAR(20) NOT NULL,
        ReservationScope VARCHAR(40) NOT NULL,
        Amount INT NOT NULL,
        Boundary INT NOT NULL,
        ReservedAtUtc DATETIME2(7) NOT NULL,
        AuthorizationKey NVARCHAR(240) NULL,
        Status VARCHAR(40) NOT NULL,
        FinalizedAtUtc DATETIME2(7) NULL,
        LiveClientInvocation BIT NOT NULL
            CONSTRAINT DF_ctl_MddMacQuotaPermit_LiveClientInvocation DEFAULT (0),
        CONSTRAINT CK_ctl_MddMacQuotaPermit_Amount CHECK (Amount > 0)
    );
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'ctl.MddMacQuotaPermit')
      AND name = N'IX_ctl_MddMacQuotaPermit_Month_Status_Reserved'
)
    CREATE INDEX IX_ctl_MddMacQuotaPermit_Month_Status_Reserved
        ON ctl.MddMacQuotaPermit (MonthKey, Status, ReservedAtUtc);
GO

IF OBJECT_ID(N'ctl.MddMacSharedQuotaAccounting', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddMacSharedQuotaAccounting
    (
        MonthKey CHAR(7) NOT NULL
            CONSTRAINT PK_ctl_MddMacSharedQuotaAccounting PRIMARY KEY CLUSTERED,
        ProviderQuotaCeiling INT NOT NULL,
        NormalOperationalMaximum INT NOT NULL,
        RetryProtectionBoundary INT NOT NULL,
        ConfirmedProviderAttempts BIGINT NOT NULL,
        ConservativeUnknownAttempts BIGINT NOT NULL,
        ExplicitSharedConsumerReservedAttempts BIGINT NOT NULL,
        AccountingStatus VARCHAR(30) NOT NULL,
        IncludesAllSharedConsumers BIT NOT NULL,
        BaselineAsOfUtc DATETIME2(7) NOT NULL,
        LocalUsageIncludedThroughUtc DATETIME2(7) NOT NULL,
        SourceDescription NVARCHAR(1000) NOT NULL,
        RecordedAtUtc DATETIME2(7) NOT NULL
            CONSTRAINT DF_ctl_MddMacSharedQuotaAccounting_RecordedAtUtc DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT CK_ctl_MddMacSharedQuotaAccounting_Boundaries
            CHECK (
                ProviderQuotaCeiling = 250000
                AND NormalOperationalMaximum = 210000
                AND RetryProtectionBoundary = 207500
            ),
        CONSTRAINT CK_ctl_MddMacSharedQuotaAccounting_Counts
            CHECK (
                ConfirmedProviderAttempts >= 0
                AND ConservativeUnknownAttempts >= 0
                AND ExplicitSharedConsumerReservedAttempts >= 0
            ),
        CONSTRAINT CK_ctl_MddMacSharedQuotaAccounting_Status
            CHECK (AccountingStatus IN ('COMPLETE', 'INCOMPLETE', 'SUPERSEDED'))
    );
END;
GO

IF COL_LENGTH(N'ctl.MddMacQuotaPermit', N'AuthorizationKey') IS NULL
    ALTER TABLE ctl.MddMacQuotaPermit ADD AuthorizationKey NVARCHAR(240) NULL;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'ctl.MddMacQuotaPermit')
      AND name = N'UX_ctl_MddMacQuotaPermit_AuthorizationKey'
)
    CREATE UNIQUE INDEX UX_ctl_MddMacQuotaPermit_AuthorizationKey
        ON ctl.MddMacQuotaPermit (AuthorizationKey)
        WHERE AuthorizationKey IS NOT NULL;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.foreign_keys
    WHERE parent_object_id = OBJECT_ID(N'ctl.MddMacHttpAttempt')
      AND name = N'FK_ctl_MddMacHttpAttempt_QuotaPermit'
)
    ALTER TABLE ctl.MddMacHttpAttempt
        ADD CONSTRAINT FK_ctl_MddMacHttpAttempt_QuotaPermit
            FOREIGN KEY (QuotaPermitId) REFERENCES ctl.MddMacQuotaPermit (PermitId);
GO

IF OBJECT_ID(N'ctl.MddMacQuotaAudit', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddMacQuotaAudit
    (
        QuotaAuditId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_ctl_MddMacQuotaAudit PRIMARY KEY CLUSTERED,
        RunId NVARCHAR(100) NOT NULL,
        TargetIdentity NVARCHAR(200) NULL,
        LogicalDueBucket BIGINT NULL,
        DecisionKind VARCHAR(20) NOT NULL,
        DecisionJson NVARCHAR(MAX) NOT NULL,
        RecordedAtUtc DATETIME2(7) NOT NULL,
        CollectionContext VARCHAR(80) NOT NULL,
        CONSTRAINT FK_ctl_MddMacQuotaAudit_Run
            FOREIGN KEY (RunId) REFERENCES ctl.MddMacCollectorRun (RunId)
    );
END;
GO

IF OBJECT_ID(N'ctl.MddMacSamplingEra', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddMacSamplingEra
    (
        SamplingEraId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_ctl_MddMacSamplingEra PRIMARY KEY CLUSTERED,
        EraCode VARCHAR(40) NOT NULL,
        PanelVersion NVARCHAR(100) NOT NULL,
        EffectiveAtUtc DATETIME2(7) NULL,
        TimestampKind VARCHAR(40) NOT NULL,
        IsActive BIT NOT NULL,
        IsSynthetic BIT NOT NULL,
        RecordedAtUtc DATETIME2(7) NOT NULL
            CONSTRAINT DF_ctl_MddMacSamplingEra_RecordedAtUtc DEFAULT (SYSUTCDATETIME())
    );
END;
GO

/*
    IsActive is retained as historical evidence.  This singleton is the
    append-only-safe current-production pointer used at cutover, so the
    historical era rows do not need to be rewritten to identify the current
    era.
*/

IF OBJECT_ID(N'ctl.MddMacCurrentProductionEra', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddMacCurrentProductionEra
    (
        CurrentMarkerId TINYINT NOT NULL
            CONSTRAINT PK_ctl_MddMacCurrentProductionEra PRIMARY KEY CLUSTERED,
        SamplingEraId BIGINT NOT NULL,
        EraCode VARCHAR(40) NOT NULL,
        PanelVersion NVARCHAR(100) NOT NULL,
        EffectiveAtUtc DATETIME2(7) NOT NULL,
        RecordedAtUtc DATETIME2(7) NOT NULL
            CONSTRAINT DF_ctl_MddMacCurrentProductionEra_RecordedAtUtc DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT CK_ctl_MddMacCurrentProductionEra_Marker CHECK (CurrentMarkerId = 1),
        CONSTRAINT FK_ctl_MddMacCurrentProductionEra_Era
            FOREIGN KEY (SamplingEraId) REFERENCES ctl.MddMacSamplingEra (SamplingEraId)
    );
END;
GO

IF OBJECT_ID(N'ctl.MddMacProductionCutover', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddMacProductionCutover
    (
        CutoverId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_ctl_MddMacProductionCutover PRIMARY KEY CLUSTERED,
        CutoverKey VARCHAR(240) NOT NULL
            CONSTRAINT UQ_ctl_MddMacProductionCutover_Key UNIQUE,
        PanelVersion NVARCHAR(100) NOT NULL,
        FinalLegacy7RunId NVARCHAR(100) NULL,
        FinalLegacy7LogicalDueBucket BIGINT NULL,
        FinalLegacy7PersistenceAtUtc DATETIME2(7) NULL,
        FinalLegacy7AuditStatus VARCHAR(40) NULL,
        Tiered50ActivationAtUtc DATETIME2(7) NULL,
        Tiered50ActivationStatus VARCHAR(40) NOT NULL,
        RecordedAtUtc DATETIME2(7) NOT NULL
            CONSTRAINT DF_ctl_MddMacProductionCutover_RecordedAtUtc DEFAULT (SYSUTCDATETIME()),
        EvidenceJson NVARCHAR(MAX) NULL,
        CONSTRAINT CK_ctl_MddMacProductionCutover_Status
            CHECK (Tiered50ActivationStatus IN ('BLOCKED', 'ACTIVATED', 'OUTAGE'))
    );
END;
GO

IF OBJECT_ID(N'ctl.MddMacMethodologyEvidence', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddMacMethodologyEvidence
    (
        MethodologyEvidenceId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_ctl_MddMacMethodologyEvidence PRIMARY KEY CLUSTERED,
        EraCode VARCHAR(80) NOT NULL,
        PanelVersion NVARCHAR(100) NOT NULL,
        CollectionContext VARCHAR(80) NOT NULL,
        WindowStartUtc DATETIME2(7) NULL,
        WindowEndUtc DATETIME2(7) NULL,
        MethodologyVersion VARCHAR(100) NOT NULL,
        ActualInitialAttempts INT NULL,
        HistoricalEquivalentInitialAttempts INT NULL,
        ExcessInitialAttempts INT NULL,
        RunIdsJson NVARCHAR(MAX) NOT NULL,
        EvidenceJson NVARCHAR(MAX) NOT NULL,
        RecordedAtUtc DATETIME2(7) NOT NULL
            CONSTRAINT DF_ctl_MddMacMethodologyEvidence_RecordedAtUtc DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT CK_ctl_MddMacMethodologyEvidence_Attempts
            CHECK (
                (ActualInitialAttempts IS NULL OR ActualInitialAttempts >= 0)
                AND (HistoricalEquivalentInitialAttempts IS NULL OR HistoricalEquivalentInitialAttempts >= 0)
                AND (ExcessInitialAttempts IS NULL OR ExcessInitialAttempts >= 0)
            )
    );
END;
GO

IF OBJECT_ID(N'stg.MddMacSourceSnapshot', N'U') IS NULL
BEGIN
    CREATE TABLE stg.MddMacSourceSnapshot
    (
        SnapshotId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_stg_MddMacSourceSnapshot PRIMARY KEY CLUSTERED,
        RunId NVARCHAR(100) NOT NULL,
        AuditId BIGINT NOT NULL,
        TargetIdentity NVARCHAR(200) NOT NULL,
        SamplingTargetId INT NULL,
        LogicalDueBucket BIGINT NOT NULL,
        ObservedAtUtc DATETIME2(7) NOT NULL,
        ResponseTimestampSourceText NVARCHAR(256) NULL,
        ParserVersion VARCHAR(80) NOT NULL,
        ApplicationStatus VARCHAR(80) NULL,
        PayloadHashSha256 CHAR(64) NOT NULL,
        SnapshotJson NVARCHAR(MAX) NOT NULL,
        CreatedAtUtc DATETIME2(7) NOT NULL
            CONSTRAINT DF_stg_MddMacSourceSnapshot_CreatedAtUtc DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT UQ_stg_MddMacSourceSnapshot_Audit UNIQUE (AuditId),
        CONSTRAINT FK_stg_MddMacSourceSnapshot_Run
            FOREIGN KEY (RunId) REFERENCES ctl.MddMacCollectorRun (RunId),
        CONSTRAINT FK_stg_MddMacSourceSnapshot_Audit
            FOREIGN KEY (AuditId) REFERENCES ctl.MddMacTargetAudit (AuditId)
    );
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'stg.MddMacSourceSnapshot')
      AND name = N'IX_stg_MddMacSourceSnapshot_Target_ObservedAtUtc'
)
    CREATE INDEX IX_stg_MddMacSourceSnapshot_Target_ObservedAtUtc
        ON stg.MddMacSourceSnapshot (TargetIdentity, ObservedAtUtc DESC, SnapshotId DESC);
GO

IF OBJECT_ID(N'stg.MddMacCallObservation', N'U') IS NULL
BEGIN
    CREATE TABLE stg.MddMacCallObservation
    (
        CallObservationId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_stg_MddMacCallObservation PRIMARY KEY CLUSTERED,
        SnapshotId BIGINT NOT NULL,
        ResultId NVARCHAR(400) NULL,
        ResultOrdinal INT NULL,
        EventOrdinal INT NULL,
        CallScope VARCHAR(20) NOT NULL,
        CallOrdinal INT NOT NULL,
        SourcePath NVARCHAR(2048) NULL,
        CallAtStopSourcePath NVARCHAR(2048) NULL,
        StopPointRef NVARCHAR(400) NULL,
        StopDisplayText NVARCHAR(1000) NULL,
        StopSeqNumber INT NULL,
        PlannedBay NVARCHAR(400) NULL,
        EstimatedBay NVARCHAR(400) NULL,
        UnplannedStop BIT NULL,
        NotServicedStop BIT NULL,
        NoBoardingAtStop BIT NULL,
        NoAlightingAtStop BIT NULL,
        Cancelled BIT NULL,
        Deviation BIT NULL,
        OperatingDayRef NVARCHAR(200) NULL,
        JourneyRef NVARCHAR(500) NULL,
        VehicleJourneyRef NVARCHAR(500) NULL,
        DatedVehicleJourneyRef NVARCHAR(500) NULL,
        LineRef NVARCHAR(400) NULL,
        DirectionRef NVARCHAR(200) NULL,
        OperatorRef NVARCHAR(300) NULL,
        PtMode NVARCHAR(200) NULL,
        Submode NVARCHAR(300) NULL,
        OriginStopPointRef NVARCHAR(400) NULL,
        OriginText NVARCHAR(1000) NULL,
        DestinationStopPointRef NVARCHAR(400) NULL,
        DestinationText NVARCHAR(1000) NULL,
        SourcePathsJson NVARCHAR(MAX) NULL,
        SourceEventJson NVARCHAR(MAX) NULL,
        CONSTRAINT UQ_stg_MddMacCallObservation_Occurrence
            UNIQUE (SnapshotId, EventOrdinal, CallScope, CallOrdinal),
        CONSTRAINT FK_stg_MddMacCallObservation_Snapshot
            FOREIGN KEY (SnapshotId) REFERENCES stg.MddMacSourceSnapshot (SnapshotId)
    );
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'stg.MddMacCallObservation')
      AND name = N'IX_stg_MddMacCallObservation_SourceEvent'
)
    CREATE INDEX IX_stg_MddMacCallObservation_SourceEvent
        ON stg.MddMacCallObservation (SnapshotId, ResultId, CallScope, CallOrdinal);
GO

IF OBJECT_ID(N'stg.MddMacTimingObservation', N'U') IS NULL
BEGIN
    CREATE TABLE stg.MddMacTimingObservation
    (
        TimingObservationId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_stg_MddMacTimingObservation PRIMARY KEY CLUSTERED,
        SnapshotId BIGINT NOT NULL,
        ResultId NVARCHAR(400) NULL,
        ResultOrdinal INT NULL,
        EventOrdinal INT NULL,
        CallScope VARCHAR(20) NOT NULL,
        CallOrdinal INT NOT NULL,
        TimingContext VARCHAR(20) NOT NULL,
        OperatingDayRef NVARCHAR(200) NULL,
        JourneyRef NVARCHAR(500) NULL,
        VehicleJourneyRef NVARCHAR(500) NULL,
        DatedVehicleJourneyRef NVARCHAR(500) NULL,
        LineRef NVARCHAR(400) NULL,
        DirectionRef NVARCHAR(200) NULL,
        OperatorRef NVARCHAR(300) NULL,
        StopPointRef NVARCHAR(400) NULL,
        StopSeqNumber INT NULL,
        TimetabledTimeSourceText NVARCHAR(256) NULL,
        EstimatedTimeSourceText NVARCHAR(256) NULL,
        EstimatedTimeLowSourceText NVARCHAR(256) NULL,
        EstimatedTimeHighSourceText NVARCHAR(256) NULL,
        RecordedAtTimeSourceText NVARCHAR(256) NULL,
        TimingSourcePath NVARCHAR(2048) NULL,
        TimetabledTimeSourcePath NVARCHAR(2048) NULL,
        EstimatedTimeSourcePath NVARCHAR(2048) NULL,
        EstimatedTimeLowSourcePath NVARCHAR(2048) NULL,
        EstimatedTimeHighSourcePath NVARCHAR(2048) NULL,
        RecordedAtTimeSourcePath NVARCHAR(2048) NULL,
        StopPointRefSourcePath NVARCHAR(2048) NULL,
        StopSeqNumberSourcePath NVARCHAR(2048) NULL,
        SourceEventIdentity NVARCHAR(200) NULL,
        SourceEventIdentityVersion VARCHAR(40) NULL,
        SourceEventIdentityStatus VARCHAR(60) NULL,
        SourceEventIdentityBasisJson NVARCHAR(MAX) NULL,
        SourceEventIdentityMissingFieldsJson NVARCHAR(MAX) NULL,
        SourceEventIdentityAmbiguityJson NVARCHAR(MAX) NULL,
        SourcePathsJson NVARCHAR(MAX) NULL,
        CONSTRAINT UQ_stg_MddMacTimingObservation_Occurrence
            UNIQUE (SnapshotId, EventOrdinal, CallScope, CallOrdinal, TimingContext),
        CONSTRAINT FK_stg_MddMacTimingObservation_Snapshot
            FOREIGN KEY (SnapshotId) REFERENCES stg.MddMacSourceSnapshot (SnapshotId)
    );
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'stg.MddMacTimingObservation')
      AND name = N'IX_stg_MddMacTimingObservation_Identity_Observed'
)
    CREATE INDEX IX_stg_MddMacTimingObservation_Identity_Observed
        ON stg.MddMacTimingObservation (SourceEventIdentity, TimingContext, SnapshotId);
GO

IF OBJECT_ID(N'stg.MddMacSituationEvidence', N'U') IS NULL
BEGIN
    CREATE TABLE stg.MddMacSituationEvidence
    (
        SituationEvidenceId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_stg_MddMacSituationEvidence PRIMARY KEY CLUSTERED,
        SnapshotId BIGINT NOT NULL,
        ResultId NVARCHAR(400) NULL,
        ResultOrdinal INT NULL,
        EventOrdinal INT NULL,
        EvidenceType VARCHAR(40) NOT NULL,
        SituationType VARCHAR(80) NULL,
        ReferenceType VARCHAR(40) NULL,
        ReferenceValue NVARCHAR(2000) NULL,
        ParticipantRef NVARCHAR(400) NULL,
        SituationNumber NVARCHAR(500) NULL,
        VersionSourceText NVARCHAR(200) NULL,
        Identifier NVARCHAR(500) NULL,
        Summary NVARCHAR(4000) NULL,
        Description NVARCHAR(MAX) NULL,
        Detail NVARCHAR(MAX) NULL,
        CreationTimeSourceText NVARCHAR(256) NULL,
        ValidFromSourceText NVARCHAR(256) NULL,
        ValidToSourceText NVARCHAR(256) NULL,
        ProviderSource NVARCHAR(1000) NULL,
        ProviderSourceType NVARCHAR(1000) NULL,
        PrioritySourceText NVARCHAR(200) NULL,
        AffectsJson NVARCHAR(MAX) NULL,
        AffectedEntityJson NVARCHAR(MAX) NULL,
        AffectedJourneyJson NVARCHAR(MAX) NULL,
        AffectedLineJson NVARCHAR(MAX) NULL,
        IdentityKey NVARCHAR(1000) NULL,
        AssociationScope VARCHAR(40) NULL,
        CallScope VARCHAR(20) NULL,
        SourcePath NVARCHAR(2048) NULL,
        FieldPathsJson NVARCHAR(MAX) NULL,
        AffectedEntityFieldPathsJson NVARCHAR(MAX) NULL,
        SourceRepresentationJson NVARCHAR(MAX) NULL,
        CONSTRAINT FK_stg_MddMacSituationEvidence_Snapshot
            FOREIGN KEY (SnapshotId) REFERENCES stg.MddMacSourceSnapshot (SnapshotId)
    );
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'stg.MddMacSituationEvidence')
      AND name = N'IX_stg_MddMacSituationEvidence_Identity_Observed'
)
    CREATE INDEX IX_stg_MddMacSituationEvidence_Identity_Observed
        ON stg.MddMacSituationEvidence (ParticipantRef, SituationNumber, SnapshotId);
GO

IF OBJECT_ID(N'stg.MddMacServiceSectionEvidence', N'U') IS NULL
BEGIN
    CREATE TABLE stg.MddMacServiceSectionEvidence
    (
        ServiceSectionEvidenceId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_stg_MddMacServiceSectionEvidence PRIMARY KEY CLUSTERED,
        SnapshotId BIGINT NOT NULL,
        ResultId NVARCHAR(400) NULL,
        ResultOrdinal INT NULL,
        EventOrdinal INT NULL,
        SectionOrdinal INT NOT NULL,
        SourcePath NVARCHAR(2048) NOT NULL,
        LineRef NVARCHAR(400) NULL,
        DirectionRef NVARCHAR(200) NULL,
        OperatorRef NVARCHAR(300) NULL,
        PtMode NVARCHAR(200) NULL,
        RailSubmode NVARCHAR(300) NULL,
        BusSubmode NVARCHAR(300) NULL,
        TramSubmode NVARCHAR(300) NULL,
        Name NVARCHAR(1000) NULL,
        ShortName NVARCHAR(1000) NULL,
        Description NVARCHAR(MAX) NULL,
        RouteDescription NVARCHAR(MAX) NULL,
        Via NVARCHAR(MAX) NULL,
        PublishedLineName NVARCHAR(1000) NULL,
        ModeSourceJson NVARCHAR(MAX) NULL,
        SourcePathsJson NVARCHAR(MAX) NULL,
        SourceRepresentationJson NVARCHAR(MAX) NULL,
        SemanticRole VARCHAR(80) NOT NULL,
        CONSTRAINT UQ_stg_MddMacServiceSectionEvidence_Occurrence
            UNIQUE (SnapshotId, EventOrdinal, SectionOrdinal, SourcePath),
        CONSTRAINT FK_stg_MddMacServiceSectionEvidence_Snapshot
            FOREIGN KEY (SnapshotId) REFERENCES stg.MddMacSourceSnapshot (SnapshotId)
    );
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'stg.MddMacServiceSectionEvidence')
      AND name = N'IX_stg_MddMacServiceSectionEvidence_Source_Order'
)
    CREATE INDEX IX_stg_MddMacServiceSectionEvidence_Source_Order
        ON stg.MddMacServiceSectionEvidence (SnapshotId, EventOrdinal, SectionOrdinal);
GO

IF OBJECT_ID(N'stg.MddMacAttributeEvidence', N'U') IS NULL
BEGIN
    CREATE TABLE stg.MddMacAttributeEvidence
    (
        AttributeEvidenceId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_stg_MddMacAttributeEvidence PRIMARY KEY CLUSTERED,
        SnapshotId BIGINT NOT NULL,
        ResultId NVARCHAR(400) NULL,
        ResultOrdinal INT NULL,
        EventOrdinal INT NULL,
        AttributeOrdinal INT NOT NULL,
        AttributeType VARCHAR(80) NOT NULL,
        SourcePath NVARCHAR(2048) NOT NULL,
        AssociationContextPath NVARCHAR(2048) NULL,
        AssociationScope VARCHAR(40) NULL,
        CallScope VARCHAR(20) NULL,
        CodeSourceText NVARCHAR(2000) NULL,
        TextSourceText NVARCHAR(MAX) NULL,
        IncidentCallSourcePath NVARCHAR(2048) NULL,
        InfoCallSourcePath NVARCHAR(2048) NULL,
        CodeSourcePathsJson NVARCHAR(MAX) NULL,
        TextSourcePathsJson NVARCHAR(MAX) NULL,
        SourceFieldPathsJson NVARCHAR(MAX) NULL,
        SourceRepresentationJson NVARCHAR(MAX) NULL,
        SemanticRole VARCHAR(100) NOT NULL,
        CONSTRAINT UQ_stg_MddMacAttributeEvidence_Occurrence
            UNIQUE (SnapshotId, EventOrdinal, AttributeOrdinal, SourcePath),
        CONSTRAINT FK_stg_MddMacAttributeEvidence_Snapshot
            FOREIGN KEY (SnapshotId) REFERENCES stg.MddMacSourceSnapshot (SnapshotId)
    );
END;
GO

IF OBJECT_ID(N'stg.MddMacResponseError', N'U') IS NULL
BEGIN
    CREATE TABLE stg.MddMacResponseError
    (
        ResponseErrorId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_stg_MddMacResponseError PRIMARY KEY CLUSTERED,
        SnapshotId BIGINT NOT NULL,
        ResultId NVARCHAR(400) NULL,
        ResultOrdinal INT NULL,
        ErrorFieldName VARCHAR(100) NOT NULL,
        ErrorValue NVARCHAR(MAX) NOT NULL,
        ErrorScope VARCHAR(40) NOT NULL,
        SourcePath NVARCHAR(2048) NOT NULL,
        ResultSourcePath NVARCHAR(2048) NULL,
        CONSTRAINT UQ_stg_MddMacResponseError_Occurrence
            UNIQUE (SnapshotId, SourcePath, ErrorFieldName),
        CONSTRAINT FK_stg_MddMacResponseError_Snapshot
            FOREIGN KEY (SnapshotId) REFERENCES stg.MddMacSourceSnapshot (SnapshotId)
    );
END;
GO

IF OBJECT_ID(N'stg.MddMacParserGap', N'U') IS NULL
BEGIN
    CREATE TABLE stg.MddMacParserGap
    (
        ParserGapId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_stg_MddMacParserGap PRIMARY KEY CLUSTERED,
        SnapshotId BIGINT NOT NULL,
        ResultId NVARCHAR(400) NULL,
        ResultOrdinal INT NULL,
        EventOrdinal INT NULL,
        FieldName NVARCHAR(400) NOT NULL,
        NormalizedFieldName NVARCHAR(400) NOT NULL,
        SourcePath NVARCHAR(2048) NOT NULL,
        ValueType VARCHAR(80) NOT NULL,
        SourceValue NVARCHAR(2000) NULL,
        SourceRepresentationJson NVARCHAR(MAX) NULL,
        ValueExcerpt NVARCHAR(2000) NULL,
        MappingStatus VARCHAR(100) NOT NULL,
        SemanticHint VARCHAR(200) NULL,
        EventContext VARCHAR(80) NULL,
        AssociationScope VARCHAR(40) NULL,
        CallScope VARCHAR(20) NULL,
        CONSTRAINT UQ_stg_MddMacParserGap_Occurrence
            UNIQUE (SnapshotId, SourcePath, NormalizedFieldName),
        CONSTRAINT FK_stg_MddMacParserGap_Snapshot
            FOREIGN KEY (SnapshotId) REFERENCES stg.MddMacSourceSnapshot (SnapshotId)
    );
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'stg.MddMacParserGap')
      AND name = N'IX_stg_MddMacParserGap_Field_Observed'
)
    CREATE INDEX IX_stg_MddMacParserGap_Field_Observed
        ON stg.MddMacParserGap (NormalizedFieldName, SnapshotId);
GO

CREATE OR ALTER PROCEDURE stg.uspPersistMddMacSnapshot
    @RunId NVARCHAR(100) = NULL,
    @AuditId BIGINT = NULL,
    @TargetIdentity NVARCHAR(200) = NULL,
    @SamplingTargetId INT = NULL,
    @LogicalDueBucket BIGINT = NULL,
    @ObservedAtUtc DATETIME2(7) = NULL,
    @ResponseTimestampSourceText NVARCHAR(256) = NULL,
    @ParserVersion VARCHAR(80) = NULL,
    @ApplicationStatus VARCHAR(80) = NULL,
    @PayloadHashSha256 CHAR(64) = NULL,
    @SnapshotJson NVARCHAR(MAX) = NULL,
    @CallsJson NVARCHAR(MAX) = NULL,
    @TimingsJson NVARCHAR(MAX) = NULL,
    @SituationEvidenceJson NVARCHAR(MAX) = NULL,
    @ServiceSectionsJson NVARCHAR(MAX) = NULL,
    @AttributesJson NVARCHAR(MAX) = NULL,
    @ResponseErrorsJson NVARCHAR(MAX) = NULL,
    @ParserGapsJson NVARCHAR(MAX) = NULL,
    @CompletedRunStatus VARCHAR(40) = NULL,
    @CompletedAtUtc DATETIME2(7) = NULL,
    @RunSummaryJson NVARCHAR(MAX) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF @CompletedRunStatus IS NOT NULL
    BEGIN
        IF @RunId IS NULL OR @CompletedAtUtc IS NULL OR ISJSON(@RunSummaryJson) <> 1
            THROW 51112, 'MDD Mac run finalization arguments are invalid.', 1;

        BEGIN TRY
            BEGIN TRANSACTION;

            UPDATE ctl.MddMacCollectorRun
            SET CompletedAtUtc = @CompletedAtUtc,
                Status = @CompletedRunStatus,
                SummaryJson = @RunSummaryJson
            WHERE RunId = @RunId;

            UPDATE ctl.MddMacDispatcherClaim
            SET Status = @CompletedRunStatus,
                CompletedAtUtc = @CompletedAtUtc
            WHERE RunId = @RunId;

            COMMIT TRANSACTION;
            SELECT CAST(NULL AS BIGINT) AS SnapshotId;
            RETURN;
        END TRY
        BEGIN CATCH
            IF XACT_STATE() <> 0
                ROLLBACK TRANSACTION;
            THROW;
        END CATCH;
    END;

    IF ISJSON(@SnapshotJson) <> 1
        THROW 51110, 'MDD Mac snapshot JSON is invalid.', 1;
    IF ISJSON(@CallsJson) <> 1 OR ISJSON(@TimingsJson) <> 1
       OR ISJSON(@SituationEvidenceJson) <> 1 OR ISJSON(@ServiceSectionsJson) <> 1
       OR ISJSON(@AttributesJson) <> 1 OR ISJSON(@ResponseErrorsJson) <> 1
       OR ISJSON(@ParserGapsJson) <> 1
        THROW 51111, 'MDD Mac normalized evidence JSON is invalid.', 1;

    BEGIN TRY
        BEGIN TRANSACTION;

        DECLARE @SnapshotId BIGINT;
        SELECT @SnapshotId = SnapshotId
        FROM stg.MddMacSourceSnapshot WITH (UPDLOCK, HOLDLOCK)
        WHERE AuditId = @AuditId;

        IF @SnapshotId IS NULL
        BEGIN
            INSERT INTO stg.MddMacSourceSnapshot
            (
                RunId, AuditId, TargetIdentity, SamplingTargetId,
                LogicalDueBucket, ObservedAtUtc, ResponseTimestampSourceText,
                ParserVersion, ApplicationStatus, PayloadHashSha256, SnapshotJson
            )
            VALUES
            (
                @RunId, @AuditId, @TargetIdentity, @SamplingTargetId,
                @LogicalDueBucket, @ObservedAtUtc, @ResponseTimestampSourceText,
                @ParserVersion, @ApplicationStatus, @PayloadHashSha256, @SnapshotJson
            );
            SET @SnapshotId = CONVERT(BIGINT, SCOPE_IDENTITY());

            INSERT INTO stg.MddMacCallObservation
            (
                SnapshotId, ResultId, ResultOrdinal, EventOrdinal, CallScope,
                CallOrdinal, SourcePath, CallAtStopSourcePath, StopPointRef,
                StopDisplayText, StopSeqNumber, PlannedBay, EstimatedBay,
                UnplannedStop, NotServicedStop, NoBoardingAtStop,
                NoAlightingAtStop, Cancelled, Deviation, OperatingDayRef,
                JourneyRef, VehicleJourneyRef, DatedVehicleJourneyRef, LineRef,
                DirectionRef, OperatorRef, PtMode, Submode,
                OriginStopPointRef, OriginText, DestinationStopPointRef,
                DestinationText, SourcePathsJson, SourceEventJson
            )
            SELECT
                @SnapshotId, ResultId, ResultOrdinal, EventOrdinal, CallScope,
                CallOrdinal, SourcePath, CallAtStopSourcePath, StopPointRef,
                StopDisplayText, StopSeqNumber, PlannedBay, EstimatedBay,
                UnplannedStop, NotServicedStop, NoBoardingAtStop,
                NoAlightingAtStop, Cancelled, Deviation, OperatingDayRef,
                JourneyRef, VehicleJourneyRef, DatedVehicleJourneyRef, LineRef,
                DirectionRef, OperatorRef, PtMode, Submode,
                OriginStopPointRef, OriginText, DestinationStopPointRef,
                DestinationText, SourcePathsJson, SourceEventJson
            FROM OPENJSON(@CallsJson)
            WITH
            (
                ResultId NVARCHAR(400) '$.result_id',
                ResultOrdinal INT '$.result_ordinal',
                EventOrdinal INT '$.event_ordinal',
                CallScope VARCHAR(20) '$.call_scope',
                CallOrdinal INT '$.call_ordinal',
                SourcePath NVARCHAR(2048) '$.source_path',
                CallAtStopSourcePath NVARCHAR(2048) '$.call_at_stop_source_path',
                StopPointRef NVARCHAR(400) '$.stop_point_ref',
                StopDisplayText NVARCHAR(1000) '$.stop_display_text',
                StopSeqNumber INT '$.stop_seq_number',
                PlannedBay NVARCHAR(400) '$.planned_bay',
                EstimatedBay NVARCHAR(400) '$.estimated_bay',
                UnplannedStop BIT '$.unplanned_stop',
                NotServicedStop BIT '$.not_serviced_stop',
                NoBoardingAtStop BIT '$.no_boarding_at_stop',
                NoAlightingAtStop BIT '$.no_alighting_at_stop',
                Cancelled BIT '$.cancelled',
                Deviation BIT '$.deviation',
                OperatingDayRef NVARCHAR(200) '$.operating_day_ref',
                JourneyRef NVARCHAR(500) '$.journey_ref',
                VehicleJourneyRef NVARCHAR(500) '$.vehicle_journey_ref',
                DatedVehicleJourneyRef NVARCHAR(500) '$.dated_vehicle_journey_ref',
                LineRef NVARCHAR(400) '$.line_ref',
                DirectionRef NVARCHAR(200) '$.direction_ref',
                OperatorRef NVARCHAR(300) '$.operator_ref',
                PtMode NVARCHAR(200) '$.pt_mode',
                Submode NVARCHAR(300) '$.submode',
                OriginStopPointRef NVARCHAR(400) '$.origin_stop_point_ref',
                OriginText NVARCHAR(1000) '$.origin_text',
                DestinationStopPointRef NVARCHAR(400) '$.destination_stop_point_ref',
                DestinationText NVARCHAR(1000) '$.destination_text',
                SourcePathsJson NVARCHAR(MAX) '$.source_paths_json',
                SourceEventJson NVARCHAR(MAX) '$.source_event_json'
            );

            INSERT INTO stg.MddMacTimingObservation
            (
                SnapshotId, ResultId, ResultOrdinal, EventOrdinal, CallScope,
                CallOrdinal, TimingContext, OperatingDayRef, JourneyRef,
                VehicleJourneyRef, DatedVehicleJourneyRef, LineRef, DirectionRef,
                OperatorRef, StopPointRef, StopSeqNumber,
                TimetabledTimeSourceText, EstimatedTimeSourceText,
                EstimatedTimeLowSourceText, EstimatedTimeHighSourceText,
                RecordedAtTimeSourceText, TimingSourcePath,
                TimetabledTimeSourcePath, EstimatedTimeSourcePath,
                EstimatedTimeLowSourcePath, EstimatedTimeHighSourcePath,
                RecordedAtTimeSourcePath, StopPointRefSourcePath,
                StopSeqNumberSourcePath, SourceEventIdentity,
                SourceEventIdentityVersion, SourceEventIdentityStatus,
                SourceEventIdentityBasisJson, SourceEventIdentityMissingFieldsJson,
                SourceEventIdentityAmbiguityJson, SourcePathsJson
            )
            SELECT
                @SnapshotId, ResultId, ResultOrdinal, EventOrdinal, CallScope,
                CallOrdinal, TimingContext, OperatingDayRef, JourneyRef,
                VehicleJourneyRef, DatedVehicleJourneyRef, LineRef, DirectionRef,
                OperatorRef, StopPointRef, StopSeqNumber,
                TimetabledTimeSourceText, EstimatedTimeSourceText,
                EstimatedTimeLowSourceText, EstimatedTimeHighSourceText,
                RecordedAtTimeSourceText, TimingSourcePath,
                TimetabledTimeSourcePath, EstimatedTimeSourcePath,
                EstimatedTimeLowSourcePath, EstimatedTimeHighSourcePath,
                RecordedAtTimeSourcePath, StopPointRefSourcePath,
                StopSeqNumberSourcePath, SourceEventIdentity,
                SourceEventIdentityVersion, SourceEventIdentityStatus,
                SourceEventIdentityBasisJson, SourceEventIdentityMissingFieldsJson,
                SourceEventIdentityAmbiguityJson, SourcePathsJson
            FROM OPENJSON(@TimingsJson)
            WITH
            (
                ResultId NVARCHAR(400) '$.result_id',
                ResultOrdinal INT '$.result_ordinal',
                EventOrdinal INT '$.event_ordinal',
                CallScope VARCHAR(20) '$.call_scope',
                CallOrdinal INT '$.call_ordinal',
                TimingContext VARCHAR(20) '$.timing_context',
                OperatingDayRef NVARCHAR(200) '$.operating_day_ref',
                JourneyRef NVARCHAR(500) '$.journey_ref',
                VehicleJourneyRef NVARCHAR(500) '$.vehicle_journey_ref',
                DatedVehicleJourneyRef NVARCHAR(500) '$.dated_vehicle_journey_ref',
                LineRef NVARCHAR(400) '$.line_ref',
                DirectionRef NVARCHAR(200) '$.direction_ref',
                OperatorRef NVARCHAR(300) '$.operator_ref',
                StopPointRef NVARCHAR(400) '$.stop_point_ref',
                StopSeqNumber INT '$.stop_seq_number',
                TimetabledTimeSourceText NVARCHAR(256) '$.timetabled_time',
                EstimatedTimeSourceText NVARCHAR(256) '$.estimated_time',
                EstimatedTimeLowSourceText NVARCHAR(256) '$.estimated_time_low',
                EstimatedTimeHighSourceText NVARCHAR(256) '$.estimated_time_high',
                RecordedAtTimeSourceText NVARCHAR(256) '$.recorded_at_time',
                TimingSourcePath NVARCHAR(2048) '$.source_path',
                TimetabledTimeSourcePath NVARCHAR(2048) '$.timetabled_time_source_path',
                EstimatedTimeSourcePath NVARCHAR(2048) '$.estimated_time_source_path',
                EstimatedTimeLowSourcePath NVARCHAR(2048) '$.estimated_time_low_source_path',
                EstimatedTimeHighSourcePath NVARCHAR(2048) '$.estimated_time_high_source_path',
                RecordedAtTimeSourcePath NVARCHAR(2048) '$.recorded_at_time_source_path',
                StopPointRefSourcePath NVARCHAR(2048) '$.stop_point_ref_source_path',
                StopSeqNumberSourcePath NVARCHAR(2048) '$.stop_seq_number_source_path',
                SourceEventIdentity NVARCHAR(200) '$.source_event_identity',
                SourceEventIdentityVersion VARCHAR(40) '$.source_event_identity_version',
                SourceEventIdentityStatus VARCHAR(60) '$.source_event_identity_status',
                SourceEventIdentityBasisJson NVARCHAR(MAX) '$.source_event_identity_basis_json',
                SourceEventIdentityMissingFieldsJson NVARCHAR(MAX) '$.source_event_identity_missing_fields_json',
                SourceEventIdentityAmbiguityJson NVARCHAR(MAX) '$.source_event_identity_ambiguity_json',
                SourcePathsJson NVARCHAR(MAX) '$.source_paths_json'
            );

            INSERT INTO stg.MddMacSituationEvidence
            (
                SnapshotId, ResultId, ResultOrdinal, EventOrdinal, EvidenceType,
                SituationType, ReferenceType, ReferenceValue, ParticipantRef,
                SituationNumber, VersionSourceText, Identifier, Summary,
                Description, Detail, CreationTimeSourceText, ValidFromSourceText,
                ValidToSourceText, ProviderSource, ProviderSourceType,
                PrioritySourceText, AffectsJson, AffectedEntityJson,
                AffectedJourneyJson, AffectedLineJson, IdentityKey,
                AssociationScope, CallScope, SourcePath, FieldPathsJson,
                AffectedEntityFieldPathsJson, SourceRepresentationJson
            )
            SELECT
                @SnapshotId, ResultId, ResultOrdinal, EventOrdinal, EvidenceType,
                SituationType, ReferenceType, ReferenceValue, ParticipantRef,
                SituationNumber, VersionSourceText, Identifier, Summary,
                Description, Detail, CreationTimeSourceText, ValidFromSourceText,
                ValidToSourceText, ProviderSource, ProviderSourceType,
                PrioritySourceText, AffectsJson, AffectedEntityJson,
                AffectedJourneyJson, AffectedLineJson, IdentityKey,
                AssociationScope, CallScope, SourcePath, FieldPathsJson,
                AffectedEntityFieldPathsJson, SourceRepresentationJson
            FROM OPENJSON(@SituationEvidenceJson)
            WITH
            (
                ResultId NVARCHAR(400) '$.result_id',
                ResultOrdinal INT '$.result_ordinal',
                EventOrdinal INT '$.event_ordinal',
                EvidenceType VARCHAR(40) '$.evidence_type',
                SituationType VARCHAR(80) '$.situation_type',
                ReferenceType VARCHAR(40) '$.reference_type',
                ReferenceValue NVARCHAR(2000) '$.reference_value',
                ParticipantRef NVARCHAR(400) '$.participant_ref',
                SituationNumber NVARCHAR(500) '$.situation_number',
                VersionSourceText NVARCHAR(200) '$.version_source_text',
                Identifier NVARCHAR(500) '$.identifier',
                Summary NVARCHAR(4000) '$.summary',
                Description NVARCHAR(MAX) '$.description',
                Detail NVARCHAR(MAX) '$.detail',
                CreationTimeSourceText NVARCHAR(256) '$.creation_time_source_text',
                ValidFromSourceText NVARCHAR(256) '$.valid_from_source_text',
                ValidToSourceText NVARCHAR(256) '$.valid_to_source_text',
                ProviderSource NVARCHAR(1000) '$.source',
                ProviderSourceType NVARCHAR(1000) '$.source_type',
                PrioritySourceText NVARCHAR(200) '$.priority_source_text',
                AffectsJson NVARCHAR(MAX) '$.affects_json',
                AffectedEntityJson NVARCHAR(MAX) '$.affected_entity_json',
                AffectedJourneyJson NVARCHAR(MAX) '$.affected_journey_json',
                AffectedLineJson NVARCHAR(MAX) '$.affected_line_json',
                IdentityKey NVARCHAR(1000) '$.identity_key',
                AssociationScope VARCHAR(40) '$.association_scope',
                CallScope VARCHAR(20) '$.call_scope',
                SourcePath NVARCHAR(2048) '$.source_path',
                FieldPathsJson NVARCHAR(MAX) '$.field_paths_json',
                AffectedEntityFieldPathsJson NVARCHAR(MAX) '$.affected_entity_field_paths_json',
                SourceRepresentationJson NVARCHAR(MAX) '$.source_representation_json'
            );

            INSERT INTO stg.MddMacServiceSectionEvidence
            (
                SnapshotId, ResultId, ResultOrdinal, EventOrdinal, SectionOrdinal,
                SourcePath, LineRef, DirectionRef, OperatorRef, PtMode,
                RailSubmode, BusSubmode, TramSubmode, Name, ShortName,
                Description, RouteDescription, Via, PublishedLineName,
                ModeSourceJson, SourcePathsJson, SourceRepresentationJson,
                SemanticRole
            )
            SELECT
                @SnapshotId, ResultId, ResultOrdinal, EventOrdinal, SectionOrdinal,
                SourcePath, LineRef, DirectionRef, OperatorRef, PtMode,
                RailSubmode, BusSubmode, TramSubmode, Name, ShortName,
                Description, RouteDescription, Via, PublishedLineName,
                ModeSourceJson, SourcePathsJson, SourceRepresentationJson,
                SemanticRole
            FROM OPENJSON(@ServiceSectionsJson)
            WITH
            (
                ResultId NVARCHAR(400) '$.result_id',
                ResultOrdinal INT '$.result_ordinal',
                EventOrdinal INT '$.event_ordinal',
                SectionOrdinal INT '$.section_ordinal',
                SourcePath NVARCHAR(2048) '$.source_path',
                LineRef NVARCHAR(400) '$.line_ref',
                DirectionRef NVARCHAR(200) '$.direction_ref',
                OperatorRef NVARCHAR(300) '$.operator_ref',
                PtMode NVARCHAR(200) '$.pt_mode',
                RailSubmode NVARCHAR(300) '$.rail_submode',
                BusSubmode NVARCHAR(300) '$.bus_submode',
                TramSubmode NVARCHAR(300) '$.tram_submode',
                Name NVARCHAR(1000) '$.name',
                ShortName NVARCHAR(1000) '$.short_name',
                Description NVARCHAR(MAX) '$.description',
                RouteDescription NVARCHAR(MAX) '$.route_description',
                Via NVARCHAR(MAX) '$.via',
                PublishedLineName NVARCHAR(1000) '$.published_line_name_text',
                ModeSourceJson NVARCHAR(MAX) '$.mode_source_representation',
                SourcePathsJson NVARCHAR(MAX) '$.source_paths_json',
                SourceRepresentationJson NVARCHAR(MAX) '$.source_representation_json',
                SemanticRole VARCHAR(80) '$.semantic_role'
            );

            INSERT INTO stg.MddMacAttributeEvidence
            (
                SnapshotId, ResultId, ResultOrdinal, EventOrdinal,
                AttributeOrdinal, AttributeType, SourcePath,
                AssociationContextPath, AssociationScope, CallScope,
                CodeSourceText, TextSourceText, IncidentCallSourcePath,
                InfoCallSourcePath, CodeSourcePathsJson, TextSourcePathsJson,
                SourceFieldPathsJson, SourceRepresentationJson, SemanticRole
            )
            SELECT
                @SnapshotId, ResultId, ResultOrdinal, EventOrdinal,
                AttributeOrdinal, AttributeType, SourcePath,
                AssociationContextPath, AssociationScope, CallScope,
                CodeSourceText, TextSourceText, IncidentCallSourcePath,
                InfoCallSourcePath, CodeSourcePathsJson, TextSourcePathsJson,
                SourceFieldPathsJson, SourceRepresentationJson, SemanticRole
            FROM OPENJSON(@AttributesJson)
            WITH
            (
                ResultId NVARCHAR(400) '$.result_id',
                ResultOrdinal INT '$.result_ordinal',
                EventOrdinal INT '$.event_ordinal',
                AttributeOrdinal INT '$.attribute_ordinal',
                AttributeType VARCHAR(80) '$.attribute_type',
                SourcePath NVARCHAR(2048) '$.source_path',
                AssociationContextPath NVARCHAR(2048) '$.association_context_path',
                AssociationScope VARCHAR(40) '$.association_scope',
                CallScope VARCHAR(20) '$.call_scope',
                CodeSourceText NVARCHAR(2000) '$.code_source_text',
                TextSourceText NVARCHAR(MAX) '$.text_source_text',
                IncidentCallSourcePath NVARCHAR(2048) '$.incident_call_source_path',
                InfoCallSourcePath NVARCHAR(2048) '$.info_call_source_path',
                CodeSourcePathsJson NVARCHAR(MAX) '$.code_source_paths_json',
                TextSourcePathsJson NVARCHAR(MAX) '$.text_source_paths_json',
                SourceFieldPathsJson NVARCHAR(MAX) '$.source_field_paths_json',
                SourceRepresentationJson NVARCHAR(MAX) '$.source_representation_json',
                SemanticRole VARCHAR(100) '$.semantic_role'
            );

            INSERT INTO stg.MddMacResponseError
            (
                SnapshotId, ResultId, ResultOrdinal, ErrorFieldName, ErrorValue,
                ErrorScope, SourcePath, ResultSourcePath
            )
            SELECT
                @SnapshotId, ResultId, ResultOrdinal, ErrorFieldName, ErrorValue,
                ErrorScope, SourcePath, ResultSourcePath
            FROM OPENJSON(@ResponseErrorsJson)
            WITH
            (
                ResultId NVARCHAR(400) '$.result_id',
                ResultOrdinal INT '$.result_ordinal',
                ErrorFieldName VARCHAR(100) '$.field_name',
                ErrorValue NVARCHAR(MAX) '$.value',
                ErrorScope VARCHAR(40) '$.error_scope',
                SourcePath NVARCHAR(2048) '$.source_path',
                ResultSourcePath NVARCHAR(2048) '$.result_source_path'
            );

            INSERT INTO stg.MddMacParserGap
            (
                SnapshotId, ResultId, ResultOrdinal, EventOrdinal, FieldName,
                NormalizedFieldName, SourcePath, ValueType, SourceValue,
                SourceRepresentationJson, ValueExcerpt, MappingStatus,
                SemanticHint, EventContext, AssociationScope, CallScope
            )
            SELECT
                @SnapshotId, ResultId, ResultOrdinal, EventOrdinal, FieldName,
                NormalizedFieldName, SourcePath, ValueType, SourceValue,
                SourceRepresentationJson, ValueExcerpt, MappingStatus,
                SemanticHint, EventContext, AssociationScope, CallScope
            FROM OPENJSON(@ParserGapsJson)
            WITH
            (
                ResultId NVARCHAR(400) '$.result_id',
                ResultOrdinal INT '$.result_ordinal',
                EventOrdinal INT '$.event_ordinal',
                FieldName NVARCHAR(400) '$.field_name',
                NormalizedFieldName NVARCHAR(400) '$.normalized_field_name',
                SourcePath NVARCHAR(2048) '$.source_path',
                ValueType VARCHAR(80) '$.value_type',
                SourceValue NVARCHAR(2000) '$.source_value',
                SourceRepresentationJson NVARCHAR(MAX) '$.source_representation_json',
                ValueExcerpt NVARCHAR(2000) '$.value_excerpt',
                MappingStatus VARCHAR(100) '$.mapping_status',
                SemanticHint VARCHAR(200) '$.semantic_hint',
                EventContext VARCHAR(80) '$.event_context',
                AssociationScope VARCHAR(40) '$.association_scope',
                CallScope VARCHAR(20) '$.call_scope'
            );

            UPDATE ctl.MddMacTargetAudit
            SET PersistenceOutcome = 'PERSISTED_PARSED_SNAPSHOT',
                StopEventResultCount = JSON_VALUE(@SnapshotJson, '$.result_count')
            WHERE AuditId = @AuditId;
        END;

        COMMIT TRANSACTION;
        SELECT @SnapshotId AS SnapshotId;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0
            ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO

CREATE OR ALTER VIEW analytics.vwMddMacRecordedAtTimeHistory
AS
WITH LatestObservation AS
(
    SELECT
        t.*,
        s.ObservedAtUtc AS ObservedAtUtc,
        ROW_NUMBER() OVER
        (
            PARTITION BY t.SourceEventIdentity, t.TimingContext
            ORDER BY s.ObservedAtUtc DESC, t.TimingObservationId DESC
        ) AS LatestRowNumber
    FROM stg.MddMacTimingObservation AS t
    INNER JOIN stg.MddMacSourceSnapshot AS s
        ON s.SnapshotId = t.SnapshotId
    WHERE t.SourceEventIdentity IS NOT NULL
      AND t.SourceEventIdentityStatus = 'SOURCE_EVENT_IDENTITY_ESTABLISHED'
),
LastObserved AS
(
    SELECT
        t.SourceEventIdentity,
        t.TimingContext,
        t.RecordedAtTimeSourceText,
        t.RecordedAtTimeSourcePath,
        s.ObservedAtUtc,
        t.SnapshotId,
        t.TimingObservationId,
        ROW_NUMBER() OVER
        (
            PARTITION BY t.SourceEventIdentity, t.TimingContext
            ORDER BY s.ObservedAtUtc DESC, t.TimingObservationId DESC
        ) AS RowNumber
    FROM stg.MddMacTimingObservation AS t
    INNER JOIN stg.MddMacSourceSnapshot AS s
        ON s.SnapshotId = t.SnapshotId
    WHERE t.SourceEventIdentity IS NOT NULL
      AND t.SourceEventIdentityStatus = 'SOURCE_EVENT_IDENTITY_ESTABLISHED'
      AND t.RecordedAtTimeSourceText IS NOT NULL
),
FirstObserved AS
(
    SELECT
        t.SourceEventIdentity,
        t.TimingContext,
        t.RecordedAtTimeSourceText,
        t.RecordedAtTimeSourcePath,
        s.ObservedAtUtc,
        t.SnapshotId,
        t.TimingObservationId,
        ROW_NUMBER() OVER
        (
            PARTITION BY t.SourceEventIdentity, t.TimingContext
            ORDER BY s.ObservedAtUtc ASC, t.TimingObservationId ASC
        ) AS RowNumber
    FROM stg.MddMacTimingObservation AS t
    INNER JOIN stg.MddMacSourceSnapshot AS s
        ON s.SnapshotId = t.SnapshotId
    WHERE t.SourceEventIdentity IS NOT NULL
      AND t.SourceEventIdentityStatus = 'SOURCE_EVENT_IDENTITY_ESTABLISHED'
      AND t.RecordedAtTimeSourceText IS NOT NULL
)
SELECT
    latest.SourceEventIdentity,
    latest.TimingContext,
    latest.RecordedAtTimeSourceText AS LatestSourceRecordedAtTime,
    latest.RecordedAtTimeSourcePath AS LatestSourceRecordedAtTimeSourcePath,
    latest.ObservedAtUtc AS LatestSourceRecordedAtTimeObservedAtUtc,
    latest.SnapshotId AS LatestSourceRecordedAtTimeSnapshotId,
    latest.TimingObservationId AS LatestSourceRecordedAtTimeObservationId,
    last_seen.RecordedAtTimeSourceText AS LastObservedRecordedAtTime,
    last_seen.RecordedAtTimeSourcePath AS LastObservedRecordedAtTimeSourcePath,
    last_seen.ObservedAtUtc AS LastObservedRecordedAtTimeObservedAtUtc,
    last_seen.SnapshotId AS LastObservedRecordedAtTimeSnapshotId,
    last_seen.TimingObservationId AS LastObservedRecordedAtTimeObservationId,
    first_seen.RecordedAtTimeSourceText AS FirstObservedRecordedAtTime,
    first_seen.RecordedAtTimeSourcePath AS FirstObservedRecordedAtTimeSourcePath,
    first_seen.ObservedAtUtc AS FirstObservedRecordedAtTimeObservedAtUtc,
    first_seen.SnapshotId AS FirstObservedRecordedAtTimeSnapshotId,
    first_seen.TimingObservationId AS FirstObservedRecordedAtTimeObservationId,
    (
        SELECT COUNT_BIG(*)
        FROM stg.MddMacTimingObservation AS count_rows
        WHERE count_rows.SourceEventIdentity = latest.SourceEventIdentity
          AND count_rows.TimingContext = latest.TimingContext
          AND count_rows.RecordedAtTimeSourceText IS NOT NULL
    ) AS RecordedAtTimeNonNullObservationCount
FROM LatestObservation AS latest
LEFT JOIN LastObserved AS last_seen
    ON last_seen.SourceEventIdentity = latest.SourceEventIdentity
   AND last_seen.TimingContext = latest.TimingContext
   AND last_seen.RowNumber = 1
LEFT JOIN FirstObserved AS first_seen
    ON first_seen.SourceEventIdentity = latest.SourceEventIdentity
   AND first_seen.TimingContext = latest.TimingContext
   AND first_seen.RowNumber = 1
WHERE latest.LatestRowNumber = 1;
GO

CREATE OR ALTER VIEW analytics.vwMddMacSourceFieldCoverage
AS
SELECT
    s.SnapshotId,
    s.RunId,
    s.TargetIdentity,
    s.ObservedAtUtc,
    COUNT_BIG(DISTINCT t.TimingObservationId) AS TimingObservationCount,
    COUNT_BIG(DISTINCT CASE WHEN t.RecordedAtTimeSourceText IS NOT NULL THEN t.TimingObservationId END)
        AS RecordedAtTimeOccurrenceCount,
    COUNT_BIG(DISTINCT sec.ServiceSectionEvidenceId) AS ServiceSectionOccurrenceCount,
    COUNT_BIG(DISTINCT attr.AttributeEvidenceId) AS AttributeOccurrenceCount,
    COUNT_BIG(DISTINCT sit.SituationEvidenceId) AS SituationEvidenceOccurrenceCount,
    COUNT_BIG(DISTINCT gap.ParserGapId) AS ParserGapOccurrenceCount
FROM stg.MddMacSourceSnapshot AS s
LEFT JOIN stg.MddMacTimingObservation AS t ON t.SnapshotId = s.SnapshotId
LEFT JOIN stg.MddMacServiceSectionEvidence AS sec ON sec.SnapshotId = s.SnapshotId
LEFT JOIN stg.MddMacAttributeEvidence AS attr ON attr.SnapshotId = s.SnapshotId
LEFT JOIN stg.MddMacSituationEvidence AS sit ON sit.SnapshotId = s.SnapshotId
LEFT JOIN stg.MddMacParserGap AS gap ON gap.SnapshotId = s.SnapshotId
GROUP BY s.SnapshotId, s.RunId, s.TargetIdentity, s.ObservedAtUtc;
GO

/*
    Historical SQLite import lineage is deliberately additive and generic.
    These objects describe a migration/import; they do not claim that the
    historical rows were collected by the Mac SQL runtime.
*/

IF COL_LENGTH(N'ctl.MddMacTargetAudit', N'DurationMs') IS NULL
    ALTER TABLE ctl.MddMacTargetAudit ADD DurationMs DECIMAL(18,6) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacTargetAudit', N'SamplingSlot') IS NULL
    ALTER TABLE ctl.MddMacTargetAudit ADD SamplingSlot INT NULL;
GO

IF COL_LENGTH(N'ctl.MddMacTargetAudit', N'SelectionPolicyVersion') IS NULL
    ALTER TABLE ctl.MddMacTargetAudit ADD SelectionPolicyVersion VARCHAR(80) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacTargetAudit', N'SkipReason') IS NULL
    ALTER TABLE ctl.MddMacTargetAudit ADD SkipReason VARCHAR(120) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacTargetAudit', N'RetryDecision') IS NULL
    ALTER TABLE ctl.MddMacTargetAudit ADD RetryDecision VARCHAR(120) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacTargetAudit', N'RetryOutcome') IS NULL
    ALTER TABLE ctl.MddMacTargetAudit ADD RetryOutcome VARCHAR(120) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacQuotaPermit', N'ReleasedAtUtc') IS NULL
    ALTER TABLE ctl.MddMacQuotaPermit ADD ReleasedAtUtc DATETIME2(7) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacQuotaPermit', N'FinalizationReason') IS NULL
    ALTER TABLE ctl.MddMacQuotaPermit ADD FinalizationReason VARCHAR(120) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacQuotaPermit', N'SourceRepresentationJson') IS NULL
    ALTER TABLE ctl.MddMacQuotaPermit ADD SourceRepresentationJson NVARCHAR(MAX) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacQuotaPermit', N'AuthorizationKey') IS NULL
    ALTER TABLE ctl.MddMacQuotaPermit ADD AuthorizationKey NVARCHAR(240) NULL;
GO

IF OBJECT_ID(N'ctl.MddImportBatch', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddImportBatch
    (
        ImportBatchId UNIQUEIDENTIFIER NOT NULL
            CONSTRAINT PK_ctl_MddImportBatch PRIMARY KEY CLUSTERED,
        SourceDatabaseSha256 CHAR(64) NOT NULL,
        SourceByteSize BIGINT NOT NULL,
        SourceSchemaVersion INT NOT NULL,
        SourceDatabaseRole VARCHAR(40) NOT NULL,
        SourceCollectionContext VARCHAR(80) NOT NULL,
        SourceSessionStartUtc DATETIME2(7) NULL,
        SourceSessionEndUtc DATETIME2(7) NULL,
        SourcePathLineage NVARCHAR(400) NULL,
        ImportStartedAtUtc DATETIME2(7) NOT NULL,
        ImportCompletedAtUtc DATETIME2(7) NULL,
        Status VARCHAR(20) NOT NULL
            CONSTRAINT DF_ctl_MddImportBatch_Status DEFAULT ('IN_PROGRESS'),
        SourceCountsJson NVARCHAR(MAX) NOT NULL,
        InsertedCountsJson NVARCHAR(MAX) NULL,
        DuplicateCountsJson NVARCHAR(MAX) NULL,
        OverlapCountsJson NVARCHAR(MAX) NULL,
        ReconciliationResultJson NVARCHAR(MAX) NULL,
        ImporterVersion VARCHAR(80) NOT NULL,
        FailureReason NVARCHAR(2000) NULL,
        CONSTRAINT CK_ctl_MddImportBatch_Status
            CHECK (Status IN ('IN_PROGRESS', 'COMPLETE', 'FAILED')),
        CONSTRAINT CK_ctl_MddImportBatch_SourceByteSize
            CHECK (SourceByteSize >= 0),
        CONSTRAINT CK_ctl_MddImportBatch_SourceSha256
            CHECK (SourceDatabaseSha256 NOT LIKE '%[^0-9A-Fa-f]%')
    );
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'ctl.MddImportBatch')
      AND name = N'IX_ctl_MddImportBatch_Source_Status'
)
    CREATE INDEX IX_ctl_MddImportBatch_Source_Status
        ON ctl.MddImportBatch (SourceDatabaseSha256, Status, ImportStartedAtUtc DESC);
GO

IF OBJECT_ID(N'ctl.MddImportTableAudit', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddImportTableAudit
    (
        ImportTableAuditId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_ctl_MddImportTableAudit PRIMARY KEY CLUSTERED,
        ImportBatchId UNIQUEIDENTIFIER NOT NULL,
        SourceTableName NVARCHAR(128) NOT NULL,
        Classification VARCHAR(50) NOT NULL,
        ExplicitReason NVARCHAR(1000) NULL,
        SourceRowCount BIGINT NOT NULL,
        EligibleRowCount BIGINT NOT NULL,
        InsertedRowCount BIGINT NOT NULL,
        DuplicateRowCount BIGINT NOT NULL,
        OverlapClassifiedRowCount BIGINT NOT NULL,
        DestinationRowCount BIGINT NOT NULL,
        UnexplainedDifference BIGINT NOT NULL,
        ReconciliationStatus VARCHAR(30) NOT NULL,
        SourceTableDigestSha256 CHAR(64) NULL,
        CONSTRAINT UQ_ctl_MddImportTableAudit_Batch_Table
            UNIQUE (ImportBatchId, SourceTableName),
        CONSTRAINT FK_ctl_MddImportTableAudit_Batch
            FOREIGN KEY (ImportBatchId) REFERENCES ctl.MddImportBatch (ImportBatchId),
        CONSTRAINT CK_ctl_MddImportTableAudit_Classification
            CHECK (Classification IN ('FULL_IMPORT', 'LINEAGE_ONLY', 'NOT_IMPORTED_WITH_EXPLICIT_REASON')),
        CONSTRAINT CK_ctl_MddImportTableAudit_ReconciliationStatus
            CHECK (ReconciliationStatus IN ('PENDING', 'PASS', 'FAIL', 'NOT_APPLICABLE'))
    );
END;
GO

IF OBJECT_ID(N'ctl.MddImportEvidenceAudit', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddImportEvidenceAudit
    (
        ImportEvidenceAuditId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_ctl_MddImportEvidenceAudit PRIMARY KEY CLUSTERED,
        ImportBatchId UNIQUEIDENTIFIER NOT NULL,
        EvidenceFamily NVARCHAR(128) NOT NULL,
        SourceRowCount BIGINT NOT NULL,
        EligibleRowCount BIGINT NOT NULL,
        InsertedRowCount BIGINT NOT NULL,
        DuplicateRowCount BIGINT NOT NULL,
        OverlapClassifiedRowCount BIGINT NOT NULL,
        DestinationRowCount BIGINT NOT NULL,
        UnexplainedDifference BIGINT NOT NULL,
        ReconciliationStatus VARCHAR(30) NOT NULL,
        CONSTRAINT UQ_ctl_MddImportEvidenceAudit_Batch_Family
            UNIQUE (ImportBatchId, EvidenceFamily),
        CONSTRAINT FK_ctl_MddImportEvidenceAudit_Batch
            FOREIGN KEY (ImportBatchId) REFERENCES ctl.MddImportBatch (ImportBatchId),
        CONSTRAINT CK_ctl_MddImportEvidenceAudit_ReconciliationStatus
            CHECK (ReconciliationStatus IN ('PENDING', 'PASS', 'FAIL', 'NOT_APPLICABLE'))
    );
END;
GO

IF OBJECT_ID(N'ctl.MddImportSourceRow', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddImportSourceRow
    (
        ImportSourceRowId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_ctl_MddImportSourceRow PRIMARY KEY CLUSTERED,
        ImportBatchId UNIQUEIDENTIFIER NOT NULL,
        SourceDatabaseSha256 CHAR(64) NOT NULL,
        SourceTableName NVARCHAR(128) NOT NULL,
        SourcePrimaryKey NVARCHAR(1000) NOT NULL,
        SourceIdentityHash CHAR(64) NOT NULL,
        SourceRowHashSha256 CHAR(64) NULL,
        Classification VARCHAR(50) NOT NULL,
        RowStatus VARCHAR(30) NOT NULL,
        OverlapClassification VARCHAR(80) NULL,
        DestinationObjectName NVARCHAR(256) NULL,
        DestinationKey NVARCHAR(1000) NULL,
        SourceRowJson NVARCHAR(MAX) NULL,
        CreatedAtUtc DATETIME2(7) NOT NULL
            CONSTRAINT DF_ctl_MddImportSourceRow_CreatedAtUtc DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT UQ_ctl_MddImportSourceRow_Source_Lineage
            UNIQUE (SourceDatabaseSha256, SourceTableName, SourceIdentityHash),
        CONSTRAINT FK_ctl_MddImportSourceRow_Batch
            FOREIGN KEY (ImportBatchId) REFERENCES ctl.MddImportBatch (ImportBatchId),
        CONSTRAINT CK_ctl_MddImportSourceRow_Classification
            CHECK (Classification IN ('FULL_IMPORT', 'LINEAGE_ONLY', 'NOT_IMPORTED_WITH_EXPLICIT_REASON')),
        CONSTRAINT CK_ctl_MddImportSourceRow_RowStatus
            CHECK (RowStatus IN ('INSERTED', 'DUPLICATE', 'OVERLAP', 'LINEAGE_ONLY', 'NOT_IMPORTED'))
    );
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'ctl.MddImportSourceRow')
      AND name = N'IX_ctl_MddImportSourceRow_Batch_Table_Status'
)
    CREATE INDEX IX_ctl_MddImportSourceRow_Batch_Table_Status
        ON ctl.MddImportSourceRow (ImportBatchId, SourceTableName, RowStatus);
GO

IF OBJECT_ID(N'ctl.MddImportOverlap', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddImportOverlap
    (
        ImportOverlapId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_ctl_MddImportOverlap PRIMARY KEY CLUSTERED,
        ImportBatchId UNIQUEIDENTIFIER NOT NULL,
        EvidenceFamily NVARCHAR(128) NOT NULL,
        SourceIdentityHash CHAR(64) NOT NULL,
        Classification VARCHAR(80) NOT NULL,
        ExistingObjectName NVARCHAR(256) NULL,
        ExistingKey NVARCHAR(1000) NULL,
        DetailsJson NVARCHAR(MAX) NOT NULL,
        RecordedAtUtc DATETIME2(7) NOT NULL
            CONSTRAINT DF_ctl_MddImportOverlap_RecordedAtUtc DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT UQ_ctl_MddImportOverlap_Batch_Family_Source
            UNIQUE (ImportBatchId, EvidenceFamily, SourceIdentityHash),
        CONSTRAINT FK_ctl_MddImportOverlap_Batch
            FOREIGN KEY (ImportBatchId) REFERENCES ctl.MddImportBatch (ImportBatchId)
    );
END;
GO
