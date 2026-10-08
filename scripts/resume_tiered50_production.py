#!/usr/bin/env python3
"""Explicitly resume the already activated Tiered50 production panel.

This entry point never invokes first-cutover logic.  It delegates to the
resume-only lifecycle in ``activate_tiered50_production.py`` and keeps the
operation visibly distinct in operator history and review artifacts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from activate_tiered50_production import resume_tiered50_production  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the explicitly requested resume after all gates pass",
    )
    parser.add_argument("--resume-key", help="idempotency key for a new resume transition")
    args = parser.parse_args(argv)
    if not args.apply:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason": "resume requires explicit --apply; no SQL or LaunchAgent action was performed",
                    "operation": "RESUME",
                    "mdd_requests_issued": 0,
                    "writes_performed": 0,
                },
                sort_keys=True,
            )
        )
        return 2
    try:
        result = resume_tiered50_production(resume_key=args.resume_key)
    except Exception as exc:
        result = {
            "status": "BLOCKED",
            "operation": "RESUME",
            "error_type": type(exc).__name__,
            "reason": str(exc)[:500],
            "mdd_requests_issued": 0,
            "writes_performed": 0,
        }
        rollback = getattr(exc, "rollback", None)
        if isinstance(rollback, dict):
            result["rollback"] = rollback
            result["original_failure"] = {
                "error_type": getattr(exc, "original_failure_type", None),
                "reason": getattr(exc, "original_failure_reason", None),
            }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result.get("status") in {"PASS", "IDEMPOTENT_ALREADY_ACTIVE", "IDEMPOTENT_ALREADY_RECORDED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
