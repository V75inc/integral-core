# ADR 002 — Contributed-skill safeguard + BYOA skill delivery

**Status:** Phase 1 runtime landed (2026-09-08 Full Sweep) — `kind=custom` +
`trust_tier=untrusted` rejected at `register_skill`; optional
`skill_tools_required` allowlist on `dispatch_tool`. Remaining ADR decisions
(marketplace vetting, BYOA skill delivery) stay design-only.
**Date:** 2026-06 (Phase 1 runtime: 2026-09-08)

## Context

Today App-bundled skills are **first-party only**. The placement contract
([workspace-agent-profile.md](../workspace-agent-profile.md) § Skill placement
invariants) and `skill_registry.register_skill` accept bundle skills, but every
shipped bundle lives in-repo (`backend/app/packages/<slug>/`) and is implicitly
trusted. Two surfaces are blocked on a missing trust layer:

1. **Contributed / marketplace bundles.** The architect intent — "App bundles
   carry skill bundles", including third-party — cannot ship a marketplace
   until untrusted bundles can be installed without granting them the same
   reach a first-party bundle has. `check_tools_permitted` (`services/hooks/trust.py`)
   already gates `tools[]` behind `trust_tier ∈ {trusted, audited}`, but skills
   themselves (`kind: custom` Python `handler_ref`) have no equivalent tier gate,
   and a skill's declared `tools_required` is validated at *registration* against
   the live catalogue but is **not enforced as a dispatch allowlist** — a skill
   can call any tool the caller's RBAC permits, regardless of what it declared.
2. **BYOA delivery.** [BYOA.md](../../product/BYOA.md) descoped skill-bundle
   delivery to **external** agents: they integrate via Integral's MCP surface
   only and receive **tools, no SOPs**. First-party App skills (the SOP
   coordination layer) reach the resident cockpit but never an external MCP
   client.

Current enforcement points this ADR extends:

- `services/hooks/trust.py :: check_tools_permitted` — bundle `tools[]` trust gate.
- `services/hooks/registry.py :: ToolContext` — sole substrate-access facade for bundle tools (I-HOOK-01 / DR-30-01).
- `agentive/services/skill_registry.py :: register_skill` — validates `tools_required` against `build_tool_catalogue()`, validates `handler_ref` shape, enforces the resolver-time `private` gate; **Phase 1:** rejects `kind=custom` + `trust_tier=untrusted`.
- `agentive/tooling/dispatch.py :: dispatch_tool` → `policy_gate.enforce_tool_policy` — per-call RBAC; **Phase 1:** optional `skill_tools_required` allowlist via `tooling/skill_allowlist.py`.

This ADR is the design for Phase 3 of the skills-gap plan
(`fuzzy-hugging-avalanche.md`). Phase 1 runtime (decision 1 + optional
dispatch allowlist) landed 2026-09-08; remaining decisions stay design-only.

## Decision

### 1. Trust-tier gating for contributed skills

Extend the existing `trust_tier` gate (`check_tools_permitted`) from `tools[]`
to **skills**:

- **`untrusted` bundles → declarative skills only.** `kind: custom` (Python
  `handler_ref`) is rejected at `register_skill` for any bundle below
  `trusted`. An untrusted bundle ships **SOP prose + `tools_required`** and
  nothing executable — it coordinates existing first-party tools, it cannot
  introduce new code paths.
- **`trusted` / `audited` bundles may ship `kind: custom`**, but only via the
  same path bundle tools already take: a `trust_tier: trusted` bundle **tool**
  reached through `ToolContext`. A skill never carries an importable substrate
  reference; "custom handler" resolves to a registered bundle tool key, not an
  arbitrary dotted import. The existing `_HANDLER_REF_RE` shape check stays as
  defense-in-depth.
- **Static analysis / capability scoping** runs at vetting time (decision 5) on
  any custom handler a trusted contributed bundle ships: AST scan rejects
  `import app.services` / `import app.models`, dynamic import, `eval`/`exec`,
  filesystem/network/subprocess calls. A trusted handler reaches substrate
  *only* through `ToolContext` methods — the scan enforces that the facade is
  the single seam.

This makes trust monotonic: the cheapest install (declarative, untrusted) has
the smallest blast radius; executable code requires a tier that only the
vetting pipeline can grant.

### 2. Skill capability scoping — `tools_required` as a hard dispatch allowlist

`tools_required` becomes an **enforced allowlist at dispatch**, not just a
registration-time existence check:

- When a skill is the active coordination context, `dispatch_tool` consults the
  skill's `tools_required` set and **refuses any tool the skill did not
  declare** — a contributed skill cannot invoke a tool outside its manifest,
  even one the caller's RBAC would otherwise permit.
- Enforcement stacks **on top of** existing RBAC (`enforce_tool_policy`): a tool
  call must pass *both* the per-skill allowlist *and* the per-call policy gate.
  Allowlist narrows; it never widens. (Same monotonic-narrowing rule BYOA token
  scopes follow.)
- The allowlist is **per-skill policy scope** carried on the `Skill` node
  (`tools_required` already persists there). For contributed bundles the scope
  is frozen at vetting time — a skill's effective allowlist cannot exceed what
  the vetting gate approved.
- First-party `integral_*` base skills are exempt from the hard allowlist (they
  define the catalogue); the gate applies to bundle-contributed skills.

### 3. BYOA delivery of first-party App skills to external MCP agents

External MCP agents stay **read/propose tools only** by default (BYOA §6). The
deferred surface — delivering first-party App **SOPs** — is sketched here, not
built:

- **SOP-as-resource, not SOP-as-server-execution.** Integral never runs a
  contributed or first-party SOP on behalf of an external agent; the multi-tenant
  constraints in BYOA §11 (no server-side bash, no per-agent FS/process) hold.
  Instead, a first-party App skill's SOP body is exposed to the external client
  as **MCP content the client's own model reads** — via an MCP prompt/resource
  (`integral_get_skill_sop(app_slug, skill_key)`) or a published Anthropic skill
  bundle (BYOA §7) generated from the same `SKILL.md`.
- **First-party only.** Only `private: false`, first-party (in-repo) App skills
  are eligible for external delivery. Contributed skills are **never** pushed to
  external agents until the marketplace vetting gate (decision 5) and an
  external-delivery policy exist — a third-party SOP shipped to a user's Claude
  Code is a supply-chain surface, deliberately out of scope.
- **Same authz, same staging.** A SOP delivered externally coordinates the same
  MCP tools the external token already scopes; every mutation still stages for
  bless in the Integral inbox (BYOA §6). The SOP carries no new capability — it
  is instructions over tools the token already holds.
- **Org policy gate.** External skill delivery is governed by the workspace
  `byoa_policy` (BYOA §9) — an org can disable SOP delivery while still allowing
  MCP tool access.

### 4. Marketplace vetting gate

A contributed bundle passes a review pipeline **before its skills become
installable** from the marketplace:

1. **Manifest lint** — placement contract (no `integral_*` skill keys; no domain
   skill claiming a base slot), `tools_required` resolves to the live catalogue
   + the bundle's own `app.tools[]`, hook bindings target frozen hook points
   (I-HOOK-01).
2. **Static capability scan** (decision 1) — every `kind: custom` handler;
   reject substrate imports, dynamic import, eval/exec, I/O.
3. **Tier assignment** — pipeline assigns `trust_tier`. Declarative-only bundles
   may be `untrusted` and auto-approved; any executable handler requires
   `trusted`/`audited`, which only the pipeline can stamp (a bundle cannot
   self-declare a trusted tier).
4. **Capability-scope freeze** — the approved `tools_required` per skill is
   recorded as the dispatch allowlist (decision 2); post-install the bundle
   cannot widen it.
5. **Signing + provenance** — bundle is signed; install verifies signature and
   records provenance, mirroring the signed-plugin model
   (`operational_model_plugins`).

Until this pipeline exists, the install path remains **first-party in-repo
bundles only** — the standing rule in workspace-agent-profile.md.

## Phased build plan

This ADR is design; the following are future build phases (each its own GSD
phase), ordered:

- **Phase A — Skill trust-tier gate.** Extend `check_tools_permitted`'s tier
  logic to skills; reject `kind: custom` below `trusted` in `register_skill`.
  Pure gating, no new runtime surface. (Unblocks declarative contributed skills.)
- **Phase B — Dispatch allowlist.** Enforce per-skill `tools_required` at
  `dispatch_tool`, layered on `enforce_tool_policy`. Carry active-skill context
  into dispatch. (Enforces capability scoping; the highest-value safeguard.)
- **Phase C — Static capability scan.** AST scanner for `kind: custom` handlers;
  CI guard + vetting-pipeline step. Enforces `ToolContext`-only substrate reach.
- **Phase D — Marketplace vetting pipeline.** Manifest lint + scan + tier
  assignment + scope freeze + signing/provenance. Gate on the install path.
- **Phase E — BYOA SOP delivery.** `integral_get_skill_sop` MCP resource (or
  published bundle generation) for first-party public skills; `byoa_policy`
  toggle. First-party only.

Phases A and B are independent of the marketplace and deliverable first — they
harden the **existing** skill surface even before any contributed bundle exists.

## Consequences

- **Monotonic trust.** Smallest install (declarative/untrusted) = smallest blast
  radius; executable code requires a vetting-stamped tier. Mirrors the
  `tools[]` gate, so the model is already familiar in-codebase.
- **Hard capability scoping** closes the gap where a registered skill could
  invoke any RBAC-permitted tool. After Phase B, a skill is bounded by its
  declared `tools_required` — declared scope becomes enforced scope.
- **`ToolContext` stays the single substrate seam.** The static scan makes the
  facade an enforced invariant for contributed code, not a convention.
- **BYOA SOP delivery is opt-in and first-party only** — no third-party SOP
  reaches a user's external agent without the full vetting gate.
- **Cost:** dispatch gains a per-skill allowlist check (cheap set membership);
  the vetting pipeline is real operational surface (signing keys, review SLA).

## Risks

- **Active-skill context at dispatch.** Phase B needs the dispatcher to know
  which skill is driving the current tool call. If unavailable, fallback is to
  scope the allowlist at the turn/session level (looser but still bounded).
- **Static analysis is necessary, not sufficient.** AST scanning catches obvious
  escapes but not all (reflection, encoded strings). Trusted tier therefore also
  requires human review in the vetting pipeline — the scan gates, it does not
  certify.
- **SOP supply-chain (BYOA).** Even first-party SOPs delivered externally are
  instructions a remote model executes; the read/propose-default token scope and
  staged-bless safety net are the backstop, not the SOP text itself.

## Alternatives considered

- **Sandbox/exec contributed skills server-side** — rejected; violates BYOA §11
  multi-tenant constraints (no server-side bash/process per agent) and is a far
  larger attack surface than declarative-only + vetted-trusted.
- **Trust everything in-repo, gate nothing** (status quo) — rejected; cannot
  support a marketplace, and leaves the registration-only `tools_required` check
  unenforced at dispatch.
- **Per-skill RBAC roles** instead of a `tools_required` allowlist — rejected;
  access already flows App → workspace install → user App role
  (workspace-agent-profile.md § User access gate). The needed control is
  *capability* scoping (which tools), not another *identity* layer; allowlist is
  the minimal addition.
- **Deliver contributed skills to external agents** in the same phase as
  first-party — rejected; third-party SOP on a user's machine is a supply-chain
  surface that must wait for the vetting gate. Decoupled into Phase E (first-party)
  vs. a later, separately-designed contributed-external surface.
