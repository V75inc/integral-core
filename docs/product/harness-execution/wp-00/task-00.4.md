# WP-00.4 — Adoption decision and execution graph

**Status:** implemented; decision evidence recorded

**Objective:** convert the compatibility probes and current implementation into a bounded V1 adoption decision and dependency-ordered execution path.

**Inputs:** WP-00.1 inventory, WP-00.2 composition evidence, WP-00.3 route evidence, current `backend/pyproject.toml` and `uv.lock`, WP-01/02/04/05/06/09 receipts, and implementation plan revision recorded in the ledger.

**Decision:** use Pydantic AI Harness as Integral Core's opt-in native runtime, pinned to `pydantic-ai-harness[skills]==0.30.0` with the resolved Pydantic AI 2.54.0 API. Keep LiteLLM SDK 1.101.x as the only model transport. Keep jvagent available and its existing binding/default intact during V1 implementation; removal or default switching requires completed acceptance, rollback evidence, and explicit authorization.

**V1 adoption boundary:** compose Instrumentation, ToolSearch, Planning, Skills, and StepPersistence with fresh per-run Agents, Integral's brokered tools, encrypted scoped stores, and current intelligence-plane routing/observations. The framework supplies loop and capability behavior; Core retains tenant/session identity, policy, effect/approval authority, model admission, durable usage evidence, and transcript ownership. Deferred approval waits for WP-08. Memory/compaction and authorized skill-resource behavior wait for WP-07. Sandbox/Code Mode, voice, SubAgents, unmetered fallbacks, and other auxiliary model surfaces are not V1 defaults; each remains unavailable until its owning package qualifies authority and accounting.

**Package facts:** the checked installed metadata reports MIT for Pydantic AI Harness 0.30.0, Pydantic AI Slim 2.54.0, and LiteLLM 1.101.4. This is a direct-dependency fact, not a completed transitive license inventory. The repository lock and runtime resolve on the current Python 3.14 environment; Python 3.10 and the complete supported-version matrix remain unqualified.

**Execution graph:**

1. Preserve WP-00.1 through WP-00.3 as accepted compatibility evidence; WP-00.4 records the V1 boundary above.
2. Complete foundational contracts/state (WP-01/02), durable work admission/fencing (WP-03), gateway (WP-04), usage reconciliation (WP-05), and Core admission (WP-12) before promoting the native provider beyond opt-in.
3. Close native read-only runtime and browser contract (WP-06); then skill/context (WP-07), governed writes/deferred approval (WP-08), and checkpoint recovery (WP-09).
4. Complete streaming/reconnect (WP-10), observability/analytics (WP-11), auxiliary capabilities (WP-13), external-client conformance (WP-14), package/migration/rollback (WP-15), and efficacy/security/release dossier (WP-16).
5. WP-17 pilot work is separate and remains human-authorized. Business implements commercial payer/plan/paywall behavior in its own repository; Core exposes generic admission and trusted attribution contracts only.

**Current qualification gaps:** WP-00.3 is accepted for dependency dispatch, while paid OpenAI/Anthropic route qualification and paid cost reconciliation remain unproven; no frozen efficacy corpus has been qualified; WP-03, WP-07, WP-08, WP-10 through WP-17 are not closed; WP-05.3 remains pending. The exact current gate failures and test selection are in `../ledger.yaml`. These gaps prohibit describing V1 as complete or changing the default provider.

**Verification:** repository metadata/lock inspection plus installed package metadata; no provider request or paid call. Full integration gate is recorded separately in the ledger.

**Rollback:** retain the existing jvagent provider/default and disable native selection. Do not remove the Pydantic packages until dependency ownership is reviewed; no schema rollback is implied by this decision report.
