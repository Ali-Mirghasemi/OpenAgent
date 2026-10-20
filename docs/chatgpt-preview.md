# ChatGPT Web UI adapter preview — v0.4.1

## Status and scope

This is an **experimental, opt-in UI adapter**. It does not use OpenAI's API,
Codex, private ChatGPT endpoints, cookie extraction, login automation, or account
control bypasses. It may not function on the current live ChatGPT UI because DOM
selectors, plan controls and completion states change. Only controlled test
fixtures have been verified. Check the service's terms and your authorization
before performing automated interactions.

The ID `chatgpt-web/default` means **the model already selected by the UI**.
It is not a claim to offer GPT model names or to select thinking effort.

## Read-only status check on Windows

After updating from 0.3 and keeping the selected portable Chromium executable:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,browser]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-status --headful
```

Status `ready` means specific UI controls were visible, **not** verified login.
`login_required`, `security_challenge`, `rate_limited`, `ui_changed` and
`browser_disconnected` are useful diagnostic categories. There is no automatic
sign-in or retry when a provider rejects browser-controlled login.

## Explicit bridge opt-in

```powershell
$env:OPENAGENT_API_TOKEN = Read-Host "Local OpenAgent API token"
.\.venv\Scripts\python.exe -m openagent.cli serve --enable-chatgpt-web --headful
```

The API remains bound to `127.0.0.1` by default (typically port 12321).
With the token in your PowerShell environment, an authenticated request can read:

- `GET /v1/models`
- `GET /openagent/capabilities/chatgpt-web/default`
- `GET /openagent/providers/chatgpt-web/diagnostics` (read-only)

For example:

```powershell
$headers = @{ Authorization = "Bearer $env:OPENAGENT_API_TOKEN" }
Invoke-RestMethod -Uri http://127.0.0.1:12321/v1/models -Headers $headers
Invoke-RestMethod -Uri http://127.0.0.1:12321/openagent/providers/chatgpt-web/diagnostics -Headers $headers
```

The opt-in HTTP adapter also accepts a **non-streaming** Chat Completions
request, but submitting one to a real service requires appropriate permissions
and current live UI verification. Never assume a successful fixture test
establishes actual account compatibility.

## Conformance and failure semantics

- A request is serialized per browser-backed provider instance, one submitted
  turn at a time. It starts a new upstream chat and packages the complete local
  transcript into an ordinary text prompt. This is **not** native system/developer
  role preservation on the website.
- The adapter considers only an assistant message created after submission;
  it does not allow a previous turn's Copy action to finish the new answer.
- A new assistant's Copy action **and** a stable response **and** absence of
  pending/busy evidence are required to finish. An unrecognized layout fails
  closed; stability alone is not completion evidence.
- A navigation failure before submitting is different from an unknown outcome
  after clicking Send. OpenAgent never retries uncertain submissions.
- Browser tabs are closed after each request, even when cancelled or failed.
- The server's application lifespan closes the OpenAgent browser connection at
  server shutdown, not the external CDP browser itself.

## Outstanding work

No verified model enumeration, effort switching, ChatGPT Projects, attachment
upload/download, native tool calls, real streaming, upstream conversation resume,
rate-limit information, or automatic context rollover. Any of these requires a
separate capability with live tests; do not mark them supported in the manifest.

## Windows authentication troubleshooting (v0.4.1)

If `GET /v1/models` returns 401, verify that both the server and the client
terminal set the **same** token; PowerShell environment variables are
process-scoped. Recreate `$headers` **after** setting the client token.

The new read-only command performs both a health check and a token-authenticated
model-list request from the client terminal, without printing credentials:

```powershell
$env:OPENAGENT_API_TOKEN = Read-Host "OpenAgent API token"
.\.venv\Scripts\python.exe -m openagent.cli api-check
```

If the server is on another local port, pass `--url http://127.0.0.1:PORT`.
This command rejects non-loopback destinations to avoid forwarding a local
secret to an arbitrary remote host. Further details in
[windows-auth-troubleshooting.md](windows-auth-troubleshooting.md).
