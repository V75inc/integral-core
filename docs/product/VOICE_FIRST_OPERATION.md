# Operating Integral by voice

**Implementation in progress.** An authenticated person should be able to call Integral, find their place, do permitted work, review its consequences, and hear what actually happened. The acceptance test is a completed task with the display covered. This release is browser-only. Telephone and SIP admission are deferred. Each call binds to one workspace and one agent profile at admission. Switching either during a call is out of scope. Browser microphone consent, account enrollment and any independently required identity verification are separate from operating the workspace.

The voice model phrases ordinary answers and tool results for the listener. A directory lookup supplies the names in their authorized order, the current workspace when relevant, and whether more results are available. The caller can refer to a choice by position even when the model reads the names in a sentence. Routine answers lead with the result and skip tool headings and filler. Requested note readbacks preserve the saved wording. Proposed changes retain the exact values and separate human confirmation.

## Qualifying conversational changes

Use a fresh conversation in a disposable workspace. Keep the selected voice, model, device, timezone and permissions constant across a comparison. Capture what Chrome schedules for playback, the time from end of caller speech to first reply audio, and the saved chat or effect receipt. A spoken promise alone cannot establish that work happened.

| Caller journey | Passing observation |
| --- | --- |
| Ask what Integral can help with, then ask which workspace this is | A brief orientation followed by the bound workspace name. No new durable task. |
| Ask to leave a note, then ask for it back using different words | The caller hears the requested note. Reloading the admitted thread shows the note. |
| Ask for the Apps, open the second one, then name a different App | Only the first question reads the directory. Both requested Apps render, the caller hears the result, and the same call stays connected. |
| Ask which Apps are available, then “can you list it out in the chat?” | One numbered message appears during the call and survives reload. The assistant confirms the post briefly. |
| Ask to create an item, correct its date, then approve | The readback names the corrected item and date. A separate human confirmation precedes a single applied receipt. |
| Interrupt a readout, cancel queued work, or lose access | The stale readout stops. Receipts and scoped reload agree about which work remains. |

On macOS, run `node scripts/qualify_voice_conversation.mjs` and then repeat with `--variant` for orientation, a directory request and repeat, and note readback. Use `--post-list` to check the follow-up that asks for the directory in chat. It compares the saved names and order exactly, checks visibility during the call and listens for an unwanted spoken repeat. Set `VOICE_QA_APP_NAMES` to a JSON array of the visible first-page names in order for an automated comparison against both recordings. Check transcriptions against the audio when names are unusual. The runner needs Chrome, Playwright (or `PLAYWRIGHT_PACKAGE_PATH`), `say`, `afconvert`, FFmpeg, and the local Whisper CLI. Supply `VOICE_QA_EMAIL`, `VOICE_QA_PASSWORD`, the exact `VOICE_QA_WORKSPACE` name, `VOICE_QA_WHISPER_MODEL` pointing to an installed model, and `VOICE_QA_URL` on IPv4 loopback. `VOICE_QA_LIVE=1` explicitly opts into a paid call. Each invocation creates one synthetic conversation and leaves its recordings in a local temporary directory. Keep credentials in the environment and use synthetic content only.

For each failure, compare the recorded audio, offline transcription, tool result and persisted state. A misheard word, an unnecessary spoken preamble and a missing write are different defects. Fix the earliest incorrect boundary, add a deterministic regression there, then replay the failed wording and a held-out wording in fresh conversations. Track first audio separately from time to an actionable result. The report flags common spoken preambles and words added after a requested note readback separately from task success. Review recordings for brevity, turn-taking and grounding even when string checks pass. Extend the matrix with distinct tasks and unfamiliar phrasings before expanding the supported experience. These checks run on demand.

**Assessment basis.** Reviewed the local Integral Core checkout at `6905a569` with its in-progress voice and profile changes. In a fresh signed-in Chrome session in the disposable QA workspace, the Apps screen had no visible Start voice call action on entry. Opening a fresh native conversation exposed it. In a separate loopback fixture, the current browser control and relay exchanged synthetic microphone frames and two audio responses, truncated the interrupted reply, then completed mute, unmute and hang-up. The focused run passed 78 backend, 18 PostgreSQL contract and 10 frontend tests. These checks cover simulated interruption and transport under controlled conditions.

**One live-provider check.** In a fresh disposable workspace conversation on the local Mac QA instance, headless Chrome called the saved OpenAI `gpt-realtime-2.1-mini` model once. The synthetic microphone asked “What is six plus seven? Answer with the number and then say cedar bridge.” Offline transcription of the two seconds of returned browser audio yielded “13. Cedar Bridge.” Call readiness took 1.94 seconds from clicking Start. The first ordinary reply audio reached Chrome’s playback graph 1.41 seconds after the synthetic input ended. Ending the call restored Start. This establishes one intelligible provider round trip. Physical microphone and speaker quality, latency distribution, real-provider workspace actions, sustained mobile use and telephone access remain unverified.

**Voice confirmation qualification.** The authorized isolated Mac Chrome test
used the saved OpenAI `gpt-realtime-2.1-mini` and
`gpt-4o-mini-transcribe` models. Synthetic microphone speech requested
approval of a disposable title edit. The realtime model requested the
independent confirmation; one batch transcription matched it, one fixture
decision saved “Acme Georgetown,” and 19,200 bytes of subsequent reply audio
reached Chrome. Readiness was 3.09 seconds. From starting the confirmation
phrase to first reply audio was 9.3 seconds, including the original fixed
capture window. The current implementation also stops capturing after a
sustained quiet tail, qualified locally. Headless and the isolated visible
Chrome instance exposed no local English voices, so the live-provider test
simulated readback completion. The implementation subsequently added an
offline host synthesis fallback. A fresh actual Chrome test played a
946,194-byte host-generated WAV to completion through the Web Audio graph,
then exercised one synthetic confirmation and decision. A disposable
Linux Python 3.11 container produced a valid WAV using the eSpeak NG package.
Separate
PostgreSQL tests exercise the actual canonical title and date updates and
verify exactly one revision increment, the returned saved values and replay
safety. These are distinct qualifications. A physical microphone,
speaker/headset and local-device readback pilot is still required. The
first provider connection attempt ended at a synthetic thread mismatch
before any confirmation speech; the corrected attempt submitted one
confirmation to the saved transcription provider.

## Workspace requests and chat continuity

A browser call in the disposable QA workspace reproduced the reported failure.
“What's urgent and due today?” and “open my to-do list and add an item” both
ended with the name of an App. Neither question appeared in the saved thread.
The typed native endpoint in the same workspace queried the real priority
and date fields and could stage a task. The first divergence was the realtime
model's choice of a directory lookup. Its private spoken readout ended that
turn before the resident received the goal.

Use the directory for an explicit request to browse. Give the resident the
whole goal when the user wants an answer based on records or wants work done.
The resident uses the ordinary Pydantic AI skill and capability discovery,
query, staging and receipt paths. Both interfaces use the App's existing
schema for record work. The call can identify its workspace and agent, write
an explicitly requested note into its own thread, and read saved chat there.
If a turn owns the thread revision, a standalone note waits until that turn
settles. The caller must ask again. An accepted task can announce that it
needs review while its eventual completion remains eligible for delivery.

Retesting used signed-in Mac Chrome with a synthetic microphone, the saved
realtime mini model and the native worker's configured DeepSeek route through
Ollama. The resident queried the Track schema and governed data. An independent
signed-in record listing returned two items due on the test day, neither High,
and one High item due the next day. A spoken add request with no title prompted
for it. The follow-up produced a pending create with the supplied title, High
priority and the requested date. The test record count stayed unchanged. A
separate request saved a note with the reference phrase “cobalt harbor”; the
next spoken turn read it back from that chat. These tests used a synthetic
workspace. They cover selected tasks and do not establish general voice parity.

To open an App in the current browser, say “Open my notes app.” If the assistant has just listed your Apps, “Open the second one on my screen” uses that list. If the list has changed or expired, give the App name. Asking which Apps are available only reads the directory. Opening an App leaves its records unchanged.

## Next implementation plan — one native conversation across voice and GUI

**Implemented layout prerequisite.** Full-page chat and the sidebar now share
one mounted native conversation and composer. Expanding or moving chat to the
sidebar keeps the existing voice connection and microphone mute state.
One synthetic Chrome call opened an authorized App. The browser still
needs to report whether a destination rendered or the caller's page was
preserved. Track and saved-view journeys remain to be qualified.

**Proposed for the next browser iteration.** The immediate sequence closes
navigation feedback, rapid GUI decision delivery and exact voice review of a
new record. Playback quality is checked at each step. The longer-term
mixed-input plan below also covers shared transcription and steering.
See the [reassessment in ADR 019](../backend/adr/019-native-chat-live-voice-parity.md#reassessment-and-remaining-work). The authenticated native chat owns
the conversation. A call subscribes to that same thread with its principal,
workspace and agent profile fixed. Speech, typing and submitted files can
contribute to the same task. The realtime model handles conversation and
playback. The resident uses its ordinary skills, tools and worker for substrate
work. Chat remains usable while the resident works or the caller speaks.

1. **Unify the transcript and turn admission.** Show interim words as a local
   draft. On a finalized utterance, persist the recognized words with one
   turn identity and distinguish them from any model interpretation. Project
   ordinary spoken replies in the same timeline, marking interrupted playback.
   Use a reference to the committed resident reply when voice reads it aloud
   so it appears once. Recording dialogue, reading history and submitting a
   task are separate operations. A request to do workspace work admits its
   finalized turn exactly once. A quick spoken exchange needs no resident
   call. A correction before submission edits the draft; a correction after
   acceptance is a new, linked input. Reuse chat permissions and retention,
   disclose transcript persistence at call entry, and keep audio and private
   confirmation challenges out of saved chat. Qualify provider transcription
   accuracy and turn correlation against known audio, including negations,
   interruptions and late or duplicate final events.
2. **Make the task addressable from either interface.** Refactor the existing
   voice adapter to resolve a permitted task in the bound thread, preserving
   the checked work identity and revision through admission. Reuse
   `accept_chat_follow_up` and `accept_steering_correction`. “After that”
   queues separate work. “Focus on decisions” steers the referenced task.
   Where two targets fit, ask a short question before attaching the input.
   Bind submitted files using native chat's existing attachment checks at
   submission and use. A file selected in a picker is still a draft. If an
   active worker cannot accept a new attachment safely, offer an explicit
   follow-up with the file and report its disposition. Do not claim the
   current task used it. Publish task status in chat and speak identified
   completions at a pause. Keep barge-in, cancellation and ending the call
   distinct. Extend selected-profile continuations only when that worker
   consumes them with its normal capability limits; check access again on
   reconnect. An unavailable operation must have an accurate spoken outcome.
3. **Let the harness open authorized Integral routes.** For “open XYZ App”
   or “go to the Deliveries Track,” use the resident's existing resource
   discovery, then `integral_open_view`. A shared name gets a choice among
   accessible matches. Bind the originating browser's bounded page snapshot
   to the voice submission just as typed chat does. Reuse
   `open_view_for_dispatch`, the native navigation event and
   `agentNavigationPath` for App, Track, record and saved-view destinations.
   Keep the call controller mounted above route-dependent chat components.
   Apply the same live foreground checks to both interfaces. Only the
   originating, still-visible tab with the same principal, thread, workspace
   and current page may move. Preserve unsent drafts, uploads and queued
   follow-ups. When automatic navigation is suppressed, offer the validated
   destination and allow the caller to continue by voice. Report “opened”
   only after the browser confirms the destination rendered. A stale event,
   reconnect or history replay cannot move a tab. Routes come from Core's
   authorized resource lookup, never a URL composed by the model.
4. **Coordinate work and approvals.** First qualify today's one-active-turn
   and ordered-queue behavior with mixed input. Gate simultaneous execution of
   independent jobs separately on worker support and per-task isolation.
   Dependent tasks wait for the prerequisite result. A result arriving out of
   order must stay with its task, as must a correction, cancellation or
   approval. Extend independent voice review to eligible create proposals:
   read back the exact App, Track and fields, invalidate confirmation on
   revision or permission change, and report the effect receipt. Preserve
   existing stronger review requirements. A transcribed “yes” authorizes
   nothing by itself.

### Acceptance before calling the experience unified

| Browser journey | Observable pass condition |
| --- | --- |
| Say “Open Todo Tracker”, then “Go to its Tasks Track”, then “Which High-priority tasks are due today?” | The originating tab renders each authorized destination, the same call remains connected, and the resident answers from the correct Track. Test duplicate names, missing access, a changed page, two tabs, a pending upload and reconnect. A suppressed or denied route never produces a spoken claim of arrival. |
| Ask by voice to summarize meeting minutes, submit the minutes in chat, then type “focus on decisions” | The same task uses the authorized attachment and incorporates the correction, or clearly offers the supported follow-up. The transcript attributes each input correctly. Chat shows one final answer and voice reads it once. A failed upload or revoked file never supplies content. |
| Start by typing, refine by voice, queue another task, then disconnect and reopen the thread | The correction reaches the intended task. Queue order, profile limits and review state survive. Reopening starts no work and replays no navigation. On workers qualified for concurrency, finish independent tasks in reverse order and verify both receipts, spoken labels and approval targets. |
| Create a task, correct its due date, hear the revised proposal and confirm | The exact saved record matches the last confirmed proposal. Replaying confirmation yields one effect. Also exercise denial, expiry and loss of access after readback. |

Run deterministic provider-event tests for races and replay, PostgreSQL tests
for admission and effects, and signed-in browser tests with synthetic speech
and files. Include the default assistant and a restricted published profile.
Compare spoken requests with the admitted text and time to the first useful
answer. Track unnecessary clarifications, duplicate dispatches, readout length
and wrong-target actions. A physical microphone and speaker pilot remains a
separate release check.

A fallback is a recovery result. Full mixed-input acceptance requires the
running task to consume the submitted file and typed correction. Similarly,
offering a link establishes recovery; the navigation journey passes only when
the intended page opens and the caller can continue in the same call.

Implementation starting points are the [voice follow-up adapter](../../backend/app/services/native_voice_followups.py),
[navigation dispatcher](../../backend/app/services/agent_navigation.py),
[typed chat runtime](../../frontend/src/features/ai-chat/useAIChatRuntime.ts)
and [browser route validation](../../frontend/src/features/ai-chat/agentNavigation.ts).

**Implementation boundaries.** Use the pinned harness for model turns and
skill/tool discovery. Core supplies authenticated scope, graph-backed chat,
authorized navigation, admission and effect receipts. Preserve I-HARNESS-01/02
for tenancy and accepted input, I-WORK-01/02/03/04/05 for execution,
idempotency and review, I-ACCESS-01 and I-APPROVAL-02/05 for disclosure and
decisions, I-GRAPH-01/02 for persistence, and I-SPEECH-01 for credentials.
The browser-only scope and fixed workspace/profile apply to every phase.

## Implementation scope

The call binds to a single workspace and profile. The first local implementation
adds Home composer call entry and workspace-assistant navigation through
numbered App, Track, saved-view and record lists, search, paging, repeat, selection and current location. It
passes a revalidated focus to native chat submission and exposes explicit
cancellation of a task accepted in the call. A spoken review reads the
canonical pending record edit through the existing staging service. The direct
navigation surface
is limited to the workspace assistant; published profiles retain their own
capability ceiling. A new call starts with no transient list or focus. The initial independent
confirmation path supports reversible edits to a selected record's title,
status and typed date fields. The browser reads the
exact current and proposed values with a local device voice or offline host synthesis, then recognizes
a bounded response using the already configured Speech to Text provider.
The realtime model cannot submit a decision. Supported record edits use the
canonical decision and application receipt. A caller can also correct a proposed title or typed date directly. The
canonical revision service retires the old proposal and its linked
WorkApproval, creates a new independently reviewed proposal, and preserves
recovery after reopening the same conversation. Rejection and a fresh
request remain available for unsupported edits.
Published-profile operation parity and the wider browser workflow catalogue remain implementation milestones. Telephone admission is outside this release.

The current ADR 019 slice also supports independent confirmation of simple
new records with scalar fields. It reads effective defaults through canonical
validation, allows a title/date correction through the existing proposal
editor, and checks schema and values in the creation transaction. Hooks,
relations, files, long bodies, tags and compound effects retain their
card path. PostgreSQL tests qualify date correction, saved-value readback
and one-record replay. The complete conversational creation journey remains unqualified. A real
browser voice navigation check now passes with the saved single-model
GPT-6.1 Sol selection after removing a fixed demo route and correcting the
pinned SDK's native Responses/streaming metadata.

This slice preserves I-RUNTIME-01 through bounded reads, expiring selections
and a finite call tool budget. I-RET-01 and I-ACCESS-01 require the existing
resource policy at selection, submission and readout. I-HARNESS-01/02 and
I-WORK-01/02/04 retain native admission, accepted input and the same work
identity. I-APPROVAL-01/02/05 and I-WORK-05 remain in force: the separate
one-use human confirmation calls the existing decision service, and cancellation
does not bless, revoke or replace a parked review. I-GRAPH-01/02 need no new
records because call-local selection is ephemeral. I-SPEECH-01 keeps provider
credentials in the existing server binding.

## Assessment of the original gaps

These observations describe the build before the voice-first changes. Current
coverage and remaining acceptance checks are recorded separately above.

| Caller’s request | Behavior at initial assessment | Consequence |
| --- | --- | --- |
| “Call Integral and tell me where I am.” | The native chat composer offers Start voice call for an eligible workspace, model, profile and microphone. The Home composer omits it. A fresh call can create a conversation after provider readiness. The only staff voice ingress found is an authenticated browser WebSocket. | Starting requires finding a control. A telephone caller has no admission path. |
| “List my Apps. Open the second one, then tell me what needs attention.” | At assessment time, the default assistant could discover three quick reads for workspace, App and Track names. Results contain up to 20 labels. A quick-read offer selects a tool, not one of its returned records. There is no spoken continuation cursor. The existing open-view dispatcher requires bound browser context and returns a navigation request. | A caller may obtain a useful list or a durable lookup. There is no reliable, permission-checked meaning of “the second one” across turns, nor a virtual location for a caller without a screen. |
| “On this record, change the due date to Friday.” | Voice submits a model-supplied rendering of the request to the resident worker. It supplies the thread and a voice-origin marker. The normal turn contract also supports focused Track/view, referenced entities and attachment bindings, which this handoff does not populate. | An explicit record name may work. References to the current view, a previous choice, a file or an ambiguously heard date require another exchange and may select the wrong target. |
| “What exactly will change? Make it Monday instead. Approve that one.” | The worker stages governed changes. Voice can describe generic review states. It withholds pending approval tokens from delegated turns and explicitly instructs the caller to review in chat. The shared decision service requires a strong card confirmation for destructive/security effects. | The user hits a mandatory screen even for an ordinary reversible edit. An approved decision and successful application are distinct outcomes. |
| “Stop speaking. Now cancel the job. Did it already change anything?” | Barge-in cuts playback and preserves the job. The default assistant supports separate follow-ups and revisions of eligible voice work. A published profile lacks these continuation tools. The ordinary application has scoped cancellation. The call has no corresponding explicit cancel tool. | A caller can interrupt the narration while the work continues. Ending a call leaves pending work intact. |
| “Read the rest. What happened to yesterday’s request?” | An existing call can seed up to eight saved excerpts of up to 500 characters. Delegated results are text-bounded, and peer notifications can direct the caller to the full reply in chat. Ordinary spoken dialogue is not automatically saved as chat. | A new call can resume some committed history, but cannot reconstruct every spoken choice. A table, attachment, lengthy answer or non-text result can end at a visual reference. |
| “Continue with my selected specialist.” | The call pins the authorized principal, workspace, profile and thread. The client closes it on scope change or document exit. The selected-profile worker handles reads under its own authority. Direct quick reads and queue/revise are withheld there. Voice admission currently allows owner, admin and member roles. | The fixed workspace and profile binding is intentional. Guest access to particular resources and published-profile capabilities need explicit product coverage. |
| “Why did the call go quiet? Can I pick up where I left off?” | The relay has a ten-minute cap, a cap of 16 accepted tool calls in the ordinary tool-call path, bounded waits and bounded readouts. At the tool cap further calls can be ignored. The browser drops capture frames under backpressure and shows text for certain connection, permission and model errors. Concurrency is limited per principal per API process. | Some failure and exhaustion states have no actionable audible explanation. There is no continuous phone session or guaranteed recovery of conversational position. |

The core works well enough to build on. Voice and text already share the durable submission, policy, staging and receipt services. The relay checks the current caller, thread, profile and model, checks protected readouts before delivery, and separates interrupted speech from cancelled work. The direct realtime path keeps small talk off the durable queue. The goal is to close the gaps around those mechanisms. [Call contract](../backend/voice-chat-conversation-contract.md) · [Voice relay](../../backend/app/services/native_voice.py) · [Browser control](../../frontend/src/features/ai-chat/components/NativeVoiceCall.tsx) · [Navigation](../../backend/app/services/agent_navigation.py) · [Decision boundary](../../backend/app/agentive/services/approval_decisions.py)

## Delivery sequence

| Order and owner | Work to deliver | Exit test with no screen interaction after admission |
| --- | --- | --- |
| **1. Call entry, orientation and navigation** — product, client and conversation services | Offer a discoverable call entry outside the composer. Bind a server-owned current location, result page and selected item to the authorized call. Reuse resource lookup, labels, view resolution and read policy. Add “where am I”, “next”, “previous”, “go back”, “repeat”, and selection by name or ordinal. The browser may mirror a destination after acknowledging navigation. A phone caller hears the destination and can work there directly. | From a fresh call, find an App, choose between identically named records, read the second, go back and continue a second page. Verify the actual selected record on the server. Revoke access between listing and selection and verify the item is no longer disclosed. |
| **2. Ground requests and finish the first write** — harness, staging and identity | Pass verified focus through the existing native turn context. Compare the caller’s spoken target, requested values, recipients and dates with the resolved proposal. Use the canonical proposal payload and current state for exact readback. Add a reviewed voice decision source for eligible private, reversible effects. Bind an explicit caller choice to one exact unchanged proposal and its expiry. Support inspect, correct, reject and confirm. The model must never mint approval or treat an incidental “yes” as authorization. | Dictate an edit, hear old and new values, correct the date, confirm the revised proposal, and hear the persisted result. Repeat the confirmation after reconnect: one effect. Test an expired proposal, two pending items, changed grants, background speech and a failed apply after approval. |
| **3. Completion, continuity and broader coverage** — capabilities, Apps and accessibility | Inventory human-visible operations against registered read tools, governed commands, effect policies, result renderers and runtime permissions. Record a disposition for each operation and role. Add generic voice presentations for search, records, filters, totals, comments/mentions, attached document results, dashboards, scheduled work and connector results. Speak a short answer first, then allow paging or drill-down to the full authorized result. Add explicit cancel, status, retry and resume against the existing work item. Carry published-profile authorization through supported continuations. | Run search → select → comment, document → quote authorized passage, report → drill into a row, and schedule → cancel/read outcome. Interrupt during a readout, lose the call, rejoin, ask what **applied**, and continue without resubmission. Test the same scripts against a limited profile and a principal with partial access. |
| **Future release: phone admission and step-up** — identity, platform and voice transport | Build an authenticated phone/SIP adapter to the same call contract. Bind the incoming call to an enrolled Integral human, then explicitly select an authorized workspace/profile. Verify provider signalling separately from caller identity. Caller ID, a familiar voice, or possession of a call handle alone grants no workspace access. For effects requiring stronger confirmation, obtain an approved, accessible independent factor or leave the proposal pending for its authorized reviewer. Keep secrets out of model audio and transcripts. | Call from an ordinary phone with no open browser. Authenticate, choose the workspace, complete the first-write journey, hang up and resume. Test shared numbers, failed verification, duplicate signalling, two API workers, reassignment, transfer and revocation. No workspace names before identity verification. |
| **5. Reliability and release** — QA, platform and product | Make microphone loss, timeout, model unavailability, noisy input, tool budget exhaustion and worker delay distinguishable. Offer an audible recovery path when output remains available and a local accessible fallback when it does not. Test speakerphone echo, headsets, mobile backgrounding, accents, pauses, corrections and switching audio devices. Roll out from read-only to reversible work, then to individually qualified higher-impact operations. | An independent tester completes the signed-off journey set with the display covered, on supported browser devices. Every failure has a documented cause, recovery path and owner. |

The first pilot should cover one workspace, one existing published or default identity as applicable, paged reads and one reversible edit. Telephone and SIP integration are deferred by the product decision for this browser-only release.

### What the caller hears at a review

If the caller says “Monday,” resolve the exact calendar date in their authorized context and speak it back with the record and changed field. Read the canonical proposal through a controlled renderer. Offer “details”, “change it”, “reject”, and a confirmation specific to this proposal. Arm confirmation only after the essential readback has played. Interruption, correction, ambiguous audio, expired scope or a revised proposal invalidates that confirmation. An assistant-generated tool argument or model paraphrase cannot attest that the human approved. Capture the user's decision through a separately controlled confirmation step bound to this authenticated session. The first browser implementation uses device-local readback with a one-use challenge and independent Speech to Text recognition. If the decision or authentication is insufficient for the operation, say so before claiming success. After execution, read the actual saved value or failure from the same receipt. For example, “The due date is Monday, 7 December” and “Your change is approved; saving it failed” describe different states.

### Harness design decisions

- Keep one resident harness. The realtime model manages turn-taking and speech. Existing authorized dispatch handles enrolled quick reads. Delegate everything requiring the resident’s skills, operating context or governed work to its normal worker. Compare the spoken request with the actual accepted arguments, especially names, negations, quantities and dates. Ask the user to resolve ambiguity before execution.
- Give the call a location independent of the browser. A selection needs a server-held reference, list revision and expiry. Validate it again at use. Changing workspace or agent profile requires ending the call and starting another. A new call has a new authorized binding and never inherits transient focus, offers, pending readouts or confirmation. Route an optional visual mirror through the existing open-view path.
- Keep authorization in Core. Use the effective intersection of caller rights, selected profile identity, published capabilities, App policy, connector installation scope and current grants. Decide explicitly whether each guest/personal context is voice-eligible. A user who can operate a control directly may still need review when acting through the agent. Retain other people’s approval requirements.
- Reuse the shared decision service and its effect classes. Require product/security approval for any new strong-confirmation equivalent. Design account recovery, confidential credentials, first-time external OAuth consent, and the acquisition of a file from a local device as explicit boundaries. Where an accessible alternative is possible, offer it. Where an external party or policy requires a different interaction, name the exact step and preserve the caller’s place. Never ask callers to dictate passwords or API keys.
- Prefer public pinned Pydantic AI facilities for realtime lifecycle, typed tools, history, interruption and usage when they preserve the call contract. Evaluate parity with synthetic provider frames before changing the relay. Integral currently needs private per-response inputs, correlated delivery, first-audio authorization and played-audio truncation. Keep that adapter until an alternative passes those same tests. Avoid a second tool catalogue or an extra model round trip just to navigate. [Pydantic AI realtime](https://pydantic.dev/docs/ai/realtime/overview/) · [OpenAI phone transport](https://developers.openai.com/api/docs/guides/voice-sip?voice-api=realtime)
- Keep spoken choices separate from consent to record a call. Resume from authorized focus, accepted work and receipts. Define consent, retention, access and deletion before persisting any additional speech transcript. Never put call content, names, proposal tokens, provider credentials or file data in operational metrics.

### Coverage register

The generated [capability map](../generated/capability-map.md) is the inventory
of Core tools. Installed App queries and profile declarations remain dynamic.
This initial register groups the actual UI and harness entry points by the
caller journey. A qualified local fixture is evidence for that journey only;
the complete eyes-free release still requires an independent device pilot.

| Journey | Authorized context and scope | Current voice coverage and recovery |
| --- | --- | --- |
| Home call entry and orientation | owner, admin or member; selected workspace/profile | implemented; browser microphone admission and reconnect; device tests pending |
| Apps, Tracks, saved views and records | workspace assistant; live read policy | implemented numbered search, select, repeat and page; reselect after reconnect |
| Record title/status/date edit | workspace assistant; selected record; scoped private reversible proposal | local confirmation and canonical application implemented; independent live speech path exercised on synthetic fixture; actual physical device pilot pending |
| Existing App queries, reports and documents | resident's current capabilities; published profile's declared queries | delegated read and bounded spoken results; complete structured drill-down and file delivery unqualified |
| Comments, tags, links, creation and dashboards | resident staging and App permissions | delegated preparation; decision may still require existing review controls |
| Connector operations, data exports and imports | installation, App and applicable effect policy | existing permitted delegation; end-to-end audible configuration, delivery and outcome workflows unqualified |
| Membership, grants, credentials, destructive changes | owner/admin and exact resource/approval policy | stronger independent confirmation or external consent required where applicable |
| Cancel, resume and status | own accepted WorkItem and conversation | explicit cancellation, status/reply paging; reconnect selection and pending review read; broader interruption and continuation coverage pending |
| Phone admission | future release | deferred; excluded from the browser release acceptance metrics |
| Failure and reconnect on devices | authenticated caller and active scope | bounded timeouts; microphone loss, status and device-local error speech where supported; mobile, physical audio and cross-worker phone tests pending |

### Measuring “voice only”

Create a coverage register from actual UI actions, API contracts and installed capability declarations. For each workflow record the role, scope, input, required evidence, possible effects, confirmation level, spoken outcome and recovery. Include App administration, grants, connectors and data transfer. For example, reporting that an export finished does not demonstrate that the caller can direct the file to an authorized destination. OAuth setup or delivery to an external recipient requires its own gate. Mark each journey **completed by voice**, **policy or external step required**, **unsupported**, or **failed**. Report all four totals so excluding hard cases cannot inflate coverage.

Use fresh synthetic workspaces for each role, a read-only user, two similar records, a private record, staged and already-applied actions, and a delayed worker. Have a separate observer verify actual destination, stored value, recipient and audit event. Score a journey as complete only when the caller hears the correct outcome and can describe what happened. A tool call, a green socket, or a spoken promise is insufficient.

Proposed release targets, to calibrate against real devices and then freeze before the pilot:

- All critical scripted journeys complete without opening chat or touching a GUI after admission. At least 95% of eligible natural-language pilot journeys complete unaided. Report policy-required handoffs separately and count avoidable screen handoffs as failures.
- Zero wrong-target effects, duplicate submissions, unapproved writes or protected readouts after revocation in the adversarial and reconnect suites. Confirm current access at selection, decision, execution and delivery. Keep the normal audit trail and original actor attribution. [Substrate invariants](../INVARIANTS.md#i-approval-02--preserve-original-actor-and-actual-outcome)
- Measure end of caller speech to first audible acknowledgment and to first useful answer separately. Initial p95 targets: 1.5 seconds to acknowledge, 3 seconds for a bounded read and 300 ms from detected interruption to silenced playback. Report durable-work completion independently. Break out browser and phone, warm and cold calls, noise, provider/tool calls per turn, dropped audio, reconnects, wrong-target corrections and review abandonment. Fixture timings do not qualify device latency.

**First demonstration:** “Find the two Acme records. Open the one in Georgetown. What is its due date? Move it to Friday. Actually, make that Monday. Read back the change. Confirm that change. What date is saved?” Verify the exact record and one write. Run again with write access removed after readback. This small journey qualifies the core design before broadening the catalogue or adding a phone number.
