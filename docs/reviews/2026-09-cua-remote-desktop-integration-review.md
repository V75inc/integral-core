# Cua remote desktop integration review

**Date:** 2026-09-30  
**Scope:** Integral Desktop (Electron on the user's machine), a potentially remote
Integral backend/resident agent, native-application automation only  
**Status:** Architecture recommendation; primary-source review of Cua Driver's
current documentation

**Implementation decision:** Integral accepted ownership of the macOS TCC
identity for the Phase 1 product path. The shipped architecture therefore uses
the bundled `CuaDriver.createPrivateWorker()` adapter on all three desktop
platforms; the standalone `CuaDriver.app` compatibility exception remains a
documented alternative, not the active implementation.

## Executive conclusion

**Recommendation:** do not bridge raw MCP between Electron and the cloud backend.
Make Electron a **local, narrow capability gateway**:

```text
remote resident / server-owned driver__* tools
        |
        | authenticated Integral WebSocket
        | typed, request-correlated application RPC + artifact channel
        v
Electron main (authorization, generation, audit correlation, redaction)
        |
        | local adapter
        v
Cua runtime (bounded manifest, native enforcement) -> desktop
```

The cleanest product-owned implementation is
`CuaDriver.createPrivateWorker()`: it keeps the typed SDK contract, gives
Electron a supervised child over inherited pipes, has no listener or reconnect
path, and accepts an immutable authorization ceiling. Use it on Windows/Linux,
and on macOS if Integral deliberately accepts ownership of the TCC identity. The
stated requirement to retain `CuaDriver.app`'s macOS TCC identity creates one
platform-specific exception: connect locally to that separately installed
service through a vendor-supported private endpoint, or terminate Cua MCP
**locally** and translate only the eight Integral operations. Do not tunnel
either local protocol to the backend.

This retains the strongest parts of ADR-013—live-connection authority, a durable
scoped/time-boxed grant mirror, Cua `bounded` mode, a deny-by-default manifest,
eight server-owned tools, and explicit foreground escalation—but makes local
user consent the originating authority and removes MCP from the Internet-facing
trust boundary. It is smoother because users see one Integral
connection and one coherent recovery path. It is safer because the cloud never
receives an opaque protocol tunnel to a 58-tool desktop server, screenshots do
not have to travel as base64 MCP frames, and authorization is enforced at both
the local gateway and Cua's native dispatch boundary.

The proposed raw bridge is technically possible in principle, but it is the
wrong abstraction. Cua documents a transport-free remote-carrier seam with
generation binding, deadlines, cancellation negotiation, request IDs, and a
separate trusted-session channel, while noting that no generated
Python/TypeScript remote constructor currently ships. Reimplementing those
properties by tunnelling stdio MCP over an application WebSocket creates a new,
security-sensitive transport without gaining a supported Cua remote contract
([SDK, MCP, and process hosting](https://cua.ai/docs/concepts/sdk-mcp-and-hosting)).

## What the Cua sources establish

The statements in this section are **facts from current first-party
documentation**, not Integral design recommendations.

1. **Ownership precedes transport.** Cua defines the runtime as the process that
   owns permissions, browser connections, recordings, and lifecycle state. Its
   starting points are MCP for an agent/harness, same-process SDK for an
   application with built-in computer use, and an app-hosted service when a
   signed desktop app must serve an external agent
   ([Choose an integration](https://cua.ai/docs/concepts/choose-a-cua-driver-integration)).

2. **The SDK is the canonical typed application contract; MCP is a downstream
   agent adapter.** Same-process, private-worker, and daemon topologies all reach
   the same native authorization boundary. A private worker has inherited pipes,
   no listener, and no reconnect path. Cua explicitly says applications may map
   SDK operations into a smaller product-owned tool
   ([SDK, MCP, and process hosting](https://cua.ai/docs/concepts/sdk-mcp-and-hosting);
   [Use the SDK in process](https://cua.ai/docs/how-to-guides/driver/use-sdk-in-process)).

3. **A signed macOS identity matters.** Standalone `cua-driver mcp` normally
   proxies to `CuaDriver.app`, preserving that app's Accessibility and Screen
   Recording identity. Conversely, when another signed app hosts a private
   service, the permission-owning app must start it directly; a gateway,
   terminal, `open`, or `NSWorkspace` cannot lend that app's TCC grants
   ([Connect your agent](https://cua.ai/docs/how-to-guides/driver/connect-your-agent);
   [Expose MCP from a desktop app](https://cua.ai/docs/how-to-guides/driver/expose-mcp-from-desktop-app)).

4. **Embedding changes who owns permission UX.** `CuaDriver.create()` loads the
   native runtime into the importing process rather than launching a daemon.
   The importing app owns OS permission UX. Direct macOS runtimes also lack the
   cursor-overlay facility unless the host supplies a suitable AppKit
   main-thread adapter
   ([Use the SDK in process](https://cua.ai/docs/how-to-guides/driver/use-sdk-in-process)).

5. **Cua's documented desktop-app MCP topology assumes a local backend.**
   `EmbeddedCuaDriverHost.start()` returns a private connection and MCP launch
   configuration; a separate Node backend is told to launch that exact local
   command/environment. After restart, clients must discard the old generation,
   endpoint, and proxies. Shutdown must stop work and MCP clients before the SDK
   and host. Cua warns never to replay an action whose outcome is unknown
   ([Expose MCP from a desktop app](https://cua.ai/docs/how-to-guides/driver/expose-mcp-from-desktop-app)).
   The page does not specify how a cloud backend should tunnel this local stdio
   connection.

6. **Transport lifetime is meaningful but public session names are not
   credentials.** Each MCP transport gets a private implicit lifecycle session;
   closing it releases session state. Repeated unnamed operations on one SDK
   transport reuse its implicit session. Public session labels carry no
   authority, and two transports remain isolated even if they use the same label
   ([Choose an integration](https://cua.ai/docs/concepts/choose-a-cua-driver-integration);
   [Use the SDK in process](https://cua.ai/docs/how-to-guides/driver/use-sdk-in-process)).

7. **Handles must be treated as short-lived capabilities.** Cua instructs callers
   not to reuse element tokens after a snapshot, window, or session change.
   Stale tokens require a new snapshot. An action result does not prove the
   intended UI effect; callers should obtain a fresh snapshot and verify a
   bounded postcondition, without replaying the action
   ([Use the SDK in process](https://cua.ai/docs/how-to-guides/driver/use-sdk-in-process)).

8. **`bounded` mode is useful but not a sandbox.** A v3 manifest explicitly
   allows tools and app/file/display resources, supports expiry and idle timeout,
   fails closed for unknown/missing resources, and is approved by a trusted
   launcher—not by a tool call. `desktop.display: false` prevents unfiltered
   display access. A manifest bounds only one runtime; other processes/runtimes
   under the user are unaffected
   ([Write a capability manifest](https://cua.ai/docs/how-to-guides/driver/write-a-bounded-manifest)).

9. **Cua authorization does not sanitize results.** Native authorization runs
   before every public dispatch, snapshots policy for the runtime generation,
   and cannot be widened by an adapter. It does not inspect tool responses,
   limit screenshot output, authenticate callers, or enforce rate/usage quotas
   ([How permission policies work](https://cua.ai/docs/concepts/how-permission-policies-work)).
   Therefore screenshot minimization, principal authentication, quotas, and
   output handling remain Integral responsibilities.

10. **Cua's network listener is not the preferred remote desktop boundary.**
    Its optional loopback HTTP MCP listener is disabled by default and, when
    enabled, requires a 32–4096-character bearer token. The documented app-hosted
    path instead produces a private local endpoint
    ([SDK, MCP, and process hosting](https://cua.ai/docs/concepts/sdk-mcp-and-hosting)).

## Alternatives compared

| Alternative | UX / install / permissions | Security and failure properties | Assessment |
|---|---|---|---|
| **Raw MCP stdio bridge over Integral WebSocket (current ADR)** | Preserves the current macOS `CuaDriver.app` path if Electron launches `cua-driver mcp`; requires separate Cua install on macOS. | Cloud MCP client receives a transparent path to a local protocol with a much larger catalog than Integral exposes. Integral must correctly proxy initialization, notifications, cancellation, ordering, backpressure, generations, stderr/exit, and multi-MB base64 image results. Reconnect semantics are ambiguous: a new WebSocket is not automatically the same Cua transport/session. | **Reject as the product boundary.** It couples Integral to two protocols and turns a private stdio channel into a remote capability tunnel. |
| **Electron in-process SDK** | Fewest local moving pieces and typed APIs. Electron/Integral, not `CuaDriver.app`, owns macOS TCC prompts and identity; users may need to regrant permissions when signing identity changes. Cursor overlay needs an AppKit adapter, though v1 does not need it. | No local listener; direct cancellation/lifecycle; Electron crash takes runtime with it. Arbitrary code in the host process can interfere with the runtime. | **Good only if Integral deliberately owns TCC.** It conflicts with the stated requirement to retain `CuaDriver.app` identity. |
| **Supervised private worker + curated Integral RPC** | Best product-owned shape: typed SDK, one Integral connection, no Cua installation or agent protocol exposed to the cloud. Cua documents `createPrivateWorker()` / `create_private_worker()` as inherited-pipe isolation with no listener or reconnect path. | Electron supplies the immutable authorization ceiling before readiness; the worker reports whether a broken/timed-out action was not started, completed, or has unknown completion. Its channel and runtime die together. The child remains in the spawning app's permission responsibility chain. | **Preferred default architecture.** Use on Windows/Linux and wherever Integral owns OS permission UX. On macOS it cannot simultaneously preserve standalone `CuaDriver.app`'s TCC identity; use the local daemon adapter there until consciously migrating TCC to Integral. |
| **Local sidecar/gateway exposing narrow Integral RPC** | One Integral connection and only eight product concepts. Can hide platform differences: vendor daemon on macOS, SDK/private worker elsewhere. | Electron authenticates the existing short-lived ticket/live binding, validates schemas and grants locally, applies rate/output policy, then invokes Cua. Remote side sees no Cua endpoint or 58-tool catalog. Clear generation and idempotency semantics can be product-owned. | **Recommended.** “Sidecar” means a private child or daemon adapter, not a new TCP service. Keep the gateway in Electron main and avoid a separately reachable listener. |
| **Remote MCP/HTTP endpoint on the laptop** | Could let the cloud MCP client connect directly, but requires NAT traversal/reverse tunnel, endpoint discovery, credential provisioning/rotation, and a second connection status for users. | Enlarges network exposure and token lifetime. Cua's documented HTTP listener is loopback/opt-in and bearer-token based; its remote carrier requires more semantics than a socket tunnel. | **Reject for v1.** The existing authenticated outbound WebSocket is safer and operationally simpler. |
| **Agent/MCP client local in Electron** | Avoids transporting screenshots to a cloud agent only if inference is also local; otherwise the local agent still sends model context remotely. Adds model credentials/runtime and competes with the singular resident architecture. | Can keep raw Cua protocol local and enforce actions close to the desktop, but splits planning/audit/state between local and cloud agents. | **Do not move the resident agent.** If MCP is needed internally, terminate it locally in an adapter; keep planning and canonical audit on the remote resident. |

## Recommended design

### 1. Keep the agent remote; make Electron the enforcement proxy

The backend continues to publish and invoke only the eight authored
`driver__*` schemas. It sends a typed envelope such as:

```text
driver_call {
  call_id, binding_generation, grant_id, deadline,
  operation, validated_arguments, expected_snapshot_id?
}
```

Electron main independently verifies the live principal/workspace binding,
binding generation, grant scope/expiry/revocation, operation allowlist, target
application, delivery mode, request size, and deadline before translating to
Cua. The renderer never receives a Cua endpoint, manifest-approval control, or
raw action channel.

This is **application RPC, not remote MCP**. The backend owns agent-facing tool
schemas; Electron owns the machine-facing enforcement and adapter. The primary
adapter should be a local Cua private worker using the typed SDK and an immutable
authorization ceiling. Cua's native manifest remains the final, independent
ceiling. The macOS standalone-daemon adapter is a deliberate exception made only
to preserve `CuaDriver.app`'s existing TCC identity.

### 2. Preserve `CuaDriver.app` identity on macOS

For the first spike, keep the separate signed Cua installation and its stable
TCC permission row. Prefer a released typed `CuaDriver.connect(socketPath)`
adapter to the vendor daemon if the installed version exposes the needed
endpoint **and** lets the trusted local host activate and rotate the exact
bounded manifest approved for the Integral grant. If typed connection is not
publicly stable, locally terminated `cua-driver mcp` can test the narrow RPC
adapter, but it is production-acceptable only when the daemon already runs with
that bounded ceiling.

Do **not** use `EmbeddedCuaDriverHost` merely to retain `CuaDriver.app` identity.
Cua's docs say an app-hosted private daemon inherits the permission-owning
desktop app's responsibility chain; that is appropriate when Integral wants its
own TCC identity, not when it wants the vendor app's identity.

> **Uncertainty:** the reviewed pages show `CuaDriver.connect(socketPath)` for an
> embedded private host and state that macOS CLI MCP proxies to
> `CuaDriver.app`. They do not establish either a stable, discoverable public
> socket contract for an arbitrary Electron SDK client or a supported mechanism
> for Electron to rotate the standalone app daemon through per-grant bounded
> manifests. Confirm both against the pinned release/API. If either is absent,
> use `createPrivateWorker()` or `EmbeddedCuaDriverHost` and deliberately make
> Integral the macOS TCC owner rather than falling back to a standard-mode shared
> daemon.

### 3. Bind authorization locally and natively

**Local user consent is the source of desktop authority.** At activation,
Electron shows the exact app/tool/time/data-egress scope to the user, records the
approval locally, constructs a fresh v3 Cua manifest from that approved scope,
starts a new `bounded` runtime generation, and approves that exact manifest. The
backend graph grant is an auditable, durable mirror used in the conjunctive
server dispatch check; it is not sufficient to create or widen local authority.
If local and backend records differ, use their intersection and fail closed.
Never accept manifest YAML, executable paths, environment variables, Cua tool
names, or MCP launch descriptors directly from the backend.

The safe mapping is:

- local consent: originating desktop authority and exact disclosure scope;
- server grant: durable mirror of intent, principal/workspace/connector scope,
  dispatch gate, and audit;
- live WebSocket binding: current authority and revocation by disconnect;
- Electron policy: authenticated caller, quotas, schema/resource/output checks;
- Cua manifest/policy: immutable native runtime ceiling for that generation.

Cua policy is immutable during a runtime generation. Grant expansion therefore
requires a new generation; contraction or revocation should immediately stop
new calls, cancel where safe, revoke/stop the generation, and reconnect under a
new manifest.

### 4. Separate control results from image artifacts

Do not enlarge the existing 1 MiB JSON message limit and do not forward base64
MCP image blocks. Request Cua's file output (`screenshot_out_file`) where the
selected SDK operation supports it, then stream/read that local file into a
distinct bounded blob-upload path rather than materializing base64 in JSON:

1. Electron captures only a locally consented, manifest-scoped application
   window; keep `desktop.display: false`.
2. Apply dimension/byte limits and optional local redaction before upload.
3. Stream binary chunks with `artifact_id`, content type, byte length, digest,
   call/binding generation, and sequence numbers; apply backpressure and an
   aggregate per-call quota.
4. Return structured AX data and an expiring attachment reference in the
   control response, not raw image bytes.
5. Encrypt in transit, restrict attachment ACL to the invoking scope, use a
   short retention default, and make persistence an explicit user choice.

This reduces memory amplification from base64 and creates one auditable point
for screenshot retention. It does not eliminate disclosure to the remote model;
the consent UI must say plainly when a screenshot will leave the device.

### 5. Treat perception as hostile input and actions as non-idempotent

Mark AX text, titles, screenshots, clipboard, and file-derived content as
untrusted model input. System/tool instructions must tell the resident never to
treat screen text as authority to expand scope, reveal secrets, approve grants,
or change the requested objective. App allowlists and Cua manifests constrain
where actions land, but they do not neutralize prompt injection in an allowed
app.

Every action gets a unique `call_id`; Electron records `admitted`, `started`,
and `completed/failed/unknown`. Never automatically retry an action after
timeout, process death, or connection loss once it may have started. Require a
fresh snapshot and explicit reconciliation. Reads may be retried only after
generation validation. A new generation invalidates all element tokens and
snapshot references.

## Changes required in ADR-013

1. Replace “backend speaks MCP over a bridged transport” with “backend speaks a
   versioned, narrow Integral driver RPC; Electron terminates the Cua protocol
   locally.”
2. Remove raw MCP frame channels, initialization proxying, and remote MCP-client
   lifecycle from the design. The backend `DriverSession` should model Integral
   calls, artifacts, generations, and outcomes—not an MCP session.
3. Correct the macOS ownership choice: standalone `CuaDriver.app` preserves the
   vendor TCC identity; an Integral-hosted embedded service is the migration path
   only if Integral intentionally takes ownership of TCC.
4. Make the native manifest launch local and derived from a validated grant.
   Treat manifest approval and runtime construction as Electron-main-only
   operations. Grant changes rotate the runtime generation.
5. Replace the proposed size-exempt/chunked raw-driver framing with a separately
   quota-controlled binary artifact channel. Keep ordinary host messages capped.
6. Require local revalidation for all eight tools, including read tools.
   Observation is sensitive exfiltration even if the OS permission mode permits
   it. “The local user can see it” is not authorization to transmit it to a
   cloud backend or model. Screenshots and AX data require explicit local consent
   to the selected apps, data types, destination, and duration; the backend edge
   mirrors that consent for dispatch/audit but cannot originate it.
7. Add explicit cancellation/deadline negotiation, bounded queues, per-call
   byte/rate quotas, and `unknown_outcome`. Disconnection must fail closed and
   must not replay actions.
8. Bind element tokens to `(binding_generation, runtime_generation, session,
   pid, window_id, snapshot_id)` and reject mismatches before Cua dispatch.
9. Record audit metadata by default, not screenshot content: principal,
   workspace, grant/manifest hashes, operation, target app/window identifiers,
   delivery mode, times, outcome, artifact digest/retention decision, and
   foreground escalation. Redact typed text and AX/screen content.
10. Keep browser automation out of v1. Cua explicitly separates origin-scoped
    typed-browser manifests from generic desktop input, so adding it later
    warrants a separate runtime and ADR
    ([Write a capability manifest](https://cua.ai/docs/how-to-guides/driver/write-a-bounded-manifest)).

## Phased implementation

### Phase 0 — prove the unsupported edges

- Pin an exact Cua version and test on real macOS hardware whether Electron can
  use a supported typed SDK connection to standalone `CuaDriver.app` and rotate
  an exact per-grant bounded manifest. If not, compare the local MCP adapter only
  as a spike and select an Integral-hosted private worker or embedded service for
  production.
- Prototype `createPrivateWorker()` as the reference local broker and verify its
  configured authorization ceiling, cancellation, inherited-channel teardown,
  and unknown-outcome reporting. Treat the macOS daemon adapter as a
  TCC-identity compatibility layer implementing the same Integral RPC.
- Verify which process appears in TCC prompts and that upgrades preserve grants.
- Measure typical/worst bounded window screenshot sizes, AX payloads, capture
  latency, memory, and reconnect behavior.
- Test token validity across separate calls on one local transport and confirm
  invalidation on snapshot/session/runtime changes.
- Exercise timeout and child/daemon death to classify not-started versus
  unknown-outcome actions.

### Phase 1 — observation-only local gateway

- Implement versioned driver RPC on the existing authenticated WebSocket.
- Implement local list-apps/list-windows/window-snapshot adapters with strict
  schemas, selected-window scope, generation binding, deadlines, and quotas.
- Add explicit local observation/data-egress consent, scoped apps,
  `desktop.display: false`, `screenshot_out_file` where supported, the binary
  blob-upload channel, local preview, short retention, and untrusted-content
  labels.
- Ship no action tools. Validate disconnect, runtime restart, daemon absence,
  stale token, oversized artifact, and partial-upload recovery.

### Phase 2 — bounded background actions

- Add local action-grant approval UI and mirror approved grants into durable
  server records for conjunctive dispatch and audit.
- Derive/approve a local bounded manifest and rotate generation on changes.
- Add background-only actions, launch, and terminate-driver-launched with
  non-replayable call IDs and postcondition snapshots.
- Audit metadata and manifest hashes; add rate and action-count ceilings.

### Phase 3 — privileged foreground escalation

- Add a separate, visibly confirmed foreground operation with a short TTL and
  per-action audit.
- Provide a local emergency stop that immediately blocks dispatch and tears down
  the active generation even when the backend is unavailable.
- Conduct prompt-injection, confused-deputy, screenshot-exfiltration,
  reconnect/replay, and renderer-compromise tests before general availability.

### Phase 4 — optional packaging simplification

- Only after operational evidence, consider making Integral own macOS TCC via a
  signed embedded host, or bundling a worker on other platforms.
- Browser automation remains a separate decision with a separate typed-browser
  runtime; never combine an origin-scoped promise with generic browser-window
  input.

## Decision summary

The right Cua topology and the right Integral network protocol are separate
choices. Cua MCP is appropriate between a local agent client and Cua. Integral
already has a remote resident, a server-owned curated contract, an authenticated
outbound desktop connection, and stronger grant semantics than MCP supplies.
For that system, the narrow local gateway is the smallest trusted interface and
the clearest user experience.

Use Cua's native SDK/MCP only on the local side, preserve `CuaDriver.app` on
macOS until Integral deliberately chooses to own TCC, keep the remote agent
behind Integral RPC, and move screenshots through a separately governed
artifact channel. This preserves defense in depth without turning a user's
desktop-control protocol into a remotely tunneled general-purpose endpoint.

## Primary sources

All sources were read in full on 2026-09-30:

- Cua, [Choose a Cua Driver integration](https://cua.ai/docs/concepts/choose-a-cua-driver-integration)
- Cua, [SDK, MCP, and process hosting](https://cua.ai/docs/concepts/sdk-mcp-and-hosting)
- Cua, [Expose MCP from a desktop app](https://cua.ai/docs/how-to-guides/driver/expose-mcp-from-desktop-app)
- Cua, [Use Cua Driver in process](https://cua.ai/docs/how-to-guides/driver/use-sdk-in-process)
- Cua, [Connect your agent to Cua Driver](https://cua.ai/docs/how-to-guides/driver/connect-your-agent)
- Cua, [Write a capability manifest](https://cua.ai/docs/how-to-guides/driver/write-a-bounded-manifest)
- Cua, [How permission policies work](https://cua.ai/docs/concepts/how-permission-policies-work)
