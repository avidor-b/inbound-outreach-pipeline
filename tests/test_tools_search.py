"""Tests for tools/search.py — web search."""
import unittest


class TestWebSearch(unittest.TestCase):
    def test_returns_results_structure(self):
        from outreach_pipeline.tools.search import web_search
        result = web_search("Example Company website", max_results=3)
        self.assertIn("success", result)
        self.assertIn("results", result)
        self.assertIn("query", result)
        self.assertEqual(result["query"], "Example Company website")

    def test_results_have_required_fields(self):
        from outreach_pipeline.tools.search import web_search
        result = web_search("Example Company", max_results=2)
        if result["success"] and result["results"]:
            r = result["results"][0]
            self.assertIn("title", r)
            self.assertIn("url", r)
            self.assertIn("snippet", r)

    def test_max_results_respected(self):
        from outreach_pipeline.tools.search import web_search
        result = web_search("Python programming", max_results=2)
        if result["success"]:
            self.assertLessEqual(len(result["results"]), 2)

    def test_empty_query(self):
        from outreach_pipeline.tools.search import web_search
        result = web_search("")
        # Should not crash — returns empty or error
        self.assertIn("success", result)

    def test_company_domain_lookup(self):
        """Company lookup keeps a generic query and returns URL strings."""
        from outreach_pipeline.tools.search import web_search
        result = web_search("Example Company official website", max_results=3)
        if result["success"] and result["results"]:
            urls = [r["url"] for r in result["results"]]
            self.assertTrue(all(isinstance(url, str) for url in urls))


if __name__ == "__main__":
    unittest.main()
