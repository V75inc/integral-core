# W6.3 integrated-candidate browser recheck

**Date:** 2026-09-29
**Candidate:** `b9a08b3d06c918ede3c027da265ff7693d0ac3e8` (`codex/local-integration-candidate`)
**Purpose:** Recheck the effective workspace/App skills panel after the App-focus reset and scope-switch changes.

## Setup

A disposable local PostgreSQL 14 cluster, candidate API, and Vite frontend were used. A fresh local-only account was created through the browser UI; email verification was skipped as the application permits. The API ran with `INTEGRAL_AGENT_KEY_MODE=byo_strict`, an empty OpenAI API key, and an empty Ollama key. No model/chat operation was submitted and no provider call was made.

## Browser recheck

The first run rendered “Could not load the effective turn catalogue,” but its API response was not captured and therefore did not establish a reproducible defect. On the same candidate SHA, I repeated the complete flow with a newly created disposable account through the browser UI. After skipping email verification, the personal workspace's Settings > AI Skills panel loaded **16 skills and 122 tools**, matching the separately authenticated endpoint result. The app-focus selector showed Workspace and the panel listed the core skills as available. No browser console errors were present.

This successful browser run demonstrates the user-session and workspace-scope path for the effective catalogue on this candidate. The initial failure remains unexplained and was not reproduced; it may have been transient. No source change was needed. No production account, external workspace, OpenAI API, or Ollama provider was used.

## Result and limit

**W6.3 browser recheck passed.** The result is limited to this local fresh-account flow and does not replace other release gates or the separate Product Owner review. Both chart contributing-record dialogs were already browser-checked on this candidate and displayed loaded rows with standard content gutters and no clipping; their `Modal.Body` padding contract also has a frontend regression assertion.
