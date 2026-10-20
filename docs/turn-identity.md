# Turn identity and response finalization (v0.4.7)

## Problem addressed

Browser chat UIs may virtualize long conversation histories: an old answer
node is removed at the same time a new answer is mounted. Relying only on
`assistant_message` counts can miss a new answer if the number of mounted
nodes stays constant. Worse, a stale Copy action from an old answer can look
like a completion signal for a new answer.

## Strategy

- Capture a bounded snapshot of existing assistant turns *before submitting*.
- Prefer stable `data-turn-id` or `data-message-id` values from an assistant
  node or its closest `article` ancestor.
- Consider an unseen identity new even if the mounted assistant count has not
  increased, **only** if the pre-submit snapshot had known identities.
- If IDs are unavailable, require an increase in assistant count. If
  virtualization replaces one unidentifiable answer with another at the same
  count, wait and eventually return `generation_timeout`.
- Tie stable-text observations to the selected new identity; reset the
  observations if that identity changes or disappears.
- Require a Copy action scoped to the new turn, absence of pending markers,
  absence of a visible Stop control, and repeated stable text observations.
- No implicit retries after a submission with an unknown outcome.

The snapshot scans at most 256 mounted assistant nodes. The scan size is a
reliability bound, not a guarantee that every UI uses the same identity format.

## Testing

The tests include no access to real user accounts, cookies, provider services,
or private website endpoints. The Chromium fixtures simulate (a) a new logical
turn replacing an old DOM node while the count remains constant, and (b) an
identically sized replacement with no stable ID, which must time out.

## Limitations

- This is only one stage of reliable conversation support. It does **not**
  establish upstream conversation ID persistence, chat/project resume,
  interruption, model effort selection, native tool calls, or streaming.
- A DOM attribute is evidence, not an authenticated upstream session identity.
- Other live website layouts may require different anchors, and only
  explicitly permitted provider interactions should be tested.
