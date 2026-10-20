# Chat recovery and everyday language

## Observed issue and limits

Read the reported production App chat through the authenticated browser. It
shows four failed App update proposals (`invalid_arguments`), then the fixed
technical final-response fallback. The public transcript deliberately excludes
private arguments and results; it does not identify the rejected field. No
production record or approval was changed during investigation.

A real local provider run also reproduced a separate unfinished-work path: the
model loaded the dashboard workflow, repeatedly read page context, and ended
with an offer to prepare a Home proposal, without saving an approval card.
Page orientation must not count as workflow discovery. Loading a procedure must
also reveal its declared workflow tools. These are structural
capability-state rules, not classifiers of user wording.

## Changes and invariants

- Loading any skill exposes its declared workflow schemas from the current
  authorized catalogue. This replaces the function-specific starter-read list.
  Reads and change proposals are both usable; shared tools and restored skill
  activation are supported. Undeclared tools remain deferred. Metadata does not
  add missing tools or grant permission: the broker checks every dispatch.
- Skill-authoring guidance documents complete procedures, typed inputs,
  approval outcomes and readback. Approval replies are limited by instructions
  to one or two short sentences, with truthful partial-failure reporting.
- Page context alone no longer bypasses initial workflow discovery. Restored
  active procedures and actual operational reads retain their supported paths.
- Suggestions return a separate `home` starter, capped at 12 unique widgets,
  stripped of preview-only metadata and checked against the ordinary Home shape,
  layout and authorized source rules. Invalid or unavailable candidates are
  omitted; no eligible starter returns null. No fallback record scan is added.
- The authoring procedure starts from that payload. Proposal and approval still
  revalidate current authority; suggestions never save a Home or grant consent.
- Host failure/recovery messages explain the problem and next step in everyday
  language. Model instructions require the same style and prohibit promises of
  work after the reply ends. Applied, pending and uncertain outcomes remain
  distinct; retry guidance does not claim that earlier work was undone.
- Failed-tool UI shows a readable sentence instead of raw JSON, arbitrary
  exception text or duplicate result/error blocks. Message debug retains the
  original diagnostic payload. Successful results keep their existing rendering.
- Stored tool diagnostics retain their existing fixed vocabulary, preserving
  compatibility with older workers and transcripts during upgrades. The UI
  presents readable sentences by error code; arbitrary content stays rejected.
  Only the exact retired host fallback is reworded in assistant history
  presentation; user messages and arbitrary answers are unchanged.

Relevant invariants: I-CHAT-01 (readable product communication), I-GRAPH-02
(private execution diagnostics), I-ROLE-01 (current source authority), and
I-APPROVAL-01 (approval before protected effects). No new data model, write
replay, index publication, version bump or deployment is introduced.

## Qualification

Evidence is recorded below after the current-source checks finish. Provider-free
regressions use generic data and structural capability cases. Live provider
acceptance uses a disposable local App, actual authenticated API, native worker
and PostgreSQL; it is not a replay of private production arguments.

### Browser acceptance

Actual provider: `ollama_chat/deepseek-v4.1-flash:cloud`, matching the model
reported by the production chat. Disposable local PostgreSQL workspace only.

- A fresh request on a synthetic App produced one pending Home approval, with
  no tool failure. Before approval, readback showed no saved Home and zero
  analytics dashboards.
- Approving the card saved four widgets. The model read back the Home; the
  browser rendered its introduction, record count, activity, recent entries and
  status chart. The API confirmed all four widgets and zero analytics dashboards.
- The earlier locally stalled conversation continued with a read-only request.
  It correctly described the already saved Home without another approval or write.
- A separate, explicitly synthetic legacy-message fixture verified presentation:
  the retired fallback becomes plain-language guidance, and an expanded failed
  step shows one readable explanation without a JSON block. Raw diagnostic
  payloads remain available in Message debug. This fixture is copy acceptance,
  not evidence of live model recovery.

Current completed checks: Node 24.19.0 frontend suite (339 files, 1,941 tests),
production build/type check, lint (zero errors; 464 existing warnings), 112
focused PostgreSQL tests, staged substrate guards and pre-commit checks. The full-backend and remaining contract qualification were interrupted after
the database blocker below was isolated; they are not claimed as passes.

### Generic skill binding and brief approval acceptance

After replacing the special-case read list, the actual local provider completed
two unrelated changes in one conversation: a Track rename and a Home
introduction update. Each created a pending approval before applying changes.
After approval, the UI showed the saved change and the provider gave one
confirmation sentence. The Home retained all four existing widgets.

Provider-free tests use arbitrary skill/tool names, exercise a fresh runtime
restored from framework history, keep unrelated tools deferred, omit tools absent
from the authorized catalogue and confirm that a disclosed write still obeys
the host write restriction. A read-only skill explicitly declares only its read
instrument, preserving the focused-workflow context boundary.

### Qualification blocker (not introduced by this patch)

The broader PostgreSQL contract lane failed
`test_independent_worker_processes_cannot_duplicate_the_effect`. A focused
repeat also failed, and the same test failed against an isolated source export
of the existing PR head `fb0db8be5fc5683fd69b8a52cf756fad21249a4b`.
The independent-worker diagnostic and server log show a cold-process schema
DDL deadlock: collection initialization takes a table lock while another
transaction holds a lock needed for its graph write. This occurs in the pinned
jvspatial PostgreSQL adapter; these copy/disclosure changes do not modify that
adapter or the transaction path. The test was not skipped, weakened or changed.
The broad lane was interrupted after diagnosis; it is not a passing result.
The Docker restore drill also remains unavailable because Docker storage is full.

Per root `AGENTS.md` commit discipline, an out-of-scope failing test blocks
committing or pushing this follow-up. The patch remains local for review until
that database issue is addressed and qualification passes. No approval bypass,
write retry, dependency override, merge or deployment was introduced.

Final focused generic-binding/approval tests: **116 passed**, including real
Pydantic AI discovery, fresh-runtime restoration, current write policy and
server-reconciled approval instructions. The unchanged frontend candidate
previously passed **1,941 tests across 339 files**, a production build and type
checks. Full current-source qualification remains incomplete due to the
database blocker, so no commit or push was made.

### Approved-build follow-up

The reported local chat saved a typed design but its artifact readback exposed
only the prose preview. The model reconstructed field/type names and retried
an invalid board configuration: first string columns, then label/value objects
without the persisted key. Staging rejected both before effects. A later build
created the App but verification found a renamed entry type; its fields existed
under that different type name. This was a real incomplete build, not a
verification false positive.

- Design artifact readback now projects the current typed blueprint, revision
  and digest from the owning thread's design authority. Stale editable scratch
  metadata cannot replace that projection. Other-user reads remain denied.
- Plan fidelity checks entry-type names and fields within each type before
  staging. The model receives instructions to read and copy the exact blueprint.
- A shared View authoring adapter converts equivalent column names/value objects
  to canonical key objects, and a single sort object to a list. The persisted
  compiler still rejects duplicate lanes, missing keys and invalid shapes.
  Known View configurations are validated before a proposal can be approved.
- Failed steps omit empty/private argument blocks. Typed legacy placeholder
  resource links route inside the current deployment; ordinary external URLs
  stay external. Scaffold handoffs and status rechecks use brief replies.

Relevant invariants remain I-ROLE-01, I-APPROVAL-01 and I-GRAPH-02: adaptation
does not confer authority, skip staging, alter prior effects or replay writes.
The local Journal label was corrected through its scoped authenticated API;
its entry-type ID and complete form schema were checked unchanged. The original
App subsequently returned verified for all three Tracks and six requested Views.

Current-source focused build/recovery checks: **177 passed**, plus the final
artifact projection tests and capability/skill alignment checks. View staging
checks passed, frontend focused checks passed **19 tests**, TypeScript passed,
backend source lint passed and targeted frontend lint had zero errors (one
existing component-export warning). This does not replace the broader release
qualification or remove the database blocker documented above.

Fresh live browser acceptance used an unrelated empty equipment/inspection App
and the same cloud provider. Proposal, approval, build and verification completed
with three tool calls on the build turn and no failed steps. The final reply was
two short sentences with a local App link. Independent scoped API readback found
the exact entry-type names and fields, two canonical board values, the calendar
date mapping, requested default Views and zero entries on both Tracks. The
original travel query was not added as a query-specific regression fixture.
