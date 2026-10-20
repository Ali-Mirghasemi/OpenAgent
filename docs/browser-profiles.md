# Browser profiles and session reuse (v0.4.4)

OpenAgent supports two separate concepts:

1. **Browser installations:** available executables (`browser discover`).
2. **Saved browser profiles:** user profile directories (`browser profiles`).

A listed Firefox profile cannot be launched as-is by Playwright. Stock Firefox
is not a supported Playwright engine; use its patched Firefox or Chromium with
a separate OpenAgent persistent profile instead.

`browser profiles [--browser firefox|chrome|msedge|chromium] [--json]` reports
profile name, path, source, and supported reuse strategy. It reads Firefox's
`profiles.ini` but **no** website cookies or passwords. Chrome/Edge default
profiles are discovery-only; avoid using them as `--profile-dir`.

For a Firefox profile already signed into ChatGPT, use the explicit command:

`openagent browser session import-firefox --profile PROFILE_PATH --site chatgpt.com --yes`

- Source must appear in `browser profiles --browser firefox`.
- Destination is the *selected* OpenAgent persistent browser profile.
- The importer reads only host `chatgpt.com` or `.chatgpt.com` records.
- Expired cookies and Firefox container/partitioned cookies are ignored.
- Session/password cookies are treated as secrets; no values leave the machine
  except storage in the user's destination browser profile. No cookie values
  are printed or included in debug logs.
- The operation does not contact ChatGPT, Google, or any remote server.
- Google cookies and Firefox saved passwords are never read.

Cookie migration often fails to authenticate due to browser-bound state,
other storage, cookie partitioning or anti-automation checks. Verify with
`browser chatgpt-status --headful --details`. Do not use this feature to
bypass a provider's sign-in restrictions. Use an official OAuth-supported
integration as a fallback.

**Windows:** The source profile may be locked during use. If an import errors,
close Firefox normally and retry. Do not copy or edit live profile databases.
A short shutdown drain attempts to prevent Python 3.10 Proactor pipe warnings.

## Firefox expiry compatibility (v0.4.5)

Some Firefox databases contain cookie `expiry` values beyond what Playwright
accepts (e.g. the SQLite signed-64-bit maximum). OpenAgent now caps those
future expiry values at `253402300799` (9999-12-31 23:59:59 UTC) before
calling `BrowserContext.add_cookies`. Expired cookies are never extended.

A backend rejection returns a brief sanitized `Cookie import failed` message
instead of the full Playwright stack trace. Authentication still requires a
separate status check; an imported cookie does not guarantee a usable session.

If a profile is named `xaghgvln.default-release`, use precisely that path. A
shortened `.default` path is different and won't resolve to the selected profile.
