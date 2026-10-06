# WP-06.2 — Authorized session scope and conversation continuity

**Status:** implemented; integration review required

**Objective:** construct each native run from the authenticated ChatThread and workspace, fence it with tenant/principal/thread/session IDs, maintain the session pointer used by Integral staging, and continue prior Pydantic AI message history from the encrypted StepStore.

**Owned source:** `backend/app/services/chat_providers/pydantic_ai_provider.py`, `backend/app/services/harness_sessions.py`, `backend/app/agentive/harness/runtime.py`; tests: `backend/tests/native_harness/wp_02/test_sessions.py` and `backend/tests/native_harness/wp_06/test_provider_stream.py`.

**Contract:** the provider re-reads ChatThread and checks user, workspace, and provider binding. Workspace membership is resolved before scope creation. The run ID is read from and verified against the host-created `AgentRun`; this is also the Pydantic AI run ID and the broker's authority key. The capability version hashes the durable run snapshot and the caller-filtered skill profile. The session ID maps to `HarnessSession.session_id`, is emitted in `_meta` before tool execution, and is therefore persisted by the existing chat stream loop as `ChatThread.provider_session_id`. StepPersistence's scoped store maps framework records under the same session and stores each run separately. The latest complete checkpoint is loaded as Pydantic AI `message_history` on the next turn. The session records the in-flight run before model/tool work and advances its checkpoint pointer after success. Workspace, thread, focused-resource, and page-context `ContextVar`s are bound only around the Agent run and reset in `finally`.

**Authorization revision:** the session revision includes the workspace membership role and a policy version. Fine-grained App/Track/Entry access and current capability policy remain broker-authoritative and are rechecked for every tool invocation. This revision is not a substitute for a complete ACL revision service; that remains an open substrate requirement before production scale-out.

**Skills:** the workspace-filtered Core and App skill profile is copied into a private temporary library and exposed through Pydantic AI Harness `Skills`. Projection emits only Agent Skills `name` and `description` frontmatter plus Markdown body. JV-only frontmatter fields and authority metadata are omitted; the native harness does not parse or execute legacy inheritance fields. The temporary source is removed after each turn. A profile or visibility revision rotates the session so old skill bodies do not remain in history.

**Acceptance:** offline tests execute consecutive TestModel turns through real Pydantic AI streaming and StepPersistence, verify stable session identity, and verify continuation from the preceding complete checkpoint. Tests also load a standard-only skill library through the pinned Pydantic AI Harness capability. The provider fails closed if a prior run has an unresolved tool effect or no continuable checkpoint.

**Limitations:** transactional multi-process session activation and graph reachability still require PostgreSQL qualification. Interrupted-run recovery and tool-effect reconciliation are not yet automated; the provider blocks rather than silently skipping unresolved effects. Export, deletion, and retention are separate lifecycle work.
