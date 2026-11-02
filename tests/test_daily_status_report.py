import unittest
import json
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import patch

from scripts.daily_status_report import (
    build_report,
    fetch_publish_status,
)


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

    @patch(
        "scripts.daily_status_report.fetch_publish_status",
        return_value={
            "schema_version": 1,
            "articles_created": 2,
            "articles_updated": 1,
            "trends_skipped": 3,
            "trends_deferred": 4,
            "retry_queue_remaining": 5,
        },
    )
    @patch("scripts.daily_status_report.fetch_workflow_runs")
    def test_report_distinguishes_posted_articles_from_successful_runs(
        self,
        fetch_runs,
        fetch_status,
    ):
        now = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)
        fetch_runs.return_value = [{
            "id": 123,
            "run_number": 42,
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-11-01T23:00:00Z",
            "html_url": "https://github.com/example/repo/actions/runs/123",
        }]

        subject, body, has_errors = build_report(
            "owner/repository",
            "test-token",
            now=now,
        )

        self.assertIn("Article publishing in the reporting window:", body)
        self.assertIn("Successfully posted: 2 new article(s), 1 refreshed article(s)", body)
        self.assertIn(
            "Article status: 2 new posted, 1 refreshed, 3 skipped, 4 deferred, "
            "5 remaining in retry queue.",
            body,
        )
        self.assertFalse(has_errors)
        fetch_status.assert_called_once_with("owner/repository", "test-token", 123)

    @patch("scripts.daily_status_report.fetch_publish_status", return_value={
        "schema_version": 1,
        "articles_created": 0,
        "articles_updated": 0,
        "trends_skipped": 2,
        "trends_deferred": 1,
        "retry_queue_remaining": 1,
    })
    @patch("scripts.daily_status_report.fetch_workflow_runs")
    def test_report_says_when_a_successful_deployment_posted_no_articles(
        self,
        fetch_runs,
        _fetch_status,
    ):
        now = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)
        fetch_runs.return_value = [{
            "id": 124,
            "run_number": 43,
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-11-01T23:00:00Z",
            "html_url": "https://github.com/example/repo/actions/runs/124",
        }]

        _, body, has_errors = build_report("owner/repository", "test-token", now=now)

        self.assertIn("Successfully posted: 0 new article(s), 0 refreshed article(s)", body)
        self.assertIn("No new or refreshed article was posted in this run.", body)
        self.assertFalse(has_errors)

    @patch("scripts.daily_status_report.fetch_publish_status", return_value={
        "schema_version": 1,
        "articles_created": 1,
        "articles_updated": 2,
        "trends_skipped": 0,
        "trends_deferred": 0,
        "retry_queue_remaining": 0,
    })
    @patch("scripts.daily_status_report.fetch_workflow_runs")
    def test_failed_deployment_drafts_are_not_counted_as_posted(
        self,
        fetch_runs,
        _fetch_status,
    ):
        now = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)
        fetch_runs.return_value = [{
            "id": 125,
            "run_number": 44,
            "status": "completed",
            "conclusion": "failure",
            "created_at": "2026-11-01T23:00:00Z",
            "html_url": "https://github.com/example/repo/actions/runs/125",
        }]

        _, body, has_errors = build_report("owner/repository", "test-token", now=now)

        self.assertIn("Successfully posted: 0 new article(s), 0 refreshed article(s)", body)
        self.assertIn(
            "Article status: deployment did not succeed; generated articles are not counted as posted.",
            body,
        )
        self.assertTrue(has_errors)

    @patch("scripts.daily_status_report.github_api_request")
    def test_fetch_publish_status_reads_the_run_artifact(self, api_request):
        status = {
            "schema_version": 1,
            "articles_created": 1,
            "articles_updated": 0,
            "trends_skipped": 2,
            "trends_deferred": 3,
            "retry_queue_remaining": 3,
        }
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("publish_status.json", json.dumps(status))
        api_request.side_effect = [
            json.dumps({
                "artifacts": [{
                    "id": 987,
                    "name": "trendwatch-publish-status-123",
                    "expired": False,
                }],
            }).encode(),
            buffer.getvalue(),
        ]

        result = fetch_publish_status("owner/repository", "test-token", 123)

        self.assertEqual(result, status)
        self.assertEqual(api_request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
