# OpenAgent Bridge — v0.1.1 starter

A **working starter**, not a complete coding agent or a working ChatGPT/DeepSeek browser connector.

The initial release proves a provider-independent contract, preserves system/tool/developer messages, exposes basic OpenAI-compatible responses, records local session state, and validates compact OAP/1 tool envelopes **without executing commands**. It contains one deterministic `mock/echo` provider. No browser account credentials are read.

## Requirements

Python **3.10 or newer** (tested in this package's development environment with
Python 3.13; validate Python 3.10 on Windows as part of installation).

## Install and run — Windows PowerShell

Open PowerShell in the directory containing `pyproject.toml`.

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -c "import sys; print(sys.executable); print(sys.version)"
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pytest -q
python -m openagent.cli serve
```

You may replace `py -3.10` with `py -3.11`, `py -3.12`, or your installed Python
version. If the `py` launcher is missing, use `python -m venv .venv`.
The reported `sys.executable` path should end in `.venv\Scripts\python.exe`.

If PowerShell blocks activation scripts, use:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Or **skip activation entirely** and run the environment's interpreter explicitly:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m openagent.cli serve
```

Do not use `source` in PowerShell. `activate.bat` is for `cmd.exe` and does not
activate the parent PowerShell process. If pip reports "Defaulting to user
installation", check your Python executable: the intended venv may not be active.

For Command Prompt (`cmd.exe`), use `.venv\Scripts\activate.bat`.

## Install and run — Linux/macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
python -m openagent.cli serve
```

By default, the HTTP API listens on `http://127.0.0.1:8765` and uses a local SQLite database at `~/.openagent/sessions.db`.

```bash
curl http://127.0.0.1:8765/v1/models
curl http://127.0.0.1:8765/openagent/capabilities/mock/echo
curl -s http://127.0.0.1:8765/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"mock/echo","messages":[{"role":"system","content":"Keep all context"},{"role":"user","content":"Hello"}]}'
```

This should return `Echo: Hello`.

To request SSE output, add `"stream":true` to the JSON request.

## Resumable local sessions

```bash
curl -s http://127.0.0.1:8765/openagent/sessions \
  -H 'Content-Type: application/json' -d '{"model":"mock/echo"}'
# Copy the resulting id into $SID.

curl -s http://127.0.0.1:8765/openagent/sessions/$SID/turns \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"turn-001","message":{"role":"user","content":"Check project"}}'

curl -s http://127.0.0.1:8765/openagent/sessions/$SID
```

A repeated `request_id` returns the committed response. An unresolved or unknown-outcome request returns HTTP 409 instead of blindly re-submitting to a remote model. Manual reconciliation is not implemented in v0.1. The native session endpoint is intentionally **not** part of the OpenAI API.

## Local security

- Server binds to loopback by default. Non-loopback binding requires setting `OPENAGENT_API_TOKEN`.
- To require a token for local requests too, export `OPENAGENT_API_TOKEN=...` before starting.
- Do not expose Chrome debugging ports, session databases, tokens, or the API to an untrusted network.
- The service returns generic provider errors to avoid exposing secrets.
- No shell tool execution is implemented in the bridge.

## Project maintenance files

`TODO` is the development backlog, `PROFILE` describes project assumptions and
architecture conventions, `CHANGES` records releases, and `.gitignore` protects
local environments and session artifacts.

## Scope and next steps

Implemented: provider registry; per-model capability manifest; ordered system/developer/user/assistant/tool messages; nonstream and synthetic SSE OpenAI Chat Completions; model discovery; SQLite turn journal; OAP/1 JSON tool-envelope validation; tests.

Not implemented: real ChatGPT/Claude/DeepSeek adapters, provider-native streaming, browser lifecycle, file upload/download, patch application, tool-call conversion for clients, `/v1/responses`, project management, session reconciliation UI, model effort selection, or the standalone agent loop. Do not treat the mock as proof that a specific website is supported.

See [`docs/architecture.md`](docs/architecture.md) for boundaries and acceptance criteria.
