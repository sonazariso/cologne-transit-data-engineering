/*
STORAGE V1 - EVALUATE on an ISOLATED RESTORED SQL copy only.
Read-only toward source tables; SQL Server may use tempdb, CPU and I/O to
sample data and estimate ROW/PAGE compression. Do not run this during collection.
No rebuild, ALTER INDEX, DELETE, SHRINK or application-code change is performed.
*/
USE CologneTransitIntelligence;
GO
SET NOCOUNT ON;
SET LOCK_TIMEOUT 5000;

PRINT 'Estimate PAGE compression for clustered TimingObservation:';
EXEC sys.sp_estimate_data_compression_savings
    @schema_name = N'stg',
    @object_name = N'MddMacTimingObservation',
    @index_id = 1,
    @partition_number = NULL,
    @data_compression = N'PAGE';

PRINT 'Estimate PAGE compression for clustered CallObservation:';
EXEC sys.sp_estimate_data_compression_savings
    @schema_name = N'stg',
    @object_name = N'MddMacCallObservation',
    @index_id = 1,
    @partition_number = NULL,
    @data_compression = N'PAGE';
GO
