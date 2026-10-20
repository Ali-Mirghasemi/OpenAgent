"""No browser binaries, actual sessions or network used in these tests."""
from __future__ import annotations

import asyncio
import sqlite3
import time
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from openagent.browser import BrowserConfig, BrowserManager, BrowserSessionError
from openagent.browser.profiles import BrowserProfile, discover_profiles
from openagent.browser.firefox_session import firefox_site_cookies, SessionImportError, _MAX_PLAYWRIGHT_EXPIRY, _portable_expiry
from openagent import cli


def make_firefox_profile(tmp_path: Path) -> Path:
    root = tmp_path / 'Roaming' / 'Mozilla' / 'Firefox'
    profile = root / 'Profiles' / 'abc.default-release'
    profile.mkdir(parents=True)
    (root / 'profiles.ini').write_text(
        '[Profile0]\nName=default-release\nIsRelative=1\nPath=Profiles/abc.default-release\n'
    )
    return profile


def make_cookie_db(profile: Path):
    conn = sqlite3.connect(str(profile / 'cookies.sqlite'))
    conn.execute('CREATE TABLE moz_cookies ('
                 'name TEXT, value TEXT, host TEXT, path TEXT, expiry INTEGER, '
                 'isSecure INTEGER, isHttpOnly INTEGER, sameSite INTEGER, originAttributes TEXT)')
    future = int(time.time() + 3600)
    expired = int(time.time() - 3600)
    rows = [
        ('session', 'private-chatgpt-cookie', '.chatgpt.com', '/', future, 1, 1, 1, ''),
        ('host', 'another-private-cookie', 'chatgpt.com', '/', future, 1, 0, 2, ''),
        ('expired', 'stale', 'chatgpt.com', '/', expired, 1, 0, 1, ''),
        ('google', 'dont-transfer', '.google.com', '/', future, 1, 1, 1, ''),
        ('evil', 'dont-transfer', 'notchatgpt.com', '/', future, 1, 1, 1, ''),
        ('container', 'dont-transfer', 'chatgpt.com', '/', future, 1, 1, 1, '^userContextId=3'),
    ]
    conn.executemany('INSERT INTO moz_cookies VALUES (?,?,?,?,?,?,?,?,?)', rows)
    conn.commit()
    conn.close()


def test_discover_windows_profiles_names_only(tmp_path):
    ff = make_firefox_profile(tmp_path)
    chrome = tmp_path / 'Local' / 'Google/Chrome/User Data/Default'
    chrome.mkdir(parents=True)
    edge = tmp_path / 'Local' / 'Microsoft/Edge/User Data/Profile 1'
    edge.mkdir(parents=True)
    results = discover_profiles(
        platform_name='win32', home=tmp_path,
        environ={'APPDATA': str(tmp_path / 'Roaming'), 'LOCALAPPDATA': str(tmp_path / 'Local')},
        include_openagent=False,
    )
    assert {Path(x.path) for x in results} == {ff, chrome, edge}
    assert {x.reuse for x in results} == {'site-cookie-import', 'discovery-only'}
    assert all('private' not in str(x.as_dict()) for x in results)


def test_malformed_profiles_ini_does_not_abort(tmp_path):
    root = tmp_path / 'AppData/Roaming/Mozilla/Firefox'
    root.mkdir(parents=True)
    (root / 'profiles.ini').write_text('[malformed\n')
    assert discover_profiles(platform_name='win32', home=tmp_path, environ={}) == []


def test_relative_profile_path_traversal_skipped(tmp_path):
    root = tmp_path / 'AppData/Roaming/Mozilla/Firefox'
    root.mkdir(parents=True)
    (tmp_path / 'outside').mkdir()
    (root / 'profiles.ini').write_text('[Profile0]\nPath=../../../../outside\nIsRelative=1\n')
    assert discover_profiles(platform_name='win32', home=tmp_path, environ={}) == []


def test_firefox_read_imports_only_site_scope_and_no_expired(tmp_path):
    ff = make_firefox_profile(tmp_path)
    make_cookie_db(ff)
    result = firefox_site_cookies(ff, 'chatgpt.com')
    assert {c['name'] for c in result} == {'session', 'host'}
    assert {c['value'] for c in result} == {'private-chatgpt-cookie', 'another-private-cookie'}
    assert result[0]['sameSite'] == 'Lax'


def test_import_normalizes_far_future_firefox_expiries(tmp_path):
    """Some real Firefox profiles use expiry values beyond Playwright's limit."""
    ff = make_firefox_profile(tmp_path)
    make_cookie_db(ff)
    db = ff / 'cookies.sqlite'
    with sqlite3.connect(str(db)) as conn:
        conn.execute('UPDATE moz_cookies SET expiry=? WHERE name=?',
                     (2**63 - 1, 'session'))
        conn.execute('UPDATE moz_cookies SET expiry=? WHERE name=?',
                     (_MAX_PLAYWRIGHT_EXPIRY + 10, 'host'))
    result = firefox_site_cookies(ff, 'chatgpt.com')
    assert len(result) == 2
    assert {c['expires'] for c in result} == {_MAX_PLAYWRIGHT_EXPIRY}
    assert all(isinstance(c['expires'], int) for c in result)


def test_expiry_portability_rejects_invalid_or_expired():
    now = time.time()
    assert _portable_expiry(2**63 - 1, now) == _MAX_PLAYWRIGHT_EXPIRY
    assert _portable_expiry(int(now) + 200, now) == int(now) + 200
    for invalid in (0, -1, int(now) - 10, float('nan'), float('inf'),
                    -float('inf'), 'bad', None, True):
        assert _portable_expiry(invalid, now) is None


@pytest.mark.asyncio
async def test_backend_cookie_failure_is_sanitized(tmp_path):
    """Do not leak potentially secret Playwright driver errors to CLI users."""
    class RejectingBackend:
        async def start(self, _config): pass
        async def stop(self): pass
        def is_connected(self): return True
        async def add_cookies(self, cookies):
            raise RuntimeError('driver failure with secret cookie content')

    manager = BrowserManager(BrowserConfig(profile_dir=tmp_path / 'managed'),
                             backend=RejectingBackend())
    await manager.start()
    with pytest.raises(BrowserSessionError, match='browser rejected site-cookie import') as captured:
        await manager.install_site_cookies('chatgpt.com', [{
            'domain': '.chatgpt.com', 'name': 'session', 'value': 'secret'
        }])
    assert 'secret' not in str(captured.value)
    await manager.stop()


@pytest.mark.asyncio
async def test_cli_rejects_backend_cookie_error_without_traceback(monkeypatch, tmp_path, capsys):
    ff = make_firefox_profile(tmp_path)
    make_cookie_db(ff)
    import openagent.browser as browser
    monkeypatch.setattr(browser, 'discover_profiles', lambda **kw: [
        BrowserProfile('firefox', 'default-release', str(ff), 'system', 'site-cookie-import')
    ])
    class BadManager:
        def __init__(self, config): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
        async def install_site_cookies(self, site, cookies):
            raise BrowserSessionError('browser rejected site-cookie import')
    monkeypatch.setattr(browser, 'BrowserManager', BadManager)
    monkeypatch.setattr(cli, '_browser_settings', lambda args: BrowserConfig(profile_dir=tmp_path / 'managed'))
    assert await cli._import_firefox(Namespace(yes=True, profile=ff, site='chatgpt.com')) == 1
    output = capsys.readouterr().out
    assert 'Cookie import failed (BrowserSessionError)' in output
    assert 'private-chatgpt-cookie' not in output
    assert 'Traceback' not in output


def test_denies_nonapproved_sites_and_missing_databases(tmp_path):
    ff = make_firefox_profile(tmp_path)
    with pytest.raises(SessionImportError, match='unsupported'):
        firefox_site_cookies(ff, 'google.com')
    with pytest.raises(SessionImportError, match='cookies.sqlite'):
        firefox_site_cookies(ff, 'chatgpt.com')


@pytest.mark.asyncio
async def test_manager_rejects_cross_site_cookie_and_cdp(tmp_path):
    class FakeBackend:
        def __init__(self):
            self.received = None
        async def start(self, _config): pass
        async def stop(self): pass
        def is_connected(self): return True
        async def add_cookies(self, cookies): self.received = cookies

    backend = FakeBackend()
    manager = BrowserManager(BrowserConfig(profile_dir=tmp_path / 'managed'), backend=backend)
    await manager.start()
    with pytest.raises(Exception, match='outside approved'):
        await manager.install_site_cookies('chatgpt.com', [{'domain': '.google.com', 'name': 'x'}])
    assert backend.received is None
    assert await manager.install_site_cookies('chatgpt.com', [{'domain': '.chatgpt.com', 'name': 'test'}]) == 1
    assert backend.received[0]['name'] == 'test'
    await manager.stop()


@pytest.mark.asyncio
async def test_import_requires_yes_before_read(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr('openagent.browser.firefox_site_cookies', lambda *_: (_ for _ in ()).throw(AssertionError('must not read')))
    args = Namespace(yes=False, profile=tmp_path, site='chatgpt.com')
    assert await cli._import_firefox(args) == 2
    assert '--yes' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_explicit_import_into_fake_browser_no_cookie_values_logged(monkeypatch, tmp_path, capsys):
    ff = make_firefox_profile(tmp_path)
    make_cookie_db(ff)
    import openagent.browser as browser
    monkeypatch.setattr(browser, 'discover_profiles', lambda **kw: [BrowserProfile('firefox','default',str(ff),'system','site-cookie-import')])
    seen = {}
    class FakeManager:
        def __init__(self, config):
            seen['config'] = config
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
        async def install_site_cookies(self, site, cookies):
            seen['site'] = site
            seen['cookies'] = cookies
            return len(cookies)
    monkeypatch.setattr(browser, 'BrowserManager', FakeManager)
    monkeypatch.setattr(cli, '_browser_settings', lambda a: BrowserConfig(profile_dir=tmp_path / 'managed'))
    args = Namespace(yes=True, profile=ff, site='chatgpt.com')
    assert await cli._import_firefox(args) == 0
    printed = capsys.readouterr().out
    assert 'Imported 2' in printed
    assert 'private-chatgpt-cookie' not in printed
    assert 'another-private-cookie' not in printed
    assert seen['site'] == 'chatgpt.com'


def test_cli_profiles_json_parser(monkeypatch, capsys, tmp_path):
    import openagent.browser as browser
    monkeypatch.setattr(browser, 'discover_profiles', lambda: [BrowserProfile('firefox', 'default', str(tmp_path), 'system', 'site-cookie-import')])
    assert cli._profiles(Namespace(browser='firefox', json=True)) == 0
    assert 'site-cookie-import' in capsys.readouterr().out
