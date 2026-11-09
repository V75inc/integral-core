# Trusted React App UI v1

Reviewed App packages can add custom React components without changing Core
source or rebuilding its frontend. Core remains domain-neutral and boots without
Business. Declarative Home components, existing view widgets and opaque iframe
extensions retain their contracts. Ordinary App settings remain in the standard
settings dialog. This source feature is disabled by default and is not a release
or deployment attestation.

## Authoring and placements

The public [React SDK](../../sdk/react/README.md) supplies controls, tokens,
scoped queries/operations, navigation, feedback and Entry validation/submit hooks.
Apps import the SDK and React, never private Core modules. Build a single ES module
using the SDK's Vite configuration. Core's public import map shares React and the
SDK context with the host. Public runtime entries preserve their exports in
production and are revalidated rather than immutably cached. Docker frontend
builds use the Core repository root as context (`docker build -f
frontend/Dockerfile .`) so both `frontend/` and `sdk/react/` are available.
Development containers mount the SDK read-only at `/sdk/react`; Vite permits
only its frontend and SDK source roots.

```yaml
app:
  ui_components:
  - key: editor
    entry: ui/editor.mjs
    digest: <64 lowercase hexadecimal SHA-256 characters>
    export_name: default
    sdk_range: ^1.0.0
    placements: [home, track, entry_compose, entry_detail]
    props_schema: {type: object, additionalProperties: false}
```

Declarations are bounded to 32 per App; artifacts to 2 MiB; props to 128 KiB;
local object schemas to 32 KiB. V1 supports the SDK 1.x/React 18 contract, expressed
as `^1.0.0` or the additive `^1.1.0`; unsupported ranges are rejected. Artifact paths stay inside the package.
Props use local JSON Schema references only. SDK v1 emits one module; assets,
remote imports and dynamic imports are rejected by the authoring build.

Home compositions use a leaf `{id, type: react_component, component_key, props}`.
The key must be declared for Home, including when preserving instance layouts
through an upgrade. Entry `ui_contributions` may use `component_key` plus `props`
instead of a saved view, inline widget or iframe key. A Track `extension_view`
widget may set `config.component_key` and `config.component_props`. Its existing
`extension_view_key` is the initial, operator-disabled fallback. Nested Entry
regions derive compose/detail placement from the contribution lifecycle.

## Admission and delivery

`POST /api/extensions/{app_id}/components/{key}/mount` accepts `{placement, props}`
and returns `{enabled, component, artifact_url, definition_revision}`. It requires
ordinary authentication and resolved workspace scope. Every response is gated by
fresh App access, canonical workspace/package identity and the exact active
definition after artifact verification. Placement/props must match the admitted
declaration. Authoring drafts never select code; the returned revision comes from
the primary active-definition proof rather than a cached App model.

Within each admission boundary, independent access and active-definition proofs
run concurrently. Canonical containment uses the existing bounded raw ancestor
batch reader and the same authority Walker; active-definition uniqueness reads
only IDs. Cold mounts check before and after artifact work. A bounded five-minute
LRU (256 entries) may reuse previously verified public release metadata as a
candidate, keyed by principal, workspace, App, component, placement and canonical
props hash. It re-verifies the artifact before obtaining fresh access and an
exact active-declaration comparison; it never caches permission decisions. A
changed release discards the candidate and performs one cold resolution. Core
preloads its shared React/JSX/SDK entry modules at boot to avoid an import
waterfall; App code is still loaded only after its authorized descriptor resolves.
Track composers can warm the likely next component's public module after an
authorized descriptor resolves. This prefetch is cancelled on leaving the Track
and does not fetch business data. Its descriptor is not reused as authority for
opening the form: the host still obtains fresh admission. Verified immutable
artifact bytes are reused from the private cache with current trust/integrity
checks rather than republished from mutable package files on each mount.

Operators configure these environment variables; App metadata cannot set them:

- `INTEGRAL_UI_ENABLED_COMPONENTS`: comma-separated `package-slug:component-key`.
- `INTEGRAL_UI_PUBLISHERS`: JSON object mapping publisher names to
  `{public_key: <base64 raw Ed25519 public key>, packages: [<allowed slugs>]}`.
- `INTEGRAL_UI_DEVELOPMENT_PACKAGES`: explicit unsigned development allowlist,
  effective only with `DEBUG=true`.
- `INTEGRAL_UI_ARTIFACT_ROOT`: private cache directory; defaults to the OS temporary
  directory. Production should select persistent storage per cell.

Production signatures bind canonical JSON containing `format: integral.ui.v1`,
publisher, package slug and every normalized declaration field. The matching
`.<component-key>.sig` file is base64 text beside the `.mjs`. Signature verification and publisher
package admission precede copying verified bytes into the artifact cache. The
signature binds the module digest, export, SDK range, placements and props schema.
Changing any of those requires a new signature. The existing configured bundle
signature gate also covers JavaScript-only packages; detached UI admission remains
mandatory even when the older bundle verification key is unset.

`GET /api/ui-artifacts/{slug}/{digest}/index.mjs` serves only cached, admitted,
digest-checked JavaScript. These approved code assets are publicly readable and
contain no customer data or credentials. Responses use JavaScript MIME, nosniff,
and immutable caching. Current configured publisher trust and component enablement
are rechecked on server delivery; cached/executed JavaScript cannot be revoked
from a browser. Retain admitted old digests for rollback; do not overwrite code at
a digest URL. No raw package path is served through this endpoint.

A frontend on another origin still requires a same-origin reverse proxy for the
artifact route. The Docker nginx config gives `/api/` precedence over `.mjs`
static handling; Vite and `integral web` proxy the same route. The static hosted
nginx config requires the deployment's existing API routing to provide it. Public
runtime modules remain on the frontend origin. Do not relax CSP to permit blob
scripts, eval or arbitrary remote module hosts.

## State, failures and security

Mounted state and data are partitioned by principal, workspace, App, component and
record context. Queries and navigation carry the captured workspace explicitly;
late replies cannot populate a replacement mount. Writes retain a stable intent
key after network/5xx uncertainty and never retry automatically. Preview writes
and navigation are blocked; live preview reads require opt-in. The existing server
dispatcher remains authoritative for permissions, revisions, approvals and receipt
recovery. The SDK never treats withheld recorded output as an available result.

Components register the existing Entry validation/submit lifecycle. Loading or
failed forms fail closed. Ordinary prop/draft changes preserve component state.
A new release prompts to reload and discard unsaved state; the reload control is
disabled during pending operations. Failure after execution does not automatically
switch implementations or replay a mutation. Disable the component, reconcile any
uncertain outcome and reload before using its retained iframe fallback.

Native modules execute with Core frontend privilege, including DOM and browser
storage access. Publisher review and signed delivery address code provenance;
they do not sandbox code. Error boundaries recover rendering failures but cannot
contain malicious code, infinite loops or browser-wide resource exhaustion.
External/unreviewed executable UI retains the existing opaque iframe sandbox and
restrictive CSP. Manifest trust labels and resident approval cannot admit native
JavaScript. Preserve I-SUBSTRATE-01, I-EXT-01/02, I-UI-SCOPE-01, I-RUNTIME-01,
I-BUNDLE-01/03/04 and existing approval/receipt invariants.

## Validation

`backend/tests/test_ui_components.py` covers signed content, tampering, trust
revocation, unsigned dev admission, path escape, active-definition changes, props,
placements, Home upgrade compatibility and retaining two artifact versions.
Frontend component/operation tests exercise retained intent identity, duplicate
submissions, preview, unavailable code, scope changes and unsaved state.
Business owns its pilot, synthetic browser comparison and PostgreSQL readback
qualification; see its Finance UI specification. Distribution, activation and
production performance require separate qualification on the intended release.

## Entry composition within React

SDK `EntryRegion` accepts either typed `fields` (optional `columns`) or a saved
`viewKey` on the host Entry's track. It reuses the native form/line controls and
their existing server contracts. `FormRegionWidget` has an opt-in `show_labels`
presentation for externally composed fields; defaults retain their behavior.
Child lifecycle handles are aggregated, unavailable views fail closed, and
submissions stop at the first failure while retaining prior recorded outcomes.
The public primitive resolves lazily to keep the host/view registry boundary
acyclic. Native App components cannot be directly mounted as nested regions.

An Entry `component_key` may declare `fallback_view` for its original saved
composition. The disabled fallback owns its lifecycle; enabling a mounted legacy
view still requires explicit reload before switching implementations. Finance
uses this contract for its live invoice composer.

Native line regions suppress identical totals/related-row echoes when async tax
metadata triggers recalculation. This prevents parent draft updates from
recreating the callback and starting another recalculation render. Changes to
line content, record scope or parent totals still propagate. Typed form regions
resolve their host entry type through the shared key/name/ID resolver.
Line edits advance the pending-row ref and notify the parent at the interaction
boundary, outside React state updater functions, so consecutive edits preserve
all rows without updating the parent during render.

Vite development deployments can set `INTEGRAL_DEV_API_PROXY_TARGET` for a
private proxy upstream without exposing it to browser code. Browser REST and
event WebSockets then use the page origin. Keep Docker-only hostnames out of
`VITE_BACKEND_URL`, which browser clients also consume.

## React SDK 1.1: App-owned fields and bounded lookups

SDK 1.1 is additive. Existing `^1.0.0` declarations remain supported; components
using these controls/hooks declare `sdk_range: ^1.1.0`. The host rejects a
component requiring a newer SDK before executing its module.

`useEntryDraft()` exposes host-loaded presentation `fields`, current `values`,
`errors`, retained child `related` drafts, `mode`, `entryId`, `patch(values)` and
`register(handle)`. Fields include installed labels, types, enum values,
required/read-only flags and help. Patching uses the host's draft and existing
validation/date-offset rules. It does not save. The App must not independently
rediscover its host schema. `Input`, `Select`, `Textarea`, `DateInput` and
`AsyncCombobox` use host controls; their metadata binding remains App-owned.

An Entry contribution may opt into `owns_field_data: true`. Core defers generic
relation/settings/number preloads while admission is pending and while the App
owns them. An operator-disabled component restores the original initialization
through its saved fallback. Existing declarations keep their prior behavior.
Switches retain the host draft and require explicit reload; no action is replayed.

`useAppQuery(key, params, {enabled, staleTime})` dispatches/subscribes only when
enabled. Reads default to 30 seconds of freshness; `staleTime: 0` explicitly
requests a fresh read. Freshness is bounded to five minutes, and unused entries
are removed after five minutes. Canonical keys include principal, Workspace,
App, component, active definition revision, preview/live-data policy and sorted
object parameters. Arrays retain their order. Identical in-flight reads share
transport. Aborting one consumer leaves the others active; the final departure
cancels transport. Scope switches purge prior-scope reads. Permission/release
changes purge affected reads; ordinary data events invalidate and coalesce
refreshes for enabled consumers. Cached data never authorizes an operation.

`AsyncCombobox` opens lazily, debounces typing by 250 ms and renders at most
50 returned options. `loadPage(q, cursor, signal)` returns
`{items: [{id, label, description?, disabled?}], next_cursor}`. Page navigation is
explicit; the control never walks every page. It does no track/schema discovery.
Selected labels can be supplied separately. Its portalled footer participates
in the owning dialog's keyboard cycle.

A retained `editable_related_lines` region may declare `catalog_query` and
`column_options.<field>.query`. In a React App host it uses declared paged reads
and bounded selected-ID resolution. Existing tool settings remain its legacy
fallback. Missing tax metadata blocks validation and does not replace saved tax
with zero; calculations and persistence remain in their existing handlers.

Explicit-field Python `read_entry_records` calls use the canonical authorized
projection iterator and return requested values without constructing Entry
objects. It adds current authority fields internally, rechecks every page and
falls back to admitted raw rows when the adapter lacks native projection.
Finance uses this path for number preview; save-time uniqueness stays unchanged.

For measurement, the Entry host emits the browser performance mark
`integral:entry-ui-metadata-ready` once its installed metadata is available.
Measure editable rendering, bootstrap, number preview and line loading separately.
A build or unit test does not qualify the latency targets.

Typed navigation optionally accepts `source_entry_id` and `field_key` for an
existing cross-App Entry reference. Core verifies the persisted typed relation,
its declared destination App/type, the same Workspace, both Apps' current access
and the destination Entry permission. It returns a governed path, never foreign
track enumeration or additional data authority. Unsaved references cannot use
this exception.


The public Python operation context also supports `delete_entry(id,
expected_record_revision=...)` for human mutation commands with a durable intent
and deferred event outbox. It requires current ordinary `entry.delete` authority,
canonical owning-App/Workspace containment and the observed revision, and uses
Core's existing guarded deletion/cleanup inside the operation transaction.
Read-only and delegated adapters cannot acquire this capability. Protected-state
and approval guards remain authoritative. Finance uses it to replace draft
lines without clearing a required parent relationship or pre-deleting in the
browser; the command receipt covers the complete replacement.

The Python context also exposes `async with ctx.track_write_scope(track_id)` for
App-owned collection checks that must serialize with a following save. Its
PostgreSQL fence lives until the outer command commits, including when the
local `with` body ends earlier. The current owning App, workspace and read
admission are checked before and after acquiring it; read-only contexts fail.
It grants no mutation authority. Existing canonical write permits, revisions,
receipts and rollback still apply. Single-process stores retain their existing
entry-write serialization and cannot promise multiworker transaction semantics.

## Reference qualification, 2026-11-07

A local Finance invoice comparison used production assets, real fixture
identity, the existing mounted foundation source frozen without changing its
artifact selection, and disposable PostgreSQL data. Both source stacks completed
20 cold and 20 warm opens at each of 100, 1,000 and 10,000 customers. Cold means
a fresh browser context; warm means reopening within it. Candidate p95 stayed
below 0.78 seconds cold and 0.74 seconds warm from New invoice to editable fields
and line editor. Number-preview p95 fell from 2.5–4.1 seconds to 0.24–0.59 seconds;
median warm initialization fell from eight requests to two. Every candidate open
made exactly one preview request and avoided eager whole-track option loads.

These are fixture observations, not a product SLA or deployment attestation.
Page startup to network idle remained up to 12.1 seconds p95, including background
discovery and a 500 ms idle window. The scaled data contains customers and an
empty invoice history; a large invoice-history preview scan remains linear and
unqualified. The broad regression gate remains non-green: its two backend
failures reproduce on the original source, frontend accessibility timeouts pass
in isolation, and existing static-check failures remain. The owning Finance
report records full percentiles, work counters and limitations; in an adjacent
Business checkout see [invoice qualification](../../../integral-business/docs/finance-invoice-performance.md).

## Shared App patterns (SDK 1.2)

SDK 1.2 is additive to 1.0/1.1. The host supplies AppPage, AppHeader, AppSection,
AppGrid, AppField, AppActions, AppSummary and AppNotice through the same primitive
provider. These are presentation only. The public `appTheme` aliases resolve
Core's existing live CSS variables; Apps inherit its light/dark theme. Field
bindings associate controls with labels, required state, help and errors.

The canonical [App UI guide](../../sdk/react/APP_UI_GUIDE.md) ships in the SDK.
`bin/app-ui-guides.mjs` synchronizes local, digest-marked guide snapshots and
Codex/Claude/Cursor entry files, preserving handwritten instructions. Its `--check`
mode performs no writes. Project instruction loading depends on the client;
independent design/guide checks enforce the contract in Business CI. Native code
continues to require operator trust and signed/admitted artifacts; the design
guard is not a security sandbox. SDK 1.2 source changes do not update deployment
pins or activate installed Apps.

The paired Business source pilot records SDK 1.2 measurements and unresolved
admission-tail limits in its [shared UI qualification report](../../../integral-business/docs/shared-app-ui-qualification.md). The initial matrix and fixed follow-up are retained separately; do not infer release qualification from a smaller artifact or a passing follow-up alone.
