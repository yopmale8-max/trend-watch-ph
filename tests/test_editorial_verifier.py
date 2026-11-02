import json
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from scripts import editorial_verifier


class EditorialVerifierTests(unittest.TestCase):
    def test_article_parser_extracts_body_and_source_cards(self):
        article = editorial_verifier.ArticlePageParser()
        article.feed(
            '<h1>Sample headline</h1>'
            '<div class="content"><p>Verified body detail.</p>'
            '<div class="faq-list"><p>Additional claim.</p></div></div>'
            '<a class="source-card" href="https://news.example/story">'
            'Source headline<span>Publisher</span></a>'
            '<a href="https://unrelated.example/">Related page</a>'
        )

        self.assertEqual(
            editorial_verifier.normalize_text(" ".join(article.headline_parts)),
            "Sample headline",
        )
        self.assertEqual(
            editorial_verifier.normalize_text(" ".join(article.content_parts)),
            "Verified body detail. Additional claim.",
        )
        self.assertEqual(
            article.sources,
            [{
                "label": "Source headline Publisher",
                "url": "https://news.example/story",
            }],
        )

    def test_source_page_parser_prefers_article_and_omits_navigation(self):
        parser = editorial_verifier.SourcePageParser()
        parser.feed(
            "<html><head><title>News title</title>"
            '<meta name="description" content="Brief description"></head>'
            "<body><header>Site navigation</header><main>Page intro "
            "<article><h1>Article heading</h1><p>Article body.</p></article>"
            "</main></body></html>"
        )

        self.assertEqual("".join(parser.title_parts), "News title")
        self.assertEqual(parser.description, "Brief description")
        self.assertEqual(
            parser.extracted_text(),
            "Article heading Article body.",
        )

    def test_source_urls_must_be_https_and_have_no_credentials(self):
        self.assertTrue(editorial_verifier.is_safe_source_url(
            "https://news.example/story"
        ))
        self.assertFalse(editorial_verifier.is_safe_source_url(
            "http://news.example/story"
        ))
        self.assertFalse(editorial_verifier.is_safe_source_url(
            "https://user:password@news.example/story"
        ))

    def test_source_fetch_rejects_hosts_resolving_to_private_addresses(self):
        private_address = [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("127.0.0.1", 443),
            )
        ]
        with patch.object(
            editorial_verifier.socket,
            "getaddrinfo",
            return_value=private_address,
        ):
            result = editorial_verifier.fetch_source_page(
                "https://news.example/story"
            )

        self.assertEqual(result["status"], "unsafe_url")

    def test_redirect_handler_rejects_unsafe_destinations(self):
        handler = editorial_verifier.SafeRedirectHandler()
        with patch.object(
            editorial_verifier,
            "is_public_source_url",
            return_value=False,
        ):
            with self.assertRaises(editorial_verifier.UnsafeRedirectError):
                handler.redirect_request(
                    object(),
                    object(),
                    302,
                    "Found",
                    {},
                    "https://localhost/private",
                )

    def test_comparison_requires_exact_advisory_schema(self):
        response = type("Response", (), {
            "text": json.dumps({
                "summary": "The source supports the dated event detail.",
                "claims": [{
                    "claim": "The event took place on Tuesday.",
                    "status": "supports",
                    "evidence": "The page dates the event to Tuesday.",
                }],
            }),
        })()
        client = type("Client", (), {
            "models": type("Models", (), {
                "generate_content": lambda self, **kwargs: response,
            })(),
        })()
        article = {"headline": "Example", "text": "The event took place on Tuesday."}
        source = {"label": "Example source", "url": "https://news.example/story"}
        source_page = {
            "title": "News title",
            "description": "",
            "text": "The event took place on Tuesday.",
        }

        result = editorial_verifier.compare_article_to_source(
            client,
            article,
            source,
            source_page,
        )

        self.assertEqual(result["claims"][0]["status"], "supports")

    def test_malformed_model_result_is_not_reported_as_verified(self):
        response = type("Response", (), {"text": '{"summary": "missing claims"}'})()
        client = type("Client", (), {
            "models": type("Models", (), {
                "generate_content": lambda self, **kwargs: response,
            })(),
        })()
        article = {"headline": "Example", "text": "Claim."}
        source = {"label": "Example source", "url": "https://news.example/story"}
        source_page = {"title": "", "description": "", "text": "Evidence."}

        with self.assertRaises(ValueError):
            editorial_verifier.compare_article_to_source(
                client,
                article,
                source,
                source_page,
            )

    def test_review_batches_comparisons_and_keeps_each_source_separate(self):
        article = {
            "slug": "example",
            "path": "example.html",
            "headline": "Example headline",
            "text": "Claim from the article.",
            "sources": [
                {"label": "First", "url": "https://first.example/story"},
                {"label": "Second", "url": "https://second.example/story"},
            ],
        }
        source_pages = [
            {
                "status": "fetched",
                "title": f"Source {index}",
                "description": "",
                "text": f"Evidence from source {index}.",
                "final_url": source["url"],
            }
            for index, source in enumerate(article["sources"], start=1)
        ]
        response = type("Response", (), {
            "text": json.dumps({
                "sources": [
                    {
                        "url": source["url"],
                        "summary": f"Compared against source {index}.",
                        "claims": [{
                            "claim": "Claim from the article.",
                            "status": "supports",
                            "evidence": f"Source {index} evidence.",
                        }],
                    }
                    for index, source in enumerate(article["sources"], start=1)
                ],
            }),
        })()
        client = type("Client", (), {
            "models": type("Models", (), {
                "generate_content": lambda self, **kwargs: response,
            })(),
        })()

        with (
            patch.object(
                editorial_verifier,
                "fetch_source_page",
                side_effect=source_pages,
            ),
            patch.object(
                client.models,
                "generate_content",
                wraps=client.models.generate_content,
            ) as compare,
        ):
            result = editorial_verifier.review_article(client, article)

        self.assertEqual(compare.call_count, 1)
        self.assertEqual(len(result["sources"]), 2)
        self.assertEqual(
            [source["claims"][0]["evidence"] for source in result["sources"]],
            ["Source 1 evidence.", "Source 2 evidence."],
        )

    def test_unreachable_sources_are_listed_without_model_comparison(self):
        article = {
            "slug": "example",
            "path": "example.html",
            "headline": "Example headline",
            "text": "An article body.",
            "sources": [{
                "label": "Example source",
                "url": "https://news.example/story",
            }],
        }
        client = object()

        with patch.object(
            editorial_verifier,
            "fetch_source_page",
            return_value={
                "status": "http_error",
                "error": "Source returned HTTP 403.",
            },
        ):
            result = editorial_verifier.review_article(client, article)

        self.assertEqual(result["review_status"], "advisory_review")
        self.assertEqual(result["sources"][0]["fetch_status"], "http_error")
        self.assertNotIn("comparison_status", result["sources"][0])


if __name__ == "__main__":
    unittest.main()
