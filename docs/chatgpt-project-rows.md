# ChatGPT Projects sidebar inspection — v0.4.11

## Background

Windows v0.4.10: 161 tests passed, `chatgpt-compose-check --headful`
reported `send_ready` and `submitted: false`, while `chatgpt-projects`
reported 0. The v0.4.10 inventory searched only rendered navigation **anchors**.
An October 2026 public ChatGPT UI variant instead renders expandable Project
rows, with a `data-sidebar-project-container-id="project:g-p-..."` DOM marker.
That markup is **not an official stable API**, and may change.

## Usage

Stop other OpenAgent processes holding the persistent browser profile, then:

```powershell
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-projects --headful --wait-seconds 12
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-projects --headful --json
```

The command opens a dedicated browser tab and **never clicks, expands,
selects, uploads, submits, or reads Project files**. The bounded wait (default
five seconds, maximum 30) lets the sidebar hydrate. Only visible canonical
links or expandable rows with strictly validated project IDs are inventoried.
The row's dedicated title element supplies the name where available. No nested
chat titles or page-wide text are read. Items include `source: link` or
`source: sidebar-row`; the latter has a **derived candidate URL**.

**If the result is still 0:** the Projects section may be collapsed, not
mounted, moved to a separate view, or genuinely absent. Open it manually in
the browser as appropriate and retry. Do not assume zero means no Projects.
Because the diagnostic creates a fresh tab, manually expanding another tab's
sidebar may not affect it; persistent layout preferences may or may not carry.

Names and project IDs are personal account metadata: redact them before
sharing. The command never reads cookies or saved credentials.

## Boundary for future Project selection

Project discovery is not selection. The adapter must obtain an explicit user
choice, verify the displayed Project scope before composing, prevent sending
in a generic chat on scope mismatch, and persist verified Project identity
separately from the conversation ID. A derived overview route is not proof that
the current web app will navigate to Project Home; real routes may change.
No Project-scope chat submission or continuation was implemented here.

Reference: community reports of late-2026 sidebar changes and public DOM row
marker usages. Their presence does not guarantee compatibility with a user's
live account. Synthetic Chromium tests do not access ChatGPT or user profiles.
