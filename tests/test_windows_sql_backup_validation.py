from __future__ import annotations

import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "capture_windows_sql_admin_evidence.ps1"
WINDOWS_BACKUP_SCRIPT = ROOT / "scripts" / "execute_windows_sql_backup.ps1"
SSMS_BACKUP_SCRIPT = ROOT / "sql" / "mdd-mac-collector" / "backup-cologne-transit-off-c-v1.sql"


class WindowsSqlBackupValidationTests(unittest.TestCase):
    def test_windows_integrated_backup_runner_is_narrow_and_fail_closed(self) -> None:
        script = WINDOWS_BACKUP_SCRIPT.read_text(encoding="utf-8")
        ssms_script = SSMS_BACKUP_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("Windows Authentication", script)
        self.assertIn("Encrypt = $EncryptMode", script)
        self.assertIn("TrustServerCertificate = $false", script)
        self.assertIn("COPY_ONLY, COMPRESSION, CHECKSUM", script)
        self.assertIn("RESTORE VERIFYONLY", script)
        self.assertIn("$Apply", script)
        self.assertIn("Test-OffCPath", script)
        self.assertNotIn("-Username", script)
        self.assertNotIn("-Password", script)
        self.assertNotIn("WITH INIT", script)

        self.assertIn("DECLARE @ApplyBackup BIT = 0;", ssms_script)
        self.assertIn("N'DataAnalyst-VM'", ssms_script)
        self.assertIn("N'DATAANALYST-VM\\Somaye'", ssms_script)
        self.assertIn("N'\\\\vmware-host\\Shared Folders\\CologneTransitData'", ssms_script)
        self.assertIn("COPY_ONLY, COMPRESSION, CHECKSUM", ssms_script)
        self.assertIn("RESTORE VERIFYONLY", ssms_script)
        self.assertIn("master.dbo.xp_fileexist", ssms_script)
        self.assertIn("FOR JSON PATH, WITHOUT_ARRAY_WRAPPER", ssms_script)
        self.assertNotIn("DROP TABLE", ssms_script.upper())
        self.assertNotIn("TRUNCATE TABLE", ssms_script.upper())
        self.assertNotIn("DBCC SHRINK", ssms_script.upper())
        self.assertNotIn("WITH INIT", ssms_script.upper())
        self.assertNotIn("DROP TABLE", script)
        self.assertNotIn("DBCC SHRINK", script)

    def test_capture_script_has_explicit_backup_boundaries(self) -> None:
        script = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("SELECT TOP (1) bs.backup_set_id", script)
        self.assertIn(
            "ORDER BY bs.backup_finish_date DESC, bs.backup_set_id DESC;",
            script,
        )
        self.assertIn("physical_device_name, device_type", script)
        self.assertIn("Convert-BackupHistoryRows", script)
        self.assertIn("Convert-BackupMediaRows", script)
        self.assertIn("Test-BackupMediaPathMissing", script)
        self.assertIn("Get-BackupMetadataValidation", script)
        self.assertIn("Get-BackupVerifyOnlyFailureClassification", script)
        self.assertIn("Invoke-BackupVerifyOnly", script)
        self.assertIn("return ,($normalizedRows)", script)
        self.assertIn("return ,($normalizedRows.ToArray())", script)
        self.assertIn('BACKUP_HISTORY_EMPTY', script)
        self.assertIn('BACKUP_HISTORY_MULTIPLE', script)
        self.assertIn('BACKUP_HISTORY_MALFORMED', script)
        self.assertIn('BACKUP_MEDIA_ROWS_MISSING', script)
        self.assertIn('BACKUP_MEDIA_PATH_MISSING', script)
        self.assertIn('BACKUP_MEDIA_ORDER_INVALID', script)
        self.assertIn('BACKUP_MEDIA_DEVICE_UNSUPPORTED', script)
        self.assertIn('BACKUP_HISTORY_QUERY_FAILED', script)
        self.assertIn('BACKUP_MEDIA_QUERY_FAILED', script)
        self.assertIn('BACKUP_FILE_INACCESSIBLE_TO_SQL_SERVER', script)
        self.assertIn('RESTORE_VERIFYONLY_FAILED', script)
        self.assertIn('WITH CHECKSUM', script)
        self.assertIn('(Test-SqlNullValue $row.family_sequence_number) -or', script)
        self.assertNotIn('SELECT TOP (2) bs.backup_set_id', script)
        self.assertNotIn('No native full backup path was available', script)
        self.assertNotIn(
            'if ($null -eq $backup -or $backup.Count -ne 1 -or $backupMedia.Count -lt 1',
            script,
        )

    def test_backup_metadata_cases_with_real_datarow_fixtures(self) -> None:
        shells = [
            (name, shell)
            for name in ("powershell", "pwsh")
            if (shell := shutil.which(name)) is not None
        ]
        if not shells:
            self.skipTest("PowerShell 5.1 or PowerShell 7 is not installed")

        harness = textwrap.dedent(
            r"""
            [CmdletBinding()]
            param([Parameter(Mandatory = $true)][string] $SourcePath)

            $ErrorActionPreference = "Stop"
            $tokens = $null
            $parseErrors = $null
            $ast = [System.Management.Automation.Language.Parser]::ParseFile(
                $SourcePath,
                [ref]$tokens,
                [ref]$parseErrors
            )
            if ($parseErrors.Count -gt 0) {
                throw "PowerShell source did not parse"
            }

            foreach ($functionName in @(
                "Test-SqlNullValue",
                "Get-SqlFieldValue",
                "Convert-SqlRequiredInt64",
                "Convert-SqlRequiredInt32",
                "Convert-SqlRequiredBoolean",
                "Convert-SqlRequiredSourceText",
                "Convert-SqlNullableString",
                "Get-SqlRequiredInt64",
                "Get-SqlRequiredInt32",
                "Get-SqlRequiredBoolean",
                "Get-SanitizedSqlError",
                "Convert-BackupHistoryRows",
                "Convert-BackupMediaRows",
                "Test-BackupMediaPathMissing",
                "Get-BackupVerifyOnlyFailureClassification",
                "Get-BackupMetadataValidation",
                "Invoke-BackupVerifyOnly"
            )) {
                $functionAst = @(
                    $ast.Find(
                        {
                            param($node)
                            $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                                $node.Name -eq $functionName
                        },
                        $true
                    )
                )
                if ($functionAst.Count -ne 1) {
                    throw "Could not extract $functionName"
                }
                Invoke-Expression $functionAst[0].Extent.Text
            }

            function New-HistoryRow {
                param([int64] $BackupSetId = 1, [int64] $MediaSetId = 7)
                $table = New-Object System.Data.DataTable
                [void]$table.Columns.Add("backup_set_id", [int64])
                [void]$table.Columns.Add("media_set_id", [int64])
                [void]$table.Columns.Add("backup_finish_date", [string])
                [void]$table.Columns.Add("is_copy_only", [bool])
                [void]$table.Columns.Add("has_backup_checksums", [bool])
                [void]$table.Columns.Add("compressed_backup_size", [int64])
                $row = $table.NewRow()
                $row.backup_set_id = $BackupSetId
                $row.media_set_id = $MediaSetId
                $row.backup_finish_date = "2026-10-07T10:00:00.0000000"
                $row.is_copy_only = $true
                $row.has_backup_checksums = $true
                $row.compressed_backup_size = 123456
                return ,$row
            }

            function New-MediaRow {
                param(
                    [int64] $Sequence = 1,
                    [AllowNull()][string] $Path = "m",
                    [int32] $DeviceType = 2
                )
                $table = New-Object System.Data.DataTable
                [void]$table.Columns.Add("family_sequence_number", [int64])
                [void]$table.Columns.Add("physical_device_name", [string])
                [void]$table.Columns.Add("device_type", [int32])
                $row = $table.NewRow()
                $row.family_sequence_number = $Sequence
                if ($null -eq $Path) {
                    $row.physical_device_name = [System.DBNull]::Value
                } else {
                    $row.physical_device_name = $Path
                }
                $row.device_type = $DeviceType
                return ,$row
            }

            function Assert-Equal {
                param([object] $Actual, [object] $Expected, [string] $Message)
                if ($Actual -ne $Expected) {
                    throw "$Message (actual: $Actual, expected: $Expected)"
                }
            }

            $script:verifyOnlyMode = "PASS"
            $script:verifyOnlyQuery = $null
            $script:verifyOnlyDiagnosticToken = $null
            function Invoke-ReadOnlySql {
                param(
                    [string] $Query,
                    [string] $StatementId
                )
                $script:verifyOnlyQuery = $Query
                if ($script:verifyOnlyMode -eq "FAIL") {
                    $slash = [string][char]92
                    $diagnosticToken = $slash + $slash + ("n" * 3) + $slash + ("d" * 2)
                    $script:verifyOnlyDiagnosticToken = $diagnosticToken
                    throw (
                        "SQL error 3201: Cannot open backup device " +
                        $diagnosticToken + ". Operating system error 2"
                    )
                }
                return ,([object[]]@())
            }

            $history = New-HistoryRow
            $normalizedHistory = Convert-BackupHistoryRows @($history)
            Assert-Equal $normalizedHistory.GetType().FullName "System.Object[]" "one history DataRow was not retained as an array"
            Assert-Equal $normalizedHistory.Count 1 "one history DataRow count changed"
            Assert-Equal $normalizedHistory[0].is_copy_only $true "COPY_ONLY full backup was rejected"
            Assert-Equal $normalizedHistory[0].has_backup_checksums $true "backup checksum flag was lost"

            $media = New-MediaRow
            $normalizedMedia = Convert-BackupMediaRows @($media)
            Assert-Equal $normalizedMedia.GetType().FullName "System.Object[]" "one media DataRow was not retained as an array"
            Assert-Equal $normalizedMedia.Count 1 "one media DataRow count changed"
            $valid = Get-BackupMetadataValidation $normalizedHistory $normalizedMedia
            Assert-Equal $valid.status "PASS" "one valid COPY_ONLY disk backup did not pass"
            Assert-Equal $valid.failure_code $null "valid backup unexpectedly had a failure code"

            $rawValidPath = Get-SqlFieldValue $media "backup.latest_full_backup_media_set" "physical_device_name"
            if ($rawValidPath -isnot [string]) {
                throw "raw populated media field was not a string"
            }
            Assert-Equal $rawValidPath.Length 1 "raw populated media field length changed"
            if (Test-BackupMediaPathMissing $rawValidPath) {
                throw "a populated raw media field was classified as missing"
            }
            if (Test-BackupMediaPathMissing $normalizedMedia[0].physical_device_name) {
                throw "a populated normalized media field was classified as missing"
            }
            Assert-Equal $normalizedMedia[0].physical_device_name.Length 1 "normalized media field length changed"

            Invoke-BackupVerifyOnly $normalizedMedia
            if ([string]$script:verifyOnlyQuery -notmatch '(?i)RESTORE VERIFYONLY') {
                throw "VERIFYONLY success did not execute the expected SQL statement"
            }
            if ([string]$script:verifyOnlyQuery -notmatch '(?i)WITH CHECKSUM') {
                throw "VERIFYONLY success did not require CHECKSUM"
            }
            $script:verifyOnlyMode = "FAIL"
            try {
                Invoke-BackupVerifyOnly $normalizedMedia
                throw "VERIFYONLY failure was converted into success"
            } catch {
                $verifyOnlyFailure = [string]$_.Exception.Message
                if ($verifyOnlyFailure -notmatch "BACKUP_FILE_INACCESSIBLE_TO_SQL_SERVER") {
                    throw
                }
                if ($verifyOnlyFailure.Contains([string]$script:verifyOnlyDiagnosticToken)) {
                    throw "VERIFYONLY failure exposed a physical backup path"
                }
            }
            $script:verifyOnlyMode = "PASS"

            $emptyHistory = [object[]]@()
            $emptyResult = Get-BackupMetadataValidation $emptyHistory $normalizedMedia
            Assert-Equal $emptyResult.failure_code "BACKUP_HISTORY_EMPTY" "empty history was not distinguished"

            $secondHistory = New-HistoryRow -BackupSetId 2 -MediaSetId 8
            $multipleHistory = [object[]]@($history, $secondHistory)
            $multipleResult = Get-BackupMetadataValidation $multipleHistory $normalizedMedia
            Assert-Equal $multipleResult.failure_code "BACKUP_HISTORY_MULTIPLE" "multiple selected history rows were not distinguished"

            $malformedHistory = [object[]]@([pscustomobject]@{ backup_set_id = 1 })
            $malformedResult = Get-BackupMetadataValidation $malformedHistory $normalizedMedia
            Assert-Equal $malformedResult.failure_code "BACKUP_HISTORY_MALFORMED" "malformed history was not fail-closed"

            $emptyMedia = [object[]]@()
            $missingMediaResult = Get-BackupMetadataValidation $normalizedHistory $emptyMedia
            Assert-Equal $missingMediaResult.failure_code "BACKUP_MEDIA_ROWS_MISSING" "missing media rows were not distinguished"

            $nullPath = New-MediaRow -Path $null
            $rawDbNullPath = Get-SqlFieldValue $nullPath "backup.latest_full_backup_media_set" "physical_device_name"
            if ($rawDbNullPath -isnot [System.DBNull]) {
                throw "the DataRow SQL NULL did not remain an explicit DBNull"
            }
            $normalizedNullPath = Convert-BackupMediaRows @($nullPath)
            if ($null -ne $normalizedNullPath[0].physical_device_name) {
                throw "DBNull did not normalize to PowerShell null"
            }
            if (-not (Test-BackupMediaPathMissing $rawDbNullPath) -or
                -not (Test-BackupMediaPathMissing $normalizedNullPath[0].physical_device_name)) {
                throw "DBNull and normalized PowerShell null were not classified as missing"
            }
            $nullPathResult = Get-BackupMetadataValidation $normalizedHistory $normalizedNullPath
            Assert-Equal $nullPathResult.failure_code "BACKUP_MEDIA_PATH_MISSING" "NULL media path was not distinguished"

            $powerShellNullPath = [pscustomobject]@{
                family_sequence_number = [int64]1
                physical_device_name = $null
                device_type = [int32]2
            }
            $rawPowerShellNullPath = Get-SqlFieldValue $powerShellNullPath "backup.latest_full_backup_media_set" "physical_device_name"
            if ($null -ne $rawPowerShellNullPath) {
                throw "the PowerShell null fixture was not null before normalization"
            }
            $normalizedPowerShellNull = Convert-BackupMediaRows @($powerShellNullPath)
            if (-not (Test-BackupMediaPathMissing $normalizedPowerShellNull[0].physical_device_name)) {
                throw "a PowerShell null media path was not classified as missing"
            }
            $powerShellNullResult = Get-BackupMetadataValidation $normalizedHistory $normalizedPowerShellNull
            Assert-Equal $powerShellNullResult.failure_code "BACKUP_MEDIA_PATH_MISSING" "PowerShell null media path was not rejected"

            foreach ($blankPath in @("", "   ")) {
                $blankRow = New-MediaRow -Path $blankPath
                $rawBlankPath = Get-SqlFieldValue $blankRow "backup.latest_full_backup_media_set" "physical_device_name"
                if ($rawBlankPath -isnot [string] -or $rawBlankPath.Length -ne $blankPath.Length) {
                    throw "empty or whitespace media field was not retained as a string with its length"
                }
                $normalizedBlank = Convert-BackupMediaRows @($blankRow)
                if (-not (Test-BackupMediaPathMissing $normalizedBlank[0].physical_device_name)) {
                    throw "empty or whitespace media path was not classified as missing"
                }
                $blankResult = Get-BackupMetadataValidation $normalizedHistory $normalizedBlank
                Assert-Equal $blankResult.failure_code "BACKUP_MEDIA_PATH_MISSING" "empty or whitespace media path was not rejected"
            }

            $familyOne = New-MediaRow -Sequence 1 -Path "a"
            $familyTwo = New-MediaRow -Sequence 2 -Path "b"
            $familyThree = New-MediaRow -Sequence 3 -Path "c"
            $threeMediaRows = Convert-BackupMediaRows @($familyOne, $familyTwo, $familyThree)
            $threeMediaResult = Get-BackupMetadataValidation $normalizedHistory $threeMediaRows
            Assert-Equal $threeMediaResult.status "PASS" "multiple valid disk media families did not pass"
            Assert-Equal $threeMediaRows.Count 3 "multiple media families were not retained"

            $outOfOrder = Convert-BackupMediaRows @($familyTwo, $familyOne)
            $orderingResult = Get-BackupMetadataValidation $normalizedHistory $outOfOrder
            Assert-Equal $orderingResult.failure_code "BACKUP_MEDIA_ORDER_INVALID" "invalid media ordering was not distinguished"

            $unsupported = New-MediaRow -DeviceType 5
            $normalizedUnsupported = Convert-BackupMediaRows @($unsupported)
            $unsupportedResult = Get-BackupMetadataValidation $normalizedHistory $normalizedUnsupported
            Assert-Equal $unsupportedResult.failure_code "BACKUP_MEDIA_DEVICE_UNSUPPORTED" "unsupported device type was not distinguished"

            $missingFileReason = "SQL error 3201: Cannot open backup device <redacted>. Operating system error 2"
            Assert-Equal (Get-BackupVerifyOnlyFailureClassification $missingFileReason) "BACKUP_FILE_INACCESSIBLE_TO_SQL_SERVER" "missing server-side file was not classified"
            Assert-Equal (Get-BackupVerifyOnlyFailureClassification "The backup set is invalid; RESTORE VERIFYONLY failed") "RESTORE_VERIFYONLY_FAILED" "VERIFYONLY failure was not classified"
            $sanitized = Get-SanitizedSqlError ([System.Exception]::new(
                "SQL error 3201: Cannot open backup device " + [string]$script:verifyOnlyDiagnosticToken
            ))
            if ($sanitized.Contains([string]$script:verifyOnlyDiagnosticToken)) {
                throw "sanitized diagnostics exposed a physical backup path"
            }

            Write-Output "WINDOWS_SQL_BACKUP_VALIDATION_TEST_PASS"
            """
        )

        with tempfile.TemporaryDirectory() as directory:
            harness_path = Path(directory) / "backup-validation-harness.ps1"
            harness_path.write_text(harness, encoding="utf-8")
            for shell_name, shell in shells:
                with self.subTest(shell=shell_name):
                    completed = subprocess.run(
                        [
                            shell,
                            "-NoLogo",
                            "-NoProfile",
                            "-NonInteractive",
                            "-ExecutionPolicy",
                            "Bypass",
                            "-File",
                            str(harness_path),
                            "-SourcePath",
                            str(SCRIPT),
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        completed.returncode,
                        0,
                        msg=(
                            f"{shell_name} backup-validation harness failed:\n"
                            f"{completed.stdout}\n{completed.stderr}"
                        ),
                    )
                    self.assertIn("WINDOWS_SQL_BACKUP_VALIDATION_TEST_PASS", completed.stdout)


if __name__ == "__main__":
    unittest.main()
