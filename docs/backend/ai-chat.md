# AI Chat — parallel streams and turn lifecycle

Integral's in-app chat is a provider-agnostic SSE proxy backed by persisted
`ChatThread` / `ChatMessage` nodes. The jvagent harness runs embedded inside
the FastAPI process (see [agent/README.md](../../agent/README.md)).

**LLM keys:** platform `OPENAI_API_KEY` and/or per-user BYOK — see
[model-credentials-byok.md](model-credentials-byok.md). Each turn resolves the
workspace owner's credential before `jvagent` runs.

## Architecture

```
Browser (AIChatSurface)
  ├─ per-thread session cache (threadSessionRegistry)
  ├─ N concurrent fetch streams (max 5)
  └─ WebSocket thread_stream_update badges
        │
        ▼
POST /api/chat/threads/{id}/messages  ──► chat_turn_registry (1 turn/thread)
        │                                      │
        └─ chat_streaming.generate_chat_turn_sse ──► ChatBackendProvider
                                                         └─ jvagent embed
```

**Thread ↔ session mapping:** each `ChatThread.provider_session_id` maps 1:1
to a jvagent `session_id`. Parallelism is **across threads**, not within one
thread.

## Invariants (I-CHAT-PAR)

| ID | Rule |
|----|------|
| I-CHAT-PAR-01 | At most **one in-flight turn** per `ChatThread` (409 on conflict) — enforced **per worker**; see the note below |
| I-CHAT-PAR-02 | Up to **5 concurrent streams** across different threads (client cap, and a matching server cap per user — likewise per worker) |
| I-CHAT-PAR-03 | Switching chat tabs **must not abort** background streams |
| I-CHAT-PAR-04 | Each thread keeps an independent **client transcript cache** |
| I-CHAT-PAR-05 | `ChatThread.provider_session_id` ↔ jvagent session is **1:1** |

> **Per worker, not per deployment.** The in-flight turn registry and the
> agent-events websocket map are module-level dicts, so PAR-01 and the per-user
> turn cap hold only within a single process, and websocket pushes reach only
> clients connected to the emitting worker. Run `WORKERS=1`; the server warns at
> boot otherwise. See [ADR-005](adr/005-single-worker-until-shared-turn-state.md)
> for what multi-worker support requires and why a partial fix was rejected.

## HTTP endpoints

All routes live under `/api/chat/*` in `backend/app/api/ai_chat.py`.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/providers` | Registered chat providers + availability |
| GET/POST | `/threads` | List / create threads |
| GET/PATCH/DELETE | `/threads/{id}` | Read / rename / archive thread |
| POST | `/threads/{id}/messages` | User-initiated turn (SSE) |
| POST | `/threads/{id}/agent-turn` | Agent-initiated turn (SSE, workstream foundation) |
| POST | `/threads/{id}/cancel` | Cancel in-flight turn on this thread |
| POST | `/threads/{id}/system-message` | Append non-model message |

### User turn (`POST …/messages`)

1. Persist user `ChatMessage` immediately (survives stream abort).
2. `chat_turn_registry.acquire_turn` — returns 409 if thread already streaming.
3. `generate_chat_turn_sse` streams normalized events; checkpoints assistant
   bubbles on each `message-boundary`.
4. `provider_session_id` from `_meta` events is persisted as soon as seen.
5. `release_turn` + WS `thread_stream_update` (`completed`) in `finally`.

### Agent turn (`POST …/agent-turn`)

Same SSE loop with `extra_data.trigger=agent_workstream` and empty user text.
Used as foundation for proactive / workstream-initiated replies.

### Cancel (`POST …/cancel`)

Sets the in-flight turn's cancel event and invokes
`jvagent.embed.cancel_interact(thread_id=…)`.

## Backend modules

| Module | Role |
|--------|------|
| `app/api/ai_chat.py` | HTTP surface, draft accumulation, persistence |
| `app/services/chat_streaming.py` | Shared SSE turn loop for user + agent turns |
| `app/services/chat_turn_registry.py` | Per-thread in-flight turn tracking |
| `app/services/chat_thread_events.py` | WebSocket `thread_stream_update` / `thread_message` |
| `app/services/chat_proactive_bridge.py` | Proactive push → `ChatMessage` by session id |
| `app/providers/jvagent_embed.py` | Embedded interact + cancel wrapper |

## WebSocket events

Dispatched on the agentive WebSocket connection (`useAgentiveWebSocket`):

| Event | When | Frontend handler |
|-------|------|------------------|
| `thread_stream_update` | Turn start/complete | `integral:thread-stream-update` → thread list badges |
| `thread_message` | Proactive message persisted | Refresh thread transcript when idle |

## Frontend modules

| File | Role |
|------|------|
| `features/ai-chat/threadSessionRegistry.ts` | LRU session cache, streaming thread set |
| `features/ai-chat/useAIChatRuntime.ts` | Per-thread messages, parallel fetch, tab switch |
| `features/ai-chat/components/ThreadList.tsx` | Streaming spinner per thread |
| `features/ai-chat/components/ThreadScrollToEndOnSwitch.tsx` | Scroll to end on tab switch (+ lazy-load follow-up) |
| `api/aiChat.ts` | `cancelThread()` API helper |

## Orchestrator observation budget

Why a turn sometimes re-queried something it had already fetched, and where the
knobs live: `agent/agents/integral/integral_agent/agent.yaml`, on the
`jvagent/orchestrator` action. A distro can set the same budget keys, plus
the model and the persona, in `agent.override.yaml` next to `.env`. The
allowlist is in the
[quick start](../developer/quickstart.md#resident-agent-override). Other
keys in that file are rejected.

jvagent replays this turn's tool results into each loop prompt and elides
anything over a size cap, marking it
`…[N chars elided — re-run the tool if you need the rest]…`. Three settings
govern it:

| setting | jvagent default | integral | governs |
|---|---|---|---|
| `observation_max_chars` | 4000 | **12000** | the most recent results |
| `stale_observation_max_chars` | 600 | **4000** | everything older (must stay ≤ recent; 18000 inverted the taper and drove ~689k-token scaffold storms) |
| `observation_full_recent` | 3 | **5** | how many count as recent |
| `activation_budget` | 24 | **20** | max think-act ticks per turn |
| `max_concurrent_tools` | 1 | **4** | parallel independent grounding reads |
| `planning_heavy_first_tick` | true | **false** | avoid forced `update_plan` before first tool |

The defaults are sized for research-shaped turns, where an older result matters
as "what happened" rather than as payload. Integral's resident mostly does the
opposite — act on what you just found — and there the payload IS the point: a
bulk update needs the entry ids fetched three steps ago.

Both raises were measured on the same prompt against the same data, and both
made turns *cheaper*, which is the counter-intuitive part. A too-small cap buys
one narrower observations section and pays for it with extra ticks, each of
which re-sends that whole section anyway:

- listing question: 7 tool calls / 123.2k tokens / an incomplete answer →
  **2 calls / 20.5k / complete** (with the lean list projection below)
- bulk update: 7 calls / 177.6k / bailed mid-task →
  **6 calls / 120.6k / staged change produced**

Two things that are easy to get wrong here:

- **Raising `observation_max_chars` alone does not help a multi-step turn.** At
  `observation_full_recent: 3`, anything more than three calls back is governed
  by the *stale* cap, so the headline number is not the binding constraint.
- **These values only apply where `JVAGENT_UPDATE_MODE=source`.** Under `merge`
  the persisted action node keeps whatever it was first registered with, and
  editing `agent.yaml` or `agent.override.yaml` does nothing. The server logs
  a warning at boot when `merge` is active and an override file is present.
  See `backend/app/main.py`. One-time land on prod/main
  (source bootstrap or direct node write, then restore merge): ops runbook
  in [docs/ops/DEPLOY.md](../ops/DEPLOY.md#observation-budgets-under-jvagent_update_modemerge).

Tool payload size is the other half: agent list tools project to the fields a
listing is for (`app/agentive/tooling/list_pagination.py`), which cut a Track
from 639 to 167 chars and an App from 944 to 233.

`backend/tests/test_orchestrator_perf_config.py` floors these so a drift back
toward the defaults fails CI.

## Dock / Conversations UI layout (I-CHAT-UI)

Chrome lives in `AssistantDockBody` (Conversations header + Chat/Inbox tabs).
The transcript is `ThreadPrimitive.Viewport` with `turnAnchor="top"` so each
new user turn pins at the top of the scrollport while the assistant streams
below.

| ID | Rule |
|----|------|
| I-CHAT-UI-01 | The **full** user message stays readable after turn-anchor scroll — never clipped under the Conversations header or mid-bubble |
| I-CHAT-UI-02 | Breathing room under the chrome is **padding inside** the anchored user `MessagePrimitive.Root` (`pt-8`), not `scroll-padding` / `scroll-margin` (assistant-ui's manual `scrollTop` ignores those) |
| I-CHAT-UI-03 | Do **not** use assistant-ui's default `topAnchorMessageClamp` (`tallerThan: 10em` / `visibleHeight: 6em`) — it over-scrolls tall prompts so only the **bottom** ~6em stays visible, which reads as a clipped bubble under the header. Integral sets an effectively disabled clamp in `Thread.tsx` |
| I-CHAT-UI-04 | Thread root is `flex-1 min-h-0` (not bare `h-full`) when sharing a column with the onboarding strip, so the composer is not clipped by the dock's `overflow-hidden` |

Source: `frontend/src/features/ai-chat/components/Thread.tsx`,
`frontend/src/features/ai-chat/dock/AssistantDockBody.tsx`.

## Production: multi-worker locking

When running multiple API workers (`deploy.replicas` > 1):

1. **`JVAGENT_UPDATE_MODE=merge`** — required so embedded jvagent action nodes are not delete-recreated on every worker restart (see `agent/README.md`).
2. **`JVAGENT_CONVERSATION_LOCK_REDIS_URL`** — required so the same jvagent `session_id` is not processed concurrently across processes. See `deploy/.env.prod.example`.
3. **`JVSPATIAL_OBSERVABILITY_ENABLED=1`** — enables slow-query logging (`JVSPATIAL_SLOW_QUERY_MS`, default 100ms) and per-request `X-DB-Round-Trip-Count` / `X-DB-Duration-Ms` headers when `INTEGRAL_PERF_HEADER_ENABLED=1`.
4. **`JVSPATIAL_CACHE_GET_SIZE`** + optional **`JVSPATIAL_CACHE_BACKEND=layered`** with Redis — read-through node cache across workers (complements the permissions process cache).

## Related docs

- [agent/README.md — Concurrency contract](../../agent/README.md#concurrency-contract-parallel-conversation-streams)
- [backend/workspace-agent-profile.md](workspace-agent-profile.md) — per-workspace skill overlay at chat time
