# OpenAgent Bridge — v0.1 starter

A **working starter**, not a complete coding agent or a working ChatGPT/DeepSeek browser connector.

The initial release proves a provider-independent contract, preserves system/tool/developer messages, exposes basic OpenAI-compatible responses, records local session state, and validates compact OAP/1 tool envelopes **without executing commands**. It contains one deterministic `mock/echo` provider. No browser account credentials are read.

## Requirements

Python 3.11+

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
openagent serve
```

On Windows, activate using `.venv\\Scripts\\activate` instead.

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

## Scope and next steps

Implemented: provider registry; per-model capability manifest; ordered system/developer/user/assistant/tool messages; nonstream and synthetic SSE OpenAI Chat Completions; model discovery; SQLite turn journal; OAP/1 JSON tool-envelope validation; tests.

Not implemented: real ChatGPT/Claude/DeepSeek adapters, provider-native streaming, browser lifecycle, file upload/download, patch application, tool-call conversion for clients, `/v1/responses`, project management, session reconciliation UI, model effort selection, or the standalone agent loop. Do not treat the mock as proof that a specific website is supported.

See [`docs/architecture.md`](docs/architecture.md) for boundaries and acceptance criteria.
