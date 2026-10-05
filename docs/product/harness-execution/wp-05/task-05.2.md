# WP-05.2 — Usage reconciliation summary

**Status:** implemented; awaiting integration review

**Objective:** aggregate physical model request facts without hiding duplicate events, incomplete token reports, unavailable cost, or uncertain provider outcomes.

**Owned source:** `backend/app/agentive/harness/contracts.py`, `backend/app/agentive/harness/model_observations.py`; tests: `backend/tests/native_harness/wp_05/test_model_observations.py`.

**Contract:** group by Core physical `request_id`, verify route and scope do not change, count one completed response per request, and keep dispatches with no response in an unresolved count. Add only reported token counts and provider cost; use `Decimal` for cost values. Explicitly expose token completeness and cost completeness. These quantities are telemetry/cost evidence, never customer prices, entitlements, or invoices.

**Acceptance:** duplicate observations do not double count; one completed and one unknown request show partial counts and incomplete totals; known provider cost is summed with decimal precision; empty/missing measurements are never promoted to a confirmed zero cost.

**Limitations:** reconciliation currently folds local durable observations only. It does not ingest or correlate LiteLLM callbacks/provider request logs, estimate missing prices, settle unknown outcomes, or persist aggregate rows. Business owns pricing and customer billing.

**Handoff:** WP-05.3 binds callback/source evidence and delayed reconciliation; WP-12 exposes generic admission/usage contracts to Integral Business without importing commercial policy into Core.
