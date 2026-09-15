---

name: integral_navigation
description: >-
  Mandatory chat linking — every cited entry, track, app, or workspace must be a
  markdown link the user can click to open in Integral. Pinned every turn.
spec: jv
requires-actions:
  - EmbeddedIntegralAction
extends: action:integral/embedded_integral_action
# always-active: linking is a cross-cutting reply rule, not a discoverable niche.
# Pin the contract every turn so list/single-reference answers never ship as
# plain text. See integral_identity / integral_filing for the same pattern.
always-active: true
tags:
  - integral
  - navigation
  - links
---

# Integral navigation — in-app links in chat

## When to use

Every assistant reply that names an Integral resource (entry, track, app,
workspace) must link it so the user can open it in one click.

## When NOT to use

- External URLs (documentation, vendor sites) — use normal markdown links with
  full `https://` URLs.
- Staged-write approval cards — they render their own shortcuts after bless.

## Grounding

- Prefer `action_url` from tool results when present — do not guess paths.
- Only link ids and titles the API returned in this turn.
- Use human titles as link text; keep raw node ids in the URL only.

## Procedure

1. When a read tool returns rows (`integral_query_entries`, `integral_list_tracks`,
   `integral_list_apps`), capture each row's `action_url`.
2. In prose and bullet lists, format every cited resource as
   `[{title}]({action_url})`.
3. If `action_url` is missing but ids are known, build a relative path:

| Resource | Path pattern |
|----------|----------------|
| Track | `/tracks/{track_id}` |
| App | `/apps/{app_id}` |
| Entry | `/tracks/{track_id}?entry={entry_id}` |
| Workspace | `/workspaces/{workspace_id}` |

4. For multi-item answers, use one markdown link per row — never plain titles only.

## Staging discipline

- Linking rules apply to summary prose after staged writes too — once consumed,
  link the created entry/track when you mention it in a follow-up turn.

## Forbidden patterns

- Plain-text entry or track titles in lists (no link).
- Absolute `http://localhost` URLs for in-app resources — use relative `/tracks/…`
  paths.
- Inventing ids or titles not returned by tools this turn.

## Example

Wrong:

```markdown
- Follow up Acme
```

Right:

```markdown
- [Follow up Acme](/tracks/n.Track.…?entry=n.Entry.…)
```
