# BYOA — Bring Your Own Agent

**Status:** Vision / design reference — **scope reduced 2026-06-03; ratified by [ADR-003](../backend/adr/003-singular-resident-harness.md).** BYOA = Integral's **MCP surface only**. Some surfaces (personal uplink, agent registry) exist under `backend/app/agentive/`; per-user MCP server mount, PAT minting UI, and the canonical Integral skill bundle are roadmap items. See "Implementation status" at the bottom for the current state.

**Companion docs:** [RESIDENT_HARNESS.md](RESIDENT_HARNESS.md) (harness spec), [ADR-003](../backend/adr/003-singular-resident-harness.md) (singular-harness decision), [ARCHITECTURE.md §10.6](ARCHITECTURE.md) (harness architecture), [ARCHITECTURE.md §22.9](ARCHITECTURE.md) (MCP-first API surface), [CONCEPT.md](CONCEPT.md) (agent-native vision). *(Note: ARCHITECTURE §22.7 "Agent ↔ Agent Fabric" is **retired** — external agents use the MCP surface, no A2A.)*

---

> **Scope update — 2026-06-03 (resident-harness decision).** Integral now runs a
> **single resident harness**: the embedded jvagent cockpit app
> (`agent/agents/integral/integral_agent/`), which interfaces with Integral
> **natively** through in-process Integral actions + jv skills — no API hop.
> As a result, **BYOA for external (non-resident) agents reduces to Integral's
> MCP surface**: external agents integrate by calling Integral's MCP server(s),
> and nothing else. The richer multi-surface uplink described below — per-user
> PAT minting, OpenAI Actions / OAuth, direct-REST and SSE uplinks, the uplink
> registry maturation, and a canonical Integral skill bundle pushed *to*
> external agents — is **descoped**. Per-workspace bundle-skill delivery into
> **external** harnesses remains out of scope; the resident cockpit **does**
> merge public App-bundled skills into its Orchestrator surface via
> `WorkspaceAgentProfile` + the jvagent host skill provider (see
> `backend/app/agentive/workspace_agent_profile.py`). External agents integrate
> via MCP tools only. The sections below are retained as historical design
> context — where they describe non-MCP uplink surfaces or skill-bundle delivery
> to external agents, treat them as superseded by this note.

> **Live path (2026-08-02).** External BYOA today:
> - **MCP server:** `backend/app/agentive/mcp/server.py` (`build_mcp_server`) — Streamable HTTP, fail-closed without authenticated principal.
> - **Tool catalogue:** `backend/app/agentive/tool_manifest.yaml` + dispatch bindings in `backend/app/agentive/tooling/bindings.py` (~99 live tools; parity tracked in manifest `status` fields).
> Sections §2 (multi-surface diagram), §3 (OpenAI Actions / Direct REST uplink classes), §4–§5 (PAT minting journey), and §8 (auto-generated-from-FastAPI catalogue) describe **historical / descoped** surfaces unless explicitly marked otherwise below.

## 1. Premise

Integral is the **substrate** — a multi-tenant, containerized knowledge graph with a strong permission model. The **cockpit agent** (`agent/agents/integral/integral_agent/`) is the default in-app surface, but users must not be locked into it. Any agent the user already trusts — Claude Code, Claude Desktop, Cursor, Codex CLI, Zed, a custom Anthropic-SDK script, a ChatGPT GPT — should be able to read from and write to that user's Integral workspace under the same permission model as the cockpit.

BYOA is how that works. It is **not** a parallel set of write paths; it is the same handlers exposed over agent-native protocols, with per-user authn material and the same authz layer.

---

## 2. Architecture

> **Historical (pre-2026-06-03 scope).** The multi-surface diagram below shows the descoped OpenAI Actions / PAT / SSE uplink vision. The **live** external-agent path is MCP-only via `backend/app/agentive/mcp/server.py` + `tool_manifest.yaml` (see scope note above).

```
┌──────────────────────────────────────────────────────────────┐
│  User's machine / browser                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐ │
│  │ Claude   │  │ Codex    │  │ Cursor   │  │ ChatGPT GPT │ │
│  │ Code     │  │ CLI      │  │          │  │             │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬──────┘ │
│       │             │              │                │        │
│   Anthropic         │              │           OpenAI       │
│   Skill +           │              │           Actions      │
│   MCP config        │              │           + OAuth      │
└───────┼─────────────┼──────────────┼────────────────┼───────┘
        ▼             ▼              ▼                ▼
┌──────────────────────────────────────────────────────────────┐
│  Integral backend (multi-tenant, containerized)               │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Uplink surface                                          │ │
│  │  • MCP server  (per-user endpoint, Streamable HTTP)    │ │
│  │  • OpenAI Actions  (OpenAPI 3.1 + OAuth)               │ │
│  │  • Direct REST API  (PAT bearer)                        │ │
│  │  • SSE / WebSocket  (streaming reads, real-time)        │ │
│  └────────────────────────┬───────────────────────────────┘ │
│                           ▼                                   │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Cockpit (house agent) — uses SAME internal handlers    │ │
│  │ via embedded action. Cockpit and BYOA are siblings,    │ │
│  │ not stacked layers.                                     │ │
│  └────────────────────────┬───────────────────────────────┘ │
│                           ▼                                   │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ services/permissions.py  +  services/request_scope.py │ │
│  │ services/workspace_resolver.py                          │ │
│  │ (single authorization layer — no BYOA bypass)           │ │
│  └────────────────────────┬───────────────────────────────┘ │
│                           ▼                                   │
│  jvspatial graph (substrate)                                  │
└──────────────────────────────────────────────────────────────┘
```

**Cockpit and BYOA call the same internal handlers.** No duplicated logic. No second permission model. Single audit log. Single rate limiter.

---

## 3. Uplink classes

> **Historical.** OpenAI Actions and Direct REST PAT uplinks are descoped. **Live:** MCP uplink only (`backend/app/agentive/mcp/server.py` + `tool_manifest.yaml` catalogue).

| Class | Protocol | Target clients | Why it matters |
|---|---|---|---|
| **MCP uplink** *(live)* | Model Context Protocol (Streamable HTTP) | Claude Code, Claude Desktop, Cursor, Continue, Codex CLI, Zed, any MCP-aware host | The 2026 lingua franca. Single surface covers the majority of agentive clients. Implemented in `backend/app/agentive/mcp/server.py`. |
| ~~**OpenAI Actions**~~ *(historical)* | OpenAPI 3.1 + OAuth 2.0 | ChatGPT GPTs, OpenAI Assistants | Descoped — would have reached ChatGPT users via auto-generated Actions from FastAPI OpenAPI. |
| ~~**Direct API**~~ *(historical)* | REST + bearer PAT | Custom agents on Anthropic SDK / OpenAI SDK / LangChain / etc. | Descoped — PAT minting UI and bearer-auth uplink not shipped. |

MCP is **the only shipped BYOA surface.** Historical rows above are retained for design context only.

---

## 4. User journey (MCP example)

> **Historical / partial.** PAT minting UI (`Settings → Connected Agents`) and per-user MCP endpoint URLs described below are not fully shipped. External MCP clients authenticate through the live server in `backend/app/agentive/mcp/server.py`; tool discovery reads `tool_manifest.yaml`.

1. User opens Integral → **Settings → Connected Agents → Add Claude Code**.
2. Integral mints an **uplink token** scoped to user identity. Token carries:
   - `workspaces`: whitelist of workspace ids the token can address (defaults to all the user has access to; user can narrow).
   - `actions`: `read` / `propose` / `write` — defaults to `read + propose` (writes require staged-change approval in Integral UI; see §6).
   - `expires_at`: default 90 days, configurable.
   - `rate_limit`: e.g. 60 calls/minute.
3. UI shows a copy-pasteable MCP config snippet:
   ```json
   {
     "mcpServers": {
       "integral": {
         "transport": "http",
         "url": "https://integral.example.com/mcp/u_abc123",
         "headers": { "Authorization": "Bearer <token>" }
       }
     }
   }
   ```
4. User pastes into `~/.claude/mcp.json` (or the client equivalent) and restarts.
5. Client's MCP discovery handshake hits Integral's MCP server, which returns the tool catalogue (`integral_query_entries`, `integral_get_entry`, `integral_propose_create_entry`, `integral_describe_substrate`, …).
6. In Claude Code: *"What's blocking the Scrubs deal?"* → Claude calls `integral_query_entries(track="Opportunities", filter="Scrubs")` → gets data → answers from real substrate.

Integral dashboard shows: **Claude Code (Eldon's MBP) — last seen 2m ago — 14 calls today**. Revoke button beside it.

---

## 5. Token model

> **Historical.** UplinkToken / PAT entity model below is design reference; per-user PAT minting UI is not shipped. Live MCP auth uses OAuth principal resolution in the MCP server middleware.

Uplink tokens are first-class entities in the substrate — not opaque secrets. Each token = a `UplinkToken` node (or equivalent record under the existing `AgentConfig` model in `backend/app/agentive/`) connected to the owning `User` and carrying:

```yaml
uplink_token:
  id: ut_abc123
  user_id: u_eldon
  kind: mcp | openai_actions | rest_pat
  label: "Claude Code on MBP"        # user-supplied for UI
  scopes:
    workspaces: [ws_personal, ws_acme_inc]
    actions: [read, propose]         # write requires explicit grant
    expires_at: 2026-08-01T00:00:00Z
    rate_limit: 60/min
  client_meta:
    user_agent: "claude-code/1.18.0"
    last_seen_at: 2026-05-18T19:43:12Z
    call_count_today: 14
  revoked_at: null
```

**Authn:** the bearer token is the only material an external client sees. Server-side, every request resolves token → `UplinkToken` node → owning `User` → request scope.

**Authz:** every uplink call routes through `services/permissions.py` and `services/request_scope.py` — the exact same authorization layer cockpit calls. The token cannot grant access beyond what the owning user already has, and per-token scopes can only **narrow** that access, never broaden it.

**Audit:** every uplink call writes an entry to the audit log with `token_id`, `tool_name`, `target_resource`, `outcome`. Users see their own activity; admins of org workspaces see uplink activity within their workspaces.

---

## 6. Write safety — staging mirror

Cockpit's `prepare_*` → user approval → `execute_*` pattern is the safety story for all mutations. BYOA preserves it:

- **Default uplink scope = `read + propose`.** `propose` means external agents can mint staged changes (`StagedChange` envelopes) but cannot execute them.
- Staged changes appear in the **same Integral UI inbox** the cockpit uses — there is no separate "approvals from external agents" queue.
- A user can opt a specific token into `write` scope (auto-execute, no approval card). Recommended for personal-workspace, short-expiry tokens only; default off.
- Org policy (§9) can force `propose`-only across the workspace.

This means the UX of "Claude Code wants to file this into your Contacts track" is identical to "the cockpit wants to file this into your Contacts track" — same approval card, same audit trail.

---

## 7. The Anthropic Skill bundle (SOP carrier)

Tools alone are not enough; agents also need **instructions** for how to use them well. Integral ships a canonical Anthropic-format skill bundle that users install into their Claude Code:

```
integral-skill/
├── SKILL.md                       # SOP: introspect-first, propose-then-execute, etc.
├── references/
│   ├── operational-models.md        # how to use integral_describe_substrate
│   ├── workflow-patterns.md       # filing, drill-down, save_view
│   └── permission-model.md
└── scripts/
    └── quickfile.py               # local helper, runs on user's machine
```

User runs `claude skill install integral` (or equivalent). Now their Claude Code has:

1. **MCP tools** (network surface → Integral substrate)
2. **SKILL.md SOP** teaching Claude *how* to use those tools idiomatically (introspect substrate before mutating, prepare before execute, never invent track ids, surface error envelopes verbatim, etc.)

**Why this is the right pattern.** The skill lives on the **user's machine**, not on Integral's server. It sidesteps every multi-tenant constraint analyzed during the cockpit/Claude-skill compat investigation: no shared filesystem, no shared bash, no shared process. Integral's server only sees MCP calls, which are stateless, scoped, and auditable.

**Per-host SOP carriers** — same recipe, different format:

| Host | Tool surface | SOP carrier |
|---|---|---|
| Claude Code / Desktop | MCP | Anthropic Skill bundle (`~/.claude/skills/integral/`) |
| Cursor | MCP | `.cursorrules` snippet (or future Cursor skill format) |
| Codex CLI | MCP (now supported) | Codex skill format |
| Zed | MCP | Zed extension manifest |
| ChatGPT GPT | OpenAI Actions | GPT instructions field |
| Custom agent (SDK) | REST / SDK | Docs page + prompt snippets we publish |

---

## 8. Tool catalogue (MCP)

> **Historical note on auto-generation.** The "auto-generated from FastAPI handlers" approach (ARCHITECTURE §22.9 vision) is **not** the live catalogue. The shipped surface is the hand-maintained manifest at `backend/app/agentive/tool_manifest.yaml` with bindings in `backend/app/agentive/tooling/bindings.py`, exposed through `backend/app/agentive/mcp/server.py`.

The MCP catalogue mirrors the resident skill set, named with the `integral_` prefix. Live inventory is in `tool_manifest.yaml` (~99 `existing`, ~1 `gap`).

Read tools (no approval needed):
- `integral_describe_substrate` — dump field types, view types, plugin registry
- `integral_describe_model` — operational model for a space or track
- `integral_list_workspaces`, `integral_list_spaces`, `integral_list_tracks`
- `integral_list_entries`, `integral_get_entry`, `integral_query_entries`
- `integral_activity_digest`, `integral_count_entries`
- `integral_get_model_draft`, `integral_diff_model_draft`
- `integral_transcribe_audio` — transcribe an audio attachment with the workspace's speech-to-text provider (bound workspace only; spends that workspace's provider quota)

Stage-and-approve tools (propose scope):
- `integral_propose_create_entry` / `integral_propose_update_entry` / `integral_propose_delete_entry`
- `integral_propose_file_content` (smart filing from freeform text)
- `integral_propose_save_view`
- `integral_propose_model_revision`

Execute tools (write scope, optional):
- `integral_execute_<staged_token>` — only callable when the token has `write` scope; otherwise the staged change must be approved in Integral UI.

~~The catalogue should be **auto-generated from FastAPI handlers** (per ARCHITECTURE §22.9), not hand-maintained. Tool descriptions are pulled from endpoint docstrings.~~ *(Historical — superseded by `tool_manifest.yaml` + bindings; see note above.)*

---

## 9. Org policy

Workspaces (especially org-kind) expose BYOA controls:

```yaml
workspace.byoa_policy:
  allowed_uplinks: [mcp, openai_actions]   # or [] to fully disable
  allow_user_pats: true                    # allow members to mint personal tokens
  require_admin_approval_for_first_use: false
  default_scopes: [read]                   # write/propose require explicit grant
  allow_write_scope: false                 # writes always require approval card
  audit_external_calls: true               # default true; cannot be disabled in regulated orgs
  rate_limit_per_token: 60/min
```

Lets enterprises adopt cockpit-only while letting individual personal workspaces opt into BYOA. Default policy is permissive for personal workspaces, conservative for org workspaces.

---

## 10. Where cockpit (house agent) still wins

BYOA gives users choice. Cockpit stays the default because it has structural advantages no external agent can match:

1. **Embedded UX.** Real-time SSE, approval cards, scope chips, in-app chat surface, "shared with me" affordances. External MCP clients render plain chat with tool-call traces.
2. **Substrate intimacy.** Cockpit SOPs are tuned per release. Substrate evolves, cockpit moves with it. External skills lag the deployed substrate by their own update cadence.
3. **Org-managed agent.** Admins ship cockpit with org-wide operational-model knowledge, security policies, audit defaults. BYOA = per-user opt-in.
4. **Cross-skill orchestration.** Cockpit can chain workspace → entries → filing in one turn (see also the `coactivate-with` + dynamic `skill_activate` work in jvagent). External clients are chattier — every drill-down is a visible round-trip.
5. **No client install required.** Works in the browser. Best onboarding path.

Positioning: **cockpit = default, friction-free, in-app. BYOA = power user, choose-your-own-model, lives outside.**

---

## 11. Multi-tenant constraints (what BYOA does *not* unlock)

Because Integral is a multi-tenant service, BYOA does **not** mean we run user-supplied code server-side. Specifically:

- **No bash execution server-side.** External agents bring their own compute. Skill bundle scripts run on the user's machine, not Integral's.
- **No persistent filesystem per agent.** All state goes into the substrate (jvspatial nodes). Agents that need scratch space use their local machine.
- **No long-running server-side processes per uplink.** MCP requests are short-lived, stateless, rate-limited.

This is the inverse of Claude Code's local-machine model. Both are valid; they describe different deployment surfaces.

---

## 12. Implementation status (2026-08)

**Live:**
- `backend/app/agentive/mcp/server.py` — MCP server (Streamable HTTP), tool list/call handlers.
- `backend/app/agentive/tool_manifest.yaml` + `backend/app/agentive/tooling/bindings.py` — tool catalogue and dispatch (~99 live tools).
- `backend/app/agentive/uplink.py` — personal + system uplink registration/heartbeat (resident-facing, not full BYOA PAT flow).

Existing under `backend/app/agentive/` (partial / resident-facing):
- `agent_tools.py` — legacy `MCP_TOOLS` / `SKILLS` dicts (superseded by manifest + bindings for external MCP)
- `uplink_registry.py` — `AgentUplinkRegistry` (in-memory + `AgentConfig` persistence)
- ARCHITECTURE.md §10.6 documents the **always-on** agentive ops layer (historical `AGENTIVE_ENABLED` conditional-load language is retired — that flag is not a live boot gate).

Roadmap (in approximate order):
1. ~~**Per-user MCP server endpoint**~~ — **partially live** in `mcp/server.py`; PAT minting + per-user endpoint URLs still roadmap.
2. **PAT minting + scopes UI** — `Settings → Connected Agents`. Token CRUD, scope editing, revocation, activity feed. ~2 days.
3. **Canonical `integral-skill` Anthropic bundle** — published to a public skill registry / GitHub repo. ~1 day to draft + iterate.
4. ~~**MCP-first endpoint introspection**~~ — **superseded** by manifest + bindings; auto-register from `@endpoint` remains optional future work (ARCHITECTURE §22.9).
5. ~~**OpenAI Actions OAuth flow**~~ — **descoped / historical** (see scope note at top).
6. **Audit log surface for uplink activity** — extends existing audit log. ~1 day.
7. **Workspace BYOA policy controls** — frontmatter + admin UI. ~1 day.
8. ~~**A2A fabric**~~ — **RETIRED** ([ADR-003](../backend/adr/003-singular-resident-harness.md)). No agent-to-agent calls inside Integral; external agents coordinate through the shared substrate via the MCP surface.

Steps 2–3 unlock smoother onboarding for the dominant 2026 client ecosystem (Claude Code, Cursor, Codex CLI, Zed) on top of the live MCP server.

---

## 13. Relationship to cockpit's internal skill-activation work

This document covers **external** agents calling Integral. The parallel work on jvagent — declarative `coactivate-with` frontmatter and a dynamic `skill_activate` harness tool — is **cockpit-internal** and orthogonal to BYOA. Improving cockpit's skill-loading discipline makes the in-app surface more reliable; BYOA gives users the option to bypass it entirely with a different agent. The two efforts reinforce each other: a more reliable cockpit makes the cockpit-vs-BYOA decision a real preference rather than a fallback to the cockpit's failure modes.
