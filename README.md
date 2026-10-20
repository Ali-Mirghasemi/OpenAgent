# OpenAgent Bridge — v0.4.1

**Early Python foundation for a provider-independent AI bridge.** The v0.4.x
release includes an opt-in, experimental ChatGPT public-UI adapter, verified
with local fixtures but **not with a live ChatGPT account**. The API serves
`mock/echo` by default; `chatgpt-web/default` appears only if explicitly
enabled with `--enable-chatgpt-web` and a bearer token. No automated login,
model control, native tools, or coding-agent command execution.

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
