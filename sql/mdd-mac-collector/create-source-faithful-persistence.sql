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
        ReleasedAtUtc DATETIME2(7) NULL,
        FinalizationReason VARCHAR(80) NULL,
        LiveClientInvocation BIT NOT NULL
            CONSTRAINT DF_ctl_MddMacQuotaPermit_LiveClientInvocation DEFAULT (0),
        CONSTRAINT CK_ctl_MddMacQuotaPermit_Amount CHECK (Amount > 0),
        CONSTRAINT CK_ctl_MddMacQuotaPermit_DecisionKind
            CHECK (DecisionKind IN ('INITIAL', 'RETRY')),
        CONSTRAINT CK_ctl_MddMacQuotaPermit_Boundary
            CHECK
            (
                (DecisionKind = 'INITIAL' AND Boundary BETWEEN 1 AND 210000)
                OR (DecisionKind = 'RETRY' AND Boundary BETWEEN 1 AND 207500)
            )
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
        /* COMPLETE rows include all accounted local permits through
           LocalUsageIncludedThroughUtc. Runtime authorization adds only
           permits strictly after that stored cutoff. */
        ConfirmedProviderAttempts BIGINT NOT NULL,
        ConservativeUnknownAttempts BIGINT NOT NULL,
        ExplicitSharedConsumerReservedAttempts BIGINT NOT NULL,
        AccountingStatus VARCHAR(30) NOT NULL,
        IncludesAllSharedConsumers BIT NOT NULL,
        BaselineAsOfUtc DATETIME2(7) NOT NULL,
        LocalUsageIncludedThroughUtc DATETIME2(7) NOT NULL,
        SourceDescription NVARCHAR(1000) NOT NULL,
        AccountingModel VARCHAR(40) NULL,
        ProviderMeasurementCutoffUtc DATETIME2(7) NULL,
        StaleAfterUtc DATETIME2(7) NULL,
        SourceEvidenceHash CHAR(64) NULL,
        ReconciliationKey NVARCHAR(240) NULL,
        ProviderPeriodTimezone VARCHAR(80) NULL,
        ProviderPeriodStartUtc DATETIME2(7) NULL,
        ProviderPeriodEndUtc DATETIME2(7) NULL,
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

/* Provider-wide baseline history is append-only administrative evidence.  It
   is intentionally not part of the collector runtime permission contract. */

IF OBJECT_ID(N'ctl.MddMacSharedQuotaReconciliation', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddMacSharedQuotaReconciliation
    (
        ReconciliationId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_ctl_MddMacSharedQuotaReconciliation PRIMARY KEY CLUSTERED,
        ReconciliationKey NVARCHAR(240) NOT NULL,
        MonthKey CHAR(7) NOT NULL,
        AccountingModel VARCHAR(40) NOT NULL,
        ProviderReportedCumulativeAttempts BIGINT NOT NULL,
        ProviderReportedConfirmedAttempts BIGINT NOT NULL,
        ProviderReportedUnknownAttempts BIGINT NOT NULL,
        UnexplainedDiscrepancyAttempts BIGINT NOT NULL,
        ProviderIncludesLocalUsage BIT NOT NULL,
        LocalUsageIncludedThroughUtc DATETIME2(7) NOT NULL,
        LocalConfirmedAttemptsThroughCutoff BIGINT NOT NULL,
        LocalUnknownAttemptsThroughCutoff BIGINT NOT NULL,
        LocalPendingReservationsThroughCutoff BIGINT NOT NULL,
        LocalConfirmedAttemptsAfterCutoff BIGINT NOT NULL,
        LocalUnknownAttemptsAfterCutoff BIGINT NOT NULL,
        LocalPendingReservationsAfterCutoff BIGINT NOT NULL,
        ExplicitSharedConsumerReservedAttempts BIGINT NOT NULL,
        ConservativeUnknownAttempts BIGINT NOT NULL,
        AccountedTotalAtReconciliationUtc BIGINT NOT NULL,
        ProviderMeasurementCutoffUtc DATETIME2(7) NOT NULL,
        BaselineAsOfUtc DATETIME2(7) NOT NULL,
        StaleAfterUtc DATETIME2(7) NOT NULL,
        ProviderPeriodTimezone VARCHAR(80) NOT NULL,
        ProviderPeriodStartUtc DATETIME2(7) NOT NULL,
        ProviderPeriodEndUtc DATETIME2(7) NOT NULL,
        IncludesAllSharedConsumers BIT NOT NULL,
        SourceDescription NVARCHAR(1000) NOT NULL,
        SourceReference NVARCHAR(1000) NOT NULL,
        SourceEvidenceHash CHAR(64) NOT NULL,
        OperatingModelEvidence NVARCHAR(2000) NOT NULL,
        EvidenceJson NVARCHAR(MAX) NOT NULL,
        AccountingStatus VARCHAR(30) NOT NULL,
        RecordedAtUtc DATETIME2(7) NOT NULL
            CONSTRAINT DF_ctl_MddMacSharedQuotaReconciliation_RecordedAtUtc DEFAULT (SYSUTCDATETIME()),
        AppliedAtUtc DATETIME2(7) NULL,
        CONSTRAINT UQ_ctl_MddMacSharedQuotaReconciliation_Key UNIQUE (ReconciliationKey),
        CONSTRAINT CK_ctl_MddMacSharedQuotaReconciliation_Counts CHECK
        (
            ProviderReportedCumulativeAttempts >= 0
            AND ProviderReportedConfirmedAttempts >= 0
            AND ProviderReportedUnknownAttempts >= 0
            AND UnexplainedDiscrepancyAttempts >= 0
            AND LocalConfirmedAttemptsThroughCutoff >= 0
            AND LocalUnknownAttemptsThroughCutoff >= 0
            AND LocalPendingReservationsThroughCutoff >= 0
            AND LocalConfirmedAttemptsAfterCutoff >= 0
            AND LocalUnknownAttemptsAfterCutoff >= 0
            AND LocalPendingReservationsAfterCutoff >= 0
            AND ExplicitSharedConsumerReservedAttempts >= 0
            AND ConservativeUnknownAttempts >= 0
            AND AccountedTotalAtReconciliationUtc >= 0
        ),
        CONSTRAINT CK_ctl_MddMacSharedQuotaReconciliation_ProviderCounts CHECK
        (
            ProviderReportedConfirmedAttempts + ProviderReportedUnknownAttempts
                = ProviderReportedCumulativeAttempts
        ),
        CONSTRAINT CK_ctl_MddMacSharedQuotaReconciliation_Status CHECK
            (AccountingStatus IN ('APPLIED', 'SUPERSEDED'))
    );
END;
GO

/*
    The October dashboard does not establish a provider measurement cutoff or
    reset timezone.  It must therefore not be written as a COMPLETE
    provider-period baseline.  This separate authorization table records the
    explicitly approved, bounded interim policy.  Runtime reads are limited
    to one active row for October 2026; the local permit ledger remains the
    durable source for all usage after LocalUsageIncludedThroughUtc.
*/

IF OBJECT_ID(N'ctl.MddMacConservativeBootstrapAuthorization', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddMacConservativeBootstrapAuthorization
    (
        AuthorizationId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_ctl_MddMacConservativeBootstrapAuthorization PRIMARY KEY CLUSTERED,
        AuthorizationKey NVARCHAR(240) NOT NULL,
        MonthKey CHAR(7) NOT NULL,
        AuthorizationMode VARCHAR(50) NOT NULL,
        AccountingModel VARCHAR(40) NOT NULL,
        ScreenshotCumulativeRequests BIGINT NOT NULL,
        ScreenshotMonthlyLimit BIGINT NOT NULL,
        ScreenshotSha256 CHAR(64) NOT NULL,
        ConfirmedLocalAttempts BIGINT NOT NULL,
        ConservativeUnknownLocalAttempts BIGINT NOT NULL,
        PendingLocalReservations BIGINT NOT NULL,
        AccountedTotalAtAuthorization BIGINT NOT NULL,
        ProviderQuotaCeiling INT NOT NULL,
        NormalOperationalMaximum INT NOT NULL,
        RetryProtectionBoundary INT NOT NULL,
        ValidFromUtc DATETIME2(7) NOT NULL,
        LocalUsageIncludedThroughUtc DATETIME2(7) NOT NULL,
        StopAtUtc DATETIME2(7) NOT NULL,
        OperatorApprovalAtUtc DATETIME2(7) NOT NULL,
        OperatorApprovalStatementHash CHAR(64) NOT NULL,
        CredentialExclusivityConfirmed BIT NOT NULL,
        ProviderScopeVerified BIT NOT NULL,
        ProviderMeasurementCutoffEstablished BIT NOT NULL,
        ProviderResetTimezoneEstablished BIT NOT NULL,
        UnexpectedExternalUsage BIT NOT NULL,
        SourceEvidenceHash CHAR(64) NOT NULL,
        AuthorizationStatus VARCHAR(20) NOT NULL,
        EvidenceJson NVARCHAR(MAX) NOT NULL,
        RecordedAtUtc DATETIME2(7) NOT NULL
            CONSTRAINT DF_ctl_MddMacConservativeBootstrapAuthorization_RecordedAtUtc DEFAULT (SYSUTCDATETIME()),
        RevokedAtUtc DATETIME2(7) NULL,
        RevocationReason NVARCHAR(500) NULL,
        CONSTRAINT UQ_ctl_MddMacConservativeBootstrapAuthorization_Key UNIQUE (AuthorizationKey),
        CONSTRAINT CK_ctl_MddMacConservativeBootstrapAuthorization_Mode CHECK
        (
            AuthorizationMode = 'CONSERVATIVE_OCTOBER_BOOTSTRAP'
            AND AccountingModel = 'EXCLUSIVE_CREDENTIAL'
            AND MonthKey = '2026-10'
        ),
        CONSTRAINT CK_ctl_MddMacConservativeBootstrapAuthorization_Limits CHECK
        (
            ScreenshotMonthlyLimit = 250000
            AND ProviderQuotaCeiling = 250000
            AND NormalOperationalMaximum = 210000
            AND RetryProtectionBoundary = 207500
        ),
        CONSTRAINT CK_ctl_MddMacConservativeBootstrapAuthorization_Counts CHECK
        (
            ScreenshotCumulativeRequests >= 0
            AND ScreenshotMonthlyLimit >= 0
            AND ConfirmedLocalAttempts >= 0
            AND ConservativeUnknownLocalAttempts >= 0
            AND PendingLocalReservations >= 0
            AND AccountedTotalAtAuthorization >= 0
            AND AccountedTotalAtAuthorization = ScreenshotCumulativeRequests
                + ConfirmedLocalAttempts
                + ConservativeUnknownLocalAttempts
                + PendingLocalReservations
            AND AccountedTotalAtAuthorization <= 250000
        ),
        CONSTRAINT CK_ctl_MddMacConservativeBootstrapAuthorization_Interval CHECK
        (
            ValidFromUtc = OperatorApprovalAtUtc
            AND LocalUsageIncludedThroughUtc = OperatorApprovalAtUtc
            AND ValidFromUtc < StopAtUtc
            AND StopAtUtc <= CONVERT(DATETIME2(7), '2026-10-30T23:00:00', 126)
        ),
        CONSTRAINT CK_ctl_MddMacConservativeBootstrapAuthorization_Evidence CHECK
        (
            CredentialExclusivityConfirmed = 1
            AND ProviderScopeVerified = 0
            AND ProviderMeasurementCutoffEstablished = 0
            AND ProviderResetTimezoneEstablished = 0
            AND UnexpectedExternalUsage = 0
        ),
        CONSTRAINT CK_ctl_MddMacConservativeBootstrapAuthorization_Status CHECK
            (AuthorizationStatus IN ('ACTIVE', 'REVOKED', 'EXPIRED'))
    );
END;
GO

IF NOT EXISTS
(
    SELECT 1
    FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'ctl.MddMacConservativeBootstrapAuthorization')
      AND name = N'IX_ctl_MddMacConservativeBootstrapAuthorization_Active'
)
    CREATE INDEX IX_ctl_MddMacConservativeBootstrapAuthorization_Active
        ON ctl.MddMacConservativeBootstrapAuthorization
            (MonthKey, AuthorizationStatus, ValidFromUtc, StopAtUtc);
GO

IF NOT EXISTS
(
    SELECT 1
    FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'ctl.MddMacConservativeBootstrapAuthorization')
      AND name = N'UX_ctl_MddMacConservativeBootstrapAuthorization_ActiveMonth'
)
    CREATE UNIQUE INDEX UX_ctl_MddMacConservativeBootstrapAuthorization_ActiveMonth
        ON ctl.MddMacConservativeBootstrapAuthorization (MonthKey)
        WHERE AuthorizationStatus = 'ACTIVE';
GO

/*
    Future-month rollover is deliberately separate from the October
    bootstrap.  An administrator may register a verified provider reset
    contract once; the runtime can then call the guarded procedure below on
    every normal heartbeat.  No contract row means no automatic zero reset.
*/

IF OBJECT_ID(N'ctl.MddMacQuotaResetContract', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddMacQuotaResetContract
    (
        ContractId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_ctl_MddMacQuotaResetContract PRIMARY KEY CLUSTERED,
        ContractKey NVARCHAR(240) NOT NULL,
        ProviderName VARCHAR(80) NOT NULL,
        ResetModel VARCHAR(60) NOT NULL,
        ResetTimezone VARCHAR(80) NOT NULL,
        /* Windows identifier used only for SQL Server's AT TIME ZONE.  It is
           an explicit representation of an exact provider contract, never an
           inference from the provider's location. */
        SqlServerResetTimezone VARCHAR(128) NULL,
        EffectiveFromUtc DATETIME2(7) NOT NULL,
        ActivationDelaySeconds INT NOT NULL
            CONSTRAINT DF_ctl_MddMacQuotaResetContract_ActivationDelaySeconds DEFAULT (1800),
        ProviderQuotaCeiling INT NOT NULL,
        CredentialExclusivityConfirmed BIT NOT NULL,
        ProviderResetBoundaryVerified BIT NOT NULL,
        CalendarMonthResetModelEstablished BIT NOT NULL
            CONSTRAINT DF_ctl_MddMacQuotaResetContract_CalendarMonthResetModelEstablished DEFAULT (0),
        OperatorAssumptionApproved BIT NOT NULL
            CONSTRAINT DF_ctl_MddMacQuotaResetContract_OperatorAssumptionApproved DEFAULT (0),
        SupportedResetTimezonesJson NVARCHAR(2000) NULL,
        SourceEvidenceHash CHAR(64) NOT NULL,
        EvidenceJson NVARCHAR(MAX) NOT NULL,
        ContractStatus VARCHAR(20) NOT NULL,
        RecordedAtUtc DATETIME2(7) NOT NULL
            CONSTRAINT DF_ctl_MddMacQuotaResetContract_RecordedAtUtc DEFAULT (SYSUTCDATETIME()),
        RevokedAtUtc DATETIME2(7) NULL,
        RevocationReason NVARCHAR(500) NULL,
        CONSTRAINT UQ_ctl_MddMacQuotaResetContract_Key UNIQUE (ContractKey),
        CONSTRAINT CK_ctl_MddMacQuotaResetContract_Contract CHECK
        (
            ProviderName = 'MDD NRW'
            AND ResetModel IN ('CALENDAR_MONTH_PROVIDER_TIMEZONE', 'CALENDAR_MONTH_BOUNDED_TIMEZONE_UNCERTAINTY')
            AND LEN(ResetTimezone) > 0
            AND EffectiveFromUtc IS NOT NULL
            AND ActivationDelaySeconds BETWEEN 0 AND 86400
            AND ProviderQuotaCeiling = 250000
            AND CredentialExclusivityConfirmed = 1
            AND
            (
                (
                    ResetModel = 'CALENDAR_MONTH_PROVIDER_TIMEZONE'
                    AND ProviderResetBoundaryVerified = 1
                    AND CalendarMonthResetModelEstablished = 1
                    AND OperatorAssumptionApproved = 0
                    AND SqlServerResetTimezone IS NOT NULL
                )
                OR
                (
                    ResetModel = 'CALENDAR_MONTH_BOUNDED_TIMEZONE_UNCERTAINTY'
                    AND ProviderResetBoundaryVerified = 0
                    AND CalendarMonthResetModelEstablished = 1
                    AND OperatorAssumptionApproved = 1
                    AND SupportedResetTimezonesJson IS NOT NULL
                    AND SqlServerResetTimezone IS NULL
                )
            )
            AND LEN(SourceEvidenceHash) = 64
        ),
        CONSTRAINT CK_ctl_MddMacQuotaResetContract_Status CHECK
            (ContractStatus IN ('ACTIVE', 'REVOKED'))
    );
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'ctl.MddMacQuotaResetContract')
      AND name = N'UX_ctl_MddMacQuotaResetContract_Active'
)
    CREATE UNIQUE INDEX UX_ctl_MddMacQuotaResetContract_Active
        ON ctl.MddMacQuotaResetContract (ContractStatus)
        WHERE ContractStatus = 'ACTIVE';
GO

/*
    Older installations may have the original shared-accounting table before
    the lineage columns below were added.  Ensure the procedure's referenced
    columns exist before CREATE OR ALTER is compiled; the later idempotent
    column guards remain the canonical migration checks for fresh and legacy
    installations alike.
*/
IF COL_LENGTH(N'ctl.MddMacSharedQuotaAccounting', N'AccountingModel') IS NULL
    ALTER TABLE ctl.MddMacSharedQuotaAccounting ADD AccountingModel VARCHAR(40) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacSharedQuotaAccounting', N'ProviderMeasurementCutoffUtc') IS NULL
    ALTER TABLE ctl.MddMacSharedQuotaAccounting ADD ProviderMeasurementCutoffUtc DATETIME2(7) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacSharedQuotaAccounting', N'StaleAfterUtc') IS NULL
    ALTER TABLE ctl.MddMacSharedQuotaAccounting ADD StaleAfterUtc DATETIME2(7) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacSharedQuotaAccounting', N'SourceEvidenceHash') IS NULL
    ALTER TABLE ctl.MddMacSharedQuotaAccounting ADD SourceEvidenceHash CHAR(64) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacSharedQuotaAccounting', N'ReconciliationKey') IS NULL
    ALTER TABLE ctl.MddMacSharedQuotaAccounting ADD ReconciliationKey NVARCHAR(240) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacSharedQuotaAccounting', N'ProviderPeriodTimezone') IS NULL
    ALTER TABLE ctl.MddMacSharedQuotaAccounting ADD ProviderPeriodTimezone VARCHAR(80) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacSharedQuotaAccounting', N'ProviderPeriodStartUtc') IS NULL
    ALTER TABLE ctl.MddMacSharedQuotaAccounting ADD ProviderPeriodStartUtc DATETIME2(7) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacSharedQuotaAccounting', N'ProviderPeriodEndUtc') IS NULL
    ALTER TABLE ctl.MddMacSharedQuotaAccounting ADD ProviderPeriodEndUtc DATETIME2(7) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacQuotaResetContract', N'CalendarMonthResetModelEstablished') IS NULL
    ALTER TABLE ctl.MddMacQuotaResetContract
        ADD CalendarMonthResetModelEstablished BIT NOT NULL
            CONSTRAINT DF_ctl_MddMacQuotaResetContract_CalendarMonthResetModelEstablished_Legacy DEFAULT (0);
GO

IF COL_LENGTH(N'ctl.MddMacQuotaResetContract', N'OperatorAssumptionApproved') IS NULL
    ALTER TABLE ctl.MddMacQuotaResetContract
        ADD OperatorAssumptionApproved BIT NOT NULL
            CONSTRAINT DF_ctl_MddMacQuotaResetContract_OperatorAssumptionApproved_Legacy DEFAULT (0);
GO

IF COL_LENGTH(N'ctl.MddMacQuotaResetContract', N'SupportedResetTimezonesJson') IS NULL
    ALTER TABLE ctl.MddMacQuotaResetContract ADD SupportedResetTimezonesJson NVARCHAR(2000) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacQuotaResetContract', N'SqlServerResetTimezone') IS NULL
    ALTER TABLE ctl.MddMacQuotaResetContract ADD SqlServerResetTimezone VARCHAR(128) NULL;
GO

IF EXISTS
(
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id = OBJECT_ID(N'ctl.MddMacQuotaResetContract')
      AND name = N'CK_ctl_MddMacQuotaResetContract_Contract'
)
    ALTER TABLE ctl.MddMacQuotaResetContract
        DROP CONSTRAINT CK_ctl_MddMacQuotaResetContract_Contract;
GO

ALTER TABLE ctl.MddMacQuotaResetContract
    ADD CONSTRAINT CK_ctl_MddMacQuotaResetContract_Contract
        CHECK
        (
            ProviderName = 'MDD NRW'
            AND ResetModel IN ('CALENDAR_MONTH_PROVIDER_TIMEZONE', 'CALENDAR_MONTH_BOUNDED_TIMEZONE_UNCERTAINTY')
            AND LEN(ResetTimezone) > 0
            AND EffectiveFromUtc IS NOT NULL
            AND ActivationDelaySeconds BETWEEN 0 AND 86400
            AND ProviderQuotaCeiling = 250000
            AND CredentialExclusivityConfirmed = 1
            AND CalendarMonthResetModelEstablished = 1
            AND LEN(SourceEvidenceHash) = 64
            AND
            (
                (
                    ResetModel = 'CALENDAR_MONTH_PROVIDER_TIMEZONE'
                    AND ProviderResetBoundaryVerified = 1
                    AND OperatorAssumptionApproved = 0
                    AND SqlServerResetTimezone IS NOT NULL
                )
                OR
                (
                    ResetModel = 'CALENDAR_MONTH_BOUNDED_TIMEZONE_UNCERTAINTY'
                    AND ProviderResetBoundaryVerified = 0
                    AND OperatorAssumptionApproved = 1
                    AND SupportedResetTimezonesJson IS NOT NULL
                    AND SqlServerResetTimezone IS NULL
                )
            )
        );
GO

/*
    The procedure is the only runtime write path for automatic rollover.  It
    locks the administrator contract, the period/accounting key, and all
    prior pending permits before inserting a zero-based COMPLETE row.  The
    SQL independently derives the expected UTC bounds from the immutable
    contract.  Exact contracts carry an explicit Windows timezone identifier
    for AT TIME ZONE; bounded contracts are restricted to the registered
    UTC-12..UTC+14 envelope and use its fixed conservative UTC bounds.  The
    caller's period timestamps and @AtUtc are consistency inputs only.
    SQL Server UTC is authoritative for activation and current-period
    eligibility.  Safely stale prior-period permits are conservatively
    finalized inside the same guarded transaction before the unresolved-
    permit boundary is checked.
*/

CREATE OR ALTER PROCEDURE ctl.uspEnsureMddMacQuotaPeriod
    @ContractKey NVARCHAR(240),
    @MonthKey CHAR(7),
    @ProviderPeriodTimezone VARCHAR(80),
    @ProviderPeriodStartUtc DATETIME2(7),
    @ProviderPeriodEndUtc DATETIME2(7),
    @LocalUsageIncludedThroughUtc DATETIME2(7),
    @AtUtc DATETIME2(7),
    @SourceEvidenceHash CHAR(64),
    @ReconciliationKey NVARCHAR(240),
    @StaleRecoverySeconds INT = 300
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    BEGIN TRANSACTION;

    DECLARE @contract_id BIGINT;
    DECLARE @reset_model VARCHAR(60);
    DECLARE @reset_timezone VARCHAR(80);
    DECLARE @effective_from_utc DATETIME2(7);
    DECLARE @activation_delay_seconds INT;
    DECLARE @provider_quota_ceiling INT;
    DECLARE @credential_exclusivity_confirmed BIT;
    DECLARE @provider_reset_boundary_verified BIT;
    DECLARE @calendar_month_reset_model_established BIT;
    DECLARE @operator_assumption_approved BIT;
    DECLARE @contract_source_hash CHAR(64);
    DECLARE @accounting_model VARCHAR(40);
    DECLARE @provider_name VARCHAR(80);
    DECLARE @sql_server_reset_timezone VARCHAR(128);
    DECLARE @supported_reset_timezones_json NVARCHAR(2000);
    DECLARE @server_utc DATETIME2(7) = SYSUTCDATETIME();
    DECLARE @lock_result INT;
    DECLARE @period_month_start DATE;
    DECLARE @expected_period_start_utc DATETIME2(7);
    DECLARE @expected_period_end_utc DATETIME2(7);

    IF @StaleRecoverySeconds IS NULL OR @StaleRecoverySeconds NOT BETWEEN 1 AND 86400
        THROW 51000, 'MDD quota stale-recovery window is outside the approved bound', 1;

    EXEC @lock_result = sys.sp_getapplock
        @Resource = N'CologneTransitIntelligence:MDD:QUOTA_ROLLOVER',
        @LockMode = N'Exclusive',
        @LockOwner = N'Transaction',
        @LockTimeout = 0;
    IF @lock_result < 0
        THROW 51000, 'MDD quota rollover coordination lock could not be acquired', 1;

    SELECT TOP (1)
        @contract_id = ContractId,
        @provider_name = ProviderName,
        @reset_model = ResetModel,
        @reset_timezone = ResetTimezone,
        @sql_server_reset_timezone = SqlServerResetTimezone,
        @effective_from_utc = EffectiveFromUtc,
        @activation_delay_seconds = ActivationDelaySeconds,
        @provider_quota_ceiling = ProviderQuotaCeiling,
        @credential_exclusivity_confirmed = CredentialExclusivityConfirmed,
        @provider_reset_boundary_verified = ProviderResetBoundaryVerified,
        @calendar_month_reset_model_established = CalendarMonthResetModelEstablished,
        @operator_assumption_approved = OperatorAssumptionApproved,
        @supported_reset_timezones_json = SupportedResetTimezonesJson,
        @contract_source_hash = SourceEvidenceHash
    FROM ctl.MddMacQuotaResetContract WITH (UPDLOCK, HOLDLOCK)
    WHERE ContractKey = @ContractKey
      AND ContractStatus = 'ACTIVE';

    IF @contract_id IS NULL
        THROW 51000, 'no active verified MDD quota reset contract exists', 1;
    IF @provider_name IS NULL OR @provider_name <> 'MDD NRW'
        THROW 51000, 'MDD quota reset contract provider identity is invalid', 1;
    IF @reset_model IS NULL
       OR @reset_model NOT IN ('CALENDAR_MONTH_PROVIDER_TIMEZONE', 'CALENDAR_MONTH_BOUNDED_TIMEZONE_UNCERTAINTY')
        THROW 51000, 'MDD quota reset contract model is not an approved calendar-month policy', 1;
    IF @credential_exclusivity_confirmed IS NULL OR @credential_exclusivity_confirmed <> 1
       OR @calendar_month_reset_model_established IS NULL OR @calendar_month_reset_model_established <> 1
        THROW 51000, 'MDD quota reset contract is not approved for exclusive calendar-month operation', 1;
    IF @reset_model = 'CALENDAR_MONTH_PROVIDER_TIMEZONE'
       AND (
           @provider_reset_boundary_verified IS NULL OR @provider_reset_boundary_verified <> 1
           OR @operator_assumption_approved IS NULL OR @operator_assumption_approved <> 0
       )
        THROW 51000, 'exact MDD quota reset contract is not verified', 1;
    IF @reset_model = 'CALENDAR_MONTH_BOUNDED_TIMEZONE_UNCERTAINTY'
       AND (
           @provider_reset_boundary_verified IS NULL OR @provider_reset_boundary_verified <> 0
           OR @operator_assumption_approved IS NULL OR @operator_assumption_approved <> 1
           OR @sql_server_reset_timezone IS NOT NULL
           OR @supported_reset_timezones_json IS NULL
           OR @supported_reset_timezones_json <> N'["Etc/GMT+12", "Etc/GMT-14"]'
       )
        THROW 51000, 'bounded MDD quota reset policy lacks explicit operator approval', 1;
    IF @reset_model = 'CALENDAR_MONTH_PROVIDER_TIMEZONE'
       AND (@sql_server_reset_timezone IS NULL OR LTRIM(RTRIM(@sql_server_reset_timezone)) = '')
        THROW 51000, 'exact MDD quota reset contract lacks a SQL Server timezone identifier', 1;
    IF @provider_quota_ceiling IS NULL OR @provider_quota_ceiling <> 250000
       OR @SourceEvidenceHash IS NULL OR @contract_source_hash IS NULL
       OR @SourceEvidenceHash <> @contract_source_hash
        THROW 51000, 'MDD quota reset contract evidence hash or ceiling does not match', 1;
    IF @SourceEvidenceHash IS NULL
       OR LEN(@SourceEvidenceHash) <> 64
       OR @SourceEvidenceHash LIKE '%[^0-9a-f]%'
        THROW 51000, 'MDD quota reset contract evidence hash is malformed', 1;
    IF @ProviderPeriodTimezone IS NULL OR @reset_timezone IS NULL
       OR @ProviderPeriodTimezone <> @reset_timezone
        THROW 51000, 'MDD quota reset timezone does not match the registered contract', 1;
    SET @period_month_start = TRY_CONVERT(DATE, LTRIM(RTRIM(@MonthKey)) + '-01', 23);
    IF @period_month_start IS NULL
       OR CONVERT(CHAR(7), @period_month_start, 126) <> LTRIM(RTRIM(@MonthKey))
        THROW 51000, 'MDD quota period key is not a canonical calendar month', 1;
    IF @ReconciliationKey IS NULL
       OR @ReconciliationKey <> CONCAT(N'AUTO-ROLLOVER:', @ContractKey, N':', LTRIM(RTRIM(@MonthKey)))
        THROW 51000, 'MDD quota reconciliation identity does not match the registered contract and period', 1;
    SET @accounting_model = CASE
        WHEN @reset_model = 'CALENDAR_MONTH_BOUNDED_TIMEZONE_UNCERTAINTY'
            THEN 'AUTOMATIC_MONTHLY_ROLLOVER_BOUNDED'
        ELSE 'AUTOMATIC_MONTHLY_ROLLOVER'
    END;
    IF @ProviderPeriodStartUtc IS NULL OR @ProviderPeriodEndUtc IS NULL
       OR @LocalUsageIncludedThroughUtc IS NULL
        THROW 51000, 'MDD quota period boundaries are required', 1;
    IF @ProviderPeriodStartUtc >= @ProviderPeriodEndUtc
        THROW 51000, 'MDD quota period interval is invalid', 1;
    IF @effective_from_utc IS NULL OR @ProviderPeriodStartUtc < @effective_from_utc
        THROW 51000, 'MDD quota reset contract is not effective for this period', 1;
    IF @activation_delay_seconds IS NULL OR @activation_delay_seconds NOT BETWEEN 0 AND 86400
        THROW 51000, 'MDD quota reset contract activation delay is outside the approved bound', 1;
    IF @AtUtc IS NULL
       OR ABS(DATEDIFF_BIG(SECOND, @AtUtc, @server_utc)) > 300
        THROW 51000, 'caller authorization timestamp is stale or unverifiable against SQL Server UTC', 1;

    /*
       These are the only period bounds SQL will authorize.  Exact contracts
       use SQL Server's Windows timezone database, including DST transitions.
       The bounded candidate is deliberately limited to the immutable global
       civil-time envelope: latest possible start is UTC-12 midnight and the
       earliest possible next-month start is UTC+14 midnight.
    */
    IF @reset_model = 'CALENDAR_MONTH_PROVIDER_TIMEZONE'
    BEGIN
        DECLARE @exact_local_start DATETIME2(7) = CAST(@period_month_start AS DATETIME2(7));
        DECLARE @exact_local_end DATETIME2(7) = CAST(DATEADD(MONTH, 1, @period_month_start) AS DATETIME2(7));
        SET @expected_period_start_utc = CONVERT(
            DATETIME2(7),
            SWITCHOFFSET(@exact_local_start AT TIME ZONE @sql_server_reset_timezone, '+00:00')
        );
        SET @expected_period_end_utc = CONVERT(
            DATETIME2(7),
            SWITCHOFFSET(@exact_local_end AT TIME ZONE @sql_server_reset_timezone, '+00:00')
        );
    END
    ELSE
    BEGIN
        SET @expected_period_start_utc = DATEADD(
            HOUR, 12, CAST(@period_month_start AS DATETIME2(7))
        );
        SET @expected_period_end_utc = DATEADD(
            HOUR, -14, CAST(DATEADD(MONTH, 1, @period_month_start) AS DATETIME2(7))
        );
    END;
    IF @ProviderPeriodStartUtc <> @expected_period_start_utc
       OR @ProviderPeriodEndUtc <> @expected_period_end_utc
        THROW 51000, 'caller period bounds do not match the registered reset contract', 1;
    IF @server_utc < @ProviderPeriodStartUtc
        THROW 51000, 'MDD quota period is not yet applicable at SQL Server UTC', 1;
    IF @server_utc >= @ProviderPeriodEndUtc
        THROW 51000, 'MDD quota period has expired at SQL Server UTC', 1;
    IF @server_utc < DATEADD(SECOND, @activation_delay_seconds, @ProviderPeriodStartUtc)
        THROW 51000, 'MDD quota period is inside the conservative activation delay', 1;
    IF @LocalUsageIncludedThroughUtc <> @ProviderPeriodStartUtc
        THROW 51000, 'automatic rollover local cutoff must equal the verified period start', 1;

    DECLARE @existing_status VARCHAR(30);
    DECLARE @existing_model VARCHAR(40);
    DECLARE @existing_reconciliation_key NVARCHAR(240);
    DECLARE @existing_start DATETIME2(7);
    DECLARE @existing_end DATETIME2(7);
    DECLARE @existing_hash CHAR(64);
    DECLARE @existing_timezone VARCHAR(80);
    DECLARE @existing_ceiling INT;
    DECLARE @existing_normal_max INT;
    DECLARE @existing_retry_boundary INT;
    SELECT TOP (1)
        @existing_status = AccountingStatus,
        @existing_model = AccountingModel,
        @existing_reconciliation_key = ReconciliationKey,
        @existing_start = ProviderPeriodStartUtc,
        @existing_end = ProviderPeriodEndUtc,
        @existing_hash = SourceEvidenceHash,
        @existing_timezone = ProviderPeriodTimezone,
        @existing_ceiling = ProviderQuotaCeiling,
        @existing_normal_max = NormalOperationalMaximum,
        @existing_retry_boundary = RetryProtectionBoundary
    FROM ctl.MddMacSharedQuotaAccounting WITH (UPDLOCK, HOLDLOCK)
    WHERE MonthKey = @MonthKey;

    IF @existing_status IS NOT NULL
    BEGIN
        IF @existing_status = 'COMPLETE'
           AND @existing_model IN
               ('AUTOMATIC_MONTHLY_ROLLOVER', 'AUTOMATIC_MONTHLY_ROLLOVER_BOUNDED')
           AND @existing_reconciliation_key = @ReconciliationKey
           AND @existing_start = @ProviderPeriodStartUtc
           AND @existing_end = @ProviderPeriodEndUtc
           AND @existing_hash = @SourceEvidenceHash
           AND @existing_timezone = @ProviderPeriodTimezone
           AND @existing_ceiling = 250000
           AND @existing_normal_max = 210000
           AND @existing_retry_boundary = 207500
        BEGIN
            COMMIT TRANSACTION;
            RETURN;
        END;
        THROW 51000, 'a conflicting quota accounting row already exists for this provider period', 1;
    END;

    IF EXISTS
    (
        SELECT 1
        FROM ctl.MddMacSharedQuotaAccounting WITH (UPDLOCK, HOLDLOCK)
        WHERE MonthKey <> @MonthKey
          AND ProviderPeriodStartUtc IS NOT NULL
          AND ProviderPeriodEndUtc IS NOT NULL
          AND ProviderPeriodStartUtc < @ProviderPeriodEndUtc
          AND ProviderPeriodEndUtc > @ProviderPeriodStartUtc
    )
        THROW 51000, 'MDD quota period overlaps an existing accounting interval', 1;

    /*
       A worker can crash after reserving a permit and before its final
       provider-attempt row is durable.  Once the bounded stale window has
       elapsed, the only safe interpretation is a conservative provider
       attempt.  MonthKey and ReservedAtUtc are intentionally untouched.
    */
    UPDATE ctl.MddMacQuotaPermit
    SET Status = 'CONSERVATIVE_LIVE_ATTEMPT',
        FinalizedAtUtc = @server_utc,
        ReleasedAtUtc = NULL,
        FinalizationReason = 'CRASH_RECOVERY_CONSERVATIVE',
        LiveClientInvocation = 1
    WHERE Status = 'PENDING_AUTHORIZATION'
      AND ReservedAtUtc < @ProviderPeriodStartUtc
      AND ReservedAtUtc < DATEADD(SECOND, -@StaleRecoverySeconds, @server_utc);

    IF EXISTS
    (
        SELECT 1
        FROM ctl.MddMacQuotaPermit WITH (UPDLOCK, HOLDLOCK)
        WHERE ReservedAtUtc < @ProviderPeriodStartUtc
          AND Status = 'PENDING_AUTHORIZATION'
    )
    BEGIN
        /* Keep safe stale finalizations durable even when a recent permit
           correctly keeps the new period blocked. */
        COMMIT TRANSACTION;
        THROW 51000, 'a prior-period quota permit is still pending at rollover', 1;
    END;

    IF EXISTS
    (
        SELECT 1
        FROM ctl.MddMacHttpAttempt AS h WITH (UPDLOCK, HOLDLOCK)
        INNER JOIN ctl.MddMacTargetAudit AS a ON a.AuditId = h.AuditId
        WHERE a.CollectionContext = 'MAC_TIERED50_PRODUCTION'
          AND h.CompletedAtUtc < @ProviderPeriodStartUtc
          AND h.NetworkAttempted = 1
          AND h.QuotaPermitId IS NULL
    )
    BEGIN
        COMMIT TRANSACTION;
        THROW 51000, 'a prior Tiered50 network attempt has no durable quota permit', 1;
    END;

    INSERT INTO ctl.MddMacSharedQuotaAccounting
    (
        MonthKey, ProviderQuotaCeiling, NormalOperationalMaximum,
        RetryProtectionBoundary, ConfirmedProviderAttempts,
        ConservativeUnknownAttempts, ExplicitSharedConsumerReservedAttempts,
        AccountingStatus, IncludesAllSharedConsumers, BaselineAsOfUtc,
        LocalUsageIncludedThroughUtc, SourceDescription, AccountingModel,
        ProviderMeasurementCutoffUtc, StaleAfterUtc, SourceEvidenceHash,
        ReconciliationKey, ProviderPeriodTimezone, ProviderPeriodStartUtc,
        ProviderPeriodEndUtc
    )
    VALUES
    (
        @MonthKey, 250000, 210000, 207500, 0, 0, 0, 'COMPLETE', 1,
        @server_utc, @LocalUsageIncludedThroughUtc,
        N'Automatic approved MDD calendar-month rollover; no provider requests are inferred before this period',
        @accounting_model, @ProviderPeriodStartUtc,
        @ProviderPeriodEndUtc, @SourceEvidenceHash, @ReconciliationKey,
        @ProviderPeriodTimezone, @ProviderPeriodStartUtc, @ProviderPeriodEndUtc
    );

    COMMIT TRANSACTION;
END;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id = OBJECT_ID(N'ctl.MddMacQuotaPermit')
      AND name = N'CK_ctl_MddMacQuotaPermit_DecisionKind'
)
    ALTER TABLE ctl.MddMacQuotaPermit
        ADD CONSTRAINT CK_ctl_MddMacQuotaPermit_DecisionKind
            CHECK (DecisionKind IN ('INITIAL', 'RETRY'));
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id = OBJECT_ID(N'ctl.MddMacQuotaPermit')
      AND name = N'CK_ctl_MddMacQuotaPermit_Boundary'
)
    ALTER TABLE ctl.MddMacQuotaPermit
        ADD CONSTRAINT CK_ctl_MddMacQuotaPermit_Boundary
            CHECK
            (
                (DecisionKind = 'INITIAL' AND Boundary BETWEEN 1 AND 210000)
                OR (DecisionKind = 'RETRY' AND Boundary BETWEEN 1 AND 207500)
            );
GO

IF COL_LENGTH(N'ctl.MddMacSharedQuotaAccounting', N'AccountingModel') IS NULL
    ALTER TABLE ctl.MddMacSharedQuotaAccounting ADD AccountingModel VARCHAR(40) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacSharedQuotaAccounting', N'ProviderMeasurementCutoffUtc') IS NULL
    ALTER TABLE ctl.MddMacSharedQuotaAccounting ADD ProviderMeasurementCutoffUtc DATETIME2(7) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacSharedQuotaAccounting', N'StaleAfterUtc') IS NULL
    ALTER TABLE ctl.MddMacSharedQuotaAccounting ADD StaleAfterUtc DATETIME2(7) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacSharedQuotaAccounting', N'SourceEvidenceHash') IS NULL
    ALTER TABLE ctl.MddMacSharedQuotaAccounting ADD SourceEvidenceHash CHAR(64) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacSharedQuotaAccounting', N'ReconciliationKey') IS NULL
    ALTER TABLE ctl.MddMacSharedQuotaAccounting ADD ReconciliationKey NVARCHAR(240) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacSharedQuotaAccounting', N'ProviderPeriodTimezone') IS NULL
    ALTER TABLE ctl.MddMacSharedQuotaAccounting ADD ProviderPeriodTimezone VARCHAR(80) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacSharedQuotaAccounting', N'ProviderPeriodStartUtc') IS NULL
    ALTER TABLE ctl.MddMacSharedQuotaAccounting ADD ProviderPeriodStartUtc DATETIME2(7) NULL;
GO

IF COL_LENGTH(N'ctl.MddMacSharedQuotaAccounting', N'ProviderPeriodEndUtc') IS NULL
    ALTER TABLE ctl.MddMacSharedQuotaAccounting ADD ProviderPeriodEndUtc DATETIME2(7) NULL;
GO

/* COMPLETE is an authorization input, so SQL must reject rows that omit the
   provider scope, freshness, reset-period, or immutable evidence fields that
   the Python validator requires.  Existing non-COMPLETE historical rows stay
   nullable and remain unavailable to the runtime. */
IF EXISTS
(
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id = OBJECT_ID(N'ctl.MddMacSharedQuotaAccounting')
      AND name = N'CK_ctl_MddMacSharedQuotaAccounting_CompleteEvidence'
      AND
      (
          definition NOT LIKE '%AUTOMATIC_MONTHLY_ROLLOVER%'
          OR definition NOT LIKE '%AUTOMATIC_MONTHLY_ROLLOVER_BOUNDED%'
      )
)
    ALTER TABLE ctl.MddMacSharedQuotaAccounting
        DROP CONSTRAINT CK_ctl_MddMacSharedQuotaAccounting_CompleteEvidence;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id = OBJECT_ID(N'ctl.MddMacSharedQuotaAccounting')
      AND name = N'CK_ctl_MddMacSharedQuotaAccounting_CompleteEvidence'
)
    ALTER TABLE ctl.MddMacSharedQuotaAccounting
        ADD CONSTRAINT CK_ctl_MddMacSharedQuotaAccounting_CompleteEvidence
            CHECK
            (
                AccountingStatus <> 'COMPLETE'
                OR
                (
                    IncludesAllSharedConsumers = 1
                    AND AccountingModel IN
                    (
                        'EXCLUSIVE_CREDENTIAL',
                        'CENTRAL_AUTHORIZATION',
                        'REFRESHED_PROVIDER_ACCOUNTING',
                        'AUTOMATIC_MONTHLY_ROLLOVER',
                        'AUTOMATIC_MONTHLY_ROLLOVER_BOUNDED'
                    )
                    AND ProviderMeasurementCutoffUtc IS NOT NULL
                    AND StaleAfterUtc IS NOT NULL
                    AND SourceEvidenceHash IS NOT NULL
                    AND ReconciliationKey IS NOT NULL
                    AND ProviderPeriodTimezone IS NOT NULL
                    AND ProviderPeriodStartUtc IS NOT NULL
                    AND ProviderPeriodEndUtc IS NOT NULL
                    AND ProviderPeriodStartUtc < ProviderPeriodEndUtc
                    AND ProviderMeasurementCutoffUtc <= BaselineAsOfUtc
                    AND LocalUsageIncludedThroughUtc >= ProviderMeasurementCutoffUtc
                    AND StaleAfterUtc <= ProviderPeriodEndUtc
                )
            );
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id = OBJECT_ID(N'ctl.MddMacSharedQuotaAccounting')
      AND name = N'CK_ctl_MddMacSharedQuotaAccounting_CompleteTotal'
)
    ALTER TABLE ctl.MddMacSharedQuotaAccounting
        ADD CONSTRAINT CK_ctl_MddMacSharedQuotaAccounting_CompleteTotal
            CHECK
            (
                AccountingStatus <> 'COMPLETE'
                OR ConfirmedProviderAttempts
                   + ConservativeUnknownAttempts
                   + ExplicitSharedConsumerReservedAttempts <= 250000
            );
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'ctl.MddMacSharedQuotaAccounting')
      AND name = N'UX_ctl_MddMacSharedQuotaAccounting_ReconciliationKey'
)
    CREATE UNIQUE INDEX UX_ctl_MddMacSharedQuotaAccounting_ReconciliationKey
        ON ctl.MddMacSharedQuotaAccounting (ReconciliationKey)
        WHERE ReconciliationKey IS NOT NULL;
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

/*
    A resume is a lifecycle event, not a second cutover.  This append-only
    table records the actual successful enablement of the already activated
    Tiered50 panel without rewriting the original activation, outage, target,
    or sampling-era rows.  ResumeKey is the operator/request idempotency key;
    repeated attempts with the same key return the existing event.
*/

IF OBJECT_ID(N'ctl.MddMacProductionResumeEvent', N'U') IS NULL
BEGIN
    CREATE TABLE ctl.MddMacProductionResumeEvent
    (
        ResumeEventId BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_ctl_MddMacProductionResumeEvent PRIMARY KEY CLUSTERED,
        ResumeKey NVARCHAR(240) NOT NULL
            CONSTRAINT UQ_ctl_MddMacProductionResumeEvent_Key UNIQUE,
        PanelVersion NVARCHAR(100) NOT NULL,
        EraCode VARCHAR(40) NOT NULL,
        SamplingEraId BIGINT NOT NULL,
        OriginalActivationAtUtc DATETIME2(7) NOT NULL,
        ResumeAtUtc DATETIME2(7) NOT NULL,
        LaunchAgentLabel VARCHAR(160) NOT NULL,
        EvidenceJson NVARCHAR(MAX) NOT NULL,
        RecordedAtUtc DATETIME2(7) NOT NULL
            CONSTRAINT DF_ctl_MddMacProductionResumeEvent_RecordedAtUtc DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT CK_ctl_MddMacProductionResumeEvent_Status CHECK (LEN(ResumeKey) > 0),
        CONSTRAINT FK_ctl_MddMacProductionResumeEvent_Era
            FOREIGN KEY (SamplingEraId) REFERENCES ctl.MddMacSamplingEra (SamplingEraId)
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
