import unittest
from datetime import datetime, timezone

from cologne_transit_collector.admin_evidence import (
    _parse_utc,
    AdminEvidenceError,
)


class AdminEvidenceTimestampTests(unittest.TestCase):

    def test_dotnet_seven_digit_utc(self):
        actual = _parse_utc(
            "2026-10-12T08:11:17.2855953Z",
            field="generated_at_utc",
        )
        expected = datetime(
            2026, 10, 12, 8, 11, 17, 285595,
            tzinfo=timezone.utc,
        )
        self.assertEqual(actual, expected)

    def test_dotnet_seven_digit_offset(self):
        actual = _parse_utc(
            "2026-10-12T10:11:17.2855953+02:00",
            field="generated_at_utc",
        )
        expected = datetime(
            2026, 10, 12, 8, 11, 17, 285595,
            tzinfo=timezone.utc,
        )
        self.assertEqual(actual, expected)

    def test_timezone_required(self):
        with self.assertRaises(AdminEvidenceError):
            _parse_utc(
                "2026-10-12T08:11:17.2855953",
                field="generated_at_utc",
            )


if __name__ == "__main__":
    unittest.main()
