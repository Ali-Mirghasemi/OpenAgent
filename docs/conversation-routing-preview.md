# Experimental ChatGPT conversation routing — v0.4.9

This release adds an **independently opt-in** provider-specific continuation
path to the existing local session journal. It is verified with offline
synthetic browser fixtures; **no live ChatGPT message submission, provider
permission, or ChatGPT account compatibility is established by these tests.**

## Operator controls

By default `serve --enable-chatgpt-web` retains the previous one-shot adapter.
Only if a provider permits this automation and the operator explicitly elects
to test the preview can they start:

```powershell
$env:OPENAGENT_API_TOKEN = Read-Host "OpenAgent API token"
.\.venv\Scripts\python.exe -m openagent.cli serve --enable-chatgpt-web --enable-chatgpt-conversations --headful
```

The additional flag is not used for any fixture tests, and it does not
log in, select a model, import cookies, perform native tools, or grant any
new user/account permission. The API remains bearer-protected. The model
identifier `chatgpt-web/default` still means the website's current UI selection.

## Session protocol (experimental)

Create an OpenAgent local session with `POST /openagent/sessions` using
`{ "model": "chatgpt-web/default" }`, then submit a plain-text user turn with
`POST /openagent/sessions/{id}/turns` and a unique `request_id`.

For the first turn, OpenAgent uses the logged-in website's new-chat page. After
one successful, turn-scoped response, it requires a canonical
`https://chatgpt.com/c/<uuid>` browser address before committing the response
or binding the upstream conversation. On later turns it navigates to exactly
that known conversation address and requires existing visible assistant history
before submitting **only the latest user text**, avoiding full local transcript
replay into an already retained upstream thread.

The strict route matcher deliberately rejects share URLs, project-specific
routes, unknown formats, redirects, origin changes and query fragments. A
changed chat identity or missing post-submit URL is an **unknown remote
outcome**: the existing SQLite request journal will block further turns until
manual reconciliation, rather than submit a duplicate. Request IDs are scoped
to a local session. Do not reset the journal simply to retry a remote request.

## Known limitations

- URL visibility is evidence of a browser route, not proof of upstream API or
  account ownership; real-site behavior must be verified before relying on it.
- Homepage URLs, temporary chats, project URLs, IDs that aren't canonical
  UUIDs, virtualized history with no visible assistant, localized or changing
  selectors, and site navigation without a stable route may fail closed.
- No native tool calling, model switching, provider streaming, attachments,
  project management, or browser log-in bypass.
- Capability `persistent_chat` remains **unknown** even when the experimental
  flag is on, because only fixtures (not real ChatGPT) verified the protocol.
- Strict HTTPS/loopback navigation and local browser profile ownership remain.

## Safe local checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_chatgpt_conversations.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-status --headful --details
```

The tests use a synthetic browser backend and temporary SQLite files; they do
not send any message to ChatGPT or access cookies. `chatgpt-status` is still
read-only. Avoid attaching screenshots of account conversations or cookie data.
