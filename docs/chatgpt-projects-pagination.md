# ChatGPT Projects — best-effort sidebar pagination (v0.4.13)

## Why five isn't all

Windows v0.4.11 enumerated five project rows. The implementation stopped as
soon as *any* rows were hydrated, and it never scrolled or expanded any UI.
Newer sidebar layouts can defer additional rows until a scroll or explicit
expansion. Other Projects may remain unavailable in this sidebar altogether.

## Commands

```powershell
# Passive snapshot; no clicks/scrolling:
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-projects --headful --json --wait-seconds 12

# Explicit bounded UI traversal; may scroll and click *Projects* expansion:
.\.venv\Scripts\python.exe -m openagent.cli browser chatgpt-projects --headful --load-all --json --wait-seconds 12 --max-steps 30
```

`--max-steps` accepts 1–60 (default 15), and initial hydration
`--wait-seconds` accepts 0–30 (default 5). Every scan creates a dedicated
tab, not a chat. The opt-in scan will interact only with a sidebar/navigation
scroll container containing project markers and with visible, enabled buttons
whose accessible label explicitly names Projects (or whose generic label is
inside a confirmed Projects section). It does not click a Project row, open a
conversation, read nested chat titles/files, click Send, or call private APIs.

## Read the result accurately

With `--load-all --json` the result is an object containing `projects`,
`initial_count`, `discovered_count`, `scroll_steps`, `expand_clicks`,
`complete: false`, and a warning note. The `projects` array contains only
DOM-observed project IDs. Their overview URLs are still *derived candidates*.

**This is not exhaustive enumeration.** Even if scanning stops at the bottom,
the web app may only expose a subset of a much larger account inventory.
The command never infers a total or manufactures additional project IDs.
To investigate a remaining discrepancy, use the normal ChatGPT UI's Projects
view or search, and compare the visible lists. Do not share private project
names or IDs unless needed; redact before pasting diagnostics.

No live ChatGPT account/project routing behavior is certified by local
fixture tests. Project selection, project-specific chat scope, effort/model
control, attachments, and OpenCode/9Router remain separate milestones.

## v0.4.13: generic “Show more” controls

Some 2026 sidebar designs put `Show more` next to the Projects heading and
observed Project rows without a `section` or `aria-label="Projects"` wrapper.
`--load-all` now accepts that grouped layout, but only if the button follows
observed Projects and is within their local DOM group; a generic More button
somewhere else in navigation still fails the safety check.

The `more_controls_seen` JSON field is the maximum count of accessible “Show
more” candidate buttons seen during the scan, **before** scoping and
ambiguity checks. `expand_clicks=0` with `more_controls_seen>0` means buttons
were present but not approved by the conservative matcher. `0/0` can mean the
site uses a different control name/role or the Projects UI was not mounted.
Avoid sharing full page HTML or browser traces with session data; a redacted
screenshot of the relevant sidebar is a safer next diagnostic.

The scanner never proves it retrieved every Project (`complete: false`),
and never selects Project context, accesses files, or submits messages.
