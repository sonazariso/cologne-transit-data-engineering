#!/usr/bin/env python3
"""Render and optionally bootstrap the user-local production LaunchAgent.

The tracked plist is a safe template.  This script writes a separate,
owner-only artifact beneath ``~/Library/LaunchAgents`` and keeps the runtime
configuration path and cutover gates there.  Secrets are never accepted as
arguments and are never written to the plist or its installation manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.domain import (  # noqa: E402
    MAC_SQL_CUTOVER_APPROVED_ENV,
    MAC_SQL_ENABLED_ENV,
    MAC_SQL_REQUIRED_TARGET_SCOPE,
    MAC_SQL_REQUIRED_VALIDATION_POLICY,
    MAC_SQL_REQUIRED_RUNTIME_STATE,
    MAC_SQL_RUNTIME_STATE_ENV,
    MAC_SQL_TARGET_SCOPE_ENV,
    MAC_SQL_VALIDATION_POLICY_ENV,
)
from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    DEFAULT_RUNTIME_CONFIG_PATH,
    REQUIRED_PYTHON_INTERPRETER,
    RUNTIME_CONFIG_PATH_ENV,
)


LABEL = "com.cologne-transit-intelligence.mdd-collector"
TEMPLATE_PATH = ROOT / "deployment" / "launchd" / f"{LABEL}.plist"
INSTALLED_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_DIRECTORY = Path.home() / "Library" / "Logs" / "CologneTransitIntelligence"
RUNTIME_BUNDLE_DIRECTORY = (
    Path.home() / "Library" / "Application Support" / "CologneTransitIntelligence" / "runtime-bundle"
)
RUNTIME_BUNDLE_ENTRY = RUNTIME_BUNDLE_DIRECTORY / "macos_launchd_entry.py"
INSTALLATION_MANIFEST = (
    Path.home() / "Library" / "Application Support" / "CologneTransitIntelligence" / "launchd-installation.json"
)
SECRET_MARKERS = frozenset(
    {
        "MDD_API_KEY",
        "MSSQL_PASSWORD",
        "SQL_SERVER_PASSWORD",
        "password",
        "secret",
        "token",
    }
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _plist_has_secret(plist: Mapping[str, Any]) -> bool:
    serialized = plistlib.dumps(dict(plist), fmt=plistlib.FMT_XML).decode("utf-8", errors="replace")
    return any(marker in serialized for marker in SECRET_MARKERS)


def _load_template() -> dict[str, Any]:
    with TEMPLATE_PATH.open("rb") as handle:
        template = plistlib.load(handle)
    if template.get("Label") != LABEL:
        raise ValueError("tracked LaunchAgent label is unexpected")
    if template.get("ProgramArguments", [None])[0] != REQUIRED_PYTHON_INTERPRETER:
        raise ValueError("tracked LaunchAgent interpreter is not the approved absolute path")
    if _plist_has_secret(template):
        raise ValueError("tracked LaunchAgent template contains a prohibited secret marker")
    return template


def build_installed_plist(*, activate: bool, runtime_config_path: str | Path = DEFAULT_RUNTIME_CONFIG_PATH) -> dict[str, Any]:
    """Render a machine-local plist without changing the tracked template."""

    plist = _load_template()
    environment = dict(plist.get("EnvironmentVariables", {}))
    environment.update(
        {
            "PYTHONPATH": str(RUNTIME_BUNDLE_DIRECTORY / "src"),
            RUNTIME_CONFIG_PATH_ENV: str(Path(runtime_config_path).expanduser().resolve()),
            "COLLECTOR_RUNTIME_ROOT": str(RUNTIME_BUNDLE_DIRECTORY),
            MAC_SQL_ENABLED_ENV: "1" if activate else "0",
            MAC_SQL_RUNTIME_STATE_ENV: MAC_SQL_REQUIRED_RUNTIME_STATE if activate else "UNCONFIRMED",
            MAC_SQL_CUTOVER_APPROVED_ENV: "1" if activate else "0",
            MAC_SQL_TARGET_SCOPE_ENV: MAC_SQL_REQUIRED_TARGET_SCOPE if activate else "UNAPPROVED",
            MAC_SQL_VALIDATION_POLICY_ENV: MAC_SQL_REQUIRED_VALIDATION_POLICY,
        }
    )
    for name in SECRET_MARKERS:
        environment.pop(name, None)
    plist["EnvironmentVariables"] = environment
    # A launchd job can expose PYTHONPATH in its diagnostic environment while
    # the interpreter still does not use it for module discovery.  The
    # installed artifact therefore calls an absolute user-local runtime
    # wrapper that inserts its bundled ``src`` before importing the runner.
    plist["ProgramArguments"] = [REQUIRED_PYTHON_INTERPRETER, str(RUNTIME_BUNDLE_ENTRY)]
    plist["WorkingDirectory"] = str(RUNTIME_BUNDLE_DIRECTORY)
    plist["Disabled"] = not activate
    plist["StandardOutPath"] = str(LOG_DIRECTORY / "mdd-collector.log")
    plist["StandardErrorPath"] = str(LOG_DIRECTORY / "mdd-collector.err.log")
    if _plist_has_secret(plist):
        raise ValueError("installed LaunchAgent contains a prohibited secret marker")
    return plist


def install(*, activate: bool, runtime_config_path: str | Path = DEFAULT_RUNTIME_CONFIG_PATH) -> dict[str, Any]:
    """Write the user-local plist and its separate non-secret manifest."""

    INSTALLED_PATH.parent.mkdir(parents=True, exist_ok=True)
    INSTALLED_PATH.parent.chmod(0o700)
    LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
    LOG_DIRECTORY.chmod(0o700)
    INSTALLATION_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    INSTALLATION_MANIFEST.parent.chmod(0o700)

    RUNTIME_BUNDLE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    RUNTIME_BUNDLE_DIRECTORY.chmod(0o700)
    shutil.copytree(
        ROOT / "src",
        RUNTIME_BUNDLE_DIRECTORY / "src",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (RUNTIME_BUNDLE_DIRECTORY / "config").mkdir(parents=True, exist_ok=True)
    (RUNTIME_BUNDLE_DIRECTORY / "docs" / "stages").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "config" / "tiered50_runtime_manifest.json", RUNTIME_BUNDLE_DIRECTORY / "config" / "tiered50_runtime_manifest.json")
    shutil.copy2(
        ROOT / "docs" / "stages" / "13-tiered-collector-readiness-evidence.json",
        RUNTIME_BUNDLE_DIRECTORY / "docs" / "stages" / "13-tiered-collector-readiness-evidence.json",
    )
    RUNTIME_BUNDLE_ENTRY.write_text(
        "#!/usr/bin/env python3\n"
        "from __future__ import annotations\n"
        "import sys\n"
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parent\n"
        "sys.path.insert(0, str(ROOT / 'src'))\n"
        "from cologne_transit_collector.macos_runner import main\n"
        "raise SystemExit(main())\n",
        encoding="utf-8",
    )
    RUNTIME_BUNDLE_ENTRY.chmod(0o700)

    rendered = build_installed_plist(activate=activate, runtime_config_path=runtime_config_path)
    with INSTALLED_PATH.open("wb") as handle:
        plistlib.dump(rendered, handle, fmt=plistlib.FMT_XML, sort_keys=False)
    INSTALLED_PATH.chmod(0o600)

    manifest = {
        "label": LABEL,
        "installed_plist_path": str(INSTALLED_PATH),
        "installed_plist_sha256": _sha256(INSTALLED_PATH),
        "tracked_template_path": str(TEMPLATE_PATH),
        "tracked_template_sha256": _sha256(TEMPLATE_PATH),
        "runtime_config_path": str(Path(runtime_config_path).expanduser().resolve()),
        "runtime_bundle_path": str(RUNTIME_BUNDLE_DIRECTORY),
        "activated": activate,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    with INSTALLATION_MANIFEST.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    INSTALLATION_MANIFEST.chmod(0o600)
    return manifest


def bootstrap(*, activate: bool = True) -> None:
    """Load the installed LaunchAgent without manually invoking its program.

    ``launchctl`` persists a per-label disabled override after a previous
    safety stop.  The plist's ``Disabled`` key alone does not clear that
    override, so synchronize the exact label state before bootstrapping it.
    """

    domain = f"gui/{os.getuid()}"
    # bootout is scoped to this exact user-local label and is idempotent when
    # the job is not currently loaded.
    subprocess.run(
        ["/bin/launchctl", "bootout", f"{domain}/{LABEL}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    state_command = "enable" if activate else "disable"
    state = subprocess.run(
        ["/bin/launchctl", state_command, f"{domain}/{LABEL}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if state.returncode != 0:
        raise RuntimeError(f"launchd {state_command} failed")
    completed = subprocess.run(
        ["/bin/launchctl", "bootstrap", domain, str(INSTALLED_PATH)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("launchd bootstrap failed")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--install-disabled", action="store_true", help="render the safe local artifact")
    mode.add_argument("--activate", action="store_true", help="render the approved production gate values")
    parser.add_argument("--runtime-config", type=Path, default=DEFAULT_RUNTIME_CONFIG_PATH)
    parser.add_argument("--bootstrap", action="store_true", help="load the exact installed artifact into launchd")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    activate = bool(args.activate)
    try:
        manifest = install(activate=activate, runtime_config_path=args.runtime_config)
        if args.bootstrap:
            bootstrap(activate=activate)
            manifest["launchd_bootstrapped"] = True
        print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, plistlib.InvalidFileException) as exc:
        print(f"LaunchAgent installation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
