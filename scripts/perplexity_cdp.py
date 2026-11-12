#!/usr/bin/env python3
"""Run the default Search flow through Perplexity's visible browser.

  CLI="uv run --with websocket-client python $HERMES_HOME/skills/perplexity-obsidian/scripts/perplexity_cdp.py"
  $CLI check
  $CLI search --prompt-file PROMPT.txt --out RESULT.json [--timeout 300]
  $CLI search --prompt-file PROMPT.txt --out RESULT.json --dry-run
  $CLI extract --url https://www.perplexity.ai/session/<id> --out RESULT.json

Search mode is the only supported mode. The workflow does not select models or use Deep Research.
RESULT.json holds thread_url, title, mode, model_label, duration, sources_count,
answer_markdown, cited_links, and source_panel_links.
Exit codes: 0 ok, 2 page/selector failure, 3 sign-in required, 5 credit/upgrade prompt,
6 waiting on user input, 7 timed out while still running (resume with `extract --url`).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from urllib.parse import urlsplit
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp_lite import Page, list_targets  # noqa: E402

ORIGIN = "https://www.perplexity.ai"
EDITOR = '[contenteditable="true"][role="textbox"]'
BLOCKERS = re.compile(
    r"now using credits|buy (more )?credits|add credits|insufficient credits|out of credits|"
    r"upgrade to max|upgrade your plan|you('ve| have) (used|reached) (all|your)|limit reached|auto-refill",
    re.I)


class FlowError(RuntimeError):
    code = 2


def same_origin(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return parsed.scheme == "https" and parsed.hostname == "www.perplexity.ai" and parsed.port in (None, 443)
    except ValueError:
        return False


def fail(code: int, msg: str):
    err = FlowError(msg)
    err.code = code
    raise err


class Perplexity:
    def __init__(self, port: int = 9222):
        targets = [t for t in list_targets(port) if t.get("type") == "page" and same_origin(t.get("url") or "")]
        if not targets:
            fail(2, f"no {ORIGIN} tab on CDP port {port}; launch it with session_chrome.py")
        self.p = Page(ws_url=targets[0]["webSocketDebuggerUrl"])
        self.p.call("Page.bringToFront")

    def js(self, expr: str, await_promise: bool = False):
        return self.p.js(expr, await_promise=await_promise)

    def wait(self, expr: str, timeout: float, what: str, code: int = 2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.js(expr)
            if value:
                return value
            time.sleep(0.3)
        fail(code, f"timed out waiting for {what}")

    def click(self, find_expr: str, what: str, settle: float = 5.0):
        """Click the element's center once its position has stopped moving (menus animate open)."""
        locate = f"""(() => {{ const e = ({find_expr}); if (!e) return null;
            e.scrollIntoView({{block: "nearest"}}); const r = e.getBoundingClientRect();
            return {{x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)}}; }})()"""
        point, deadline = None, time.monotonic() + settle
        while time.monotonic() < deadline:
            current = self.js(locate)
            if current and current == point:
                break
            point = current
            time.sleep(0.25)
        if not point:
            fail(2, f"could not find {what}")
        self.p.call("Input.dispatchMouseEvent", type="mouseMoved", **point)
        for kind in ("mousePressed", "mouseReleased"):
            self.p.call("Input.dispatchMouseEvent", type=kind, button="left", clickCount=1, **point)

    def key(self, key: str, code: str, vk: int):
        for kind in ("keyDown", "keyUp"):
            self.p.call("Input.dispatchKeyEvent", type=kind, key=key, code=code, windowsVirtualKeyCode=vk)

    def page_text(self) -> str:
        return self.js('(document.querySelector("main") || document.body).innerText + "\\n" + '
                       'Array.from(document.querySelectorAll("[role=dialog],[role=alertdialog]")).map(d => d.innerText).join("\\n")') or ""

    def assert_no_blockers(self):
        dialogs = self.js('Array.from(document.querySelectorAll("[role=dialog],[role=alertdialog],[role=alert],[data-sonner-toast], [data-radix-toast-viewport] [data-state]")) .map(e => e.innerText || "").join("\n")') or ""
        match = BLOCKERS.search(dialogs)
        if match:
            fail(5, f"credit/upgrade prompt on page ({match.group(0)!r}); stopping without spending credits")

    def open_home(self):
        self.p.call("Page.navigate", url=ORIGIN + "/")
        self.wait(f'document.readyState === "complete" && !!document.querySelector({json.dumps(EDITOR)})',
                  30, "home composer")

    def type_prompt(self, prompt: str, must_contain: str | None = None):
        self.js(f"document.querySelector({json.dumps(EDITOR)}).focus()")
        self.p.call("Input.insertText", text=prompt)
        text = self.js(f"document.querySelector({json.dumps(EDITOR)}).innerText") or ""
        normalize = lambda value: " ".join((value or "").split())
        if normalize(prompt) not in normalize(text) or (must_contain and must_contain not in text):
            fail(2, "composer text did not match the prompt after typing")

    def submit(self) -> str:
        self.assert_no_blockers()
        self.click('document.querySelector("button[aria-label=Submit]")', "Submit button")
        url = self.wait('/\\/(session|search)\\//.test(location.pathname) && location.href', 30, "thread URL")
        return url

    def state(self) -> str:
        return self.js("""(() => { const m = document.querySelector("main") || document.body;
            const btns = Array.from(m.querySelectorAll("button"));
            if (btns.some(b => /^Stop response/.test(b.getAttribute("aria-label") || ""))) return "running";
            if (btns.some(b => /^Finished\\b/.test(b.innerText.trim()))) return "finished";
            if (btns.some(b => b.getAttribute("aria-label") === "Copy") && btns.some(b => b.getAttribute("aria-label") === "Submit")) return "finished";
            return "idle"; })()""")

    def wait_finished(self, timeout: float, url: str):
        deadline, idle_since = time.monotonic() + timeout, None
        while time.monotonic() < deadline:
            try:
                self.assert_no_blockers()
            except FlowError as exc:
                raise FlowError(f"{exc}; Thread: {url}") from exc
            state = self.state()
            if state == "finished":
                return
            if state == "idle":
                idle_since = idle_since or time.monotonic()
                if time.monotonic() - idle_since > 45:
                    buttons = self.js('Array.from(document.querySelectorAll("main button")).map(b => (b.getAttribute("aria-label") || b.innerText).trim()).filter(Boolean).slice(-15)')
                    fail(6, f"thread stopped without finishing; it may be asking for input. Visible buttons: {buttons}. Thread: {url}")
            else:
                idle_since = None
            time.sleep(3)
        fail(7, f"still running after {timeout:.0f}s; resume later with: extract --url {url}")

    def extract(self, mode: str, model_label: str, prompt: str | None) -> dict:
        self.p.call("Page.bringToFront")
        supported = self.js("typeof document.addEventListener === 'function' && typeof window.getSelection === 'function'")
        if not supported:
            fail(2, "safe answer capture unsupported: page copy interception unavailable")
        self.js("window.__pplxCapturedCopy = null; document.addEventListener('copy', function(e) { window.__pplxCapturedCopy = window.getSelection()?.toString() || ''; e.preventDefault(); }, {capture:true, once:true})")
        self.click('Array.from(document.querySelectorAll("main button[aria-label=Copy]")).pop()', "answer Copy button")
        time.sleep(1.0)
        markdown = self.js("window.__pplxCapturedCopy") or ""
        if len(markdown.strip()) < 200:
            fail(2, "Copy returned no answer text")
        meta = self.js("""(() => { const m = document.querySelector("main") || document.body;
            const btns = Array.from(m.querySelectorAll("button"));
            const fin = btns.map(b => b.innerText.trim()).find(t => /^Finished\\b/.test(t)) || "";
            const src = btns.map(b => b.innerText.trim()).find(t => /^\\d+ sources?$/.test(t)) || "";
            const title = (document.querySelector('button[aria-label^="Rename session: "]') || {}).getAttribute?.("aria-label") || document.title;
            return {duration: fin.replace(/^Finished\\s*/, ""), sources: src, title: title.replace(/^Rename session: /, "")}; })()""")
        panel_links = []
        if meta["sources"]:
            self.click('Array.from(document.querySelectorAll("main button")).find(b => /^\\d+ sources?$/.test(b.innerText.trim()))', "sources button")
            time.sleep(1.0)
            seen = {}
            for _ in range(40):
                batch = self.js("""Array.from(document.querySelectorAll("[role=dialog] a[href^=http]")).map(a => ({url: a.href, text: a.innerText.trim().split("\\n").slice(0, 2).join(" | ")}))""") or []
                before = len(seen)
                for item in batch:
                    seen.setdefault(item["url"], item)
                moved = self.js("""(() => { const d = document.querySelector("[role=dialog]"); if (!d) return false;
                    const s = Array.from(d.querySelectorAll("*")).find(e => e.scrollHeight > e.clientHeight + 5) || d;
                    const top = s.scrollTop; s.scrollTop = top + s.clientHeight; return s.scrollTop > top; })()""")
                time.sleep(0.3)
                if not moved and len(seen) == before:
                    break
            panel_links = list(seen.values())
            self.key("Escape", "Escape", 27)
        cited = list(dict.fromkeys(re.findall(r"\]\((https?://[^)\s]+)\)", markdown)))
        return {
            "thread_url": self.js("location.href"),
            "title": meta["title"],
            "mode": mode,
            "model_label": model_label,
            "duration": meta["duration"],
            "sources_count": int(meta["sources"].split()[0]) if meta["sources"] else None,
            "submitted_prompt": prompt,
            "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "answer_markdown": markdown,
            "cited_links": cited,
            "source_panel_links": panel_links,
        }


def search(px: Perplexity, prompt: str, timeout: float, dry_run: bool = False) -> dict:
    px.open_home()
    search_button = 'document.querySelector("button[aria-label=Search]")'
    if px.js(f"!!{search_button}") and px.js(f"{search_button}?.getAttribute(\"aria-pressed\")") != "true":
        px.click(search_button, "Search mode button")
        px.wait(f"{search_button}?.getAttribute(\"aria-pressed\") === \"true\"", 10, "Search mode")
    px.type_prompt(prompt)
    if dry_run:
        px.assert_no_blockers()
        px.js(f"document.querySelector({json.dumps(EDITOR)}).focus()")
        for kind in ("keyDown", "keyUp"):
            px.p.call("Input.dispatchKeyEvent", type=kind, key="a", code="KeyA", windowsVirtualKeyCode=65, modifiers=2)
        px.key("Backspace", "Backspace", 8)
        left = (px.js(f"document.querySelector({json.dumps(EDITOR)}).innerText") or "").strip()
        return {"dry_run": True, "composer_cleared": left == "",
                "steps_verified": ["home composer", "Search mode", "prompt typed", "no credit prompt"]}
    url = px.submit()
    px.wait_finished(timeout, url)
    return px.extract("Search", "Default", prompt)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=9333)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    p = sub.add_parser("search")
    p.add_argument("--prompt-file", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--timeout", type=float, default=300)
    p.add_argument("--dry-run", action="store_true", help="verify pre-submit steps, then clear the composer")
    e = sub.add_parser("extract")
    e.add_argument("--url", required=True)
    e.add_argument("--out", type=Path, required=True)
    e.add_argument("--timeout", type=float, default=600)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "extract" and (not same_origin(args.url) or urlsplit(args.url).path.split("/", 2)[1] not in {"session", "search"}):
            fail(2, "extract only accepts HTTPS Perplexity session or search URLs")
        px = Perplexity(args.port)
        if args.cmd == "check":
            px.open_home()
            print(json.dumps({"ok": True, "url": px.js("location.href"), "account_tier_required": False}))
            return 0
        if args.cmd == "extract":
            if not same_origin(args.url) or urlsplit(args.url).path.split("/", 2)[1] not in {"session", "search"}:
                fail(2, "extract only accepts HTTPS Perplexity session or search URLs")
            px.p.call("Page.navigate", url=args.url)
            px.wait('document.readyState === "complete"', 30, "thread page")
            time.sleep(2)
            px.wait_finished(args.timeout, args.url)
            result = px.extract("Search (extracted)", "unknown", None)
        else:
            prompt = args.prompt_file.read_text(encoding="utf-8").strip()
            if not prompt:
                fail(2, "prompt file is empty")
            result = search(px, prompt, args.timeout, args.dry_run)
            if result.get("dry_run"):
                print(json.dumps(result, indent=2))
                return 0 if result["composer_cleared"] else 2
        if args.out.exists():
            fail(2, f"refusing to overwrite existing output file: {args.out}")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({k: result[k] for k in ("thread_url", "title", "mode", "model_label", "duration", "sources_count")}
                         | {"cited_links": len(result["cited_links"]), "answer_chars": len(result["answer_markdown"]), "out": str(args.out)}, indent=2))
        return 0
    except FlowError as exc:
        print(f"error[{exc.code}]: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
