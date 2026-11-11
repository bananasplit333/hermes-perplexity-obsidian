#!/usr/bin/env python3
"""Track and close browser processes launched for one agent session.

Requires psutil at runtime (runner: uv --with psutil).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import platform

import psutil  # Runtime dependency: declare with uv --with psutil.
from contextlib import contextmanager
from pathlib import Path

HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).resolve()
STATE = HOME / "state" / "session-chrome"
REGISTRY = STATE / "sessions.json"
DEFAULT_PROFILE = "perplexity-obsidian"
DEFAULT_PORT = 9333
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class LifecycleError(RuntimeError):
    pass


def safe_id(value: str) -> str:
    if not SAFE_ID.fullmatch(value or ""):
        raise LifecycleError(f"invalid identifier: {value!r}")
    return value


def profile_dir(profile: str) -> Path:
    return (HOME / "browser-profiles" / safe_id(profile)).resolve()


def endpoint(port: int) -> str:
    if not 1 <= int(port) <= 65535:
        raise LifecycleError("invalid port")
    return f"http://127.0.0.1:{int(port)}"


def proc_info(pid) -> dict | None:
    """Portable process identity and command line, or None if unavailable."""
    try:
        proc = psutil.Process(int(pid))
        with proc.oneshot():
            if proc.status() == psutil.STATUS_ZOMBIE:
                return None
            starttime = proc.create_time()
            if sys.platform.startswith("linux"):
                stat = Path(f"/proc/{int(pid)}/stat").read_text()
                starttime = int(stat[stat.rfind(")") + 2:].split()[19])
            return {"starttime": starttime, "cmdline": " " + " ".join(proc.cmdline()) + " "}
    except (psutil.Error, OSError, IndexError, ValueError, TypeError):
        return None


def is_main_chrome_for(info: dict | None, pdir: Path, port: int | None = None) -> bool:
    if not info or " --type=" in info["cmdline"] or f" --user-data-dir={pdir} " not in info["cmdline"]:
        return False
    return port is None or f" --remote-debugging-port={port} " in info["cmdline"]


def chrome_main_pids_for_profile(pdir: Path) -> list[int]:
    found = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            if is_main_chrome_for(proc_info(proc.info["pid"]), pdir):
                found.append(int(proc.info["pid"]))
        except (psutil.Error, TypeError):
            continue
    return found


def chrome_executable() -> str:
    """Find a native Chrome executable for the current desktop platform."""
    if sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            str(Path.home() / "Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
        return next((p for p in candidates if os.path.isfile(p) and os.access(p, os.X_OK)), "")
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        candidate = shutil.which(name)
        if candidate:
            return candidate
    return ""


def cdp_version(port: int) -> dict:
    try:
        with urllib.request.urlopen(endpoint(port) + "/json/version", timeout=2) as r:
            version = json.loads(r.read())
        if not isinstance(version, dict):
            raise ValueError("endpoint response is not an object")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise LifecycleError(f"invalid or unavailable CDP endpoint on port {port}") from exc
    ws = version.get("webSocketDebuggerUrl", "")
    if not re.match(rf"^ws://(127\.0\.0\.1|localhost):{int(port)}/devtools/browser/", ws):
        raise LifecycleError("CDP browser endpoint is not loopback on the expected port")
    return {"browser_ws": ws, "browser": version.get("Browser", "")}


def same_process(entry: dict) -> bool:
    info = proc_info(entry.get("pid"))
    return bool(info and info["starttime"] == entry.get("starttime")
                and is_main_chrome_for(info, Path(entry["profile"]), entry["port"]))


@contextmanager
def registry():
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "registry.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}
        except json.JSONDecodeError as exc:
            raise LifecycleError(f"corrupt registry {REGISTRY}") from exc
        yield data
        tmp = REGISTRY.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        os.chmod(tmp, 0o600)
        tmp.replace(REGISTRY)


def largest_output_geometry() -> tuple[int, int, int, int] | None:
    """(width, height, x, y) of the largest connected X output, used to place the window."""
    try:
        listing = subprocess.run(["xrandr", "--query"], capture_output=True, text=True, check=True,
                                 env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    outputs = [(int(w), int(h), int(x), int(y))
               for w, h, x, y in re.findall(r" connected (?:primary )?(\d+)x(\d+)\+(\d+)\+(\d+)", listing)]
    return max(outputs, key=lambda g: g[0] * g[1]) if outputs else None


def window_args() -> list[str]:
    geometry = largest_output_geometry()
    if not geometry:
        return ["--window-position=150,120", "--window-size=1100,850"]
    width, height, x, y = geometry
    w, h = min(1100, width - 200), min(850, height - 200)
    return [f"--window-position={x + 150},{y + 120}", f"--window-size={max(w, 640)},{max(h, 480)}"]


def launch(session: str, profile: str, port: int, url: str, timeout: float) -> dict:
    session, pdir = safe_id(session), profile_dir(profile)
    try:
        existing = cdp_version(port)
    except (OSError, LifecycleError):
        existing = None
    if existing:
        matches = [pid for pid in chrome_main_pids_for_profile(pdir)
                   if is_main_chrome_for(proc_info(pid), pdir, port)]
        if not matches:
            raise LifecycleError(f"port {port} is occupied by a browser not verified for profile {profile}; refusing to borrow")
        entry = {"owned": False, "pid": matches[0], "profile": str(pdir), "port": port, **existing, "created": time.time()}
        with registry() as data:
            data.setdefault(session, []).append(entry)
        return {**entry, "state": "borrowed"}
    if chrome_main_pids_for_profile(pdir):
        raise LifecycleError(f"profile {profile} is already open without CDP on port {port}; close it or use its real port")
    chrome = chrome_executable()
    if not chrome:
        raise LifecycleError("Chrome or Chromium executable not found")
    pdir.mkdir(parents=True, exist_ok=True)
    STATE.mkdir(parents=True, exist_ok=True)
    cmd = [chrome, f"--user-data-dir={pdir}", f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1", "--no-first-run", "--no-default-browser-check", "--new-window"]
    if sys.platform.startswith("linux"):
        cmd += ["--ozone-platform=x11", *window_args()]
    cmd.append(url)
    env = {**os.environ, "HOME": str(HOME)}
    if sys.platform.startswith("linux"):
        env["DISPLAY"] = os.environ.get("DISPLAY", ":0")
    with (STATE / f"{session}.log").open("ab") as log:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True, env=env)
    info = proc_info(proc.pid)
    if not info:
        raise LifecycleError("Chrome exited immediately; see log")
    entry = {"owned": True, "pid": proc.pid, "starttime": info["starttime"], "profile": str(pdir), "port": port,
             "browser_ws": None, "browser": None, "created": time.time()}
    with registry() as data:
        data.setdefault(session, []).append(entry)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise LifecycleError(f"Chrome exited during startup; see {STATE / (session + '.log')}")
        try:
            ident = cdp_version(port)
            break
        except (OSError, LifecycleError):
            time.sleep(0.25)
    else:
        raise LifecycleError(f"Chrome did not expose CDP on port {port}; it stays registered, run close")
    with registry() as data:
        for e in data.get(session, []):
            if e.get("pid") == entry["pid"] and e.get("starttime") == entry["starttime"]:
                e.update(ident)
                entry = e
    return {**entry, "state": "running"}


def adopt(session: str, pid: int, profile: str, port: int) -> dict:
    session, pdir = safe_id(session), profile_dir(profile)
    info = proc_info(pid)
    if info is None or not is_main_chrome_for(info, pdir, port):
        raise LifecycleError("PID is not the main Chrome process for that profile and port")
    entry = {"owned": True, "pid": pid, "starttime": info["starttime"], "profile": str(pdir), "port": port,
             **cdp_version(port), "created": time.time(), "adopted": True}
    with registry() as data:
        browsers = data.setdefault(session, [])
        if any(e.get("pid") == pid and e.get("starttime") == info["starttime"] for e in browsers):
            return {**entry, "state": "already-registered"}
        browsers.append(entry)
    return {**entry, "state": "running"}


def entry_state(entry: dict) -> str:
    if not entry.get("owned"):
        return "borrowed"
    if not proc_info(entry["pid"]):
        return "exited"
    if not same_process(entry):
        return "pid-reused"
    try:
        ident = cdp_version(entry["port"])
    except (OSError, LifecycleError):
        return "alive-cdp-unavailable"
    if entry.get("browser_ws") and ident["browser_ws"] != entry["browser_ws"]:
        return "cdp-identity-mismatch"
    return "running"


def status(session: str) -> dict:
    with registry() as data:
        browsers = list(data.get(safe_id(session), []))
    return {"session": session, "browsers": [{**e, "state": entry_state(e)} for e in browsers]}


def send_browser_close(browser_ws: str) -> None:
    import websocket

    ws = websocket.create_connection(browser_ws, timeout=5, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
        try:
            ws.recv()
        except Exception:
            pass  # Chrome often drops the socket before replying; PID exit is the real signal.
    finally:
        ws.close()


def close(session: str, wait: float = 20) -> dict:
    session = safe_id(session)
    with registry() as data:
        browsers = list(data.get(session, []))
    results, remaining = [], []
    for entry in browsers:
        state = entry_state(entry)
        if state in ("borrowed", "exited", "pid-reused"):
            results.append({"pid": entry.get("pid"), "result": {"borrowed": "left-open-not-owned"}.get(state, state)})
            continue
        if state != "running":
            results.append({"pid": entry["pid"], "result": f"refused-{state}"})
            remaining.append(entry)
            continue
        try:
            send_browser_close(entry["browser_ws"] or cdp_version(entry["port"])["browser_ws"])
        except Exception as exc:
            results.append({"pid": entry["pid"], "result": f"close-error:{type(exc).__name__}"})
            remaining.append(entry)
            continue
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and same_process(entry):
            time.sleep(0.2)
        if same_process(entry):
            results.append({"pid": entry["pid"], "result": "still-alive-after-Browser.close"})
            remaining.append(entry)
        else:
            results.append({"pid": entry["pid"], "result": "closed"})
    with registry() as data:
        if remaining:
            data[session] = remaining
        else:
            data.pop(session, None)
    return {"session": session, "results": results, "owned_still_open": len(remaining)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("launch", "adopt", "status", "close"):
        p = sub.add_parser(name)
        p.add_argument("--session", required=True)
        if name in ("launch", "adopt"):
            p.add_argument("--profile", default=DEFAULT_PROFILE)
            p.add_argument("--port", type=int, default=DEFAULT_PORT)
        if name == "launch":
            p.add_argument("--url", default="about:blank")
            p.add_argument("--timeout", type=float, default=30)
        if name == "adopt":
            p.add_argument("--pid", type=int, required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "launch":
            result = launch(args.session, args.profile, args.port, args.url, args.timeout)
        elif args.cmd == "adopt":
            result = adopt(args.session, args.pid, args.profile, args.port)
        elif args.cmd == "status":
            result = status(args.session)
        else:
            result = close(args.session)
        print(json.dumps(result, indent=2))
        return 1 if args.cmd == "close" and result["owned_still_open"] else 0
    except LifecycleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
