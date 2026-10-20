from argparse import Namespace
from pathlib import Path

import pytest
from openagent import cli


def test_probe_config_with_downloaded_chromium_dir(tmp_path, monkeypatch):
    chrome = tmp_path / "chrome-win64" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_bytes(b"fake")
    monkeypatch.setattr("openagent.browser.selection.load_selection", lambda: {"browser": "chromium", "executable_path": None})
    # The CLI imported wrapper has same function name in browser package.
    monkeypatch.setattr("openagent.browser.load_selection", lambda: {"browser": "chromium", "executable_path": None})
    args = Namespace(browser="chromium", browser_path=chrome.parent, profile_dir=tmp_path / "profile", mode="persistent", cdp_url="http://127.0.0.1:9222", headful=True)
    config = cli._browser_settings(args)
    assert config.executable_path == chrome
    assert config.headless is False


def test_cdp_cannot_use_firefox(tmp_path, monkeypatch):
    monkeypatch.setattr("openagent.browser.load_selection", lambda: {"browser": "chromium", "executable_path": None})
    args = Namespace(browser="firefox", browser_path=None, profile_dir=tmp_path / "profile", mode="cdp", cdp_url="http://127.0.0.1:9222", headful=False)
    with pytest.raises(ValueError, match="Firefox cannot attach"):
        cli._browser_settings(args)
