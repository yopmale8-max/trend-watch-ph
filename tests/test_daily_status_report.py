import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from scripts.daily_status_report import build_report


class DailyStatusReportTests(unittest.TestCase):
    @patch("scripts.daily_status_report.fetch_workflow_runs", return_value=[])
    def test_no_runs_is_informational_not_an_alert(self, fetch_runs):
        now = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)

        subject, body, has_errors = build_report(
            "owner/repository",
            "test-token",
            now=now,
        )

        self.assertTrue(subject.startswith("No runs:"))
        self.assertIn("No matching workflow runs were found.", body)
        self.assertFalse(has_errors)
        fetch_runs.assert_called_once()


if __name__ == "__main__":
    unittest.main()
