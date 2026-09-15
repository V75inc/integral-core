# Integral × JV Harness — Deep Review, Verification & Fixes

**Date:** 2026-09-09
**Branch reviewed:** `dev` @ `d10e5742` (after the Cursor Full-Sweep `342ad895`, the
`c43d5c57` authz/staging/chat hardening, the Prompt-Sheet work, and the PR-101
MCP-as-connector merge).
**Method:** six independent code-reading audits (harness integration, write-safety/staging,
auth/permissions/MCP, proactivity/memory/skills, substrate/boot, frontend) re-run against the
live tree, a dedicated review of the new MCP-connector subsystem, and live smoke tests against
the running instance (backend :4000, frontend :9006, pgvector :5433).

---

## 1. Headline

The substrate remains strong and the last two hardening passes closed most of the earlier
findings. Verification of the 50 previously-reported items: **41 CLOSED, 5 PARTIAL, 4 OPEN**
(the OPEN/PARTIAL set is listed in §4). Both gates are green: `make verify-ci` passes and the
frontend suite is 970/970 with clean `tsc`.

The new **MCP-as-connector** subsystem is the highest-risk addition and shipped with a
**critical, incident-grade remote-code-execution hole**, now fixed and regression-tested (§2).
A second confirmed **cross-tenant data leak** (private/App-scoped workspace skills readable by
non-members) is also fixed (§3). The remaining gaps are catalogued and ranked in §4 for a
decision on scope.

---

## 2. FIXED — Critical RCE in the MCP connector spawn path (S-MCP-RCE)

**What:** Any authenticated user — no admin role, no workspace scope — could run an arbitrary
process on the API host with three ordinary API calls:

1. `POST /api/agentive/connectors` `{"kind":"mcp","auth_state":{"transport":"stdio","command":"/bin/sh","args":["-c","<anything>"]}}` — generic create has no workspace/role gate.
2. `PATCH /api/agentive/connectors/{id}` `{"subclass_slug":"mcp"}`.
3. `GET /api/agentive/connectors/{id}/health` → `probe_mcp_health` → `open_mcp_session` → subprocess spawn.

The `ba28c1d2` mitigation only gated the `/mcp/mount` *front door*; the generic
create → PATCH → health/refresh path re-reads `command`/`args` straight from the caller-controlled
`auth_state` and never consulted the gate. **Confirmed live** on the running instance with a
benign `touch <marker>` payload — the file appeared on the host.

**Fix** (`backend/app/agentive/connectors/mcp_client.py`): move the trust boundary to the point of
spawn. For `transport == "stdio"`, command/args are now **re-derived server-side from the vetted
connector catalog** keyed by `auth_state["catalog_slug"]`; a forged `catalog_slug` therefore
launches only that catalog entry's curated command, and a connector with no vetted slug is refused
(free-form commands remain allowed only under `TESTING=1` for the local echo-server fixtures).
The spawn env also drops interpreter code-loading vars (`NODE_OPTIONS`, `LD_PRELOAD`,
`PYTHONSTARTUP`, `DYLD_*`, …) so a forged connector can't smuggle code through the environment.
**Re-verified live:** the same three-call exploit now returns `status:"error"`,
`"stdio MCP command is not permitted…"`, and spawns nothing.

**Tests:** `backend/tests/test_mcp_stdio_command_gate.py` (freeform refused, forged command
discarded in favor of catalog, unknown slug refused, TESTING escape hatch, env-key strip list).

---

## 3. FIXED — Cross-tenant leak of private / App-scoped workspace skills (S-SKILL-LEAK)

**What:** A `private=True`, `origin="workspace"`, App-scoped Skill (e.g. an HR "salary bands"
SOP) was returned to a workspace member — even a non-member outsider — through all three skill
surfaces: the resident's overlay (`compose_workspace_agent_profile`), the editor list
(`list_workspace_skills`), and the detail endpoint (`get_skill_detail`). The detail gate was also
dead: `if not await can_access_workspace(...)` never fired because `can_access_workspace` returns
the **string `"none"`** (truthy) for no-access.

**Fix:**
- `workspace_agent_profile.py` — the workspace-skill overlay loop now skips App-scoped skills the
  caller can't access and excludes `private` App-scoped skills from the general overlay (they only
  belong in their own App's focused context).
- `agent_skills.py` — `list_workspace_skills` gates App-scoped workspace skills on the caller's
  accessible Apps; `get_skill_detail` uses the boolean `user_in_workspace_member_pool` (not the
  truthy `"none"` string) and additionally requires access to the skill's owning App.

**Tests:** new overlay-exclusion regression in `test_workspace_agent_profile.py`; existing
skill/registry suites still green.

---

## 4. Remaining gaps (ranked; NOT yet fixed)

### High — multi-tenant isolation / new MCP surface
- **Harness #19 — concurrent skill-overlay cross-serve.** The per-turn `clear_skill_discovery_cache`
  stopgap only holds for strictly serialized turns; two concurrent turns from different workspaces
  still cross-serve overlays because both jvagent caches are process-global keyed on the single
  resident id. Fix: host-supplied cache token upstream, or serialize the discovery window per
  process, or disable the caches (a clear that races looks fixed but isn't).
- **MCP F-2 — SSRF via un-revalidated redirects.** HTTP MCP mounts and the registry client follow
  redirects (`follow_redirects=True`) with no per-hop revalidation and no DNS pinning; reachable to
  `169.254.169.254` etc. `link_preview.py` already has the correct pattern to copy.
- **MCP F-3 — SSRF + credential exfil in OAuth discovery.** PRM/AS-metadata and token/registration
  endpoints are taken from remote-supplied URLs and fetched/POSTed to without validation; a
  malicious server can steal the OAuth code + PKCE verifier.
- **MCP F-5 — `client_secret` in plaintext on reads.** `mcp_safe_auth_state` is a 2-key denylist
  weaker than the generic redactor; the QuickBooks-MCP install writes `client_secret` to
  `auth_state` top-level and it is echoed on create/GET/LIST until the OAuth callback rebuilds state.
- **MCP F-6 — resident can never invoke a mounted MCP tool.** `actor_kind="agent"` with the human's
  principal id makes the policy gate evaluate a nonexistent agent subject → `policy_denied` on every
  `mcp__*` call. The feature is dead until the subject is corrected.

### Medium
- **MCP F-9 / F-11 / F-1 layer-3** — `/health`, `/mcp/refresh`, `/delete` re-check only the owner
  scalar, never current workspace membership; mounted MCP tool invokes bypass the staging/bless path
  (outbound-capable tools ship enabled by default in the catalog).
- **MCP F-7 / #17** — connector OAuth tokens + QuickBooks `.env` token file are plaintext at rest and
  the token file is never deleted on unmount; `credential_crypto` exists but is unused on this path.
- **MCP F-12** — `Workspace —HAS_CONNECTOR→ Connector` edge is never wired (I-GRAPH-01); a failed
  `OWNS` wire logs a warning and returns a detached node.
- **Substrate #31** — `change_event` reclaim is filtered but still unbounded on the first/backlog
  pass (whole matching set hydrated + saved serially).
- **Proactivity #32 / #39** — routine write-scope snapshot fails *open* on exception; the workspace
  profile cache hint ignores permission changes for the 60s TTL.
- **Staging #9 / #10** — batch/bulk executors replay from op 0 after partial failure; ~13 staging
  kinds are missing from `_validate_kind_scope` defense-in-depth (and `remove_entry_tag` has the
  same decision-key collision that item #1 fixed for creates).
- **Two purge paths** (`workspace_lifecycle.delete_workspace_cascade`, `apps.py` non-library delete)
  skip bundle hook/tool unregister → stale in-process registry until restart.

### Low
- **#16** — `mcp_oauth.py` still carries the `or "integral-dev-secret"` state-signing fallback the
  gmail/quickbooks fix removed (unreachable today, but the same anti-pattern).
- **MCP architecture** — `api/connectors.py` is ~2330 lines of four subsystems; `mcp_mount.py` and
  `mcp_adapter.py` are two parallel implementations with divergent tool-spec shapes, health
  vocabularies and timeouts, and HTTP-mounted tools may not route to the proxy at all; several
  `mcp_adapter` functions are dead. Worth a consolidation pass.
- **SM1** — `POST /api/auth/register` (jvspatial built-in, auth-exempt) creates an AuthUser with no
  Integral `User` node and no personal workspace; only `/auth/signup` provisions. Half-provisioned
  orphan.
- **SM2** — `/auth/signup` response echoes `preferences.email_verification.hash` (unsalted SHA-256
  of a 6-digit code == reversible OTP). Strip from serialization.
- **SM3** — a bogus/inaccessible `ws:<id>` scope silently falls back to the caller's personal
  workspace instead of 403 (not a leak — own data — but violates the documented "refuses" contract).

---

## 5. Smoke tests (live instance, all PASS)
- Unauth surface: `/workspaces`, `/tracks`, `/agentive/status`, `/mcp` all 401; MCP fail-closed.
- Signup → personal workspace auto-provisioned → create Track → create Entry → list.
- `/auth/me` works (there is no `GET /users/me` route by design).
- Cross-user IDOR: a second user reading the first user's track → 403 under both own and guessed scope.
- Harness turn: SSE pipeline healthy end-to-end (interact-context → text-delta → final-content,
  orchestrator ran, identity directives enforced); model backend degraded gracefully with no BYOK key.
- MCP: initialize + `tools/list` (97 tools) + a read tool call returning real substrate data; unauth 401.

---

## 6. Changes in this pass
- `backend/app/agentive/connectors/mcp_client.py` — catalog-vetted stdio command + env strip (S-MCP-RCE).
- `backend/app/agentive/workspace_agent_profile.py` — overlay access gate for App-scoped workspace skills.
- `backend/app/agentive/services/agent_skills.py` — list/detail access gate + `can_access_workspace` truthiness fix.
- `backend/tests/test_mcp_stdio_command_gate.py` (new), `backend/tests/test_workspace_agent_profile.py` (regression added).

Nothing committed or pushed. `make verify-ci` green; frontend suite 970/970; touched backend
suites green.

---

## 7. Second pass — HIGH + MEDIUM batch (commit `a460e4a4`)

Everything ranked HIGH or MEDIUM in §4 was implemented on branch
`fix/harness-high-med-hardening`, with regression tests. Highlights beyond §2/§3:

- **F-6 — the MCP feature was dead.** The caller gate evaluated
  `Subject(kind="agent", id=<human user id>)`; non-human subjects take the
  graph-local Policy branch, found no `HAS_POLICY` on a nonexistent agent, and
  fail-closed — so *every* resident-initiated `mcp__*` call returned
  `policy_denied`. `actor_kind` is audit provenance, not a policy subject.
- **F-2 / F-3 SSRF.** Per-hop redirect revalidation + DNS pinning + port
  allowlist for HTTP mounts; OAuth discovery URLs validated and origin-bound
  (they were attacker-supplied and received the auth code, PKCE verifier and
  client secret).
- **F-5 / F-7.** `auth_state` redaction is now an allowlist and recurses
  (`oauth.tokens.access_token` was previously echoed); secrets encrypted at
  rest; the stdio token file is purged on unmount.
- **#19.** Clearing jvagent's process-global caches per turn loses the race
  between overlapping turns; they are now replaced with caches that cannot
  retain an entry at all.
- **Mediums.** Bounded snapshot reclaim; bundle unregister on the two purge
  paths that skipped it; routine reconciliation fails closed and will not
  auto-bless cards it did not mint; profile cache keyed on caller access;
  batch/bulk executors resume instead of replaying; ~13 staging kinds added to
  a now-declarative scope table.

### Latent bugs found while fixing
- `delete_workspace_cascade` filtered `node=["App"]`, but jvspatial matches
  `__entity_name__` (`"WorkspaceApp"`) — **workspace deletion silently orphaned
  every App and its tracks.**
- `create_connector` looked up `User.get(<AuthUser id>)`, so **no Connector has
  ever had an `OWNS` edge** (masked by a scalar fallback).

### Verification
Full backend suite failures **byte-identical to the pristine `dev` baseline**
(9, all pre-existing — see below); `make verify-ci` green; all seven substrate
guards pass; frontend `tsc` clean, 970/970. The RCE and secret-redaction fixes
were re-verified against the running instance after the changes.

### Pre-existing failures on `dev` — RESOLVED in commit `0c1ed161`
The backend suite now runs clean (0 failures, from 9). One was a real defect —
the Prompt Sheet's three mutations emitted no ChangeEvent, so they left no
audit trail; emission now happens at the write in `services/prompt_queue.py`,
with three new `ChangeEventAction` literals and their `PolicyAction` mirrors.
The other seven were stale tests asserting behavior that deliberate security
changes had already replaced (admin-only `POST /notifications`, ADR-002 Phase 1
trust tiers, owner-bound bundle schedules, opt-in credential-key derivation,
and a facade test asserting an import no bundle tool has). Two of those rules
had no test at all until now.

### Original list (for the record)
`test_change_event_no_bypass` (three `agentive/api/prompt_queue.py` handlers
skip `emit_change_event` — a real audit gap on the new Prompt Sheet surface),
`test_crud_notifications` (3), `test_app_bundled_skills` (2),
`test_app_bundled_agents`, `test_credential_crypto_reason`,
`test_observe_entry_hook`.

### Vacuous guard — FIXED in commit `0c1ed161`
`bundle_facade_check.sh` shelled out to `rg` with `2>/dev/null || true`, and
the pre-commit hook's PATH has no ripgrep on this machine — so the I-HOOK-01
guard printed "clean" while checking nothing. It now uses `grep`, treats a
missing tool as fatal, and reports the file count (`clean (12 bundle tool
file(s) checked)`). Verified it fails on a planted violation; the old one did
not.

### Still open (LOW, deferred)
`mcp_oauth` architecture consolidation (`api/connectors.py` is ~2330 lines of
four subsystems; `mcp_mount.py` / `mcp_adapter.py` are parallel implementations
with divergent tool-spec shapes and health vocabularies, and several
`mcp_adapter` functions are dead), plus SM1 (`/auth/register` half-provisions),
SM2 (signup echoes the reversible OTP hash), SM3 (bogus `ws:` scope falls back
to personal instead of 403).
