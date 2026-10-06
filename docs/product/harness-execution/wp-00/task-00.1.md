# WP-00.1 — Baseline and compatibility inventory

**Status:** accepted

**Objective:** establish a reproducible, secret-safe baseline and map existing Core provider, WorkItem, tenant, skill and accounting surfaces to the Harness proof.

**Baseline:** branch `feat/pydantic-ai-harness-v1`, commit `b0934bd5d39e7b07d714cb9c8d68ff26835c61b8`; 157 pre-existing changed/untracked paths were present at branch creation. Their aggregate worktree fingerprint is recorded in `../ledger.yaml`. Preserve them. Backend environment: Python 3.14.3, uv 0.11.3, jvagent 0.1.8rc20, jvspatial 0.1.1, LiteLLM 1.101.4; Pydantic AI and Pydantic AI Harness are not installed in the backend environment.

**Owned files:** `docs/product/harness-execution/ledger.yaml`, this task brief, `docs/product/harness-evidence/wp-00/task-00.1-inventory.md`. No production source, dependency, lock, provider default or configuration edits.

**Read:** root and `backend/app/agentive/AGENTS.md`; this plan §§2–5, 6.1–6.5, 7, 10, 13; the intelligence-plane specification; chat provider protocol/registry/jvagent adapter, streaming, chat thread, WorkItem execution/recovery/outbox/approval, AgentRun/RunStep, tool broker, standard skill parser/compliance and package configuration.

**Checks:**

- Verify branch/HEAD and compare the status manifest to the recorded fingerprint without exposing file contents or secrets.
- Inventory the live provider registration/startup/cancel/stream/session path and map its existing event protocol.
- Inventory durable work/approval/lease/fence and usage/capture implementations; distinguish existing production capability from the plan’s target.
- Record exact currently installed dependency versions and the candidate Harness/Pydantic API and dependency requirements in the evidence report.
- List each mandatory WP-00 capability with its documented export, unknowns, isolation boundary and next probe. Keep facts, assumptions and unverified behaviors visibly distinct.

**Acceptance:** evidence report identifies the baseline and existing dirty state, correct integration points, exact available dependency facts, all mandatory probe questions and missing external inputs. No model call is made and no repository dependency changes. A fresh-context review confirms inventory paths and claims against the checkout.

**Handoff:** accepted inventory becomes input to WP-00.2. Live model/provider claims remain pending until the scoped real-route probe in WP-00.3.

**Stop:** source conflicts with the baseline, relevant dirty edits cannot be attributed, a mandatory capability appears to lack any bounded probe, or a secret would be needed in an artifact. Record the blocker and continue independent read-only inventory.
