# ChatGPT Project destination verification preview (v0.4.14)

## Why this check exists

Windows v0.4.13 found fourteen Project rows (five before "Show more" on the first run). A subsequent invocation began with fourteen rendered entries because the browser's sidebar expansion state apparently persisted. The UI scan remains best effort and does not know the account-wide total.

Some 2026 ChatGPT layouts use a sidebar Project row as a chat-list expander, not navigation. A guessed `/g/g-p-.../project` URL can redirect or fail to display that Project's home. OpenAgent must not assume that seeing a project ID or clicking a row guarantees that a chat is inside the selected Project.

## Command

Stop any other OpenAgent process using the same browser profile. Use the exact **ID** returned by `browser chatgpt-projects`, not a Project name:

```powershell
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-project-check --headful --project-id "g-p-YOUR_ID"
```

If that Project requires expanding the sidebar before its row is rendered:

```powershell
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-project-check --headful --project-id "g-p-YOUR_ID" --load-all --wait-seconds 12 --max-steps 30
```

The command first confirms the ID appears in the visible/project-discovery inventory. It opens the candidate same-origin Project URL in a **separate tab**, and waits for the Project's exact visible heading within the page's **main** content. It rejects off-Project routes, login/challenge UI, and missing or mismatched headings. A sidebar label alone is not enough.

Possible results:

- `verified` / `project_heading_matches_route`: evidence for this Project home **at the time checked**, not for actual chat submission.
- `unverified` / `project_not_in_visible_inventory`: the Project was not discovered; use `--load-all` or inspect the UI manually.
- `unverified` / `route_mismatch`: the site navigated/redirected to a different route.
- `unverified` / `project_heading_not_verified`: correct route, but the main content did not provide a matching heading. This may be an unsupported layout or a stale Project URL, not necessarily a nonexistent Project.
- `unverified` / `login_or_challenge_visible`: login or challenge controls were visible.

This command is read-only apart from browser navigation. With `--load-all`, it can scroll and click only a matched "Show more" control for the Projects inventory. It **never sends a message**, updates Project settings, reads Project files, selects a Project in the server/API, or persists the ID to local session state. Its output is not an account-wide Project inventory guarantee. Browser tests use local HTML and disposable profiles; no production account was exercised.

## Follow-on work

A future Project-scoped API request must establish a trusted Project binding for a local session, capture and validate project chat URLs, and re-verify project membership on continuation. Do not assume the ordinary `/c/<uuid>` route is a Project chat route. Actual model/effort selection, files, and OpenCode support remain separate milestones.
