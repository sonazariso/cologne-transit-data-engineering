#!/usr/bin/env python3
"""Offline Stage 14 manifest, policy, compilation, and architecture checks."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.manifest import load_default_manifest  # noqa: E402
from cologne_transit_collector.schedule import schedule_reconciliation  # noqa: E402


BANNED_IMPORTS = {
    "psycopg",
    "psycopg2",
    "pyodbc",
    "pymssql",
    "win32api",
    "win32con",
    "winreg",
}
BANNED_TEXT = ("powershell", "sql server", "windows task scheduler", "windows\\")


def architecture_check() -> list[str]:
    errors: list[str] = []
    package = ROOT / "src" / "cologne_transit_collector"
    for path in package.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        lowered = source.lower()
        for term in BANNED_TEXT:
            if term in lowered:
                errors.append(f"{path}: banned architecture text {term!r}")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            errors.append(f"{path}: syntax error: {exc.msg}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [str(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                if name in BANNED_IMPORTS:
                    errors.append(f"{path}: banned import {name}")
        if any(marker in source for marker in ("C:\\", "D:\\", "\\\\vmware-host")):
            errors.append(f"{path}: hardcoded platform path")
    return errors


def run_checks() -> dict[str, object]:
    manifest = load_default_manifest(ROOT)
    reconciliation = schedule_reconciliation(manifest.targets)
    errors = architecture_check()
    if reconciliation["super_cycle_counts"] != (19, 18, 18, 18, 19, 18):
        errors.append("schedule distribution differs from Stage 13")
    if reconciliation["super_cycle_total"] != 110 or reconciliation["requests_per_hour"] != 220:
        errors.append("schedule total differs from Stage 13")
    return {
        "status": "PASS" if not errors else "FAIL",
        "network_calls": 0,
        "manifest_targets": len(manifest.targets),
        "legacy7_targets": len(manifest.legacy7_targets),
        "new_tiered50_targets": len(manifest.new_tiered50_targets),
        "schedule": reconciliation,
        "architecture_errors": errors,
        "stage15_status": "NOT STARTED",
    }


def main() -> int:
    result = run_checks()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=list))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
