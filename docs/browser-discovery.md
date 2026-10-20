# Browser discovery, selection, and session persistence

## Commands

```powershell
.\.venv\Scripts\python.exe -m openagent.cli browser discover
.\.venv\Scripts\python.exe -m openagent.cli browser discover --json
.\.venv\Scripts\python.exe -m openagent.cli browser discover --search-dir "F:\Browsers\chrome-win64"
.\.venv\Scripts\python.exe -m openagent.cli browser select chromium --browser-path "F:\Browsers\chrome-win64"
.\.venv\Scripts\python.exe -m openagent.cli browser probe --headful
.\.venv\Scripts\python.exe -m openagent.cli browser open --url https://example.com
```

`--browser-path` accepts an executable (`chrome.exe`) or an extracted folder
containing it. The CLI stores the selected browser and path (not credentials)
in `~/.openagent/browser.json`. The downloaded browser need not be in
Playwright's cache when `executable_path` is explicit.

## Discovery versus compatibility

- Google Chrome and Microsoft Edge: system-installed versions or specified
  portable executables may work with Playwright's Chromium backend, but browser
  version mismatch may still cause incompatibilities.
- Playwright-managed Chromium: must match the version that the installed
  Playwright package expects; a random `chrome-win64` directory is not a
  recognized Playwright cache root.
- Firefox: regular Firefox is discoverable but **not** officially supported as
  the executable for Playwright automation. Install Playwright's Firefox build
  with `python -m playwright install firefox`. Firefox cannot attach over CDP.
- `PLAYWRIGHT_BROWSERS_PATH` must name the directory containing versioned
  folders like `chromium-1200`, not the `chrome-win64` child directory.

## Login reuse

A browser executable is not its authenticated profile. OpenAgent does **not**
read or clone the normal browser's existing personal profile. A separate
persistent profile is created per backend (to avoid profile database conflicts).

For an initial login, select a browser and call `browser open --url <HTTPS site>`.
Sign in normally; press Enter to close. Subsequent OpenAgent launches of the
same backend/profile retain whatever login the service/browser permits until
session expiration. This may require one initial login and periodic renewal.

Running a normal Firefox already logged in does not make that session available
to Playwright Firefox. A future, separately designed native Firefox/Gecko
backend may support other workflows, but is not part of this release.

## Limitations

Discovery does not prove a browser can launch, detect website authentication,
or validate a service's permission to automate it. Use `browser probe` to test
startup; real providers will need separate end-to-end validation. The local
HTTP bridge is still mock-only.
