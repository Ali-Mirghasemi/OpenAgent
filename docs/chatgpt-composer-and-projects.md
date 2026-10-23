# ChatGPT composer and Projects inspection (v0.4.10 experimental)

## Why this release exists

A Windows operator on v0.4.9 successfully authenticated, passed 149 tests and
verified `mock/echo` through `/v1/chat/completions`. A single authorized
account-backed smoke request returned HTTP 503 with:

`submit_unavailable: no visible, enabled Send control after typing; nothing submitted`

The operator saw text entered in ChatGPT's editor. The current selectors did
not prove an active Send control; **OpenAgent did not click Send**. The operator
should not automatically retry failed calls. This update adds known public
variants and a separate draft-only preflight, but still requires live validation.

## Draft-only Send preflight

From PowerShell, with any other OpenAgent process using the same persistent
profile stopped:

```powershell
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-compose-check --headful
```

A **new** tab is opened in the selected OpenAgent browser profile. OpenAgent
types a fixed benign string (`OpenAgent draft-only send check`) in the first
unique visible composer, waits up to five seconds for a visible enabled Send
control, **never clicks Send**, and closes that tab. Some web apps autosave
drafts even without sending them. Nothing is posted to the API or logged from
the account. Only `state` and `submitted: false` are returned.

A result of `send_ready` means the control is detectable after typing; it is
**not proof** that a response can be received, that provider automation is
permitted, or that native tools and projects work. `submit_unavailable`
indicates no safely identified control was found; do not use an unconditional
Enter-keypress or click a generic page-wide submit control as a workaround.

The adapter now recognizes `#composer-submit-button`, retains `send-button` and
label-based candidates, prefers candidates within the active composer form,
and optionally considers a **unique** form-scoped submit button (never
guesses among multiple unlabeled submits). Obvious microphone,
audio, attachment, cancel and search actions are excluded. Multiple visible
composers cause a safe `ui_changed` error.

## Read-only Projects inventory

```powershell
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-projects --headful
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-projects --headful --json
```

Starting in v0.4.11, the command also discovers visible, expandable sidebar
Project rows carrying a validated public DOM project marker, and waits briefly
for hydration. See [v0.4.11 Project rows](chatgpt-project-rows.md). It never
clicks, selects, changes Projects, reads files, or submits a chat. Names and URLs may be private, so review output
before sharing it. When the sidebar is collapsed or links are not mounted,
zero results is inconclusive. Project URL validation rejects non-ChatGPT
origins, query strings, fragments and conversation URLs.

### Future Project-selection contract (not implemented)

The intended sequence is (1) operator selects one verified existing Project;
(2) the provider binds that Project identity to an **OpenAgent session**;
(3) the adapter verifies a project-scoped new-chat surface before submission;
(4) the upstream conversation keeps the same Project binding on resume.
A model or Project choice must not silently change another existing session.
The currently implemented `/c/<uuid>` conversation parser does not cover
project-specific `/g/<project>/c/<uuid>` routes, so **it must not be used to
claim project-resume support**. The current model ID still reflects whatever
model is selected on the website, not a verified model selection API.

## Next roadmap (explicitly retained)

- Project selection and project-scoped chat creation/resumption.
- Model list and model/thinking-effort selection (only where verifiable).
- File uploads, output downloads, attachments, and bounded workspace access.
- Per-provider capabilities, interruption, and streaming.
- OpenCode/9Router compatibility and OAP/1 tool-call translation.
- Other browser providers (DeepSeek, Claude), respecting service permissions.
- Standalone coding agent with approved local command execution, later.

Use automation only where the service permits it. Login/session checks do not
override provider policies or security challenges.
