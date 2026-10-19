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
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.domain import (  # noqa: E402
    MAC_SQL_CUTOVER_APPROVED_ENV,
    MAC_SQL_ENABLED_ENV,
    MAC_SQL_MODE_ENV,
    MAC_SQL_REQUIRED_TARGET_SCOPE,
    MAC_SQL_REQUIRED_VALIDATION_POLICY,
    MAC_SQL_REQUIRED_RUNTIME_STATE,
    MAC_SQL_RUNTIME_STATE_ENV,
    MAC_SQL_TARGET_SCOPE_ENV,
    MAC_SQL_VALIDATION_POLICY_ENV,
    MAC_TIERED50_CUTOVER_APPROVED_ENV,
    MAC_TIERED50_ENABLED_ENV,
    MAC_TIERED50_REQUIRED_TARGET_SCOPE,
    MAC_TIERED50_REQUIRED_VALIDATION_POLICY,
    MAC_TIERED50_TARGET_SCOPE_ENV,
    MAC_TIERED50_VALIDATION_POLICY_ENV,
)
from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    DEFAULT_RUNTIME_CONFIG_PATH,
    REQUIRED_PYTHON_INTERPRETER,
    RUNTIME_CONFIG_PATH_ENV,
)
from cologne_transit_collector.schedule import validate_launchd_wall_clock_schedule  # noqa: E402


LABEL = "com.cologne-transit-intelligence.mdd-collector"
TEMPLATE_PATH = ROOT / "deployment" / "launchd" / f"{LABEL}.plist"
INSTALLED_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
TIERED50_MODE = "tiered50"
LEGACY7_MODE = "legacy7"
TIERED50_LABEL = "com.cologne-transit-intelligence.mdd-tiered50-collector"
TIERED50_TEMPLATE_PATH = ROOT / "deployment" / "launchd" / f"{TIERED50_LABEL}.plist"
TIERED50_INSTALLED_PATH = Path.home() / "Library" / "LaunchAgents" / f"{TIERED50_LABEL}.plist"
LOG_DIRECTORY = Path.home() / "Library" / "Logs" / "CologneTransitIntelligence"
RUNTIME_BUNDLE_DIRECTORY = (
    Path.home() / "Library" / "Application Support" / "CologneTransitIntelligence" / "runtime-bundle"
)
RUNTIME_BUNDLE_ENTRY = RUNTIME_BUNDLE_DIRECTORY / "macos_launchd_entry.py"
TIERED50_RUNTIME_BUNDLE_ROOT = (
    Path.home() / "Library" / "Application Support" / "CologneTransitIntelligence" / "runtime-bundles"
)
INSTALLATION_MANIFEST = (
    Path.home() / "Library" / "Application Support" / "CologneTransitIntelligence" / "launchd-installation.json"
)
TIERED50_INSTALLATION_MANIFEST = (
    Path.home()
    / "Library"
    / "Application Support"
    / "CologneTransitIntelligence"
    / "tiered50-launchd-installation.json"
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


def _tiered50_bundle_fingerprint() -> str:
    """Hash the exact non-secret inputs copied into a Tiered50 bundle."""

    digest = hashlib.sha256()
    inputs = [
        path
        for path in (ROOT / "src").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    ]
    inputs.extend(
        (
            ROOT / "config" / "tiered50_runtime_manifest.json",
            ROOT / "docs" / "stages" / "13-tiered-collector-readiness-evidence.json",
        )
    )
    for path in sorted(inputs):
        digest.update(str(path.relative_to(ROOT)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def tiered50_runtime_bundle_directory() -> Path:
    return TIERED50_RUNTIME_BUNDLE_ROOT / f"tiered50-v1-{_tiered50_bundle_fingerprint()[:16]}"


def _runtime_bundle_paths(mode: str) -> tuple[Path, Path]:
    if mode == LEGACY7_MODE:
        return RUNTIME_BUNDLE_DIRECTORY, RUNTIME_BUNDLE_ENTRY
    if mode == TIERED50_MODE:
        bundle = tiered50_runtime_bundle_directory()
        return bundle, bundle / "macos_launchd_entry.py"
    raise ValueError(f"unsupported LaunchAgent mode: {mode}")


def _plist_has_secret(plist: Mapping[str, Any]) -> bool:
    serialized = plistlib.dumps(dict(plist), fmt=plistlib.FMT_XML).decode("utf-8", errors="replace")
    return any(marker in serialized for marker in SECRET_MARKERS)


def _mode_paths(mode: str) -> tuple[str, Path, Path, Path]:
    if mode == LEGACY7_MODE:
        return LABEL, TEMPLATE_PATH, INSTALLED_PATH, INSTALLATION_MANIFEST
    if mode == TIERED50_MODE:
        return TIERED50_LABEL, TIERED50_TEMPLATE_PATH, TIERED50_INSTALLED_PATH, TIERED50_INSTALLATION_MANIFEST
    raise ValueError(f"unsupported LaunchAgent mode: {mode}")


def _load_template(mode: str = TIERED50_MODE) -> dict[str, Any]:
    label, template_path, _installed_path, _installation_manifest = _mode_paths(mode)
    with template_path.open("rb") as handle:
        template = plistlib.load(handle)
    if template.get("Label") != label:
        raise ValueError("tracked LaunchAgent label is unexpected")
    if template.get("ProgramArguments", [None])[0] != REQUIRED_PYTHON_INTERPRETER:
        raise ValueError("tracked LaunchAgent interpreter is not the approved absolute path")
    if _plist_has_secret(template):
        raise ValueError("tracked LaunchAgent template contains a prohibited secret marker")
    if mode == TIERED50_MODE:
        validate_launchd_wall_clock_schedule(template)
    return template


def build_installed_plist(
    *,
    activate: bool,
    runtime_config_path: str | Path = DEFAULT_RUNTIME_CONFIG_PATH,
    mode: str = TIERED50_MODE,
) -> dict[str, Any]:
    """Render a machine-local plist without changing the tracked template."""

    if mode == LEGACY7_MODE and activate:
        raise RuntimeError("Legacy7 production activation is retired")
    label, _template_path, _installed_path, _installation_manifest = _mode_paths(mode)
    bundle_directory, bundle_entry = _runtime_bundle_paths(mode)
    plist = _load_template(mode)
    environment = dict(plist.get("EnvironmentVariables", {}))
    environment.update({
        "PYTHONPATH": str(bundle_directory / "src"),
        RUNTIME_CONFIG_PATH_ENV: str(Path(runtime_config_path).expanduser().resolve()),
        "COLLECTOR_RUNTIME_ROOT": str(bundle_directory),
    })
    if mode == LEGACY7_MODE:
        environment.update(
            {
                MAC_SQL_ENABLED_ENV: "1" if activate else "0",
                MAC_SQL_RUNTIME_STATE_ENV: MAC_SQL_REQUIRED_RUNTIME_STATE if activate else "UNCONFIRMED",
                MAC_SQL_CUTOVER_APPROVED_ENV: "1" if activate else "0",
                MAC_SQL_TARGET_SCOPE_ENV: MAC_SQL_REQUIRED_TARGET_SCOPE if activate else "UNAPPROVED",
                MAC_SQL_VALIDATION_POLICY_ENV: MAC_SQL_REQUIRED_VALIDATION_POLICY,
            }
        )
    else:
        environment.update(
            {
                MAC_SQL_MODE_ENV: "TIERED50",
                MAC_TIERED50_ENABLED_ENV: "1" if activate else "0",
                MAC_SQL_RUNTIME_STATE_ENV: MAC_SQL_REQUIRED_RUNTIME_STATE if activate else "UNCONFIRMED",
                MAC_TIERED50_CUTOVER_APPROVED_ENV: "1" if activate else "0",
                MAC_TIERED50_TARGET_SCOPE_ENV: (
                    MAC_TIERED50_REQUIRED_TARGET_SCOPE if activate else "UNAPPROVED"
                ),
                MAC_TIERED50_VALIDATION_POLICY_ENV: MAC_TIERED50_REQUIRED_VALIDATION_POLICY,
            }
        )
    for name in SECRET_MARKERS:
        environment.pop(name, None)
    plist["EnvironmentVariables"] = environment
    # A launchd job can expose PYTHONPATH in its diagnostic environment while
    # the interpreter still does not use it for module discovery.  The
    # installed artifact therefore calls an absolute user-local runtime
    # wrapper that inserts its bundled ``src`` before importing the runner.
    plist["ProgramArguments"] = [REQUIRED_PYTHON_INTERPRETER, str(bundle_entry)]
    if mode == TIERED50_MODE:
        plist["ProgramArguments"].extend(["--mode", TIERED50_MODE])
    plist["WorkingDirectory"] = str(bundle_directory)
    plist["Disabled"] = not activate
    log_stem = "mdd-collector" if mode == LEGACY7_MODE else "mdd-tiered50-collector"
    plist["StandardOutPath"] = str(LOG_DIRECTORY / f"{log_stem}.log")
    plist["StandardErrorPath"] = str(LOG_DIRECTORY / f"{log_stem}.err.log")
    if plist.get("Label") != label:
        raise ValueError("rendered LaunchAgent label changed unexpectedly")
    if _plist_has_secret(plist):
        raise ValueError("installed LaunchAgent contains a prohibited secret marker")
    if mode == TIERED50_MODE:
        validate_launchd_wall_clock_schedule(plist)
    return plist


def install(
    *,
    activate: bool,
    runtime_config_path: str | Path = DEFAULT_RUNTIME_CONFIG_PATH,
    mode: str = TIERED50_MODE,
) -> dict[str, Any]:
    """Write the user-local plist and its separate non-secret manifest."""

    if mode == LEGACY7_MODE:
        raise RuntimeError("Legacy7 production installation is retired")
    label, _template_path, installed_path, installation_manifest = _mode_paths(mode)
    installed_path.parent.mkdir(parents=True, exist_ok=True)
    installed_path.parent.chmod(0o700)
    LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
    LOG_DIRECTORY.chmod(0o700)
    installation_manifest.parent.mkdir(parents=True, exist_ok=True)
    installation_manifest.parent.chmod(0o700)

    bundle_directory, bundle_entry = _runtime_bundle_paths(mode)
    if mode == TIERED50_MODE:
        TIERED50_RUNTIME_BUNDLE_ROOT.mkdir(parents=True, exist_ok=True)
        TIERED50_RUNTIME_BUNDLE_ROOT.chmod(0o700)
        if not bundle_directory.exists():
            staging = TIERED50_RUNTIME_BUNDLE_ROOT / f".{bundle_directory.name}.staging-{uuid4().hex}"
            try:
                staging.mkdir(mode=0o700)
                shutil.copytree(
                    ROOT / "src",
                    staging / "src",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
                (staging / "config").mkdir(parents=True, exist_ok=True)
                (staging / "docs" / "stages").mkdir(parents=True, exist_ok=True)
                shutil.copy2(
                    ROOT / "config" / "tiered50_runtime_manifest.json",
                    staging / "config" / "tiered50_runtime_manifest.json",
                )
                shutil.copy2(
                    ROOT / "docs" / "stages" / "13-tiered-collector-readiness-evidence.json",
                    staging / "docs" / "stages" / "13-tiered-collector-readiness-evidence.json",
                )
                staging_entry = staging / "macos_launchd_entry.py"
                staging_entry.write_text(
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
                staging_entry.chmod(0o700)
                os.replace(staging, bundle_directory)
            except BaseException:
                if staging.exists():
                    shutil.rmtree(staging)
                raise
        bundle_directory.chmod(0o700)
        bundle_entry.chmod(0o700)

    rendered = build_installed_plist(
        activate=activate,
        runtime_config_path=runtime_config_path,
        mode=mode,
    )
    temporary_plist = installed_path.with_name(f".{installed_path.name}.tmp-{uuid4().hex}")
    with temporary_plist.open("wb") as handle:
        plistlib.dump(rendered, handle, fmt=plistlib.FMT_XML, sort_keys=False)
    os.replace(temporary_plist, installed_path)
    installed_path.chmod(0o600)

    manifest = {
        "label": label,
        "mode": mode,
        "installed_plist_path": str(installed_path),
        "installed_plist_sha256": _sha256(installed_path),
        "tracked_template_path": str(_template_path),
        "tracked_template_sha256": _sha256(_template_path),
        "runtime_config_path": str(Path(runtime_config_path).expanduser().resolve()),
        "runtime_bundle_path": str(bundle_directory),
        "schedule": validate_launchd_wall_clock_schedule(rendered),
        "activated": activate,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    with installation_manifest.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    installation_manifest.chmod(0o600)
    return manifest


def bootstrap(*, activate: bool = True, mode: str = TIERED50_MODE) -> None:
    """Load the installed LaunchAgent without manually invoking its program.

    ``launchctl`` persists a per-label disabled override after a previous
    safety stop.  The plist's ``Disabled`` key alone does not clear that
    override, so synchronize the exact label state before bootstrapping it.
    """

    if mode == LEGACY7_MODE:
        raise RuntimeError("Legacy7 production bootstrap is retired")
    label, _template_path, installed_path, _installation_manifest = _mode_paths(mode)
    domain = f"gui/{os.getuid()}"
    # bootout is scoped to this exact user-local label and is idempotent when
    # the job is not currently loaded.
    subprocess.run(
        ["/bin/launchctl", "bootout", f"{domain}/{label}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    state_command = "enable" if activate else "disable"
    state = subprocess.run(
        ["/bin/launchctl", state_command, f"{domain}/{label}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if state.returncode != 0:
        raise RuntimeError(f"launchd {state_command} failed")
    completed = subprocess.run(
        ["/bin/launchctl", "bootstrap", domain, str(installed_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("launchd bootstrap failed")


def retire_legacy7() -> dict[str, Any]:
    """Unload and disable only the historical Legacy7 LaunchAgent."""

    domain = f"gui/{os.getuid()}"
    qualified = f"{domain}/{LABEL}"
    bootout = subprocess.run(
        ["/bin/launchctl", "bootout", qualified],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    disable = subprocess.run(
        ["/bin/launchctl", "disable", qualified],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if INSTALLED_PATH.exists():
        with INSTALLED_PATH.open("rb") as handle:
            plist = plistlib.load(handle)
        plist["Disabled"] = True
        environment = dict(plist.get("EnvironmentVariables", {}))
        environment.update(
            {
                MAC_SQL_ENABLED_ENV: "0",
                MAC_SQL_CUTOVER_APPROVED_ENV: "0",
                MAC_SQL_TARGET_SCOPE_ENV: "UNAPPROVED",
                MAC_SQL_RUNTIME_STATE_ENV: "UNCONFIRMED",
            }
        )
        for name in SECRET_MARKERS:
            environment.pop(name, None)
        plist["EnvironmentVariables"] = environment
        temporary_plist = INSTALLED_PATH.with_name(f".{INSTALLED_PATH.name}.retired-{uuid4().hex}")
        with temporary_plist.open("wb") as handle:
            plistlib.dump(plist, handle, fmt=plistlib.FMT_XML, sort_keys=False)
        os.replace(temporary_plist, INSTALLED_PATH)
        INSTALLED_PATH.chmod(0o600)

    state = subprocess.run(
        ["/bin/launchctl", "print", qualified],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if state.returncode == 0:
        raise RuntimeError("Legacy7 LaunchAgent is still loaded after retirement")
    processes = subprocess.run(
        ["/bin/ps", "-axo", "pid=,command="],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )
    legacy_processes = [
        line.strip()
        for line in processes.stdout.splitlines()
        if "--mode legacy7" in line
        and (
            "macos_runner" in line
            or str(RUNTIME_BUNDLE_ENTRY) in line
            or "com.cologne-transit-intelligence.mdd-collector" in line
        )
    ]
    if legacy_processes:
        raise RuntimeError("Legacy7 runner process is still present")
    return {
        "status": "RETIRED",
        "label": LABEL,
        "launchctl_bootout_returncode": bootout.returncode,
        "launchctl_disable_returncode": disable.returncode,
        "launchctl_print_returncode": state.returncode,
        "legacy_process_count": len(legacy_processes),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--install-disabled", action="store_true", help="render the safe local artifact")
    mode.add_argument("--activate", action="store_true", help="render the approved production gate values")
    parser.add_argument("--mode", choices=(LEGACY7_MODE, TIERED50_MODE), default=TIERED50_MODE)
    parser.add_argument("--runtime-config", type=Path, default=DEFAULT_RUNTIME_CONFIG_PATH)
    parser.add_argument("--bootstrap", action="store_true", help="load the exact installed artifact into launchd")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    activate = bool(args.activate)
    try:
        manifest = install(activate=activate, runtime_config_path=args.runtime_config, mode=args.mode)
        if args.bootstrap:
            bootstrap(activate=activate, mode=args.mode)
            manifest["launchd_bootstrapped"] = True
        print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, plistlib.InvalidFileException) as exc:
        print(f"LaunchAgent installation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
