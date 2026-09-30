# ADR 013 — Local computer use (Cua Driver) as a second desktop-environment kind

**Status:** Amended for Phase 2 implementation (bounded background actions)
**Date:** 2026-09-30
**Parent:** [ADR-010](010-connector-subsystem-architecture.md) — extends the Desktop
environment amendment. Placement: [ADR-004](004-app-composition-domain-apps-capability-bundles-scheduled-skills.md)
addendum (domain-agnostic routines become core tools). Single-worker posture:
[ADR-005](005-single-worker-until-shared-turn-state.md).

> **Implementation amendment (2026-09-30):** the raw MCP-over-WebSocket bridge
> described in the historical decision below is superseded by the primary-source
> integration review at
> [2026-09-cua-remote-desktop-integration-review.md](../../reviews/2026-09-cua-remote-desktop-integration-review.md).
> Electron main now terminates Cua locally through
> `CuaDriver.createPrivateWorker()` and exposes a narrow, versioned Integral RPC
> over the authenticated desktop-environment WebSocket. The remote backend never
> receives a Cua MCP transport. Screenshots use a separately bounded binary
> artifact channel, not base64 control frames. Local user consent originates
> authority; the backend grant is a conjunctive dispatch/audit mirror and cannot
> widen it. Phase 1 exposes only approved-app discovery, window listing, and
> exact-window snapshots under a locally generated bounded manifest with
> `desktop.display: false`. Phase 2 adds a locally approved action lease and
> background-only `driver__act` (`click`, `type_text`, `press_key`, `hotkey`)
> that requires a fresh snapshot and element token. The host remembers the last
> locally approved apps and duration on disk and may restore the lease until
> expiry or revoke. A short `steps[]` burst may
> share that snapshot and is verified once after the last step (AX tree by
> default; screenshot only when requested or the tree is empty). An unknown
> outcome is never retried. Foreground escalation remains
> disabled. The remaining text is retained as design history
> where the amendment explicitly changes it.

**Amendment (2026-09-30, S1 spike):** §1 and §7 are corrected. The first draft
had the backend spawning the stdio client directly, which is impossible when the
backend is remote from the desktop — a cloud process cannot spawn a subprocess on
a user's laptop. The host now owns the child and **bridges the stdio transport**
over the existing authenticated WebSocket; the backend remains a real MCP client
whose transport happens to be framed differently. The long-lived-session
requirement is unaffected, because token validity is enforced driver-side against
a long-lived transport, not by whoever owns the spawn. This has a second-order
consequence for §7: `get_window_state` returns base64 PNGs, so the existing 1 MiB
host-message cap is now a hard blocker on reusing the current request/response
framing for driver traffic. See the amended §1 and the WP-00 S3 spike.

**S1 outcome (recorded):** the packaging question is answered and cheaper than
assumed. On macOS, a driver on `PATH` runs `cua-driver mcp`, which proxies to
the installed `CuaDriver.app` daemon, so TCC grants stay attached to the app
bundle identity. **Bundling is not required for the first release** — no nested
signing, no notarization, no `extraResources` dependency. The bundling path is
still wired (`package.json` `extraResources` → `<resourcesPath>/driver/`, outside
the asar) for when Integral must not require a separate install, but it ships
empty and nothing downloads it at build time. Shipped: `desktop/src/driver-resource.js`,
`desktop/src/driver-host.js`, and 20 tests.

**S1b outcome (recorded, same date):** distribution is decided per platform, and
the installer is stricter than the vendor's own.

- **Windows / Linux auto-install.** Consent-gated, into the app's own
  `userData/driver`. No `sudo`, no admin elevation, no `PATH` mutation.
- **macOS hands off** to the vendor's install page. A driver unpacked into our
  storage cannot carry Cua's `com.trycua.driver` signature identity, which is
  precisely what keeps the Accessibility and Screen Recording grants alive
  across upgrades — so an auto-installed macOS driver would quietly degrade
  into a *bundled* driver with worse signature stability, the exact cost the
  separate app bundle exists to avoid.

**Integrity finding.** The vendor installer is a two-stage `curl | bash`
(`install.sh` fetches `_install-rust.sh` and pipes it to a shell), it edits the
user's shell rc, and — searching `_install-rust.sh` for
`sha256|shasum|checksum|integrity` — it performs **no integrity verification at
all**. It downloads a ~34 MB executable and execs it. The releases *do* publish
a `checksums.txt` in standard sha256sum format; it is simply never read.

So `desktop/src/driver-install.js` does its own fetch and verifies against the
published `checksums.txt`, deleting and refusing to unpack on mismatch. No shell
anywhere (`execFile`/`spawn`, fixed argv); 512 MB cap enforced on
`content-length` and while streaming; staging removed on success and failure; an
asset absent from `checksums.txt` is refused; idempotent by version stamp; a
tampered install is replaced. Verified end to end against release `0.30.4`: all
four platform assets present in `checksums.txt`, a 33.8 MB Linux x86_64 asset
downloaded, digest-verified, unpacked to a 56.7 MB mode-755 ELF, a second run
correctly a no-op, and a deliberately corrupted install repaired byte-identical.

**Release-pinning caveat.** As of `0.30.4` every `cua-driver-rs` release is
marked `prerelease: true` upstream, so a stable-only filter matches nothing and
unpinned auto-install would have been dead on arrival. Unpinned resolution
therefore falls back to the newest `cua-driver-rs-v*` release and reports
`fromPrerelease`, which the consent dialog discloses. Nightly tags are never
selected. **Pin `CUA_DRIVER_PINNED_VERSION` for releases.**

**Telemetry.** Cua Driver sends content-free product telemetry by default. The
consent dialog discloses it and points at `cua-driver telemetry disable`, which
persists across upgrades. Given Integral is a proserve product, defaulting that
off for auto-installed drivers is the likely follow-up.

**Scope:** `desktop/src/main.js`, `desktop/src/environment-host.js`,
`desktop/src/driver-install.js`, `desktop/src/driver-probe.js`,
`backend/app/agentive/services/desktop_environment.py`,
`backend/app/agentive/connectors/mcp_client.py`,
`backend/app/connectors/catalog/cua_driver.yaml`,
`backend/app/agentive/services/direct_tools.py`, `backend/app/models/edges.py`.

**Vendor:** [Cua Driver](https://cua.ai/cua-driver) (MIT). An open-source
computer-use driver for macOS / Windows / Linux that addresses a **window** rather
than the shared cursor: `element_token` actions route through the platform
accessibility API against a backgrounded window, so the agent does not move the
user's cursor, raise the target, or steal focus. Foreground escalation is an
explicit, per-action, narrow opt-in. 58 MCP tools over one stdio server; also a
CLI, a daemon, and a same-process SDK.

## Context

ADR-010 §1 routed "need local capabilities on the user's desktop?" to the
**Desktop environment host**, and that substrate shipped as six read-only
`desktop__*` tools: `desktop__list_roots`, `list_directory`, `read_file`,
`find_path`, `grep`, `diagnostics`. The architecture is deliberate and strong —
its docstring states the governing principle:

> persisted connector declarations alone never make a desktop callable

Live-connection-as-authority. Revocation is a socket close, not a policy
migration. Two-hop auth keeps the long-lived user JWT out of Electron: the
renderer mints a 45s single-use ticket, main consumes it over
`WS /ws/desktop-environment`, and every dispatch re-checks that the live
binding's `principal_id` **and** `workspace_id` match exactly.

That surface is **read-only, and correctly so.** The user did not ask for a
browser or a text editor to be readable; they asked for the resident to *use* the
machine. That is a different capability class: opposite polarity, different blast
radius, and a trust boundary the existing contract does not contemplate.

Three constraints from the existing code dominate the design.

1. **The vetted catalog is the spawn gate, and it is a post-mortem.**
   `mcp_client._resolve_trusted_stdio_command` re-derives `command`/`args`
   **server-side from the in-repo catalog** because trusting them from
   `auth_state` was remote code execution (S-MCP-RCE): any authenticated user
   could create a `kind="mcp"` `Connector` with arbitrary `auth_state` and reach
   the spawn path via `/health` or `/mcp/refresh`. `_build_spawn_env` is an
   **allowlist**, not a denylist, because the denylist was *also* live RCE — an
   attacker-supplied `auth_state.env` containing `PATH=/tmp/evil` ran their
   binary under a "vetted" `npx`. The gate is enforced **at the spawn point**,
   not at the mount endpoint, because the mount endpoint is bypassable via
   generic create → PATCH → `/health`. Any new spawn path inherits this shape.

2. **Element tokens do not survive a per-call session.** Cua's snapshot contract
   is explicit: `get_window_state` must be called once per turn per
   `(pid, window_id)` before any element action, and *the next snapshot of the
   same window replaces this one and stales its element tokens*. The token cache
   is per-transport, per-session. But `open_mcp_session` is a context manager
   that tears the subprocess down on exit — *"caller must not retain the session
   after the context exits."* **A per-call session cannot carry element tokens**,
   and the token path is Cua's preferred addressing mode (it works on
   backgrounded, hidden, and off-Space windows; pixel coordinates do not).

3. **The permission-owning process must start the runtime.** Cua requires the
   app that holds the Accessibility / Screen Recording grant to start the driver
   host, because a gateway, terminal, `open`, or `NSWorkspace` "changes the TCC
   responsibility chain and cannot lend the app's grants to the child." The
   backend therefore **cannot** spawn the driver for a real user, no matter how
   it is wired.

Constraint 1 says the spawn must be catalog-gated. Constraint 2 says the session
must be long-lived. Constraint 3 says the *runtime* is not ours to start. All
three are simultaneously satisfiable only in one topology, which is what this ADR
locks.

Two secondary findings shaped the placement decision:

- `extension-contract-v1.md` is unambiguous: "The canonical tool schemas are
  server-owned; a desktop client cannot invent capabilities during discovery."
  A capability bundle declares capability in an App manifest, which is a direct
  contract violation. ADR-010's desktop amendment says the same from the other
  direction, and ADR-004's addendum already sets the precedent — a capability
  bundle that turns out domain-agnostic becomes an `integral_*` core tool
  (`document-render`). Computer use is that exact shape.
- `app-bundles-v1.md:990` (open question 4) defers Python sandboxing for
  first-party custom skills. A bundle tool would shell out to a local binary
  **unsandboxed, in the backend process**. In the topology below the backend
  never executes driver code — the driver is a separate native process, spoken
  to over stdio — so that gap is not engaged at all.

`docs/product/BYOA.md` §11 records "**No bash execution server-side.**" That
constraint is preserved and not contradicted: compute stays on the client. Any
framing of this feature that implies server-side execution is out of bounds.

## Decision

### 1. The host owns the runtime and the child process; the backend speaks MCP over a bridged transport

The permission-owning process starts a private driver host **and** the stdio MCP
client that talks to it. The backend is a genuine MCP client, but the transport
underneath it is the existing authenticated WebSocket rather than a local pipe.

```
┌─ user's machine ──────────────────────┐  ┌─ Integral backend (cloud) ─────────┐
│ Integral Desktop — Electron main      │  │                                    │
│   ├─ owns TCC grants                  │  │  desktop_environment.py (existing)  │
│   ├─ embedded driver host .start()    │WS│    _connections / binding registry  │
│   │     ↓ private endpoint            │◄►│      │                               │
│   ├─ spawns `cua-driver mcp`          │  │      ├─ desktop__* (read, shipped)   │
│   │   (long-lived, generation-tracked)│  │      └─ driver__*  (read+execute)   │
│   └─ bridges stdio frames ────────────┼─►│  DriverSession (MCP client)         │
│                                        │  │  CuaService (curated contract)     │
│  element-token cache lives HERE,       │  │  GRANTS_DRIVER_ACCESS edge          │
│  which is why the child must not churn │  │                                    │
└────────────────────────────────────────┘  └────────────────────────────────────┘
```

**Why the host owns the spawn.** A cloud backend cannot spawn a subprocess on a
user's machine. Cua's own guidance is that the backend "launches that
connection" — but in their topology the "backend" is a Node process colocated
with the desktop app. Integral's is not, so the spawn has to live client-side.
The backend must not start a second driver host.

**Why this still satisfies the token-lifetime requirement.** Element-token
validity is enforced driver-side against a long-lived transport, not by whoever
owns the spawn. What matters is that the stdio process does not churn. The host
holds one child per generation and hands its pipes to the bridge, so tokens
survive exactly as they would under a local spawn. The backend still owns the
MCP session semantics — snapshot bookkeeping, which token came from which
`(pid, window_id, generation)`.

**Transport is additive, not a second channel.** A new `driver_binding` WebSocket
message announces `{generation, mcp frame channel}`; the existing
request/response `invoke`/`result` framing is untouched for `desktop__*`.

**Consequence — the 1 MiB host-message cap is now a blocker.** The existing WS
caps host messages at 1 MiB (`_MAX_HOST_MESSAGE_BYTES`) and closes with 4009 on
overflow. `get_window_state` returns a base64 PNG; a 1280×800 Retina screenshot
is several MB base64. The bridge therefore needs a chunked or size-exempt
framing for driver traffic, and that must be designed before the transport is
built rather than discovered during it. See WP-00 S3.

**Consequence — descriptor delivery is dropped.** The first draft had the host
ship `{command, args, environment}` to the backend for substitution into the
catalog. That is obsolete: the backend no longer resolves a command, so the
`{cua_socket}` catalog substitution in the original design **does not exist**.
What replaces it is simpler — the `generation` the host announces, which the
backend binds capability to exactly as it already binds a desktop binding. The
catalog gate in `mcp_client.py` is untouched and still governs every *other*
stdio mount.

### 2. A sibling of `desktop__*`, not a central-manifest tool

The driver surface reuses ADR-010's desktop spine: a `Connector` with
`subclass_slug = "integral_desktop_driver"`, the same live-binding authority
gate, and the same Capability Broker / `RunStep` receipt path. It is a **second
contract family hosted by the same connection registry**, not a new mechanism.

**Consequences:** no `tool_manifest.yaml` edit, no `TOOL_BINDINGS` edit, no
`test_tool_manifest_reconciliation.py` churn, and the `capability.ambiguous_declaration`
path does not apply. The driver's contribution to the capability snapshot arrives
through the `environments[]` branch that `desktop__*` already uses, so
`capability.revoked` fires when the host disconnects — free, and correct.

### 3. Not the MCP auto-mount path, and no new spawn gate

`register_mcp_tools` would register all **58** remote tools into the workspace
registry and would drag in per-call ADR-010 §6 staging. Both are wrong here: 58
tools cannot enter a resident's context, and per-call staging is the wrong shape
under a lease.

Per the §1 amendment, the driver surface also does **not** need a new spawn gate
in `mcp_client.py`, because the backend does not spawn. The S-MCP-RCE hardening
stands unchanged for every *other* stdio mount; this surface simply does not
travel that path. What it must honour instead is §5's two-layer authorization and
`I-DRIVER-02`, restated for the amended topology: the driver executable is
resolved **client-side** from the vetted resolution order in
`driver-resource.js`, and no part of it is ever derived from a value the client
supplies over the wire.

### 4. Curated contract — eight tools from fifty-eight

`DESKTOP_TOOL_SPECS` is the precedent: server-owned, authored `input_schema`,
`additionalProperties: false`, re-validated at the trusted dispatch seam. The
host cannot invent a tool.

| Tool | op_class | Grant? | Maps to |
|------|----------|--------|---------|
| `driver__list_apps` | read | no | `list_apps` |
| `driver__list_windows` | read | no | `list_windows` |
| `driver__snapshot_window` | read | no | `get_window_state` (AX tree + screenshot) |
| `driver__grant_state` | read | no | active lease + manifest |
| `driver__act` | execute | **yes** | `click` / `type_text` / `press_key` / `hotkey` / `set_value` / `scroll` / `drag` / `double_click` / `right_click` — one verb, required `action` discriminator |
| `driver__escalate_foreground` | execute, **privileged** | **yes** | the same verbs at `delivery_mode: "foreground"` |
| `driver__launch_app` | execute | **yes** | `launch_app` |
| `driver__terminate_app` | execute | **yes** | `kill_app`, `terminate: driver_launched` only |

Two choices are load-bearing:

- **Collapsing nine input verbs into `driver__act`** is not tidiness. Cua's own
  policy is "escalate narrowly, one action at a time." A single verb with a
  required discriminator makes the escalation surface exactly one auditable call.
- **`driver__escalate_foreground` is separate and `privileged`.** It is the only
  action that interrupts the human at their desk. `privileged: true` already
  means workspace owner/admin-only (`workspace_tools.py`), so the semantic is
  free and exactly right.

Namespace: `driver__*` is fresh. jvagent reserves `action__` / `harness__` /
`skill__` / `mcp__`, and `desktop__` is taken. Extending `desktop__` is
**rejected** — hiding "read a file" and "move my mouse" under one prefix hides the
polarity split from the model, which is precisely what `mcp_tool_class.py`'s
default-deny classification exists to police.

**v1 scope excludes browser automation** (`browser_prepare`, `page`, CDP). Not
deferral for its own sake: those tools obtain DOM-level access rather than
pixels-plus-AX, and the origin-scope interaction is subtle enough to deserve its
own ADR. Native applications cover the majority of "look at my screen and do this
thing" and keep the surface coherent.

### 5. Two-layer authorization, composed

**Observation needs no grant.** Reading your own screen is not more privileged
than the user already is, and Cua's permission table allows "observe windows,
applications, and the desktop" unconditionally in every mode. The read/execute
split is already in the op_class taxonomy, so it costs nothing conceptually.

An **action** requires a human-approved, scoped, time-boxed grant. The resident
may read the grant; it may never write one — the same posture as ADR-008's
`bundle_slug` stamping, and the same posture Cua enforces natively ("the host
owns the permission profile, capability manifest, launch grants, and any human
consent UX; agent tools cannot change them").

The two layers compose:

| | Outer (Integral) | Inner (Cua) |
|---|---|---|
| Gate | live binding **∧** scoped grant | `bounded` runtime: `allow.tools` + `resources` |
| Out of scope | refuse to dispatch | deny the action |

An action inside both layers runs. An action outside either fails twice. Cua's
`bounded` mode requires a deny-by-default capability manifest with declared
lifetimes (`expires_after`, `idle_timeout`) and refuses to start when an
origin-scoped manifest also allows a generic-input tool — the **observation**
leak, where a window screenshot of a browser exposes whichever tab is open
regardless of the origin list. That check is the reason the two layers are worth
composing rather than one being assumed sufficient.

### 6. The grant is associative edge state

A grant is relationship state between a principal and a connector, so per the
object-spatial contract it is a **typed field on a named edge**, not a Node and
not a `Policy` (`nodes.py:828` — *"LOCKED — DO NOT add/remove/rename fields"*).

```
User —GRANTS_DRIVER_ACCESS→ Connector
  manifest:   <Cua capability manifest v3 JSON>
  issued_at / expires_at / approved_by / revoked_at
```

Approval reuses the existing path end to end: a `driver_grant` staging kind whose
executor writes the edge after bless. `SESSION_AUTONOMY_BLOCKED_KINDS`
(`staging.py:65`) supplies the deny-list. `Approval.expires_at` /
`APPROVAL_TTL_DAYS` (`approval_ttl.py:54`) supplies the TTL precedent.

**The grant is durable graph state, not a process-local dict.** `_autonomy`
(`staging.py:230`) is the explicit anti-pattern: it drops on restart and diverges
per worker. The edge survives both, so `DriverSessionManager` teardown on restart
is cheap and the grant is still there.

**The live binding is an additional conjunctive requirement.** The grant never
outlives the socket regardless of its `expires_after`, because the live binding is
already a gate the desktop spine provides for free. This resolves the
lease-vs-session question: it is a lease, and the session is its upper bound.

### 7. Gate placement, and the fingerprint hazard

The lease check lives in the dispatch target, **not** in
`_dispatch_execute_guard` — that function receives neither `principal_id` nor
`scope` and structurally cannot evaluate a per-principal grant. Put the check
where identity and scope are both in hand.

Driver calls route through the existing short-lived-run mint
(`mint_surface_run`, `origin ∈ SHORT_LIVED_ORIGINS`) rather than the long-lived
chat run. **This is required, not an optimisation.** The capability snapshot
fingerprint hashes `environments[].health_status` and `tool_keys`
(`execution_runs.py:190`), and `ERR_UPGRADED` fires for `propose`/`execute` on
drift with **no reauthorization endpoint**. A driver runtime that flaps health or
rotates generations would otherwise invalidate every action in a long chat run.
Short-lived runs re-snapshot per call, so divergence is only observed *within* a
call.

Generation staleness (a host restart voids all outstanding element tokens) is
reported as a typed, actionable `driver.generation_stale` telling the model to
re-snapshot — more useful than `ERR_UPGRADED`, because the correct response is
different. The generation itself is already known to the host; the backend is
told the current generation in `driver_binding` and compares.

### 8. Untrusted content

Screen, clipboard, and file content returned to the model are **untrusted
content**, marked `content_untrusted: true` on every screen-derived return — the
`integral_transcribe_audio` precedent. Screen content is a strictly worse
injection surface than an audio transcript, because an adversarial page can render
instructions directly. A PC-* privacy clause names clipboard / screen /
file-content as trust-boundary crossings, and the skill's forbidden-patterns
section says so in the model's own terms.

Persisting artifacts: trajectories are log-shaped and **append-mostly**, so they
are `Object`, not `Node` (I-GRAPH-02, the `ChangeEvent`/`DBLog` precedent — no
graph work). Screenshots are the valuable artifact and go through
`ToolContext.put_attachment`, which preserves I-GRAPH-01 explicitly. I-ACCESS-01
applies: there is no field-level visibility, so anything sensitive needs its own
anchored track with `EXCLUDED_FROM`. A screen dump of someone's mail is not
something every track reader should see.

## Consequences

- **Go/no-go spikes precede all implementation.**
  - **S1 — host + packaging. ✅ Resolved.** The packaging question is answered:
    the `PATH` route needs no bundling at all, because `cua-driver mcp` proxies
    to `CuaDriver.app` and keeps TCC attribution with the app bundle. The
    bundling path is wired but empty. Shipped: `driver-resource.js`,
    `driver-host.js`, 20 tests, `extraResources` config, Environment-menu
    controls, `before-quit` teardown. The *runtime* half of S1 is still open —
    see S4.
  - **S2 — token lifetime. Still blocking.** Does a long-lived transport
    preserve `element_token`s across separate round trips, and what exactly
    happens across a host restart? **If this fails, §1's transport design
    fails.** There is no obvious fallback: a per-call transport cannot carry
    tokens, and tokens are the only addressing mode that works on backgrounded,
    hidden, and off-Space windows.
  - **S3 — bridge framing. New, from the §1 amendment.** The 1 MiB host-message
    cap cannot carry a base64 screenshot. Design and measure the chunked or
    size-exempt framing for driver traffic before writing the bridge; a
    1280×800 Retina PNG is several MB base64, so this is a first-class design
    question, not an implementation detail. It also fixes whether driver frames
    get their own WebSocket path or share the existing one with a type prefix.
  - **S4 — permissions. New.** Confirm the TCC grants the PATH route actually
    requires, and that they land on `CuaDriver.app` rather than on Integral. If
    they land on Integral, the whole "no bundling" conclusion collapses and the
    bundled + nested-signing route becomes mandatory. Requires a real install
    and a human granting Accessibility and Screen Recording in System Settings —
    not automatable, which is why it is a spike and not a task.
- **Invariants preserved:** `I-CON-04` (grant materialized at grant time,
  fail-closed) · `I-GRAPH-01/02` (grant is edge state; trajectories are `Object`) ·
  `I-SUBSTRATE-01` / `I-EXT-01` (the driver is a `subclass_slug` — data, not a
  code conditional; no driver name in substrate scope) · `I-HOOK-01` (no new hook
  point) · `I-PC-01` (driver actions are not `create_entry`/`update_entry`, so
  they **never** bypass staging — the grant is a separate mechanism and
  deliberately **not** an unstaged-write exemption) · `I-APPROVAL-03` (new
  `PolicyAction` members strict-superset `ChangeEventAction`, with
  `services/audit.py` in lockstep) · `I-CRUD-01` · `I-CONV-01/02/03` ·
  `I-ACCESS-01` · `I-CHAT-01` · `I-SKILL-SCOPE-01` + `I-SKILL-01..04` ·
  `I-TEST-01/02`.
- **Invariants introduced** (full Scope / Rule / Rationale / Verification /
  Origin bodies land in [INVARIANTS.md](../../INVARIANTS.md) **in the same commit**
  as each constraint, per that file's "How to Update This File"):
  - `I-DRIVER-01` — driver authority is the *intersection* of a live binding and a
    scoped grant; neither alone suffices.
  - `I-DRIVER-02` — the driver executable is resolved **client-side** from a
    fixed precedence (`override` → `bundled` → `PATH`) and no part of it is ever
    derived from a value the client supplies over the wire. (Restated from the
    original catalog-substitution form per the §1 amendment; the backend
    substitution point no longer exists.)
  - `I-DRIVER-03` — a granted manifest is deny-by-default; out-of-manifest actions
    are refused by both layers independently.
  - `I-DRIVER-04` — screen / clipboard / file content returned to the model is
    untrusted content.
  - `I-DRIVER-05` — foreground escalation is a distinct, privileged,
    separately-audited capability.
  - `I-DRIVER-06` — the grant is durable graph state; no driver authority may live
    in a process-local store.
  - `I-DRIVER-07` — an auto-installed driver binary is verified against the
    release's published SHA-256 before it is unpacked or executed, and is
    deleted on mismatch. It is installed into app-owned storage, without
    elevation, and never mutates the user's shell environment. (Client-side;
    enforced by `driver-install.js` plus its tests. Note the *vendor* installer
    performs no such verification — this invariant exists because delegating
    to it would silently opt out.)

  **Client-side, therefore not agent-visible:** the four remaining risks below
  are desktop-host concerns and no tool reaches them.
  - No auto-install on macOS; that path requires the separately signed
    `CuaDriver.app`.
  - No driver download at build time; packs stay reproducible.
  - Install requires an explicit native consent dialog that names the resolved
    version, the install directory, and the telemetry default. The renderer
    cannot bypass it — the handler always prompts.
  - Auto-install is Windows/Linux only and is a no-op when an operator install
    is already present.
- **Implementation sequencing:** S1 ✅ → S2/S3/S4 (all blocking, and S2 is the
  one that can invalidate the design) → bridge framing + backend `DriverSession`
  → **observation surface (`read` only, shippable with zero new authority)** →
  action grant + curated execute tools → skill, docs, CI guards.
  The observation surface remains the first shippable increment: it needs no
  grant, no `PolicyAction` addition, and no grant edge, so the snapshot/token
  loop can be learned against real windows *before* the authorization model is
  designed against real failure modes.
- **Deployment:** single backend worker is the accepted posture
  ([ADR-005](005-single-worker-until-shared-turn-state.md)), so the in-process
  `_tickets` / `_connections` registries are a decided state, not a latent bug. The
  grant is graph state precisely so it is correct if that ever changes.
- **Documentation debt to close:** there is currently **no** `docs/` page for the
  desktop host. `desktop/README.md` is the only operational documentation for a
  subsystem that is about to take a second responsibility. Browser automation
  needs its own ADR.

## Alternatives considered

- **Capability bundle with `tools[]`** — rejected on three independent grounds.
  `extension-contract-v1.md` makes App-declared capability a contract violation
  ("canonical tool schemas are server-owned"); ADR-004's addendum routes genuinely
  domain-agnostic routines to core tools with `document-render` as precedent; and
  a bundle tool would shell out to a local binary **unsandboxed in the backend
  process**, engaging the deferred gap at `app-bundles-v1.md:990`. ADR-004 remains
  open pending review; this ADR does not pre-empt it and does not depend on it.
- **Backend spawns `cua-driver` directly** — rejected; violates the TCC
  responsibility chain (constraint 3). *Originally proposed in the first draft
  of §1 and withdrawn:* it additionally assumes the backend can spawn a
  subprocess on the user's machine, which a cloud backend cannot do at all. The
  host owns the child; the backend speaks MCP across a bridge.
- **Auto-mounted MCP connector** — rejected (§3). 58 tools in the resident's
  context, plus the wrong staging shape.
- **Relaying driver calls through the existing request/response `invoke` frame**
  (like `desktop__*` today) — rejected. `get_window_state` returns a
  multi-megabyte base64 PNG and the host-message cap is 1 MiB, so tool calls
  cannot share that framing. This is a *transport* rejection, distinct from the
  earlier reason: even if frames did fit, the host's 1000ms reconnect cadence
  would couple token validity to reconnection, so calls must not share the
  reconnect-prone request channel either.
- **Session-scoped grant instead of a scoped lease** — rejected. A session grant
  has no human decision point at which a scope is chosen, so it degenerates to
  either auto-granting everything or guessing a static allowlist — and Cua refuses
  at load time to combine an origin-scoped manifest with generic input, so a
  guessed scope is frequently unusable.
- **Stage every action** (reuse I-PC-01 / ADR-010 §6 as-is) — rejected on UX. It
  is the correct *default* and observation ships under it, but per-click blessing
  makes multi-step flows unusable. Note this is a real product trade, not a
  technical one; if the two-layer model in §5 proves too permissive in practice,
  this is the fallback.
- **Extending the `desktop__` namespace** — rejected (§4); hides the polarity
  split from the model.
- **Implementing a `device` / `execution_target` node** — rejected. `ResourceKind`
  has no such member, `ExecutionScope` (`app/contracts/runtime.py`) models only
  `principal_id` + `workspace_id`, and `contracts_boundary_check.sh` forbids
  `app/contracts/` from importing private modules. Reusing `connector` is correct
  because the driver *is* a connector.

### Adjacent findings — flagged, not in scope

Found while evaluating; each is a defect in a different surface and each needs its
own decision. **No fixes are folded into this work.**

1. **MCP call-time scope gap.** `_call_tool_impl` never reads OAuth scopes; only
   list-time filtering exists. A client holding only `integral:read` can *call* an
   execute tool by name — it simply is not advertised.
2. **`integral_mark_notification_read` is broken.** It returns `forbidden` on
   every real call: `_KIND_ID_PREFIX` (`policy_gate.py:56-62`) has no `notification`
   entry, so the gate fires and `policy_engine._evaluate_human_default` has no
   matching branch. Its own test monkeypatches the gate out to hide this.
3. **`_dispatch_execute_guard` is unreachable dead code with a stale docstring**
   claiming "0 `op_class == "execute"` tools in the manifest today". Two exist
   (`integral_invoke_app_operation`, `integral_mark_notification_read`), both with
   `direct_ref`. `write_scope_required` has no producer, no consumer, and no
   schema entry anywhere in the repo.
4. **`policy_evaluate`'s `subject` parameter is accepted and discarded**;
   `PolicyModule.evaluate` hardcodes `kind="human"`. The `Policy` / `HAS_POLICY`
   grant graph is therefore unreachable from `dispatch_tool` — which is *why* §6
   needs its own mechanism.
5. **`validate_manifest` has no production caller.** The policy-action vocabulary
   gate lives in tests, not at boot. The manifest's `policy_actions:` block is an
   unvalidated duplicate of `PolicyAction` and has already drifted (missing
   `prompt_queue.*`, `entry.archived`, `staging.rollback`, `routine_task.*`,
   `skill.*`, `model_credential.*`).
6. **`I-AUTH-02` is cited in 8 places across `docs/` and does not exist in
   `docs/INVARIANTS.md`.**

## Cross-references

- Theme B: [ROADMAP.md](../../product/ROADMAP.md) § Theme B — **needs a milestone
  item; nothing currently owns this**
- Parent architecture: [ADR-010](010-connector-subsystem-architecture.md)
- Capability-bundle placement precedent: [ADR-004](004-app-composition-domain-apps-capability-bundles-scheduled-skills.md)
- Single-worker posture: [ADR-005](005-single-worker-until-shared-turn-state.md)
- Facade stamping posture: [ADR-008](008-toolcontext-own-workspace-writes.md)
- Invariants: [INVARIANTS.md](../../INVARIANTS.md) — I-CON-01…05, I-GRAPH-01/02,
  I-SUBSTRATE-01, I-EXT-01, I-HOOK-01, I-PC-01, I-APPROVAL-03, I-ACCESS-01
- Core↔Apps contract: [extension-contract-v1.md](../../platform/extension-contract-v1.md)
- Connector authoring: [connectors.md](../connectors.md)
- BYOA compute boundary: [BYOA.md](../../product/BYOA.md) §11
- Resident spec: [RESIDENT_HARNESS.md](../../product/RESIDENT_HARNESS.md)
- Desktop host operations: [`desktop/README.md`](../../../desktop/README.md)
- Upstream: [cua.ai/cua-driver](https://cua.ai/cua-driver) ·
  [permission modes](https://cua.ai/docs/reference/cua-driver/permission-modes) ·
  [no-foreground contract](https://cua.ai/docs/concepts/the-no-foreground-contract)
