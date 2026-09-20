# Integral × JV Harness — Full Sweep Review

**Date:** 2026-09-08
**Branch:** `dev` (synced with `origin/dev` at review start)
**Status:** Living checklist for Waves 1–8 of the Full Harness Sweep
**Framing lock:** Integral is an **ops layer** on a pluggable harness (default: embedded jvagent). The Harness Switcher is intentional. ADR-003 “singular resident” applies **per active harness binding**, not as a ban on provider selection.

---

## 1. Architecture map

### Layers

| Layer | Role | Canonical loci |
|-------|------|----------------|
| **Harness providers** | Model/orchestration mind (jvagent embed, Echo smoke, future) | `agent/`, `providers/jvagent_embed.py`, `MockEchoProvider` |
| **Ops layer (Integral)** | Substrate, permissions, staging, skills overlay, MCP perimeter, inbox/feed | `backend/app/` + `frontend/src/features/ai-chat/` |
| **External agents** | Same catalogue via MCP only (no A2A) | `backend/app/agentive/mcp/server.py`, [BYOA.md](../product/BYOA.md) |

### Request path (resident turn)

```
UI / MCP client
  → JWT principal + X-Integral-Scope
  → chat turn OR MCP call_tool
  → tool_manifest bindings + policy_gate
  → propose → StagedChange → human bless → execute via real handlers
  → ChangeEvent audit
```

### Specs

- [RESIDENT_HARNESS.md](../product/RESIDENT_HARNESS.md)
- [ADR-003](../backend/adr/003-singular-resident-harness.md) — singular mind per binding; A2A retired
- [ADR-001](../backend/adr/001-model-credentials-byok.md) — credentials
- [ADR-002](../backend/adr/002-contributed-skill-safeguard.md) — marketplace safeguards (design → Phase 1 in Wave 6)
- [workspace-agent-profile.md](../backend/workspace-agent-profile.md)
- [INVARIANTS.md](../INVARIANTS.md) — I-AUTH, I-RET, I-SCRATCH, I-HOOK, I-GRAPH

### ADR-003 ↔ Harness Switcher

| Claim | Interpretation after framing lock |
|-------|-----------------------------------|
| One resident per deployment | One **active** mind for the bound provider; facets narrow that mind |
| No peer-agent fleet / A2A | Switcher picks **provider** for the ops layer — not N cooperating agents |
| Facets (personal / org-facing / system) | Policy postures on the bound harness, not separate agent configs |

---

## 2. Security findings

| ID | Sev | Finding | Wave |
|----|-----|---------|------|
| S1 | High | `INTEGRAL_SERVICE_KEY` + arbitrary `X-Integral-User-Id` impersonation | 1 |
| S2 | High | No rate limits on `/api/mcp`, `/api/agentive/*`, chat turns | 1 |
| S3 | High | Session autonomy auto-bless; prompt-injection / “always approve” blast radius | 1 |
| S4 | High | Collaborator share links: unlimited redemptions | 1 |
| S5 | Med | `TESTING=1` + `DEBUG=true` TestAuthBypass footgun | 1 |
| S6 | Med | Coarse OAuth scope; MCP lists full catalogue | 1 + 5 |
| S7 | Med | Credential crypto DEBUG fallback couples ciphertext to `SECRET_KEY` | 1 |
| S8 | Med | Unauth invitation preview metadata | 1 |
| S9 | Med | Public-share write spam / injection | 1 |
| S10 | Low | CSP `unsafe-inline` styles / jsdelivr | 1 |
| S11 | Low | HSTS / CORS prod knobs documentation | 1 |

**Well done:** workspace gate on tools; staging re-enters real handlers as blessing user; share tokens hashed at rest; boot guards for weak secrets; `trust_tier` on bundle tools.

---

## 3. UX / ops-layer alignment

| ID | Gap | Wave |
|----|-----|------|
| U1 | Home = Mission Control only | 2 |
| U2 | Sidebar/FAB frame “AI chat” as optional overlay | 2 |
| U3 | AgentSwitcher under-refined (keep; rename/refine as Harness Switcher) | 2 |
| U4 | Dead `/settings#get-started` | 2 |
| U5 | Empties + ⌘K ignore harness | 2 |
| U6 | Feed / MC ignore staged work | 2 |
| U7 | Suggestions ignore page context | 2 |
| U8 | Content Profile UI-first | 2 |
| U9 | Skills overlay opaque in chat | 2 |
| U10 | Eng-facing Agents settings copy | 2 |

---

## 4. Resident loop / facets / BYOA / trust

| ID | Gap | Wave |
|----|-----|------|
| R1–R7 | Routines, proactive, scratch, wake-ups, 3 tool gaps, org-facing, walkers | 3 |
| F1–F5 | Facet/scope dual-write, inert A2A fields, edge migration, naming | 4 |
| B1–B4 | PAT UX, MCP scopes, mcp_stub, dual uplink | 5 |
| T1–T3 | ADR-002 Phase 1, policy_gate audit, unstaged indicator | 6 |

---

## 5. Coherence / docs drift

| ID | Issue | Wave |
|----|-------|------|
| D1–D7 | `AGENTIVE_ENABLED` lie, ARCHITECTURE/PRD/steering stale, manifest DRAFT, ops-layer docs | 7 |

---

## 6. Wave checklist

- [x] Wave 0 — this document
- [x] Wave 1 — Security (service allowlist, rate limits, autonomy blocks, share caps, crypto opt-in, invitation preview)
- [x] Wave 2 — UX / Harness Switcher (ops-layer framing, Get started, empties, ⌘K)
- [x] Wave 3 — Resident loop (proactive delivery, event_wake, dated tool gaps, WhatsApp marker)
- [x] Wave 4 — Facet helpers + dual-write on register
- [x] Wave 5 — MCP scopes, mcp_stub quarantine, Connected Agents already shipped
- [x] Wave 6 — ADR-002 Phase 1 (custom+untrusted reject; skill allowlist)
- [x] Wave 7 — Docs coherence pass: ARCHITECTURE §10.6 + BYOA, PRD, `.kiro` steering, backend README, AGENTS.md, RESIDENT_HARNESS + ADR-003; D3 = always-on (no live `AGENTIVE_ENABLED` gate)
- [x] Wave 8 — targeted suites green (rate limit, MCP, shares, staging); `tsc --noEmit` green. `make verify-ci` hit 2 pre-existing DNS flakes in `test_link_preview_ssrf.py` (example.com resolve) — unrelated to harness sweep. Invariants preserved: I-AUTH, I-RET, I-SCRATCH, I-HOOK, I-GRAPH; no A2A revival.

### Invariants to preserve (substrate-touching waves)

`I-AUTH-02`, `I-RET-01..05`, `I-SCRATCH-01..05`, `I-HOOK-01`, `I-GRAPH-01/02`. No A2A revival.

---

## 7. D3 decision (AGENTIVE_ENABLED)

**Locked 2026-09-08:** Always-on. `AGENTIVE_ENABLED` is **not** a live boot gate
(`app/config.py` has no such field; `main.py` registers agentive unconditionally).
Docs must not describe conditional load as current behavior. Restoring a
substrate-only kill-switch requires an explicit plan.

---

## 8. Residual backlog (not claimed done by wave checkboxes)

### UX still open
- **U1** — Home remains Mission Control; not harness-first
- **U6** — Mission Control still ignores pending staging / harness inbox
- **U8** — Content Profile pages still UI-first (weak CTA into dock propose-revision)
- **U9** — Chat lacks “skills loaded in this workspace” affordance

### Resident / facets / trust still open
- **R1** — Routine grant/pause UX polish; app `default_schedules` dispatch beyond status labels
- **R5** — Three `status: gap` tools (dated 2026-09-08)
- **R6** — Org-facing WhatsApp not productized (code marker only)
- **R7** — No Walkers under `agentive/` yet
- **F1** — Facet dual-write only; full collapse unfinished
- **T2/T3** — Deeper policy_gate audit; unstaged-write UI indicator
- **ADR-002** phases beyond Phase 1 (marketplace / custom-code skills)

### Security nits
- **S10/S11** — CSP tighten / HSTS-CORS prod checklist

---

## 9. Deferred markers

Items marked “not productized” in-code (e.g. WhatsApp org-facing) must carry an
explicit UI/doc marker rather than silent stubs. ADR-002 Phase 1 runtime landed
(custom+untrusted rejected; optional tools_required allowlist); marketplace
custom-code skills still blocked pending later ADR-002 phases.
