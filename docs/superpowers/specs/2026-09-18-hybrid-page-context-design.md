# Hybrid page context → host-rendered SESSION CONTEXT extra

**Date:** 2026-09-18 (updated 2026-09-24)
**Status:** accepted — utterance stub superseded; jvagent ADR-0056 is schema-free
**PR:** integral-core #7 (hybrid tool); follow-up removes utterance preamble

## Problem

Every chat turn prepended a full `page_context` block into the **user
utterance**. That bloated tokens and treated the on-screen App as *topic*
(e.g. personal expenses answered from Sales).

## Decision (current)

1. **Host-rendered UI ROUTE** — `build_ui_route_session_extra(page_context)`
   produces prose; send as `visitor.data["session_context_extra"]`. jvagent
   appends it to SESSION CONTEXT without parsing Integral fields (ADR-0056).
2. **Tool snapshot** — `page_context` still on `visitor.data` / thread stash
   for `integral_get_page_context` (visible lists, full JSON). Harness never
   reads that schema for prompting.

## Superseded

- Always-on utterance stub (`BEGIN_CONTEXT_DATA kind=page_context`)
- Soft/minimal relevance gate on that stub
- jvagent parsing Integral `page_context` inside `session_context.py`

## Non-goals

- Live DOM re-scrape mid-turn
- Changing entity_refs inject
- Framework knowledge of Apps / Tracks / dashboards

## Success

Greeting turns stay small; transcript clean. UI ROUTE in system prompt.
Unrelated domains ignore focused App ids. Tool still grounds “what’s on screen.”
