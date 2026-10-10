from __future__ import annotations

import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "capture_windows_sql_admin_evidence.ps1"


class WindowsSqlResultShapeTests(unittest.TestCase):
    def test_capture_script_keeps_sql_rows_as_one_collection(self) -> None:
        script = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("System.Collections.Generic.List[object]", script)
        self.assertIn("Write-Output -NoEnumerate -InputObject $rows", script)
        self.assertNotIn("return @(& $SqlCmdCommand @parameters)", script)
        self.assertIn("$rows = Invoke-ReadOnlySql $Query", script)
        self.assertNotIn("$rows = @(Invoke-ReadOnlySql $Query)", script)
        self.assertIn("query returned a row without expected column", script)
        self.assertIn("$evidence.status = \"PASS\"", script)
        self.assertIn("$evidence.reason = Get-SanitizedSqlError $_", script)

    def test_actual_datarow_zero_one_many_missing_column_and_error_paths(self) -> None:
        shell = shutil.which("powershell") or shutil.which("pwsh")
        if shell is None:
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
                "Get-SanitizedSqlError",
                "Invoke-ReadOnlySql",
                "Invoke-AuditedReadOnlySql"
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

            function New-TestDataRow([int] $RowNumber) {
                $table = New-Object System.Data.DataTable
                [void]$table.Columns.Add("DatabaseName", [string])
                [void]$table.Columns.Add("RowNumber", [int])
                $row = $table.NewRow()
                $row.DatabaseName = "CologneTransitIntelligence"
                $row.RowNumber = $RowNumber
                Write-Output $row
            }

            function Invoke-SqlFixture {
                param(
                    [string] $ServerInstance,
                    [string] $Database,
                    [string] $Query,
                    [string] $Encrypt,
                    [bool] $TrustServerCertificate
                )
                switch ($Query) {
                    "single" {
                        New-TestDataRow 1
                        return
                    }
                    "multiple" {
                        New-TestDataRow 1
                        New-TestDataRow 2
                        return
                    }
                    "empty" {
                        return
                    }
                    "error" {
                        throw "fixture SQL error"
                    }
                    "missing" {
                        New-TestDataRow 1
                        return
                    }
                    default {
                        throw "unknown fixture query"
                    }
                }
            }

            $SqlInstance = "fixture-instance"
            $Database = "CologneTransitIntelligence"
            $EncryptMode = "Mandatory"
            $SqlCmdCommand = Get-Command Invoke-SqlFixture
            $captured = "2026-10-10T10:00:00.0000000Z"

            $single = Invoke-ReadOnlySql "single"
            if (-not ($single -is [System.Collections.IList]) -or $single.Count -ne 1) {
                throw "single DataRow was not returned as a one-row collection"
            }
            if ($single[0].GetType().FullName -ne "System.Data.DataRow") {
                throw "single result was not a DataRow"
            }
            if ([string]$single[0].DatabaseName -ne "CologneTransitIntelligence") {
                throw "single DataRow columns were flattened or lost"
            }

            $multiple = Invoke-ReadOnlySql "multiple"
            if (-not ($multiple -is [System.Collections.IList]) -or $multiple.Count -ne 2) {
                throw "multiple DataRows were not preserved"
            }
            if ($multiple[0].GetType().FullName -ne "System.Data.DataRow" -or
                $multiple[1].GetType().FullName -ne "System.Data.DataRow") {
                throw "multiple results were not complete DataRows"
            }

            $empty = Invoke-ReadOnlySql "empty"
            if (-not ($empty -is [System.Collections.IList]) -or $empty.Count -ne 0) {
                throw "zero-row SQL result was not an empty collection"
            }

            try {
                $null = Invoke-ReadOnlySql "error"
                throw "SQL error was converted into a successful result"
            } catch {
                if ([string]$_.Exception.Message -notmatch "fixture SQL error") {
                    throw
                }
            }

            $missing = Invoke-AuditedReadOnlySql "collector.historical_runs" "missing"
            if ($missing.Evidence.status -ne "NOT_PROVEN" -or
                $missing.Evidence.executed -ne $true -or
                [string]$missing.Evidence.reason -notmatch "MacCollectorRunId") {
                throw "missing expected SQL columns were not fail-closed"
            }
            if (-not ($missing.Rows -is [System.Collections.IList]) -or $missing.Rows.Count -ne 0) {
                throw "failed audited query did not return a predictable empty collection"
            }

            Write-Output "WINDOWS_SQL_RESULT_SHAPE_TEST_PASS"
            """
        )

        with tempfile.TemporaryDirectory() as directory:
            harness_path = Path(directory) / "result-shape-harness.ps1"
            harness_path.write_text(harness, encoding="utf-8")
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
            msg=f"PowerShell result-shape harness failed:\n{completed.stdout}\n{completed.stderr}",
        )
        self.assertIn("WINDOWS_SQL_RESULT_SHAPE_TEST_PASS", completed.stdout)


if __name__ == "__main__":
    unittest.main()
