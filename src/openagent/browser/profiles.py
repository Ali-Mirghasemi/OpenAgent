"""Discover local browser profile *locations* without reading credentials.

No cookie DB, passwords, history, or local-storage values are opened during
profile discovery. Scans are intentionally bounded to known profile roots.
"""
from __future__ import annotations

import configparser
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class BrowserProfile:
    browser: str
    name: str
    path: str
    origin: str
    reuse: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def discover_profiles(
    *, platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    include_openagent: bool = True,
) -> list[BrowserProfile]:
    platform_name = platform_name or sys.platform
    env = os.environ if environ is None else environ
    home = Path.home() if home is None else home
    results: list[BrowserProfile] = []
    seen: set[str] = set()

    def add(browser: str, name: str, location: Path, origin: str, reuse: str) -> None:
        if not location.is_dir():
            return
        resolved = str(location.resolve())
        marker = resolved.casefold() if platform_name == 'win32' else resolved
        if marker in seen:
            return
        seen.add(marker)
        results.append(BrowserProfile(browser, name, resolved, origin, reuse))

    if platform_name == 'win32':
        roaming = Path(env.get('APPDATA', str(home / 'AppData/Roaming')))
        local = Path(env.get('LOCALAPPDATA', str(home / 'AppData/Local')))
        firefox_root = roaming / 'Mozilla/Firefox'
        chrome_roots = [
            ('chrome', local / 'Google/Chrome/User Data'),
            ('msedge', local / 'Microsoft/Edge/User Data'),
        ]
    elif platform_name == 'darwin':
        firefox_root = home / 'Library/Application Support/Firefox'
        chrome_roots = [
            ('chrome', home / 'Library/Application Support/Google/Chrome'),
            ('msedge', home / 'Library/Application Support/Microsoft Edge'),
        ]
    else:
        firefox_root = home / '.mozilla/firefox'
        config = Path(env.get('XDG_CONFIG_HOME', str(home / '.config')))
        chrome_roots = [
            ('chrome', config / 'google-chrome'),
            ('chromium', config / 'chromium'),
            ('msedge', config / 'microsoft-edge'),
        ]

    ini = firefox_root / 'profiles.ini'
    if ini.is_file():
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(ini, encoding='utf-8')
        except (configparser.Error, OSError, UnicodeError):
            parser = configparser.ConfigParser()
        for section in parser.sections():
            if not section.lower().startswith('profile'):
                continue
            relative_path = parser.get(section, 'Path', fallback='').strip()
            if not relative_path:
                continue
            profile_path = Path(relative_path)
            if not profile_path.is_absolute() and parser.get(section, 'IsRelative', fallback='1') == '1':
                profile_path = firefox_root / profile_path
            elif not profile_path.is_absolute():
                continue
            # Never follow profile entries outside the known root when marked relative.
            if parser.get(section, 'IsRelative', fallback='1') == '1' and not profile_path.resolve().is_relative_to(firefox_root.resolve()):
                continue
            name = parser.get(section, 'Name', fallback=section).strip() or section
            add('firefox', name, profile_path, 'system', 'site-cookie-import')
    # Firefox occasionally has profile folders not listed in profiles.ini.
    # Avoid guessing which one holds live cookies; user can supply an explicit path.

    for browser, root in chrome_roots:
        if not root.is_dir():
            continue
        for folder in (root / 'Default', *sorted(root.glob('Profile [0-9]*'))):
            add(browser, folder.name, folder, 'system', 'discovery-only')

    if include_openagent:
        managed = home / '.openagent'
        add('chromium', 'OpenAgent Chromium', managed / 'browser-profile', 'openagent', 'persistent')
        for kind in ('chrome', 'msedge', 'firefox'):
            add(kind, f'OpenAgent {kind}', managed / 'browser-profiles' / kind, 'openagent', 'persistent')
    return results
