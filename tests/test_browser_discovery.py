"""Offline browser discovery/selection tests, including Windows-style installations."""
import json
from pathlib import Path

import pytest
from openagent.browser import discover_browsers, resolve_executable
from openagent.browser import load_selection, save_selection, profile_dir_for
from openagent.browser import discovery as browser_discovery


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"dummy binary")
    return path


def test_portable_extraction_dir_is_supported(tmp_path):
    chrome = _touch(tmp_path / "chrome-win64" / "chrome.exe")
    assert resolve_executable(tmp_path, "chromium") == chrome
    assert resolve_executable(chrome.parent, "chrome") == chrome
    with pytest.raises(ValueError, match="no matching"):
        resolve_executable(tmp_path, "firefox")


def test_windows_discovery_with_system_firefox_and_playwright_firefox(tmp_path, monkeypatch):
    pf = tmp_path / "Program Files"
    app_data = tmp_path / "Local AppData"
    chrome = _touch(pf / "Google" / "Chrome" / "Application" / "chrome.exe")
    edge = _touch(pf / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    ff = _touch(pf / "Mozilla Firefox" / "firefox.exe")
    pw_firefox = _touch(app_data / "ms-playwright" / "firefox-1497" / "firefox" / "firefox.exe")
    pw_chromium = _touch(app_data / "ms-playwright" / "chromium-1200" / "chrome-win64" / "chrome.exe")
    monkeypatch.setattr(browser_discovery, "_playwright_revisions", lambda: {"firefox": "1497", "chromium": "1200"})
    found = discover_browsers(platform_name="win32", home=tmp_path, environ={"PROGRAMFILES": str(pf), "LOCALAPPDATA": str(app_data)})
    assert {Path(x.executable) for x in found} == {chrome, edge, ff, pw_firefox, pw_chromium}
    stock = next(x for x in found if x.executable == str(ff))
    bundled = next(x for x in found if x.executable == str(pw_firefox))
    assert not stock.usable
    assert bundled.usable and bundled.source == "playwright-cache"
    assert any(x.id == "chrome" and x.usable for x in found)


def test_custom_path_does_not_search_entire_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_discovery, "_playwright_revisions", lambda: {})
    portable = _touch(tmp_path / "downloaded" / "chrome-win64" / "chrome.exe")
    found = discover_browsers(platform_name="win32", home=tmp_path, environ={}, extra_paths=[tmp_path / "downloaded"])
    assert any(Path(x.executable) == portable for x in found)
    assert not any(x.source == "system" for x in found)


def test_wrong_cache_root_is_not_a_playwright_install(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_discovery, "_playwright_revisions", lambda: {"chromium": "1200"})
    _touch(tmp_path / "chrome-win64" / "chrome.exe")
    found = discover_browsers(platform_name="win32", home=tmp_path, environ={"PLAYWRIGHT_BROWSERS_PATH": str(tmp_path)})
    assert not any(x.source == "playwright-cache" for x in found)


def test_save_reload_selection_no_credentials(tmp_path):
    file = tmp_path / "cfg" / "browser.json"
    exe = tmp_path / "chrome.exe"
    save_selection("chrome", exe, file)
    assert load_selection(file) == {"browser": "chrome", "executable_path": str(exe)}
    assert sorted(json.loads(file.read_text()).keys()) == ["browser", "executable_path"]
    save_selection("firefox", file=file)
    assert load_selection(file) == {"browser": "firefox", "executable_path": None}
    with pytest.raises(ValueError, match="unknown browser"):
        save_selection("safari", file=file)


def test_missing_selection_defaults_to_chromium(tmp_path):
    assert load_selection(tmp_path / "missing.json")["browser"] == "chromium"
    assert profile_dir_for("firefox") != profile_dir_for("chromium")
