# OpenAgent Bridge — Architecture Decision Record 001

**Status:** Initial direction. API and protocol may change before v1.0.

## Goal

Bridge supported AI providers into clients such as OpenCode and 9Router while allowing a future standalone agent to reuse the same infrastructure.

## Boundaries

1. **Client API**: OpenAI-compatible HTTP endpoint, with bridge-specific endpoints under `/openagent/`.
2. **Registry/capabilities**: Resolve fully-qualified model IDs; never assume unverified model/plan features.
3. **Provider adapter**: Handle provider authentication/session transport and model responses; explicitly advertise limitations.
4. **Session journal**: Preserve local conversation history and request outcomes. New chat migration later consumes checkpoints, not raw unbounded history.
5. **Standalone agent (future)**: Own shell/file tools, approvals, Git patches, and execution. **The bridge must not execute tool calls.**

## Contract principles

- Preserve system, developer, user, assistant, tool messages and tool-call IDs end to end.
- `unknown` differs from `unsupported`; label capability evidence as declared, observed, or tested.
- Never claim genuine token-by-token model streaming when only one buffered chunk is available.
- Never automatically retry an ambiguous submitted turn: it could have completed remotely.
- Cancellation support is provider-specific and must indicate whether interruption is confirmed.
- No silent fallback that changes model/provider semantics.
- Authenticated browser session handling, when allowed, must use separate provider adapters and avoid exposing cookies/tokens.

## OAP/1 future protocol

```json
{"v":1,"mode":"sequential","stop_on_error":true,"calls":[
  {"id":"c1","tool":"file.read","args":{"path":"src/main.py"}},
  {"id":"c2","tool":"shell.run","args":{"cmd":"pytest -q"}}
]}
```

v0.1 only validates this JSON; caller is responsible for user authorization, file boundaries and tool implementation. Treat model-provided paths and commands as untrusted input.

## Acceptance criteria before real provider integration

- Contract tests for full transcript propagation, tool schemas, stream framing, capability reporting.
- Deterministic fixture tests for browser completion-detection states.
- Live adapter smoke tests run separately and only for providers whose use is authorized.
- Explicit idempotency and recovery policy for remote submission.
- Unit tests for file transfer boundaries, hashes, and safe patch paths before implementing downloads.
- OpenCode interoperability tests with tool calls, model lists, and streamed responses.

## Immediate next milestone

Refine `Provider` to support stream events, per-session remote IDs, interrupt, and attachment operations with typed `UnsupportedFeature` errors. Add a permitted real provider via an adapter after those contracts are proven. Avoid changing the API and agent runtime simultaneously.
