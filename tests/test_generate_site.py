import os
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from google.genai import errors

os.environ.setdefault("GEMINI_API_KEY", "test-only-not-a-real-key")

import generate_site


class FeedResponse:
    status = 200

    def __init__(self, content):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.content


class GoogleTrendsFeedTests(unittest.TestCase):
    def setUp(self):
        self.original_rss_url = generate_site.NICHE_CONFIG["rss_url"]
        generate_site.NICHE_CONFIG["rss_url"] = "https://example.com/trends.xml"

    def tearDown(self):
        generate_site.NICHE_CONFIG["rss_url"] = self.original_rss_url

    def test_extracts_nested_news_items_and_unique_slugs(self):
        feed = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"
             xmlns:ht="https://trends.google.com/trending/rss">
          <channel>
            <title>Google Trends</title>
            <item>
              <title>Sample topic</title>
              <ht:approx_traffic>200+</ht:approx_traffic>
              <pubDate>Sat, 31 Oct 2026 10:00:00 GMT</pubDate>
              <ht:news_item>
                <ht:news_item_title>First source headline</ht:news_item_title>
                <ht:news_item_url>https://example.com/first</ht:news_item_url>
                <ht:news_item_source>Example Publisher</ht:news_item_source>
                <ht:news_item_snippet>First source summary</ht:news_item_snippet>
              </ht:news_item>
              <ht:news_item>
                <ht:news_item_title>Second source headline</ht:news_item_title>
                <ht:news_item_url>https://example.com/second</ht:news_item_url>
                <ht:news_item_source>Another Publisher</ht:news_item_source>
                <ht:news_item_snippet>Second source summary</ht:news_item_snippet>
              </ht:news_item>
            </item>
            <item>
              <title>Sample topic</title>
              <ht:approx_traffic>100+</ht:approx_traffic>
            </item>
          </channel>
        </rss>"""
        response = FeedResponse(feed)

        with patch.object(generate_site, "urlopen", return_value=response):
            trends = generate_site.fetch_google_trends()

        self.assertEqual(len(trends), 2)
        self.assertEqual(trends[0]["slug"], "sample-topic")
        self.assertEqual(trends[1]["slug"], "sample-topic-2")
        self.assertEqual(trends[0]["traffic"], "200+")
        self.assertEqual(len(trends[0]["news"]), 2)
        self.assertEqual(
            trends[0]["news"][0],
            {
                "title": "First source headline",
                "url": "https://example.com/first",
                "source": "Example Publisher",
                "snippet": "First source summary",
            },
        )
        self.assertEqual(trends[0]["published_at"], "2026-10-31T10:00:00Z")

    def test_rejects_malformed_feed(self):
        with patch.object(
            generate_site,
            "urlopen",
            return_value=FeedResponse(b"<rss><channel>"),
        ):
            with self.assertRaises(RuntimeError):
                generate_site.fetch_google_trends()

    def test_source_evidence_requires_a_safe_absolute_http_url(self):
        self.assertTrue(generate_site.has_source_evidence({
            "news": [{"title": "Headline", "url": "https://example.com/story"}],
        }))
        self.assertFalse(generate_site.has_source_evidence({
            "news": [{"title": "Headline", "url": "javascript:alert(1)"}],
        }))


class ArticleCopyTests(unittest.TestCase):
    def valid_article(self, headline="Where to check the latest PBA schedule"):
        return {
            "status": "SUCCESS",
            "headline": headline,
            "meta_description": (
                "Check the PBA's official channels for current fixtures and changes."
            ),
            "html_content": (
                "<p>Looking for the latest PBA fixture? This guide explains what the "
                "trend feed can confirm, where the limits are, and which official "
                "source to check before making plans.</p>"
                '<div class="key-takeaways"><h3>Quick Takeaways</h3><ul>'
                "<li>The feed lists a schedule query, not a confirmed fixture.</li>"
                "<li>Official league listings are the best place to check changes.</li>"
                "<li>Confirm venue and time close to game day.</li>"
                "</ul></div>"
                "<h2>What the Trend Feed Shows</h2>"
                "<p>The feed shows that this query appeared in its Philippines results. "
                "It does not identify a game, date, team, or reason for the search interest. "
                "That distinction matters because a query is not itself a fixture listing.</p>"
                "<h2>What Current Sources Report</h2>"
                "<p>The supplied source coverage points readers toward league information, "
                "but it does not verify a specific upcoming game. Check the official PBA "
                "listing for an event's date, venue, participating teams, and any changes. "
                "Do not rely on an older schedule when planning a trip.</p>"
                "<h2>Philippine Context</h2>"
                "<p>The Philippines label describes the feed's geographic scope. It cannot "
                "tell us what an individual searched for or whether they plan to attend. "
                "For a local game, confirm the details with the league before travelling.</p>"
                "<h2>What to Verify Next</h2>"
                "<p>Open the PBA's official event information and check the date and venue. "
                "If you need broadcast details, verify the specific game with the league "
                "and broadcaster because coverage can vary by event.</p>"
                "<h2>Frequently Asked Questions</h2>"
                '<div class="faq-list">'
                '<div class="faq-card"><h3>Does this trend show today\'s schedule?</h3>'
                "<p>No. It shows a query in the feed, not current fixtures. Use the "
                "official league listing to check today's schedule and changes.</p></div>"
                '<div class="faq-card"><h3>Why was the schedule searched?</h3>'
                "<p>The feed does not explain why. It cannot establish that a specific "
                "game or announcement prompted the searches.</p></div>"
                '<div class="faq-card"><h3>Where should I check before travelling?</h3>'
                "<p>Check the PBA's official event listing for the latest venue and time, "
                "then verify any last-minute updates before leaving.</p></div>"
                "</div>"
            ),
        }

    def test_validation_requires_a_concise_headline_and_lead(self):
        article = self.valid_article()
        validated = generate_site.validate_ai_content(article)

        self.assertEqual(validated["headline"], article["headline"])
        self.assertTrue(validated["html_content"].startswith("<p>Looking for"))
        with self.assertRaisesRegex(ValueError, "headline"):
            generate_site.validate_ai_content(
                self.valid_article("A" * 91)
            )

    def test_article_page_uses_the_generated_reader_focused_headline(self):
        article = self.valid_article()
        html = generate_site.build_page_html(
            {
                "title": "PBA schedule",
                "slug": "pba-schedule-test",
                "news": [],
            },
            article,
        )

        self.assertIn("<h1>Where to check the latest PBA schedule</h1>", html)
        self.assertIn(
            "<title>Where to check the latest PBA schedule | TrendWatch Philippines</title>",
            html,
        )

    def test_homepage_archive_uses_the_generated_article_headline(self):
        original_output_dir = generate_site.NICHE_CONFIG["output_dir"]
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            (output_dir / "index.html").write_text(
                "<!-- TREND_CARDS_START --><!-- TREND_CARDS_END -->",
                encoding="utf-8",
            )
            (output_dir / "pba-schedule.html").write_text(
                "<html></html>",
                encoding="utf-8",
            )
            generate_site.NICHE_CONFIG["output_dir"] = temp_dir
            try:
                generate_site.update_index_page([{
                    "slug": "pba-schedule",
                    "title": "PBA schedule",
                    "article_headline": "Where to check the latest PBA schedule",
                    "traffic": "1000+",
                    "category": "Basketball",
                    "meta_description": "Check the PBA's official fixture listings.",
                    "generated_at": "2026-11-02T00:00:00Z",
                }])
            finally:
                generate_site.NICHE_CONFIG["output_dir"] = original_output_dir

            index_html = (output_dir / "index.html").read_text(encoding="utf-8")
            archive = json.loads(
                (output_dir / "trends_archive.json").read_text(encoding="utf-8")
            )

        self.assertIn(">Where to Check the Latest PBA Schedule</a>", index_html)
        self.assertEqual(archive[0]["title"], "Where to check the latest PBA schedule")

    def test_publish_status_is_written_in_a_machine_readable_shape(self):
        original_queue_path = generate_site.NICHE_CONFIG["retry_queue_path"]
        with tempfile.TemporaryDirectory() as temp_dir:
            generate_site.NICHE_CONFIG["retry_queue_path"] = str(
                Path(temp_dir) / "pending_trends.json"
            )
            try:
                generate_site.write_publish_status(2, 1, 3, 4, 5)
            finally:
                generate_site.NICHE_CONFIG["retry_queue_path"] = original_queue_path

            status = json.loads(
                (Path(temp_dir) / "publish_status.json").read_text(encoding="utf-8")
            )

        self.assertEqual(status, {
            "schema_version": 1,
            "articles_created": 2,
            "articles_updated": 1,
            "trends_skipped": 3,
            "trends_deferred": 4,
            "retry_queue_remaining": 5,
        })


class GeminiRetryTests(unittest.TestCase):
    def test_editorial_guidance_is_topic_specific_and_has_a_general_fallback(self):
        basketball_guidance = generate_site.editorial_guidance_for_trend({
            "title": "PBA schedule",
            "category": "Basketball",
        })
        general_guidance = generate_site.editorial_guidance_for_trend({
            "title": "A topic with no mapped category",
            "category": "Other",
        })

        self.assertIn("fixtures, results, tickets", basketball_guidance)
        self.assertIn("one concrete question", general_guidance)

    def test_generation_prompt_requires_reader_value_and_topic_guidance(self):
        trend = {
            "title": "PBA schedule",
            "category": "Basketball",
            "news": [{
                "title": "League fixture announcement",
                "source": "Example Publisher",
                "url": "https://example.com/fixture",
                "snippet": "The league shared an update.",
            }],
        }
        response = type("Response", (), {"text": "SKIP"})()

        with patch.object(
            generate_site.client.models,
            "generate_content",
            return_value=response,
        ) as generate:
            self.assertIsNone(generate_site.generate_ai_content(trend))

        prompt = generate.call_args.kwargs["contents"]
        self.assertIn("answer a practical reader question", prompt)
        self.assertIn("Topic-specific editorial direction:", prompt)
        self.assertIn("fixtures, results, tickets, or official coverage", prompt)
        self.assertIn("reader-first article", prompt)
        self.assertIn("reader's likely question", prompt)
        self.assertIn("accurate, reader-focused headline", prompt)
        self.assertIn("Do not force slang, Taglish, or local references", prompt)
        self.assertIn("Do not describe this automatically generated article as reviewed", prompt)

    def test_scheduled_run_respects_generation_request_cap(self):
        trends = [
            {"title": f"Topic {index}", "slug": f"topic-{index}", "news": []}
            for index in range(5)
        ]
        original_output_dir = generate_site.NICHE_CONFIG["output_dir"]
        original_queue_path = generate_site.NICHE_CONFIG["retry_queue_path"]

        with tempfile.TemporaryDirectory() as temp_dir:
            generate_site.NICHE_CONFIG["output_dir"] = temp_dir
            generate_site.NICHE_CONFIG["retry_queue_path"] = str(
                Path(temp_dir) / "pending_trends.json"
            )
            try:
                with (
                    patch.object(generate_site, "get_site_url", return_value=""),
                    patch.object(generate_site, "synchronize_canonical_urls"),
                    patch.object(generate_site, "fetch_google_trends", return_value=trends),
                    patch.object(generate_site, "has_source_evidence", return_value=True),
                    patch.object(
                        generate_site,
                        "generate_ai_content_with_retry",
                        return_value=(
                            {
                                "headline": "Sample reader-focused headline",
                                "meta_description": "sample",
                                "html_content": "<p>sample</p>",
                            },
                            1,
                            False,
                            None,
                        ),
                    ) as generate,
                    patch.object(generate_site, "build_page_html", return_value="<html></html>"),
                    patch.object(generate_site, "update_index_page"),
                    patch.object(generate_site, "install_cloudflare_beacon"),
                ):
                    generate_site.main()
            finally:
                generate_site.NICHE_CONFIG["output_dir"] = original_output_dir
                generate_site.NICHE_CONFIG["retry_queue_path"] = original_queue_path

        self.assertEqual(generate.call_count, generate_site.MAX_TREND_GENERATION_CALLS)
        self.assertEqual(
            [call.args[1] for call in generate.call_args_list],
            [3, 2, 1],
        )

    def test_quota_exhaustion_stops_without_retrying(self):
        quota_error = errors.ClientError(
            429,
            {"error": {"message": "Generate requests per day limit exceeded"}},
        )

        with patch.object(
            generate_site,
            "generate_ai_content",
            side_effect=quota_error,
        ) as generate:
            with self.assertRaises(generate_site.GeminiQuotaExceeded):
                generate_site.generate_ai_content_with_retry(
                    {"title": "Sample topic"},
                    request_budget=5,
                )

        generate.assert_called_once()

    def test_daily_quota_error_preserves_server_retry_delay(self):
        quota_error = errors.ClientError(
            429,
            {"error": {"message": "Generate requests per day limit exceeded"}},
            response=httpx.Response(429, headers={"Retry-After": "3600"}),
        )

        with patch.object(
            generate_site,
            "generate_ai_content",
            side_effect=quota_error,
        ) as generate:
            with self.assertRaises(generate_site.GeminiQuotaExceeded) as raised:
                generate_site.generate_ai_content_with_retry(
                    {"title": "Sample topic"},
                    request_budget=3,
                )

        self.assertEqual(raised.exception.retry_after_seconds, 3600)
        generate.assert_called_once()

    def test_temporary_rate_limit_honors_retry_after_header(self):
        rate_limit = errors.ClientError(
            429,
            {"error": {"message": "Too many requests"}},
            response=httpx.Response(429, headers={"Retry-After": "8"}),
        )

        with (
            patch.object(
                generate_site,
                "generate_ai_content",
                side_effect=[rate_limit, {"status": "SUCCESS"}],
            ) as generate,
            patch.object(generate_site.time, "sleep") as sleep,
        ):
            result, requests_used, transient_failure, retry_after = (
                generate_site.generate_ai_content_with_retry(
                    {"title": "Sample topic"},
                    request_budget=2,
                )
            )

        self.assertEqual(result, {"status": "SUCCESS"})
        self.assertEqual(requests_used, 2)
        self.assertFalse(transient_failure)
        self.assertIsNone(retry_after)
        sleep.assert_called_once_with(8)
        self.assertEqual(
            [call.kwargs["model"] for call in generate.call_args_list],
            [generate_site.PRIMARY_GEMINI_MODEL] * 2,
        )

    def test_temporary_rate_limit_without_retry_hint_uses_bounded_backoff(self):
        rate_limit = errors.ClientError(
            429,
            {"error": {"message": "Too many requests"}},
        )

        with (
            patch.object(
                generate_site,
                "generate_ai_content",
                side_effect=[rate_limit, {"status": "SUCCESS"}],
            ),
            patch.object(generate_site.random, "uniform", return_value=0),
            patch.object(generate_site.time, "sleep") as sleep,
        ):
            result, requests_used, transient_failure, retry_after = (
                generate_site.generate_ai_content_with_retry(
                    {"title": "Sample topic"},
                    request_budget=2,
                )
            )

        self.assertEqual(result, {"status": "SUCCESS"})
        self.assertEqual(requests_used, 2)
        self.assertFalse(transient_failure)
        self.assertIsNone(retry_after)
        sleep.assert_called_once_with(
            generate_site.GEMINI_RETRY_BASE_DELAY_SECONDS / 2
        )

    def test_long_retry_after_defers_rate_limited_trend_without_fallback(self):
        rate_limit = errors.ClientError(
            429,
            {"error": {"message": "Too many requests"}},
            response=httpx.Response(429, headers={"Retry-After": "120"}),
        )

        with patch.object(
            generate_site,
            "generate_ai_content",
            side_effect=rate_limit,
        ) as generate:
            result, requests_used, transient_failure, retry_after = (
                generate_site.generate_ai_content_with_retry(
                    {"title": "Sample topic"},
                    request_budget=3,
                )
            )

        self.assertIsNone(result)
        self.assertEqual(requests_used, 1)
        self.assertTrue(transient_failure)
        self.assertEqual(retry_after, 120)
        generate.assert_called_once_with(
            {"title": "Sample topic"},
            model=generate_site.PRIMARY_GEMINI_MODEL,
        )

    def test_google_retry_info_detail_is_used_for_rate_limit(self):
        rate_limit = errors.ClientError(
            429,
            {
                "error": {
                    "message": "Too many requests",
                    "details": [{
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "12.5s",
                    }],
                },
            },
        )

        self.assertEqual(generate_site.gemini_retry_after_seconds(rate_limit), 12.5)
        self.assertFalse(generate_site.is_daily_quota_error(rate_limit))

    def test_quota_exhaustion_stops_remaining_trends_and_finishes_site_updates(self):
        quota_error = generate_site.GeminiQuotaExceeded()
        trends = [
            {"title": "First topic", "slug": "first-topic", "news": []},
            {"title": "Second topic", "slug": "second-topic", "news": []},
        ]
        original_output_dir = generate_site.NICHE_CONFIG["output_dir"]
        original_queue_path = generate_site.NICHE_CONFIG["retry_queue_path"]

        with tempfile.TemporaryDirectory() as temp_dir:
            generate_site.NICHE_CONFIG["output_dir"] = temp_dir
            generate_site.NICHE_CONFIG["retry_queue_path"] = str(
                Path(temp_dir) / "pending_trends.json"
            )
            try:
                with (
                    patch.object(generate_site, "get_site_url", return_value=""),
                    patch.object(generate_site, "synchronize_canonical_urls"),
                    patch.object(generate_site, "fetch_google_trends", return_value=trends),
                    patch.object(generate_site, "has_source_evidence", return_value=True),
                    patch.object(
                        generate_site,
                        "generate_ai_content_with_retry",
                        side_effect=quota_error,
                    ) as generate,
                    patch.object(generate_site, "update_index_page") as update_index,
                    patch.object(generate_site, "install_cloudflare_beacon"),
                ):
                    generate_site.main()
                self.assertTrue((Path(temp_dir) / "robots.txt").is_file())
                queued = json.loads(
                    (Path(temp_dir) / "pending_trends.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(len(queued), 2)
                self.assertTrue(all(
                    datetime.fromisoformat(
                        entry["next_attempt_at"].replace("Z", "+00:00")
                    ) > datetime.now(timezone.utc)
                    for entry in queued
                ))
            finally:
                generate_site.NICHE_CONFIG["output_dir"] = original_output_dir
                generate_site.NICHE_CONFIG["retry_queue_path"] = original_queue_path

        generate.assert_called_once()
        update_index.assert_called_once_with([])

    def test_retries_server_timeout_once_within_budget(self):
        timeout = errors.ServerError(
            504,
            {"error": {"message": "Deadline expired"}},
        )
        trend = {"title": "Sample topic"}

        with (
            patch.object(
                generate_site,
                "generate_ai_content",
                side_effect=[timeout, {"status": "SUCCESS"}],
            ) as generate,
            patch.object(generate_site.random, "uniform", return_value=1.5) as jitter,
            patch.object(generate_site.time, "sleep") as sleep,
        ):
            result, requests_used, transient_failure, retry_after = generate_site.generate_ai_content_with_retry(
                trend,
                request_budget=5,
            )

        self.assertEqual(result, {"status": "SUCCESS"})
        self.assertEqual(requests_used, 2)
        self.assertFalse(transient_failure)
        self.assertIsNone(retry_after)
        self.assertEqual(generate.call_count, 2)
        jitter.assert_called_once_with(0, generate_site.GEMINI_RETRY_BASE_DELAY_SECONDS / 2)
        sleep.assert_called_once_with(3.5)

    def test_retries_transient_transport_errors(self):
        network_error = httpx.ConnectError("connection reset")

        with (
            patch.object(
                generate_site,
                "generate_ai_content",
                side_effect=[network_error, {"status": "SUCCESS"}],
            ) as generate,
            patch.object(generate_site.random, "uniform", return_value=0.5),
            patch.object(generate_site.time, "sleep") as sleep,
        ):
            result, requests_used, transient_failure, retry_after = generate_site.generate_ai_content_with_retry(
                {"title": "Sample topic"},
                request_budget=2,
            )

        self.assertEqual(result, {"status": "SUCCESS"})
        self.assertEqual(requests_used, 2)
        self.assertFalse(transient_failure)
        self.assertIsNone(retry_after)
        self.assertEqual(generate.call_count, 2)
        sleep.assert_called_once_with(2.5)

    def test_retries_request_timeout_status(self):
        timeout = errors.ClientError(
            408,
            {"error": {"message": "Request timed out"}},
        )

        with (
            patch.object(
                generate_site,
                "generate_ai_content",
                side_effect=[timeout, {"status": "SUCCESS"}],
            ) as generate,
            patch.object(generate_site.random, "uniform", return_value=0),
            patch.object(generate_site.time, "sleep") as sleep,
        ):
            result, requests_used, transient_failure, retry_after = generate_site.generate_ai_content_with_retry(
                {"title": "Sample topic"},
                request_budget=2,
            )

        self.assertEqual(result, {"status": "SUCCESS"})
        self.assertEqual(requests_used, 2)
        self.assertFalse(transient_failure)
        self.assertIsNone(retry_after)
        self.assertEqual(generate.call_count, 2)
        sleep.assert_called_once_with(2)

    def test_does_not_retry_non_transient_client_errors(self):
        invalid_request = errors.ClientError(
            400,
            {"error": {"message": "Invalid request"}},
        )

        with (
            patch.object(
                generate_site,
                "generate_ai_content",
                side_effect=invalid_request,
            ) as generate,
            patch.object(generate_site.time, "sleep") as sleep,
        ):
            with self.assertRaises(errors.ClientError):
                generate_site.generate_ai_content_with_retry(
                    {"title": "Sample topic"},
                    request_budget=2,
                )

        generate.assert_called_once()
        sleep.assert_not_called()

    def test_exhausted_retry_budget_skips_topic(self):
        timeout = errors.ServerError(
            504,
            {"error": {"message": "Deadline expired"}},
        )

        with (
            patch.object(generate_site, "generate_ai_content", side_effect=timeout) as generate,
            patch.object(generate_site.time, "sleep"),
        ):
            result, requests_used, transient_failure, retry_after = generate_site.generate_ai_content_with_retry(
                {"title": "Sample topic"},
                request_budget=2,
            )

        self.assertIsNone(result)
        self.assertEqual(requests_used, 2)
        self.assertTrue(transient_failure)
        self.assertIsNone(retry_after)
        self.assertEqual(generate.call_count, 2)

    def test_request_budget_limits_retry_attempts(self):
        timeout = errors.ServerError(
            504,
            {"error": {"message": "Deadline expired"}},
        )

        with patch.object(
            generate_site,
            "generate_ai_content",
            side_effect=timeout,
        ) as generate:
            result, requests_used, transient_failure, retry_after = generate_site.generate_ai_content_with_retry(
                {"title": "Sample topic"},
                request_budget=1,
            )

        self.assertIsNone(result)
        self.assertEqual(requests_used, 1)
        self.assertTrue(transient_failure)
        self.assertIsNone(retry_after)
        generate.assert_called_once()

    def test_falls_back_to_stable_model_after_primary_transient_failure(self):
        unavailable = errors.ServerError(
            503,
            {"error": {"message": "Service unavailable"}},
        )

        with (
            patch.object(
                generate_site,
                "generate_ai_content",
                side_effect=[
                    unavailable,
                    unavailable,
                    {"status": "SUCCESS"},
                ],
            ) as generate,
            patch.object(generate_site.time, "sleep"),
        ):
            result, requests_used, transient_failure, retry_after = (
                generate_site.generate_ai_content_with_retry(
                    {"title": "Sample topic"},
                    request_budget=3,
                )
            )

        self.assertEqual(result, {"status": "SUCCESS"})
        self.assertEqual(requests_used, 3)
        self.assertFalse(transient_failure)
        self.assertIsNone(retry_after)
        self.assertEqual(
            [call.kwargs["model"] for call in generate.call_args_list],
            [
                generate_site.PRIMARY_GEMINI_MODEL,
                generate_site.PRIMARY_GEMINI_MODEL,
                generate_site.FALLBACK_GEMINI_MODEL,
            ],
        )

    def test_transient_failures_are_reported_for_persistent_queue(self):
        unavailable = errors.ServerError(
            503,
            {"error": {"message": "Service unavailable"}},
        )

        with (
            patch.object(generate_site, "generate_ai_content", side_effect=unavailable),
            patch.object(generate_site.time, "sleep"),
        ):
            result, requests_used, transient_failure, retry_after = (
                generate_site.generate_ai_content_with_retry(
                    {"title": "Sample topic"},
                    request_budget=3,
                )
            )

        self.assertIsNone(result)
        self.assertEqual(requests_used, 3)
        self.assertTrue(transient_failure)
        self.assertIsNone(retry_after)

    def test_retry_queue_expires_old_entries_and_preserves_cooldown(self):
        now = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)
        fresh_trend = {
            "title": "Sample topic",
            "slug": "sample-topic",
            "traffic": "1000+",
            "news": [{"title": "Headline", "url": "https://example.com/story"}],
        }
        old_entry = generate_site.make_retry_entry(
            {"title": "Old topic", "slug": "old-topic", "traffic": "100+", "news": []},
            now=now - timedelta(days=8),
        )
        old_entry["next_attempt_at"] = now.isoformat().replace("+00:00", "Z")
        fresh_entry = generate_site.make_retry_entry(
            fresh_trend,
            now=now,
            count_attempt=True,
            error="Transient Gemini API or transport failure",
        )
        original_queue_path = generate_site.NICHE_CONFIG["retry_queue_path"]

        with tempfile.TemporaryDirectory() as temp_dir:
            generate_site.NICHE_CONFIG["retry_queue_path"] = str(
                Path(temp_dir) / "pending_trends.json"
            )
            try:
                generate_site.write_retry_queue([old_entry, fresh_entry])
                entries = generate_site.load_retry_queue(now=now)
            finally:
                generate_site.NICHE_CONFIG["retry_queue_path"] = original_queue_path

        self.assertEqual([entry["trend"]["slug"] for entry in entries], ["sample-topic"])
        self.assertEqual(entries[0]["attempts"], 1)
        self.assertEqual(entries[0]["last_error"], "Transient Gemini API or transport failure")
        self.assertGreater(
            datetime.fromisoformat(entries[0]["next_attempt_at"].replace("Z", "+00:00")),
            now,
        )

    def test_generation_summary_reports_deferred_topics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            summary_path = Path(temp_dir) / "summary.md"
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_path)}):
                generate_site.write_generation_summary(
                    generated_count=0,
                    queued_count=2,
                    transient_failures=1,
                    quota_hit=False,
                )

            summary = summary_path.read_text(encoding="utf-8")

        self.assertIn("Trends waiting in retry queue: 2", summary)
        self.assertIn("Some trends were deferred", summary)
        self.assertIn("1 transient generation failure", summary)

    def test_queued_trend_is_retried_after_it_disappears_from_rss(self):
        trend = {
            "title": "Sample topic",
            "slug": "sample-topic",
            "traffic": "1000+",
            "news": [{
                "title": "Headline",
                "url": "https://example.com/story",
                "source": "Example",
                "snippet": "",
            }],
        }
        original_output_dir = generate_site.NICHE_CONFIG["output_dir"]
        original_queue_path = generate_site.NICHE_CONFIG["retry_queue_path"]

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "docs"
            queue_path = Path(temp_dir) / "pending_trends.json"
            generate_site.NICHE_CONFIG["output_dir"] = str(output_dir)
            generate_site.NICHE_CONFIG["retry_queue_path"] = str(queue_path)
            try:
                with (
                    patch.object(generate_site, "get_site_url", return_value=""),
                    patch.object(generate_site, "synchronize_canonical_urls"),
                    patch.object(generate_site, "fetch_google_trends", return_value=[trend]),
                    patch.object(generate_site, "has_source_evidence", return_value=True),
                    patch.object(
                        generate_site,
                        "generate_ai_content_with_retry",
                        return_value=(None, 1, True, None),
                    ),
                    patch.object(generate_site, "update_index_page"),
                    patch.object(generate_site, "install_cloudflare_beacon"),
                ):
                    generate_site.main()

                queue = json.loads(queue_path.read_text(encoding="utf-8"))
                self.assertEqual(queue[0]["trend"]["slug"], "sample-topic")
                queue[0]["next_attempt_at"] = "2020-01-01T00:00:00Z"
                queue_path.write_text(json.dumps(queue), encoding="utf-8")

                with (
                    patch.object(generate_site, "get_site_url", return_value=""),
                    patch.object(generate_site, "synchronize_canonical_urls"),
                    patch.object(generate_site, "fetch_google_trends", return_value=[]),
                    patch.object(generate_site, "generate_ai_content_with_retry",
                                 return_value=(
                                     {
                                         "headline": "Sample reader-focused headline",
                                         "meta_description": "sample",
                                         "html_content": "<p>sample</p>",
                                     },
                                     1,
                                     False,
                                     None,
                                 )) as generate,
                    patch.object(generate_site, "build_page_html", return_value="<html></html>"),
                    patch.object(generate_site, "update_index_page"),
                    patch.object(generate_site, "install_cloudflare_beacon"),
                ):
                    generate_site.main()
                self.assertEqual(
                    json.loads(queue_path.read_text(encoding="utf-8")),
                    [],
                )
            finally:
                generate_site.NICHE_CONFIG["output_dir"] = original_output_dir
                generate_site.NICHE_CONFIG["retry_queue_path"] = original_queue_path

        generate.assert_called_once()
        self.assertEqual(generate.call_args.args[0]["slug"], "sample-topic")


class SiteOptimizationTests(unittest.TestCase):
    def test_github_pages_project_url_is_valid_for_canonical_links(self):
        original_site_url = generate_site.NICHE_CONFIG["site_url"]
        generate_site.NICHE_CONFIG["site_url"] = (
            "https://yopmale8-max.github.io/trend-watch-ph"
        )
        try:
            self.assertEqual(
                generate_site.get_site_url(),
                "https://yopmale8-max.github.io/trend-watch-ph",
            )
        finally:
            generate_site.NICHE_CONFIG["site_url"] = original_site_url

    def test_article_review_interval_uses_generated_date(self):
        now = datetime(2026, 11, 8, tzinfo=timezone.utc)

        self.assertTrue(generate_site.is_article_review_due("2026-11-01T00:00:00Z", now))
        self.assertFalse(generate_site.is_article_review_due("2026-11-01T00:00:01Z", now))
        self.assertFalse(generate_site.is_article_review_due("", now))

    def test_legacy_article_refreshes_only_after_review_interval(self):
        for generated_at, expected_calls in (
            ("2020-01-01T00:00:00Z", 1),
            (datetime.now(timezone.utc).isoformat(), 0),
        ):
            with self.subTest(generated_at=generated_at), tempfile.TemporaryDirectory() as temp_dir:
                output_dir = Path(temp_dir)
                (output_dir / "sample-topic.html").write_text(
                    "<html>legacy article</html>",
                    encoding="utf-8",
                )
                (output_dir / "trends_archive.json").write_text(
                    json.dumps([{
                        "slug": "sample-topic",
                        "generated_at": generated_at,
                        "source_fingerprint": "",
                    }]),
                    encoding="utf-8",
                )
                trend = {
                    "title": "Sample topic",
                    "slug": "sample-topic",
                    "news_items": [{"title": "Source", "url": "https://example.com"}],
                }
                original_output_dir = generate_site.NICHE_CONFIG["output_dir"]
                original_queue_path = generate_site.NICHE_CONFIG["retry_queue_path"]
                generate_site.NICHE_CONFIG["output_dir"] = temp_dir
                generate_site.NICHE_CONFIG["retry_queue_path"] = str(
                    Path(temp_dir) / "pending_trends.json"
                )
                try:
                    with (
                        patch.object(generate_site, "get_site_url", return_value=""),
                        patch.object(generate_site, "synchronize_canonical_urls"),
                        patch.object(generate_site, "fetch_google_trends", return_value=[trend]),
                        patch.object(generate_site, "has_source_evidence", return_value=True),
                        patch.object(
                            generate_site,
                            "generate_ai_content_with_retry",
                            return_value=(
                                {
                                    "headline": "Sample reader-focused headline",
                                    "meta_description": "sample",
                                    "html_content": "<p>sample</p>",
                                },
                                1,
                                False,
                                None,
                            ),
                        ) as generate,
                        patch.object(generate_site, "build_page_html", return_value="<html>new</html>"),
                        patch.object(generate_site, "update_index_page"),
                        patch.object(generate_site, "install_cloudflare_beacon"),
                    ):
                        generate_site.main()
                finally:
                    generate_site.NICHE_CONFIG["output_dir"] = original_output_dir
                    generate_site.NICHE_CONFIG["retry_queue_path"] = original_queue_path

                self.assertEqual(generate.call_count, expected_calls)

    def test_robots_txt_includes_sitemap_when_site_url_is_configured(self):
        self.assertEqual(
            generate_site.render_robots_txt("https://example.com"),
            "User-agent: *\nAllow: /\n\nSitemap: https://example.com/sitemap.xml\n",
        )
        self.assertEqual(
            generate_site.render_robots_txt(""),
            "User-agent: *\nAllow: /\n",
        )

    def test_sitemap_omits_unreliable_lastmod_and_noindex_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            for name in ("index.html", "about.html", "contact.html", "privacy-policy.html"):
                (output_dir / name).write_text("<html></html>", encoding="utf-8")
            (output_dir / "example-topic.html").write_text(
                "<html><meta name=\"robots\" content=\"index, follow\"></html>",
                encoding="utf-8",
            )
            (output_dir / "privacy.html").write_text(
                '<html><meta name="robots" content="noindex, follow"></html>',
                encoding="utf-8",
            )
            (output_dir / "trends_archive.json").write_text(
                json.dumps([{"slug": "example-topic"}, {"slug": "privacy"}]),
                encoding="utf-8",
            )

            original_output_dir = generate_site.NICHE_CONFIG["output_dir"]
            generate_site.NICHE_CONFIG["output_dir"] = temp_dir
            try:
                sitemap = generate_site.generate_sitemap("https://example.com")
            finally:
                generate_site.NICHE_CONFIG["output_dir"] = original_output_dir

        root = ET.fromstring(sitemap)
        namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        entries = root.findall("s:url", namespace)
        locations = {
            entry.findtext("s:loc", namespaces=namespace)
            for entry in entries
        }
        self.assertIn("https://example.com/", locations)
        self.assertIn("https://example.com/example-topic.html", locations)
        self.assertNotIn("https://example.com/privacy.html", locations)
        self.assertTrue(all(entry.find("s:lastmod", namespace) is None for entry in entries))


if __name__ == "__main__":
    unittest.main()
