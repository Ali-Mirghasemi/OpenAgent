# Windows API authentication and ChatGPT UI troubleshooting

## A working installation is not proof of a working ChatGPT account session

After 72 successful Windows tests on v0.4.0, the user reported HTTP 401 and
`ui_changed` in the read-only status tool. These failures belong to separate
subsystems. `mock/echo` and `chatgpt-web/default` are visible in the model
catalog only when correctly registered and authorized. Live ChatGPT turn
submission remains unverified.

## Terminal A — API server

```powershell
$env:OPENAGENT_API_TOKEN = Read-Host "OpenAgent API token"
.\.venv\Scripts\python.exe -m openagent.cli serve --enable-chatgpt-web --headful
```

Do not close Terminal A. Confirm the port printed by Uvicorn. Use a strong
random token rather than the demonstration value `openagent-api`.

## Terminal B — API client

```powershell
$env:OPENAGENT_API_TOKEN = Read-Host "OpenAgent API token"
.\.venv\Scripts\python.exe -m openagent.cli api-check
```

Supply the *same* token used in Terminal A. Environment variables set in one
PowerShell process do not propagate to already-open terminals. If making raw
requests, **rebuild** `$headers` after changing the environment variable:

```powershell
$headers = @{ Authorization = "Bearer $env:OPENAGENT_API_TOKEN" }
Invoke-RestMethod -Uri http://127.0.0.1:12321/v1/models -Headers $headers
```

`api-check` distinguishes a 401 response from a stopped server or wrong port.
It sends credentials only to a loopback URL and never prints the credential.

## Read-only ChatGPT browser diagnostics

```powershell
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-status --headful --details
```

The `controls` object reports fixed UI selector names and only
`visible`, `not_visible`, `unavailable`, or `not_configured`. It reports
`on_chatgpt_domain` but does not print the full URL, page text, cookies or
account details. `ui_changed` with composer/send not visible may mean a
logged-out page, an incomplete load, or a changed ChatGPT UI. Do not assume
automation permission from a successful selector check. If authentication
is blocked by the provider, use supported authentication rather than bypassing
a security challenge.

The 404 favicon response and third-party `starlette.testclient` deprecation
warning are unrelated to either issue.
