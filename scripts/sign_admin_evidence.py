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

from cologne_transit_collector.admin_evidence import sign_payload, verify_signature  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--signing-key", type=Path, required=True)
    parser.add_argument(
        "--public-key",
        type=Path,
        default=(Path(os.environ["MSSQL_ADMIN_EVIDENCE_PUBLIC_KEY"]) if os.environ.get("MSSQL_ADMIN_EVIDENCE_PUBLIC_KEY") else None),
        help="public key used to verify the signature; defaults to MSSQL_ADMIN_EVIDENCE_PUBLIC_KEY",
    )
    args = parser.parse_args(argv)
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("administrative evidence payload must be a JSON object")
    payload["artifact_integrity"] = sign_payload(payload, signing_key_path=args.signing_key)
    if args.public_key is not None and not verify_signature(
        payload,
        public_key_path=args.public_key,
        integrity=payload["artifact_integrity"],
    ):
        raise SystemExit("administrative-evidence signature could not be verified with the configured public key")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.output.chmod(0o600)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
