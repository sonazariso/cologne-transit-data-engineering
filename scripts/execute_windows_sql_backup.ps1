<##
Create and verify one current SQL Server recovery point under the existing
interactive Windows identity.

This is intentionally narrower than the storage migration: it does not change
schema, data, collector state, or the backup chain.  Invoke-Sqlcmd supplies
Windows Authentication because no SQL username or password is accepted here.
The destination must be a SQL Server-visible non-C: drive or UNC path.
##>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $SqlInstance,
    [string] $Database = "CologneTransitIntelligence",
    [Parameter(Mandatory = $true)] [string] $BackupPath,
    [double] $DestinationFreeGiB = 0,
    [double] $MinimumDestinationFreeGiB = 5.0,
    [ValidateSet("Mandatory", "Optional", "Strict")]
    [string] $EncryptMode = "Mandatory",
    [switch] $Apply,
    [string] $EvidencePath
)

$ErrorActionPreference = "Stop"
$SafetyFloorGiB = 12.0

function Initialize-SqlCmdCommand {
    $module = @(
        Get-Module -ListAvailable -Name SqlServer |
            Sort-Object Version -Descending |
            Select-Object -First 1
    )
    if ($module.Count -eq 0) {
        throw "The approved SqlServer PowerShell module is unavailable"
    }
    Import-Module -Name SqlServer -ErrorAction Stop
    $command = Get-Command Invoke-Sqlcmd -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "Invoke-Sqlcmd is unavailable from the SqlServer module"
    }
    foreach ($name in @("ServerInstance", "Database", "Query", "Encrypt", "TrustServerCertificate")) {
        if (-not $command.Parameters.ContainsKey($name)) {
            throw "Invoke-Sqlcmd is missing the encrypted Windows-authentication parameter '$name'"
        }
    }
    return $command
}

$SqlCmdCommand = Initialize-SqlCmdCommand

function Invoke-SafeSql([string] $Query) {
    $parameters = @{
        ServerInstance = $SqlInstance
        Database = $Database
        Query = $Query
        Encrypt = $EncryptMode
        TrustServerCertificate = $false
        ErrorAction = "Stop"
    }
    if ($SqlCmdCommand.Parameters.ContainsKey("AbortOnError")) {
        $parameters.AbortOnError = $true
    }
    return @(& $SqlCmdCommand @parameters)
}

function Convert-ToSqlUnicodeLiteral([string] $Value) {
    return "N'" + $Value.Replace("'", "''") + "'"
}

function Test-OffCPath([string] $Value) {
    $normalized = $Value.Trim().Replace("/", "\")
    return $normalized -match '^(\\\\|[d-zD-Z]:[\\/])'
}

function Write-JsonEvidence([object] $Value) {
    $json = $Value | ConvertTo-Json -Depth 12 -Compress
    Write-Output $json
    if (-not [string]::IsNullOrWhiteSpace($EvidencePath)) {
        $parent = Split-Path -Parent $EvidencePath
        if (-not [string]::IsNullOrWhiteSpace($parent)) {
            New-Item -ItemType Directory -Force -Path $parent | Out-Null
        }
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($EvidencePath, $json, $utf8)
    }
}

$result = [ordered]@{
    operation = "windows_integrated_sql_backup"
    mode = if ($Apply) { "APPLY" } else { "PREFLIGHT" }
    server_name = $SqlInstance
    database_name = $Database
    backup_path = $BackupPath
    destination_is_off_c = Test-OffCPath $BackupPath
    restore_verifyonly = "NOT_RUN"
    status = "BLOCKED"
}

try {
    if ($Database -ne "CologneTransitIntelligence") {
        throw "the target database must be CologneTransitIntelligence"
    }
    if (-not (Test-OffCPath $BackupPath)) {
        throw "the backup destination must be a SQL Server-visible non-C: drive or UNC path"
    }
    if ($Apply -and $DestinationFreeGiB -lt $MinimumDestinationFreeGiB) {
        throw "measured backup destination free space is below the safety threshold"
    }

    $identity = @(Invoke-SafeSql @"
SELECT DB_NAME() AS DatabaseName,
       CAST(SERVERPROPERTY('ServerName') AS NVARCHAR(128)) AS ServerName,
       DATABASEPROPERTYEX(DB_NAME(), 'Status') AS DatabaseStatus,
       DATABASEPROPERTYEX(DB_NAME(), 'UserAccess') AS UserAccess,
       SUSER_SNAME() AS EffectiveLogin;
"@)
    if ($identity.Count -ne 1) {
        throw "SQL identity query did not return exactly one row"
    }
    $result.identity = $identity[0]
    if ($identity[0].DatabaseName -ne "CologneTransitIntelligence" -or
        $identity[0].DatabaseStatus -ne "ONLINE") {
        throw "SQL identity or database ONLINE preflight failed"
    }
    if ($identity[0].UserAccess -and $identity[0].UserAccess -ne "MULTI_USER") {
        throw "the database is not in MULTI_USER mode"
    }

    $volumeRows = @(Invoke-SafeSql @"
SELECT DISTINCT vs.volume_mount_point AS Volume,
       vs.total_bytes AS TotalBytes,
       vs.available_bytes AS AvailableBytes
FROM sys.database_files AS df
CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs;
"@)
    $cVolumes = @($volumeRows | Where-Object { ([string]$_.Volume).StartsWith("C:", [System.StringComparison]::OrdinalIgnoreCase) })
    if ($cVolumes.Count -eq 0) {
        throw "SQL data files did not prove a Windows C: volume"
    }
    $windowsFreeBytes = ($cVolumes | Measure-Object -Property AvailableBytes -Minimum).Minimum
    $windowsFreeGiB = [double]$windowsFreeBytes / 1GB
    $result.windows_free_gib = $windowsFreeGiB
    $result.required_windows_free_gib = $SafetyFloorGiB
    if ($windowsFreeGiB -lt $SafetyFloorGiB) {
        throw "Windows C: free space is below the 12 GiB operational floor"
    }

    $activeRows = @(Invoke-SafeSql @"
SELECT
    (SELECT COUNT_BIG(*) FROM ctl.MddMacCollectorRun
     WHERE Status IN ('RUNNING', 'CLAIMED')
       AND CollectionContext IN ('MAC_LEGACY7_PRODUCTION', 'MAC_TIERED50_PRODUCTION')) AS ActiveRuns,
    (SELECT COUNT_BIG(*) FROM ctl.MddMacProductionLease
     WHERE LeaseName = 'MDD_MAC_PRODUCTION' AND Status = 'ACTIVE') AS ActiveLeases,
    (SELECT COUNT_BIG(*)
     FROM sys.dm_tran_session_transactions AS st
     INNER JOIN sys.dm_exec_sessions AS es ON es.session_id = st.session_id
     WHERE st.session_id <> @@SPID AND es.is_user_process = 1) AS ActiveUserTransactions;
"@)
    if ($activeRows.Count -ne 1 -or [int64]$activeRows[0].ActiveRuns -ne 0 -or
        [int64]$activeRows[0].ActiveLeases -ne 0 -or
        [int64]$activeRows[0].ActiveUserTransactions -ne 0) {
        throw "an active collector, production lease, or unexpected user transaction was detected"
    }
    $result.preflight = [ordered]@{
        status = "PASS"
        windows_free_gib = $windowsFreeGiB
        destination_free_gib = $DestinationFreeGiB
        active_runs = [int64]$activeRows[0].ActiveRuns
        active_leases = [int64]$activeRows[0].ActiveLeases
        active_user_transactions = [int64]$activeRows[0].ActiveUserTransactions
    }

    if (-not $Apply) {
        $result.status = "PASS"
        Write-JsonEvidence $result
        exit 0
    }

    $started = @(Invoke-SafeSql "SELECT SYSDATETIME() AS BackupStartedAt;")
    if ($started.Count -ne 1 -or $null -eq $started[0].BackupStartedAt) {
        throw "SQL Server did not return a backup start timestamp"
    }
    $startedText = ([datetime]$started[0].BackupStartedAt).ToString("yyyy-MM-ddTHH:mm:ss.fffffff", [System.Globalization.CultureInfo]::InvariantCulture)
    $pathLiteral = Convert-ToSqlUnicodeLiteral $BackupPath
    Invoke-SafeSql ("BACKUP DATABASE [CologneTransitIntelligence] TO DISK = {0} WITH COPY_ONLY, COMPRESSION, CHECKSUM, STATS = 5;" -f $pathLiteral) | Out-Null

    $startLiteral = Convert-ToSqlUnicodeLiteral $startedText
    $metadata = @(Invoke-SafeSql @"
SELECT TOP (1)
       bs.backup_set_id AS BackupSetId,
       bs.media_set_id AS MediaSetId,
       bs.backup_start_date AS BackupStartDate,
       bs.backup_finish_date AS BackupFinishDate,
       bs.is_copy_only AS CopyOnly,
       bs.has_backup_checksums AS HasBackupChecksums,
       bs.compressed_backup_size AS CompressedBackupSize,
       bs.position AS BackupPosition
FROM msdb.dbo.backupset AS bs
INNER JOIN msdb.dbo.backupmediafamily AS bmf ON bmf.media_set_id = bs.media_set_id
WHERE bs.database_name = DB_NAME()
  AND bs.type = 'D'
  AND bs.is_copy_only = 1
  AND bs.has_backup_checksums = 1
  AND bs.backup_finish_date >= CONVERT(datetime2(7), $startLiteral)
  AND bmf.physical_device_name = $pathLiteral
ORDER BY bs.backup_finish_date DESC, bs.backup_set_id DESC;
"@)
    if ($metadata.Count -ne 1) {
        throw "fresh COPY_ONLY full-backup metadata was not uniquely identified"
    }
    $media = @(Invoke-SafeSql ("SELECT family_sequence_number AS FamilySequenceNumber, physical_device_name AS PhysicalDeviceName, device_type AS DeviceType FROM msdb.dbo.backupmediafamily WHERE media_set_id = {0} ORDER BY family_sequence_number;" -f [int64]$metadata[0].MediaSetId))
    if ($media.Count -lt 1 -or @($media | Where-Object { [int]$_.DeviceType -ne 2 }).Count -ne 0) {
        throw "fresh backup media is missing or is not disk media"
    }
    if (@($media | Where-Object { [string]$_.PhysicalDeviceName -ne $BackupPath }).Count -ne 0) {
        throw "fresh backup media does not match the requested SQL-visible path"
    }
    if ([int64]$metadata[0].CompressedBackupSize -le 0 -or [int]$metadata[0].BackupPosition -le 0) {
        throw "fresh backup metadata has no compressed payload or valid backup position"
    }

    Invoke-SafeSql ("DECLARE @BackupPath NVARCHAR(4000) = {0}; DECLARE @BackupPosition INT = {1}; RESTORE VERIFYONLY FROM DISK = @BackupPath WITH FILE = @BackupPosition, CHECKSUM;" -f $pathLiteral, [int]$metadata[0].BackupPosition) | Out-Null
    $result.restore_verifyonly = "PASS"
    $result.backup = [ordered]@{
        status = "PASS"
        backup_set_id = [int64]$metadata[0].BackupSetId
        media_set_id = [int64]$metadata[0].MediaSetId
        backup_position = [int]$metadata[0].BackupPosition
        backup_start_date = $metadata[0].BackupStartDate
        backup_finish_date = $metadata[0].BackupFinishDate
        copy_only = [bool]$metadata[0].CopyOnly
        checksum = [bool]$metadata[0].HasBackupChecksums
        compressed_backup_size = [int64]$metadata[0].CompressedBackupSize
        media_count = $media.Count
        backup_path = $BackupPath
        destination_accessibility = "PASS"
        verifyonly = "PASS"
    }
    $result.status = "PASS"
    Write-JsonEvidence $result
    exit 0
} catch {
    $reason = [string]$_.Exception.Message
    if (-not [string]::IsNullOrWhiteSpace($BackupPath)) {
        $reason = $reason.Replace($BackupPath, "<backup path redacted>")
    }
    $result.error_type = $_.Exception.GetType().Name
    $result.error = $reason.Substring(0, [Math]::Min(800, $reason.Length))
    Write-JsonEvidence $result
    exit 1
}
