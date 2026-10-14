/*
    CURRENT VERIFIED OFF-C RECOVERY POINT

    This SSMS batch is deliberately fail-closed.  It is the first manual
    production operation in the S144 continuation: it creates one COPY_ONLY,
    COMPRESSION, CHECKSUM full backup on the already-approved SQL-visible UNC
    destination and verifies that backup with RESTORE VERIFYONLY.

    The source-controlled default does not execute a backup.  In the reviewed
    SSMS working copy, change only @ApplyBackup from 0 to 1 after the operator
    has confirmed the prerequisites in the runbook.  Do not substitute a Mac
    filesystem path and do not reuse an existing filename.
*/

USE CologneTransitIntelligence;
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
SET LOCK_TIMEOUT 5000;

DECLARE @ApplyBackup BIT = 0;
DECLARE @ExpectedServer sysname = N'DataAnalyst-VM';
DECLARE @ExpectedLogin nvarchar(256) = N'DATAANALYST-VM\Somaye';
DECLARE @BackupRoot nvarchar(4000) =
    N'\\vmware-host\Shared Folders\CologneTransitData';
DECLARE @StartedAt datetime2(7);
DECLARE @BackupPath nvarchar(4000);
DECLARE @CFreeBytes bigint;
DECLARE @FileExists TABLE
(
    FileExists int NULL,
    IsDirectory int NULL,
    ParentDirectoryExists int NULL
);

IF COALESCE(DB_NAME(), N'') <> N'CologneTransitIntelligence'
    THROW 51400, 'Wrong database context.', 1;
IF COALESCE(CAST(SERVERPROPERTY('ServerName') AS nvarchar(128)), N'') <> @ExpectedServer
    THROW 51401, 'The SSMS session is not connected to DataAnalyst-VM.', 1;
IF COALESCE(SUSER_SNAME(), N'') <> @ExpectedLogin
    THROW 51402, 'The SSMS session is not using DATAANALYST-VM\Somaye.', 1;
IF COALESCE(IS_SRVROLEMEMBER(N'sysadmin'), 0) <> 1
    THROW 51403, 'The Windows login is not sysadmin for this backup operation.', 1;
IF COALESCE(CONVERT(nvarchar(60), DATABASEPROPERTYEX(DB_NAME(), 'Status')), N'') <> N'ONLINE'
    THROW 51404, 'The target database is not ONLINE.', 1;
IF COALESCE(CONVERT(nvarchar(60), DATABASEPROPERTYEX(DB_NAME(), 'UserAccess')), N'') <> N'MULTI_USER'
    THROW 51405, 'The target database is not MULTI_USER.', 1;
IF OBJECT_ID(N'ctl.MddMacCollectorRun', N'U') IS NULL
   OR OBJECT_ID(N'ctl.MddMacProductionLease', N'U') IS NULL
    THROW 51406, 'Required collector control tables are missing.', 1;
IF EXISTS
(
    SELECT 1
    FROM ctl.MddMacCollectorRun
    WHERE Status IN ('RUNNING', 'CLAIMED')
      AND CollectionContext IN ('MAC_LEGACY7_PRODUCTION', 'MAC_TIERED50_PRODUCTION')
)
    THROW 51407, 'A production collector run is active.', 1;
IF EXISTS
(
    SELECT 1
    FROM ctl.MddMacProductionLease
    WHERE LeaseName = 'MDD_MAC_PRODUCTION'
      AND Status = 'ACTIVE'
)
    THROW 51408, 'The production lease is active.', 1;
IF EXISTS
(
    SELECT 1
    FROM sys.dm_tran_session_transactions AS st
    INNER JOIN sys.dm_exec_sessions AS es
        ON es.session_id = st.session_id
    WHERE st.session_id <> @@SPID
      AND es.is_user_process = 1
)
    THROW 51409, 'Another user transaction is active.', 1;

SELECT @CFreeBytes = MIN(CONVERT(bigint, vs.available_bytes))
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
WHERE LOWER(LEFT(vs.volume_mount_point, 2)) = N'c:';
IF @CFreeBytes IS NULL
    THROW 51410, 'SQL Server could not measure Windows C: free space.', 1;
IF @CFreeBytes < CONVERT(bigint, 12.000 * 1073741824.0)
    THROW 51411, 'Windows C: free space is below the 12 GiB operational floor.', 1;

SET @StartedAt = SYSDATETIME();
SET @BackupPath = @BackupRoot
    + N'\CologneTransitIntelligence-copy-only-'
    + REPLACE(REPLACE(REPLACE(REPLACE(
          CONVERT(nvarchar(30), @StartedAt, 126), N':', N''),
          N'T', N'-'), N'.', N''), N' ', N'')
    + N'.bak';

IF @BackupRoot <> N'\\vmware-host\Shared Folders\CologneTransitData'
    THROW 51412, 'The approved SQL-visible backup root was changed.', 1;
IF @BackupPath NOT LIKE @BackupRoot + N'\%'
    THROW 51413, 'The generated backup path is outside the approved root.', 1;

INSERT INTO @FileExists (FileExists, IsDirectory, ParentDirectoryExists)
EXEC master.dbo.xp_fileexist @BackupPath;
IF NOT EXISTS
(
    SELECT 1
    FROM @FileExists
    WHERE FileExists IS NOT NULL
      AND IsDirectory IS NOT NULL
      AND ParentDirectoryExists IS NOT NULL
)
    THROW 51414, 'SQL Server could not probe the backup destination.', 1;
IF EXISTS (SELECT 1 FROM @FileExists WHERE FileExists = 1)
    THROW 51415, 'The generated backup filename already exists; do not append to an existing file.', 1;
IF NOT EXISTS (SELECT 1 FROM @FileExists WHERE ParentDirectoryExists = 1)
    THROW 51416, 'The SQL Server service cannot see the approved backup directory.', 1;

SELECT DB_NAME() AS database_name,
       CAST(SERVERPROPERTY('ServerName') AS nvarchar(128)) AS server_name,
       SUSER_SNAME() AS effective_login,
       DATABASEPROPERTYEX(DB_NAME(), 'Status') AS database_status,
       DATABASEPROPERTYEX(DB_NAME(), 'UserAccess') AS user_access,
       @BackupPath AS sql_visible_backup_path,
       @CFreeBytes / 1073741824.0 AS windows_c_free_gib,
       N'PREFLIGHT_PASS' AS preflight_status;

IF @ApplyBackup <> 1
BEGIN
    PRINT 'BACKUP NOT APPLIED: @ApplyBackup is 0.';
    RETURN;
END;

BACKUP DATABASE [CologneTransitIntelligence]
    TO DISK = @BackupPath
    WITH COPY_ONLY, COMPRESSION, CHECKSUM, STATS = 5;

DECLARE @Fresh TABLE
(
    backup_set_id int NOT NULL,
    media_set_id int NOT NULL,
    backup_position int NOT NULL,
    database_name sysname NOT NULL,
    backup_start_date datetime NOT NULL,
    backup_finish_date datetime NOT NULL,
    copy_only bit NOT NULL,
    checksum_enabled bit NOT NULL,
    compressed_backup_size bigint NOT NULL,
    media_count bigint NOT NULL
);

INSERT @Fresh
SELECT DISTINCT
    bs.backup_set_id,
    bs.media_set_id,
    bs.position,
    bs.database_name,
    bs.backup_start_date,
    bs.backup_finish_date,
    bs.is_copy_only,
    bs.has_backup_checksums,
    bs.compressed_backup_size,
    (
        SELECT COUNT_BIG(*)
        FROM msdb.dbo.backupmediafamily AS bmf_count
        WHERE bmf_count.media_set_id = bs.media_set_id
    )
FROM msdb.dbo.backupset AS bs
INNER JOIN msdb.dbo.backupmediafamily AS bmf
    ON bmf.media_set_id = bs.media_set_id
WHERE bs.database_name = N'CologneTransitIntelligence'
  AND bs.type = 'D'
  AND bs.is_copy_only = 1
  AND bs.has_backup_checksums = 1
  AND bs.backup_finish_date >= @StartedAt
  AND bmf.device_type = 2
  AND bmf.physical_device_name = @BackupPath;

IF (SELECT COUNT(*) FROM @Fresh) <> 1
    THROW 51417, 'Exactly one fresh COPY_ONLY/checksum full backup was not identified.', 1;
IF EXISTS
(
    SELECT 1
    FROM @Fresh
    WHERE database_name <> N'CologneTransitIntelligence'
       OR copy_only <> 1
       OR checksum_enabled <> 1
       OR compressed_backup_size <= 0
       OR media_count <> 1
)
    THROW 51418, 'Fresh backup metadata failed the COPY_ONLY/compression/checksum/single-media gate.', 1;

DECLARE @FreshMediaSetId int = (SELECT media_set_id FROM @Fresh);
IF EXISTS
(
    SELECT 1
    FROM msdb.dbo.backupmediafamily
    WHERE media_set_id = @FreshMediaSetId
      AND (device_type <> 2 OR physical_device_name <> @BackupPath)
)
    THROW 51419, 'Fresh backup media does not exactly match the approved SQL-visible path.', 1;

DECLARE @BackupPosition int = (SELECT backup_position FROM @Fresh);
RESTORE VERIFYONLY FROM DISK = @BackupPath
    WITH FILE = @BackupPosition, CHECKSUM;

SELECT *,
       @BackupPath AS sql_visible_backup_path,
       N'PASS' AS verifyonly_status
FROM @Fresh;

/*
    Save only the value returned by the next result set as
    fresh-backup-evidence.json when a later Python gate requires an evidence
    file.  The JSON shape is the existing _backup_gate contract.
*/
SELECT
    N'PASS' AS [status],
    JSON_QUERY
    (
        (
            SELECT N'PASS' AS [status],
                   database_name,
                   backup_set_id,
                   media_set_id,
                   backup_position,
                   backup_start_date,
                   backup_finish_date,
                   copy_only,
                   checksum_enabled AS [checksum],
                   compressed_backup_size,
                   media_count,
                   @BackupPath AS [backup_path],
                   N'PASS' AS [destination_accessibility],
                   N'PASS' AS [verifyonly]
            FROM @Fresh
            FOR JSON PATH, WITHOUT_ARRAY_WRAPPER
        )
    ) AS [backup]
FOR JSON PATH, WITHOUT_ARRAY_WRAPPER;
