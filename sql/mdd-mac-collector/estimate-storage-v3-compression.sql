/*
STORAGE V3 LAB-ONLY COMPRESSION MEASUREMENT.

This script is read-only against the lab database but can consume substantial
CPU, I/O, tempdb, and time.  It never rebuilds an index.  Run only against a
small restored/sanitized database whose name ends in _STORAGE_V3_LAB.  The
default guard intentionally refuses both production and an unlabelled copy.
Set @RunLab to 1 only after the isolated lab has been provisioned.
*/
USE CologneTransitIntelligence_STORAGE_V3_LAB;
GO
SET NOCOUNT ON;
SET LOCK_TIMEOUT 5000;

DECLARE @RunLab BIT = 0;
IF @RunLab <> 1 OR DB_NAME() NOT LIKE N'%_STORAGE_V3_LAB'
    THROW 51310, 'This compression estimate is lab-only and disabled by default.', 1;

PRINT 'ROW compression estimates:';
EXEC sys.sp_estimate_data_compression_savings
    @schema_name = N'stg', @object_name = N'MddMacSourceSnapshot',
    @index_id = NULL, @partition_number = NULL, @data_compression = N'ROW';
EXEC sys.sp_estimate_data_compression_savings
    @schema_name = N'stg', @object_name = N'MddMacCallObservation',
    @index_id = NULL, @partition_number = NULL, @data_compression = N'ROW';
EXEC sys.sp_estimate_data_compression_savings
    @schema_name = N'stg', @object_name = N'MddMacTimingObservation',
    @index_id = NULL, @partition_number = NULL, @data_compression = N'ROW';

PRINT 'PAGE compression estimates:';
EXEC sys.sp_estimate_data_compression_savings
    @schema_name = N'stg', @object_name = N'MddMacSourceSnapshot',
    @index_id = NULL, @partition_number = NULL, @data_compression = N'PAGE';
EXEC sys.sp_estimate_data_compression_savings
    @schema_name = N'stg', @object_name = N'MddMacCallObservation',
    @index_id = NULL, @partition_number = NULL, @data_compression = N'PAGE';
EXEC sys.sp_estimate_data_compression_savings
    @schema_name = N'stg', @object_name = N'MddMacTimingObservation',
    @index_id = NULL, @partition_number = NULL, @data_compression = N'PAGE';
GO
