/*
    COMPACT OBSERVATION GATE APPROVAL V1

    This is the explicit administrative checkpoint between a verified
    historical migration and either future compact writes or legacy retirement.
    It is a no-op by default.  The operator must set both the apply bit and the
    relevant confirmation bits after reviewing the durable migration ledger,
    exact parity evidence, and a real Power BI/warehouse readback.

    This script does not drop tables, shrink files, reset the sampling guard,
    or start the collector.
*/

USE CologneTransitIntelligence;
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
SET LOCK_TIMEOUT 5000;

DECLARE @ApplyCompactGateApproval BIT = 0;
DECLARE @PowerBICompatibilityConfirmed BIT = 0;
DECLARE @EnableFutureWrites BIT = 0;
DECLARE @ApproveLegacyRetirement BIT = 0;

IF @ApplyCompactGateApproval <> 1
BEGIN
    PRINT 'COMPACT GATE APPROVAL NOT APPLIED: @ApplyCompactGateApproval is 0.';
    RETURN;
END;

IF DB_NAME() <> N'CologneTransitIntelligence'
    THROW 51340, 'Compact gate approval must run in CologneTransitIntelligence.', 1;

IF @EnableFutureWrites = 1 AND @PowerBICompatibilityConfirmed <> 1
    THROW 51341, 'Future writes require explicit Power BI/warehouse compatibility confirmation.', 1;

IF @ApproveLegacyRetirement = 1 AND @PowerBICompatibilityConfirmed <> 1
    THROW 51349, 'Legacy retirement requires explicit Power BI/warehouse compatibility confirmation.', 1;

IF @ApproveLegacyRetirement = 1 AND @EnableFutureWrites = 1
    THROW 51342, 'Retire legacy storage before enabling future writes.', 1;

IF OBJECT_ID(N'stg.MddMacCompactStorageState', N'U') IS NULL
   OR OBJECT_ID(N'stg.MddMacCompactMigration', N'U') IS NULL
   OR OBJECT_ID(N'stg.MddMacCompactObservationEnvelope', N'U') IS NULL
    THROW 51343, 'Compact storage is not installed.', 1;

IF @PowerBICompatibilityConfirmed = 1
   AND (OBJECT_ID(N'analytics.vwMddMacCompactSourceFieldCoverage', N'V') IS NULL
        OR OBJECT_ID(N'analytics.vwMddMacCompactRecordedAtTimeHistory', N'V') IS NULL)
    THROW 51344, 'Compact analytical compatibility views are missing.', 1;

IF @EnableFutureWrites = 1
   AND NOT EXISTS
   (
       SELECT 1
       FROM stg.MddMacCompactMigration
       WHERE MigrationKind = 'HISTORICAL' AND Status = 'COMPLETE'
   )
    THROW 51345, 'Future writes require a COMPLETE historical compact migration.', 1;

IF @ApproveLegacyRetirement = 1
   AND EXISTS
   (
       SELECT 1
       FROM stg.MddMacCompactMigration
       WHERE MigrationKind = 'HISTORICAL' AND Status = 'COMPLETE'
   )
   AND EXISTS
   (
       SELECT 1
       FROM stg.MddMacCompactObservationEnvelope
       WHERE VerificationStatus <> 'VERIFIED'
   )
    THROW 51346, 'Legacy retirement requires every compact envelope to be VERIFIED.', 1;

IF @ApproveLegacyRetirement = 1
   AND NOT EXISTS
   (
       SELECT 1
       FROM stg.MddMacCompactMigration
       WHERE MigrationKind = 'HISTORICAL' AND Status = 'COMPLETE'
   )
    THROW 51348, 'Legacy retirement requires a COMPLETE historical compact migration.', 1;

BEGIN TRY
    BEGIN TRANSACTION;
    UPDATE stg.MddMacCompactStorageState
    SET CompatibilityVerified = CASE WHEN @PowerBICompatibilityConfirmed = 1 THEN 1 ELSE CompatibilityVerified END,
        HistoricalReadbackVerified = CASE WHEN @EnableFutureWrites = 1 OR @ApproveLegacyRetirement = 1 THEN 1 ELSE HistoricalReadbackVerified END,
        FutureWriteEnabled = CASE WHEN @EnableFutureWrites = 1 THEN 1 ELSE 0 END,
        LegacyRetirementApproved = CASE WHEN @ApproveLegacyRetirement = 1 THEN 1 ELSE LegacyRetirementApproved END,
        UpdatedAtUtc = SYSUTCDATETIME(),
        UpdatedBy = SUSER_SNAME()
    WHERE StateId = 1;
    IF @@ROWCOUNT <> 1
        THROW 51347, 'Compact storage state singleton is missing.', 1;
    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;

SELECT StateId, StorageFormatVersion, FutureWriteEnabled,
       CompatibilityVerified, HistoricalReadbackVerified,
       LegacyRetirementApproved, UpdatedAtUtc, UpdatedBy
FROM stg.MddMacCompactStorageState
WHERE StateId = 1;
