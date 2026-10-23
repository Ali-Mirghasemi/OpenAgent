# Experimental Project-scoped session routing — v0.4.16

## Preflight and gated submission

v0.4.15 already verifies and persists Project binding. v0.4.16 adds an
independent **disabled-by-default** transport for Project-bound turns. This
release has **not** been tested with a live ChatGPT account and has no private
API support. Do not assume actual Project-scoped message submission works
merely because static route and fixture tests pass.

Start the server only where public-site automation is authorized:

```powershell
$env:OPENAGENT_API_TOKEN = "replace-with-strong-random-token"
.\.venv\Scripts\python.exe -m openagent.cli serve --enable-chatgpt-web --enable-chatgpt-conversations --enable-chatgpt-project-messages --headful
```

The server rejects `--enable-chatgpt-project-messages` without the
continuation flag, and requires an authenticated loopback HTTP API.
Use `/openagent/sessions` to create an empty ChatGPT session, bind the Project
via `POST /openagent/sessions/{id}/project`, and only then initiate a turn:

```powershell
$body = @{ request_id='project-turn-1'; message=@{role='user';content='Hello from my Project'} } | ConvertTo-Json -Depth 6
Invoke-RestMethod -Uri "$base/openagent/sessions/$sid/turns" -Method Post -Headers $headers -ContentType 'application/json' -Body $body
```

The first turn opens `/g/<bound-id>/project`, waits for an exact visible
Project heading in `main`, checks UI readiness, fills the newest user message,
verifies the Project route again and clicks Send once. The answer must have a
new completed assistant turn and navigate to `/g/<bound-id>[-slug]/c/<uuid>`.
Follow-ups reopen precisely that Project conversation URL, require visible
prior assistant history, submit only the newest user message and check that
both Project and chat UUID remain unchanged. Redirects or unverified history
are rejected before submission. There is no fallback to regular ChatGPT chat.

If an error occurs after a possible submission, the local turn journal records
an unresolved `unknown` state, blocking replay; inspect the website manually.
The same successful `request_id` returns a cached response without resending.
Neither this project transport nor current browser automation discovers or
selects model/reasoning options, handles files or supports native tool calls.

The tests use local HTML in disposable Chromium profiles or fake provider
methods. They do not log in, touch personal profiles or contact ChatGPT.

## v0.4.17: recover an already-submitted unknown Project turn

In the live Windows test, ChatGPT **received and answered** the prompt but the
bridge reported 502 because the URL contained a Project-name suffix, for example
`/g/g-p-<32 hex>-openagent/c/<uuid>`. Both the canonical and slugged variant are
now recognized, but only when the entire Project ID matches the saved binding.

If the local journal lists the earlier request as `unknown`, do not send it again.
Stop and restart the server with the same three experimental opt-in flags, then
copy the exact existing Project conversation URL from the browser address bar.
The endpoint below reads that conversation; it does **not** click Send:

```powershell
$base = 'http://127.0.0.1:12321'
$headers = @{ Authorization = "Bearer $env:OPENAGENT_API_TOKEN" }
$sid = '<existing-session-id>'
$requestId = '<previous-unknown-request-id>'
$conversationUrl = 'https://chatgpt.com/g/<project-id>-<slug>/c/<uuid>'
$body = @{
  message = @{ role='user'; content='<EXACT original user message>' }
  conversation_url = $conversationUrl
} | ConvertTo-Json -Depth 6
Invoke-RestMethod -Uri "$base/openagent/sessions/$sid/turns/$requestId/reconcile" `
    -Method Post -Headers $headers -ContentType 'application/json' -Body $body
```

Requirements: same original request hash in SQLite (`unknown` status only),
verified saved Project binding, exact Project+conversation URL, matching visible
*last* user message, a later assistant reply that is stable and exposes a scoped
Copy action, and no generation pending. On failure, nothing is committed and the
unknown turn remains blocked. No automatic retries, cookie extraction or
private endpoints are used. The recovered output contains the existing reply and
the upstream chat UUID, permitting later continuation under the same session.

Because `serve --headful` uses one persistent browser profile, the window stays
open between requests. Stop the server to close it safely; don't close the
window during an active turn.
