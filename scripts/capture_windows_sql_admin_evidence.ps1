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

function Invoke-ReadOnlySql([string] $Query, [string] $StatementId = "admin.read_only_query") {
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

    <#
    Invoke-Sqlcmd writes DataRow objects to the PowerShell pipeline.  Returning
    that pipeline directly is unsafe: a single DataRow is assigned as a scalar,
    and indexing it can address a column instead of a row.  Accumulate complete
    row objects in a List, then return its Object[] with unary comma so
    PowerShell 5.1 and PowerShell 7 assign one predictable System.Object[]
    collection for zero, one, and multiple rows.  This preserves complete
    DataRow objects without enumerating their columns.  A terminating
    Invoke-Sqlcmd error is not converted into an empty collection; it still
    propagates to the caller.
    #>
    try {
        $rows = New-Object 'System.Collections.Generic.List[object]'
        foreach ($result in @(& $SqlCmdCommand @parameters)) {
            if ($null -ne $result) {
                [void]$rows.Add($result)
            }
        }
        return ,($rows.ToArray())
    } catch {
        throw ("SQL query '{0}' execution failed: {1}" -f $StatementId, (Get-SanitizedSqlError $_))
    }
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

function Test-SqlNullValue([object] $Value) {
    return ($null -eq $Value -or $Value -is [System.DBNull])
}

function Get-SqlFieldValue([object] $Row, [string] $StatementId, [string] $FieldName) {
    if ($null -eq $Row) {
        throw ("SQL query '{0}' returned no row while reading field '{1}'" -f $StatementId, $FieldName)
    }
    $property = $Row.PSObject.Properties[$FieldName]
    if ($null -eq $property) {
        throw ("SQL query '{0}' returned a row without field '{1}'" -f $StatementId, $FieldName)
    }
    return $property.Value
}

function Convert-SqlRequiredInt64([object] $Value, [string] $StatementId, [string] $FieldName) {
    if (Test-SqlNullValue $Value) {
        throw ("SQL query '{0}' returned SQL NULL for required field '{1}'" -f $StatementId, $FieldName)
    }
    $parsed = [decimal]0
    $valid = $true
    try {
        if ($Value -is [bool]) {
            $valid = $false
        } elseif ($Value -is [string]) {
            $text = $Value.Trim()
            $valid = -not [string]::IsNullOrWhiteSpace($text) -and
                [decimal]::TryParse(
                    $text,
                    [System.Globalization.NumberStyles]::Integer,
                    [System.Globalization.CultureInfo]::InvariantCulture,
                    [ref]$parsed
                )
        } elseif ($Value -is [System.IConvertible]) {
            $parsed = [decimal]$Value
        } else {
            $valid = $false
        }
    } catch {
        $valid = $false
    }
    $minimum = [decimal]([int64]::MinValue)
    $maximum = [decimal]([int64]::MaxValue)
    if (-not $valid -or [decimal]::Truncate($parsed) -ne $parsed -or $parsed -lt $minimum -or $parsed -gt $maximum) {
        throw ("SQL query '{0}' returned malformed integer field '{1}'" -f $StatementId, $FieldName)
    }
    try {
        return [int64]$parsed
    } catch {
        throw ("SQL query '{0}' returned out-of-range integer field '{1}'" -f $StatementId, $FieldName)
    }
}

function Convert-SqlNullableInt64([object] $Value, [string] $StatementId, [string] $FieldName) {
    if (Test-SqlNullValue $Value) {
        return $null
    }
    return Convert-SqlRequiredInt64 $Value $StatementId $FieldName
}

function Convert-SqlRequiredInt32([object] $Value, [string] $StatementId, [string] $FieldName) {
    $parsed = Convert-SqlRequiredInt64 $Value $StatementId $FieldName
    if ($parsed -lt [int32]::MinValue -or $parsed -gt [int32]::MaxValue) {
        throw ("SQL query '{0}' returned out-of-range 32-bit integer field '{1}'" -f $StatementId, $FieldName)
    }
    return [int32]$parsed
}

function Convert-SqlNullableInt32([object] $Value, [string] $StatementId, [string] $FieldName) {
    if (Test-SqlNullValue $Value) {
        return $null
    }
    return Convert-SqlRequiredInt32 $Value $StatementId $FieldName
}

function Convert-SqlRequiredDecimal([object] $Value, [string] $StatementId, [string] $FieldName) {
    if (Test-SqlNullValue $Value) {
        throw ("SQL query '{0}' returned SQL NULL for required field '{1}'" -f $StatementId, $FieldName)
    }
    $parsed = [decimal]0
    $valid = $true
    try {
        if ($Value -is [bool]) {
            $valid = $false
        } elseif ($Value -is [string]) {
            $text = $Value.Trim()
            $valid = -not [string]::IsNullOrWhiteSpace($text) -and
                [decimal]::TryParse(
                    $text,
                    [System.Globalization.NumberStyles]::Float,
                    [System.Globalization.CultureInfo]::InvariantCulture,
                    [ref]$parsed
                )
        } elseif ($Value -is [System.IConvertible]) {
            $parsed = [decimal]$Value
        } else {
            $valid = $false
        }
    } catch {
        $valid = $false
    }
    if (-not $valid) {
        throw ("SQL query '{0}' returned malformed decimal field '{1}'" -f $StatementId, $FieldName)
    }
    return $parsed
}

function Convert-SqlRequiredString([object] $Value, [string] $StatementId, [string] $FieldName) {
    if (Test-SqlNullValue $Value) {
        throw ("SQL query '{0}' returned SQL NULL for required field '{1}'" -f $StatementId, $FieldName)
    }
    if ($Value -isnot [string] -or [string]::IsNullOrWhiteSpace([string]$Value)) {
        throw ("SQL query '{0}' returned malformed string field '{1}'" -f $StatementId, $FieldName)
    }
    return [string]$Value
}

function Convert-SqlNullableString([object] $Value, [string] $StatementId, [string] $FieldName) {
    if (Test-SqlNullValue $Value) {
        return $null
    }
    if ($Value -isnot [string]) {
        throw ("SQL query '{0}' returned malformed string field '{1}'" -f $StatementId, $FieldName)
    }
    return [string]$Value
}

function Convert-SqlRequiredBoolean([object] $Value, [string] $StatementId, [string] $FieldName) {
    if (Test-SqlNullValue $Value) {
        throw ("SQL query '{0}' returned SQL NULL for required field '{1}'" -f $StatementId, $FieldName)
    }
    if ($Value -is [bool]) {
        return [bool]$Value
    }
    if ($Value -is [string]) {
        switch ($Value.Trim().ToUpperInvariant()) {
            "TRUE" { return $true }
            "YES" { return $true }
            "1" { return $true }
            "FALSE" { return $false }
            "NO" { return $false }
            "0" { return $false }
        }
    }
    try {
        $parsed = Convert-SqlRequiredInt64 $Value $StatementId $FieldName
        if ($parsed -eq 1) { return $true }
        if ($parsed -eq 0) { return $false }
    } catch {
        # Replace conversion details with the stable query/field diagnostic below.
    }
    throw ("SQL query '{0}' returned malformed Boolean field '{1}'" -f $StatementId, $FieldName)
}

function Convert-SqlNullableBoolean([object] $Value, [string] $StatementId, [string] $FieldName) {
    if (Test-SqlNullValue $Value) {
        return $null
    }
    return Convert-SqlRequiredBoolean $Value $StatementId $FieldName
}

function Get-SqlRequiredInt64([object] $Row, [string] $StatementId, [string] $FieldName) {
    return Convert-SqlRequiredInt64 (Get-SqlFieldValue $Row $StatementId $FieldName) $StatementId $FieldName
}

function Get-SqlNullableInt64([object] $Row, [string] $StatementId, [string] $FieldName) {
    return Convert-SqlNullableInt64 (Get-SqlFieldValue $Row $StatementId $FieldName) $StatementId $FieldName
}

function Get-SqlRequiredInt32([object] $Row, [string] $StatementId, [string] $FieldName) {
    return Convert-SqlRequiredInt32 (Get-SqlFieldValue $Row $StatementId $FieldName) $StatementId $FieldName
}

function Get-SqlNullableInt32([object] $Row, [string] $StatementId, [string] $FieldName) {
    return Convert-SqlNullableInt32 (Get-SqlFieldValue $Row $StatementId $FieldName) $StatementId $FieldName
}

function Get-SqlRequiredDecimal([object] $Row, [string] $StatementId, [string] $FieldName) {
    return Convert-SqlRequiredDecimal (Get-SqlFieldValue $Row $StatementId $FieldName) $StatementId $FieldName
}

function Get-SqlRequiredString([object] $Row, [string] $StatementId, [string] $FieldName) {
    return Convert-SqlRequiredString (Get-SqlFieldValue $Row $StatementId $FieldName) $StatementId $FieldName
}

function Get-SqlNullableString([object] $Row, [string] $StatementId, [string] $FieldName) {
    return Convert-SqlNullableString (Get-SqlFieldValue $Row $StatementId $FieldName) $StatementId $FieldName
}

function Get-SqlRequiredBoolean([object] $Row, [string] $StatementId, [string] $FieldName) {
    return Convert-SqlRequiredBoolean (Get-SqlFieldValue $Row $StatementId $FieldName) $StatementId $FieldName
}

function Get-SqlNullableBoolean([object] $Row, [string] $StatementId, [string] $FieldName) {
    return Convert-SqlNullableBoolean (Get-SqlFieldValue $Row $StatementId $FieldName) $StatementId $FieldName
}

function Convert-SqlRequiredSourceText([object] $Value, [string] $StatementId, [string] $FieldName) {
    if (Test-SqlNullValue $Value) {
        throw ("SQL query '{0}' returned SQL NULL for required field '{1}'" -f $StatementId, $FieldName)
    }
    if (($Value -isnot [string]) -and ($Value -isnot [DateTime]) -and ($Value -isnot [DateTimeOffset])) {
        throw ("SQL query '{0}' returned malformed source field '{1}'" -f $StatementId, $FieldName)
    }
    if ([string]::IsNullOrWhiteSpace([string]$Value)) {
        throw ("SQL query '{0}' returned an empty required field '{1}'" -f $StatementId, $FieldName)
    }
    return [string]$Value
}

function Resolve-PermissionState([string] $MetadataState, [bool] $AuthoritativeAccessProven) {
    if ($MetadataState -eq "UNKNOWN" -and $AuthoritativeAccessProven) {
        return "GRANTED"
    }
    return $MetadataState
}

function Get-PermissionState($Value) {
    if (Test-SqlNullValue $Value) {
        return "UNKNOWN"
    }
    try {
        $parsed = Convert-SqlNullableInt32 $Value "admin.permissions" "permission_metadata"
    } catch {
        return "UNKNOWN"
    }
    if ($null -eq $parsed) {
        return "UNKNOWN"
    }
    if ($parsed -eq 1) {
        return "GRANTED"
    }
    if ($parsed -eq 0) {
        return "DENIED"
    }
    return "UNKNOWN"
}

function Convert-SqlUtcDateTimeToIso8601([object] $Value, [string] $StatementId = "admin.read_only_query", [string] $FieldName = "timestamp") {
    <#
    SYSUTCDATETIME() returns SQL datetime2, which has no timezone metadata.
    Its clock fields are already UTC.  Assign UTC to those fields explicitly;
    never route an unspecified DateTime through the guest's local timezone.
    #>
    if (Test-SqlNullValue $Value) {
        throw ("SQL query '{0}' returned SQL NULL for required timestamp field '{1}'" -f $StatementId, $FieldName)
    }
    try {
        if ($Value -is [DateTimeOffset]) {
            $utcDateTime = [DateTime]::SpecifyKind($Value.DateTime, [DateTimeKind]::Utc)
            $utcValue = New-Object -TypeName System.DateTimeOffset -ArgumentList $utcDateTime
        } elseif ($Value -is [DateTime]) {
            $utcDateTime = [DateTime]::SpecifyKind([DateTime]$Value, [DateTimeKind]::Utc)
            $utcValue = New-Object -TypeName System.DateTimeOffset -ArgumentList $utcDateTime
        } elseif ($Value -is [string]) {
            $parsedDateTime = [DateTime]::Parse(
                [string]$Value,
                [System.Globalization.CultureInfo]::InvariantCulture,
                [System.Globalization.DateTimeStyles]::AllowWhiteSpaces
            )
            $utcDateTime = [DateTime]::SpecifyKind($parsedDateTime, [DateTimeKind]::Utc)
            $utcValue = New-Object -TypeName System.DateTimeOffset -ArgumentList $utcDateTime
        } else {
            throw "unsupported timestamp type"
        }
    } catch {
        throw ("SQL query '{0}' returned malformed UTC timestamp field '{1}'" -f $StatementId, $FieldName)
    }
    return $utcValue.ToString("yyyy-MM-dd'T'HH:mm:ss.fffffff'Z'", [System.Globalization.CultureInfo]::InvariantCulture)
}

function Convert-NullableSqlUtcDateTimeToIso8601([object] $Value, [string] $StatementId = "admin.read_only_query", [string] $FieldName = "timestamp") {
    if (Test-SqlNullValue $Value) {
        return $null
    }
    return Convert-SqlUtcDateTimeToIso8601 $Value $StatementId $FieldName
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
        # Invoke-ReadOnlySql returns one non-enumerated System.Object[] row
        # collection.  Do not wrap the invocation in @(), which would turn it
        # into a list-of-list.
        $rows = Invoke-ReadOnlySql $Query $StatementId
        $requiredColumns = switch ($StatementId) {
            "collector.production_cutover_history" { @("CutoverKey", "PanelVersion", "Tiered50ActivationAtUtc", "Tiered50ActivationStatus", "RecordedAtUtc"); break }
            "collector.historical_runs" { @("MacCollectorRunId", "RunId", "LogicalDueBucket", "CollectionContext", "StartedAtUtc", "CompletedAtUtc", "Status"); break }
            "collector.historical_target_audits" { @("AuditId", "RunId", "TargetIdentity", "LogicalDueBucket", "CollectionContext", "Status", "HttpAttempts", "StartedAtUtc", "CompletedAtUtc"); break }
            "collector.http_attempt_history" { @("HttpAttemptId", "AuditId", "AttemptNumber", "StartedAtUtc", "CompletedAtUtc", "HttpStatus", "Outcome", "NetworkAttempted", "HttpResponseReceived", "ProviderQuotaStatus", "JoinedAuditId", "RunId", "TargetIdentity", "LogicalDueBucket", "CollectionContext"); break }
            "collector.tiered50_first_heartbeat" { @("MacCollectorRunId", "RunId", "LogicalDueBucket", "StartedAtUtc", "CompletedAtUtc", "Status"); break }
            "collector.tiered50_first_heartbeat_target_audits" { @("AuditId", "RunId", "TargetIdentity", "LogicalDueBucket", "CollectionContext", "Status", "HttpAttempts", "StartedAtUtc", "CompletedAtUtc"); break }
            "collector.tiered50_first_heartbeat_http_attempts" { @("HttpAttemptId", "AuditId", "AttemptNumber", "StartedAtUtc", "CompletedAtUtc", "HttpStatus", "Outcome", "NetworkAttempted", "HttpResponseReceived", "ProviderQuotaStatus", "JoinedAuditId", "RunId", "TargetIdentity", "LogicalDueBucket", "CollectionContext"); break }
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
    $identityQuery = @"
SELECT CAST(SERVERPROPERTY('ServerName') AS nvarchar(128)) AS ServerName,
       CAST(SERVERPROPERTY('MachineName') AS nvarchar(128)) AS MachineName,
       CAST(SERVERPROPERTY('InstanceName') AS nvarchar(128)) AS InstanceName,
       CAST(@@SERVERNAME AS nvarchar(128)) AS ConfiguredServerName,
       DB_NAME() AS DatabaseName,
       SUSER_SNAME() AS LoginName, SYSUTCDATETIME() AS CapturedAtUtc,
       CAST(SERVERPROPERTY('ProductMajorVersion') AS INT) AS ProductMajorVersion;
"@
    $identity = Invoke-ReadOnlySql $identityQuery "admin.identity"
} catch {
    $sanitizedSqlError = Get-SanitizedSqlError $_
    if ($sanitizedSqlError -match '(?i)certificate|trust|ssl|tls|hostname') {
        throw ("SQL certificate validation failed while TrustServerCertificate=False; " + $sanitizedSqlError)
    }
    throw ("Encrypted SQL connection failed before identity was proven; " + $sanitizedSqlError)
}

if ($identity.Count -ne 1) {
    throw "SQL identity/database binding was not proven"
}
$identityDatabaseName = Get-SqlRequiredString $identity[0] "admin.identity" "DatabaseName"
$identityLoginName = Get-SqlRequiredString $identity[0] "admin.identity" "LoginName"
if ($identityDatabaseName -ne $Database) {
    throw "SQL identity/database binding was not proven"
}
if ($identityLoginName -eq $RuntimeLoginName) {
    throw "Windows evidence identity must be separate from the limited runtime login"
}

$captured = Convert-SqlUtcDateTimeToIso8601 (Get-SqlFieldValue $identity[0] "admin.identity" "CapturedAtUtc") "admin.identity" "CapturedAtUtc"
$identityServerName = Get-SqlRequiredString $identity[0] "admin.identity" "ServerName"
$identityMachineName = Get-SqlRequiredString $identity[0] "admin.identity" "MachineName"
$identityInstanceName = Get-SqlNullableString $identity[0] "admin.identity" "InstanceName"
$identityConfiguredServerName = Get-SqlRequiredString $identity[0] "admin.identity" "ConfiguredServerName"

$transportQuery = @"
SELECT CAST(encrypt_option AS nvarchar(10)) AS EncryptOption,
       CAST(auth_scheme AS nvarchar(128)) AS AuthScheme,
       CAST(net_transport AS nvarchar(128)) AS NetTransport
FROM sys.dm_exec_connections
WHERE session_id = @@SPID;
"@
$transport = Invoke-ReadOnlySql $transportQuery "admin.transport"
if ($transport.Count -ne 1) {
    throw "SQL connection transport security was not returned for the evidence session"
}
$transportEncryptOption = Get-SqlRequiredString $transport[0] "admin.transport" "EncryptOption"
$transportAuthScheme = Get-SqlRequiredString $transport[0] "admin.transport" "AuthScheme"
$transportNetTransport = Get-SqlRequiredString $transport[0] "admin.transport" "NetTransport"
$actualEncryptOption = $transportEncryptOption.Trim().ToUpperInvariant()
if ($actualEncryptOption -notin @("TRUE", "YES", "1")) {
    throw "SQL connection did not prove encrypted transport; no administrative evidence was written"
}
$authScheme = $transportAuthScheme.Trim().ToUpperInvariant()
if ($authScheme -notin @("KERBEROS", "NTLM")) {
    throw "SQL connection did not prove Windows Authentication; no administrative evidence was written"
}
$tlsEvidence = [ordered]@{
    encryption_mode = $EncryptMode
    actual_encryption = $transportEncryptOption.Trim()
    actual_transport = "ENCRYPTED"
    trust_server_certificate = $false
    certificate_validation_required = $true
    certificate_validation_observed = $true
    certificate_validation_status = "VALIDATED"
    certificate_validation_method = "INVOKE_SQLCMD_TRUST_SERVER_CERTIFICATE_FALSE_AND_DM_EXEC_CONNECTIONS_ENCRYPT_OPTION_TRUE"
    auth_scheme = $transportAuthScheme.Trim()
    net_transport = $transportNetTransport.Trim()
}

$permissionsQuery = @"
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
$permissions = Invoke-ReadOnlySql $permissionsQuery "admin.permissions"

if ($permissions.Count -ne 1) {
    throw "SQL permission inspection did not return exactly one row"
}
$productMajorVersion = Get-SqlRequiredInt32 $identity[0] "admin.identity" "ProductMajorVersion"
$permissionMetadata = [ordered]@{
    HasViewDefinition = Get-SqlFieldValue $permissions[0] "admin.permissions" "HasViewDefinition"
    HasViewDatabaseState = Get-SqlFieldValue $permissions[0] "admin.permissions" "HasViewDatabaseState"
    HasViewServerState = Get-SqlFieldValue $permissions[0] "admin.permissions" "HasViewServerState"
    HasViewServerPerformanceState = Get-SqlFieldValue $permissions[0] "admin.permissions" "HasViewServerPerformanceState"
    HasBackupsetSelect = Get-SqlFieldValue $permissions[0] "admin.permissions" "HasBackupsetSelect"
    HasBackupmediafamilySelect = Get-SqlFieldValue $permissions[0] "admin.permissions" "HasBackupmediafamilySelect"
    HasDatabaseSelect = Get-SqlFieldValue $permissions[0] "admin.permissions" "HasDatabaseSelect"
    HasCreateAnyDatabase = Get-SqlFieldValue $permissions[0] "admin.permissions" "HasCreateAnyDatabase"
    IsSysadmin = Get-SqlFieldValue $permissions[0] "admin.permissions" "IsSysadmin"
    IsDbcreator = Get-SqlFieldValue $permissions[0] "admin.permissions" "IsDbcreator"
}
$permissionMetadataStates = [ordered]@{
    database_view_definition = Get-PermissionState $permissionMetadata.HasViewDefinition
    database_view_state = Get-PermissionState $permissionMetadata.HasViewDatabaseState
    server_view_state = Get-PermissionState $permissionMetadata.HasViewServerState
    server_view_performance_state = Get-PermissionState $permissionMetadata.HasViewServerPerformanceState
    backup_history_backupset_select = Get-PermissionState $permissionMetadata.HasBackupsetSelect
    backup_history_backupmediafamily_select = Get-PermissionState $permissionMetadata.HasBackupmediafamilySelect
    operational_history_database_select = Get-PermissionState $permissionMetadata.HasDatabaseSelect
    create_any_database = Get-PermissionState $permissionMetadata.HasCreateAnyDatabase
    sysadmin = Get-PermissionState $permissionMetadata.IsSysadmin
    dbcreator = Get-PermissionState $permissionMetadata.IsDbcreator
}
$serverDmvMetadataState = if ($productMajorVersion -ge 16) {
    $permissionMetadataStates.server_view_performance_state
} else {
    $permissionMetadataStates.server_view_state
}
$backupVerifyPermissionState = if (
    @(
        $permissionMetadataStates.create_any_database,
        $permissionMetadataStates.sysadmin,
        $permissionMetadataStates.dbcreator
    ) -contains "GRANTED"
) {
    "GRANTED"
} elseif (
    @(
        $permissionMetadataStates.create_any_database,
        $permissionMetadataStates.sysadmin,
        $permissionMetadataStates.dbcreator
    ) -notcontains "UNKNOWN" -and
    @(
        $permissionMetadataStates.create_any_database,
        $permissionMetadataStates.sysadmin,
        $permissionMetadataStates.dbcreator
    ) -contains "DENIED"
) {
    "DENIED"
} else {
    "UNKNOWN"
}
$permissionChecks = [ordered]@{
    database_view_definition = $permissionMetadataStates.database_view_definition
    database_view_state = $permissionMetadataStates.database_view_state
    server_dmv_state = $serverDmvMetadataState
    backup_history_backupset_select = $permissionMetadataStates.backup_history_backupset_select
    backup_history_backupmediafamily_select = $permissionMetadataStates.backup_history_backupmediafamily_select
    operational_history_database_select = $permissionMetadataStates.operational_history_database_select
    backup_verifyonly_authorization = $backupVerifyPermissionState
}

$databaseFiles = Invoke-ReadOnlySql "SELECT name, type_desc, size, growth, is_percent_growth, max_size, FILEPROPERTY(name, 'SpaceUsed') AS space_used_pages, physical_name FROM sys.database_files ORDER BY file_id;" "capacity.database_files"
$databaseFileDetails = @($databaseFiles | ForEach-Object {
    $sizePages = Get-SqlRequiredInt64 $_ "capacity.database_files" "size"
    $spaceUsedPages = Get-SqlNullableInt64 $_ "capacity.database_files" "space_used_pages"
    @{
        name = Get-SqlRequiredString $_ "capacity.database_files" "name"
        type = Get-SqlRequiredString $_ "capacity.database_files" "type_desc"
        size_pages = $sizePages
        growth = Get-SqlRequiredInt32 $_ "capacity.database_files" "growth"
        is_percent_growth = Get-SqlRequiredBoolean $_ "capacity.database_files" "is_percent_growth"
        max_size_pages = Get-SqlRequiredInt64 $_ "capacity.database_files" "max_size"
        space_used_pages = $spaceUsedPages
        size_bytes = [int64]($sizePages * 8192)
        space_used_bytes = if ($null -eq $spaceUsedPages) { $null } else { [int64]($spaceUsedPages * 8192) }
        physical_name = Get-SqlRequiredString $_ "capacity.database_files" "physical_name"
    }
})
$runtimeHealth = Invoke-ReadOnlySql "SELECT DB_NAME() AS database_name, DATABASEPROPERTYEX(DB_NAME(), 'Status') AS database_status, DATABASEPROPERTYEX(DB_NAME(), 'Recovery') AS recovery_model;" "admin.sql_runtime_health"
$volumes = Invoke-ReadOnlySql "SELECT DISTINCT vs.volume_mount_point, vs.total_bytes, vs.available_bytes FROM sys.database_files AS df CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs ORDER BY vs.volume_mount_point;" "capacity.volume_capacity_dm_os_volume_stats"
$logSpace = Invoke-ReadOnlySql "SELECT total_log_size_in_bytes, used_log_space_in_bytes, used_log_space_in_percent FROM sys.dm_db_log_space_usage WHERE database_id = DB_ID();" "capacity.database_log_space_dm_db_log_space_usage"
$backup = Invoke-ReadOnlySql "SELECT TOP (1) bs.backup_set_id, bs.media_set_id, bs.backup_finish_date, bs.is_copy_only, bs.has_backup_checksums, bs.compressed_backup_size FROM msdb.dbo.backupset AS bs WHERE bs.database_name = DB_NAME() AND bs.type = 'D' ORDER BY bs.backup_finish_date DESC;" "backup.latest_full_backup_history"
$materialization = Invoke-ReadOnlySql "SELECT COUNT_BIG(*) AS TargetCount, COALESCE(SUM(CASE WHEN IsEnabled = 1 THEN 1 ELSE 0 END), 0) AS ActiveCount FROM ctl.MddMacTiered50ProductionTarget WHERE PanelVersion = 'TIERED50_PANEL_V1' AND SamplingEra = 'Tiered50';" "collector_runtime_history_or_materialization"
$runtimeHealth = @($runtimeHealth | ForEach-Object {
    [pscustomobject]@{
        database_name = Get-SqlRequiredString $_ "admin.sql_runtime_health" "database_name"
        database_status = Get-SqlRequiredString $_ "admin.sql_runtime_health" "database_status"
        recovery_model = Get-SqlRequiredString $_ "admin.sql_runtime_health" "recovery_model"
    }
})
$volumes = @($volumes | ForEach-Object {
    [pscustomobject]@{
        volume_mount_point = Get-SqlRequiredString $_ "capacity.volume_capacity_dm_os_volume_stats" "volume_mount_point"
        total_bytes = Get-SqlRequiredInt64 $_ "capacity.volume_capacity_dm_os_volume_stats" "total_bytes"
        available_bytes = Get-SqlRequiredInt64 $_ "capacity.volume_capacity_dm_os_volume_stats" "available_bytes"
    }
})
$logSpace = @($logSpace | ForEach-Object {
    [pscustomobject]@{
        total_log_size_in_bytes = Get-SqlRequiredInt64 $_ "capacity.database_log_space_dm_db_log_space_usage" "total_log_size_in_bytes"
        used_log_space_in_bytes = Get-SqlRequiredInt64 $_ "capacity.database_log_space_dm_db_log_space_usage" "used_log_space_in_bytes"
        used_log_space_in_percent = Get-SqlRequiredDecimal $_ "capacity.database_log_space_dm_db_log_space_usage" "used_log_space_in_percent"
    }
})
$backup = @($backup | ForEach-Object {
    [pscustomobject]@{
        backup_set_id = Get-SqlRequiredInt64 $_ "backup.latest_full_backup_history" "backup_set_id"
        media_set_id = Get-SqlRequiredInt64 $_ "backup.latest_full_backup_history" "media_set_id"
        backup_finish_date = Convert-SqlRequiredSourceText (Get-SqlFieldValue $_ "backup.latest_full_backup_history" "backup_finish_date") "backup.latest_full_backup_history" "backup_finish_date"
        is_copy_only = Get-SqlRequiredBoolean $_ "backup.latest_full_backup_history" "is_copy_only"
        has_backup_checksums = Get-SqlRequiredBoolean $_ "backup.latest_full_backup_history" "has_backup_checksums"
        compressed_backup_size = Get-SqlRequiredInt64 $_ "backup.latest_full_backup_history" "compressed_backup_size"
    }
})
$materialization = @($materialization | ForEach-Object {
    [pscustomobject]@{
        TargetCount = Get-SqlRequiredInt64 $_ "collector_runtime_history_or_materialization" "TargetCount"
        ActiveCount = Get-SqlRequiredInt64 $_ "collector_runtime_history_or_materialization" "ActiveCount"
    }
})
$backupMediaQueryExecuted = $false
$backupMedia = if ($backup.Count -eq 1) {
    $backupMediaQueryExecuted = $true
    $mediaSetId = [int64]$backup[0].media_set_id
    $mediaRows = Invoke-ReadOnlySql ("SELECT family_sequence_number, physical_device_name FROM msdb.dbo.backupmediafamily WHERE media_set_id = {0} ORDER BY family_sequence_number;" -f $mediaSetId) "backup.latest_full_backup_media_set"
    @($mediaRows | ForEach-Object {
        [pscustomobject]@{
            family_sequence_number = Get-SqlRequiredInt64 $_ "backup.latest_full_backup_media_set" "family_sequence_number"
            physical_device_name = Get-SqlRequiredString $_ "backup.latest_full_backup_media_set" "physical_device_name"
        }
    })
} else { @() }
$cutoverHistoryQuery = Invoke-AuditedReadOnlySql "collector.production_cutover_history" "SELECT CutoverKey, PanelVersion, Tiered50ActivationAtUtc, Tiered50ActivationStatus, RecordedAtUtc FROM ctl.MddMacProductionCutover WHERE PanelVersion = 'TIERED50_PANEL_V1' ORDER BY CutoverId;"
$cutoverHistory = @($cutoverHistoryQuery.Rows | ForEach-Object {
    [ordered]@{
        cutover_key = Get-SqlRequiredString $_ "collector.production_cutover_history" "CutoverKey"
        panel_version = Get-SqlRequiredString $_ "collector.production_cutover_history" "PanelVersion"
        tiered50_activation_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 (Get-SqlFieldValue $_ "collector.production_cutover_history" "Tiered50ActivationAtUtc") "collector.production_cutover_history" "Tiered50ActivationAtUtc"
        tiered50_activation_status = Get-SqlRequiredString $_ "collector.production_cutover_history" "Tiered50ActivationStatus"
        recorded_at_utc = Convert-SqlUtcDateTimeToIso8601 (Get-SqlFieldValue $_ "collector.production_cutover_history" "RecordedAtUtc") "collector.production_cutover_history" "RecordedAtUtc"
    }
})
$cutoverHistoryEvidence = $cutoverHistoryQuery.Evidence
$competingWriters = Invoke-ReadOnlySql "SELECT CollectionContext, COUNT_BIG(*) AS RunningCount FROM ctl.MddMacCollectorRun WHERE Status IN ('RUNNING', 'CLAIMED') AND CollectionContext IN ('MAC_LEGACY7_PRODUCTION', 'MAC_TIERED50_PRODUCTION') GROUP BY CollectionContext;" "collector.competing_production_writers"
$competingWriters = @($competingWriters | ForEach-Object {
    [pscustomobject]@{
        CollectionContext = Get-SqlRequiredString $_ "collector.competing_production_writers" "CollectionContext"
        RunningCount = Get-SqlRequiredInt64 $_ "collector.competing_production_writers" "RunningCount"
    }
})
$currentQuotaLedger = Invoke-ReadOnlySql "SELECT TOP (2) MonthKey, ProviderQuotaCeiling, NormalOperationalMaximum, RetryProtectionBoundary, ConfirmedProviderAttempts, ConservativeUnknownAttempts, ExplicitSharedConsumerReservedAttempts, AccountingStatus, IncludesAllSharedConsumers, ProviderPeriodStartUtc, ProviderPeriodEndUtc, LocalUsageIncludedThroughUtc, SourceEvidenceHash, ReconciliationKey FROM ctl.MddMacSharedQuotaAccounting WHERE ProviderPeriodStartUtc <= SYSUTCDATETIME() AND ProviderPeriodEndUtc > SYSUTCDATETIME() ORDER BY ProviderPeriodStartUtc DESC, RecordedAtUtc DESC;" "quota.current_shared_ledger"
$currentQuotaLedger = @($currentQuotaLedger | ForEach-Object {
    [ordered]@{
        month_key = Get-SqlRequiredString $_ "quota.current_shared_ledger" "MonthKey"
        provider_quota_ceiling = Get-SqlRequiredInt64 $_ "quota.current_shared_ledger" "ProviderQuotaCeiling"
        normal_operational_maximum = Get-SqlRequiredInt64 $_ "quota.current_shared_ledger" "NormalOperationalMaximum"
        retry_protection_boundary = Get-SqlRequiredInt64 $_ "quota.current_shared_ledger" "RetryProtectionBoundary"
        confirmed_provider_attempts = Get-SqlRequiredInt64 $_ "quota.current_shared_ledger" "ConfirmedProviderAttempts"
        conservative_unknown_attempts = Get-SqlRequiredInt64 $_ "quota.current_shared_ledger" "ConservativeUnknownAttempts"
        reserved_shared_usage = Get-SqlRequiredInt64 $_ "quota.current_shared_ledger" "ExplicitSharedConsumerReservedAttempts"
        accounting_status = Get-SqlRequiredString $_ "quota.current_shared_ledger" "AccountingStatus"
        includes_all_shared_consumers = Get-SqlRequiredBoolean $_ "quota.current_shared_ledger" "IncludesAllSharedConsumers"
        provider_period_start_utc = Convert-NullableSqlUtcDateTimeToIso8601 (Get-SqlFieldValue $_ "quota.current_shared_ledger" "ProviderPeriodStartUtc") "quota.current_shared_ledger" "ProviderPeriodStartUtc"
        provider_period_end_utc = Convert-NullableSqlUtcDateTimeToIso8601 (Get-SqlFieldValue $_ "quota.current_shared_ledger" "ProviderPeriodEndUtc") "quota.current_shared_ledger" "ProviderPeriodEndUtc"
        local_usage_included_through_utc = Convert-SqlUtcDateTimeToIso8601 (Get-SqlFieldValue $_ "quota.current_shared_ledger" "LocalUsageIncludedThroughUtc") "quota.current_shared_ledger" "LocalUsageIncludedThroughUtc"
        source_evidence_hash = Get-SqlNullableString $_ "quota.current_shared_ledger" "SourceEvidenceHash"
        reconciliation_key = Get-SqlNullableString $_ "quota.current_shared_ledger" "ReconciliationKey"
    }
})
$quotaSourceStatementId = "quota.current_shared_ledger"
$quotaQuery = @"
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
$quota = Invoke-ReadOnlySql $quotaQuery "quota.current_shared_ledger"

# A successful DMV query is effective permission evidence.  SQL Server 2025
# can return NULL for a metadata permission function even for an authorized
# sysadmin; do not reject that identity when the protected DMV reads actually
# complete under the same Windows Authentication context.
if ($databaseFiles.Count -gt 0) {
    $permissionChecks.database_view_definition = Resolve-PermissionState $permissionChecks.database_view_definition $true
}
if ($logSpace.Count -eq 1) {
    $permissionChecks.database_view_state = Resolve-PermissionState $permissionChecks.database_view_state $true
}
if ($volumes.Count -gt 0 -and $logSpace.Count -eq 1) {
    $permissionChecks.server_dmv_state = Resolve-PermissionState $permissionChecks.server_dmv_state $true
}
$permissionChecks.backup_history_backupset_select = Resolve-PermissionState $permissionChecks.backup_history_backupset_select $true
if ($backupMediaQueryExecuted) {
    $permissionChecks.backup_history_backupmediafamily_select = Resolve-PermissionState $permissionChecks.backup_history_backupmediafamily_select $true
}
if ($quota.Count -eq 0) {
$quotaSourceStatementId = "quota.bounded_bootstrap"
$bootstrapQuotaQuery = @"
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
$quota = Invoke-ReadOnlySql $bootstrapQuotaQuery "quota.bounded_bootstrap"
}
$quota = @($quota | ForEach-Object {
    [pscustomobject]@{
        SourceKind = Get-SqlRequiredString $_ $quotaSourceStatementId "SourceKind"
        MonthKey = Get-SqlRequiredString $_ $quotaSourceStatementId "MonthKey"
        AccountingStatus = Get-SqlRequiredString $_ $quotaSourceStatementId "AccountingStatus"
        KnownTotal = Get-SqlRequiredInt64 $_ $quotaSourceStatementId "KnownTotal"
        ReservedSharedUsage = Get-SqlRequiredInt64 $_ $quotaSourceStatementId "ReservedSharedUsage"
    }
})

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
       a.AuditId AS JoinedAuditId, a.RunId, a.TargetIdentity,
       a.LogicalDueBucket, a.CollectionContext
FROM ctl.MddMacHttpAttempt AS h
LEFT JOIN ctl.MddMacTargetAudit AS a ON a.AuditId = h.AuditId
WHERE a.CollectionContext IN ('MAC_LEGACY7_PRODUCTION', 'MAC_TIERED50_PRODUCTION')
   OR a.AuditId IS NULL
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
if ($firstTiered50HeartbeatRows.Count -eq 1) {
    $firstTiered50RunId = Get-SqlRequiredInt64 $firstTiered50HeartbeatRows[0] "collector.tiered50_first_heartbeat" "MacCollectorRunId"
    $firstTiered50RunIdText = Get-SqlRequiredString $firstTiered50HeartbeatRows[0] "collector.tiered50_first_heartbeat" "RunId"
    $escapedFirstTiered50RunId = $firstTiered50RunIdText.Replace("'", "''")
$firstTiered50HeartbeatTargetAuditsQuery = Invoke-AuditedReadOnlySql "collector.tiered50_first_heartbeat_target_audits" (@"
SELECT AuditId, RunId, TargetIdentity, LogicalDueBucket,
       CollectionContext, Status, HttpAttempts, StartedAtUtc, CompletedAtUtc
FROM ctl.MddMacTargetAudit
WHERE RunId = N'{0}'
ORDER BY AuditId ASC;
"@ -f $escapedFirstTiered50RunId)
    $firstTiered50HeartbeatHttpAttemptsQuery = Invoke-AuditedReadOnlySql "collector.tiered50_first_heartbeat_http_attempts" (@"
SELECT h.HttpAttemptId, h.AuditId, h.AttemptNumber,
       h.StartedAtUtc, h.CompletedAtUtc, h.HttpStatus, h.Outcome,
       h.NetworkAttempted, h.HttpResponseReceived, h.ProviderQuotaStatus,
       a.AuditId AS JoinedAuditId, a.RunId, a.TargetIdentity,
       a.LogicalDueBucket, a.CollectionContext
FROM ctl.MddMacHttpAttempt AS h
LEFT JOIN ctl.MddMacTargetAudit AS a ON a.AuditId = h.AuditId
WHERE a.RunId = N'{0}' OR a.AuditId IS NULL
ORDER BY h.CompletedAtUtc ASC, h.HttpAttemptId ASC;
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
    $permissionChecks.operational_history_database_select = Resolve-PermissionState $permissionChecks.operational_history_database_select $true
}

if ($null -eq $backup -or $backup.Count -ne 1 -or $backupMedia.Count -lt 1 -or ($backupMedia | Where-Object { [string]::IsNullOrWhiteSpace([string]$_.physical_device_name) }).Count -gt 0) {
    throw "No native full backup path was available for server-side VERIFYONLY"
}
if ($databaseFiles.Count -eq 0 -or $runtimeHealth.Count -ne 1 -or $runtimeHealth[0].database_status -ne "ONLINE" -or [string]::IsNullOrWhiteSpace([string]$runtimeHealth[0].recovery_model) -or $volumes.Count -eq 0 -or $logSpace.Count -ne 1) {
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
Invoke-ReadOnlySql ("RESTORE VERIFYONLY FROM {0} WITH CHECKSUM;" -f ($backupMediaClauses -join ", ")) "backup.restore_verifyonly_with_checksum" | Out-Null
$permissionChecks.backup_verifyonly_authorization = Resolve-PermissionState $permissionChecks.backup_verifyonly_authorization $true

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
    $spaceUsedBytesUnknown = @($filesOnVolume | Where-Object { $null -eq $_.space_used_bytes }).Count -gt 0
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
        database_file_space_used_bytes = if ($spaceUsedBytesUnknown) { $null } else { [int64](($filesOnVolume | Measure-Object -Property space_used_bytes -Sum).Sum) }
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
        mac_collector_run_id = Get-SqlRequiredInt64 $_ "collector.historical_runs" "MacCollectorRunId"
        run_id = Get-SqlRequiredString $_ "collector.historical_runs" "RunId"
        logical_due_bucket = Get-SqlRequiredInt64 $_ "collector.historical_runs" "LogicalDueBucket"
        collection_context = Get-SqlRequiredString $_ "collector.historical_runs" "CollectionContext"
        started_at_utc = Convert-SqlUtcDateTimeToIso8601 (Get-SqlFieldValue $_ "collector.historical_runs" "StartedAtUtc") "collector.historical_runs" "StartedAtUtc"
        completed_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 (Get-SqlFieldValue $_ "collector.historical_runs" "CompletedAtUtc") "collector.historical_runs" "CompletedAtUtc"
        status = Get-SqlRequiredString $_ "collector.historical_runs" "Status"
    }
})
$historicalTargetAuditRows = @($historicalTargetAuditsQuery.Rows | ForEach-Object {
    [ordered]@{
        audit_id = Get-SqlRequiredInt64 $_ "collector.historical_target_audits" "AuditId"
        run_id = Get-SqlRequiredString $_ "collector.historical_target_audits" "RunId"
        target_identity = Get-SqlRequiredString $_ "collector.historical_target_audits" "TargetIdentity"
        logical_due_bucket = Get-SqlRequiredInt64 $_ "collector.historical_target_audits" "LogicalDueBucket"
        collection_context = Get-SqlRequiredString $_ "collector.historical_target_audits" "CollectionContext"
        status = Get-SqlRequiredString $_ "collector.historical_target_audits" "Status"
        http_attempts = Get-SqlRequiredInt32 $_ "collector.historical_target_audits" "HttpAttempts"
        started_at_utc = Convert-SqlUtcDateTimeToIso8601 (Get-SqlFieldValue $_ "collector.historical_target_audits" "StartedAtUtc") "collector.historical_target_audits" "StartedAtUtc"
        completed_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 (Get-SqlFieldValue $_ "collector.historical_target_audits" "CompletedAtUtc") "collector.historical_target_audits" "CompletedAtUtc"
    }
})
$httpAttemptRows = @($httpAttemptHistoryQuery.Rows | ForEach-Object {
    [ordered]@{
        http_attempt_id = Get-SqlRequiredInt64 $_ "collector.http_attempt_history" "HttpAttemptId"
        audit_id = Get-SqlRequiredInt64 $_ "collector.http_attempt_history" "AuditId"
        attempt_number = Get-SqlRequiredInt32 $_ "collector.http_attempt_history" "AttemptNumber"
        started_at_utc = Convert-SqlUtcDateTimeToIso8601 (Get-SqlFieldValue $_ "collector.http_attempt_history" "StartedAtUtc") "collector.http_attempt_history" "StartedAtUtc"
        completed_at_utc = Convert-SqlUtcDateTimeToIso8601 (Get-SqlFieldValue $_ "collector.http_attempt_history" "CompletedAtUtc") "collector.http_attempt_history" "CompletedAtUtc"
        http_status = Get-SqlNullableInt32 $_ "collector.http_attempt_history" "HttpStatus"
        outcome = Get-SqlRequiredString $_ "collector.http_attempt_history" "Outcome"
        network_attempted = Get-SqlRequiredBoolean $_ "collector.http_attempt_history" "NetworkAttempted"
        http_response_received = Get-SqlRequiredBoolean $_ "collector.http_attempt_history" "HttpResponseReceived"
        provider_quota_status = Get-SqlRequiredString $_ "collector.http_attempt_history" "ProviderQuotaStatus"
        joined_audit_id = Get-SqlNullableInt64 $_ "collector.http_attempt_history" "JoinedAuditId"
        run_id = Get-SqlNullableString $_ "collector.http_attempt_history" "RunId"
        target_identity = Get-SqlNullableString $_ "collector.http_attempt_history" "TargetIdentity"
        logical_due_bucket = Get-SqlNullableInt64 $_ "collector.http_attempt_history" "LogicalDueBucket"
        collection_context = Get-SqlNullableString $_ "collector.http_attempt_history" "CollectionContext"
    }
})
$firstHeartbeatRows = @($firstTiered50HeartbeatQuery.Rows | ForEach-Object {
    [ordered]@{
        mac_collector_run_id = Get-SqlRequiredInt64 $_ "collector.tiered50_first_heartbeat" "MacCollectorRunId"
        run_id = Get-SqlRequiredString $_ "collector.tiered50_first_heartbeat" "RunId"
        logical_due_bucket = Get-SqlRequiredInt64 $_ "collector.tiered50_first_heartbeat" "LogicalDueBucket"
        started_at_utc = Convert-SqlUtcDateTimeToIso8601 (Get-SqlFieldValue $_ "collector.tiered50_first_heartbeat" "StartedAtUtc") "collector.tiered50_first_heartbeat" "StartedAtUtc"
        completed_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 (Get-SqlFieldValue $_ "collector.tiered50_first_heartbeat" "CompletedAtUtc") "collector.tiered50_first_heartbeat" "CompletedAtUtc"
        status = Get-SqlRequiredString $_ "collector.tiered50_first_heartbeat" "Status"
    }
})
$firstHeartbeatTargetAuditRows = @($firstTiered50HeartbeatTargetAuditsQuery.Rows | ForEach-Object {
    [ordered]@{
        audit_id = Get-SqlRequiredInt64 $_ "collector.tiered50_first_heartbeat_target_audits" "AuditId"
        run_id = Get-SqlRequiredString $_ "collector.tiered50_first_heartbeat_target_audits" "RunId"
        target_identity = Get-SqlRequiredString $_ "collector.tiered50_first_heartbeat_target_audits" "TargetIdentity"
        logical_due_bucket = Get-SqlRequiredInt64 $_ "collector.tiered50_first_heartbeat_target_audits" "LogicalDueBucket"
        collection_context = Get-SqlRequiredString $_ "collector.tiered50_first_heartbeat_target_audits" "CollectionContext"
        status = Get-SqlRequiredString $_ "collector.tiered50_first_heartbeat_target_audits" "Status"
        http_attempts = Get-SqlRequiredInt32 $_ "collector.tiered50_first_heartbeat_target_audits" "HttpAttempts"
        started_at_utc = Convert-SqlUtcDateTimeToIso8601 (Get-SqlFieldValue $_ "collector.tiered50_first_heartbeat_target_audits" "StartedAtUtc") "collector.tiered50_first_heartbeat_target_audits" "StartedAtUtc"
        completed_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 (Get-SqlFieldValue $_ "collector.tiered50_first_heartbeat_target_audits" "CompletedAtUtc") "collector.tiered50_first_heartbeat_target_audits" "CompletedAtUtc"
    }
})
$firstHeartbeatHttpAttemptRows = @($firstTiered50HeartbeatHttpAttemptsQuery.Rows | ForEach-Object {
    [ordered]@{
        http_attempt_id = Get-SqlRequiredInt64 $_ "collector.tiered50_first_heartbeat_http_attempts" "HttpAttemptId"
        audit_id = Get-SqlRequiredInt64 $_ "collector.tiered50_first_heartbeat_http_attempts" "AuditId"
        attempt_number = Get-SqlRequiredInt32 $_ "collector.tiered50_first_heartbeat_http_attempts" "AttemptNumber"
        started_at_utc = Convert-SqlUtcDateTimeToIso8601 (Get-SqlFieldValue $_ "collector.tiered50_first_heartbeat_http_attempts" "StartedAtUtc") "collector.tiered50_first_heartbeat_http_attempts" "StartedAtUtc"
        completed_at_utc = Convert-SqlUtcDateTimeToIso8601 (Get-SqlFieldValue $_ "collector.tiered50_first_heartbeat_http_attempts" "CompletedAtUtc") "collector.tiered50_first_heartbeat_http_attempts" "CompletedAtUtc"
        http_status = Get-SqlNullableInt32 $_ "collector.tiered50_first_heartbeat_http_attempts" "HttpStatus"
        outcome = Get-SqlRequiredString $_ "collector.tiered50_first_heartbeat_http_attempts" "Outcome"
        network_attempted = Get-SqlRequiredBoolean $_ "collector.tiered50_first_heartbeat_http_attempts" "NetworkAttempted"
        http_response_received = Get-SqlRequiredBoolean $_ "collector.tiered50_first_heartbeat_http_attempts" "HttpResponseReceived"
        provider_quota_status = Get-SqlRequiredString $_ "collector.tiered50_first_heartbeat_http_attempts" "ProviderQuotaStatus"
        joined_audit_id = Get-SqlNullableInt64 $_ "collector.tiered50_first_heartbeat_http_attempts" "JoinedAuditId"
        run_id = Get-SqlNullableString $_ "collector.tiered50_first_heartbeat_http_attempts" "RunId"
        target_identity = Get-SqlNullableString $_ "collector.tiered50_first_heartbeat_http_attempts" "TargetIdentity"
        logical_due_bucket = Get-SqlNullableInt64 $_ "collector.tiered50_first_heartbeat_http_attempts" "LogicalDueBucket"
        collection_context = Get-SqlNullableString $_ "collector.tiered50_first_heartbeat_http_attempts" "CollectionContext"
    }
})
$historicalOutageRows = @($historicalOutageQuery.Rows | ForEach-Object {
    [ordered]@{
        cutover_id = Get-SqlRequiredInt64 $_ "collector.historical_outage" "CutoverId"
        cutover_key = Get-SqlRequiredString $_ "collector.historical_outage" "CutoverKey"
        panel_version = Get-SqlRequiredString $_ "collector.historical_outage" "PanelVersion"
        tiered50_activation_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 (Get-SqlFieldValue $_ "collector.historical_outage" "Tiered50ActivationAtUtc") "collector.historical_outage" "Tiered50ActivationAtUtc"
        tiered50_activation_status = Get-SqlRequiredString $_ "collector.historical_outage" "Tiered50ActivationStatus"
        recorded_at_utc = Convert-SqlUtcDateTimeToIso8601 (Get-SqlFieldValue $_ "collector.historical_outage" "RecordedAtUtc") "collector.historical_outage" "RecordedAtUtc"
    }
})
$laterTiered50RunRows = @($laterTiered50RunsQuery.Rows | ForEach-Object {
    [ordered]@{
        mac_collector_run_id = Get-SqlRequiredInt64 $_ "collector.tiered50_later_runs" "MacCollectorRunId"
        run_id = Get-SqlRequiredString $_ "collector.tiered50_later_runs" "RunId"
        logical_due_bucket = Get-SqlRequiredInt64 $_ "collector.tiered50_later_runs" "LogicalDueBucket"
        started_at_utc = Convert-SqlUtcDateTimeToIso8601 (Get-SqlFieldValue $_ "collector.tiered50_later_runs" "StartedAtUtc") "collector.tiered50_later_runs" "StartedAtUtc"
        completed_at_utc = Convert-NullableSqlUtcDateTimeToIso8601 (Get-SqlFieldValue $_ "collector.tiered50_later_runs" "CompletedAtUtc") "collector.tiered50_later_runs" "CompletedAtUtc"
        status = Get-SqlRequiredString $_ "collector.tiered50_later_runs" "Status"
    }
})
$firstHeartbeatRunIdCrossCheck = if ($firstHeartbeatRows.Count -eq 1) { [string]$firstHeartbeatRows[0].run_id } else { "" }
$firstHeartbeatTargetAuditReasons = @()
$firstHeartbeatTargetAuditById = @{}
$duplicateTargetAuditIds = @()
$declaredHttpAttempts = [int64]0
foreach ($row in $firstHeartbeatTargetAuditRows) {
    if ($null -eq $row.audit_id) {
        $firstHeartbeatTargetAuditReasons += "first-heartbeat target audits are missing AuditId"
        continue
    }
    $auditId = [int64]$row.audit_id
    if ($firstHeartbeatTargetAuditById.ContainsKey($auditId)) {
        $duplicateTargetAuditIds += $auditId
        continue
    }
    $firstHeartbeatTargetAuditById[$auditId] = $row
    if ([string]$row.run_id -ne $firstHeartbeatRunIdCrossCheck) {
        $firstHeartbeatTargetAuditReasons += "first-heartbeat target audits are not linked by RunId"
    }
    if ($null -eq $row.http_attempts -or [int64]$row.http_attempts -lt 0) {
        $firstHeartbeatTargetAuditReasons += ("AuditId {0} is missing a non-negative HttpAttempts count" -f $auditId)
    } else {
        $declaredHttpAttempts += [int64]$row.http_attempts
    }
}
if ($duplicateTargetAuditIds.Count -gt 0) {
    $firstHeartbeatTargetAuditReasons += (
        "duplicated target-audit AuditId(s): " + (($duplicateTargetAuditIds | Sort-Object -Unique) -join ", ")
    )
}
if ($firstHeartbeatTargetAuditRows.Count -eq 0) {
    $firstHeartbeatTargetAuditReasons += "the original heartbeat returned no target-audit rows"
}
if ($firstTiered50HeartbeatTargetAuditsEvidence.status -ne "PASS") {
    $firstHeartbeatTargetAuditReasons += [string]$firstTiered50HeartbeatTargetAuditsEvidence.reason
}
if ([string]::IsNullOrWhiteSpace($firstHeartbeatRunIdCrossCheck)) {
    $firstHeartbeatTargetAuditReasons += "the original Tiered50 heartbeat RunId was not returned"
}

$firstHeartbeatHttpAttemptReasons = @()
$firstHeartbeatHttpAttemptById = @{}
$firstHeartbeatHttpAttemptKeySet = @{}
$firstHeartbeatHttpAttemptCounts = @{}
$duplicateHttpAttemptIds = @()
$duplicateHttpAttemptKeys = @()
$orphanHttpAttemptIds = @()
$actualHttpAttemptCount = [int64]$firstHeartbeatHttpAttemptRows.Count
foreach ($row in $firstHeartbeatHttpAttemptRows) {
    if ($null -eq $row.http_attempt_id) {
        $firstHeartbeatHttpAttemptReasons += "first-heartbeat HTTP attempts are missing HttpAttemptId"
    } else {
        $httpAttemptId = [int64]$row.http_attempt_id
        if ($firstHeartbeatHttpAttemptById.ContainsKey($httpAttemptId)) {
            $duplicateHttpAttemptIds += $httpAttemptId
        } else {
            $firstHeartbeatHttpAttemptById[$httpAttemptId] = $row
        }
    }
    if ([string]$row.run_id -ne $firstHeartbeatRunIdCrossCheck) {
        $firstHeartbeatHttpAttemptReasons += "first-heartbeat HTTP attempts are not linked by RunId"
    }
    if ($null -eq $row.audit_id) {
        $firstHeartbeatHttpAttemptReasons += "first-heartbeat HTTP attempts are missing AuditId"
        continue
    }
    $auditId = [int64]$row.audit_id
    if (-not $firstHeartbeatTargetAuditById.ContainsKey($auditId)) {
        $orphanHttpAttemptIds += if ($null -eq $row.http_attempt_id) { [string]$auditId } else { [string]$row.http_attempt_id }
    } else {
        if ($firstHeartbeatHttpAttemptCounts.ContainsKey($auditId)) {
            $firstHeartbeatHttpAttemptCounts[$auditId] += 1
        } else {
            $firstHeartbeatHttpAttemptCounts[$auditId] = 1
        }
        $targetRow = $firstHeartbeatTargetAuditById[$auditId]
        foreach ($field in @("target_identity", "logical_due_bucket", "collection_context")) {
            if ($row.Keys -contains $field -and $targetRow.Keys -contains $field -and
                -not [string]::IsNullOrWhiteSpace([string]$row[$field]) -and
                -not [string]::IsNullOrWhiteSpace([string]$targetRow[$field]) -and
                [string]$row[$field] -ne [string]$targetRow[$field]) {
                $firstHeartbeatHttpAttemptReasons += ("HTTP attempt for AuditId {0} has inconsistently attributed {1}" -f $auditId, $field)
            }
        }
    }
    if ($null -eq $row.attempt_number -or [int]$row.attempt_number -lt 1) {
        $firstHeartbeatHttpAttemptReasons += ("HTTP attempt for AuditId {0} has an invalid AttemptNumber" -f $auditId)
    } else {
        $attemptKey = "{0}/{1}" -f $auditId, [int]$row.attempt_number
        if ($firstHeartbeatHttpAttemptKeySet.ContainsKey($attemptKey)) {
            $duplicateHttpAttemptKeys += $attemptKey
        } else {
            $firstHeartbeatHttpAttemptKeySet[$attemptKey] = $true
        }
    }
    if ($row.Keys -contains "joined_audit_id" -and $null -eq $row.joined_audit_id) {
        $orphanHttpAttemptIds += if ($null -eq $row.http_attempt_id) { [string]$auditId } else { [string]$row.http_attempt_id }
    } elseif ($row.Keys -contains "joined_audit_id" -and [int64]$row.joined_audit_id -ne $auditId) {
        $firstHeartbeatHttpAttemptReasons += ("HTTP attempt for AuditId {0} has an inconsistent joined AuditId" -f $auditId)
    }
}
if ($duplicateHttpAttemptIds.Count -gt 0) {
    $firstHeartbeatHttpAttemptReasons += (
        "duplicated HTTP attempt HttpAttemptId(s): " + (($duplicateHttpAttemptIds | Sort-Object -Unique) -join ", ")
    )
}
if ($duplicateHttpAttemptKeys.Count -gt 0) {
    $firstHeartbeatHttpAttemptReasons += (
        "duplicated HTTP attempt AuditId/AttemptNumber pair(s): " + (($duplicateHttpAttemptKeys | Sort-Object -Unique) -join ", ")
    )
}
if ($orphanHttpAttemptIds.Count -gt 0) {
    $firstHeartbeatHttpAttemptReasons += (
        "first-heartbeat HTTP attempts are not linked to its target audits; orphaned HTTP attempt row(s): " +
        (($orphanHttpAttemptIds | Sort-Object -Unique) -join ", ")
    )
}
foreach ($auditId in $firstHeartbeatTargetAuditById.Keys) {
    $declared = [int64]$firstHeartbeatTargetAuditById[$auditId].http_attempts
    $actual = if ($firstHeartbeatHttpAttemptCounts.ContainsKey($auditId)) { [int64]$firstHeartbeatHttpAttemptCounts[$auditId] } else { [int64]0 }
    if ($declared -ne $actual) {
        $classification = if ($actual -lt $declared) { "missing" } else { "duplicated or extra" }
        $firstHeartbeatHttpAttemptReasons += ("AuditId {0} declares {1} HTTP attempt(s) but {2} actual row(s) were returned ({3} history)" -f $auditId, $declared, $actual, $classification)
    }
}
if ($declaredHttpAttempts -gt 0 -and $actualHttpAttemptCount -eq 0) {
    $firstHeartbeatHttpAttemptReasons += "known historical HTTP activity has no preserved first-heartbeat HTTP attempt rows"
}
if ($declaredHttpAttempts -eq 0 -and $actualHttpAttemptCount -gt 0) {
    $firstHeartbeatHttpAttemptReasons += "HTTP attempt rows exist although the first-heartbeat target audits declare no attempts"
}
if ($firstTiered50HeartbeatHttpAttemptsEvidence.status -ne "PASS") {
    $firstHeartbeatHttpAttemptReasons += [string]$firstTiered50HeartbeatHttpAttemptsEvidence.reason
}
if ([string]::IsNullOrWhiteSpace($firstHeartbeatRunIdCrossCheck)) {
    $firstHeartbeatHttpAttemptReasons += "first-heartbeat HTTP attempts are not linked to the authoritative target-audit query"
}

$firstHeartbeatTargetAuditsCheckStatus = if (
    $firstTiered50HeartbeatTargetAuditsEvidence.status -eq "PASS" -and
    $firstHeartbeatTargetAuditRows.Count -gt 0 -and
    $firstHeartbeatTargetAuditById.Count -gt 0 -and
    $firstHeartbeatTargetAuditReasons.Count -eq 0
) { "PASS" } else { "NOT_PROVEN" }
$firstHeartbeatHttpAttemptsCheckStatus = if (
    $firstTiered50HeartbeatHttpAttemptsEvidence.status -eq "PASS" -and
    $firstHeartbeatTargetAuditsCheckStatus -eq "PASS" -and
    $firstHeartbeatHttpAttemptReasons.Count -eq 0
) { "PASS" } else { "NOT_PROVEN" }
$firstHeartbeatTargetAuditsReason = if ($firstHeartbeatTargetAuditReasons.Count -gt 0) { $firstHeartbeatTargetAuditReasons -join "; " } else { $null }
$firstHeartbeatHttpAttemptsReason = if ($firstHeartbeatHttpAttemptReasons.Count -gt 0) { $firstHeartbeatHttpAttemptReasons -join "; " } else { $null }
$firstHeartbeatReconciliationReasons = @()
if (-not [string]::IsNullOrWhiteSpace([string]$firstHeartbeatTargetAuditsReason)) {
    $firstHeartbeatReconciliationReasons += $firstHeartbeatTargetAuditsReason
}
if (-not [string]::IsNullOrWhiteSpace([string]$firstHeartbeatHttpAttemptsReason)) {
    $firstHeartbeatReconciliationReasons += $firstHeartbeatHttpAttemptsReason
}

$historicalHttpOrphanRows = @(
    $httpAttemptRows |
        Where-Object {
            ($_.Keys -contains "joined_audit_id" -and $null -eq $_.joined_audit_id) -or
            [string]::IsNullOrWhiteSpace([string]$_.run_id)
        }
)
$httpAttemptHistoryLinked = $httpAttemptHistoryEvidence.status -eq "PASS" -and $historicalHttpOrphanRows.Count -eq 0
$httpAttemptHistoryReason = if ($historicalHttpOrphanRows.Count -gt 0) {
    "{0} HTTP attempt row(s) are orphaned from their target audit" -f $historicalHttpOrphanRows.Count
} else { $null }
$historicalRunsCheckStatus = if ($historicalRunsEvidence.status -eq "PASS" -and $historicalRunRows.Count -gt 0) { "PASS" } else { "NOT_PROVEN" }
$historicalTargetAuditsCheckStatus = if ($historicalTargetAuditsEvidence.status -eq "PASS" -and $historicalTargetAuditRows.Count -gt 0) { "PASS" } else { "NOT_PROVEN" }
$httpAttemptHistoryCheckStatus = if ($httpAttemptHistoryLinked) { "PASS" } else { "NOT_PROVEN" }
$firstHeartbeatCheckStatus = if ($firstTiered50HeartbeatEvidence.status -eq "PASS" -and $firstHeartbeatRows.Count -eq 1) { "PASS" } else { "NOT_PROVEN" }
$historicalOutageCheckStatus = if ($historicalOutageEvidence.status -eq "PASS" -and $historicalOutageRows.Count -gt 0) { "PASS" } else { "NOT_PROVEN" }
$laterTiered50RunsCheckStatus = if ($laterTiered50RunsEvidence.status -eq "PASS") { "PASS" } else { "NOT_PROVEN" }
$cutoverHistoryCheckStatus = if ($cutoverHistoryEvidence.status -eq "PASS" -and $cutoverHistory.Count -gt 0) { "PASS" } else { "NOT_PROVEN" }
$firstHeartbeatReconciliation = [ordered]@{
    status = if ($firstHeartbeatTargetAuditsCheckStatus -eq "PASS" -and $firstHeartbeatHttpAttemptsCheckStatus -eq "PASS") { "PASS" } else { "NOT_PROVEN" }
    reason = if ($firstHeartbeatReconciliationReasons.Count -gt 0) { $firstHeartbeatReconciliationReasons -join "; " } else { $null }
    run_id = $firstHeartbeatRunIdCrossCheck
    target_audit_count = [int64]$firstHeartbeatTargetAuditRows.Count
    declared_http_attempt_count = $declaredHttpAttempts
    actual_http_attempt_count = $actualHttpAttemptCount
    http_backed_successful_target_count = @($firstHeartbeatTargetAuditRows | Where-Object { [string]$_.status -eq "SUCCEEDED" -and $null -ne $_.http_attempts -and [int64]$_.http_attempts -gt 0 }).Count
    skipped_target_count = @($firstHeartbeatTargetAuditRows | Where-Object { [string]$_.status -eq "SKIPPED" }).Count
    duplicate_target_audit_ids = @($duplicateTargetAuditIds | Sort-Object -Unique)
    duplicate_http_attempt_ids = @($duplicateHttpAttemptIds | Sort-Object -Unique)
    duplicate_http_attempt_keys = @($duplicateHttpAttemptKeys | Sort-Object -Unique)
    orphan_http_attempt_ids = @($orphanHttpAttemptIds | Sort-Object -Unique)
    target_check_status = $firstHeartbeatTargetAuditsCheckStatus
    target_check_reason = $firstHeartbeatTargetAuditsReason
    http_check_status = $firstHeartbeatHttpAttemptsCheckStatus
    http_check_reason = $firstHeartbeatHttpAttemptsReason
}
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
        server_name = $identityServerName
        sql_instance_identity = @{ server_name = $identityServerName; machine_name = $identityMachineName; instance_name = $identityInstanceName; database_name = $identityDatabaseName }
        database_name = $identityDatabaseName
        effective_login = $identityLoginName
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
        identity = @{ status = "PASS"; server_name = $identityServerName; machine_name = $identityMachineName; instance_name = $identityInstanceName; configured_server_name = $identityConfiguredServerName; database_name = $identityDatabaseName; login_name = $identityLoginName; captured_at_utc = $captured; sql_instance_identity = @{ server_name = $identityServerName; machine_name = $identityMachineName; instance_name = $identityInstanceName; database_name = $identityDatabaseName } }
        admin_identity_gate = @{ status = "PASS"; effective_login = $identityLoginName; runtime_login_match = $false }
        admin_permissions = @{ status = if ($permissionsProven) { "PASS" } else { "NOT_PROVEN" }; product_major_version = $productMajorVersion; permission_checks = $permissionChecks; missing_permissions = $missingPermissions; tls = $tlsEvidence }
        runtime_health = @{ status = "PASS"; database_name = $runtimeHealth[0].database_name; database_status = $runtimeHealth[0].database_status; recovery_model = $runtimeHealth[0].recovery_model; evidence = @{ status = "PASS" } }
        database_files = @($databaseFileDetails | ForEach-Object { @{ name = $_.name; type = $_.type; size_pages = $_.size_pages; growth = $_.growth; is_percent_growth = $_.is_percent_growth; max_size_pages = $_.max_size_pages; space_used_pages = $_.space_used_pages; size_bytes = $_.size_bytes; space_used_bytes = $_.space_used_bytes } })
        database_files_evidence = @{ status = "PASS" }
        volume_capacity = @($volumes | ForEach-Object { @{ volume = $_.volume_mount_point; total_bytes = $_.total_bytes; available_bytes = $_.available_bytes } })
        volume_capacity_evidence = @{ status = "PASS" }
        log_space = @{ total_bytes = $logSpace[0].total_log_size_in_bytes; used_bytes = $logSpace[0].used_log_space_in_bytes; used_percent = [double]$logSpace[0].used_log_space_in_percent }
        log_space_evidence = @{ status = "PASS" }
        backup_verifyonly = @{ status = "PASS"; checksum_verified = $true; media_count = [int]$backupMedia.Count; cause_classification = "VERIFIED" }
        backup_gate = @{ status = "PASS" }
        capacity_gate = @{ status = "PASS" }
        log_space_gate = @{ status = "PASS" }
        quota_integrity = @{ status = "PASS"; state_available = $true; source_kind = $quota[0].SourceKind; month_key = $quota[0].MonthKey; accounting_status = $quota[0].AccountingStatus; known_total = $quota[0].KnownTotal; reserved_shared_usage = $quota[0].ReservedSharedUsage }
        latest_full_backup = @{ backup_set_id = $backup[0].backup_set_id; media_set_id = $backup[0].media_set_id; backup_finish_date = $backup[0].backup_finish_date; copy_only = $backup[0].is_copy_only; has_backup_checksums = $backup[0].has_backup_checksums; compressed_backup_size = $backup[0].compressed_backup_size; media_count = [int]$backupMedia.Count; complete_media_set_proven = $true }
        latest_full_backup_evidence = @{ status = "PASS" }
        latest_full_backup_media_evidence = @{ status = "PASS"; media_count = [int]$backupMedia.Count }
        tiered50_materialization = @{ status = "PASS"; target_count = $materialization[0].TargetCount; active_count = $materialization[0].ActiveCount }
        tiered50_audit_history_evidence = @{ status = $historicalTargetAuditsEvidence.status; statement_id = $historicalTargetAuditsEvidence.statement_id; executed = $historicalTargetAuditsEvidence.executed; result_count = $historicalTargetAuditsEvidence.result_count; captured_at_utc = $historicalTargetAuditsEvidence.captured_at_utc; check_status = $historicalTargetAuditsCheckStatus; reason = $historicalTargetAuditsEvidence.reason }
        recent_http_attempts_evidence = @{ status = $httpAttemptHistoryEvidence.status; statement_id = $httpAttemptHistoryEvidence.statement_id; executed = $httpAttemptHistoryEvidence.executed; result_count = $httpAttemptHistoryEvidence.result_count; captured_at_utc = $httpAttemptHistoryEvidence.captured_at_utc; check_status = $httpAttemptHistoryCheckStatus; reason = if ($httpAttemptHistoryReason) { $httpAttemptHistoryReason } else { $httpAttemptHistoryEvidence.reason } }
        first_tiered50_heartbeat_target_audits_evidence = @{ status = $firstTiered50HeartbeatTargetAuditsEvidence.status; statement_id = $firstTiered50HeartbeatTargetAuditsEvidence.statement_id; executed = $firstTiered50HeartbeatTargetAuditsEvidence.executed; result_count = $firstTiered50HeartbeatTargetAuditsEvidence.result_count; captured_at_utc = $firstTiered50HeartbeatTargetAuditsEvidence.captured_at_utc; check_status = $firstHeartbeatTargetAuditsCheckStatus; run_id = $firstHeartbeatRunIdCrossCheck; reason = if ($firstHeartbeatTargetAuditsReason) { $firstHeartbeatTargetAuditsReason } else { $firstTiered50HeartbeatTargetAuditsEvidence.reason } }
        first_tiered50_heartbeat_http_attempts_evidence = @{ status = $firstTiered50HeartbeatHttpAttemptsEvidence.status; statement_id = $firstTiered50HeartbeatHttpAttemptsEvidence.statement_id; executed = $firstTiered50HeartbeatHttpAttemptsEvidence.executed; result_count = $firstTiered50HeartbeatHttpAttemptsEvidence.result_count; captured_at_utc = $firstTiered50HeartbeatHttpAttemptsEvidence.captured_at_utc; check_status = $firstHeartbeatHttpAttemptsCheckStatus; run_id = $firstHeartbeatRunIdCrossCheck; reason = if ($firstHeartbeatHttpAttemptsReason) { $firstHeartbeatHttpAttemptsReason } else { $firstTiered50HeartbeatHttpAttemptsEvidence.reason } }
        first_tiered50_heartbeat_reconciliation = $firstHeartbeatReconciliation
        production_cutover_history = @($cutoverHistory | ForEach-Object { @{ cutover_key = $_.cutover_key; panel_version = $_.panel_version; tiered50_activation_at_utc = $_.tiered50_activation_at_utc; tiered50_activation_status = $_.tiered50_activation_status; recorded_at_utc = $_.recorded_at_utc } })
        production_cutover_history_evidence = @{ status = $cutoverHistoryEvidence.status; statement_id = $cutoverHistoryEvidence.statement_id; executed = $cutoverHistoryEvidence.executed; result_count = [int64]$cutoverHistory.Count; captured_at_utc = $cutoverHistoryEvidence.captured_at_utc; check_status = $cutoverHistoryCheckStatus; reason = $cutoverHistoryEvidence.reason }
        competing_production_writers = @($competingWriters | ForEach-Object { @{ collection_context = $_.CollectionContext; running_count = $_.RunningCount } })
        competing_production_writers_evidence = @{ status = "PASS" }
        current_quota_ledger = @($currentQuotaLedger | ForEach-Object { @{ month_key = $_.month_key; provider_quota_ceiling = $_.provider_quota_ceiling; normal_operational_maximum = $_.normal_operational_maximum; retry_protection_boundary = $_.retry_protection_boundary; confirmed_provider_attempts = $_.confirmed_provider_attempts; conservative_unknown_attempts = $_.conservative_unknown_attempts; reserved_shared_usage = $_.reserved_shared_usage; accounting_status = $_.accounting_status; includes_all_shared_consumers = $_.includes_all_shared_consumers; provider_period_start_utc = $_.provider_period_start_utc; provider_period_end_utc = $_.provider_period_end_utc; local_usage_included_through_utc = $_.local_usage_included_through_utc; source_evidence_hash = $_.source_evidence_hash; reconciliation_key = $_.reconciliation_key } })
        current_quota_ledger_evidence = @{ status = "PASS" }
        windows_vm_disk = @{ status = "PASS"; minimum_free_percent = $MinimumFreePercent; minimum_free_reserve_bytes = $MinimumFreeReserveBytes; volumes = $windowsDisk; sql_volume_headroom = $sqlVolumeHeadroom }
        audit_provenance = [ordered]@{
            "collector.historical_runs" = @{ query = $historicalRunsEvidence; check_status = $historicalRunsCheckStatus; rows = $historicalRunRows }
            "collector.historical_target_audits" = @{ query = $historicalTargetAuditsEvidence; check_status = $historicalTargetAuditsCheckStatus; rows = $historicalTargetAuditRows }
            "collector.http_attempt_history" = @{ query = $httpAttemptHistoryEvidence; check_status = $httpAttemptHistoryCheckStatus; linked_to_target_audit = $httpAttemptHistoryLinked; rows = $httpAttemptRows; reason = $httpAttemptHistoryReason }
            "collector.tiered50_first_heartbeat" = @{ query = $firstTiered50HeartbeatEvidence; check_status = $firstHeartbeatCheckStatus; rows = $firstHeartbeatRows }
            "collector.tiered50_first_heartbeat_target_audits" = @{ query = $firstTiered50HeartbeatTargetAuditsEvidence; check_status = $firstHeartbeatTargetAuditsCheckStatus; linked_to_first_heartbeat_run = (-not [string]::IsNullOrWhiteSpace($firstHeartbeatRunIdCrossCheck)); run_id = $firstHeartbeatRunIdCrossCheck; rows = $firstHeartbeatTargetAuditRows; reason = $firstHeartbeatTargetAuditsReason }
            "collector.tiered50_first_heartbeat_http_attempts" = @{ query = $firstTiered50HeartbeatHttpAttemptsEvidence; check_status = $firstHeartbeatHttpAttemptsCheckStatus; linked_to_first_heartbeat_run = (-not [string]::IsNullOrWhiteSpace($firstHeartbeatRunIdCrossCheck)); linked_to_target_audit = ($firstHeartbeatHttpAttemptReasons.Count -eq 0); run_id = $firstHeartbeatRunIdCrossCheck; rows = $firstHeartbeatHttpAttemptRows; reason = $firstHeartbeatHttpAttemptsReason }
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
