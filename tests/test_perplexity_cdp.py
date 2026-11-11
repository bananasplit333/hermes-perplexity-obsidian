import unittest
from unittest.mock import patch

import perplexity_cdp


class PerplexityCdpTests(unittest.TestCase):
    def test_strict_https_origin(self):
        self.assertTrue(perplexity_cdp.same_origin("https://www.perplexity.ai/session/abc"))
        for url in ("http://www.perplexity.ai/session/a", "https://www.perplexity.ai.evil.test/", "https://evil.test/www.perplexity.ai", "https://www.perplexity.ai:444/"):
            with self.subTest(url=url):
                self.assertFalse(perplexity_cdp.same_origin(url))

    def test_credit_checks_only_dialog_content(self):
        px = object.__new__(perplexity_cdp.Perplexity)
        px.js = lambda _expr: ""
        with patch.object(px, "page_text", return_value="Buy more credits is mentioned in my research question"):
            px.assert_no_blockers()
        px.js = lambda _expr: "Buy more credits"
        with self.assertRaises(perplexity_cdp.FlowError) as raised:
            px.assert_no_blockers()
        self.assertEqual(raised.exception.code, 5)

    def test_prompt_whitespace_normalized(self):
        px = object.__new__(perplexity_cdp.Perplexity)
        px.js = lambda _expr: "  first\n\t second  "
        class FakePage:
            def call(self, *_args, **_kwargs): pass
        px.p = FakePage()
        px.type_prompt("first second")

    def test_extract_url_rejects_lookalike_origin(self):
        with patch.object(perplexity_cdp, "Perplexity", side_effect=AssertionError("must reject before CDP")):
            self.assertEqual(perplexity_cdp.main(["extract", "--url", "https://www.perplexity.ai.evil.test/session/a", "--out", "/tmp/nope"]), 2)


if __name__ == "__main__":
    unittest.main()
