from __future__ import annotations

import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "capture_windows_sql_admin_evidence.ps1"


class WindowsSqlDbNullTests(unittest.TestCase):
    def test_capture_script_has_explicit_sql_null_boundaries(self) -> None:
        script = SCRIPT.read_text(encoding="utf-8")

        for helper in (
            "Test-SqlNullValue",
            "Get-SqlFieldValue",
            "Convert-SqlRequiredInt64",
            "Convert-SqlNullableInt64",
            "Convert-SqlRequiredInt32",
            "Convert-SqlNullableInt32",
            "Convert-SqlRequiredBoolean",
            "Convert-SqlNullableBoolean",
            "Convert-SqlUtcDateTimeToIso8601",
            "Resolve-PermissionState",
        ):
            self.assertIn(f"function {helper}", script)

        self.assertIn("$Value -is [System.DBNull]", script)
        self.assertIn('Get-PermissionState $permissionMetadata.HasViewServerPerformanceState', script)
        self.assertIn('Resolve-PermissionState $permissionChecks.server_dmv_state $true', script)
        self.assertIn(
            'space_used_bytes = if ($null -eq $spaceUsedPages) { $null }',
            script,
        )
        self.assertIn(
            'Get-SqlNullableInt32 $_ "collector.http_attempt_history" "HttpStatus"',
            script,
        )
        self.assertIn(
            'Get-SqlNullableInt64 $_ "collector.http_attempt_history" "JoinedAuditId"',
            script,
        )
        self.assertNotIn("[int]$permissions[0]", script)
        self.assertNotIn("[int64]$_.size", script)
        self.assertNotIn("[bool]$_.NetworkAttempted", script)
        self.assertNotIn("[int]$_.HttpStatus", script)

    def test_real_datarow_sql_null_and_conversion_contracts(self) -> None:
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
                "Convert-SqlNullableInt64",
                "Convert-SqlRequiredInt32",
                "Convert-SqlNullableInt32",
                "Convert-SqlRequiredDecimal",
                "Convert-SqlRequiredBoolean",
                "Convert-SqlNullableBoolean",
                "Get-SqlRequiredInt64",
                "Get-SqlNullableInt64",
                "Get-SqlRequiredInt32",
                "Get-SqlNullableInt32",
                "Get-SqlRequiredDecimal",
                "Get-SqlRequiredString",
                "Get-SqlNullableString",
                "Get-SqlRequiredBoolean",
                "Get-SqlNullableBoolean",
                "Convert-SqlRequiredSourceText",
                "Resolve-PermissionState",
                "Get-PermissionState",
                "Convert-SqlUtcDateTimeToIso8601",
                "Convert-NullableSqlUtcDateTimeToIso8601"
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

            function Assert-Throws {
                param(
                    [scriptblock] $Block,
                    [string] $MessagePattern
                )
                try {
                    & $Block
                    throw "expected exception"
                } catch {
                    if ([string]$_.Exception.Message -notmatch $MessagePattern) {
                        throw
                    }
                }
            }

            $table = New-Object System.Data.DataTable
            [void]$table.Columns.Add("Permission", [int])
            [void]$table.Columns.Add("NullableInt", [int])
            [void]$table.Columns.Add("NullableLong", [int64])
            [void]$table.Columns.Add("NullableBool", [bool])
            [void]$table.Columns.Add("NullableText", [string])
            [void]$table.Columns.Add("Timestamp", [DateTime])
            $row = $table.NewRow()
            $row["Permission"] = 1
            $row["NullableInt"] = [System.DBNull]::Value
            $row["NullableLong"] = [System.DBNull]::Value
            $row["NullableBool"] = [System.DBNull]::Value
            $row["NullableText"] = [System.DBNull]::Value
            $row["Timestamp"] = [DateTime]::SpecifyKind(
                [DateTime]::Parse("2026-10-10T10:00:00"),
                [DateTimeKind]::Unspecified
            )
            [void]$table.Rows.Add($row)

            $dbNull = $row["NullableInt"]
            if ($dbNull -isnot [System.DBNull] -or -not (Test-SqlNullValue $dbNull)) {
                throw "a real DataRow SQL NULL was not recognized as DBNull"
            }
            if (-not (Test-SqlNullValue $null)) {
                throw "PowerShell null was not recognized"
            }
            if ((Get-PermissionState $dbNull) -ne "UNKNOWN" -or
                (Get-PermissionState $null) -ne "UNKNOWN") {
                throw "SQL NULL or PowerShell null was not mapped to UNKNOWN"
            }
            if ((Get-PermissionState 1) -ne "GRANTED" -or
                (Get-PermissionState 0) -ne "DENIED" -or
                (Get-PermissionState "unexpected") -ne "UNKNOWN" -or
                (Get-PermissionState 2) -ne "UNKNOWN") {
                throw "permission values were not classified strictly"
            }
            if ((Resolve-PermissionState "UNKNOWN" $false) -ne "UNKNOWN" -or
                (Resolve-PermissionState "UNKNOWN" $true) -ne "GRANTED" -or
                (Resolve-PermissionState "DENIED" $true) -ne "DENIED") {
                throw "authoritative access did not preserve the UNKNOWN-only upgrade rule"
            }

            if ($null -ne (Get-SqlNullableInt32 $row "collector.http_attempt_history" "NullableInt") -or
                $null -ne (Get-SqlNullableInt64 $row "collector.http_attempt_history" "NullableLong") -or
                $null -ne (Get-SqlNullableBoolean $row "collector.http_attempt_history" "NullableBool") -or
                $null -ne (Get-SqlNullableString $row "collector.http_attempt_history" "NullableText")) {
                throw "nullable SQL fields were not preserved as PowerShell null"
            }
            if ((Convert-SqlRequiredInt32 0 "admin.permissions" "Permission") -ne 0 -or
                (Convert-SqlRequiredInt32 1 "admin.permissions" "Permission") -ne 1) {
                throw "valid integer permission values were rejected"
            }
            if ((Convert-SqlRequiredBoolean 0 "query" "flag") -ne $false -or
                (Convert-SqlRequiredBoolean 1 "query" "flag") -ne $true) {
                throw "valid Boolean values were rejected"
            }
            Assert-Throws { Convert-SqlRequiredInt64 $dbNull "capacity.required" "total_bytes" } "capacity\.required.*total_bytes"
            Assert-Throws { Convert-SqlRequiredInt64 "unexpected" "capacity.required" "total_bytes" } "capacity\.required.*total_bytes"
            Assert-Throws { Convert-SqlRequiredBoolean "unexpected" "query" "flag" } "query.*flag"
            Assert-Throws { Get-SqlFieldValue $row "query" "MissingField" } "query.*MissingField"
            Assert-Throws { Convert-SqlUtcDateTimeToIso8601 $dbNull "query" "captured_at" } "query.*captured_at"
            Assert-Throws { Convert-SqlUtcDateTimeToIso8601 "not-a-timestamp" "query" "captured_at" } "query.*captured_at"

            $iso = Convert-SqlUtcDateTimeToIso8601 $row["Timestamp"] "admin.identity" "CapturedAtUtc"
            if ($iso -ne "2026-10-10T10:00:00.0000000Z") {
                throw "an unspecified SQL datetime2 value was not treated as UTC"
            }
            if ($null -ne (Convert-NullableSqlUtcDateTimeToIso8601 $row["NullableText"] "history" "completed_at")) {
                throw "nullable timestamp SQL NULL was not preserved"
            }

            Write-Output "WINDOWS_SQL_DBNULL_TEST_PASS"
            """
        )

        with tempfile.TemporaryDirectory() as directory:
            harness_path = Path(directory) / "dbnull-harness.ps1"
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
                            f"{shell_name} DBNull harness failed:\n"
                            f"{completed.stdout}\n{completed.stderr}"
                        ),
                    )
                    self.assertIn("WINDOWS_SQL_DBNULL_TEST_PASS", completed.stdout)


if __name__ == "__main__":
    unittest.main()
