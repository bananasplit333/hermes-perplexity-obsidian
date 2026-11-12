import unittest
from unittest.mock import patch

import perplexity_cdp


class PerplexityCdpTests(unittest.TestCase):
    def test_search_uses_default_without_opening_model_picker(self):
        class FakePerplexity:
            def __init__(self): self.actions = []
            def open_home(self): self.actions.append("home")
            def js(self, expr): return "true" if expr.startswith("!!") else "false"
            def click(self, _expr, label): self.actions.append(("click", label))
            def wait(self, _expr, _timeout, label): self.actions.append(("wait", label))
            def type_prompt(self, prompt): self.actions.append(("prompt", prompt))
            def submit(self): self.actions.append("submit"); return "https://www.perplexity.ai/search/abc"
            def wait_finished(self, _timeout, _url): self.actions.append("finished")
            def extract(self, mode, model, prompt): return {"mode": mode, "model": model, "prompt": prompt}
        px = FakePerplexity()
        result = perplexity_cdp.search(px, "nontrivial question", 300)
        self.assertEqual(result, {"mode": "Search", "model": "Default", "prompt": "nontrivial question"})
        self.assertEqual(px.actions, ["home", ("click", "Search mode button"), ("wait", "Search mode"),
                                      ("prompt", "nontrivial question"), "submit", "finished"])

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
