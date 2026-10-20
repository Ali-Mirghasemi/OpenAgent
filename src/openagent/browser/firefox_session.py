"""Explicit site-scoped cookie migration from the user's own Firefox profile.

Cookie values are never logged, printed, exported to JSON, or saved to a separate
intermediate file. Migration is limited to a small, declared set of site domains.
This is *not* a password or Google SSO importer, nor a login-success guarantee.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
import math
import time
from pathlib import Path

ALLOWED_SITES = frozenset({'chatgpt.com'})
_MAX_COOKIES = 128
_MAX_VALUE = 16384
# Playwright/Chromium cookie maximum: 9999-12-31 23:59:59 UTC.
# Firefox may represent extremely distant expiry with an int64 sentinel,
# which Playwright rejects instead of accepting as a persistent cookie.
_MAX_PLAYWRIGHT_EXPIRY = 253402300799


def _portable_expiry(expiry: object, now: float) -> int | None:
    """Return a valid, still-future Playwright epoch or skip an invalid cookie.

    Preserve persistent-cookie behavior for Firefox's far-future expiry
    sentinels by capping to Playwright's supported upper bound. Never turn an
    expired cookie into an active one, and never silently convert one to a
    session cookie. This does not claim the destination site will accept it.
    """
    if isinstance(expiry, bool) or not isinstance(expiry, (int, float)):
        return None
    if not math.isfinite(expiry) or expiry <= now:
        return None
    return min(int(expiry), _MAX_PLAYWRIGHT_EXPIRY)


class SessionImportError(ValueError):
    pass


def firefox_site_cookies(profile: Path, site: str) -> list[dict]:
    if site not in ALLOWED_SITES:
        raise SessionImportError('unsupported cookie import site')
    profile = profile.expanduser().resolve()
    db = profile / 'cookies.sqlite'
    if not profile.is_dir() or not db.is_file() or db.is_symlink():
        raise SessionImportError('selected Firefox profile has no readable cookies.sqlite')
    # SQLite read-only; do not alter Firefox's live database or WAL files.
    uri = db.as_uri() + '?mode=ro'
    output = []
    now = time.time()
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as conn:
            conn.execute('PRAGMA query_only = ON')
            cursor = conn.execute(
                'SELECT name,value,host,path,expiry,isSecure,isHttpOnly,sameSite,originAttributes '
                'FROM moz_cookies WHERE host = ? OR host = ?', (site, '.' + site)
            )
            for row in cursor:
                name, value, host, path, expiry, secure, http_only, same_site, attrs = row
                # Firefox container/partitioned cookies are not portable across browser contexts.
                if attrs or not name or not isinstance(value, str) or len(value) > _MAX_VALUE:
                    continue
                portable_expiry = _portable_expiry(expiry, now)
                if portable_expiry is None:
                    continue
                cookie = {
                    'name': name, 'value': value, 'domain': host,
                    'path': path if isinstance(path, str) and path.startswith('/') else '/',
                    'expires': portable_expiry, 'httpOnly': bool(http_only), 'secure': bool(secure),
                }
                if same_site in (0, 1, 2):
                    cookie['sameSite'] = {0: 'None', 1: 'Lax', 2: 'Strict'}[same_site]
                if cookie.get('sameSite') == 'None' and not cookie['secure']:
                    # Chromium rejects SameSite=None for insecure cookies.
                    continue
                output.append(cookie)
                if len(output) > _MAX_COOKIES:
                    raise SessionImportError('too many site cookies; import was not attempted')
    except (sqlite3.Error, OSError) as exc:
        raise SessionImportError('could not read Firefox cookie database (close Firefox and retry)') from exc
    return output
