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

## Windows v0.4.1 verified / next read-only diagnosis (v0.4.2)

Windows logs confirm that `api-check` succeeded and `/v1/models` returned
`mock/echo` and `chatgpt-web/default` with HTTP 200. Therefore the HTTP
bearer token issue is resolved. The ChatGPT UI report had
`on_chatgpt_domain: true` but **no detected controls**. This alone cannot
distinguish delayed JavaScript rendering from login/alternate layouts.

```powershell
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-status --headful --details --wait-seconds 12
```

Look at `surface` and `controls`: if `ready_state` is complete and visible
editables/buttons are still zero, inspect the visible browser page manually
for a login, challenge, blank screen or unsupported interface. Do not
automatically retry login or bypass a security check.

If non-content metadata is insufficient, capture a **local** screenshot
explicitly using `--screenshot "$env:USERPROFILE\openagent-page.png"`.
Review it and redact personal information before sharing. This file may contain
account and conversation data; never commit it to the repository.

`browser chatgpt-status` automatically closes its diagnostic tab. Do not run
it simultaneously with `browser open` using the same persistent profile.

## ChatGPT logged out on rendered homepage (v0.4.3)

If the screenshot shows a ChatGPT homepage with **Log in** at top right or in
its sidebar, the browser is not authenticated to the ChatGPT account even when
an editable composer is visible. The updated detector should report
`login_required`; it must not report `ready` based only on an anonymous editor.
OpenAgent does not transfer normal Chrome/Firefox cookies into its managed
profile. For login in a managed browser use only authentication supported by
that provider; OpenAgent cannot fix an identity provider's automated-browser
restriction. Avoid repeated automated login attempts or circumventing challenges.
