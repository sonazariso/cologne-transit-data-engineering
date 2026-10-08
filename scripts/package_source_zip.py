#!/usr/bin/env python3
"""Create a working-tree source ZIP without macOS metadata or local state.

The archive is built from Git-visible working-tree paths so tracked edits and
non-ignored source additions are included without packaging ignored secrets,
databases, caches, or runtime data.  macOS metadata is filtered again at the
archive boundary because an already-present filesystem artifact must never
become source evidence merely because it was copied into the working tree.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable


MACOS_METADATA_NAMES = frozenset({".DS_Store"})
MACOS_METADATA_DIRECTORY = "__MACOSX"
RUNTIME_STATE_NAMES = frozenset(
    {
        ".env",
        "credentials.json",
        "secrets.json",
        "keychain.json",
        "launchd-installation.json",
        "tiered50-launchd-installation.json",
    }
)
RUNTIME_STATE_SUFFIXES = frozenset(
    {
        ".db",
        ".sqlite",
        ".sqlite3",
        ".sqlite-shm",
        ".sqlite-wal",
        ".log",
        ".err.log",
        ".zip",
        ".pem",
        ".p12",
        ".key",
    }
)
NON_SOURCE_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        ".tox",
        ".venv",
        "venv",
    }
)


def is_macos_metadata(relative_path: str | Path) -> bool:
    """Return whether a relative path is Apple filesystem metadata."""

    path = PurePosixPath(str(relative_path).replace(os.sep, "/"))
    if MACOS_METADATA_DIRECTORY in path.parts:
        return True
    name = path.name
    return name in MACOS_METADATA_NAMES or name.startswith("._")


def is_forbidden_source_member(relative_path: str | Path) -> bool:
    """Return whether a path is local runtime state rather than source."""

    path = PurePosixPath(str(relative_path).replace(os.sep, "/"))
    name = path.name.casefold()
    if is_macos_metadata(path):
        return True
    if name in RUNTIME_STATE_NAMES:
        return True
    if name.startswith(".env.") and name != ".env.example":
        return True
    if any(marker in name for marker in ("credential", "secret", "keychain")):
        return True
    if any(name.endswith(suffix) for suffix in RUNTIME_STATE_SUFFIXES):
        return True
    # The approved manifest is source configuration. Other runtime JSON
    # artifacts are machine-local state and must not enter source evidence.
    if name.endswith(".json") and "runtime" in name and path.as_posix() != "config/tiered50_runtime_manifest.json":
        return True
    return False


def git_visible_paths(repository_root: str | Path) -> tuple[Path, ...]:
    """Return source paths from Git, or from a sanitized extracted tree.

    A real working tree keeps the existing Git-visible behavior, including
    tracked edits and non-ignored additions.  A source ZIP extracted without
    ``.git`` is still a legitimate review input, so the fallback walks only
    regular files and applies the same source/runtime filters.
    """

    root = Path(repository_root).resolve()
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError):
        return _filesystem_visible_paths(root)
    paths: list[Path] = []
    for item in result.stdout.decode("utf-8").split("\0"):
        if not item:
            continue
        candidate = (root / item).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file() and not is_forbidden_source_member(candidate.relative_to(root)):
            paths.append(candidate)
    return tuple(sorted(set(paths), key=lambda path: path.relative_to(root).as_posix()))


def _filesystem_visible_paths(root: Path) -> tuple[Path, ...]:
    """Enumerate a sanitized source tree when Git metadata is unavailable."""

    paths: list[Path] = []
    for candidate in root.rglob("*"):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        relative = candidate.relative_to(root)
        if any(part in NON_SOURCE_DIRECTORY_NAMES for part in relative.parts):
            continue
        if not is_forbidden_source_member(relative):
            paths.append(candidate.resolve())
    return tuple(sorted(set(paths), key=lambda path: path.relative_to(root).as_posix()))


def source_members(repository_root: str | Path, *, output_path: str | Path | None = None) -> Iterable[tuple[Path, str]]:
    """Yield source files and their POSIX archive names."""

    root = Path(repository_root).resolve()
    output = Path(output_path).resolve() if output_path is not None else None
    for path in git_visible_paths(root):
        if output is not None and path == output:
            continue
        relative = path.relative_to(root)
        archive_name = relative.as_posix()
        if not is_forbidden_source_member(relative):
            yield path, archive_name


def verify_source_zip(output_path: str | Path) -> tuple[str, ...]:
    """Return forbidden archive members, raising if the archive is unreadable."""

    output = Path(output_path).expanduser().resolve()
    with zipfile.ZipFile(output, "r") as archive:
        return tuple(name for name in archive.namelist() if is_forbidden_source_member(name))


def create_source_zip(repository_root: str | Path, output_path: str | Path) -> Path:
    """Write and verify a sanitized source ZIP, then return its absolute path."""

    root = Path(repository_root).resolve()
    output = Path(output_path).expanduser().resolve()
    if output == root or root in output.parents:
        output.parent.mkdir(parents=True, exist_ok=True)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, archive_name in source_members(root, output_path=output):
            archive.write(path, archive_name)
    forbidden_members = verify_source_zip(output)
    if forbidden_members:
        formatted = ", ".join(repr(member) for member in forbidden_members)
        raise ValueError(f"source ZIP contains forbidden macOS metadata: {formatted}")
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        output = create_source_zip(args.repository, args.output)
    except (OSError, subprocess.CalledProcessError, UnicodeError, ValueError, zipfile.BadZipFile) as exc:
        print(f"source ZIP creation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
