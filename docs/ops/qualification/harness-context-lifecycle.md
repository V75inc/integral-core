# Resident context lifecycle qualification

Date: 2026-10-23. Base: PR #171 (`e6d15099` before these changes).
The initial context qualification below used Pydantic AI Slim 2.54.0 / Harness
0.36.0 at `533fdaad`. The maintenance update uses Slim 2.59.0 / Harness 0.59.0
and requalifies the native boundary; see dependency compatibility below.

## Reproduction and correction

The previous rotating-skill probe transmitted 1,263,174 bytes with 60 retained
skill bodies after 60 runs. The regression exercises a fresh production native
runtime each run, serializes/deserializes its checkpoint, and validates that
the next restored working history matches the result. Actual provider JSON is
captured through OpenAIChatModel and a local httpx mock transport. No cloud
inference or synthetic token estimates are presented as latency measurements.

| Workload | Maximum projected request characters | Final actual provider bytes | Retained skill bodies |
| --- | ---: | ---: | ---: |
| Same skill, 100 resumed runs | 33,939 | 42,949 | 1 |
| Rotating skills, 100 resumed runs | 91,616 | 53,290 | 2 |
| Same skill, 500 resumed runs | 33,963 | 43,559 | 1 |
| Rotating skills, 500 resumed runs | 91,625 | 52,601 | 2 |

The final 40 requests remain within the bounded working-window cycle. These
workloads load approximately 20 KB procedure bodies. Assertions cap each
projected and final actual request, not only an average. They also preserve
original archived checkpoint evidence while normalizing an already bloated
session on first resume.

Additional automated checks exercise 40 distinct deferred tools followed by
rediscovery/execution of the first expired tool, idempotent disclosure projection,
typed serialization/pairing, pending staging and unknown effect preservation,
oversized current read previews, pre-inference protected-input rejection, and
image accounting independent of compressed bytes. Existing provider, approval,
scope and terminal-continuation suites remain part of the gate.

## Running browser smoke

The existing isolated local deployment was restarted from this worktree at
`http://127.0.0.1:9155` (backend 4473), preserving its PostgreSQL data and model
credentials. Workspace: **Dual Routing Smoke**, not hosted V75 Inc.

One real chat completed this sequence:

1. List workspace Apps read-only: returned the three actual Apps with canonical
   links, using the configured DeepSeek v4.1 flash operational model.
2. Say “Thanks!”: GPT-4.1 Light answered; the activity table reported 1,255 input
   tokens, 32 output tokens, 1.8 seconds provider duration and 2.6 seconds total.
3. Ask which App was listed second and request its Tracks: correctly retained
   the earlier reference, resolved Reference SDK Home and linked its three
   actual Tracks. No creation or update was requested.
4. Restart the service with the final implementation and continue the same
   chat: correctly confirmed that Reference SDK Home still has three Tracks.

This verifies operational → light → operational continuity and usable read
results. It does not establish a statistically significant latency improvement,
nor does it verify the patch on hosted Dev before that release is deployed.

## Repository gates

Passed locally:

- Full default backend suite with four xdist workers, and the CI-faithful smoke
  lane (`TESTING=1`, `DEBUG=false`, no backend `.env`, xdist).
- Updated native adapter/context regressions under that CI environment,
  including pre-inference rejection of an oversized nested summary.
- PostgreSQL scope, lease, provider, design-decision and terminal-continuation
  selection: 61 passed on isolated per-worker test databases.
- Frontend: 355 files / 2,175 tests passed; production build passed.
- Staged substrate guards, pre-commit, pinned formatting/lint, TypeScript and
  wheel build/import outside the source tree.
- Frontend dependency audit under the repository's blocking severity policy.

Backend JSON lanes ran sequentially because their test-store paths share worker
names. The browser database was preserved. These are the completed individual
verification components, not a claim that an uninterrupted `make verify`
invocation finished. Frontend audit passes after the compatible Zod lock update
4.4.3 → 4.6.7; the existing moderate Vitest findings remain outside the blocking
high/critical policy.

## Limits and maintenance

Context remains finite. Required pending state or a very large current request
can exceed it; Core stops before inference rather than dropping execution
evidence or retrying indefinitely. Model token estimates and image reserves are
operational safeguards, not exact billing quantities. Detailed historical
evidence remains in the encrypted scoped archive and can grow with legitimate
activity; this fix bounds model working context, not archive storage.

The generated native deferred-catalogue projection and history/disclosure APIs
are qualified against the pinned library. Dependency changes must rerun these
tests. Use `INTEGRAL_CONTEXT_SOAK_RUNS=500` for the extended probe. No new
worktree, destructive migration, hosted deployment or merge is part of this fix.

## Dependency compatibility

The maintenance update raises the published runtime pins to Pydantic AI Slim
2.59.0 and Harness 0.59.0, and the filelock floor to 4.2.1 (lock resolves 4.2.4).
These address the backend advisory gate without suppressions. The relevant
upstream records are [Harness 0.59.0](https://pypi.org/project/pydantic-ai-harness/0.59.0/)
and the [filelock advisory](https://github.com/tox-dev/filelock/security/advisories/GHSA-v4x8-3wr5-3wq8).

Skills now receive a host-selected `LocalWorkspaceBackend` only for reading the
authorized skill libraries. No filesystem/shell capability is added to the
resident agent. Regression fixtures use the public async `Skills.load` catalog
instead of private deferred-capability fields.

The bounded catalog projection selects the native typed deferred-capability
instruction source. Native queued usage warnings are captured on a disposable
projection and attached as dynamic instructions, preserving real user text and
task boundaries. When the upstream retained-token floor would retain an
indivisible oversized completed response, Core asks native compaction to summarize
that completed history, preserving the first user constraint and restoring typed
skill evidence. Current work and pending effects are excluded from that history;
summary and final-request admission remain enforced.

Completed implementation plans and raw QA artifacts have been retired from the
reader tree. Git preserves their history; maintained behavior belongs to the
[chat contract](../../backend/ai-chat.md), [model configuration](../../backend/model-credentials-byok.md)
and [native Home SDK](../../platform/app-home-sdk.md).

The updated 100-run probes preserve exact checkpoint/result equality and bound
actual serialized provider requests:

| Workload | Runs | Maximum projected characters | Final provider bytes | Retained skill bodies |
| --- | ---: | ---: | ---: | ---: |
| Same skill | 100 | 30,268 | 32,685 | 1 |
| Rotating skills | 100 | 92,155 | 53,123 | 2 |

Native message-count compaction is synchronized with the public working history
before the tier scheduler runs. Settled historical retry feedback whose original
call was summarized is removed with that call; Core never invents a successful
tool outcome. This keeps the native provider-valid checkpoint equal to the
returned working history. The original 500-run measurements above describe the
initial dependency versions, not this maintenance update.
