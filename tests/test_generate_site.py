import os
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

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


class GeminiRetryTests(unittest.TestCase):
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
            patch.object(generate_site.time, "sleep") as sleep,
        ):
            result, requests_used = generate_site.generate_ai_content_with_retry(
                trend,
                request_budget=5,
            )

        self.assertEqual(result, {"status": "SUCCESS"})
        self.assertEqual(requests_used, 2)
        self.assertEqual(generate.call_count, 2)
        sleep.assert_called_once_with(generate_site.GEMINI_RETRY_DELAY_SECONDS)

    def test_exhausted_retry_budget_skips_topic(self):
        timeout = errors.ServerError(
            504,
            {"error": {"message": "Deadline expired"}},
        )

        with (
            patch.object(generate_site, "generate_ai_content", side_effect=timeout) as generate,
            patch.object(generate_site.time, "sleep"),
        ):
            result, requests_used = generate_site.generate_ai_content_with_retry(
                {"title": "Sample topic"},
                request_budget=2,
            )

        self.assertIsNone(result)
        self.assertEqual(requests_used, 2)
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
            result, requests_used = generate_site.generate_ai_content_with_retry(
                {"title": "Sample topic"},
                request_budget=1,
            )

        self.assertIsNone(result)
        self.assertEqual(requests_used, 1)
        generate.assert_called_once()


class SiteOptimizationTests(unittest.TestCase):
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
                generate_site.NICHE_CONFIG["output_dir"] = temp_dir
                try:
                    with (
                        patch.object(generate_site, "get_site_url", return_value=""),
                        patch.object(generate_site, "synchronize_canonical_urls"),
                        patch.object(generate_site, "fetch_google_trends", return_value=[trend]),
                        patch.object(generate_site, "has_source_evidence", return_value=True),
                        patch.object(
                            generate_site,
                            "generate_ai_content_with_retry",
                            return_value=({"meta_description": "sample", "html_content": "<p>sample</p>"}, 1),
                        ) as generate,
                        patch.object(generate_site, "build_page_html", return_value="<html>new</html>"),
                        patch.object(generate_site, "update_index_page"),
                        patch.object(generate_site, "install_cloudflare_beacon"),
                    ):
                        generate_site.main()
                finally:
                    generate_site.NICHE_CONFIG["output_dir"] = original_output_dir

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
