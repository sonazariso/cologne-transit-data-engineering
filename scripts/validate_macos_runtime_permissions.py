#!/usr/bin/env python3
"""Read-only validation of the deployed CologneTransitCollector SQL permissions."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cologne_transit_collector.mssql_persistence import MssqlConnectionSettings  # noqa: E402
from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    MacOSKeychainSecretProvider,
    RUNTIME_CONFIG_PATH_ENV,
)
from provision_macos_runtime_login import inspect_runtime_permissions  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-config", type=Path)
    args = parser.parse_args(argv)
    environment = dict(os.environ)
    if args.runtime_config is not None:
        environment[RUNTIME_CONFIG_PATH_ENV] = str(args.runtime_config.expanduser().resolve())
    try:
        settings = MssqlConnectionSettings.from_environment(
            environment,
            secret_provider=MacOSKeychainSecretProvider(),
        )
        result = inspect_runtime_permissions(settings)
        result["runtime_config_path"] = str(
            args.runtime_config.expanduser().resolve()
            if args.runtime_config is not None
            else Path.home() / "Library" / "Application Support" / "CologneTransitIntelligence" / "runtime.json"
        )
    except Exception as exc:
        result = {
            "status": "FAIL",
            "validation_mode": "READ_ONLY_PERMISSION_METADATA",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
