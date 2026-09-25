# Hybrid page context → SESSION CONTEXT UI ROUTE

**Date:** 2026-09-18 (updated 2026-09-24)
**Status:** accepted — utterance stub superseded by jvagent ADR-0056
**PR:** integral-core #7 (hybrid tool); follow-up removes utterance preamble

## Problem

Every chat turn prepended a full `page_context` block into the **user
utterance**. That bloated tokens and treated the on-screen App as *topic*
(e.g. personal expenses answered from Sales).

## Decision (current)

1. **SESSION CONTEXT UI ROUTE** — client `page_context` rides
   `visitor.data` into jvagent `render_session_context` (ADR-0056): compact
   kind / labels / ids / path / crumbs + optional-focus authority line.
   **Not** prepended onto the utterance.
2. **On-demand tool** `integral_get_page_context` — full snapshot incl.
   `visible_data` from turn ContextVar or `ChatThread.last_page_context`.

## Superseded

- Always-on utterance stub (`BEGIN_CONTEXT_DATA kind=page_context`)
- Soft/minimal relevance gate on that stub (channel was wrong; gate optional
  only if UI ROUTE still overfits in eval)

## Non-goals

- Live DOM re-scrape mid-turn
- Changing entity_refs inject
- Wiring jvagent's messenger `PageContextInteractAction` (different schema;
  response parameters, not SESSION CONTEXT)

## Success

Greeting turns stay small and transcript stays clean. “What’s on this page?”
uses `integral_get_page_context`. Unrelated domains ignore focused App ids.
