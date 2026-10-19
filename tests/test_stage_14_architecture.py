from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from stage_14_validation import (  # noqa: E402
    ADMIN_EVIDENCE,
    APPROVED_SQL_SERVER_TEXT_FILES,
    architecture_check,
)


class Stage14ArchitectureTests(unittest.TestCase):
    def test_signed_administrative_evidence_sql_identity_text_is_approved(self) -> None:
        source = ADMIN_EVIDENCE.read_text(encoding="utf-8").lower()
        self.assertIn("sql server", source)
        self.assertIn(ADMIN_EVIDENCE, APPROVED_SQL_SERVER_TEXT_FILES)
        self.assertFalse(any(str(ADMIN_EVIDENCE) in error for error in architecture_check()))


if __name__ == "__main__":
    unittest.main()
