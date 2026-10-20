"""Offline browser discovery; never opens browsers or reads profile credentials.

Browser executable presence is not evidence that Playwright can drive it.
In particular, stock Firefox is listed for diagnostics, but Playwright needs
its instrumented Firefox build. Detected entries do not imply logged-in status.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Mapping, Sequence


@dataclass(frozen=True)
class BrowserInstallation:
    id: str
    name: str
    engine: str
    executable: str
    source: str
    usable: bool
    note: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def resolve_executable(path: Path, browser: str) -> Path:
    """Allow --browser-path to name either the executable or its extraction folder."""
    path = path.expanduser().resolve()
    if path.is_file():
        return path
    if not path.is_dir():
        raise ValueError("browser path does not exist")
    names = {
        "chromium": ("chrome.exe", "chrome", "chromium", "Chromium"),
        "chrome": ("chrome.exe", "chrome"),
        "msedge": ("msedge.exe", "msedge"),
        "firefox": ("firefox.exe", "firefox"),
    }[browser]
    subdirs = ("", "chrome-win64", "chrome-win", "chrome-linux", "firefox", "Application")
    for subdir in subdirs:
        for name in names:
            candidate = path / subdir / name if subdir else path / name
            if candidate.is_file():
                return candidate.resolve()
    raise ValueError("no matching browser executable found inside the selected folder")


def _playwright_revisions() -> dict[str, str]:
    """Read the exact browser revisions expected by this installed Playwright."""
    try:
        spec = importlib.util.find_spec("playwright")
        if spec is None or not spec.origin:
            return {}
        metadata = Path(spec.origin).parent / "driver" / "package" / "browsers.json"
        data = json.loads(metadata.read_text(encoding="utf-8"))
        return {entry["name"]: entry["revision"] for entry in data.get("browsers", [])}
    except (OSError, ValueError, KeyError, TypeError, ImportError):
        return {}


def _registry_app_paths() -> dict[str, Path]:
    """Windows app-path registration, optional and read-only."""
    if sys.platform != "win32":
        return {}
    try:
        import winreg
        names = {"chrome": "chrome.exe", "msedge": "msedge.exe", "firefox": "firefox.exe"}
        result = {}
        for key, exe in names.items():
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as handle:
                        value, _ = winreg.QueryValueEx(handle, "")
                    if Path(value).is_file():
                        result[key] = Path(value)
                        break
                except (OSError, ValueError):
                    continue
        return result
    except ImportError:
        return {}


def discover_browsers(
    *,
    extra_paths: Sequence[Path] = (),
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> list[BrowserInstallation]:
    """List available system/browser-cache installations without starting them.

    extra_paths lets the user point to portable browser extraction directories.
    This scan is bounded; we intentionally never crawl whole disks for executables.
    """
    platform_name = platform_name or sys.platform
    env = environ if environ is not None else os.environ
    home = home or Path.home()
    results: list[BrowserInstallation] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, path: Path, source: str, *, bundled: bool = False) -> None:
        if not path.is_file():
            return
        resolved = str(path.resolve())
        key = (kind, resolved.lower() if platform_name == "win32" else resolved)
        if key in seen:
            return
        seen.add(key)
        names = {"chromium": "Chromium", "chrome": "Google Chrome", "msedge": "Microsoft Edge", "firefox": "Firefox"}
        usable = kind != "firefox" or bundled
        if bundled:
            note = "Matching Playwright browser build; uses an OpenAgent persistent profile"
        elif kind == "firefox":
            note = "Stock Firefox cannot be controlled reliably by Playwright; install Playwright Firefox"
        else:
            note = "System/portable Chromium-based executable; compatibility depends on version"
        results.append(BrowserInstallation(kind, names[kind], "firefox" if kind == "firefox" else "chromium", resolved, source, usable, note))

    if platform_name == "win32":
        program_files = [Path(env[name]) for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA") if env.get(name)]
        for base in program_files:
            for kind, relative in (
                ("chrome", "Google/Chrome/Application/chrome.exe"),
                ("msedge", "Microsoft/Edge/Application/msedge.exe"),
                ("firefox", "Mozilla Firefox/firefox.exe"),
            ):
                add(kind, base / relative, "system")
        if platform_name == sys.platform:
            for kind, path in _registry_app_paths().items():
                add(kind, path, "registry")
    elif platform_name == "darwin":
        for kind, rel in (
            ("chrome", "Google Chrome.app/Contents/MacOS/Google Chrome"),
            ("msedge", "Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            ("firefox", "Firefox.app/Contents/MacOS/firefox"),
        ):
            add(kind, Path("/Applications") / rel, "system")
            add(kind, home / "Applications" / rel, "system")
    else:
        for kind, names in (
            ("chrome", ("google-chrome", "google-chrome-stable")),
            ("msedge", ("microsoft-edge", "microsoft-edge-stable")),
            ("firefox", ("firefox",)),
            ("chromium", ("chromium", "chromium-browser")),
        ):
            for name in names:
                location = shutil.which(name)
                if location:
                    add(kind, Path(location), "PATH")

    # The cache root must contain chromium-REVISION/firefox-REVISION subfolders.
    cache_env = env.get("PLAYWRIGHT_BROWSERS_PATH")
    if cache_env and cache_env != "0":
        cache = Path(cache_env).expanduser()
    elif platform_name == "win32":
        cache = Path(env.get("LOCALAPPDATA", str(home / "AppData/Local"))) / "ms-playwright"
    elif platform_name == "darwin":
        cache = home / "Library/Caches/ms-playwright"
    else:
        cache = Path(env.get("XDG_CACHE_HOME", str(home / ".cache"))) / "ms-playwright"
    folders = {
        "win32": {"chromium": "chrome-win64/chrome.exe", "firefox": "firefox/firefox.exe"},
        "darwin": {"chromium": "chrome-mac/Chromium.app/Contents/MacOS/Chromium", "firefox": "firefox/Nightly.app/Contents/MacOS/firefox"},
    }
    relative = folders.get(platform_name, {"chromium": "chrome-linux/chrome", "firefox": "firefox/firefox"})
    for kind, revision in _playwright_revisions().items():
        if kind in relative:
            add(kind, cache / f"{kind}-{revision}" / relative[kind], "playwright-cache", bundled=True)
    # Portable downloads can be anywhere. Look only where the user explicitly asks.
    for candidate in extra_paths:
        for kind in ("chromium", "chrome", "msedge", "firefox"):
            try:
                path = resolve_executable(candidate, kind)
            except ValueError:
                continue
            exe = path.name.lower()
            if kind in ("chromium", "chrome") and exe not in ("chrome.exe", "chrome", "chromium", "Chromium"):
                continue
            if kind == "msedge" and not exe.startswith("msedge"):
                continue
            if kind == "firefox" and not exe.startswith("firefox"):
                continue
            # Portable Firefox can be a patched Playwright build, but we cannot prove it.
            add(kind, path, "custom-path")
    return results
