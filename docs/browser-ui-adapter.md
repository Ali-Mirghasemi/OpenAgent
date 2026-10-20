# OpenAgent v0.3 — UI Adapter and Diagnostics

## Scope

The new `BrowserUIProvider` communicates using a declared `BrowserSiteProfile`:
`provider_id`, `model_id`, initial HTTPS/loopback-HTTP URL, verified CSS
selectors, completion evidence, polling, timeout. It has **no built-in ChatGPT,
DeepSeek or Claude selectors**. Public AI websites are not used in its tests.

It is deliberately one-shot: a fresh OpenAgent-owned tab per completion. A
complete ordered JSON transcript is sent as the prompt, preserving `system`,
`developer`, `user`, `assistant` and `tool` entries; however, website text cannot
provide native role priority guarantees. Model-native tool calls, website chat
persistence, streaming, projects and artifact transfers are not claimed.

## Diagnostic results

`inspect_ui()` checks these provider-supplied selectors, highest priority first:

| State | Evidence | Automatic action |
| --- | --- | --- |
| `security_challenge` | visible challenge element | stop and report |
| `login_required` | visible login element | stop and report |
| `rate_limited` | visible rate-limit element | stop and report |
| `generation_in_progress` | visible busy element | wait in active turn |
| `ready` | composer and send button both visible | eligible to send |
| `ui_changed` | controls absent or invalid selectors | stop and report |
| `browser_disconnected` | page closed or transport down | stop; no retry |

Only selector category and state leave the diagnostic function; it never reads
or outputs session cookies, page text, local storage, or authorization tokens.
Site selectors must be validated against the actual chosen provider's page.

## Turn lifecycle

1. Browser Manager opens a new tab at the declared URL. Its allowlist, tab cap,
   navigation restrictions and ownership continue to apply.
2. UI diagnostics must say `ready` before submitting anything.
3. Capture the number of existing assistant messages and the completion marker
   state; fill a compact JSON transcript preserving message roles.
4. Click Send **once**. If the click could have been received, any later error
   has an uncertain remote outcome; no replay is attempted.
5. Wait for an assistant node created after the captured baseline, stable text
   in several observations, and **positive completion evidence**. A stale done
   marker or stable text alone is not sufficient. Prefer an adapter-specific
   completed-turn marker; otherwise busy-to-idle transitions are provisional.
6. Return a single text response (no synthetic native-tool claims). Close the
   owned tab even after cancellation or failure.

The offline adapter may not detect every page race. A real provider must add
session-specific message IDs, marker anchoring and site-version regression tests.
For browser providers, interruption currently cancels the **local wait only**;
provider-side generation cancellation is not promised.

## Windows local exercise

Open PowerShell in the repository root, and run the fixture server in terminal A:

```powershell
.\.venv\Scripts\python.exe -m http.server 8999 --bind 127.0.0.1 --directory examples
```

From terminal B, using the browser you selected earlier:

```powershell
.\.venv\Scripts\python.exe -m openagent.cli browser diagnose --url http://127.0.0.1:8999/chat_fixture.html
.\.venv\Scripts\python.exe -m openagent.cli browser ui-test --message "Hello OpenAgent"
.\.venv\Scripts\python.exe -m openagent.cli browser ui-test --headful --message "Visible test"
```

No browser download or authentication is needed. The `ui-test` URL is restricted
to loopback HTTP and cannot be pointed at a production AI website. The
`browser diagnose` command is read-only and may accept a user-provided HTTPS
site plus explicit selectors for future compatibility investigations.

## What is still needed for a real adapter

- Check the provider's terms and authorized interface before live interaction.
- Discover model availability, UI controls, reasoning levels, projects and files
  from actual account/session state, not hardcoded guesses.
- Detect challenge/login/limits reliably, without bypassing provider controls.
- Bind completion to a specific conversation and response (not a stale marker).
- Preserve provider session IDs, request IDs, and outcomes in the SQLite journal.
- Test cancellation, partial output, new chats, retries, and resumption on the
  target browser/platform.
- Expose compatible native tool envelopes only after translation is verified.

The mock `/v1/models` service still advertises `mock/echo` alone. No live
browser-based provider is yet registered with the HTTP server.
