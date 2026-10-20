# OpenAgent Bridge — Architecture Decision Record 001

**Status:** Iterating. Current implementation: v0.2.0.

## Goal

Bridge supported AI providers into OpenCode/9Router clients. A future
standalone agent reuses the same bridge without moving model-tool execution
into the provider layer.

## Core boundaries

1. **Client API:** OpenAI Chat Completions subset, bridge-specific sessions and
   capabilities under `/openagent/`. Native Responses API is not implemented.
2. **Registry/capabilities:** resolves fully qualified model IDs. `unknown`
   is not equivalent to `unsupported`; feature evidence is explicit.
3. **Provider:** handles service communication; must preserve ordered system,
   developer, user, assistant and tool messages.
4. **Browser Manager:** optional Playwright backend owning its connection and
   created tabs only. No site-specific logic or implicit prompt retry.
5. **Session journal:** SQLite, caches completed results and blocks retry of
   pending/unknown-outcome turns until reconciliation.
6. **Standalone agent (future):** owns filesystem, shell, tools, approvals and
   patch application. The bridge must not run arbitrary model commands.

## Implemented in v0.2

- `src/openagent/browser/manager.py`: typed lifecycle, origin validation, tab
  ownership, concurrency limit, failure cleanup.
- `src/openagent/browser/playwright_backend.py`: persistent and CDP Playwright
  backend, imported lazily as an optional feature.
- `src/openagent/network.py`: Windows-friendly preflight port selection.
- `openagent browser probe`: browser startup/shutdown without site navigation.
- Existing mock provider, capability manifests, SQLite journal and API tests.

## OAP/1 wire envelope (parsing only)

```json
{"v":1,"mode":"sequential","stop_on_error":true,"calls":[
  {"id":"c1","tool":"file.read","args":{"path":"src/main.py"}},
  {"id":"c2","tool":"shell.run","args":{"cmd":"pytest -q"}}
]}
```

Tool calls from a model are untrusted. The bridge parses and validates the
wire format but does not execute these commands or read arbitrary files.

## Future acceptance criteria

- Provider adapters with tested conversation continuation, uploads and
  downloads, stop and model/effort selection where authorized.
- Robust fixture-driven completion detection; cancellation result must
  distinguish provider-confirmed stop from local transport disconnect.
- Tool-calling interoperability tests for OpenCode and 9Router.
- Deterministic request IDs with no duplicate remote submission.
- File artifact hashes and rollback-safe patches in the separate agent.

## Security

Keep browser CDP loopback-only; reject default browser profiles and non-HTTPS
remote URL navigation. Runtime error messages may contain credentials: do not
expose full Playwright exceptions to HTTP clients. Evaluate terms and model
permissions per adapter. See [browser-manager.md](browser-manager.md).


## Experimental browser-backed provider (v0.4)

`ChatGPTWebProvider` is optional and delegates browser ownership to the existing
`BrowserManager`, which is closed by the FastAPI lifespan at server shutdown.
Browser startup is lazy; requests are serialized per provider instance. A single
turn opens a new, owned tab, sends the full serialized caller history as text
(no provider-native roles), and requires new-turn DOM completion evidence before
returning. The bridge still refuses unsupported streaming and native tools.

`chatgpt-web/default` denotes *the UI selection*, not a verified model.
`--enable-chatgpt-web` requires `OPENAGENT_API_TOKEN` even on loopback.
Public website permissions and account verification are separate from transport
capability; no live third-party service was exercised by the release tests.
