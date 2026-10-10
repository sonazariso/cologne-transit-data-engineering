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
    [string] $RuntimeLoginName = "CologneTransitCollector",
    [ValidateSet("Mandatory", "Optional", "Strict")]
    [string] $EncryptMode = "Mandatory"
)

$ErrorActionPreference = "Stop"
$MinimumFreePercent = 5.0
$MinimumFreeReserveBytes = [int64]1GB

function Initialize-SqlCmdCommand {
    $availableModule = @(
        Get-Module -ListAvailable -Name SqlServer |
            Sort-Object Version -Descending |
            Select-Object -First 1
    )
    if ($availableModule.Count -eq 0) {
        throw "The SqlServer PowerShell module is unavailable. Install only the approved local prerequisite with: Install-Module -Name SqlServer -Scope CurrentUser -Repository PSGallery"
    }
    try {
        Import-Module -Name SqlServer -ErrorAction Stop
    } catch {
        throw "The SqlServer PowerShell module could not be imported; no SQL evidence was written"
    }
    $command = Get-Command Invoke-Sqlcmd -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "Invoke-Sqlcmd is unavailable from the installed SqlServer PowerShell module; no SQL evidence was written"
    }
    $requiredParameters = @("ServerInstance", "Database", "Query", "Encrypt", "TrustServerCertificate")
    $missingParameters = @(
        $requiredParameters | Where-Object { -not $command.Parameters.ContainsKey($_) }
    )
    if ($missingParameters.Count -gt 0) {
        throw (
            "Invoke-Sqlcmd is incompatible with the controlled encrypted evidence path; missing parameter(s): " +
            ($missingParameters -join ", ") +
            ". Update only the SqlServer module for the current Windows identity with: Install-Module -Name SqlServer -Scope CurrentUser -Repository PSGallery"
        )
    }
    return $command
}

$SqlCmdCommand = Initialize-SqlCmdCommand

function Invoke-ReadOnlySql([string] $Query) {
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

function Normalize-VolumePath([string] $Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return ""
    }
    $normalized = $Value.Trim().Replace("/", "\")
    while ($normalized.EndsWith("\")) {
        $normalized = $normalized.Substring(0, $normalized.Length - 1)
    }
    return $normalized + "\"
}

function Test-PathOnVolume([string] $Path, [string] $VolumeMountPoint) {
    $normalizedPath = Normalize-VolumePath $Path
    $normalizedVolume = Normalize-VolumePath $VolumeMountPoint
    if ([string]::IsNullOrWhiteSpace($normalizedPath) -or [string]::IsNullOrWhiteSpace($normalizedVolume)) {
        return $false
    }
    return $normalizedPath.StartsWith($normalizedVolume, [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-SameVolume([string] $Left, [string] $Right) {
    $normalizedLeft = Normalize-VolumePath $Left
    $normalizedRight = Normalize-VolumePath $Right
    return $normalizedLeft.Equals($normalizedRight, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-PermissionState($Value) {
    if ($null -eq $Value) {
        return "UNKNOWN"
    }
    if ([int]$Value -eq 1) {
        return "GRANTED"
    }
    if ([int]$Value -eq 0) {
        return "DENIED"
    }
    return "UNKNOWN"
}

function Convert-SqlUtcDateTimeToIso8601([object] $Value) {
    <#
    SYSUTCDATETIME() returns SQL datetime2, which has no timezone metadata.
    Its clock fields are already UTC.  Assign UTC to those fields explicitly;
    never route an unspecified DateTime through the guest's local timezone.
    #>
    if ($null -eq $Value) {
        throw "SQL UTC timestamp was null"
    }
    if ($Value -is [DateTimeOffset]) {
        $utcDateTime = [DateTime]::SpecifyKind($Value.DateTime, [DateTimeKind]::Utc)
        $utcValue = New-Object -TypeName System.DateTimeOffset -ArgumentList $utcDateTime
    } elseif ($Value -is [DateTime]) {
        $utcDateTime = [DateTime]::SpecifyKind([DateTime]$Value, [DateTimeKind]::Utc)
        $utcValue = New-Object -TypeName System.DateTimeOffset -ArgumentList $utcDateTime
    } else {
        $parsedDateTime = [DateTime]::Parse(
            [string]$Value,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::AllowWhiteSpaces
        )
        $utcDateTime = [DateTime]::SpecifyKind($parsedDateTime, [DateTimeKind]::Utc)
        $utcValue = New-Object -TypeName System.DateTimeOffset -ArgumentList $utcDateTime
    }
    return $utcValue.ToString("yyyy-MM-dd'T'HH:mm:ss.fffffff'Z'", [System.Globalization.CultureInfo]::InvariantCulture)
}

function Convert-NullableSqlUtcDateTimeToIso8601([object] $Value) {
    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) {
        return $null
    }
    return Convert-SqlUtcDateTimeToIso8601 $Value
}

function Get-SanitizedSqlError([object] $ErrorRecord) {
    $reason = if ($null -ne $ErrorRecord.Exception) {
        [string]$ErrorRecord.Exception.Message
    } else {
        [string]$ErrorRecord
    }
    $reason = [regex]::Replace($reason, '(?i)(password|secret|token|apikey|credential)\s*[=:]\s*[^,;\s]+', '$1=<redacted>')
    $reason = [regex]::Replace($reason, '(?i)(?:[A-Za-z]:[\\/]|/)[^\s'']+', '<path redacted>')
    if ($reason.Length -gt 500) {
        return $reason.Substring(0, 500)
    }
    return $reason
}

function New-NotExecutedAuditQuery([string] $StatementId, [string] $Reason) {
    return [pscustomobject]@{
        Evidence = [ordered]@{
            statement_id = $StatementId
            status = "NOT_PROVEN"
            executed = $false
            result_count = 0
            captured_at_utc = $captured
            reason = $Reason
        }
        Rows = @()
    }
}

function Invoke-AuditedReadOnlySql([string] $StatementId, [string] $Query) {
    $evidence = [ordered]@{
        statement_id = $StatementId
        status = "NOT_PROVEN"
        executed = $false
        result_count = 0
        captured_at_utc = $captured
    }
    try {
        $rows = @(Invoke-ReadOnlySql $Query)
        $requiredColumns = switch ($StatementId) {
            "collector.production_cutover_history" { @("CutoverKey", "PanelVersion", "Tiered50ActivationAtUtc", "Tiered50ActivationStatus", "RecordedAtUtc"); break }
            "collector.historical_runs" { @("MacCollectorRunId", "RunId", "LogicalDueBucket", "CollectionContext", "StartedAtUtc", "CompletedAtUtc", "Status"); break }
            "collector.historical_target_audits" { @("AuditId", "RunId", "TargetIdentity", "LogicalDueBucket", "CollectionContext", "Status", "HttpAttempts", "StartedAtUtc", "CompletedAtUtc"); break }
            "collector.http_attempt_history" { @("HttpAttemptId", "AuditId", "AttemptNumber", "StartedAtUtc", "CompletedAtUtc", "HttpStatus", "Outcome", "NetworkAttempted", "HttpResponseReceived", "ProviderQuotaStatus", "RunId", "TargetIdentity", "LogicalDueBucket", "CollectionContext"); break }
            "collector.tiered50_first_heartbeat" { @("MacCollectorRunId", "RunId", "LogicalDueBucket", "StartedAtUtc", "CompletedAtUtc", "Status"); break }
            "collector.tiered50_first_heartbeat_target_audits" { @("AuditId", "RunId", "TargetIdentity", "LogicalDueBucket", "CollectionContext", "Status", "HttpAttempts", "StartedAtUtc", "CompletedAtUtc"); break }
            "collector.tiered50_first_heartbeat_http_attempts" { @("HttpAttemptId", "AuditId", "AttemptNumber", "StartedAtUtc", "CompletedAtUtc", "HttpStatus", "Outcome", "NetworkAttempted", "HttpResponseReceived", "ProviderQuotaStatus", "RunId", "TargetIdentity", "LogicalDueBucket", "CollectionContext"); break }
            "collector.historical_outage" { @("CutoverId", "CutoverKey", "PanelVersion", "Tiered50ActivationAtUtc", "Tiered50ActivationStatus", "RecordedAtUtc"); break }
            "collector.tiered50_later_runs" { @("MacCollectorRunId", "RunId", "LogicalDueBucket", "StartedAtUtc", "CompletedAtUtc", "Status"); break }
            default { @() }
        }
        foreach ($row in $rows) {
            foreach ($column in $requiredColumns) {
                if ($null -eq $row.PSObject.Properties[$column]) {
                    throw ("query returned a row without expected column " + $column)
                }
            }
        }
        $evidence.status = "PASS"
        $evidence.executed = $true
        $evidence.result_count = [int64]$rows.Count
        return [pscustomobject]@{
            Evidence = $evidence
            Rows = $rows
        }
    } catch {
        $evidence.executed = $true
        $evidence.reason = Get-SanitizedSqlError $_
        $evidence.error_type = $_.Exception.GetType().Name
        $errorCodeMatch = [regex]::Match([string]$_.Exception.Message, '\b\d{3,5}\b')
        if ($errorCodeMatch.Success) {
            $evidence.error_code = [int]$errorCodeMatch.Value
        }
        return [pscustomobject]@{
            Evidence = $evidence
            Rows = @()
        }
    }
}

try {
    $identity = Invoke-ReadOnlySql @"
SELECT CAST(SERVERPROPERTY('ServerName') AS nvarchar(128)) AS ServerName,
       CAST(SERVERPROPERTY('MachineName') AS nvarchar(128)) AS MachineName,
       CAST(SERVERPROPERTY('InstanceName') AS nvarchar(128)) AS InstanceName,
       CAST(@@SERVERNAME AS nvarchar(128)) AS ConfiguredServerName,
       DB_NAME() AS DatabaseName,
       SUSER_SNAME() AS LoginName, SYSUTCDATETIME() AS CapturedAtUtc,
       CAST(SERVERPROPERTY('ProductMajorVersion') AS INT) AS ProductMajorVersion;
"@
} catch {
    $sanitizedSqlError = Get-SanitizedSqlError $_
    if ($sanitizedSqlError -match '(?i)certificate|trust|ssl|tls|hostname') {
        throw ("SQL certificate validation failed while TrustServerCertificate=False; " + $sanitizedSqlError)
    }
    throw ("Encrypted SQL connection failed before identity was proven; " + $sanitizedSqlError)
}

if ($identity.Count -ne 1 -or [string]$identity[0].DatabaseName -ne $Database) {
    throw "SQL identity/database binding was not proven"
}
if ([string]$identity[0].LoginName -eq $RuntimeLoginName) {
    throw "Windows evidence identity must be separate from the limited runtime login"
}

$captured = Convert-SqlUtcDateTimeToIso8601 $identity[0].CapturedAtUtc

$transport = Invoke-ReadOnlySql @"
SELECT CAST(encrypt_option AS nvarchar(10)) AS EncryptOption,
       CAST(auth_scheme AS nvarchar(128)) AS AuthScheme,
       CAST(net_transport AS nvarchar(128)) AS NetTransport
FROM sys.dm_exec_connections
WHERE session_id = @@SPID;
"@
if ($transport.Count -ne 1) {
    throw "SQL connection transport security was not returned for the evidence session"
}
$actualEncryptOption = ([string]$transport[0].EncryptOption).Trim().ToUpperInvariant()
if ($actualEncryptOption -notin @("TRUE", "YES", "1")) {
    throw "SQL connection did not prove encrypted transport; no administrative evidence was written"
}
$authScheme = ([string]$transport[0].AuthScheme).Trim().ToUpperInvariant()
if ($authScheme -notin @("KERBEROS", "NTLM")) {
    throw "SQL connection did not prove Windows Authentication; no administrative evidence was written"
}
$tlsEvidence = [ordered]@{
    encryption_mode = $EncryptMode
    actual_encryption = ([string]$transport[0].EncryptOption).Trim()
    actual_transport = "ENCRYPTED"
    trust_server_certificate = $false
    certificate_validation_required = $true
    certificate_validation_observed = $true
    certificate_validation_status = "VALIDATED"
    certificate_validation_method = "INVOKE_SQLCMD_TRUST_SERVER_CERTIFICATE_FALSE_AND_DM_EXEC_CONNECTIONS_ENCRYPT_OPTION_TRUE"
    auth_scheme = ([string]$transport[0].AuthScheme).Trim()
    net_transport = ([string]$transport[0].NetTransport).Trim()
}

$permissions = Invoke-ReadOnlySql @"
SELECT HAS_PERMS_BY_NAME(DB_NAME(), N'DATABASE', N'VIEW DEFINITION') AS HasViewDefinition,
       HAS_PERMS_BY_NAME(DB_NAME(), N'DATABASE', N'VIEW DATABASE STATE') AS HasViewDatabaseState,
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
$productMajorVersionValue = $identity[0].ProductMajorVersion
if ($null -eq $productMajorVersionValue) {
    throw "SQL Server product major version was not returned"
}
$productMajorVersion = [int]$productMajorVersionValue
$serverDmvMetadataState = if ($productMajorVersion -ge 16) {
    Get-PermissionState $permissions[0].HasViewServerPerformanceState
} else {
    Get-PermissionState $permissions[0].HasViewServerState
}
$backupVerifyPermissionState = if (
    ([int]$permissions[0].HasCreateAnyDatabase -eq 1) -or
    ([int]$permissions[0].IsSysadmin -eq 1) -or
    ([int]$permissions[0].IsDbcreator -eq 1)
) { "GRANTED" } elseif (
    $null -ne $permissions[0].HasCreateAnyDatabase -and
    $null -ne $permissions[0].IsSysadmin -and
    $null -ne $permissions[0].IsDbcreator
) { "DENIED" } else { "UNKNOWN" }
$permissionChecks = [ordered]@{
    database_view_definition = (Get-PermissionState $permissions[0].HasViewDefinition)
    database_view_state = (Get-PermissionState $permissions[0].HasViewDatabaseState)
    server_dmv_state = $serverDmvMetadataState
    backup_history_backupset_select = (Get-PermissionState $permissions[0].HasBackupsetSelect)
    backup_history_backupmediafamily_select = (Get-PermissionState $permissions[0].HasBackupmediafamilySelect)
    operational_history_database_select = (Get-PermissionState $permissions[0].HasDatabaseSelect)
    backup_verifyonly_authorization = $backupVerifyPermissionState
}

$databaseFiles = Invoke-ReadOnlySql "SELECT name, type_desc, size, growth, is_percent_growth, max_size, FILEPROPERTY(name, 'SpaceUsed') AS space_used_pages, physical_name FROM sys.database_files ORDER BY file_id;"
$databaseFileDetails = @($databaseFiles | ForEach-Object {
    $sizePages = [int64]$_.size
    $spaceUsedPages = if ($null -eq $_.space_used_pages) { [int64]0 } else { [int64]$_.space_used_pages }
    @{
        name = [string]$_.name
        type = [string]$_.type_desc
        size_pages = $sizePages
        growth = [int]$_.growth
        is_percent_growth = [bool]$_.is_percent_growth
        max_size_pages = [int64]$_.max_size
        space_used_pages = $spaceUsedPages
        size_bytes = $sizePages * 8192
        space_used_bytes = $spaceUsedPages * 8192
        physical_name = [string]$_.physical_name
    }
})
$runtimeHealth = Invoke-ReadOnlySql "SELECT DB_NAME() AS database_name, DATABASEPROPERTYEX(DB_NAME(), 'Status') AS database_status, DATABASEPROPERTYEX(DB_NAME(), 'Recovery') AS recovery_model;"
$volumes = Invoke-ReadOnlySql "SELECT DISTINCT vs.volume_mount_point, vs.total_bytes, vs.available_bytes FROM sys.database_files AS df CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs ORDER BY vs.volume_mount_point;"
$logSpace = Invoke-ReadOnlySql "SELECT total_log_size_in_bytes, used_log_space_in_bytes, used_log_space_in_percent FROM sys.dm_db_log_space_usage WHERE database_id = DB_ID();"
$backup = Invoke-ReadOnlySql "SELECT TOP (1) bs.backup_set_id, bs.media_set_id, bs.backup_finish_date, bs.is_copy_only, bs.has_backup_checksums, bs.compressed_backup_size FROM msdb.dbo.backupset AS bs WHERE bs.database_name = DB_NAME() AND bs.type = 'D' ORDER BY bs.backup_finish_date DESC;"
$materialization = Invoke-ReadOnlySql "SELECT COUNT_BIG(*) AS TargetCount, COALESCE(SUM(CASE WHEN IsEnabled = 1 THEN 1 ELSE 0 END), 0) AS ActiveCount FROM ctl.MddMacTiered50ProductionTarget WHERE PanelVersion = 'TIERED50_PANEL_V1' AND SamplingEra = 'Tiered50';"
$backupMedia = if ($backup.Count -eq 1) {
    Invoke-ReadOnlySql ("SELECT family_sequence_number, physical_device_name FROM msdb.dbo.backupmediafamily WHERE media_set_id = {0} ORDER BY family_sequence_number;" -f ([int64]$backup[0].media_set_id))
} else { @() }
$cutoverHistoryQuery = Invoke-AuditedReadOnlySql "collector.production_cutover_history" "SELECT CutoverKey, PanelVersion, Tiered50ActivationAtUtc, Tiered50ActivationStatus, RecordedAtUtc FROM ctl.MddMacProductionCutover WHERE PanelVersion = 'TIERED50_PANEL_V1' ORDER BY CutoverId;"
$cutoverHistory = @($cutoverHistoryQuery.Rows)
$cutoverHistoryEvidence = $cutoverHistoryQuery.Evidence
$competingWriters = Invoke-ReadOnlySql "SELECT CollectionContext, COUNT_BIG(*) AS RunningCount FROM ctl.MddMacCollectorRun WHERE Status IN ('RUNNING', 'CLAIMED') AND CollectionContext IN ('MAC_LEGACY7_PRODUCTION', 'MAC_TIERED50_PRODUCTION') GROUP BY CollectionContext;"
$currentQuotaLedger = Invoke-ReadOnlySql "SELECT TOP (2) MonthKey, ProviderQuotaCeiling, NormalOperationalMaximum, RetryProtectionBoundary, ConfirmedProviderAttempts, ConservativeUnknownAttempts, ExplicitSharedConsumerReservedAttempts, AccountingStatus, IncludesAllSharedConsumers, ProviderPeriodStartUtc, ProviderPeriodEndUtc, LocalUsageIncludedThroughUtc, SourceEvidenceHash, ReconciliationKey FROM ctl.MddMacSharedQuotaAccounting WHERE ProviderPeriodStartUtc <= SYSUTCDATETIME() AND ProviderPeriodEndUtc > SYSUTCDATETIME() ORDER BY ProviderPeriodStartUtc DESC, RecordedAtUtc DESC;"
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

# A successful DMV query is effective permission evidence.  SQL Server 2025
# can return NULL for a metadata permission function even for an authorized
# sysadmin; do not reject that identity when the protected DMV reads actually
# complete under the same Windows Authentication context.
if ($databaseFiles.Count -gt 0) {
    $permissionChecks.database_view_definition = "GRANTED"
}
if ($logSpace.Count -eq 1) {
    $permissionChecks.database_view_state = "GRANTED"
}
if ($volumes.Count -gt 0 -and $logSpace.Count -eq 1) {
    $permissionChecks.server_dmv_state = "GRANTED"
}
$permissionChecks.backup_history_backupset_select = "GRANTED"
if ($backupMedia.Count -gt 0) {
    $permissionChecks.backup_history_backupmediafamily_select = "GRANTED"
}
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

# These are deliberately separate read-only queries.  A successful query with
# zero rows is recorded as PASS with result_count = 0; a denied, missing, or
# malformed query is recorded as NOT_PROVEN with a sanitized cause.  The
# CollectorRun count is not used as a proxy for target-audit or HTTP history.
$historicalRunsQuery = Invoke-AuditedReadOnlySql "collector.historical_runs" @"
SELECT TOP (100) MacCollectorRunId, RunId, LogicalDueBucket,
       CollectionContext, StartedAtUtc, CompletedAtUtc, Status
FROM ctl.MddMacCollectorRun
WHERE CollectionContext IN ('MAC_LEGACY7_PRODUCTION', 'MAC_TIERED50_PRODUCTION')
ORDER BY MacCollectorRunId DESC;
"@
$historicalTargetAuditsQuery = Invoke-AuditedReadOnlySql "collector.historical_target_audits" @"
SELECT TOP (100) AuditId, RunId, TargetIdentity, LogicalDueBucket,
       CollectionContext, Status, HttpAttempts, StartedAtUtc, CompletedAtUtc
FROM ctl.MddMacTargetAudit
WHERE CollectionContext IN ('MAC_LEGACY7_PRODUCTION', 'MAC_TIERED50_PRODUCTION')
ORDER BY AuditId DESC;
"@
$httpAttemptHistoryQuery = Invoke-AuditedReadOnlySql "collector.http_attempt_history" @"
SELECT TOP (100) h.HttpAttemptId, h.AuditId, h.AttemptNumber,
       h.StartedAtUtc, h.CompletedAtUtc, h.HttpStatus, h.Outcome,
       h.NetworkAttempted, h.HttpResponseReceived, h.ProviderQuotaStatus,
       a.RunId, a.TargetIdentity, a.LogicalDueBucket, a.CollectionContext
FROM ctl.MddMacHttpAttempt AS h
INNER JOIN ctl.MddMacTargetAudit AS a ON a.AuditId = h.AuditId
WHERE a.CollectionContext IN ('MAC_LEGACY7_PRODUCTION', 'MAC_TIERED50_PRODUCTION')
ORDER BY h.CompletedAtUtc DESC, h.HttpAttemptId DESC;
"@
# These two queries deliberately remain separate from the recent samples above.
# The first heartbeat is the original Tiered50 record, and its target audits
# and HTTP attempts are cross-checked against that exact RunId. Both queries
# are bounded and read-only; they never synthesize a successful result.
$firstTiered50HeartbeatQuery = Invoke-AuditedReadOnlySql "collector.tiered50_first_heartbeat" @"
SELECT TOP (1) MacCollectorRunId, RunId, LogicalDueBucket,
       StartedAtUtc, CompletedAtUtc, Status
FROM ctl.MddMacCollectorRun
WHERE CollectionContext = 'MAC_TIERED50_PRODUCTION'
ORDER BY MacCollectorRunId ASC;
"@
$historicalOutageQuery = Invoke-AuditedReadOnlySql "collector.historical_outage" @"
SELECT TOP (100) CutoverId, CutoverKey, PanelVersion,
       Tiered50ActivationAtUtc, Tiered50ActivationStatus, RecordedAtUtc
FROM ctl.MddMacProductionCutover
WHERE PanelVersion = 'TIERED50_PANEL_V1'
  AND Tiered50ActivationStatus = 'OUTAGE'
ORDER BY CutoverId ASC;
"@
$firstTiered50HeartbeatRows = @($firstTiered50HeartbeatQuery.Rows)
if ($firstTiered50HeartbeatRows.Count -eq 1 -and -not [string]::IsNullOrWhiteSpace([string]$firstTiered50HeartbeatRows[0].RunId)) {
    $firstTiered50RunId = [int64]$firstTiered50HeartbeatRows[0].MacCollectorRunId
    $firstTiered50RunIdText = [string]$firstTiered50HeartbeatRows[0].RunId
    $escapedFirstTiered50RunId = $firstTiered50RunIdText.Replace("'", "''")
    $firstTiered50HeartbeatTargetAuditsQuery = Invoke-AuditedReadOnlySql "collector.tiered50_first_heartbeat_target_audits" (@"
SELECT TOP (50) AuditId, RunId, TargetIdentity, LogicalDueBucket,
       CollectionContext, Status, HttpAttempts, StartedAtUtc, CompletedAtUtc
FROM ctl.MddMacTargetAudit
WHERE RunId = N'{0}'
ORDER BY AuditId ASC;
"@ -f $escapedFirstTiered50RunId)
    $firstTiered50HeartbeatHttpAttemptsQuery = Invoke-AuditedReadOnlySql "collector.tiered50_first_heartbeat_http_attempts" (@"
SELECT TOP (100) h.HttpAttemptId, h.AuditId, h.AttemptNumber,
       h.StartedAtUtc, h.CompletedAtUtc, h.HttpStatus, h.Outcome,
       h.NetworkAttempted, h.HttpResponseReceived, h.ProviderQuotaStatus,
       a.RunId, a.TargetIdentity, a.LogicalDueBucket, a.CollectionContext
FROM ctl.MddMacHttpAttempt AS h
INNER JOIN ctl.MddMacTargetAudit AS a ON a.AuditId = h.AuditId
WHERE a.RunId = N'{0}'
ORDER BY h.HttpAttemptId ASC;
"@ -f $escapedFirstTiered50RunId)
    $laterTiered50RunsQueryText = @"
SELECT TOP (100) MacCollectorRunId, RunId, LogicalDueBucket,
       StartedAtUtc, CompletedAtUtc, Status
FROM ctl.MddMacCollectorRun
WHERE CollectionContext = 'MAC_TIERED50_PRODUCTION'
  AND MacCollectorRunId > {0}
ORDER BY MacCollectorRunId ASC;
"@
    $laterTiered50RunsQuery = Invoke-AuditedReadOnlySql "collector.tiered50_later_runs" ($laterTiered50RunsQueryText -f $firstTiered50RunId)
} else {
    $firstTiered50RunIdText = ""
    $firstTiered50HeartbeatTargetAuditsQuery = New-NotExecutedAuditQuery "collector.tiered50_first_heartbeat_target_audits" "the original Tiered50 heartbeat RunId was not returned"
    $firstTiered50HeartbeatHttpAttemptsQuery = New-NotExecutedAuditQuery "collector.tiered50_first_heartbeat_http_attempts" "the original Tiered50 heartbeat RunId was not returned"
    $laterTiered50RunsQuery = New-NotExecutedAuditQuery "collector.tiered50_later_runs" "the later-run query was not executed because the original Tiered50 heartbeat was not returned"
}
$historicalRunsEvidence = $historicalRunsQuery.Evidence
$historicalTargetAuditsEvidence = $historicalTargetAuditsQuery.Evidence
$httpAttemptHistoryEvidence = $httpAttemptHistoryQuery.Evidence
$firstTiered50HeartbeatEvidence = $firstTiered50HeartbeatQuery.Evidence
$firstTiered50HeartbeatTargetAuditsEvidence = $firstTiered50HeartbeatTargetAuditsQuery.Evidence
$firstTiered50HeartbeatHttpAttemptsEvidence = $firstTiered50HeartbeatHttpAttemptsQuery.Evidence
$historicalOutageEvidence = $historicalOutageQuery.Evidence
$laterTiered50RunsEvidence = $laterTiered50RunsQuery.Evidence
if ($historicalRunsEvidence.status -eq "PASS") {
    $permissionChecks.operational_history_database_select = "GRANTED"
}

if ($null -eq $backup -or $backup.Count -ne 1 -or $backupMedia.Count -lt 1 -or ($backupMedia | Where-Object { [string]::IsNullOrWhiteSpace([string]$_.physical_device_name) }).Count -gt 0) {
    throw "No native full backup path was available for server-side VERIFYONLY"
}
if ($databaseFiles.Count -eq 0 -or $runtimeHealth.Count -ne 1 -or [string]$runtimeHealth[0].database_status -ne "ONLINE" -or [string]::IsNullOrWhiteSpace([string]$runtimeHealth[0].recovery_model) -or $volumes.Count -eq 0 -or $logSpace.Count -ne 1) {
    throw "SQL capacity evidence was incomplete"
}
if ($materialization.Count -ne 1 -or [int64]$materialization[0].TargetCount -ne 43 -or [int64]$materialization[0].ActiveCount -ne 43) {
    throw "Tiered50 materialization or operational-history evidence was incomplete"
}
if ($quota.Count -ne 1 -or [string]$quota[0].AccountingStatus -notin @('COMPLETE', 'ACTIVE')) {
    throw "No current SQL quota baseline or bounded authorization was available"
}
if ($currentQuotaLedger.Count -gt 1) {
    throw "Multiple current SQL quota ledger rows were returned"
}
if ($currentQuotaLedger.Count -eq 0 -and ($quota.Count -ne 1 -or [string]$quota[0].SourceKind -ne 'BOUNDED_BOOTSTRAP')) {
    throw "No single current SQL quota ledger or approved bounded bootstrap was available"
}
if (($competingWriters | Where-Object { [int64]$_.RunningCount -gt 0 }).Count -gt 0) {
    throw "A competing production writer is active"
}

# VERIFYONLY is executed by SQL Server, not by the Windows client.  Its path
# and diagnostic text never enter the sanitized JSON payload.
$backupMediaClauses = @($backupMedia | ForEach-Object { "DISK = N'{0}'" -f ([string]$_.physical_device_name).Replace("'", "''") })
Invoke-ReadOnlySql ("RESTORE VERIFYONLY FROM {0} WITH CHECKSUM;" -f ($backupMediaClauses -join ", ")) | Out-Null
$permissionChecks.backup_verifyonly_authorization = "GRANTED"

$missingPermissions = @(
    $permissionChecks.GetEnumerator() |
        Where-Object { $_.Value -ne "GRANTED" } |
        ForEach-Object { $_.Key + "=" + $_.Value }
)
$permissionsProven = $missingPermissions.Count -eq 0

$windowsDiskRaw = @(Get-CimInstance Win32_Volume -Filter "DriveType = 3")
if ($windowsDiskRaw.Count -eq 0) {
    # Win32_LogicalDisk is retained only as a local compatibility fallback;
    # SQL volume binding below still has to match a measured fixed volume.
    $windowsDiskRaw = @(Get-CimInstance Win32_LogicalDisk -Filter "DriveType = 3")
}
$windowsDisk = @($windowsDiskRaw | ForEach-Object {
    $mountPoint = if (-not [string]::IsNullOrWhiteSpace([string]$_.Caption)) {
        [string]$_.Caption
    } elseif (-not [string]::IsNullOrWhiteSpace([string]$_.DeviceID)) {
        ([string]$_.DeviceID).TrimEnd("\") + "\"
    } else {
        ""
    }
    $totalBytes = if ($null -ne $_.Capacity) { [int64]$_.Capacity } else { [int64]$_.Size }
    $freeBytes = [int64]$_.FreeSpace
    @{ drive = [string]$_.DriveLetter; mount_point = $mountPoint; total_bytes = $totalBytes; free_bytes = $freeBytes; free_percent = if ($totalBytes -gt 0) { [double]($freeBytes * 100.0 / $totalBytes) } else { 0.0 } }
})
if ($windowsDisk.Count -eq 0 -or ($windowsDisk | Where-Object { $_.total_bytes -le 0 -or $_.free_bytes -le 0 -or $_.free_bytes -gt $_.total_bytes }).Count -gt 0) {
    throw "Windows VM fixed-disk capacity was not proven"
}

$sqlVolumeHeadroom = @($volumes | ForEach-Object {
    $sqlVolume = [string]$_.volume_mount_point
    $filesOnVolume = @($databaseFileDetails | Where-Object {
        Test-PathOnVolume ([string]$_.physical_name) $sqlVolume
    })
    if ($filesOnVolume.Count -eq 0) {
        throw ("SQL database files could not be bound to volume " + $sqlVolume)
    }
    $fixedVolume = @($windowsDisk | Where-Object {
        Test-SameVolume ([string]$_.mount_point) $sqlVolume
    })
    if ($fixedVolume.Count -ne 1) {
        throw ("SQL volume " + $sqlVolume + " was not matched to exactly one measured Windows fixed volume")
    }
    $sqlTotalBytes = [int64]$_.total_bytes
    $sqlAvailableBytes = [int64]$_.available_bytes
    $windowsTotalBytes = [int64]$fixedVolume[0].total_bytes
    $windowsAvailableBytes = [int64]$fixedVolume[0].free_bytes
    $totalBytes = [Math]::Min($sqlTotalBytes, $windowsTotalBytes)
    $availableBytes = [Math]::Min($sqlAvailableBytes, $windowsAvailableBytes)
    $requiredBytes = [Math]::Max($MinimumFreeReserveBytes, [int64]($totalBytes * ($MinimumFreePercent / 100.0)))
    $headroomBytes = $availableBytes - $requiredBytes
    $availablePercent = if ($totalBytes -gt 0) { [double]($availableBytes * 100.0 / $totalBytes) } else { 0.0 }
    $fileTypes = @($filesOnVolume | ForEach-Object { [string]$_.type } | Sort-Object -Unique)
    $headroomStatus = if ($headroomBytes -gt 0 -and $availablePercent -ge $MinimumFreePercent) { "PASS" } else { "FAIL" }
    @{
        volume = $sqlVolume
        total_bytes = $totalBytes
        available_bytes = $availableBytes
        available_percent = $availablePercent
        minimum_required_free_bytes = $requiredBytes
        headroom_bytes = $headroomBytes
        headroom_status = $headroomStatus
        database_file_types = $fileTypes
        database_file_count = [int]$filesOnVolume.Count
        database_file_size_bytes = [int64](($filesOnVolume | Measure-Object -Property size_bytes -Sum).Sum)
        database_file_space_used_bytes = [int64](($filesOnVolume | Measure-Object -Property space_used_bytes -Sum).Sum)
    }
})
if (-not ($sqlVolumeHeadroom | Where-Object { $_.database_file_types -contains "ROWS" })) {
    throw "Measured SQL data-file volume headroom was not proven"
}
if (-not ($sqlVolumeHeadroom | Where-Object { $_.database_file_types -contains "LOG" })) {
    throw "Measured SQL log-file volume headroom was not proven"
}
if (($sqlVolumeHeadroom | Where-Object { $_.headroom_status -ne "PASS" }).Count -gt 0) {
    throw "SQL data/log volume headroom is below the approved reserve"
}
$historicalRunRows = @($historicalRunsQuery.Rows | ForEach-Object {
    [ordered]@{
        mac_collector_run_id = [int64]$_.MacCollectorRunId
        run_id = [string]$_.RunId
        logical_due_bucket = [int64]$_.LogicalDueBucket
        collection_context = [string]$_.CollectionContext
        started_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 $_.StartedAtUtc
        completed_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 $_.CompletedAtUtc
        status = [string]$_.Status
    }
})
$historicalTargetAuditRows = @($historicalTargetAuditsQuery.Rows | ForEach-Object {
    [ordered]@{
        audit_id = [int64]$_.AuditId
        run_id = [string]$_.RunId
        target_identity = [string]$_.TargetIdentity
        logical_due_bucket = [int64]$_.LogicalDueBucket
        collection_context = [string]$_.CollectionContext
        status = [string]$_.Status
        http_attempts = [int]$_.HttpAttempts
        started_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 $_.StartedAtUtc
        completed_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 $_.CompletedAtUtc
    }
})
$httpAttemptRows = @($httpAttemptHistoryQuery.Rows | ForEach-Object {
    [ordered]@{
        http_attempt_id = [int64]$_.HttpAttemptId
        audit_id = [int64]$_.AuditId
        attempt_number = [int]$_.AttemptNumber
        started_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 $_.StartedAtUtc
        completed_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 $_.CompletedAtUtc
        http_status = if ($null -eq $_.HttpStatus) { $null } else { [int]$_.HttpStatus }
        outcome = [string]$_.Outcome
        network_attempted = [bool]$_.NetworkAttempted
        http_response_received = [bool]$_.HttpResponseReceived
        provider_quota_status = [string]$_.ProviderQuotaStatus
        run_id = [string]$_.RunId
        target_identity = [string]$_.TargetIdentity
        logical_due_bucket = [int64]$_.LogicalDueBucket
        collection_context = [string]$_.CollectionContext
    }
})
$firstHeartbeatRows = @($firstTiered50HeartbeatQuery.Rows | ForEach-Object {
    [ordered]@{
        mac_collector_run_id = [int64]$_.MacCollectorRunId
        run_id = [string]$_.RunId
        logical_due_bucket = [int64]$_.LogicalDueBucket
        started_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 $_.StartedAtUtc
        completed_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 $_.CompletedAtUtc
        status = [string]$_.Status
    }
})
$firstHeartbeatTargetAuditRows = @($firstTiered50HeartbeatTargetAuditsQuery.Rows | ForEach-Object {
    [ordered]@{
        audit_id = [int64]$_.AuditId
        run_id = [string]$_.RunId
        target_identity = [string]$_.TargetIdentity
        logical_due_bucket = [int64]$_.LogicalDueBucket
        collection_context = [string]$_.CollectionContext
        status = [string]$_.Status
        http_attempts = [int]$_.HttpAttempts
        started_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 $_.StartedAtUtc
        completed_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 $_.CompletedAtUtc
    }
})
$firstHeartbeatHttpAttemptRows = @($firstTiered50HeartbeatHttpAttemptsQuery.Rows | ForEach-Object {
    [ordered]@{
        http_attempt_id = [int64]$_.HttpAttemptId
        audit_id = [int64]$_.AuditId
        attempt_number = [int]$_.AttemptNumber
        started_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 $_.StartedAtUtc
        completed_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 $_.CompletedAtUtc
        http_status = if ($null -eq $_.HttpStatus) { $null } else { [int]$_.HttpStatus }
        outcome = [string]$_.Outcome
        network_attempted = [bool]$_.NetworkAttempted
        http_response_received = [bool]$_.HttpResponseReceived
        provider_quota_status = [string]$_.ProviderQuotaStatus
        run_id = [string]$_.RunId
        target_identity = [string]$_.TargetIdentity
        logical_due_bucket = [int64]$_.LogicalDueBucket
        collection_context = [string]$_.CollectionContext
    }
})
$historicalOutageRows = @($historicalOutageQuery.Rows | ForEach-Object {
    [ordered]@{
        cutover_id = [int64]$_.CutoverId
        cutover_key = [string]$_.CutoverKey
        panel_version = [string]$_.PanelVersion
        tiered50_activation_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 $_.Tiered50ActivationAtUtc
        tiered50_activation_status = [string]$_.Tiered50ActivationStatus
        recorded_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 $_.RecordedAtUtc
    }
})
$laterTiered50RunRows = @($laterTiered50RunsQuery.Rows | ForEach-Object {
    [ordered]@{
        mac_collector_run_id = [int64]$_.MacCollectorRunId
        run_id = [string]$_.RunId
        logical_due_bucket = [int64]$_.LogicalDueBucket
        started_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 $_.StartedAtUtc
        completed_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 $_.CompletedAtUtc
        status = [string]$_.Status
    }
})
$firstHeartbeatRunIdCrossCheck = if ($firstHeartbeatRows.Count -eq 1) { [string]$firstHeartbeatRows[0].run_id } else { "" }
$firstHeartbeatTargetAuditIds = @($firstHeartbeatTargetAuditRows | ForEach-Object { [int64]$_.audit_id })
$firstHeartbeatTargetAuditRunIdMismatch = @(
    $firstHeartbeatTargetAuditRows |
        Where-Object { [string]$_.run_id -ne $firstHeartbeatRunIdCrossCheck }
).Count -gt 0
$firstHeartbeatHttpAttemptRunIdMismatch = @(
    $firstHeartbeatHttpAttemptRows |
        Where-Object { [string]$_.run_id -ne $firstHeartbeatRunIdCrossCheck }
).Count -gt 0
$firstHeartbeatHttpAttemptAuditMismatch = @(
    $firstHeartbeatHttpAttemptRows |
        Where-Object { $firstHeartbeatTargetAuditIds -notcontains [int64]$_.audit_id }
).Count -gt 0
$historicalRunsCheckStatus = if ($historicalRunsEvidence.status -eq "PASS" -and $historicalRunRows.Count -gt 0) { "PASS" } else { "NOT_PROVEN" }
$historicalTargetAuditsCheckStatus = if ($historicalTargetAuditsEvidence.status -eq "PASS" -and $historicalTargetAuditRows.Count -gt 0) { "PASS" } else { "NOT_PROVEN" }
$httpAttemptHistoryCheckStatus = if ($httpAttemptHistoryEvidence.status -eq "PASS") { "PASS" } else { "NOT_PROVEN" }
$firstHeartbeatCheckStatus = if ($firstTiered50HeartbeatEvidence.status -eq "PASS" -and $firstHeartbeatRows.Count -eq 1) { "PASS" } else { "NOT_PROVEN" }
$firstHeartbeatTargetAuditsCheckStatus = if (
    $firstTiered50HeartbeatTargetAuditsEvidence.status -eq "PASS" -and
    $firstHeartbeatTargetAuditRows.Count -gt 0 -and
    -not $firstHeartbeatTargetAuditRunIdMismatch -and
    -not [string]::IsNullOrWhiteSpace($firstHeartbeatRunIdCrossCheck)
) { "PASS" } else { "NOT_PROVEN" }
$firstHeartbeatHttpAttemptsCheckStatus = if (
    $firstTiered50HeartbeatHttpAttemptsEvidence.status -eq "PASS" -and
    -not $firstHeartbeatHttpAttemptRunIdMismatch -and
    -not $firstHeartbeatHttpAttemptAuditMismatch -and
    -not [string]::IsNullOrWhiteSpace($firstHeartbeatRunIdCrossCheck)
) { "PASS" } else { "NOT_PROVEN" }
$historicalOutageCheckStatus = if ($historicalOutageEvidence.status -eq "PASS" -and $historicalOutageRows.Count -gt 0) { "PASS" } else { "NOT_PROVEN" }
$laterTiered50RunsCheckStatus = if ($laterTiered50RunsEvidence.status -eq "PASS") { "PASS" } else { "NOT_PROVEN" }
$cutoverHistoryCheckStatus = if ($cutoverHistoryEvidence.status -eq "PASS" -and $cutoverHistory.Count -gt 0) { "PASS" } else { "NOT_PROVEN" }
$auditHistoryProven = @(
    $historicalRunsCheckStatus,
    $historicalTargetAuditsCheckStatus,
    $httpAttemptHistoryCheckStatus,
    $firstHeartbeatCheckStatus,
    $firstHeartbeatTargetAuditsCheckStatus,
    $firstHeartbeatHttpAttemptsCheckStatus,
    $historicalOutageCheckStatus,
    $laterTiered50RunsCheckStatus,
    $cutoverHistoryCheckStatus
) -notcontains "NOT_PROVEN"
$adminAuthorizationProven = $auditHistoryProven -and $permissionsProven

$payload = [ordered]@{
    artifact_version = "TIERED50_ADMIN_EVIDENCE_V3"
    capture_role = "WINDOWS_INTEGRATED_AUTH_READ_ONLY_CAPTURE"
    generated_at_utc = $captured
    capture_provenance = [ordered]@{
        status = if ($adminAuthorizationProven) { "PASS" } else { "NOT_PROVEN" }
        read_only = $true
        execution_path = "WINDOWS_INTEGRATED_AUTH"
        server_name = [string]$identity[0].ServerName
        sql_instance_identity = @{ server_name = [string]$identity[0].ServerName; machine_name = [string]$identity[0].MachineName; instance_name = [string]$identity[0].InstanceName; database_name = [string]$identity[0].DatabaseName }
        database_name = [string]$identity[0].DatabaseName
        effective_login = [string]$identity[0].LoginName
        captured_at_utc = $captured
        required_check_ids = @(
            "admin.identity", "admin.permissions", "capacity.database_files",
            "admin.sql_runtime_health",
            "capacity.volume_capacity_dm_os_volume_stats",
            "capacity.database_log_space_dm_db_log_space_usage",
            "backup.latest_full_backup_history", "backup.restore_verifyonly_with_checksum",
            "collector_runtime_history_or_materialization",
            "collector.production_cutover_history", "collector.competing_production_writers",
            "quota.current_shared_ledger",
            "collector.historical_runs", "collector.historical_target_audits",
            "collector.http_attempt_history", "collector.tiered50_first_heartbeat",
            "collector.tiered50_first_heartbeat_target_audits",
            "collector.tiered50_first_heartbeat_http_attempts",
            "collector.historical_outage", "collector.tiered50_later_runs"
        )
        authentication_source = "WINDOWS_AUTHENTICATION_SQL_SERVER_VM"
        tls = $tlsEvidence
    }
    sql = [ordered]@{
        admin_authorization = @{ status = if ($adminAuthorizationProven) { "PASS" } else { "NOT_PROVEN" }; permission_checks = $permissionChecks; audit_history_proven = $auditHistoryProven }
        admin_connection = @{ status = if ($adminAuthorizationProven) { "PASS" } else { "NOT_PROVEN" }; authorization_status = if ($adminAuthorizationProven) { "PASS" } else { "NOT_PROVEN" }; tls = $tlsEvidence }
        identity = @{ status = "PASS"; server_name = [string]$identity[0].ServerName; machine_name = [string]$identity[0].MachineName; instance_name = [string]$identity[0].InstanceName; configured_server_name = [string]$identity[0].ConfiguredServerName; database_name = [string]$identity[0].DatabaseName; login_name = [string]$identity[0].LoginName; captured_at_utc = $captured; sql_instance_identity = @{ server_name = [string]$identity[0].ServerName; machine_name = [string]$identity[0].MachineName; instance_name = [string]$identity[0].InstanceName; database_name = [string]$identity[0].DatabaseName } }
        admin_identity_gate = @{ status = "PASS"; effective_login = [string]$identity[0].LoginName; runtime_login_match = $false }
        admin_permissions = @{ status = if ($permissionsProven) { "PASS" } else { "NOT_PROVEN" }; product_major_version = $productMajorVersion; permission_checks = $permissionChecks; missing_permissions = $missingPermissions; tls = $tlsEvidence }
        runtime_health = @{ status = "PASS"; database_name = [string]$runtimeHealth[0].database_name; database_status = [string]$runtimeHealth[0].database_status; recovery_model = [string]$runtimeHealth[0].recovery_model; evidence = @{ status = "PASS" } }
        database_files = @($databaseFileDetails | ForEach-Object { @{ name = [string]$_.name; type = [string]$_.type; size_pages = [int64]$_.size_pages; growth = [int]$_.growth; is_percent_growth = [bool]$_.is_percent_growth; max_size_pages = [int64]$_.max_size_pages; space_used_pages = [int64]$_.space_used_pages; size_bytes = [int64]$_.size_bytes; space_used_bytes = [int64]$_.space_used_bytes } })
        database_files_evidence = @{ status = "PASS" }
        volume_capacity = @($volumes | ForEach-Object { @{ volume = [string]$_.volume_mount_point; total_bytes = [int64]$_.total_bytes; available_bytes = [int64]$_.available_bytes } })
        volume_capacity_evidence = @{ status = "PASS" }
        log_space = @{ total_bytes = [int64]$logSpace[0].total_log_size_in_bytes; used_bytes = [int64]$logSpace[0].used_log_space_in_bytes; used_percent = [double]$logSpace[0].used_log_space_in_percent }
        log_space_evidence = @{ status = "PASS" }
        backup_verifyonly = @{ status = "PASS"; checksum_verified = $true; media_count = [int]$backupMedia.Count; cause_classification = "VERIFIED" }
        backup_gate = @{ status = "PASS" }
        capacity_gate = @{ status = "PASS" }
        log_space_gate = @{ status = "PASS" }
        quota_integrity = @{ status = "PASS"; state_available = $true; source_kind = [string]$quota[0].SourceKind; month_key = [string]$quota[0].MonthKey; accounting_status = [string]$quota[0].AccountingStatus; known_total = [int64]$quota[0].KnownTotal; reserved_shared_usage = [int64]$quota[0].ReservedSharedUsage }
        latest_full_backup = @{ backup_set_id = [int64]$backup[0].backup_set_id; media_set_id = [int64]$backup[0].media_set_id; backup_finish_date = [string]$backup[0].backup_finish_date; copy_only = [bool]$backup[0].is_copy_only; has_backup_checksums = [bool]$backup[0].has_backup_checksums; compressed_backup_size = [int64]$backup[0].compressed_backup_size; media_count = [int]$backupMedia.Count; complete_media_set_proven = $true }
        latest_full_backup_evidence = @{ status = "PASS" }
        latest_full_backup_media_evidence = @{ status = "PASS"; media_count = [int]$backupMedia.Count }
        tiered50_materialization = @{ status = "PASS"; target_count = [int64]$materialization[0].TargetCount; active_count = [int64]$materialization[0].ActiveCount }
        tiered50_audit_history_evidence = @{ status = $historicalTargetAuditsEvidence.status; statement_id = $historicalTargetAuditsEvidence.statement_id; executed = $historicalTargetAuditsEvidence.executed; result_count = $historicalTargetAuditsEvidence.result_count; captured_at_utc = $historicalTargetAuditsEvidence.captured_at_utc; check_status = $historicalTargetAuditsCheckStatus; reason = $historicalTargetAuditsEvidence.reason }
        recent_http_attempts_evidence = @{ status = $httpAttemptHistoryEvidence.status; statement_id = $httpAttemptHistoryEvidence.statement_id; executed = $httpAttemptHistoryEvidence.executed; result_count = $httpAttemptHistoryEvidence.result_count; captured_at_utc = $httpAttemptHistoryEvidence.captured_at_utc; check_status = $httpAttemptHistoryCheckStatus; reason = $httpAttemptHistoryEvidence.reason }
        first_tiered50_heartbeat_target_audits_evidence = @{ status = $firstTiered50HeartbeatTargetAuditsEvidence.status; statement_id = $firstTiered50HeartbeatTargetAuditsEvidence.statement_id; executed = $firstTiered50HeartbeatTargetAuditsEvidence.executed; result_count = $firstTiered50HeartbeatTargetAuditsEvidence.result_count; captured_at_utc = $firstTiered50HeartbeatTargetAuditsEvidence.captured_at_utc; check_status = $firstHeartbeatTargetAuditsCheckStatus; run_id = $firstHeartbeatRunIdCrossCheck; reason = $firstTiered50HeartbeatTargetAuditsEvidence.reason }
        first_tiered50_heartbeat_http_attempts_evidence = @{ status = $firstTiered50HeartbeatHttpAttemptsEvidence.status; statement_id = $firstTiered50HeartbeatHttpAttemptsEvidence.statement_id; executed = $firstTiered50HeartbeatHttpAttemptsEvidence.executed; result_count = $firstTiered50HeartbeatHttpAttemptsEvidence.result_count; captured_at_utc = $firstTiered50HeartbeatHttpAttemptsEvidence.captured_at_utc; check_status = $firstHeartbeatHttpAttemptsCheckStatus; run_id = $firstHeartbeatRunIdCrossCheck; reason = $firstTiered50HeartbeatHttpAttemptsEvidence.reason }
        production_cutover_history = @($cutoverHistory | ForEach-Object { @{ cutover_key = [string]$_.CutoverKey; panel_version = [string]$_.PanelVersion; tiered50_activation_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 $_.Tiered50ActivationAtUtc; tiered50_activation_status = [string]$_.Tiered50ActivationStatus; recorded_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 $_.RecordedAtUtc } })
        production_cutover_history_evidence = @{ status = $cutoverHistoryEvidence.status; statement_id = $cutoverHistoryEvidence.statement_id; executed = $cutoverHistoryEvidence.executed; result_count = [int64]$cutoverHistory.Count; captured_at_utc = $cutoverHistoryEvidence.captured_at_utc; check_status = $cutoverHistoryCheckStatus; reason = $cutoverHistoryEvidence.reason }
        competing_production_writers = @($competingWriters | ForEach-Object { @{ collection_context = [string]$_.CollectionContext; running_count = [int64]$_.RunningCount } })
        competing_production_writers_evidence = @{ status = "PASS" }
        current_quota_ledger = @($currentQuotaLedger | ForEach-Object { @{ month_key = [string]$_.MonthKey; provider_quota_ceiling = [int64]$_.ProviderQuotaCeiling; normal_operational_maximum = [int64]$_.NormalOperationalMaximum; retry_protection_boundary = [int64]$_.RetryProtectionBoundary; confirmed_provider_attempts = [int64]$_.ConfirmedProviderAttempts; conservative_unknown_attempts = [int64]$_.ConservativeUnknownAttempts; reserved_shared_usage = [int64]$_.ExplicitSharedConsumerReservedAttempts; accounting_status = [string]$_.AccountingStatus; includes_all_shared_consumers = [bool]$_.IncludesAllSharedConsumers; provider_period_start_utc = Convert-NullableSqlUtcDateTimeToIso8601 $_.ProviderPeriodStartUtc; provider_period_end_utc = Convert-NullableSqlUtcDateTimeToIso8601 $_.ProviderPeriodEndUtc; local_usage_included_through_utc = Convert-NullableSqlUtcDateTimeToIso8601 $_.LocalUsageIncludedThroughUtc; source_evidence_hash = [string]$_.SourceEvidenceHash; reconciliation_key = [string]$_.ReconciliationKey } })
        current_quota_ledger_evidence = @{ status = "PASS" }
        windows_vm_disk = @{ status = "PASS"; minimum_free_percent = $MinimumFreePercent; minimum_free_reserve_bytes = $MinimumFreeReserveBytes; volumes = $windowsDisk; sql_volume_headroom = $sqlVolumeHeadroom }
        audit_provenance = [ordered]@{
            "collector.historical_runs" = @{ query = $historicalRunsEvidence; check_status = $historicalRunsCheckStatus; rows = $historicalRunRows }
            "collector.historical_target_audits" = @{ query = $historicalTargetAuditsEvidence; check_status = $historicalTargetAuditsCheckStatus; rows = $historicalTargetAuditRows }
            "collector.http_attempt_history" = @{ query = $httpAttemptHistoryEvidence; check_status = $httpAttemptHistoryCheckStatus; linked_to_target_audit = $true; rows = $httpAttemptRows }
            "collector.tiered50_first_heartbeat" = @{ query = $firstTiered50HeartbeatEvidence; check_status = $firstHeartbeatCheckStatus; rows = $firstHeartbeatRows }
            "collector.tiered50_first_heartbeat_target_audits" = @{ query = $firstTiered50HeartbeatTargetAuditsEvidence; check_status = $firstHeartbeatTargetAuditsCheckStatus; linked_to_first_heartbeat_run = (-not [string]::IsNullOrWhiteSpace($firstHeartbeatRunIdCrossCheck)); run_id = $firstHeartbeatRunIdCrossCheck; rows = $firstHeartbeatTargetAuditRows }
            "collector.tiered50_first_heartbeat_http_attempts" = @{ query = $firstTiered50HeartbeatHttpAttemptsEvidence; check_status = $firstHeartbeatHttpAttemptsCheckStatus; linked_to_first_heartbeat_run = (-not [string]::IsNullOrWhiteSpace($firstHeartbeatRunIdCrossCheck)); linked_to_target_audit = (-not $firstHeartbeatHttpAttemptAuditMismatch); run_id = $firstHeartbeatRunIdCrossCheck; rows = $firstHeartbeatHttpAttemptRows }
            "collector.historical_outage" = @{ query = $historicalOutageEvidence; check_status = $historicalOutageCheckStatus; rows = $historicalOutageRows }
            "collector.tiered50_later_runs" = @{ query = $laterTiered50RunsEvidence; check_status = $laterTiered50RunsCheckStatus; later_collection_exists = ($laterTiered50RunRows.Count -gt 0); rows = $laterTiered50RunRows }
        }
    }
}

$parent = Split-Path -Parent $UnsignedPayloadPath
New-Item -ItemType Directory -Force -Path $parent | Out-Null
$json = $payload | ConvertTo-Json -Depth 20
$utf8NoBom = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false
[System.IO.File]::WriteAllText($UnsignedPayloadPath, $json, $utf8NoBom)
Write-Output $UnsignedPayloadPath
