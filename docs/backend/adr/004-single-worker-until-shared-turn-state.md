# ADR 004 — One worker until turn state is shared

**Status:** Accepted (shipped — single-worker is normative; boot warns when
`max(WORKERS, WEB_CONCURRENCY, 1) > 1`)
**Date:** 2026-08

## Context

Two pieces of chat state live in module-level dicts, i.e. per **process**:

- `chat_turn_registry._in_flight` — the in-flight turn per thread, which backs
  I-CHAT-PAR-01 (one active turn per thread) and, since the turn-admission
  work, `MAX_CONCURRENT_TURNS_PER_USER`;
- `agent_events._agent_event_connections` — the websocket map used to push
  staged-change and thread-status events to a user's open tabs.

Under a single worker both are correct. Under `WORKERS>1` they degrade, and
they degrade *silently*:

1. **One turn per thread holds only within a worker.** Two workers can each
   admit a turn for the same thread, so the same conversation answers twice.
2. **The per-user cap is per worker.** The real ceiling is limit × workers, so
   the admission control that exists to bound concurrent LLM streams does not
   bound them.
3. **Websocket fan-out reaches only the emitting worker's clients.** A staged
   change minted on worker A never lights the inbox badge of a tab connected
   to worker B — the exact class of "surfaces disagree about what is pending"
   bug the agent inbox was built to end.

None of this announced itself. Worse, `.env.example` recommended `WORKERS=4`
for production, so the documented posture was the broken one: following the
guidance produced the fault.

## Decision

**Ship one worker. Do not partially fix this.**

1. `.env.example` says `WORKERS=1` / `WEB_CONCURRENCY=1`, with the three
   consequences named. The server warns at boot when
   `max(WORKERS, WEB_CONCURRENCY, 1) > 1` (Docker drives uvicorn via
   `WEB_CONCURRENCY`). *(Shipped.)*

2. **Multi-worker support requires the turn registry AND websocket fan-out to
   move to shared state, together, as one unit of work.** Doing only the
   registry is explicitly rejected: it would make the visible symptom (double
   answers) disappear while leaving real-time updates broken, and it would
   read as "multi-worker is fixed now", inviting exactly the `WORKERS=4` that
   is unsafe. A half-fix here is worse than the current honest limitation.

3. Until that work lands, `WORKERS>1` is unsupported rather than discouraged.

## What the real fix requires

Recorded so the next person does not re-derive it:

- **Shared turn registry.** Postgres is already a dependency, so a
  `StagedChangeRecord`-style row keyed by `thread_id` with the acquiring
  worker's identity is the cheap path; Redis is the alternative if turn-start
  latency proves sensitive. Either way it adds a round trip to every turn
  start, which is the main cost to weigh.
- **TTL / liveness.** A worker that crashes mid-turn must not hold a thread
  forever. Needs a heartbeat or an expiry the acquire path sweeps, mirroring
  `staging_store`'s lazy-expiry approach.
- **Websocket fan-out across workers.** A pub/sub channel (Postgres `LISTEN`/
  `NOTIFY` or Redis) so an event emitted on one worker reaches connections held
  by another. This is the half most easily forgotten, because nothing fails
  loudly when it is missing — the push simply never arrives.
- **The per-user cap becomes a shared count**, falling out of the shared
  registry rather than needing its own mechanism.

## Consequences

- Single-worker throughput is the ceiling until this is done. For the current
  deployment shape that is acceptable; it will not stay acceptable.
- The boot warning is a stopgap, not a control: nothing prevents `WORKERS=4`.
  Making it a hard refusal was considered and rejected — it removes a scaling
  lever from operators who may have reasons we do not know, and the warning
  plus corrected guidance addresses the actual failure, which was silence.
- I-CHAT-PAR-01 should be read as "per worker" until this lands. That
  qualification belongs in `docs/backend/ai-chat.md` when the invariant is next
  revised.
