import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import session_chrome as chrome


def test_mac_chrome_native_application_path(monkeypatch, tmp_path):
    app_exec = tmp_path / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    app_exec.parent.mkdir(parents=True)
    app_exec.touch()
    app_exec.chmod(0o755)
    monkeypatch.setattr(chrome.sys, "platform", "darwin")
    monkeypatch.setattr(chrome.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(chrome.os, "path", os.path)
    with patch.object(chrome.os.path, "isfile", side_effect=lambda p: p == str(app_exec)), patch.object(
        chrome.os, "access", return_value=True
    ):
        assert chrome.chrome_executable() == str(app_exec)


def test_linux_chrome_candidates(monkeypatch):
    monkeypatch.setattr(chrome.sys, "platform", "linux")
    monkeypatch.setattr(chrome.shutil, "which", lambda name: "/usr/bin/chromium" if name == "chromium" else None)
    assert chrome.chrome_executable() == "/usr/bin/chromium"


def test_proc_info_uses_psutil_for_current_process():
    info = chrome.proc_info(os.getpid())
    assert info is not None
    assert info["starttime"] > 0
    assert "python" in info["cmdline"].lower()


def test_linux_identity_does_not_change_with_wall_clock(monkeypatch):
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux process tick identity")
    before = chrome.proc_info(os.getpid())
    monkeypatch.setattr(chrome.psutil.Process, "create_time", lambda self: 1.0)
    after = chrome.proc_info(os.getpid())
    assert before["starttime"] == after["starttime"]


def test_occupied_unverified_cdp_port_is_not_borrowed(monkeypatch, tmp_path):
    monkeypatch.setattr(chrome, "HOME", tmp_path)
    monkeypatch.setattr(chrome, "cdp_version", lambda port: {"browser_ws": f"ws://127.0.0.1:{port}/devtools/browser/x", "browser": "Chrome"})
    monkeypatch.setattr(chrome, "chrome_main_pids_for_profile", lambda pdir: [])
    with pytest.raises(chrome.LifecycleError, match="refusing to borrow"):
        chrome.launch("test-session", "test-profile", 9333, "about:blank", 0.1)


def test_close_keeps_unknown_state_registered(monkeypatch, tmp_path):
    monkeypatch.setattr(chrome, "HOME", tmp_path)
    monkeypatch.setattr(chrome, "STATE", tmp_path / "state")
    monkeypatch.setattr(chrome, "REGISTRY", tmp_path / "state" / "sessions.json")
    chrome.STATE.mkdir(parents=True, exist_ok=True)
    chrome.REGISTRY.write_text('{"s": [{"owned": true, "pid": 10, "starttime": 1, "profile": "/p", "port": 9333}]}')
    monkeypatch.setattr(chrome, "entry_state", lambda entry: "alive-cdp-unavailable")
    result = chrome.close("s")
    assert result["owned_still_open"] == 1
    assert "s" in __import__("json").loads(chrome.REGISTRY.read_text())
