# W6.3 integrated-candidate browser recheck

**Date:** 2026-09-29
**Candidate:** `b9a08b3d06c918ede3c027da265ff7693d0ac3e8` (`codex/local-integration-candidate`)
**Purpose:** Recheck the effective workspace/App skills panel after the App-focus reset and scope-switch changes.

## Setup

A disposable local PostgreSQL 14 cluster, candidate API, and Vite frontend were used. A fresh local-only account was created through the browser UI; email verification was skipped as the application permits. The API ran with `INTEGRAL_AGENT_KEY_MODE=byo_strict`, an empty OpenAI API key, and an empty Ollama key. No model/chat operation was submitted and no provider call was made.

## Browser observation

Settings > AI Skills rendered the panel, but it showed **“Could not load the effective turn catalogue.”** The core-skill list still rendered. A separate authenticated GET to `/api/agentive/skills/effective` returned HTTP 200 for a different disposable account created directly through the local signup API, with 16 skills, 122 tools, and no accessible Apps. That request does not prove the browser account's session/scope path works.

## Result and limit

**Incomplete.** The mismatch between the browser-session panel and the direct API request is not yet explained. This is a candidate-level integration finding, not evidence that the endpoint or W6.3 is closed. Retest the browser flow after identifying the session/scope discrepancy. No production account, external workspace, OpenAI API, or Ollama provider was used.
