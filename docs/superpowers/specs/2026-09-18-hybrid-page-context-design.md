# Hybrid page context → InteractAction parameter (messenger pattern)

**Date:** 2026-09-18 (updated 2026-09-24)
**Status:** accepted
**PR:** integral-core #7 (hybrid tool); follow-up removes utterance preamble

## Problem

Every chat turn prepended a full `page_context` block into the **user
utterance**. That bloated tokens and treated the on-screen App as *topic*.

## Decision (current)

1. **`integral/ui_route_interact_action`** — reads `visitor.data["page_context"]`,
   contributes an **orchestration** parameter (factual UI focus + optional-focus
   framing), same pattern as jvagent messenger `PageContextInteractAction`.
   **Zero jvagent core change.**
2. **Tool** `integral_get_page_context` — full snapshot incl. `visible_data`.

## Superseded

- Utterance stub (`BEGIN_CONTEXT_DATA kind=page_context`)
- Soft/minimal utterance gate
- jvagent `session_context_extra` / parsing Integral `page_context` in SESSION CONTEXT

## Non-goals

- Live DOM re-scrape mid-turn
- Framework knowledge of Apps / Tracks
- Putting client labels into SESSION CONTEXT (system authority)

## Success

Transcript clean. Model sees focus only as a conditional HOW parameter.
Unrelated domains ignore focused App. Tool grounds “what’s on screen.”
