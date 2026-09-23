# Integral Agent

This directory hosts the [jvagent](https://github.com/TrueSelph/jvagent)
application that backs Integral's in-app AI chat. It runs **embedded**
inside the integral FastAPI backend — there is no separate process,
port, or database.

```
┌──────────────────────────────────────────────────────────────────┐
│  integral backend · :4000                                        │
│  ┌─────────────────────────┐    ┌─────────────────────────────┐  │
│  │ FastAPI + jvspatial DB  │◀──▶│ jvagent runtime (embedded)  │  │
│  │ /api/* (host endpoints) │    │ orchestrator + actions + skills │  │
│  └─────────────────────────┘    └─────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

`backend/app/main.py:_startup` calls `jvagent.embed.bootstrap(app_root="agent")`
when `agent/app.yaml` exists. Bootstrap walks this directory, registers the
App + Agents + Actions described in `app.yaml`, and ensures jvagent's
schema indexes on the host's database. The jvspatial default context
(database, auth, request scope) is shared.

The action-registration `update_mode` defaults to `source` and is
overridable via the `JVAGENT_UPDATE_MODE` env var — see
[Action update mode](#action-update-mode) below.

## Layout

```
agent/
├── app.yaml                    # App identity + agents list + runtime config
├── .env.example                # OPENAI_API_KEY template
├── .gitignore
├── README.md                   # This file
└── agents/
    └── integral/
        └── integral_agent/
            ├── agent.yaml      # Orchestrator (-200) + reply + intro + openai_lm + ollama_lm + embedded_integral_action
            └── actions/
                └── integral/
                    └── embedded_integral_action/   # In-process tool surface (ADR-0012)
                        ├── SKILL.md                # Base SOP (extends source; not discovered)
                        └── skills/                 # Action-backed integral_* SOP overlays
                            ├── integral_identity/
                            ├── integral_workspace/
                            ├── integral_entries/
                            ├── integral_models/
                            ├── integral_insights/
                            └── integral_filing/
```

`integral_agent_db/` (and any `jvagent_db*` / `jvagent_logs*` dirs) are
leftovers from the previous standalone-runtime layout and are ignored at
runtime — embed mode uses the host's database (see
`backend/app/main.py:server.database`). Safe to delete after confirming
no in-flight migrations depend on them.

## Prerequisites

- A working integral backend venv with `jvagent` installed:

  ```bash
  cd backend
  source venv/bin/activate
  uv sync --frozen --extra dev --extra test   # jvagent from backend/uv.lock
  ```

- `OPENAI_API_KEY` set in `backend/.env` (NOT `agent/.env` — see warning
  below).

## LLM credentials (platform + BYOK)

| Mode | When |
|------|------|
| **Platform** | `OPENAI_API_KEY` in `backend/.env` — default when owner has no BYOK |
| **Per-user BYOK** | Settings → Agents → Model API key; **workspace owner's** key + models |
| **Strict BYO** | `INTEGRAL_AGENT_KEY_MODE=byo_strict` — no platform fallback |

**Operator setup:** generate `INTEGRAL_CREDENTIAL_ENC_KEY` (`openssl rand -base64 32`), set in `backend/.env`, restart backend. Full guide: [docs/backend/model-credentials-byok.md](../docs/backend/model-credentials-byok.md).

Requires jvagent **0.1.6** (embedded Orchestrator + host skill providers).

## First-time setup

In `backend/.env`:

```bash
OPENAI_API_KEY=sk-…
```

Optional BYOK operator keys — see [docs/backend/model-credentials-byok.md](../docs/backend/model-credentials-byok.md).

The active agent is picked by the user via the `/agent` surface and
persisted on each `ChatThread` (per-user × per-workspace preference);
no environment knob selects an agent at boot.

Embed bootstrap runs on every backend start when `agent/app.yaml` exists.
No feature flag required. A published wheel (`0.1.1rc6` and later) carries
a copy of this directory inside the package. A checkout uses this `agent/`
tree.

A distro may add `agent.override.yaml` next to its `.env`. That file can
change `context.alias`, `context.role`, `context.interaction_limit`, and
the orchestrator model and budget numbers. Unknown keys and extra actions
are rejected. The [App developer quick start](../docs/developer/quickstart.md#resident-agent-override)
lists the allowlist. `JVAGENT_UPDATE_MODE=source` applies it on restart.
`merge` keeps the context already stored.

> **`agent/.env` is NOT loaded under embed mode.** The integral backend
> only loads `backend/.env`. Putting keys in `agent/.env` will have no
> effect on the running process. Earlier guidance suggesting otherwise
> caused a database-pointer regression (legacy `JVSPATIAL_DB_*` lines in
> the standalone-mode `agent/.env` shadowed the host's DB config). The
> file is retained for users running the standalone `jvagent .` server.

## Action update mode

`embed.bootstrap()` is called with an `update_mode` resolved from the
`JVAGENT_UPDATE_MODE` env var (`backend/app/main.py:_jvagent_update_mode`,
declarative default on `Settings.JVAGENT_UPDATE_MODE`). It controls what
happens to an action node that already exists in the DB when bootstrap
re-registers it:

| `JVAGENT_UPDATE_MODE` | Behavior | Use |
|-----------------------|----------|-----|
| `source` (**default**) | Delete + recreate each action node from `agent.yaml` every restart — YAML is the source of truth. `context.*` overrides (model, skills, prompts, `response_mode`, routing flags) always propagate. | Development. |
| `merge` | Update action metadata + `module_path` in place; preserve runtime property values (API-driven drift survives restarts). | Production. |
| `run` | Skip existing actions; only register new ones. | Frozen / read-only boots. |

Invalid values fall back to `source` with a warning. `run` maps to the
bootstrap `None` sentinel internally.

> **Multi-worker caution.** `source` mode deletes-then-recreates and is
> **not concurrency-safe** across processes. With `WORKERS>1` (or
> `--reload`), every worker runs `_startup` and races the same global
> agent graph on the shared DB → `duplicate key value violates unique
> constraint "node_context_agent_id_context_label_uniq"` on
> `(agent_id, label)`. Use `JVAGENT_UPDATE_MODE=merge` for multi-worker /
> production boots.

## Running

```bash
cd backend
venv/bin/python -m app.main
```

Watch startup log for the embed banner:

```
jvagent embed bootstrap starting (app_root=…/agent, ensure_admin=False)
Index migration: ensuring indexes for N entity classes
Bootstrap: Application graph (sync mode)
App: Integral Agent: v0.1.0
Initialized rate limiter: 60 req/min, max_utterance_length=…
```

The integral OpenAPI surface stays at <http://localhost:4000/docs>; no
second `:8787` jvagent server.

## Calling the agent

Embed mode does **not** mount jvagent's `POST /agents/{id}/interact`
endpoint on the host's FastAPI app. Instead, integral exposes its own
chat endpoint that delegates into jvagent via the embed surface. Today
that goes through the legacy `ai-chat` SSE proxy; the planned PR2
exposes a pure-Python `embed.interact(agent_id, utterance, *, user_id,
session_id)` callable that integral can wrap with its own JWT auth and
mount as a single endpoint. See the `Open work` section below.

## Extending the agent

The agent follows the jvagent **Orchestrator** pattern (ADR-0012): one
`OrchestratorInteractAction` at weight `-200` runs a think-act-observe
loop over a unified tool surface. Tools come from each enabled action's
`get_tools()`; skills are SOP overlays that coordinate those tools. To
grow it:

- **Capabilities** — add an action that implements
  `async def get_tools(self) -> List[jvagent.tooling.tool.Tool]`. The
  orchestrator calls it and exposes each `Tool` by `Tool.name` verbatim.
  Integral's own surface lives in
  [`EmbeddedIntegralAction`](agents/integral/integral_agent/actions/integral/embedded_integral_action/embedded_integral_action.py)
  (see `integral_tools.py`).
- **Skills** — drop action-backed `SKILL.md` bundles under
  `agents/integral/integral_agent/actions/integral/embedded_integral_action/skills/`.
  Declare `extends: action:integral/embedded_integral_action` and
  `requires-actions: [EmbeddedIntegralAction]`. SOP-only — no `scripts/`.
  The orchestrator loads skills per `skills_source` / `skills` in
  `agent.yaml` (currently `app` / `-all`).
- **Tool servers** — wire MCP servers via the `jvagent/mcp` action; tools
  surface as `mcp_<server>__<tool>` (set `tool_servers` on the
  orchestrator).
- **Document retrieval** — add `jvagent/pageindex_action` (requires
  `pip install jvagent[pageindex]`).
- **Web search** — add `jvagent/serper_web_search` (set `SERPER_API_KEY`).

See `jvagent/examples/jvagent_app/agents/jvagent/orchestrator_agent/agent.yaml`
in the jvagent source repo for a fuller example.

> **Two-tier skill model.** The resident agent always loads the **base profile**
> — thirteen `integral_*` action-overlay SOP skills plus the full Integral tool
> manifest — in every workspace. On top of that, a **workspace overlay**
> merges public declarative skills from installed App bundles for the active
> workspace (`X-Integral-Scope`). Composition is handled by
> `backend/app/agentive/workspace_agent_profile.py` and surfaced to jvagent via
> a host skill provider registered at embed bootstrap.
>
> **Two skill authoring contexts — don't confuse them.** Skills under
> `actions/integral/embedded_integral_action/skills/` are **base-tier**
> capabilities every Integral install should have.
>
> **App-bundled skills** are declared in a OperationalModel manifest's
> `app.skills[]` section. Each skill is `skills/<key>/SKILL.md` under the
> bundle directory. Skills that call `integral_*` tools should declare
> `extends: action:integral/embedded_integral_action` (same base SOP as resident
> `integral_*` skills) plus `requires-actions: [EmbeddedIntegralAction]`.
> They register on App install and merge into the workspace overlay at runtime.
> See [docs/backend/app-bundles-v1.md §5.2.1](../docs/backend/app-bundles-v1.md#521-extending-the-embedded-integral-base-sop-required-for-integral-tools)
> and [docs/backend/workspace-agent-profile.md](../docs/backend/workspace-agent-profile.md).
>
> Quick rule of thumb: if it's a capability every integral install should have,
> it's a base orchestrator skill set here. If it's specific to a domain App
> (CRM, Content Factory, etc.), it belongs in that App's OperationalModel manifest
> and surfaces dynamically when that App is installed in the active workspace.

## Skill bundles

Skills under
`agents/integral/integral_agent/actions/integral/embedded_integral_action/skills/`
are action-backed `SKILL.md` bundles (ADR-0020) — SOP overlays, no executable
code:

```
actions/integral/embedded_integral_action/
├── SKILL.md                              # Base procedure (extends source)
└── skills/<skill_name>/
    └── SKILL.md                          # extends + allowed-tools + custom SOP
```

The bundle's `allowed-tools` list references tool names exposed by an
action's `get_tools()`; activating the skill surfaces those tools to the
orchestrator. The Integral tool surface is provided by
[`EmbeddedIntegralAction`](agents/integral/integral_agent/actions/integral/embedded_integral_action/embedded_integral_action.py),
which calls integral's endpoint handlers **in-process** (no HTTP, no
service-auth bridge). Tools are named `integral_<area>__<op>` (e.g.
`integral_workspace__prepare_create_track`).

Mutations follow a **prepare → user approval → execute** staging
discipline: a `prepare_*` tool mints a `StagedChange` the chat surface
renders as an approval card; the matching `execute_*` consumes the
confirmation token and commits.

| Skill | Tools | Purpose |
|-------|-------|---------|
| `integral_identity` | `whoami` | Resolve the active user's profile (smoke test). |
| `integral_workspace` | `list_apps`, `get_app`, `list_tracks`, `get_track`, `prepare/execute_{create,update,delete}_track` | Read + manage Apps and Tracks. |
| `integral_entries` | `list_entries`, `get_entry`, `prepare/execute_{create,update,delete}_entry` | Read + manage entries inside a track. |
| `integral_models` | `list_library_profiles`, `get_attached_operational_model`, `prepare/execute_{author,modify,apply_library}_profile` | Inspect + author/modify OperationalModel schema. |
| `integral_insights` | `query_entries`, `count_entries`, `activity_digest`, `prepare/execute_save_view` | Query, analyze, and persist views. |
| `integral_filing` | `prepare/execute_file_content` | Smart-file freeform content into the right track/type. |

## Concurrency contract (parallel conversation streams)

Integral AI chat maps **one `ChatThread` ↔ one jvagent `session_id`**. Parallelism is **across threads**, not within a single thread:

| Rule | Detail |
|------|--------|
| One in-flight turn per thread | `POST /api/chat/threads/{id}/messages` returns 409 if a turn is already running on that thread |
| Parallel tabs | Different threads for the same user+agent may stream concurrently (each owns a distinct jvagent session) |
| Tab switch | Frontend keeps per-thread stream state; switching tabs does not abort background streams |
| Cancel | `POST /api/chat/threads/{id}/cancel` cancels the in-flight walker via `jvagent.embed.cancel_interact` |
| Agent workstreams | `POST /api/chat/threads/{id}/agent-turn` starts an agent-initiated stream (`data.trigger=agent_workstream`) |

For multi-worker production, configure jvagent's distributed conversation lock (Redis) so same-session turns do not fork across processes. See `JVAGENT_CONVERSATION_LOCK_*` in `deploy/.env.prod.example`.

Full architecture, endpoints, and frontend module map: [docs/backend/ai-chat.md](../docs/backend/ai-chat.md).

## Open work (deferred)

These items still reflect the prior standalone-runtime model and need
follow-up passes before they line up with embed mode:

1. **`jvagent.embed.interact()` extraction** — pure-Python interact
   callable in jvagent so integral can mount its own auth-fronted
   `POST /api/agent/{id}/interact` endpoint and stop relying on the
   `ai-chat` SSE proxy + `JVAGENT_BASE_URL` indirection.
2. **Native integral actions** — ✅ DONE. `EmbeddedIntegralAction` calls
   integral's endpoint handlers in-process (no localhost HTTP, no
   service-auth bridge). The HTTP `IntegralApiAction` is retained only
   for an out-of-process deployment and would need its own `get_tools()`
   before it could run under the orchestrator.
3. **Decommission service-auth bridge** — the embedded action no longer
   uses it; once no out-of-process path depends on it, delete
   `backend/app/agentive/middleware/service_auth.py`,
   `INTEGRAL_SERVICE_KEY`, and the related env on both sides.
4. **Decommission parallel runner** — `scripts/dev.sh` is no longer
   the canonical dev loop. Remove or refit to a single-process command
   once the points above are done.
5. **Decommission `JVAGENT_BASE_URL` plumbing** — `ai_chat.py` SSE
   proxy still dials a separate jvagent over HTTP; replace with the
   embedded `interact()` path from item 1.
