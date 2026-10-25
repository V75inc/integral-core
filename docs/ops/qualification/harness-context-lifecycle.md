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

## Business RC33 regression replay (2026-10-25)

A separate loopback Business deployment reproduces the deployed RC33 source
(Core `7e3c5064`, Business tree `8cb20977`), with an isolated PostgreSQL database
and seven installed Apps. It uses saved GPT-4.1 Light / DeepSeek v4.1 flash Heavy
profiles, automatic routing and provider-default generation settings. The
workspace is synthetic, not a copy of hosted customer data: twenty employees
have twenty dated monthly compensation records, plus the App-created starting
records. Two salaries total GYD 550,000; the remaining eighteen are zero. No pay
runs, payslips or accounting journals are seeded.

The published RC33 runtime reproduced a host context overflow on the fourth
question of a normal conversation (28,179 estimated input tokens against a
25,396 admission limit). Three context regressions also fail against the
immutable RC33 source: optimistic native usage hiding current wire size,
duplicated current capability activation after historical summary, and stale
provider usage rejecting already-cleared reads.

Corrections use one wire-size floor for compaction and admission, persist
measured context reclaim without changing billing usage, and retain current
capability activation once. Completed prose has a smaller recent window while
active work, unknown effects and approval receipts remain separately protected.
Mixed App read/write wrappers now carry per-call trusted read metadata; a read
can be cheaply cleared without making a write through the same tool clearable.
The full read result remains archived. Schema discovery keeps broad workflow
read tools deferred, rather than revealing an entire read catalogue on load.

An intermediate candidate completed template lookup, a brief acknowledgement, App
selection, actual-payroll inquiry, exact configured-salary calculation and a
second acknowledgement. It returns the actual Receipt entry link, correctly
reports no recorded paid payroll, calculates GYD 550,000 with 20/20 coverage and
18 zero salaries, and links the compensation Track. The final acknowledgement
routes back to Light. No write was requested or executed.

One six-turn sample measured 32.7, 2.7, 11.3, 39.9, 26.6 and 2.6 seconds wall
time respectively. First operational-tool starts on the four substantive turns
were 7.8, 7.7, 5.2 and 8.2 seconds (rounded; inspect native timing telemetry for
new measurements). These are cloud-model workflow samples, not a production
latency SLO or a controlled speedup claim. Substantive answers still involve
multiple model round trips. The baseline used initial ENV transport settings;
that replay used saved BYOK profiles, so timing differences are not a
controlled model-configuration comparison. Hosted Dev has not received these
local corrections.

A second fresh replay exposed a further disclosure-budget defect: both current
and previous tasks were exempt from the skill allowance, and protected bodies
were not charged against the historical allowance. Thus a short follow-up could
retain another full set of procedures beside the current task. The projection
now protects current/unsettled work and charges its bodies before admitting
completed-task disclosures. Procedure text is never shortened; expired complete
load pairs can be rediscovered. A focused two-task regression reproduces the
old over-retention. The 100-run rotating-skill probe now retains one 20 KB skill
body, with a maximum projected request of 29,047 characters and final provider
wire size 31,678 bytes on that fixture. These are bounded-context measurements,
not inference-speed measurements.

Static and dynamic read receipts originally had separate recent-result windows.
They now share one window; an additional pressure pass retains only the latest
read when needed to satisfy the common admission estimate. Replaying the captured
failing request without summary inference reduces its estimate from 27,457 to
23,708 tokens under the unchanged 25,396-token limit. The regression also keeps
an exact pending-write receipt beside those disposable reads.

App read discovery no longer disappears when a previously loaded skill expires.
The shared wrapper remains visible for reads, while its write handler still
requires the loaded workflow and all broker/approval checks. Core navigation
metadata avoids paging App tool catalogues just to obtain Track IDs or links.
Generic-boundary results advertise the actual App read protocol without
executing it or weakening the boundary.

Original-pair live requalification is blocked by Ollama Cloud's weekly account
usage limit. A separate GPT-4.1 control conversation exposed incomplete/incorrect
answers despite successful transport; it is not accepted as a passing accuracy
smoke or as evidence that the final candidate works on DeepSeek. Preserve that
qualification gap until the original cloud pair can be exercised again. Local
corrections have not been published to hosted Dev.

The control also exposed a presentation trap in paged App catalogues: sorting
only by key placed the generic read behind more than twenty write actions.
Read-first stable ordering now exposes data access on the initial page without
removing writes, changing page counts or granting privileges. A regression
places an alphabetically earlier write ahead of a read and verifies that the
read still leads the default page while exact write-key lookup remains intact.

Acceptance testing uses ordinary user prompts, without tool names, schema names,
Track titles or expected numbers supplied in the question. Directed prompts
remain diagnostics only. An ordinary salary follow-up exposed over-clearing of
navigation: the model discovered the correct compensation Track, but subsequent
reads removed its title before the query completed. The candidate now retains
two bounded navigation inventories during active work, rather than retaining
all record bodies. A regression verifies exact names/links, size limits,
expiry of older inventories and continued clearing of data reads. App-defined
collection names also receive canonical entry links, preventing an internal-ID
reply when the user asks where to open a result.

With the user's approval to substitute Heavy, two fresh ordinary conversations
use GPT-4.1 Light / GPT-6.1 Heavy (`gpt-6.1-sol`, medium reasoning) through the
existing OpenAI connection. Fifteen turns complete without stream errors and
pass readback against the canonical fixture: the published Receipt and its
entry link, Documents for narrative proposals, GYD 550,000 configured monthly
base, twenty current employees and eighteen current zero salaries. Actual pay
runs remain absent. A browser-originated recheck beside the unrelated Receipt
page also returns eighteen current zero salaries and explicitly distinguishes
them from thirty-eight historical zero-valued records. The receipt source link
opens the correct published template in the browser.

These are accuracy/recovery checks, not a latency acceptance claim. Substantive
answers range from 14.8 to 51.3 seconds in these samples; first operational-tool
starts range from 4.7 to 19.0 seconds where reported. Tests and some smoke traffic
overlap, and the replacement model has a different context window. Its successful
cloud runs do not substitute for the unchanged-limit captured overflow replay
or prove DeepSeek qualification. GPT-5.4 control runs still made false-absence or
incomplete-read claims and are not accepted as passing. Model selection and the
App's own typed read contracts remain part of end-to-end answer quality. No
hosted configuration or deployment has been changed.

The 51.3-second follow-up contains twelve model calls totalling 36.4 seconds,
including an 11.8-second native history-summary call, and nine tool calls with
8.4 seconds of accumulated tool duration. Those durations need not partition
wall time exactly. Repeated discovery and summarization remain measurable
optimization targets; this candidate does not claim that all cloud workflows
meet a low-latency SLO. Five sequential local list samples under this test load
have medians of 205 ms for Apps, 796 ms for Tracks, 376 ms for Feed and 207 ms
for twenty compensation Entries. These are local observations, not hosted or
large-tenant capacity measurements.

After the full gate finishes, a service restart and one fresh ordinary salary
conversation run without suites or other model smoke traffic. The correct
initial answer takes 37.9 seconds wall time (first requested tool 7.8 seconds;
first operational read 16.3 seconds; eleven model calls). The zero-salary
follow-up takes 6.7 seconds and the Light acknowledgement 2.2 seconds. Thus the
remaining first-request latency is real, not explained away by test contention.
It remains an optimization gap rather than a passing latency SLO.

Final local verification: `make verify` passes, with 7,605 backend tests passed,
997 skipped and 2,237 frontend tests passed. Focused PostgreSQL App-tool,
broker and query-boundary checks pass separately. The captured overflow replay
still admits 23,708 tokens under the original 25,396 limit after the navigation
change. No provider quota bypass, larger admission limit for that replay,
authorization relaxation or domain-specific Core routing is used.
