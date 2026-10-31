import os
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
