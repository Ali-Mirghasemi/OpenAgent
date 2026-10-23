# OpenAgent Bridge v0.4.13 — Projects “Show more” button recognition

Windows v0.4.12 passed 170 tests but `chatgpt-projects --load-all`
discovered the same five Projects after four scrolls and **zero** expansion
clicks. The old scanner rejected the generic “Show more” control if the
Projects heading and rows were not inside a specially labeled `section`.

v0.4.13 recognizes a generic Show more control when it is grouped in the DOM
with visible Project rows and a Projects heading, including plain `<div>` / `<span>`
layouts. It still refuses to click unrelated chat-list controls, controls
inside a Project conversation, or ambiguous multiple matching controls.
The passive command stays completely read-only. An opt-in `--load-all` is
required for scrolling or expanding anything.

```powershell
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-projects --headful --load-all --wait-seconds 12 --max-steps 30 --json
```

New `more_controls_seen` reports how many candidate “Show more” buttons were
present, **before safety checks**. If the scan still reports zero expansions,
this count can distinguish a missing button from one OpenAgent intentionally
refused to click. It does not reveal page text outside the allowed label or
private browsing data. `complete: false` remains intentional: sidebar
traversal cannot prove account-wide completeness.

See [Projects pagination and limits](docs/chatgpt-projects-pagination.md).

---

# OpenAgent Bridge v0.4.12 — More complete Projects UI inventory

Windows v0.4.11 passed **165 tests** and found five Projects in the visible
ChatGPT sidebar, but the user reports additional Projects. The basic command
still reads only currently rendered rows. **New opt-in `--load-all`** performs
bounded scrolling and uses only unmistakable Projects-list expansion controls
in the sidebar. It accumulates Project IDs even if the UI virtualizes older rows.

```powershell
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-projects --headful --load-all --wait-seconds 12
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-projects --headful --load-all --max-steps 30 --json
```

The JSON report deliberately returns `complete: false`: these UI actions
**cannot prove an account-wide total**, and an unmounted Projects picker,
search results, other workspace, or collapsed section may hold more Projects.
No chat or Project is selected, no prompts are sent, and no internal API calls
are made. `--load-all` *does* scroll the sidebar and click specifically labeled
Projects-list expansion buttons; omit it for a fully passive inspection.

See [Projects pagination and limits](docs/chatgpt-projects-pagination.md).

---

# OpenAgent Bridge v0.4.11 — Project sidebar row discovery

Windows v0.4.10 was validated with **161 passing tests** and
`browser chatgpt-compose-check` returned `send_ready` without submission.
However, `chatgpt-projects` found 0 project links. Newer ChatGPT layouts may
render Projects as expandable sidebar rows rather than anchors; this release
recognizes visible rows with a validated `data-sidebar-project-container-id`
and adds a bounded wait for the sidebar to render. It **does not click a
Project, prove a Project route works, or submit a prompt**.

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,browser]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-projects --headful --wait-seconds 12
```

Project names/IDs are private metadata; redact them if sharing output.
A derived `/g/<id>/project` URL is a **candidate only**, not an established
Project-selection or continuation route. See
[Project sidebar inspection](docs/chatgpt-project-rows.md).

---

# OpenAgent Bridge v0.4.10 — Send preflight & Projects inventory

Windows v0.4.9 passed 149 tests and reported a typed ChatGPT draft but HTTP 503
`submit_unavailable` before any Send click. v0.4.10 expands composer-scoped Send
recognition, adds an explicit **draft-only** diagnostic (`browser chatgpt-compose-check`)
and a **read-only** Projects link inventory (`browser chatgpt-projects`). It does
not automatically choose a Project or submit live messages during tests.

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,browser]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-compose-check --headful
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-projects --headful
```

Both commands use a dedicated tab that is closed afterward; do not run a second
OpenAgent process with the same persistent profile. Project names/links are
private account metadata—inspect before sharing. No actual account-backed
message submission was performed by the release tests. See
[composer and Projects inspection](docs/chatgpt-composer-and-projects.md).

---

# OpenAgent Bridge v0.4.9 — Experimental conversation routing

Windows v0.4.8 was validated with 126 passing tests and a successful local
`mock/echo` session API run. This release adds a **separately opt-in**,
fixture-tested ChatGPT continuation protocol using a verified `/c/<uuid>`
conversation route and the existing SQLite request journal. It is **disabled
by default** and has not been tested by submitting a message to live ChatGPT.
The previous `--enable-chatgpt-web` flag alone keeps one-shot behavior.

For Windows installation from v0.4.8:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,browser]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-status --headful --details
```

See [experimental conversation routing](docs/conversation-routing-preview.md)
for operator controls, route-identity safeguards, limitations and local tests.
Do not enable account-backed automation where the service does not permit it.

---

# OpenAgent Bridge v0.4.8 — Session continuation foundation

This update adds **durable upstream conversation identity** as an optional,
provider-issued field on the existing SQLite local session. Only providers
which explicitly implement conversation continuation can bind or resume that
identity. A previously bound session cannot silently fall back to a new chat.

The new read-only endpoint `GET /openagent/sessions/{id}/turns` displays the
outcome ledger. Pending/unknown requests remain blocked rather than retried.
Existing v0.4.7 SQLite databases migrate in place, preserving messages and
request IDs. The feature is covered by offline fixture-provider tests.

**ChatGPT-specific resume is not implemented or verified.** The existing
browser UI adapter remains one-shot and account-backed use is still
experimental. No real chat messages were sent as part of this release.

On Windows, apply the v0.4.7 → v0.4.8 patch, install and run tests:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,browser]"
.\.venv\Scripts\python.exe -m pytest -q
```

See [Session continuation design](docs/session-continuation.md) for a mock-only
PowerShell API test, persistence guarantees, and known limitations.

---

## v0.4.7: DOM turn-identity reliability

This update improves ChatGPT-shaped browser fixture handling when a site
virtualizes history. The adapter now snapshots stable turn IDs before a
submission, so it can distinguish a new answer even when the number of
mounted messages stays the same. If the site replaces messages without
exposing stable IDs, OpenAgent fails closed instead of treating old Copy
buttons as proof of a completed response.

Your v0.4.6 Windows check already confirmed that the imported-session page
reports `ready` with the evidence `composer_visible_send_inactive`. **This is
readiness evidence only**, not verification of production prompt submission.

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,browser]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-status --headful --details
```

The optional new Chromium tests use local HTML fixtures and a temporary
profile; they never access your signed-in session or a real provider service.
See [turn identity](docs/turn-identity.md) for guarantees and limitations.

# OpenAgent Bridge — v0.4.5

**Early Python foundation for a provider-independent AI bridge.** The v0.4.x
release includes an opt-in, experimental ChatGPT public-UI adapter, verified
with local fixtures but **not with a live ChatGPT account**. The API serves
`mock/echo` by default; `chatgpt-web/default` appears only if explicitly
enabled with `--enable-chatgpt-web` and a bearer token. No automated login,
model control, native tools, or coding-agent command execution.

## Fix in v0.4.5 — Firefox cookie expiry compatibility

Firefox cookie databases may contain unusually distant expiry values, including
an int64 maximum sentinel. Playwright rejects expiry timestamps beyond
`253402300799` (end of year 9999). OpenAgent now caps only those too-large
*future* expiries to Playwright's supported limit. Expired, invalid and
non-finite expiry values are still discarded, without being made active.

The importer now handles Playwright cookie errors without a traceback or
revealing cookie data. **Importing cookies is not equivalent to signing in**;
check the browser's status afterward. Use the exact Firefox profile path shown
by `browser profiles --browser firefox`. Never paste cookies, secrets, or a
raw Firefox cookie database into the chat.

## New in v0.4.4 — Discover existing profiles and optional Firefox session reuse

The browser discovery command lists *executables*, while the new profile command
lists the saved **profile locations** used by Firefox, Chrome, and Edge:

```powershell
.\.venv\Scripts\python.exe -m openagent.cli browser profiles
.\.venv\Scripts\python.exe -m openagent.cli browser profiles --browser firefox
```

Profile discovery is read-only: it does not inspect stored cookies, history or
saved passwords. Chrome/Edge system profile folders are **discovery-only**:
Playwright does not reliably control an existing everyday browser profile, and
these browsers may protect cookies with OS/application-bound encryption.

**Opt-in Firefox site-cookie import** (requires Firefox already signed into
ChatGPT, which is different from merely being signed into Google in Firefox):

```powershell
# 1. First choose the OpenAgent browser where the copied site cookies go:
.\.venv\Scripts\python.exe -m openagent.cli browser select chromium --browser-path "F:\Browsers\chrome-win64"

# 2. List Firefox profiles (copy one actual Path value from the result):
.\.venv\Scripts\python.exe -m openagent.cli browser profiles --browser firefox

# 3. Explicitly approve local transfer of ChatGPT-domain cookies only:
.\.venv\Scripts\python.exe -m openagent.cli browser session import-firefox `
    --profile "C:\Users\YOU\AppData\Roaming\Mozilla\Firefox\Profiles\YOUR.default-release" `
    --site chatgpt.com --yes

# 4. Check whether the resulting browser is ACTUALLY logged in:
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-status --headful --details
```

The command reads only the selected Firefox profile's `cookies.sqlite` and
passes the applicable unexpired, unpartitioned `chatgpt.com` cookies to the
**separate** OpenAgent persistent browser profile. It does not read Firefox
passwords, copy Google cookies, print cookie values, export cookies, modify the
source profile, or make requests to ChatGPT during import. The destination
browser profile itself contains sensitive session information and must be
protected and excluded from Git.

**Limitations:** Importing cookies is not proof of authentication. Login might
still require other browser storage, security binding, fresh credentials, or
provider permission. The import does not work around Google OAuth browser
restrictions. If an import fails, sign in through a supported normal browser
flow or use an authorized ChatGPT OAuth/Codex integration.

With Python 3.10 on Windows, Playwright shutdown after Ctrl+C can emit late
subprocess transport warnings. This release allows a short drain period after
Playwright stops; Windows runtime verification is still required.

## New in v0.4.3 — Recognize a logged-out ChatGPT homepage

A Windows screenshot showed a fully rendered ChatGPT homepage with visible
**Log in** buttons and an anonymous message composer. In v0.4.2 this was
misclassified as `ui_changed` because we only checked older login selectors.
The new selector candidates recognize the login state before claiming readiness.

```powershell
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-status --headful --details
```

Expected on that same logged-out layout: `state: login_required` and
`controls.login_required: visible`. This does **not** log in or copy browser
credentials. Authentication must occur through a provider-supported flow in
OpenAgent's own persistent browser profile. A 200 response from `/v1/models`
only confirms the bridge is reachable and authenticated, not that the ChatGPT
account is logged in.

## New in v0.4.2 — diagnosing an empty ChatGPT UI

Your v0.4.1 Windows tests and API authorization succeeded; the remaining
`ui_changed` result means OpenAgent did not see its expected controls.
This does **not** prove the account is logged in or that selectors changed.

```powershell
# Browser must not already be open with the same OpenAgent profile.
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-status --headful --details --wait-seconds 12
```

`--details` shows only safe counts/presence: document readiness, whether
`main` and `body` exist, visible editor/button counts and iframe count, plus
configured control visibility. It doesn't read or print page text, DOM HTML,
full URLs, tokens, browser storage or credentials.

Only if needed, request a local screenshot (never uploaded automatically):

```powershell
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-status --headful --details --screenshot "$env:USERPROFILE\openagent-page.png"
```

Screenshots can contain **private account and chat content**. Review/redact
the PNG yourself before sharing. Saving refuses to overwrite an existing file.

## What is implemented

- Provider registry, per-model capability declarations, and preserved ordered
  system/developer/user/assistant/tool messages.
- OpenAI-compatible subset: `GET /v1/models`, `POST /v1/chat/completions`,
  with synthetic SSE output for the mock model.
- SQLite local conversation history with durable request IDs, cached completed
  turns, and safe unknown-outcome handling (not automatic resubmission).
- OAP/1 compact JSON tool-envelope validation; **the bridge never executes tools**.
- **Browser Manager:** attach to local CDP or launch a dedicated Playwright
  persistent profile (Chromium, installed Chrome/Edge, or Playwright Firefox); own only newly created request tabs, enforce
  per-provider origin restrictions, cap open tabs, and expose typed errors.
- Browser discovery, explicit `--browser-path`, persistent backend selection, and one-time interactive sign-in in an OpenAgent-owned browser profile.
- Browser disconnection detection, explicit restart, and deterministic fixture
  tests for lifecycle and failures.
- **v0.3 DOM adapter:** configured selectors, read-only status diagnostics,
  ordered JSON message envelopes, explicit completion evidence, one submission
  per request, cancellation cleanup, and safe uncertain-outcome errors.
- A local, account-free HTML chat fixture and `browser diagnose`/`browser ui-test`
  commands. These do not connect to ChatGPT or other external AI providers.
- Windows-friendly port behavior: default `12321`, configurable with
  `OPENAGENT_PORT` or `--port`; if unavailable, try the following 30 ports.

Python **3.10+**. Browser support requires an optional Playwright installation.

## Windows PowerShell — install and run

Open PowerShell in the directory containing `pyproject.toml`.

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev,browser]"
# Only if you do not already have a usable browser:
# .\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m openagent.cli serve
```

The API is available at <http://127.0.0.1:12321/docs>. `GET /` returning
404 is expected: no homepage is implemented.

**If you only want to test the API**, omit the `[browser]` extra and the
`playwright install chromium` step, and install `".[dev]"` instead.

The installed virtual environment can be used without activation. If you wish
to activate it within PowerShell, use `.\.venv\Scripts\Activate.ps1`, **not**
`source` or `activate.bat`. In Command Prompt use `.venv\Scripts\activate.bat`.

## Discover and select browsers (updated in v0.2.2)

Use your existing downloaded Chromium build — **no new browser download**:

```powershell
# Your earlier environment variable pointed at chrome-win64, not a Playwright cache root.
Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH -ErrorAction SilentlyContinue

# Find Chrome, Edge, Firefox, and installed Playwright browser builds.
.\.venv\Scripts\python.exe -m openagent.cli browser discover

# Inspect an arbitrary portable browser extraction directory.
.\.venv\Scripts\python.exe -m openagent.cli browser discover --search-dir "F:\Browsers\chrome-win64"

# Set the browser default for future probes and provider adapters.
.\.venv\Scripts\python.exe -m openagent.cli browser select chromium --browser-path "F:\Browsers\chrome-win64"

# Test it without navigating to a website.
.\.venv\Scripts\python.exe -m openagent.cli browser probe --headful

# Open a site ONCE in the dedicated OpenAgent profile to sign in manually.
.\.venv\Scripts\python.exe -m openagent.cli browser open --url https://example.com
```

You can override a saved preference for one probe:

```powershell
.\.venv\Scripts\python.exe -m openagent.cli browser probe --browser chrome --headful
.\.venv\Scripts\python.exe -m openagent.cli browser probe --browser msedge --headful
.\.venv\Scripts\python.exe -m openagent.cli browser probe --browser chromium --browser-path "F:\Browsers\chrome-win64" --headful
```

Firefox support requires the **Playwright-patched Firefox build**; normal desktop
Firefox can be discovered but is not a drop-in Playwright executable:

```powershell
.\.venv\Scripts\python.exe -m playwright install firefox
.\.venv\Scripts\python.exe -m openagent.cli browser select firefox
.\.venv\Scripts\python.exe -m openagent.cli browser probe --headful
```

Browser selection does NOT automatically reuse a login from Chrome/Firefox's
normal personal profile. OpenAgent uses a dedicated **persistent profile per
browser type**; sign in once through `browser open` and that browser's session
may be available to later OpenAgent runs. Logins can expire, and provider
permission requirements still apply. The selected backend is persisted under
`~/.openagent/browser.json` (non-secret browser type/path only).

`browser open` uses the visible browser UI and waits for you to press Enter
before closing and retaining its profile. Press Ctrl+C **once** to request clean shutdown
(exit code 130). Closing the window by itself does not finalize the CLI.
On Windows the CLI polls console input asynchronously rather than leaving a
background `input()` thread blocked during shutdown. It does not automate login, copy
cookies, or access other browser profiles. The Browser Manager does not start
when serving the current mock provider.

### Google sign-in and interrupted downloads

Google can deny account sign-in from a browser it identifies as being controlled
by automation, even if that browser is a supported Firefox or Chrome build.
OpenAgent does **not** bypass those restrictions, change automation fingerprints,
or copy cookies from personal profiles. Sign in using a normal supported browser
when Google refuses; for integrations requiring Google authentication, use a
supported browser-based OAuth integration when available. An OpenAgent persistent
profile retains a session only when the provider permits sign-in.

A `403 AccessDenied` with "service is not available in your location" during
`python -m playwright install chromium` is a CDN access restriction, not a Python
installation failure. Your manually downloaded Chromium binary can still be
used via `browser select chromium --browser-path ...`; you do not need the
Playwright-managed Chromium download when that probe succeeds.

For CDP, use a locally available Chrome/Edge debug endpoint:

```powershell
.\.venv\Scripts\python.exe -m openagent.cli browser probe --mode cdp --cdp-url http://127.0.0.1:9222
```

See [docs/browser-discovery.md](docs/browser-discovery.md) for installation
paths, Firefox limits, and when a browser can reuse its existing login state.

### Python interface for future adapters

```python
from pathlib import Path
from openagent.browser import BrowserConfig, BrowserManager

config = BrowserConfig(
    mode="persistent",
    profile_dir=Path.home() / ".openagent" / "browser-profile",
    allowed_origins=("https://example.com",),
)

async with BrowserManager(config) as browser:
    page = await browser.open_tab("request-123", "https://example.com/")
    # A future authorized provider adapter interacts with page.
    await browser.close_tab("request-123")
```

`open_tab()` does **not** submit prompts. A failed or disconnected request is
not retried automatically, because its remote completion state may be unknown.

## v0.4 — experimental ChatGPT public-UI adapter (opt-in)

Version 0.4 adds a **ChatGPT-shaped browser adapter** and a provider-specific
completion detector. This is an **unverified preview** of live website support,
not a claim of working ChatGPT automation or permitted account use.
`chatgpt-web/default` refers to the model **currently selected in the website
UI**. There is no model/plan/effort discovery or selection yet, and no native
tools, file transfers, provider-side conversation continuation or true streaming.

Read-only UI check (uses the previously selected OpenAgent browser/profile):

```powershell
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-status --headful
```

The result reports observable `ready`, `login_required`, `security_challenge`,
`ui_changed` etc. UI readiness is not account-authentication proof.

For `ui_changed`, use read-only visibility diagnostics:

```powershell
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-status --headful --details
```

The output identifies which configured composer, send, login, and challenge
selectors are visible; it does **not** retrieve page text, credentials or
cookies. `ui_changed` alone cannot distinguish an unsigned-in session from a
website layout change. See `docs/windows-auth-troubleshooting.md`.

To explicitly enable this experimental backend over loopback HTTP, **set a
strong local token** in `OPENAGENT_API_TOKEN` and start:

```powershell
# Supply your own strong, local-only token for this session first
$env:OPENAGENT_API_TOKEN = Read-Host "Local OpenAgent API token"
.\.venv\Scripts\python.exe -m openagent.cli serve --enable-chatgpt-web --headful
```

**Use two separate PowerShell terminals.** Environment variables and `$headers`
are not shared between them. Set the same `OPENAGENT_API_TOKEN` in both and
rebuild `$headers` after assigning the token. With the server still running,
you can check the connection from Terminal 2 without constructing HTTP headers:

```powershell
$env:OPENAGENT_API_TOKEN = Read-Host "OpenAgent API token"
.\.venv\Scripts\python.exe -m openagent.cli api-check
```

`api-check` first checks unauthenticated `/health`, then authorized `/v1/models`;
`401` is reported as a token mismatch and a connection refusal as a stopped
server or wrong port. No token values are printed or stored.

Visit `/v1/models` with `Authorization: Bearer <your token>` to see
`chatgpt-web/default`; `/openagent/capabilities/chatgpt-web/default` shows
unknown/unsupported features explicitly. Read-only status is at
`/openagent/providers/chatgpt-web/diagnostics`. The existing `mock/echo`
remains available. Non-streaming completion is experimental and must only be
used with a provider/account where browser automation is permitted.

**Never use the normal Chrome profile.** If a site blocks a browser-controlled
login, use its officially supported authentication interface rather than
trying to bypass security controls. A failed or ambiguous submission is never
silently replayed.

See [docs/chatgpt-preview.md](docs/chatgpt-preview.md). A local, deterministic
ChatGPT-shaped HTML fixture is also tested in a real Chromium browser; no live
commercial-service interaction is part of the tests.

## v0.3 — test the browser UI adapter locally

This exercises the browser UI adapter on a **local test webpage**, not a paid AI
service or a live provider. You do not need another Playwright browser download;
your selected `F:\Browsers\chrome-win64` remains the default.

In **PowerShell terminal A**, serve the fixture from the OpenAgent repository:

```powershell
.\.venv\Scripts\python.exe -m http.server 8999 --bind 127.0.0.1 --directory examples
```

In **PowerShell terminal B**:

```powershell
# Read-only status inspection: {"state":"ready", ...}
.\.venv\Scripts\python.exe -m openagent.cli browser diagnose --url http://127.0.0.1:8999/chat_fixture.html

# Send one structured prompt to the local mock page, then read its finished answer
.\.venv\Scripts\python.exe -m openagent.cli browser ui-test --message "Hello from Windows"

# Optional: watch the interaction in your selected visible Chromium browser
.\.venv\Scripts\python.exe -m openagent.cli browser ui-test --headful --message "test UI"
```

Expected final line looks like:
`Fixture received 1 message(s); last user: Hello from Windows`.
Press Ctrl+C in terminal A to stop the fixture server.

`browser diagnose` supports `--composer`, `--send-button`, `--assistant-message`,
`--login-selector`, `--challenge-selector`, `--rate-selector` and
`--busy-selector` to inspect a separately reviewed site's UI. The status is
*only observed visual evidence*, not confirmation of account authentication.
`browser ui-test` refuses non-loopback URLs to prevent accidental prompt
submission to a public website. The external provider capability list remains
unknown until a real, permitted adapter and a live compatibility test exist.

See [docs/browser-ui-adapter.md](docs/browser-ui-adapter.md) for the state
machine, limitations and integration checklist.

## Port configuration

```powershell
# Explicit preferred port; fallback to subsequent available permitted ports
.\.venv\Scripts\python.exe -m openagent.cli serve --port 8765

# Use precisely this port (fail if Windows reserves it)
.\.venv\Scripts\python.exe -m openagent.cli serve --port 12321 --strict-port

# Let the operating system allocate a free port
.\.venv\Scripts\python.exe -m openagent.cli serve --port 0

# Environment variable instead of a command line argument
$env:OPENAGENT_PORT = "12321"
```

Port selection is a preflight check rather than an atomic reservation;
rare last-moment conflicts may still cause Uvicorn to report a bind error.

## API smoke test

```powershell
Invoke-RestMethod http://127.0.0.1:12321/health
Invoke-RestMethod http://127.0.0.1:12321/v1/models
$payload = @{model="mock/echo";messages=@(@{role="user";content="Hi"})} | ConvertTo-Json -Depth 6
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:12321/v1/chat/completions -ContentType "application/json" -Body $payload
```

These examples assume the server selected port `12321`. If the startup log
reports a different port, use that address instead.

## Local session API

- `POST /openagent/sessions` with `{"model":"mock/echo"}` creates a local
  conversation.
- `GET /openagent/sessions/{session_id}` retrieves it.
- `POST /openagent/sessions/{session_id}/turns` with a stable `request_id`
  and a `message` appends one turn.
- A repeated successful ID returns a cached answer. A pending or ambiguous
  failed turn responds with HTTP 409 rather than resubmitting blindly.

This is local OpenAgent session history, not a ChatGPT-native Project or chat.

## Security boundaries

- API binds to `127.0.0.1` by default; non-loopback binding requires
  `OPENAGENT_API_TOKEN`. Set this token for local use too when other local
  processes/users are untrusted.
- CDP attachments are restricted to loopback. Existing browser tabs are never
  closed by the Browser Manager; only tabs created by OpenAgent are owned.
- Browser navigation requires HTTPS or loopback HTTP. Provider adapters should
  pass an explicit `allowed_origins` tuple.
- Browser profile directories, cookies, login data, SQLite sessions, logs, and
  artifacts must never be committed. **Do not expose CDP to the network.**
- Model-produced tool calls are untrusted; the bridge never executes them.
- Terms and capabilities must be evaluated independently for each website.

## Project development files

- `TODO` — tested features, next milestones, outstanding tasks.
- `PROFILE` — architectural contract and engineering conventions.
- `CHANGES` — release history.
- `.gitignore` — prevent committing local profiles, sessions, and secrets.

See [docs/architecture.md](docs/architecture.md) for boundaries and
[docs/browser-manager.md](docs/browser-manager.md) for transport semantics.


### Authenticated idle composer (v0.4.6)
After the Firefox ChatGPT-site cookie import, a browser page may be signed in
while its Send button is disabled or hidden until the message field has text.
`browser chatgpt-status --headful --details` may now return
`ready` / `composer_visible_send_inactive`. This is **UI readiness only**, not
proof of authentication or authorization to automate the website.

The bridge's experimental ChatGPT provider will refuse to click Send unless an
enabled and visible Send control appears *after* text entry. No message is
submitted by `chatgpt-status`, and `submit_unavailable` means nothing was sent.
The provider's live UI compatibility and service permissions remain unverified.
