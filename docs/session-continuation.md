# Session continuity and upstream conversation identity (v0.4.8)

OpenAgent has **two different kinds of state**:

1. A **local session**, always managed by `SessionStore` and persisted in SQLite.
2. An optional **upstream conversation ID**, which a provider adapter may report only
   when it knows which upstream conversation produced the response.

**No live ChatGPT conversation ID discovery, ChatGPT chat-resume navigation, or
cross-chat handoff is included in v0.4.8.** The ChatGPT browser adapter is
still a one-shot experimental preview (`persistent_chat: unsupported`).

## New session contract

`POST /openagent/sessions` creates an unbound local session. Its response
includes `"upstream": null`. After a provider with **verified** upstream
continuation support returns a valid ID, OpenAgent can persist it as:

```json
{
  "upstream": {
    "conversation_id": "upstream-1234",
    "source": "provider_reported"
  }
}
```

`provider_reported` is a provenance label, **not** an independent verification
of the user's account or service permissions. IDs are opaque strings, not raw
URLs or cookies. OpenAgent limits accepted ID characters and length.

`Provider.complete_in_session(..., upstream_conversation_id=...)` receives the
saved reference on later local turns. A provider must explicitly set
`supports_upstream_conversations = True` and implement real continuation
semantics before it can bind/resume an upstream conversation. Existing
one-shot providers still receive the full local transcript when unbound.

If a provider returns a *different* upstream ID for an already bound session,
OpenAgent declines the commit, leaves the local transcript untouched, marks
the outcome `unknown`, and blocks further turns pending manual investigation.
A bound session is **never silently redirected to a new conversation**.

## Durable request ledger

- `GET /openagent/sessions/{id}` — local transcript, model, optional upstream ID.
- `GET /openagent/sessions/{id}/turns` — request IDs and status only (`pending`,
  `done`, or `unknown`), plus `has_response`; no cookie data or prompt contents.
- `POST /openagent/sessions/{id}/turns` — requires a unique `request_id`.

Completed turns can be requested again with the **same content and ID**; the
response is returned from SQLite (`cached: true`) without another provider
request. Different content under the same ID is rejected.

`pending` and `unknown` status are **not** automatically retried. A timeout,
client disconnect, cancellation, or local/database error can occur after the
remote provider processed a request. A subsequent request in that session is
blocked until a trusted reconciliation process is implemented. There is not yet
an API for deleting or force-retrying uncertain turns.

## Schema migration

Opening an existing v0.4.7 SQLite file automatically adds a nullable
`upstream_conversation_id` column to `sessions`. It does not delete or modify
existing messages, turn IDs, or statuses. The new column is only populated by
provider-reported identity during an atomic successful turn commit.

## PowerShell smoke test (mock only)

Start the service in one terminal:

```powershell
.\.venv\Scripts\python.exe -m openagent.cli serve
```

In another terminal, using the port printed by the server:

```powershell
$base = 'http://127.0.0.1:12321'
$session = Invoke-RestMethod "$base/openagent/sessions" -Method Post `
  -ContentType 'application/json' -Body '{"model":"mock/echo"}'
$sid = $session.id
$body = '{"request_id":"step-1","message":{"role":"user","content":"hello"}}'
Invoke-RestMethod "$base/openagent/sessions/$sid/turns" -Method Post `
  -ContentType 'application/json' -Body $body
Invoke-RestMethod "$base/openagent/sessions/$sid/turns"
Invoke-RestMethod "$base/openagent/sessions/$sid"
```

Mock output is **not** proof of upstream session continuity; `upstream` stays
`null` in this example. If a bearer token is configured for the server, send
that token in a matching `Authorization: Bearer ...` header.

## Next milestone

Implement provider-specific, permission-respecting upstream conversation
identity and resume operations, with independent capability verification and
synthetic fixtures before any real-world service testing. Do not use a
website-internal API or assume cookie import authorizes automation.
