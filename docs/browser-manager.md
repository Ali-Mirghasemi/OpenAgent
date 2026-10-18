# Browser Manager v0.2 — transport contract

This module owns browser connections and OpenAgent-created pages only. It is **not**
a ChatGPT adapter, an authentication extractor, or an autonomous agent.

## Lifecycle

`stopped → starting → running → stopped`

- Startup failure sets `failed`. The manager stops partially opened resources.
- A lost browser connection sets `disconnected`. Requests are not retried.
- `start()` after an observed disconnect performs an explicit transport restart,
  but cannot infer whether earlier prompts were completed remotely.
- `stop()` closes OpenAgent-owned tabs and the dedicated persistent context.
  In CDP mode, the connection is stopped while pre-existing Chrome tabs and its
  context remain owned by the user.

## Browser modes

`persistent`: `chromium.launch_persistent_context()` with a dedicated profile,
controlled by OpenAgent. Headless by default. User can explicitly run a visible
browser with `--headful`. The normal Chrome/Edge User Data location is not a
supported automation profile.

`cdp`: `chromium.connect_over_cdp()` to a **local** HTTP or WS endpoint. Uses
an existing browser context and creates its own tabs. It never closes the
attached browser context directly.

## Request tabs

Each request has an explicit unique `tab_id`. The manager:

1. Rejects an existing `tab_id` to prevent accidental duplicate submissions.
2. Checks origin restrictions and navigation scheme *before* opening a tab.
3. Enforces `max_tabs` against currently live managed tabs.
4. Navigates with a finite timeout; closes the page on navigation failure.
5. Returns the page to the provider adapter; it does not fill UI or parse output.
6. Closes only managed pages on shutdown.

A future provider owns response completion detection, UI selector handling,
model selection, and any permitted file uploads. The bridge owns request IDs,
capabilities and protocol translation; the coding agent owns commands.

## Security and reliability

- No login/cookie extraction or provider-private HTTP requests are implemented.
- Playwright is an optional dependency, imported only when starting a browser.
- The CDP endpoint may only target localhost. Never expose remote debugging
  or the bridge API to an untrusted network.
- Error messages are intentionally sanitized at the manager boundary. Runtime
  exception causes can contain URLs or credentials and must not be logged in
  customer-facing output without redaction.
- A failed navigation is distinct from a prompt that was submitted but whose
  reply was lost. Adapters must maintain this distinction before any retry.
- The current implementation does not yet coordinate cancellation with the
  SQLite session journal, or provide an operation that guarantees remote model
  execution has been stopped.

## Test strategy

`tests/test_browser.py`: no actual browser; deterministic fakes test startup,
explicit tab management, URL policy, disconnect, restart, failure cleanup.

`tests/test_playwright_backend.py`: monkeypatched Playwright confirms resource
ownership, especially CDP not closing the existing context.

Manual Playwright `browser probe`: optional end-to-end browser startup and
shutdown. A successful probe does not establish compatibility with ChatGPT or
any other provider. Provider-specific tests belong in the next milestone.
