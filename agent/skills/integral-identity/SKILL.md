---
name: integral-identity
description: Resolves the acting Integral user's identity (id, display name, email). For workspace orientation maps (apps/tracks), activate integral-workspace.
allowed-tools: integral_whoami
---

# Integral identity — SOP

## Workflow

1. Use `integral_whoami` whenever you need to confirm which Integral
   user the conversation is acting on behalf of, or to surface their
   id, display name, and email. (It returns identity only — not the
   user's workspaces, apps, or memberships.)
2. Treat this as the canonical identity probe — do not infer the user's
   name or email from prior turns.
3. For orientation — "what apps / tracks can I see", "what's my scope",
   "what can I do here" — activate **integral-workspace** (via
   `use_skill`) and follow that skill's list/read procedure. Do not
   invent workspace inventory from memory or page chrome alone.

### Constraints

- The tool resolves identity from the active session — it takes no arguments.
- If the response contains an `error` field, surface it verbatim and do not
  pretend the call succeeded.

## When to use this (vs delegate)

- **Use here:** confirm the acting user (`whoami`).
- **Delegate to integral-workspace:** flat orientation maps, creating /
  renaming / sharing apps or tracks, reading a track's schema.
- **Delegate to integral-entries / integral-insights:** track contents or
  "what's been happening".

## Procedure

1. **Identity** — `integral_whoami`. Report only returned fields
   (`id`, `email`, `display_name`).
2. **Reach (optional)** — when the user wants the map, not just the name,
   `use_skill` → integral-workspace, then list apps/tracks per that SOP.
3. **Present** — name the user; if orientation ran, summarize reachable
   apps/tracks with markdown links (see integral-navigation).

## Scope

Identity resolution for the acting principal. It does **not** list apps or
tracks (that is integral-workspace), mutate structure, or list entries.

## Staging discipline

Pure read — `integral_whoami` returns immediately with no staged mutations.

## Staging discipline

Pure reads only — identity and orientation tools return immediately with
no staged mutations and nothing to bless.

## Grounding

- Only report fields actually returned by the tool (e.g. `id`, `email`,
  `display_name`).
- If the call returns an error envelope, state the error code and stop —
  do not fabricate identity.

## Example

> **User:** "Who am I?"

1. `integral_whoami` → read `display_name`, `email`, `id` from the
   response only.
2. **Present:** "You're signed in as *Jane Doe* (jane@example.com)."

> **User:** "Who am I and what can I see here?"

1. `integral_whoami` as above.
2. `use_skill` → integral-workspace → list apps/tracks.
3. Present identity + a short reach summary with links.
