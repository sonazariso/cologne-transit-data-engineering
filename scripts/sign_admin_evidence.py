#!/usr/bin/env python3
"""Sign a sanitized SQL administrative-evidence payload.

The input is produced by a controlled SQL read-only capture, commonly the
Windows Authentication workflow.  The private signing key is supplied by the
operator and is never copied into the artifact or repository.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.admin_evidence import (  # noqa: E402
    require_current_evidence_path,
    require_operator_staging_path,
    sign_payload,
    verify_signature,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--signing-key", type=Path, required=True)
    parser.add_argument(
        "--intermediate",
        action="store_true",
        help="write a Windows-side signed staging envelope; without this flag output must be the stable current artifact",
    )
    parser.add_argument(
        "--public-key",
        type=Path,
        default=(Path(os.environ["MSSQL_ADMIN_EVIDENCE_PUBLIC_KEY"]) if os.environ.get("MSSQL_ADMIN_EVIDENCE_PUBLIC_KEY") else None),
        help="public key used to verify the signature; defaults to MSSQL_ADMIN_EVIDENCE_PUBLIC_KEY",
    )
    args = parser.parse_args(argv)
    if args.public_key is None:
        raise SystemExit(
            "MSSQL_ADMIN_EVIDENCE_PUBLIC_KEY must point to the operator-controlled public key "
            "so the detached signature is independently verified"
        )
    if args.intermediate:
        output = require_operator_staging_path(args.output, repository_root=ROOT)
    else:
        output = require_current_evidence_path(args.output, repository_root=ROOT)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit("administrative evidence input is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise SystemExit("administrative evidence payload must be a JSON object")
    payload["artifact_integrity"] = sign_payload(payload, signing_key_path=args.signing_key)
    if args.public_key is not None and not verify_signature(
        payload,
        public_key_path=args.public_key,
        integrity=payload["artifact_integrity"],
    ):
        raise SystemExit("administrative-evidence signature could not be verified with the configured public key")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output.chmod(0o600)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
