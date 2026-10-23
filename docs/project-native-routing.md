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
new completed assistant turn and navigate to `/g/<bound-id>/c/<uuid>`.
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
