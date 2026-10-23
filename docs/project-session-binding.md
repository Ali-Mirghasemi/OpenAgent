# Verified ChatGPT Project session binding (v0.4.15 experimental)

## What changed

v0.4.14's read-only Project Home verifier is confirmed on Windows: `verified`, `project_heading_matches_route` for the explicitly chosen OpenAgent Project, with 185 tests passing. v0.4.15 adds a **separate, authenticated HTTP workflow** to create an empty OpenAgent session and bind an explicit Project ID **after the server repeats that verification in its own browser**. The SQLite association survives process restarts. It is never inferred from a Project name, chat text, or a client-supplied `verified` flag.

This release **does not send Project-scoped messages**. The normal `/openagent/sessions/{id}/turns` route returns HTTP 422 for a Project-bound session and will not reserve or submit a turn. `/v1/chat/completions` has no Project selection parameter; it continues to use the existing non-Project adapter. Future Project messaging must have separate, verified `/g/<project>/c/<chat>` identity rules.

## Windows verification

Make sure no other OpenAgent process is using the same managed browser profile. First start the server with the **same local API token in that PowerShell process**. A strong randomly generated token is better than the sample shown here.

Terminal 1:

```powershell
$env:OPENAGENT_API_TOKEN = "openagent-api"
.\.venv\Scripts\python.exe -m openagent.cli serve --enable-chatgpt-web --headful
```

Terminal 2:

```powershell
$base = "http://127.0.0.1:12321"
$headers = @{ Authorization = "Bearer openagent-api" }
$created = Invoke-RestMethod -Method Post -Uri "$base/openagent/sessions" `
    -Headers $headers -ContentType "application/json" `
    -Body '{"model":"chatgpt-web/default"}'
$sid = $created.id

$projectId = 'g-p-6ad516cbe000819184ae2c1ed16d4a22'  # choose yours
$payload = @{ project_id = $projectId } | ConvertTo-Json
$bound = Invoke-RestMethod -Method Post -Uri "$base/openagent/sessions/$sid/project" `
    -Headers $headers -ContentType "application/json" -Body $payload
$bound.project
```

The binding request scans the ChatGPT sidebar, with bounded Show-more/scroll actions (`wait_seconds=8`, `max_steps=30`), discovers the requested ID, then navigates to the candidate Project Home in a separate owned tab and waits for the exact heading in `main`. Both the route and heading must match. Nothing is submitted to ChatGPT. A `verified_at` timestamp is stored for audit. API authentication protects the endpoint; no cookies or page HTML are returned.

A rejected or unfamiliar layout produces HTTP 409 (`unverified`), or 503 if the browser verification is unavailable, and nothing is bound. It does **not** fall back to another Project. The HTTP request could take several seconds while it inspects the browser.

The Project binding is immutable. Create a new local session to select a different Project. An existing session with any history, upstream identity, or reserved turn cannot gain a Project binding. Repeat binding to the same Project only after the server re-verifies it.

To inspect the saved binding, even after restarting the server:

```powershell
Invoke-RestMethod "$base/openagent/sessions/$sid" -Headers $headers
```

## Boundaries / next engineering step

A `verified` Project Home route is **not** proof that a new message created inside it will be associated with that Project, or that follow-up chats retain membership. Before enabling submission, implement a separately gated Project-scoped provider path with pre- and post-submit membership checks, validated Project chat routing, first-turn URL identity, and an uncertain-outcome journal that never automatically retries. If these cannot be proved in the current UI, refuse the action.

Model and reasoning settings, attachments, uploads/downloads, typed tool calls, OpenCode/9Router and additional providers remain on the roadmap. Browser automation must be used only where permitted by the provider.
