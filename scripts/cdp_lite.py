"""Minimal CDP client for a Chrome started with --remote-debugging-port.

Usage from other scripts:
    from cdp_lite import Page
    p = Page("https://example.com")
    p.call("Runtime.evaluate", expression="document.title")
"""
import json
import time
import urllib.request

import websocket


def _http(path, port=9222):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
        return json.loads(r.read().decode())


def list_targets(port=9222):
    return _http("/json/list", port)


def find_page(match=None, port=9222):
    pages = [t for t in list_targets(port) if t.get("type") == "page"]
    if match:
        for t in pages:
            if match in (t.get("url") or ""):
                return t
    return pages[0] if pages else None


class Page:
    def __init__(self, match=None, port=9222, ws_url=None):
        target = None if ws_url else find_page(match, port)
        self.ws_url = ws_url or target["webSocketDebuggerUrl"]
        self.target_id = target.get("id") if target else None
        self.ws = websocket.create_connection(self.ws_url, timeout=30, suppress_origin=True)
        self._id = 0

    def call(self, method, **params):
        self._id += 1
        msg_id = self._id
        self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))
        while True:
            raw = self.ws.recv()
            data = json.loads(raw)
            if data.get("id") == msg_id:
                if "error" in data:
                    raise RuntimeError(f"{method}: {data['error']}")
                return data.get("result")

    def js(self, expression, await_promise=False):
        res = self.call(
            "Runtime.evaluate",
            expression=expression,
            returnByValue=True,
            awaitPromise=await_promise,
        )
        return res.get("result", {}).get("value")

    def url(self):
        return self.call("Runtime.evaluate", expression="location.href", returnByValue=True)["result"]["value"]

    def screenshot(self, path):
        import base64

        res = self.call("Page.captureScreenshot", format="png")
        with open(path, "wb") as f:
            f.write(base64.b64decode(res["data"]))
        return path

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def wait_for_page(match, timeout=30, port=9222):
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = find_page(match, port)
        if t:
            return t
        time.sleep(1)
    raise TimeoutError(f"no page matching {match!r} within {timeout}s")
