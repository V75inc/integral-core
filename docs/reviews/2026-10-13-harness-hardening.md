# Native harness disclosure hardening

## Implemented scope

Based on Core `8af5d9ca1605154435966ac0e6491ff3330cdf50`, on the isolated `codex/harness-hardening-qualification` branch. The screenshot probes reproduced reconstructive prompt inventories/stories and unnecessary workspace discovery; they did not demonstrate unauthorized mutations or credential disclosure.

- **Host support contract:** explicit private-introspection requests, selected public product help and general permission explanations use fixed server text. The provider validates the authenticated thread, workspace and running Core execution. Durable turns also validate their WorkItem scope and recheck current authority at each emitted event. No model credentials, workspace tools, private checkpoint context, title generation or harness session pointer writes are needed for this path. The normal host event consumer persists its public transcript.
- **Operational model policy:** rejects private instruction reconstruction through inventories, stories, translations, encoding, incremental requests and claimed privilege. Public product information and authorized user records remain available. A fixed public support answer can satisfy the disclosure capability without forcing discovery; arbitrary operational answers still face the existing discovery gate.
- **Literal-copy defense:** a pinned-framework after-model hook inspects text and tool arguments before dispatch/history processing. A substantial normalized literal copy of protected host instructions becomes the fixed public refusal. An output validator also checks the settled answer. User attachment/page context, current receipt/approval facts and time context are excluded from provider fingerprints to preserve ordinary scoped record responses. Streaming candidates remain buffered until the sanitized settled result is available.
- **History search projection:** removes system prompts, request instructions, instruction updates, private reasoning and retry-control text from the scoped searchable transcript. It sanitizes literal instruction copies in old assistant text/tool calls. The underlying encrypted restoration checkpoint is preserved. Source scope/principal/session fences remain in the underlying `ScopedStepStore`.
- **Portable qualification:** capability-search tests now resolve canonical checked-in resident assets instead of depending on an ignored wheel-build directory. Context tests verify the wrapped source still holds the exact scoped store.

No role, approval, entitlement, connector or resource policy is relaxed. Invariants preserved: I-SUBSTRATE-01, I-ROLE-01/02, I-APPROVAL-01/02 and current WorkItem authority/effect fences. Core changes remain domain neutral.

## Evidence and acceptance

New deterministic cases cover the supplied screenshot patterns, raw/inventory/story/translation/encoding and claimed administrator requests, benign user prompt operations, authenticated thread/workspace/run denials, interactive durable execution, cancellation, unchanged operational session identity, canonical public role names, literal synthetic canaries, unsafe tool arguments, actual seven-character stream fragmentation, checkpoint assistant response sanitization and private search projection. Existing harness, broker, scope, permission, approval and PostgreSQL transaction suites qualify authority independently of the model.

Final counts and diagnostics: `tasks/implementation-qualification.md`; JUnit evidence in `tasks/evidence/implementation-postgres-final.xml`. Earlier baseline live-model outcomes remain in `tasks/live-results.json`; they are evidence of the original gaps, not of a deployed fix.

## Explicit limits

The deterministic recognizer is intentionally narrow and English-oriented. Contextual and multilingual extraction that does not match it relies on the model following the disclosure policy. The fixed-response exemption permits a safe refusal but cannot force a provider that insists on tool choice to recognize every such request before discovery. A sixteen-word literal-overlap guard does not recognize arbitrary semantic paraphrases, encodings or short secret fragments. It is defense in depth, not a semantic confidentiality proof.

Private instructions must never contain secrets; credentials remain in the server credential plane. Public source code and tool schemas are not confidential. Normal checkpoint restoration retains encrypted private host inputs; the new projection protects conversation search, not access to legitimately authorized private administrative/debug infrastructure. External tracing/export configuration requires its own operational review.

The update has not been deployed, committed or pushed. The existing Business browser stack and PRs are unchanged. Post-update live-provider/browser and alternate-provider qualification remains a deployment gate; source tests do not establish those outcomes.
