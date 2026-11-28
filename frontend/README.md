# Integral frontend

The web interface lets people work with the same records, relationships, and authority used by Integral's agents. It is React 18, TypeScript, Vite, and Tailwind with shared UI primitives.

```bash
npm ci
npm run dev
npm run lint:types
npm run test:run
```

The source server runs at port 9006 and proxies API traffic to the backend at 4000. Container and packaged-wheel serving use their own configuration.

`pages/` contains routed experiences; `components/` contains records, views, collaboration, layout, and shared UI; `features/ai-chat/` manages conversation; `context/` coordinates auth and scope; `api/` supplies clients; `views/` defines palette contracts.

Use the shared UI system rather than duplicating typography and surface literals. Named references resolve through authorized APIs. Filters and hidden controls are not access boundaries. Preserve explicit loading, empty, denied, unavailable, and error states.

Keep readable App content independent of collaborator-directory latency. Editing
stays unavailable until current access details succeed; access failures offer a
separate retry. Reset local data and editing state on App or account changes.
Load track-link pickers and dashboard editor metadata when those controls are
used. Select the default dashboard before starting its data request.

Session boot and capability consumers share the public deployment-metadata
request without depending on authenticated renewal. Identical reads share their
pending request even when it outlasts the reuse window; that window begins at
completion. Writes and live changes still invalidate reads, and authenticated
GET sharing remains partitioned by token and workspace. Reasserting an already
confirmed or pending workspace does not write scope again; failed writes remain
retryable. See [performance qualification](../docs/ops/PERFORMANCE.md) for query
budgets and browser measurement requirements.

Event-history catch-up collects at most ten pages before reconciling active
queries once, with one trailing refresh for an older read already in flight.
Completed pages survive a later page failure or hidden-tab pause. Their cursor
advances after reconciliation; credential renewal preserves the same workspace
subscription, while account/workspace changes retire pending work.

Default Track navigation uses current scoped view flags and the API's effective
navigation defaults. Shared-model choices stay local to the Track; general
schema reads do not acquire saved-view collections. Preserve explicit view
links, hidden-view fallback, and selection through a reload when changing this
contract.

Auth uses a static canonical outline. The split-square logo geometry has no frame. Review narrow screens and keyboard interaction as well as desktop output.

Application-wide notices and welcome guidance use `useSystemNotifications` and
the shared `SystemNotificationBar`. One notice is visible at a time; guidance
yields to verification, updates and operational alerts. Avoid separate sticky
status strips in the shell: the sidebar, assistant dock and viewport heights
share the bar's measured `--system-bar-h` offset. Remove account-specific notices
on unmount or sign-out. Queuing a notice behind another is not user dismissal.

Read the [user guide](../docs/user-guide/README.md), [view palette](../docs/operational-models/VIEW_PALETTE.md), and [architecture](../docs/product/ARCHITECTURE.md). Browser readback is required for experience claims; a build alone does not establish them.

### Resource breadcrumbs

App-owned resources use `Home → Workspace → Apps → App → Track → Entry`.
Anchored and relation navigation retain their parent-record links. Standalone
Tracks keep the Tracks listing as their parent. A resolved resource's workspace
wins over a temporarily stale workspace switcher; equal labels do not imply
that two distinct resource links are duplicates.

### Optional iframe Web Chat asset

`npm run build:web-chat-embed` produces a separate
`dist-web-chat-embed/public-web-chat-embed.html` and hashed files referenced at
`/web-chat-embed-assets/assets/`. Neither the standard build nor
`build:web-chat` emits this artifact. The build installs no route, starts no
service, and gives no workspace permission.

Only a separately reviewed HTTPS embed host may render the template on its
vetted origin for a current admitted route and publication. Place the
HTML-escaped values on `#public-web-chat-embed-root` exactly once:

| Attribute | Host-projected value |
| --- | --- |
| `data-web-chat-embed-route` | The current opaque embed route segment, not a URL. |
| `data-web-chat-embed-origin` | The iframe's canonical HTTPS origin, not its parent's. |
| `data-web-chat-embed-bootstrap-seed` | A fresh short-lived `iweb_` proof for this embed admission. |
| `data-web-chat-embed-title` | A public display title, never credentials or visitor information. |

Missing, malformed, repeated roots or unexpected embed configuration fail
closed; opening the template as a top-level page does not start a session.
The entry removes its configuration attributes before rendering or issuing
requests. This does not remove the original bytes of the HTML response.
The proof is used once in a bounded JSON request to the same-origin embed API,
never in a URL, cookie, browser storage, or parent message. The bearer remains
in this frame's JS memory. The first embed release must not offer in-place
session rotation without server support; reopening from the embedding site
requires a freshly admitted document and starts a new conversation.

The host must enforce the exact allowed frame ancestors in CSP on the distinct
HTTPS origin, serve the rendered document and API replies with
`Cache-Control: no-store`, serve only reviewed same-origin scripts on this
document and avoid logging or persisting its rendered proof. The iframe
client's origin check does not authenticate the parent or grant authority.
The server must independently verify the route, publication, origin policy
and revocation, and atomically consume the proof.
