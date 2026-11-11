#!/usr/bin/env python3
"""Drive Perplexity in the Chrome (CDP port 9333) step by step, with a check after each step.

  CLI="uv run --with websocket-client python $HERMES_HOME/skills/perplexity-obsidian/scripts/perplexity_cdp.py"
  $CLI check
  $CLI deep-research --prompt-file PROMPT.txt --out RESULT.json [--timeout 1200]
  $CLI deep-research --prompt-file PROMPT.txt --out RESULT.json --dry-run   # every check, no submit
  $CLI search --model "Claude Sonnet 5.5" --prompt-file PROMPT.txt --out RESULT.json
  $CLI extract --url https://www.perplexity.ai/session/<id> --out RESULT.json

Deep Research flow (mapped 2026-11-11): home -> "+" (Add files, plugins, and more) ->
listbox option "Deep Research" -> chip "Remove Deep Research" in the composer, Computer
mode pressed, model "Default" -> type -> Submit -> URL /session/<id> -> "Stop response"
while Reading/Searching/Working -> "Finished <time>" + "<N> sources" + Copy -> Copy
yields Markdown with inline source links.

RESULT.json holds thread_url, title, mode, model_label, duration, sources_count,
answer_markdown, cited_links, and source_panel_links.
Exit codes: 0 ok, 2 page/selector failure, 3 not signed in to Pro, 4 model is not
Default (would spend credits), 5 credit/upgrade prompt seen, 6 waiting on user input,
7 timed out while still running (resume with `extract --url`).
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
PLUS = 'button[aria-label="Add files, plugins, and more"]'
EDITOR = '[contenteditable="true"][role="textbox"]'
DR_CHIP = 'button[aria-label="Remove Deep Research"]'
# Max-only per Perplexity's help page (2026-11-11) even when the menu shows another badge.
MAX_ONLY = {"GPT-6 Astra", "Gemini 4 Carbon", "Claude Opus 5.5", "Claude Fable 5.2"}
# The model picker is the last menu button in the composer. Its aria-label is "Model" when
# unset, "Default" in Computer mode, or the full model name once one is chosen (sticky).
MODEL_BUTTON = """(() => { let c = document.querySelector('[contenteditable="true"][role="textbox"]');
    while (c && !c.querySelector('button[aria-label="Submit"], button[aria-label="Use voice mode"]')) c = c.parentElement;
    const b = c ? Array.from(c.querySelectorAll("button[aria-haspopup=menu]")) : [];
    return b.length ? b[b.length - 1] : null; })()"""
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
        self.wait(f'document.readyState === "complete" && !!document.querySelector({json.dumps(EDITOR)}) '
                  f'&& !!document.querySelector({json.dumps(PLUS)})', 30, "home composer")
        self.wait('Array.from(document.querySelectorAll("nav button, aside button, button")).some(b => /\\nPro$/.test(b.innerText.trim()))',
                  20, "account button with Pro badge (sign in to the Pro account in the visible window)", code=3)

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


def deep_research(px: Perplexity, prompt: str, timeout: float, dry_run: bool = False) -> dict:
    px.open_home()
    option = 'Array.from(document.querySelectorAll("[role=listbox] [role=option]")).find(o => o.innerText.trim().split("\\n")[0] === "Deep Research")'
    chip = f"!!document.querySelector({json.dumps(EDITOR + ' ' + DR_CHIP)})"
    for _ in range(3):
        if px.js(chip):
            break
        if not px.js(f"!!({option})"):
            px.click(f"document.querySelector({json.dumps(PLUS)})", "+ button")
            px.wait(f"!!({option})", 10, "+ menu with Deep Research")
        px.click(option, "Deep Research option")
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and not px.js(chip):
            time.sleep(0.25)
    if not px.js(chip):
        state = px.js('({listbox: !!document.querySelector("[role=listbox] [role=option]"), editor: document.querySelector('
                      + json.dumps(EDITOR) + ')?.innerText})')
        fail(2, f"Deep Research chip never appeared after 3 attempts; page state {state}")
    px.wait('document.querySelector("button[aria-label=Computer]")?.getAttribute("aria-pressed") === "true"', 10, "Computer mode")
    model = px.js(f"({MODEL_BUTTON})?.getAttribute('aria-label') || ''")
    if (model or "").strip() != "Default":
        fail(4, f"Deep Research model is {model!r}, not Default; a non-default model spends credits")
    px.type_prompt(prompt, must_contain="Deep Research")
    if dry_run:
        px.assert_no_blockers()
        px.js(f"document.querySelector({json.dumps(EDITOR)}).focus()")
        for kind in ("keyDown", "keyUp"):
            px.p.call("Input.dispatchKeyEvent", type=kind, key="a", code="KeyA", windowsVirtualKeyCode=65, modifiers=2)
        px.key("Backspace", "Backspace", 8)
        px.key("Backspace", "Backspace", 8)
        left = (px.js(f"document.querySelector({json.dumps(EDITOR)}).innerText") or "").strip()
        return {"dry_run": True, "composer_cleared": left == "",
                "steps_verified": ["home + Pro badge", "+ menu", "Deep Research option", "chip in composer",
                                   "Computer mode", "model Default", "prompt typed", "no credit prompt"]}
    url = px.submit()
    px.wait_finished(timeout, url)
    return px.extract("Deep Research (Computer)", "Default", prompt)


def search(px: Perplexity, prompt: str, model: str, timeout: float) -> dict:
    px.open_home()
    if px.js('document.querySelector("button[aria-label=Search]")?.getAttribute("aria-pressed")') != "true":
        px.click('document.querySelector("button[aria-label=Search]")', "Search mode button")
        px.wait('document.querySelector("button[aria-label=Search]")?.getAttribute("aria-pressed") === "true"', 10, "Search mode")
    px.click(MODEL_BUTTON, "Model button")
    items = px.wait('(() => { const i = Array.from(document.querySelectorAll("[role=menuitem],[role=menuitemradio]")).map(e => e.innerText.trim().split("\\n")); return i.length ? i : null; })()',
                    10, "model menu")
    match = next((lines for lines in items if lines[0] == model), None)
    if not match:
        fail(2, f"model {model!r} not in menu: {[i[0] for i in items]}")
    if "Max" in match[1:] or model in MAX_ONLY:
        px.key("Escape", "Escape", 27)
        fail(4, f"{model} is Max-only on this account; pick a Pro-included model")
    px.click(f'Array.from(document.querySelectorAll("[role=menuitem],[role=menuitemradio]")).find(e => e.innerText.trim().split("\\n")[0] === {json.dumps(model)})',
             f"{model} menu item")
    px.wait(f"({MODEL_BUTTON})?.getAttribute('aria-label') === {json.dumps(model)}", 10, f"{model} selected")
    px.type_prompt(prompt)
    url = px.submit()
    px.wait_finished(timeout, url)
    return px.extract("Search", model, prompt)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=9333)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    for name in ("deep-research", "search"):
        p = sub.add_parser(name)
        p.add_argument("--prompt-file", type=Path, required=True)
        p.add_argument("--out", type=Path, required=True)
        p.add_argument("--timeout", type=float, default=1200 if name == "deep-research" else 300)
        if name == "search":
            p.add_argument("--model", required=True)
        else:
            p.add_argument("--dry-run", action="store_true", help="verify every pre-submit step, then clear the composer")
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
            print(json.dumps({"ok": True, "url": px.js("location.href"), "pro": True}))
            return 0
        if args.cmd == "extract":
            if not same_origin(args.url) or urlsplit(args.url).path.split("/", 2)[1] not in {"session", "search"}:
                fail(2, "extract only accepts HTTPS Perplexity session or search URLs")
            px.p.call("Page.navigate", url=args.url)
            px.wait('document.readyState === "complete"', 30, "thread page")
            time.sleep(2)
            px.wait_finished(args.timeout, args.url)
            label = px.js(f"({MODEL_BUTTON})?.getAttribute('aria-label') || ''")
            result = px.extract("unknown (extracted)", (label or "").strip() or "unknown", None)
        else:
            prompt = args.prompt_file.read_text(encoding="utf-8").strip()
            if not prompt:
                fail(2, "prompt file is empty")
            if args.cmd == "deep-research":
                result = deep_research(px, prompt, args.timeout, args.dry_run)
            else:
                result = search(px, prompt, args.model, args.timeout)
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
