# ChatGPT access errors and page capabilities (v0.4.22)

This is an **experimental** public-UI adapter, never a supported replacement
for the provider's authorized API. Provider permissions still apply.

## Read-only access-error classification

An unexpected Project chat route is not automatically a stale local ID.
The app may render a page-level error, for example "You cannot access this
chat" or "This chat is not available". The detection code checks only
visible headings/alerts in `main` or short `main` error pages **when no chat
turns are rendered**. It never treats a phrase quoted in an existing message
as a page error and never returns page text, HTML or tokens.

When a route changes, a bounded extra check allows SPA error text to render
before OpenAgent returns a generic route mismatch. These codes are
informational only:

- `conversation_access_denied`: the website says this chat is inaccessible;
- `conversation_unavailable`: the website says the chat is unavailable;
- `reconcile_route_mismatch`: route did not match and no known page-level
  error was confirmed.

There is **no automatic retry or new-chat fallback**, and no attempt to
bypass authentication or account-level access controls. The current Project
reply repair remains blocked until the real answer can be unambiguously read.

## Control presence, not implemented functionality

`browser chatgpt-controls --headful` reports only coarse DOM control presence.
A detected model picker, effort picker, file input, attachment button, tools
menu or voice button is not proof that the account can use that feature.
No controls are clicked and no file paths, chat text or credentials are returned.

Next work: store and verify exact upstream slugged route; select model/effort
from verified UI labels with explicit user choice; opt-in attachments with
strict file validation and success markers; Project resource inventory and
upload/download lifecycle; OpenCode/9Router API capability negotiation.
