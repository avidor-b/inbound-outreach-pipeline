"""Unit tests for scrape_site.py"""
import unittest

from outreach_pipeline.scrape_site import extract_text, slugify, parse_args, _url_priority


class TestUrlPriority(unittest.TestCase):
    def test_prioritizes_about(self):
        self.assertGreater(_url_priority("https://example.com/about"), 1)

    def test_prioritizes_products(self):
        self.assertGreater(_url_priority("https://example.com/products"), 1)

    def test_prioritizes_case_studies(self):
        self.assertGreater(_url_priority("https://example.com/case-studies"), 1)

    def test_prioritizes_blog(self):
        self.assertGreater(_url_priority("https://example.com/blog/news"), 1)

    def test_skips_legal(self):
        self.assertEqual(_url_priority("https://example.com/legal/privacy"), -1)

    def test_skips_careers(self):
        self.assertEqual(_url_priority("https://example.com/careers"), -1)

    def test_skips_login(self):
        self.assertEqual(_url_priority("https://example.com/login"), -1)

    def test_homepage_is_useful(self):
        self.assertGreater(_url_priority("https://example.com/"), 1)

    def test_unknown_page_gets_low_score(self):
        self.assertEqual(_url_priority("https://example.com/random-page"), 1)


class TestExtractText(unittest.TestCase):
    def test_strips_html_tags(self):
        html = "<p>Hello <b>world</b></p>"
        _, text = extract_text(html)
        self.assertIn("Hello", text)
        self.assertIn("world", text)
        self.assertNotIn("<p>", text)

    def test_removes_script_and_style(self):
        html = "<div><script>var x=1;</script><style>.a{}</style><p>Content</p></div>"
        _, text = extract_text(html)
        self.assertIn("Content", text)
        self.assertNotIn("var x", text)
        self.assertNotIn(".a{}", text)

    def test_removes_nav_footer_header(self):
        html = "<nav>Menu</nav><main>Body</main><footer>Footer</footer>"
        _, text = extract_text(html)
        self.assertIn("Body", text)
        self.assertNotIn("Menu", text)
        self.assertNotIn("Footer", text)

    def test_collapses_whitespace(self):
        html = "<p>A</p>\n\n\n\n<p>B</p>"
        _, text = extract_text(html)
        self.assertNotIn("\n\n\n", text)


class TestSlugify(unittest.TestCase):
    def test_basic_slug(self):
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_strips_special_chars(self):
        self.assertEqual(slugify("Price: $100!"), "price-100")

    def test_truncates_long_text(self):
        result = slugify("A" * 200, max_len=80)
        self.assertEqual(len(result), 80)

    def test_empty_returns_untitled(self):
        self.assertEqual(slugify("!!!"), "untitled")


class TestParseArgs(unittest.TestCase):
    def test_parses_url(self):
        args = parse_args(["--url", "https://example.com"])
        self.assertEqual(args.url, "https://example.com")
        self.assertIsInstance(args.max_pages, int)
        self.assertGreater(args.max_pages, 0)
        self.assertFalse(args.index)
        self.assertIsNone(args.collection)

    def test_parses_index_flag(self):
        args = parse_args(["--url", "https://example.com", "--index"])
        self.assertTrue(args.index)

    def test_parses_collection(self):
        args = parse_args(["--url", "https://example.com", "--index", "--collection", "sales_materials"])
        self.assertEqual(args.collection, "sales_materials")

    def test_parses_max_pages(self):
        args = parse_args(["--url", "https://example.com", "--max-pages", "10"])
        self.assertEqual(args.max_pages, 10)

    def test_parses_output_dir(self):
        args = parse_args(["--url", "https://example.com", "--output-dir", "my_dir"])
        self.assertEqual(args.output_dir, "my_dir")


if __name__ == "__main__":
    unittest.main()
