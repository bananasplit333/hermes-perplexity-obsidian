import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from subprocess import CompletedProcess

import perplexity_run


class FakeCall:
    def __init__(self, *, check_code=0, research_code=0, close_code=0, active_session=False):
        self.calls = []
        self.check_code = check_code
        self.research_code = research_code
        self.close_code = close_code
        self.active_session = active_session
        self.status_count = 0

    def __call__(self, script, *args, timeout=None):
        self.calls.append((script, args, timeout))
        if script == "session_chrome.py" and args[0] == "launch":
            return CompletedProcess([], 0, json.dumps({"state": "running"}), "")
        if script == "perplexity_cdp.py" and args[-1] == "check":
            return CompletedProcess([], self.check_code, "", "login required" if self.check_code else "")
        if script == "perplexity_cdp.py":
            if self.research_code == 0 and "--dry-run" not in args:
                out = Path(args[args.index("--out") + 1])
                out.write_text(json.dumps({"thread_url": "https://www.perplexity.ai/session/test",
                                           "title": "Example", "sources_count": 3}), encoding="utf-8")
            return CompletedProcess([], self.research_code, '{"composer_cleared": true}\n', "research failed" if self.research_code else "")
        if script == "session_chrome.py" and args[0] == "close":
            return CompletedProcess([], self.close_code, "", "close failed" if self.close_code else "")
        if script == "session_chrome.py" and args[0] == "status":
            self.status_count += 1
            browsers = [{"owned": True, "state": "running", "pid": 77, "port": 9444}] if self.active_session and self.status_count == 1 else []
            return CompletedProcess([], 0, json.dumps({"browsers": browsers}), "")
        raise AssertionError((script, args))


class PerplexityRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"HERMES_HOME": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.home_scripts = self.home / "scripts"
        self.home_scripts.mkdir()
        self.scripts_patch = patch.object(perplexity_run, "HOME", self.home)
        self.scripts_patch.start()
        self.addCleanup(self.scripts_patch.stop)
        self.calls_patch = patch.object(perplexity_run, "SCRIPTS", self.home_scripts)
        self.calls_patch.start()
        self.addCleanup(self.calls_patch.stop)
        config_patch = patch.object(perplexity_run, "load_vault_config", return_value={"vault_path": str(self.home), "notes_folder": "Notes", "layout": "flat"})
        config_patch.start()
        self.addCleanup(config_patch.stop)

    def test_success_runs_steps_in_order_and_verifies_cleanup(self):
        fake = FakeCall()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = perplexity_run.main(["--prompt", "Research this", "--session", "test-run"], call=fake)
        self.assertEqual(code, 0)
        self.assertEqual([c[1][0] if c[0] == "session_chrome.py" else c[1][-1] if c[1][-1] == "check" else c[1][2]
                          for c in fake.calls], ["status", "launch", "check", "search", "close", "status"])
        report = json.loads(output.getvalue())
        self.assertEqual(report["sources_count"], 3)
        self.assertTrue(Path(report["out"]).is_file())
        research = next(c for c in fake.calls if c[0] == "perplexity_cdp.py" and c[1][-1] != "check")
        self.assertEqual(research[1][2], "search")
        self.assertNotIn("--model", research[1])

    def test_login_keeps_browser_for_user_handoff(self):
        fake = FakeCall(check_code=3)
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            code = perplexity_run.main(["--prompt", "Research this", "--session", "login-run"], call=fake)
        self.assertEqual(code, 3)
        self.assertFalse(any(c[0] == "session_chrome.py" and c[1][0] == "close" for c in fake.calls))
        self.assertIn("rerun with --session login-run --resume", errors.getvalue())

    def test_dry_run_never_creates_result_and_closes_browser(self):
        fake = FakeCall()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = perplexity_run.main(["--prompt", "Check only", "--session", "dry-run", "--dry-run"], call=fake)
        self.assertEqual(code, 0)
        research = next(c for c in fake.calls if c[0] == "perplexity_cdp.py" and c[1][2] == "search")
        self.assertIn("--dry-run", research[1])
        self.assertFalse((self.home / "cache/scratch/perplexity/dry-run/result.json").exists())
        self.assertEqual(fake.calls[-2][1][0], "close")
        self.assertEqual(fake.calls[-1][1][0], "status")

    def test_research_failure_still_closes_browser(self):
        fake = FakeCall(research_code=5)
        with contextlib.redirect_stderr(io.StringIO()):
            code = perplexity_run.main(["--prompt", "Research this", "--session", "failed-run"], call=fake)
        self.assertEqual(code, 5)
        self.assertEqual(fake.calls[-2][1][0], "close")
        self.assertEqual(fake.calls[-1][1][0], "status")

    def test_existing_session_requires_explicit_resume(self):
        fake = FakeCall(active_session=True)
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            code = perplexity_run.main(["--prompt", "Research this", "--session", "owned-run"], call=fake)
        self.assertEqual(code, 13)
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("use --resume", errors.getvalue())

    def test_resume_uses_registered_browser_without_relaunch(self):
        fake = FakeCall(active_session=True)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = perplexity_run.main(["--prompt", "Research this", "--session", "owned-run", "--resume"], call=fake)
        self.assertEqual(code, 0)
        cdp_calls = [c for c in fake.calls if c[0] == "perplexity_cdp.py"]
        self.assertTrue(all(c[1][c[1].index("--port") + 1] == "9444" for c in cdp_calls))
        self.assertFalse(any(c[0] == "session_chrome.py" and c[1][0] == "launch" for c in fake.calls))
        self.assertTrue(any(c[0] == "session_chrome.py" and c[1][0] == "close" for c in fake.calls))

    def test_cleanup_failure_is_not_reported_as_success(self):
        fake = FakeCall(close_code=1)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                perplexity_run.main(["--prompt", "Research this", "--session", "cleanup-run"], call=fake)
        self.assertEqual(raised.exception.code, 12)

    def test_mode_and_model_options_are_rejected(self):
        for options in (("--mode", "deep-research"), ("--model", "some-model")):
            with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()):
                fake = FakeCall()
                with self.assertRaises(SystemExit) as raised:
                    perplexity_run.main(["--prompt", "Research this", *options], call=fake)
                self.assertEqual(raised.exception.code, 2)
                self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
