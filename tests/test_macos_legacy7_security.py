from __future__ import annotations

import plistlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cologne_transit_collector.domain import (  # noqa: E402
    LEGACY7_SELECTION_POLICY_VERSION,
    MAC_LEGACY7_PRODUCTION_COLLECTION_CONTEXT,
)
from provision_macos_runtime_login import (  # noqa: E402
    GRANT_STATEMENTS,
    RUNTIME_LOGIN,
    RUNTIME_ROLE,
    _generate_password,
)


class MacLegacy7SecurityTests(unittest.TestCase):
    def test_generated_password_is_strong_and_not_printed_by_contract(self) -> None:
        password = _generate_password()
        self.assertGreaterEqual(len(password), 64)
        self.assertTrue(any(character.isupper() for character in password))
        self.assertTrue(any(character.islower() for character in password))
        self.assertTrue(any(character.isdigit() for character in password))
        self.assertTrue(any(not character.isalnum() for character in password))
        self.assertNotIn(password, " ".join(GRANT_STATEMENTS))

    def test_runtime_grants_are_object_scoped_and_have_no_destructive_permission(self) -> None:
        self.assertEqual(RUNTIME_LOGIN, "CologneTransitCollector")
        self.assertEqual(RUNTIME_ROLE, "CologneTransitCollectorRuntime")
        serialized = "\n".join(GRANT_STATEMENTS).upper()
        self.assertNotIn("DELETE", serialized)
        self.assertNotIn("ALTER", serialized)
        self.assertNotIn("DROP", serialized)
        self.assertNotIn("CONTROL SERVER", serialized)
        self.assertNotIn("DB_OWNER", serialized)
        self.assertNotIn("SYSADMIN", serialized)
        self.assertTrue(all("OBJECT::" in statement or "EXECUTE ON OBJECT::" in statement for statement in GRANT_STATEMENTS))
        self.assertTrue(any("MDDREAL TIMESAMPLINGSLOT".replace(" ", "") in statement.replace(" ", "").upper() for statement in GRANT_STATEMENTS))

    def test_future_lineage_and_safe_scheduler_contract_are_explicit(self) -> None:
        migration = (ROOT / "sql" / "mdd-mac-collector" / "create-source-faithful-persistence.sql").read_text(
            encoding="utf-8"
        )
        self.assertIn("SamplingSlot INT NULL", migration)
        self.assertIn("SelectionPolicyVersion VARCHAR(80) NULL", migration)
        self.assertIn("ctl.MddMacMethodologyEvidence", migration)
        self.assertEqual(MAC_LEGACY7_PRODUCTION_COLLECTION_CONTEXT, "MAC_LEGACY7_PRODUCTION")
        self.assertEqual(LEGACY7_SELECTION_POLICY_VERSION, "LEGACY7_SQL_SLOT_V1")
        with (ROOT / "deployment" / "launchd" / "com.cologne-transit-intelligence.mdd-collector.plist").open("rb") as handle:
            plist = plistlib.load(handle)
        self.assertTrue(plist["Disabled"])
        self.assertFalse(plist["RunAtLoad"])


if __name__ == "__main__":
    unittest.main()
