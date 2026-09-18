# Hybrid page context (stub + tool)

**Date:** 2026-09-18
**Status:** accepted for implementation
**PR:** integral-core #7

## Problem

Every chat turn prepended a full `page_context` block (URL, metadata, visible
entries/tracks) into the agent utterance. Mission Control dumps ~20 entry
previews on every "hi", bloating tokens whether the model needed them or not.

## Decision

**Hybrid:**

1. **Always-on stub** in the utterance — `url`, `page_kind`, breadcrumbs,
   focused ids (+ focused entry title when known), capped metadata, and a
   one-line pointer to the tool when lists exist.
2. **On-demand tool** `integral_get_page_context` — returns the last client
   snapshot (incl. `visible_data`) from the turn ContextVar or
   `ChatThread.last_page_context`.

## Non-goals

- Live DOM re-scrape mid-turn
- Changing entity_refs inject
- Wiring jvagent's messenger `page_context` InteractAction (different schema)

## Success

Greeting turns stay small. “What’s on this page?” uses one tool call and
returns grounded visible lists.
