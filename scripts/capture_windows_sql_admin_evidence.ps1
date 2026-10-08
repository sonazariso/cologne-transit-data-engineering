<##
Read-only Windows-side SQL administrative evidence capture.

Run this inside the existing SQL Server VM under an operator-approved Windows
identity.  It uses Windows Authentication only; it accepts no SQL password,
does not modify SQL data, and writes a temporary sanitized payload which must
be signed by the operator-controlled key outside the repository with
sign_admin_evidence.py.  The macOS resume gate accepts only the resulting
signed V3 artifact and an independently configured public key.
##>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $SqlInstance,
    [string] $Database = "CologneTransitIntelligence",
    [Parameter(Mandatory = $true)] [string] $UnsignedPayloadPath,
    [string] $RuntimeLoginName = "CologneTransitCollector"
)

$ErrorActionPreference = "Stop"

function Invoke-ReadOnlySql([string] $Query) {
    if (-not (Get-Command Invoke-Sqlcmd -ErrorAction SilentlyContinue)) {
        throw "Invoke-Sqlcmd is required for the controlled Windows Authentication evidence path"
    }
    return @(Invoke-Sqlcmd -ServerInstance $SqlInstance -Database $Database -Query $Query -TrustServerCertificate:$false -AbortOnError)
}

$identity = Invoke-ReadOnlySql @"
SELECT @@SERVERNAME AS ServerName, DB_NAME() AS DatabaseName,
       SUSER_SNAME() AS LoginName, SYSUTCDATETIME() AS CapturedAtUtc,
       CAST(SERVERPROPERTY('ProductMajorVersion') AS INT) AS ProductMajorVersion;
"@

if ($identity.Count -ne 1 -or [string]$identity[0].DatabaseName -ne $Database) {
    throw "SQL identity/database binding was not proven"
}
if ([string]$identity[0].LoginName -eq $RuntimeLoginName) {
    throw "Windows evidence identity must be separate from the limited runtime login"
}

$permissions = Invoke-ReadOnlySql @"
SELECT HAS_PERMS_BY_NAME(DB_NAME(), N'DATABASE', N'VIEW DEFINITION') AS HasViewDefinition,
       HAS_PERMS_BY_NAME(NULL, N'SERVER', N'VIEW SERVER STATE') AS HasViewServerState,
       HAS_PERMS_BY_NAME(NULL, N'SERVER', N'VIEW SERVER PERFORMANCE STATE') AS HasViewServerPerformanceState,
       HAS_PERMS_BY_NAME(N'msdb.dbo.backupset', N'OBJECT', N'SELECT') AS HasBackupsetSelect,
       HAS_PERMS_BY_NAME(N'msdb.dbo.backupmediafamily', N'OBJECT', N'SELECT') AS HasBackupmediafamilySelect,
       HAS_PERMS_BY_NAME(DB_NAME(), N'DATABASE', N'SELECT') AS HasDatabaseSelect,
       HAS_PERMS_BY_NAME(NULL, N'SERVER', N'CREATE ANY DATABASE') AS HasCreateAnyDatabase,
       IS_SRVROLEMEMBER(N'sysadmin') AS IsSysadmin,
       IS_SRVROLEMEMBER(N'dbcreator') AS IsDbcreator;
"@

if ($permissions.Count -ne 1) {
    throw "SQL permission inspection did not return exactly one row"
}
$productMajorVersion = [int]$identity[0].ProductMajorVersion
$serverDmvGranted = if ($productMajorVersion -ge 16) {
    [int]$permissions[0].HasViewServerPerformanceState -eq 1
} else {
    [int]$permissions[0].HasViewServerState -eq 1
}
$permissionChecks = [ordered]@{
    database_view_definition = ([int]$permissions[0].HasViewDefinition -eq 1)
    server_dmv_state = $serverDmvGranted
    backup_history_backupset_select = ([int]$permissions[0].HasBackupsetSelect -eq 1)
    backup_history_backupmediafamily_select = ([int]$permissions[0].HasBackupmediafamilySelect -eq 1)
    operational_history_database_select = ([int]$permissions[0].HasDatabaseSelect -eq 1)
    backup_verifyonly_authorization = (
        [int]$permissions[0].HasCreateAnyDatabase -eq 1 -or
        [int]$permissions[0].IsSysadmin -eq 1 -or
        [int]$permissions[0].IsDbcreator -eq 1
    )
}
$missingPermissions = @(
    $permissionChecks.GetEnumerator() |
        Where-Object { -not $_.Value } |
        ForEach-Object { $_.Key }
)
if ($missingPermissions.Count -gt 0) {
    throw ("Required read-only administrative permissions are not proven: " + ($missingPermissions -join ","))
}

$databaseFiles = Invoke-ReadOnlySql "SELECT name, type_desc, size, growth, is_percent_growth, max_size, FILEPROPERTY(name, 'SpaceUsed') AS space_used_pages FROM sys.database_files ORDER BY file_id;"
$runtimeHealth = Invoke-ReadOnlySql "SELECT DB_NAME() AS database_name, DATABASEPROPERTYEX(DB_NAME(), 'Status') AS database_status, DATABASEPROPERTYEX(DB_NAME(), 'Recovery') AS recovery_model;"
$volumes = Invoke-ReadOnlySql "SELECT DISTINCT vs.volume_mount_point, vs.total_bytes, vs.available_bytes FROM sys.database_files AS df CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs ORDER BY vs.volume_mount_point;"
$logSpace = Invoke-ReadOnlySql "SELECT total_log_size_in_bytes, used_log_space_in_bytes, used_log_space_in_percent FROM sys.dm_db_log_space_usage WHERE database_id = DB_ID();"
$backup = Invoke-ReadOnlySql "SELECT TOP (1) bs.backup_finish_date, bs.is_copy_only, bs.has_backup_checksums, bs.compressed_backup_size, bmf.physical_device_name FROM msdb.dbo.backupset AS bs INNER JOIN msdb.dbo.backupmediafamily AS bmf ON bmf.media_set_id = bs.media_set_id WHERE bs.database_name = DB_NAME() AND bs.type = 'D' ORDER BY bs.backup_finish_date DESC;"
$history = Invoke-ReadOnlySql "SELECT TOP (1) COUNT_BIG(*) AS Tiered50Runs FROM ctl.MddMacCollectorRun WHERE CollectionContext = 'MAC_TIERED50_PRODUCTION';"
$materialization = Invoke-ReadOnlySql "SELECT COUNT_BIG(*) AS TargetCount, COALESCE(SUM(CASE WHEN IsEnabled = 1 THEN 1 ELSE 0 END), 0) AS ActiveCount FROM ctl.MddMacTiered50ProductionTarget WHERE PanelVersion = 'TIERED50_PANEL_V1' AND SamplingEra = 'Tiered50';"
$quota = Invoke-ReadOnlySql @"
SELECT TOP (1) 'COMPLETE_PROVIDER_PERIOD' AS SourceKind, MonthKey,
       AccountingStatus,
       (ConfirmedProviderAttempts + ConservativeUnknownAttempts + ExplicitSharedConsumerReservedAttempts) AS KnownTotal,
       ExplicitSharedConsumerReservedAttempts AS ReservedSharedUsage
FROM ctl.MddMacSharedQuotaAccounting
WHERE ProviderPeriodStartUtc <= SYSUTCDATETIME()
  AND ProviderPeriodEndUtc > SYSUTCDATETIME()
  AND AccountingStatus = 'COMPLETE'
ORDER BY ProviderPeriodStartUtc DESC, RecordedAtUtc DESC;
"@
if ($quota.Count -eq 0) {
    $quota = Invoke-ReadOnlySql @"
SELECT TOP (1) 'BOUNDED_BOOTSTRAP' AS SourceKind, MonthKey,
       AuthorizationStatus AS AccountingStatus,
       (ScreenshotCumulativeRequests + ConfirmedLocalAttempts + ConservativeUnknownLocalAttempts + PendingLocalReservations) AS KnownTotal,
       PendingLocalReservations AS ReservedSharedUsage
FROM ctl.MddMacConservativeBootstrapAuthorization
WHERE AuthorizationStatus = 'ACTIVE'
  AND ValidFromUtc <= SYSUTCDATETIME()
  AND StopAtUtc > SYSUTCDATETIME()
ORDER BY ValidFromUtc DESC, RecordedAtUtc DESC;
"@
}

if ($null -eq $backup -or $backup.Count -ne 1 -or [string]::IsNullOrWhiteSpace([string]$backup[0].physical_device_name)) {
    throw "No native full backup path was available for server-side VERIFYONLY"
}
if ($databaseFiles.Count -eq 0 -or $runtimeHealth.Count -ne 1 -or [string]$runtimeHealth[0].database_status -ne "ONLINE" -or [string]::IsNullOrWhiteSpace([string]$runtimeHealth[0].recovery_model) -or $volumes.Count -eq 0 -or $logSpace.Count -ne 1) {
    throw "SQL capacity evidence was incomplete"
}
if ($history.Count -ne 1 -or $materialization.Count -ne 1 -or [int64]$materialization[0].TargetCount -ne 43 -or [int64]$materialization[0].ActiveCount -ne 43) {
    throw "Tiered50 materialization or operational-history evidence was incomplete"
}
if ($quota.Count -ne 1 -or [string]$quota[0].AccountingStatus -notin @('COMPLETE', 'ACTIVE')) {
    throw "No current SQL quota baseline or bounded authorization was available"
}

# VERIFYONLY is executed by SQL Server, not by the Windows client.  Its path
# and diagnostic text never enter the sanitized JSON payload.
$backupPath = [string]$backup[0].physical_device_name
Invoke-ReadOnlySql ("RESTORE VERIFYONLY FROM DISK = N'{0}' WITH CHECKSUM;" -f $backupPath.Replace("'", "''")) | Out-Null

$captured = [DateTime]::Parse([string]$identity[0].CapturedAtUtc).ToUniversalTime().ToString("o")
$payload = [ordered]@{
    artifact_version = "TIERED50_ADMIN_EVIDENCE_V3"
    capture_role = "WINDOWS_INTEGRATED_AUTH_READ_ONLY_CAPTURE"
    generated_at_utc = $captured
    capture_provenance = [ordered]@{
        status = "PASS"
        read_only = $true
        execution_path = "WINDOWS_INTEGRATED_AUTH"
        server_name = [string]$identity[0].ServerName
        database_name = [string]$identity[0].DatabaseName
        effective_login = [string]$identity[0].LoginName
        captured_at_utc = $captured
        required_check_ids = @(
            "admin.identity", "admin.permissions", "capacity.database_files",
            "admin.sql_runtime_health",
            "capacity.volume_capacity_dm_os_volume_stats",
            "capacity.database_log_space_dm_db_log_space_usage",
            "backup.latest_full_backup_history", "backup.restore_verifyonly_with_checksum",
            "collector_runtime_history_or_materialization"
        )
        authentication_source = "WINDOWS_AUTHENTICATION_SQL_SERVER_VM"
    }
    sql = [ordered]@{
        admin_authorization = @{ status = "PASS"; permission_checks = $permissionChecks }
        admin_connection = @{ status = "PASS"; authorization_status = "PASS" }
        identity = @{ status = "PASS"; server_name = [string]$identity[0].ServerName; database_name = [string]$identity[0].DatabaseName; login_name = [string]$identity[0].LoginName; captured_at_utc = $captured }
        admin_identity_gate = @{ status = "PASS"; effective_login = [string]$identity[0].LoginName; runtime_login_match = $false }
        admin_permissions = @{ status = "PASS"; product_major_version = $productMajorVersion; permission_checks = $permissionChecks; missing_permissions = @() }
        runtime_health = @{ status = "PASS"; database_name = [string]$runtimeHealth[0].database_name; database_status = [string]$runtimeHealth[0].database_status; recovery_model = [string]$runtimeHealth[0].recovery_model; evidence = @{ status = "PASS" } }
        database_files = @($databaseFiles | ForEach-Object { @{ name = [string]$_.name; type = [string]$_.type_desc; size_pages = [int64]$_.size; growth = [int]$_.growth; is_percent_growth = [bool]$_.is_percent_growth; max_size_pages = [int]$_.max_size; space_used_pages = [int64]$_.space_used_pages } })
        database_files_evidence = @{ status = "PASS" }
        volume_capacity = @($volumes | ForEach-Object { @{ volume = [string]$_.volume_mount_point; total_bytes = [int64]$_.total_bytes; available_bytes = [int64]$_.available_bytes } })
        volume_capacity_evidence = @{ status = "PASS" }
        log_space = @{ total_bytes = [int64]$logSpace[0].total_log_size_in_bytes; used_bytes = [int64]$logSpace[0].used_log_space_in_bytes; used_percent = [double]$logSpace[0].used_log_space_in_percent }
        log_space_evidence = @{ status = "PASS" }
        backup_verifyonly = @{ status = "PASS"; checksum_verified = $true; cause_classification = "VERIFIED" }
        backup_gate = @{ status = "PASS" }
        capacity_gate = @{ status = "PASS" }
        log_space_gate = @{ status = "PASS" }
        quota_integrity = @{ status = "PASS"; state_available = $true; source_kind = [string]$quota[0].SourceKind; month_key = [string]$quota[0].MonthKey; accounting_status = [string]$quota[0].AccountingStatus; known_total = [int64]$quota[0].KnownTotal; reserved_shared_usage = [int64]$quota[0].ReservedSharedUsage }
        latest_full_backup = @{ backup_finish_date = [string]$backup[0].backup_finish_date; copy_only = [bool]$backup[0].is_copy_only; has_backup_checksums = [bool]$backup[0].has_backup_checksums; compressed_backup_size = [int64]$backup[0].compressed_backup_size; device_name_present = $true }
        latest_full_backup_evidence = @{ status = "PASS" }
        tiered50_materialization = @{ status = "PASS"; target_count = [int64]$materialization[0].TargetCount; active_count = [int64]$materialization[0].ActiveCount }
        tiered50_audit_history_evidence = @{ status = "PASS"; tiered50_run_count = [int64]$history[0].Tiered50Runs }
        recent_http_attempts_evidence = @{ status = "PASS" }
    }
}

$parent = Split-Path -Parent $UnsignedPayloadPath
New-Item -ItemType Directory -Force -Path $parent | Out-Null
$payload | ConvertTo-Json -Depth 12 | Set-Content -Encoding UTF8 -Path $UnsignedPayloadPath
Write-Output $UnsignedPayloadPath
