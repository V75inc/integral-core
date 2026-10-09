# Scaffold validation and recovery

Integral validates typed sample records before saving a proposed app design.
Every sample must fit one declared entry type, supply its required fields, and
use a declared choice for select fields. Track-expansion relations are supplied
by the runtime. Validation errors create no app or track.

An applied build receipt prevents another execution. An incomplete build retains
its original batch token and durable successful-operation cursor. Resuming the
same approved revision uses that cursor, including resource IDs needed by later
relations, without repeating completed operations.

If unfinished samples need corrected values, the assistant proposes the complete
revised blueprint. The app, tracks, schema, views, routines and sample identities
must remain unchanged; only fields on unapplied samples may change. The user
approves that revision before execution. Integral then creates a replacement
immutable batch with the original successful prefix and cursor, retires the old
token and binds the conversation to the replacement in one PostgreSQL transaction.
If any of those writes fail, none of them commits. An in-flight, expired, foreign
or unavailable batch refuses repair and requires inspecting the existing app.
It never grants permission to create a second app. JSON storage cannot perform
this transactional replacement and fails closed.

The normal governed executor still checks current permissions and work-item
authority. A previous turn's approval does not freeze later conversation replies;
once the current run chooses build, its design becomes immutable for that run.
Reading conversation artifacts remains available while a design is pending.

This preserves I-HARNESS-01 (principal/workspace/session isolation), I-WORK-01
(current execution authority), I-WORK-02 (successful effects are not replayed),
I-WORK-03 (atomic replacement) and I-WORK-05 (approval binds the revised payload).
Staging records remain `Object` records under I-GRAPH-02; no graph nodes or
domain-specific substrate behavior are introduced.

## Scheduled checks read current state

Each native scheduled run has a new host event containing its current instruction.
An empty human utterance must not be treated as continuing an old assistant answer.
The resident reads current records through scoped tools before reporting on them.
Historical digests are context, not evidence of the current state.

When the routine asks to remain silent if nothing qualifies, the resident may
return the exact reserved quiet-output signal. Core retains the harness checkpoint,
execution trace and usage records, but does not append a chat message. A failed
run is still recorded as a failure; an ordinary answer cannot trigger suppression.
