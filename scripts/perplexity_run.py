#!/usr/bin/env python3
"""Run the existing Perplexity browser workflow end-to-end.

Examples:
  python3 perplexity_run.py --prompt-file question.txt
  python3 perplexity_run.py --prompt "Find laptop-friendly cafes in Toronto"
  python3 perplexity_run.py --mode search --model "Claude Sonnet 5.5" --prompt-file question.txt
  python3 perplexity_run.py --prompt-file question.txt --dry-run

The wrapper never signs in, spends credits intentionally, or publishes notes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from obsidian_vault import load as load_vault_config, VaultError

HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).resolve()
SCRIPTS = Path(__file__).resolve().parent
ORIGIN = "https://www.perplexity.ai/"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def run(argv: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


def helper(script: str, *args: str, timeout: int | None = None) -> subprocess.CompletedProcess:
    uv = os.environ.get("UV", "uv")
    return run([uv, "run", "--no-project", "--with", "websocket-client", "--with", "psutil", "python", str(SCRIPTS / script), *args], timeout)


def read_launch(output: str) -> dict:
    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"launch returned invalid JSON: {output[:300]}") from exc
    if value.get("state") not in {"running", "borrowed", "already-registered"}:
        raise RuntimeError(f"unexpected browser state: {value.get('state')!r}")
    return value


def main(argv: list[str] | None = None, call=helper) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--prompt")
    inputs.add_argument("--prompt-file", type=Path)
    parser.add_argument("--mode", choices=("deep-research", "search"), default="deep-research")
    parser.add_argument("--model", help="Required by Search mode; must be included in the signed-in Pro plan")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--session", help="Safe reusable session ID; defaults to a timestamped ID")
    parser.add_argument("--resume", action="store_true", help="reuse a browser already registered to --session")
    parser.add_argument("--out", type=Path, help="Result JSON path; defaults to profile scratch")
    parser.add_argument("--dry-run", action="store_true", help="Verify the browser workflow without submitting research")
    args = parser.parse_args(argv)

    if args.mode == "search" and not args.model:
        parser.error("--model is required with --mode search")
    if args.mode == "deep-research" and args.model:
        parser.error("--model is not used by Deep Research; it must stay on Default")
    if args.mode == "search" and args.dry_run:
        parser.error("--dry-run is supported only for Deep Research")
    if args.timeout < 1 or not 1 <= args.port <= 65535:
        parser.error("timeout and port must be positive and port must be <= 65535")
    session = args.session or "pplx-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    if not SAFE_ID.fullmatch(session):
        parser.error("--session must contain 1-80 letters, digits, dots, underscores, or hyphens")

    try:
        config = load_vault_config()
        if not config:
            raise VaultError("No Obsidian vault configured. Configure one before running research.")
    except (VaultError, OSError) as exc:
        parser.error(str(exc))

    prompt = args.prompt if args.prompt is not None else args.prompt_file.read_text(encoding="utf-8").strip()
    if not prompt.strip():
        parser.error("prompt cannot be empty")
    scratch = HOME / "cache" / "scratch" / "perplexity" / session
    out = (args.out or scratch / "result.json").expanduser().resolve()
    if not args.dry_run and out.exists():
        parser.error(f"refusing to overwrite existing output file: {out}")
    prompt_path = scratch / "prompt.txt"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt.strip(), encoding="utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)

    launched = False
    preserve_for_login = False
    primary_code = 2
    cleanup_error = None
    try:
        existing = call("session_chrome.py", "status", "--session", session)
        if existing.returncode:
            print(existing.stderr or existing.stdout, file=sys.stderr, end="")
            primary_code = existing.returncode
            return primary_code
        current = json.loads(existing.stdout)
        active = [b for b in current.get("browsers", []) if b.get("state") in {"running", "alive-cdp-unavailable"}]
        if active and not args.resume:
            print(f"session {session} already has a running browser; use --resume or choose another --session", file=sys.stderr)
            primary_code = 13
            return primary_code
        if len(active) > 1:
            print(f"session {session} has multiple running browsers; refusing ambiguous resume", file=sys.stderr)
            primary_code = 13
            return primary_code
        if active:
            browser = active[0]
            launched = True
            try:
                args.port = int(browser["port"])
            except (KeyError, TypeError, ValueError):
                print("registered browser has no valid CDP port; cannot resume safely", file=sys.stderr)
                primary_code = 13
                return primary_code
        else:
            launched = True
            launch = call("session_chrome.py", "launch", "--session", session, "--port", str(args.port), "--url", ORIGIN)
            if launch.returncode:
                print(launch.stderr or launch.stdout, file=sys.stderr, end="")
                primary_code = launch.returncode
                return primary_code
            browser = read_launch(launch.stdout)

        check = call("perplexity_cdp.py", "--port", str(args.port), "check")
        if check.returncode:
            print(check.stderr or check.stdout, file=sys.stderr, end="")
            primary_code = check.returncode
            preserve_for_login = check.returncode == 3
            if preserve_for_login:
                print(f"Sign in to Perplexity Pro in the visible browser, then rerun with --session {session} --resume.", file=sys.stderr)
            return primary_code

        cmd = ["--port", str(args.port), args.mode, "--prompt-file", str(prompt_path), "--out", str(out), "--timeout", str(args.timeout)]
        if args.mode == "search":
            cmd.extend(["--model", args.model])
        elif args.dry_run:
            cmd.append("--dry-run")
        research = call("perplexity_cdp.py", *cmd, timeout=args.timeout + 180)
        if research.returncode:
            print(research.stderr or research.stdout, file=sys.stderr, end="")
            if research.stdout:
                print(research.stdout, file=sys.stderr, end="" if research.stdout.endswith("\n") else "\n")
            primary_code = research.returncode
            return primary_code
        if args.dry_run:
            print(research.stdout, end="" if research.stdout.endswith("\n") else "\n")
            primary_code = 0
            return 0
        if not out.is_file():
            print(f"research exited successfully but result file is missing: {out}", file=sys.stderr)
            primary_code = 2
            return primary_code
        result = json.loads(out.read_text(encoding="utf-8"))
        print(json.dumps({"research_ok": True, "session": session, "browser_state": browser["state"],
                          "thread_url": result.get("thread_url"), "title": result.get("title"),
                          "sources_count": result.get("sources_count"), "out": str(out)}, indent=2))
        primary_code = 0
        return 0
    except (OSError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(f"perplexity run failed: {exc}", file=sys.stderr)
        primary_code = 2
        return primary_code
    finally:
        if launched and not preserve_for_login:
            try:
                closed = call("session_chrome.py", "close", "--session", session)
            except Exception as exc:
                closed = None
                cleanup_error = f"close call failed: {exc}"
            if closed is not None and closed.returncode:
                cleanup_error = closed.stderr or closed.stdout or f"close exited {closed.returncode}"
            try:
                status = call("session_chrome.py", "status", "--session", session)
                if status.returncode:
                    cleanup_error = cleanup_error or status.stderr or status.stdout or f"status exited {status.returncode}"
                else:
                    state = json.loads(status.stdout)
                    still_open = [b for b in state.get("browsers", []) if b.get("owned") and b.get("state") in {"running", "alive-cdp-unavailable"}]
                    if still_open:
                        cleanup_error = f"owned browser still running: {[b.get('pid') for b in still_open]}"
            except Exception as exc:
                cleanup_error = cleanup_error or f"could not verify browser cleanup: {exc}"
            if cleanup_error:
                print(f"cleanup warning: {cleanup_error}", file=sys.stderr)
                if primary_code == 0:
                    raise SystemExit(12)


if __name__ == "__main__":
    raise SystemExit(main())
