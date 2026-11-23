# Integral Connector Standard v1

**Status:** Accepted architecture and migration target. This is not a general production drop-in guarantee. The local conformance branch proved approved wheel discovery, the authenticated managed-channel lifecycle API, and the Settings contract in tests. Production trust, provider qualification, and migration of existing connectors remain separate gates.

## One product contract, multiple runtime adapters

A connector registers atomically. Core accepts the descriptor, typed setup and lifecycle contract, and implementation for every declared capability together or rejects the entire package. The definition is the source of truth for discovery. The owning service is the source of truth for each installation and its current authority. Sync, external MCP and inbound channels remain separate execution lanes; an adapter for one lane does not grant access in another.

| Contract area | Required behavior |
| --- | --- |
| Package | Stable canonical identifier, publisher/provenance, artifact and compatibility versions, typed capability declarations, supported scope/cardinality, a Core-renderable configuration schema, lifecycle and adapter declarations. A package can declare several capabilities. Preserve established connector IDs; namespace new third-party identities under the host's naming policy. |
| Admission | Inspect reviewable metadata and verify the approved artifact and dependency provenance before loading executable code. Reject missing, incompatible, incomplete, ambiguous or duplicate packages. A distribution/version/import pin is useful in qualification but is not a signature, proof of integrity, or a sandbox. Untrusted code is not imported into Core. |
| Install and Configure | Derive both from the same schema and current caller/scope. Validate non-secret configuration server-side; store credentials as scoped references through Core, never send them back in settings responses. Use explicit installed instances and atomic cardinality. Add installs disabled. Configure has own permission checks, observes current provision and revision, and never skips validation or activates implicitly. |
| Hooks | Stable, typed hooks for observing, installing, updating/validating, probing, enabling/disabling, upgrading and removing, as applicable to declared capabilities. Dispatch uses the registered capability adapter and a narrow Core-provided scope, not arbitrary browser method names or package-supplied React, HTML, or commands. Trusted adapters still enforce exact workspace, instance and revision at commit. |
| Readiness | An enabled flag is not a health result. Verify capabilities in the current provision and revision before serving. List a complete admitted but unready package with Add disabled unless its trusted lifecycle explicitly supports authorized inactive preparation without serving readiness. Keep installed inactive or unavailable instances visible under Connected, with authorized safe recovery, disable and removal. No active or sharing affordance without current authorization and readiness. Reject partial artifacts; preserve unavailable management records where previously installed. |
| Runtime | Core owns tenant and actor checks, resource grants, timeout and capacity rules, normalized health, redaction and audit receipts. Authorize again at execution and on delivery when authority can change. Persist idempotency, retry/recovery and checkpoints as needed; do not treat an in-process lock or registry as multi-worker proof. Expose bounded diagnostic categories and a correlation reference, not credentials, prompts, raw provider errors or internal endpoints. |
| Upgrade and removal | Keep stable IDs, canonical routing, auth and provenance. Apply reversible data/credential migrations. Verify rollback without restoring revoked grants or secrets. Disable/revoke effective authority first; finish external cleanup idempotently. Retain a managed state on partial failure. |

Web Chat is one managed channel instance with separate presentations. The host owns the full-page link, the approved-origin JavaScript embed, and separately scoped optional API credentials. The same current published profile, revocation and conversation scope apply across presentations; the embed does not use a server API key. Activation, link visibility and key issue/revocation require their own authority. Existing Workspace Email and connected/external agents do not gain new capabilities or change ownership by migrating connector UI conventions.

## Qualification gate for each migrated connector

- A separately packaged fixture completes discover/Add, install disabled, Configure, enable/Connected, disable and Remove through real Core APIs. Settings consumes the same typed projections without a connector-specific branch. Test a dev browser against the actual connector where this has not yet been verified.
- Test every supported scope and role, exact instance/epoch mutation, concurrent install, failed health and recovery, stale sessions, retry/idempotency, and disable/removal/revocation across workers. Use a real supported datastore where the adapter requires shared persistence.
- Compare the legacy and new paths using existing IDs, bindings, credentials and history in a disposable store. Verify safe readback, no privilege increases, no duplicate provider operations, connector-specific behavior and rollback.
- Protect secret storage and logging before admitting credential-bearing third-party connectors. Legacy access tokens need a reversible, redaction-tested migration; standardize raw provider-error handling across sync and MCP. Auth, consent and real provider behavior need separate qualification.
- Record the exact tested revision, versions, scope, data boundary, and results. A conformance fixture or UI mock does not certify a provider, production readiness, or an active Web Chat.

## Migration queue

Inventory baseline: Core catalog and built-ins at `206ffa81`. Eleven curated entries plus two built-ins are in scope, alongside the Web Chat reference implementation. No curated YAML marks an entry hidden or superseded at this baseline. Do not introduce inactive placeholders for unshipped providers.

| Wave | Existing connectors or surfaces | Next work and gate |
| --- | --- | --- |
| 0 · Reference | Public Web Chat (`public_web_chat` runtime family; use one stable catalog/install identity) | Replace its special Settings path with the managed-channel contract. Qualify durable visitor/route authority and real tool-free replies through Integral on an approved host. Then verify the actual share link, approved-origin JavaScript embed, and scoped API specification and revocable key controls. The standalone preview and generic sample do not complete this. |
| 1 · Curated HTTP MCP | Calendly (`calendly`), Cloudflare (`cloudflare`), Google Drive (`google_drive`), Gmail MCP (`google_gmail`), Google Sheets (`google_sheets`), Notion (`notion`) | Replace frontend provider branches with admitted descriptors and typed Configure. Keep current IDs, consent, authorization and read-only/write policy. Fix discovery/install role parity. Qualify the first adapter and then do provider-specific consent and tool discovery for each. |
| 2 · MCP special cases | Custom MCP Server (`custom_mcp`), its reviewed-registry install path, QuickBooks MCP (`quickbooks_mcp`), Serper Web Search (`serper_web_search`) | Move to the same install/configure/remove projection. Retain free-form HTTPS as authorized today, distinguish vetted stdio packages, and do not offer arbitrary commands in production. Verify per-user/shared cardinality, credential isolation, process cleanup, and revocation. Registry discovery is an install source, not a new connector identity. |
| 3 · Existing inbound channel | WhatsApp (JV Connect) (`whatsapp_jvconnect`) | Use a thin adapter over the current workspace grant and independent verified personal-number binding. Verify signed ingress, reconnect, removal and old permission preservation. Keep JV Connect distinct from any future Meta adapter. |
| 4 · Native synchronization | GitHub Issues (`github_issues`), Gmail (`gmail`), QuickBooks (`quickbooks`) | Close secret-storage and diagnostic gates before expanding. Adapt the current native runtime; preserve source IDs, track bindings, sync cursors, ownership, conflict handling and leases. Prove credential migration/rollback and scope and datastore parity. Native Gmail and QuickBooks are distinct from their MCP editions. |
| Compatibility | Existing installed versions, legacy aliases and scope records across waves | Keep routing IDs and explicit legacy migrations until consumers/callers have changed. Do not republish deprecated packages merely to retain compatibility. Uninstalled planned channels stay absent. |

Out of this catalog migration: Workspace Email outbound delivery, the server exposing Integral over inbound MCP, the quarantine-only outbound agent stub, and in-app notifications. A separately delivered email-as-agent channel should register as a new connector when implemented.

See the [source audit](connector-extension-audit.md), [Connector implementation guide](connectors.md), and [ADR 010](adr/010-connector-subsystem-architecture.md).

The detached full-page Web Chat reference authority reads the same persisted
workspace profile-route policy used by the managed provision store. Add and
candidate-profile configuration require verified workspace authority and
durable storage, not visitor transport readiness. A disabled assigned
provision can be ready for enable without admitting visitors. Public page/API loading requires an enabled
provision, canonical approved HTTPS origin, exact route epoch and eligible
published profile version. A host reference is immutable: issue a different
opaque key after epoch, publication, provision, or origin changes, and maintain
consistent key and provision lookups. Neither the reference reader, its
transport check, nor the page/API boot wiring is supplied by this detached
authority. An installed but unassigned provision remains configurable without
being eligible for enablement.

An unmounted PostgreSQL host reference store supports this read port with a
current provision record and an opaque-key index updated in one transaction.
Only trusted host code may supply approved origin/scope/publication and invoke
issue/revoke after its own authorization. Rotate on every change. Revoke
retains a revision tombstone, so old host decisions cannot restore a route.
On uncertain commit, reread instead of replaying an unconditional issue. This
store neither verifies domain ownership nor mounts routes, emits URLs, or changes
connector/profile state. The host must supply domain/transport attestations
and own retention of retired provision tombstones.

The detached Web Chat share read is
`GET /api/agentive/managed-channels/public_web_chat/share`. Standard Integral
authentication, an exact `X-Integral-Scope: ws:<workspace_id>`, and manager
authority are required. The read takes `workspace_id`, `provision_id`,
`route_epoch`, `profile_key`, and `published_version` for one observed
selection. It returns only the scoped `approved-full-page-share-v1` proof
with a host-issued `full_page_url`, or a generic unavailable response. The
URL must be the verified canonical HTTPS origin followed by
`/chat/<opaque-reference>`; no query, fragment, credentials or alternate
origin are accepted. The API does not issue links or register on import.
An authorized host must explicitly mount it behind standard API authentication
and supply the existing link read and current route resolver.

The detached host assembly returns one Web Chat registry, authenticated-share
router, visitor router, and a read-only page context lookup. Its trusted
platform owner must provide approved HTTPS origin evidence for each observed
assignment, including disabled pre-enable checks. That hosting evidence
is not a prerequisite for disabled Add or Configure. No output is mounted or installed by constructing the assembly.
After the durable assign commits, the trusted owner reconciles that provision
before Enable. Enable advances the epoch; the owner reconciles the new epoch
before any share, page or browser API is admitted. The same flow applies after
republication or an approved origin change. The old route fails closed while
the owner is reconciling. The private reconciler accepts only a provision
identity, rereads managed state, and never takes a browser-supplied key,
assignment, or origin. On remove, reconcile the retired provision to retire
its stored reference. Generic normalized event dispatch is not a substitute
for the cookie-bound browser ingress.

The connector-owned runtime can contribute a management-only package (no
routes) or a reviewed full-page package with separate authenticated share and
visitor document/API routers. Both use the same generic contribution registrar;
that registrar registers package metadata only. The approved host explicitly
mounts each route group at the correct trust boundary. The full-page option
requires canonical origin, current platform attestation and host readiness,
reviewed standalone assets, PostgreSQL and explicit inference/budget ports.
Optional embed and service-client route groups have distinct approved HTTPS
origins and explicitly separate host readiness, policy/credentials and budgets.
Neither is automatically mounted or activated. An approved transport also
requires the exact current service identity for its published profile via a
trusted authorization port. Identity replacement must invalidate the route
epoch before any visitor can reuse an old session. This contract does not map
profile identities itself or add server-side grant management.

This is a trusted in-process composition hook. It does not change the v1
third-party installed-distribution entry point, which still discovers a
pure package and never mounts any runtime. Hosts mount only the separate route
groups returned after successful catalogue registration.
