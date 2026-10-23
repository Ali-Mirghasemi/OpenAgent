# ChatGPT access errors and page capabilities (v0.4.22)

This is an **experimental** public-UI adapter, never a supported replacement
for the provider's authorized API. Provider permissions still apply.

## Read-only access-error classification

An unexpected Project chat route is not automatically a stale local ID.
The app may render a page-level error, for example "You cannot access this
chat" or "This chat is not available". The detection code checks only
visible headings/alerts in `main` or short `main` error pages **when no chat
turns are rendered**. It never treats a phrase quoted in an existing message
as a page error and never returns page text, HTML or tokens.

When a route changes, a bounded extra check allows SPA error text to render
before OpenAgent returns a generic route mismatch. These codes are
informational only:

- `conversation_access_denied`: the website says this chat is inaccessible;
- `conversation_unavailable`: the website says the chat is unavailable;
- `reconcile_route_mismatch`: route did not match and no known page-level
  error was confirmed.

There is **no automatic retry or new-chat fallback**, and no attempt to
bypass authentication or account-level access controls. The current Project
reply repair remains blocked until the real answer can be unambiguously read.

## Control presence, not implemented functionality

`browser chatgpt-controls --headful` reports only coarse DOM control presence.
A detected model picker, effort picker, file input, attachment button, tools
menu or voice button is not proof that the account can use that feature.
No controls are clicked and no file paths, chat text or credentials are returned.

Next work: store and verify exact upstream slugged route; select model/effort
from verified UI labels with explicit user choice; opt-in attachments with
strict file validation and success markers; Project resource inventory and
upload/download lifecycle; OpenCode/9Router API capability negotiation.

## v0.4.23: Wait for hydrated controls

v0.4.22 ran the control inventory immediately after `open_tab` had navigated
through `DOMContentLoaded`; this was not a readiness wait. ChatGPT is a
single-page app, and its composer and menu buttons can appear later. Use:

```powershell
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-controls --headful --wait-seconds 15
```

The new read-only scanner polls for a visible composer, login marker, challenge,
or rate-limit state (up to 30 seconds), then polls the optional feature-control
candidates while they settle. If the composer appears but no optional controls
are detected, it waits out the remaining budget before reporting their absence.
Output adds the observed UI evidence, elapsed wait, expected-domain check and
content-free DOM shape (`ready_state`, body/main presence, counts of visible
buttons/editable elements). No ChatGPT message, account text, cookie, file path,
raw HTML, title or private URL is returned. Do **not** confuse
`document.readyState=complete` with a hydrated, usable chat composer.

If `page_state` remains `ui_changed`, use the existing
`browser chatgpt-status --details --wait-seconds 15` for additional safe
control-shape diagnostics. A 15- or 30-second wait does not guarantee
successful login, functionality, complete rendering or a supported model/effort.
All controls remain detection-only: there are no menu clicks or uploads.

## v0.4.24: Cloudflare human-verification page

A real managed Chromium screenshot showed a Cloudflare **Verify you are human**
challenge at `chatgpt.com`. The v0.4.23 control scan waited 15 seconds and
saw `ready_state=complete`, but no `main`, composer, visible buttons or iframe.
An HTML-ready challenge is **not** a hydrated ChatGPT app.

`chatgpt-controls` and `chatgpt-status` now report `security_challenge` with
finite evidence (`challenge_title`, `challenge_markup`, or
`human_verification_prompt`) when a narrowly scoped top-level challenge is
recognized. The optional feature inventory is marked `unavailable`, not
`not_detected`, during a known challenge. The Project chat page-error guard
uses the same passive classification before route-mismatch handling. The
probe never returns raw HTML, page title, challenge body text, tokens, or
cookies, and deliberately avoids content from an actual conversation.

To try normal **manual** interaction in the same dedicated browser profile,
stop the OpenAgent server and run:

```powershell
.\.venv\Scripts\python.exe -m openagent.cli browser open --url https://chatgpt.com
```

The browser remains open for you to interact with the website. If Cloudflare
allows normal human verification, complete it personally, check whether a
real ChatGPT composer appears, then press Enter in that terminal to close.
Rerun the read-only status or controls command afterward. Human verification
may recur; success is **not** guaranteed by OpenAgent. If verification keeps
failing, consult the website's support. **Do not** automatically solve the
challenge, spoof browser fingerprints, rotate identities, or retry prompts.

The known incorrect cached Project answer remains unchanged. The exact
Project conversation URL and safe reply recovery remain separate work.
