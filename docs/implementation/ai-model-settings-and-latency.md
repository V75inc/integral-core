# AI configuration and agent response latency

Baseline: Core 0.1.1rc30 (`0f198e82b328650c4464e5b1066c5246ff561889`).

## Configuration

Settings → AI Models now separates provider connections from model profiles.
A connection owns its encrypted secret and immutable provider/endpoint identity.
Profiles select a connection, model ID, and validated generation settings. Editing
or testing a saved connection does not require sending its secret back to the
browser. Changing provider or endpoint requires a new connection. Optional voice
transcription remains independent; switching the default model preserves its key.

Existing active credentials appear as a deterministic, idempotent connection and
profile snapshot. Reading settings never rewrites the active credential. Explicit
activation retains the legacy credential projection for existing integrations.
Private scalar configuration uses `Object` (I-GRAPH-02), with same-user ownership
checks and bounded lists. No graph nodes, domain hooks, or additional resident
harnesses are introduced. Writes live in services (I-CRUD-01); Core remains domain
neutral (I-SUBSTRATE-01 / I-EXT-01).

The in-use summary reflects the current workspace owner's configuration and
funding policy. A member's personal settings do not override another owner's
workspace. Platform-only deployments disable personal changes. Strict BYOK without
an active credential displays missing setup rather than claiming platform fallback.

A workspace owner can select an owned profile for an idle conversation or reset it
to the account default. The thread stores only a profile ID. Field-only atomic
CAS protects the existing active-work admission pointer. Routing resolves the
profile server-side, rechecks the workspace role and route before physical model
dispatch, and never saves a transient conversation override as the account default.
Key usage timestamps use throttled field-only updates. Account deletion purges
connections and profiles alongside the legacy credential. Configuration edits use
revision checks so concurrent validation cannot restore stale secrets.

The page leads with the effective workspace model, then Models and Providers
tabs with compact saved rows. A focused right drawer uses the canonical padded
body and sticky action footer; it fills the mobile viewport. Voice input and
deployment ceilings are secondary disclosures. Keyboard tab navigation, permission
explanations, loading, empty, failure and partial-metadata states are covered.

### Controls

Supported controls are advertised from local LiteLLM metadata, with conservative
model-specific effort choices. The browser and server share a typed, bounded
contract; unsupported options are rejected rather than silently dropped.

- Reasoning effort or an Anthropic thinking token budget, where supported.
- Maximum output tokens, temperature or top-p, and verbosity, where supported.
- Local Ollama context window, with headroom beyond the effective output budget.
- Request and idle timeouts, bounded by deployment ceilings.
- Per-profile model request, tool-call, total token, and wall-clock turn limits.
  These can reduce operator ceilings, never increase them. Adaptive routing model
  usage counts against the same turn budget. The turn deadline includes preparation.

Retry/fallback and general compaction policy remain runtime/operator controls.
Unset values inherit provider/deployment defaults. Context window configuration is
local-Ollama-specific; the UI does not imply it can resize a hosted model's context.
Connection verification proves provider access, not model availability, inference
quality, tool support, or acceptance of every parameter. Custom endpoint reasoning
controls are not inferred from an arbitrary model name. OpenAI effort distinctions
follow the [provider's GPT-5.2 guidance](https://developers.openai.com/cookbook/examples/gpt-5/gpt-5-2_prompting_guide).

The authenticated APIs under `/users/me/` expose `ai-configuration`,
`model-capabilities`, `model-connections` (save/test/remove), `model-profiles`
(save/activate/remove), and `conversation-model` (read/set/reset). Responses whitelist
safe fields; secrets remain encrypted server-side. Removing referenced connections,
the active default, or profiles with conversation overrides is rejected.

## Response performance

Two avoidable costs were identified and addressed:

1. Durable chat previously shared one general worker. A bounded chat-only worker
   pool allows independent conversations to proceed concurrently, while existing
   per-thread admission, leases, principal authorization, and cancellation remain
   authoritative. App jobs and other lifecycle work keep separate workers.
2. Deferred capability discovery repeatedly embedded unchanged tool descriptions.
   A bounded LRU reuses immutable description vectors keyed by exact document hash
   and embedding model. Query embeddings, current authorized candidate membership,
   ranking, permission checks, and tool results are not cached.

`INTEGRAL_CHAT_WORKERS=4` is the default, configurable from 1 through 16 **per API
process**. Operators should account for replica count and provider capacity; a
single local model may benefit from a smaller pool. This is concurrency, not a
promise that an individual provider responds faster.

### Measurement

Content-free timing now distinguishes server enqueue-to-worker delay, preparation,
first provider output, first tool request, tool execution start, first operational
tool, first published answer text, and provider completion. Tool discovery is
excluded from the first-operational-tool marker. Browser delivery separately records
first answer text, first tool activity, and receipt of durable settlement.

Server markers persist through the existing allowlisted durable event/transcript
path. Browser markers are session-local and include network/delivery delay; replay
measurements describe the current attachment, not the original request. Preparation
is a duration; server first-occurrence markers include queue/setup offset when a
valid enqueue timestamp exists. Provider completion precedes durable settlement.

Answer validation buffering remains in place. Provider first-token timing and the
first published answer are deliberately distinct. Removing buffering without
preserving validation/retry and checkpoint guarantees would expose rejected output.
No claim of production end-to-end latency improvement is made from the microbenchmark.

A reproducible offline microbenchmark is available:

```bash
cd backend
HF_HUB_OFFLINE=1 .venv/bin/python scripts/benchmark_capability_discovery.py
```

On the local warm MiniLM encoder, 40 synthetic definitions and five samples:

| Discovery preparation | Median | Maximum |
| --- | ---: | ---: |
| Re-embed unchanged definitions | 36.34 ms | 37.75 ms |
| Reuse definition vectors | 3.10 ms | 3.13 ms |

This measures only local embedding/ranking work, with identical candidate metadata;
it excludes provider inference, cold encoder load, graph reads, network and tools.

## Activity presentation

The running fallback says “Working on that for you.” The expanded introduction is
“Activity, tool results, and model usage are shown here.”

Tool cards retain safe outcome, returned-item count, and elapsed time rather than
always displaying a generic completion sentence. Counts describe returned items,
not total matches. Summaries allow only fixed outcomes, bounded counts and duration.
Arguments, free-form tool output, record contents, identifiers, URLs and private
reasoning remain outside this public projection. External MCP tools receive a generic
outcome unless a safe structural contract exists. Existing public error diagnostics
retain precedence. Summaries survive transcript replay.

## Session continuity

The existing client already tracked human activity and refreshed proactively.
Reproduction tests identified three failure paths: a throttled expiry timer could
pause a recently active page before renewal, an in-flight refresh crossing expiry
could lock the page, and a temporary proactive-refresh outage could lock a still
valid access token. These are reproducible candidates for tester reports, not a
claim that production logs proved a particular cause.

Recently active pages now await renewal at expiry before sending API/chat requests.
Transient proactive failures retry within the remaining access lifetime. Real idle
locks, authoritative refresh rejection and explicit sign-out still require recovery.
Polling does not count as human activity. Same-origin Web Locks serialize refresh
rotation across tabs where supported; the client adopts a newer valid token only
for the same stored account, guards late responses across sign-out/new login, and
publishes token updates to React consumers and other-tab listeners. Issued TTL is
retained across page reloads to bound the activity window. Browsers without Web
Locks retain per-window single-flight behavior.

## Qualification

Regression coverage includes ownership, encrypted-key retention and retesting,
legacy snapshots, profile activation, voice retention, transient conversation routing,
stale-write rejection, provider SDK payload mapping, budget enforcement, deadline
cleanup, concurrent chat workers, discovery cache isolation, durable summary
projection, browser receipt timing, and settings interaction.

The isolated browser preview uses a disposable PostgreSQL database and synthetic
local-provider fixture. Save/reload, saved connection testing, and conversation
selection/reset are exercised without calling a paid model or using personal keys.
Provider-specific live latency and task-quality comparisons remain necessary to
quantify real workload improvements; the added timing separates those causes.

Final local validation on this branch:

- Full default backend suite (`make test-backend`, six xdist workers): passed.
- Frontend suite: 343 files / 2,049 tests passed; the final settings icon change
  also passed both Settings page and model configuration tests (16 tests).
- Substrate guards, pinned formatters/lint, pre-commit hooks and TypeScript: passed.
- Production frontend build and reproducible wheel/import check: passed.
- Selected PostgreSQL configuration, routing, budget, admission and durable-event
  regressions: 191 passed.
- Browser inspection: 1440×1000 and 390×844, plus normal desktop viewport;
  padded model/provider editors, sticky actions, responsive rows, focus wrap,
  Escape/return focus and saved configuration reload verified.

The required CI-faithful smoke gate is checked again immediately before push.
