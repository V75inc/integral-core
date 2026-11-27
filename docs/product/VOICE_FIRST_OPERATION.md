# Operating Integral by voice

Browser calls use the signed-in account’s local time for a short greeting when
available. For hands-free record changes, open **Permissions** in the chat composer, choose **Custom**, check apps or individual private lists you own and choose **Use Custom**. An app checkbox selects its currently eligible lists. You can then say “Add wrapping
up the proposal to my to-dos for today.” The resident resolves the request,
uses the normal workspace model assignments and checks the saved result before
speaking. With **Ask for approval**, eligible writes are reviewed aloud. The call
reads the proposed values, including each digit of a numeric text identifier.
The call asks about the action directly, such as “Would you like me to add
‘Review the proposal’ to Today?” Say “yes, please” to approve or “no, thanks”
to decline. You can also give a correction in the same reply. For example,
“Actually, call it ‘Review the estimate’” prepares a new proposal and you hear
the revised title before deciding. Ask to hear the details again at any point in the review. You can leave the review details in chat closed.

During a call, the call controls take the place of dictation in the chat composer. In a narrow sidebar, use the microphone icon to mute or unmute and the red stop button to end the call. Dictation returns when the call ends.

**Implementation in progress.** An authenticated person should be able to call Integral, find their place, do permitted work, review its consequences, and hear what actually happened. The acceptance test is a completed task with the display covered. This release is browser-only. Telephone and SIP admission are deferred. Each call binds to one workspace and one agent profile at admission. Switching either during a call is out of scope. Browser microphone consent, account enrollment and any independently required identity verification are separate from operating the workspace.

The voice model phrases ordinary answers and tool results for the listener. A directory lookup supplies the names in their authorized order, the current workspace when relevant, and whether more results are available. The caller can refer to a choice by position even when the model reads the names in a sentence. Routine answers lead with the result and skip tool headings and filler. Requested note readbacks preserve the saved wording. Proposed changes retain the exact values and separate human confirmation.

The call already has its authenticated workspace and assistant names. It verifies and speaks the requested name against that binding without starting resident work.

For substantial requests, expect a brief spoken plan before work starts. A short lookup can return directly; slower work receives occasional verified progress updates at pauses. Completed work is announced once and the call remains available for another turn. If Integral is making small talk when a completed result arrives, it can cut its own ordinary speech and go straight to the useful answer. It waits if you are speaking, receiving a verified result, or confirming a change. If your microphone temporarily loses its audio route, the call reports the interruption and resumes listening when capture returns. A disconnected microphone requires a fresh call; the call also reports its ten-minute connection limit.

Say “Hang up now” or “Please end this call” to finish without using the screen. Integral may say a brief farewell, then releases the call. A conversational goodbye alone does not end it. Already accepted work continues and its results remain in chat. Asking to cancel a task is a separate action. While describing your entries, Integral should speak the relevant values naturally, preserve requested details and avoid reading a field schema aloud; ask for the exact saved wording when you need it.

A separate spoken lookup can join the same chat queue while the first lookup runs. A correction targets the current request. An explicit question about progress reads its status. If a separate follow-up arrives after its preceding voice request has verifiably succeeded, the workspace assistant can submit it to the same conversation. Selected profiles retain their own continuation limits. A second call for the same signed-in user reports that the first call is still open.

A caller can schedule a one-time text reminder in the bound chat entirely by voice. Integral reads the message and local schedule, then independently verifies the spoken answer. The chat card stays synchronized. A new call on that chat can recover one still-pending reminder.


## Voice-first behavior and qualification

Use the browser call with the screen covered as the first acceptance test.
The caller should be able to ask what is urgent, choose an item, correct a
proposed change and hear its committed outcome. An open chat window adds
ways to participate. It does not change how Integral answers a live call.

1. Audit the spoken paths first. Replace routine directions to check chat
   with an answer, a next step the caller can take aloud, or the precise
   reason a stronger review is required. Start with long replies, attachment
   updates and unverifiable reviews. Reuse the current read-more, scoped
   status and independent confirmation operations where they apply.
2. Repeat the journey with chat open. Type a correction, submit a file and
   decide an eligible proposal on its card. Verify that voice uses the
   correction, distinguishes file receipt from successful analysis, and
   reports the actual outcome once at a pause. Also request a written list
   and verify that voice acknowledges it without reading it back unasked.
3. Run the same journey against an audio-only adapter fixture before adding
   a connector. Omit browser routing, text delivery and any confirmation
   method the adapter cannot perform. Verify spoken selection, follow-up
   and an honest pending outcome when verification is unavailable. Use one
   resident task and one approval path across all three runs.

The native call now scopes browser navigation, posting notes and independent
confirmation to the actions its transport offers. Spoken selection and the
resident work queue use the same authorized resources with or without a
screen. An explicitly written-only lookup leaves its result in the thread and
gets a short spoken receipt. This preference applies to one request. Asking
for details about a spoken reply starts with its saved text and “continue”
pages through it. A file acknowledgment confirms receipt only.

Compare end of speech to useful answer, unnecessary spoken words and avoidable
screen handoffs in all three runs. Render acknowledgment matters when the
caller requests a page on screen. It should add no wait to a spoken lookup.

## Qualifying conversational changes

Use a fresh conversation in a disposable workspace. Keep the selected voice, model, device, timezone and permissions constant across a comparison. Capture what Chrome schedules for playback, the time from end of caller speech to first reply audio, and the saved chat or effect receipt. A spoken promise alone cannot establish that work happened.

| Caller journey | Passing observation |
| --- | --- |
| Ask what Integral can help with, then ask which workspace this is | A brief orientation followed by the bound workspace name. No new durable task. |
| Ask to leave a note, then ask for it back using different words | The caller hears the requested note. Reloading the admitted thread shows the note. |
| Ask for the Apps, open the second one, then name a different App | Only the first question reads the directory. Both requested Apps render, the caller hears the result, and the same call stays connected. |
| Ask which Apps are available, then “can you list it out in the chat?” | One numbered message appears during the call and survives reload. The assistant confirms the post briefly. |
| Ask for a task “tomorrow at eight” in an app with a date field and text notes. Agree to put the clock time in Notes, then say “yes” to the staged proposal. | The reply to the app's question returns to the resident. The caller hears the actual title, date and note before independent confirmation. Verify one saved task, the literal note, and no scheduled reminder. If the caller declines to specify AM or PM, neither the stored value nor the spoken review should invent one. |
| Ask to create an item, correct its date, then approve | The readback names the corrected item and date. A separate human confirmation precedes a single applied receipt. |
| Vary one filter while a lookup is still running, then repeat after completion | Both scoped results appear once. A change phrased as a replacement steers the active lookup. Measure end of speech to first audio, to each useful answer, and for navigation to the browser’s rendered acknowledgment. |
| Interrupt a readout, cancel queued work, or lose access | The stale readout stops. Receipts and scoped reload agree about which work remains. |

On macOS, run `node scripts/qualify_voice_conversation.mjs` and then repeat with `--variant` for orientation, a directory request and repeat, and note readback. Use `--post-list` to check the follow-up that asks for the directory in chat. It compares the saved names and order exactly, checks visibility during the call and listens for an unwanted spoken repeat. Set `VOICE_QA_APP_NAMES` to a JSON array of the visible first-page names in order for an automated comparison against both recordings. Check transcriptions against the audio when names are unusual. The runner needs Chrome, Playwright (or `PLAYWRIGHT_PACKAGE_PATH`), `say`, `afconvert`, FFmpeg, and the local Whisper CLI. Supply `VOICE_QA_EMAIL`, `VOICE_QA_PASSWORD`, the exact `VOICE_QA_WORKSPACE` name, `VOICE_QA_WHISPER_MODEL` pointing to an installed model, and `VOICE_QA_URL` on IPv4 loopback. `VOICE_QA_LIVE=1` explicitly opts into a paid call. Each invocation creates one synthetic conversation and leaves its recordings in a local temporary directory. Keep credentials in the environment and use synthetic content only.

Use `--written-result` with `VOICE_QA_LOOKUP` and
`VOICE_QA_EXPECTED_RESULT` to check an explicitly written lookup. Choose a
distinguishing phrase from the known synthetic result that is absent from the
question. The check requires a single persisted answer, listens for a spoken
repeat, then asks a different question and verifies the reply is audible.

For each failure, compare the recorded audio, offline transcription, tool result and persisted state. A misheard word, an unnecessary spoken preamble and a missing write are different defects. Fix the earliest incorrect boundary, add a deterministic regression there, then replay the failed wording and a held-out wording in fresh conversations. Track first audio separately from time to an actionable result. The report flags common spoken preambles and words added after a requested note readback separately from task success. Review recordings for brevity, turn-taking and grounding even when string checks pass. Extend the matrix with distinct tasks and unfamiliar phrasings before expanding the supported experience. These checks run on demand.

### Checking a delegated lookup

In a disposable workspace, seed two distinguishable tasks on different dates.
Ask what is due today. Compare the browser’s **entire** recorded reply with
the authorized record listing, the accepted chat request and the saved
answer. A false access disclaimer followed by the correct item fails the
journey, even when the relay counted one admission and one result. Repeat with
no matching records and with access withdrawn. A genuine restriction must
remain audible.

### Mixed-input and device qualification

Start a task by voice, attach a synthetic file in the same chat, then type a
correction while the task is running. Check which work item consumed the file
and correction, whether another turn was queued, and whether the caller heard
the committed result once. Repeat with work initiated by typing and with a
published profile. Queue and steering support differs by binding. A file
receipt alone only establishes that the chat received it.

Check both chat layouts, a protected draft, barge-in, a muted microphone and
a second tab. Move the active call between sidebar and full page and verify
both audio directions survive. From full-page chat navigate to Home and an
App, then return and collapse: verify the same call and mute state, with no
leave warning. Verify that closing the dock or selecting a different
conversation offers Stay in call or End call and leave; cancel keeps the
call, confirm releases it without cancelling resident work. Check document
refresh/close with the browser-native prompt (where supported), and verify
the quiet connecting ring stops before the greeting or any captured audio.
Check the app-owned Reload warning separately from the browser-native
warning, and interrupt a plan readout with “go ahead and build it”: verify
the existing plan becomes the resident's next task and its actual outcome
is spoken. Verify an actionable request with a missing model-produced
recap still uses one natural spoken paraphrase before the resident begins,
including a quick read or navigation request, before execution; pure conversation remains immediate. During a completed-result interruption, verify that a late
valid tool call is not cancelled with ordinary playback.
Check the physical microphone, speakers,
mobile viewport and assistive technology independently of synthetic Chrome
audio. The [conversation contract](../backend/voice-chat-conversation-contract.md)
defines the supported routes, review types and receipt behavior.

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
| Failure and reconnect on devices | authenticated caller and active scope | bounded timeouts; microphone loss, status and accessible call status on failure; mobile, physical audio and cross-worker phone tests pending |

### Measuring “voice only”

Create a coverage register from actual UI actions, API contracts and installed capability declarations. For each workflow record the role, scope, input, required evidence, possible effects, confirmation level, spoken outcome and recovery. Include App administration, grants, connectors and data transfer. For example, reporting that an export finished does not demonstrate that the caller can direct the file to an authorized destination. OAuth setup or delivery to an external recipient requires its own gate. Mark each journey **completed by voice**, **policy or external step required**, **unsupported**, or **failed**. Report all four totals so excluding hard cases cannot inflate coverage.

Use fresh synthetic workspaces for each role, a read-only user, two similar records, a private record, staged and already-applied actions, and a delayed worker. Have a separate observer verify actual destination, stored value, recipient and audit event. Score a journey as complete only when the caller hears the correct outcome and can describe what happened. A tool call, a green socket, or a spoken promise is insufficient.

Proposed release targets, to calibrate against real devices and then freeze before the pilot:

- All critical scripted journeys complete without opening chat or touching a GUI after admission. At least 95% of eligible natural-language pilot journeys complete unaided. Report policy-required handoffs separately and count avoidable screen handoffs as failures.
- Zero wrong-target effects, duplicate submissions, unapproved writes or protected readouts after revocation in the adversarial and reconnect suites. Confirm current access at selection, decision, execution and delivery. Keep the normal audit trail and original actor attribution. [Substrate invariants](../INVARIANTS.md#i-approval-02--preserve-original-actor-and-actual-outcome)
- Measure end of caller speech to first audible acknowledgment and to first useful answer separately. Initial p95 targets: 1.5 seconds to acknowledge, 3 seconds for a bounded read and 300 ms from detected interruption to silenced playback. Report durable-work completion independently. Break out warm and cold browser calls, noise, provider/tool calls per turn, dropped audio, reconnects, wrong-target corrections and review abandonment. Fixture timings do not qualify device latency.

**First demonstration:** “Find the two Acme records. Open the one in Georgetown. What is its due date? Move it to Friday. Actually, make that Monday. Read back the change. Confirm that change. What date is saved?” Verify the exact record and one write. Run again with write access removed after readback. This small journey qualifies the core design before broadening the catalogue or adding a phone number.
