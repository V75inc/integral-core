# Connectors

Connectors give Integral declared paths to external information and capabilities. Native synchronization and external MCP mounts share lifecycle concerns but have different execution contracts.

## Catalogue and connection lifecycle

Use the [Connector Standard v1 and migration queue](connector-extension-standard-v1.md)
for normative package, lifecycle, readiness and qualification requirements.

The Connector Library presents one collection of definitions. The Settings
catalogue projection gives each definition a stable ID, a provider, supported
scopes with cardinality, a list of capabilities (`api`, `mcp`, `channel`), and
the setup flow. Capabilities can be combined. Existing curated package IDs are
their API slugs. Their current install metadata and endpoints still come from
the server. Built-in adapters for Custom MCP and WhatsApp use their existing
specialized endpoints. Catalogue metadata describes the product surface.
Authorization and instance cardinality are enforced by the owning service.

**Add** opens setup for a new connection. A singleton displays **Configure**
once its connection exists. **Connected** lists actual connections, with
configuration and removal on each instance where the caller can manage it.
Custom MCP supports multiple connections, with one workspace connection per
server identity under the current normalized-name guard. Curated packages allow
multiple per-user connections and one workspace connection per package.
**Workspace** is the label for the API's `shared` scope. Eligible workspace
members can use that connected account. The connection owner and authorized
workspace admins and owners can manage it.

### Managed inbound channel packages (source-only)

The versioned [managed channel contract](../../backend/app/connectors/channel_packages.py)
accepts one reviewed server package atomically: a stable catalogue definition,
durable workspace lifecycle callbacks, and the serving adapter's readiness and
verified-message entry point. This is separate from the sync and MCP execution
registries. A planned name or metadata-only entry does not register a channel.

Keep the definition, installation, and live route separate. A complete ready
package with no provision can offer **Add**. **Connected** lists explicit
durable installations, never agent-profile settings or an enabled flag alone.
An installed, disabled channel can show **Configure** and enablement when ready;
an installed outage can still show **Configure**, but must not offer enablement
or sharing. An active channel requires a matching, accepting current provision.
Full-page links are optional, server-issued HTTPS URLs and excluded from default
discovery. The separate share read must recheck manager and exact transport authority.

Connector Library and Connected consume the same authenticated management
projection. The generic frontend handles the versioned setup kind rather than
a connector's slug; it does not evaluate package-supplied UI code or assets.
A reviewed management-only package may support inactive Add and Configure
without a hosted transport. Serving requires a separately configured, ready
host. Management-only registration does not activate routing. The standard
server has no default Web Chat, Facebook or Signal package registration.

An isolated qualification harness can explicitly load one installed
distribution using the [approved package loader](../../backend/app/connectors/channel_package_discovery.py).
Pin the distribution's exact identity and version, the channel slug and the
`module:factory` target in the `integral.managed_channels.v1` entry-point group.
The loader validates unique metadata and the factory's installed ownership
before import, then accepts only one complete matching package into the
registry. There is no startup scan, installation, route mount or automatic
workspace enablement. The [independent sample wheel](../../examples/reference-managed-text-channel/README.md)
is in-memory and synthetic, not a shipping connector. Native sync and curated
MCP retain distinct installation and execution contracts.

The [out-of-tree conformance test](../../backend/tests/test_out_of_tree_channel_smoke.py)
builds the test wheel offline, explicitly loads it and exercises the actual
authenticated management API: discover/Add, install disabled/Configure, enable,
reject a stale epoch, and remove back to Add. Generic UI tests cover the same
states against the wire contract. These checks do not certify durable lifecycle
storage, an external provider, public serving, browser-to-live deployment, or
upgrade/rollback. Those remain separate qualification gates.

Version and import-origin pins are not signatures, artifact integrity, or a
sandbox. Approved factories execute as trusted Python. Before any production
loading path, add an authenticated operator review and artifact verification
policy (including dependencies), explicit host composition, and release
qualification. Never use request values or unreviewed modules as approvals.

## WhatsApp (JV Connect)

**Settings → Connectors → Add Connector → WhatsApp (JV Connect)** opens an
in-place dialog for the workspace line and personal number settings.
Installed lines also open that dialog from their row in **Connected**. The catalogue ID is
`whatsapp_jvconnect`, and `/settings#whatsapp` still opens the same panel.
Connections keep their channel binding and identity records. A future direct
Meta integration can use a separate definition and its own credentials.

The workspace line is a singleton. Add and Configure use the existing binding
PUT with an explicit expected binding ID. A null ID requires an empty workspace.
A supplied ID must match the current grant, and a verified number cannot be
silently replaced from Configure. Omitting the field preserves the original
PUT behavior. Workspace grant changes serialize by workspace in PostgreSQL,
with an in-process lock used by the local JSON fixture. Disconnect removes the
workspace's grant. Other workspace grants and personal number links remain.

[Workspace Email](../platform/workspace-email.md) stays under **Settings →
Workspace → Email** for outbound delivery.

## Public Web Chat preview and delivery boundary

The explicitly enabled loopback preview now serves a full page and a same-origin
iframe demo. They render the same chat and share one disposable channel pinned
to the synthetic preview assistant. Both use the same session and message
handlers, per-visitor rate and history limits, and deterministic tool-free
replies. Only the iframe presentation accepts a same-origin parent. The main
page remains unframeable. The preview has no catalogue entry or share URL in
Settings.

The managed route already records one eligible published public profile and a
separate enabled flag. Detached hosted adapters supply a server-issued visitor identity and recheck
the publication, route generation, and enablement before history reads and
turns. A deployment still needs explicit host readiness, approved origins and
mounts. Embeds also require their own current origin and frame policy.
An inactive or
unassigned route must expose neither a link nor embed code.

The detached [full-page visitor composition](../../frontend/src/features/public-web-chat/PublicWebChatPage.tsx)
expects only public `{route, title}` configuration supplied by an approved host
after verifying the active route. It uses that opaque route for same-origin
visitor requests and creates no link, route mount, default configuration, or
Settings action. Do not include account, workspace, profile, model, or visitor
credentials in page data. The host still owns its HTML bootstrap, response
policies, and revalidation of the live route on every visitor request.

The detached [Configure share view](../../frontend/src/features/settings/connectors/ManagedChannelFullPageShare.tsx)
does not obtain, construct, or publish a link. An approved host must first
provide a separate authorized share read; default catalogue, mutation and
profile configuration responses never expose the URL. The detached typed
projection matches the read's captured manager and local non-secret session
fence, workspace, connector, provision, route epoch, and exact published
profile/version to the current view, and rejects malformed or extra fields.
Hide while checking or when inactive, and invalidate the read on publication
changes even if the channel epoch remains the same. The host must independently
deny obsolete links before Settings refetches. A reviewed host may explicitly
inject the read control into standard Configure. It is opt-in, requires an
active install and the same exact configuration's eligible published version
(not its profile revision), and reads only after a user gesture. The read has
no cache and is discarded when the session, selection, or publication changes.
Standard Configure wires the Web Chat reader only when current server
configuration supplies the exact full_page_share_check_available indicator
and publication. A reviewed host can also supply a scoped
managedChannelShareSource to SettingsPage. Embed controls require their own
server-discovered approved_embed_policies. Neither reader mounts a route or
authorizes new origins and neither makes an unconfigured host serve.

The detached [public request budget](../../backend/app/agentive/services/web_chat_request_budget.py)
requires an explicitly supplied PostgreSQL adapter and identical fixed-window
policy across its workers. It atomically charges deployment, workspace, and
exact provision limits for starts and sends, plus a provision-local send limit
for the verified visitor. A missed deadline or uncertain commit does not admit a
request. It stores bounded hashed visitor counters, with one stable row per
workspace and provision rather than one row per request or time window.

Before enabling a host, qualify a separate bounded public connection pool and
reserve worker and model capacity for staff; rate counters alone are not
resource isolation. Set bounded deadlines, coordinate policy changes and clocks,
and test the effect of the shared public counter's lock and fixed-window
boundary bursts. Provide a retention policy for abandoned workspace/provision
rows without resetting active budgets. No host wiring, provider capacity, or
traffic volume is established by the detached adapter.

### Full-page API and optional server integration

Deliver the full-page UI first, backed by the existing same-origin
`POST /api/public/web-chat/{route}/session` (`mode: new | resume`) and
`POST /api/public/web-chat/{route}/messages` (`id`, `text`). Both require
canonical HTTPS and an exact origin; sessions use a route-scoped Secure,
HttpOnly, SameSite=Strict cookie. The browser API is detached and qualified with
synthetic inputs; it is not mounted by Core startup or a cross-origin client
API. Do not relax the browser gate to accommodate an integration.

If server-to-server clients need direct access, add a separate **versioned
transport for the same Web Chat connector**, not another connector, profile
assignment, or approval path. The v1 HTTP adapter and PostgreSQL-backed grants,
short-lived sessions, and durable turns are available through the **detached**
`compose_web_chat_client_api` factory. Core does not mount it; no public endpoint
or manager-issued client keys are currently available. The adapter requires a
supplied canonical HTTPS origin, rejects query credentials, browser
origin/cookie context, and wrong credential classes, and checks the bound
grant/session again immediately before returning protected output. The host
must supply the exact management review and assignment authority, bounded
public model capacity, and a separate durable client request budget.

v1 isolated contract:

| Request | Authorization and result |
| --- | --- |
| `POST /api/v1/web-chat/sessions` | Revocable per-provision client bearer; create a visitor session and return a short-lived opaque session bearer with an empty snapshot. |
| `GET /api/v1/web-chat/session` | Session bearer; resume that conversation and return its bounded history. |
| `POST /api/v1/web-chat/session/messages` | Session bearer with `{id, text}`; return bounded history on completion, `202 pending` until durable, or `409 conflict` for changed input with the same ID. |

Session issuance requires an empty request and returns only `session_token`,
`expires_in` (at most 900 seconds), and an empty `messages` snapshot. Use the
two bearer classes in the `Authorization` header only. Successful history
and turn responses carry the same bounded plain-text message DTO as the
full-page UI; they never return either credential. Never log the returned
session bearer. There is no CORS or browser-cookie fallback.

The server derives workspace, provision, route generation, exact published
profile/version, and client grant. No caller-supplied scope, browser cookies,
keys in URLs, CORS access, or Integral workspace role. Check the current grant,
route, and tool-free publication before each history read and turn and again
before exposing a reply. Grant revocation, disablement, expiry, or a change of
route or publication cuts off existing sessions. Reuse the bounded history,
durable receipt, and rate-budget boundaries; enforce deployment, workspace,
provision, client, and visitor limits on a dedicated public capacity budget.
Exact retries must not re-run a turn; changed payloads using the same ID must
conflict. Return bounded plain text with no-store responses; redact credentials,
text, and provider details from diagnostics.

Qualify on detached ASGI with disposable PostgreSQL and a synthetic model.
Prove two clients and two visitors stay isolated; concurrent/replayed requests
run the model once; altered retries conflict; rate, expiry and revocation
apply, including during inference. Verify browser authorization is unchanged,
workspace tools are never available, and no anonymous API is mounted by Core
startup. This exercise does not establish live capacity or a real provider.

## Native synchronization

A native connector implements the registered sync interface and binds to a Track through `IS_CONNECTED_TO`. It supplies stable external identity, record transformation, credential requirements, and deduplication semantics. Scope and policy gates precede synchronization.

Provenance keeps `source: connector` separate from the connector/external source identity. Audit actions carry the connector actor rather than substituting the human who requested a sync. Repeated records follow the configured update/conflict branch rather than creating duplicates.

Supported conflict modes include mirror-only, last-write-wins, and manual resolution. Conflict records are rooted participants with authorized reads and resolution tied to the underlying Entry. Do not reuse the obsolete assumption that every authenticated caller can list all conflicts.

## External MCP

An MCP connector mounts an external server's declared tools. Installation, refresh, reauthorization, and source configuration belong to its connector lifecycle. These tools do not establish a peer-agent delegation fabric.

Inbound MCP exposes Integral to external agents; outbound MCP exposes an external server to the resident. Keep credentials, transport origins, and authority distinct in each direction.

### Local stdio credential storage

Set `MCP_STDIO_TOKEN_STORE_ROOT` to a persistent directory writable by the API
user when Core source or installed packages are mounted read-only. The default
remains `backend/.data/mcp-stdio` for existing installations. QuickBooks MCP
writes its rotating refresh-token file here; the child process must see the same
path on every spawn. Files retain mode `0600`, workspace subdirectories and
random filenames; connector cleanup refuses paths outside the configured root.
Changing this root does not migrate previously issued token files. Retain the
old directory until affected connectors have been reauthorized and cleaned up.
Do not commit or log token-file contents.

Business's local source overlay sets `/data/mcp-stdio` on its existing persistent
`/data` volume and provides Node 22, npm/npx and Git for catalog stdio packages.
Rebuild/recreate the API after changing container configuration; OAuth consent
and a real provider tools/list still need qualification in the user's session.

### Cloudflare

The curated Cloudflare entry uses the official Streamable HTTP endpoint at
`https://mcp.cloudflare.com/mcp`. Add it from Settings → Connectors → Add
Connector, then complete Cloudflare sign-in and permission selection. Core
discovers OAuth metadata from the initial authentication challenge; the
catalog's `auth.type: none` means no credential field is required in Integral's
install form, not that the provider is unauthenticated. No tools are admitted
while consent remains pending.

All Cloudflare tools remain subject to Integral's propose/bless approval gate,
including the provider's broad `search` and `execute` surface. This entry
certifies no read-only tools. Choose narrow account permissions before granting
access; do not infer resource limits from a connector label. Installing this
entry does not change an existing custom MCP connection.

For automation, the provider also supports scoped bearer tokens. The existing
Custom MCP form can supply an Authorization header; keep the token out of URLs,
chat, and committed configuration. See the
[official Cloudflare MCP documentation](https://developers.cloudflare.com/agents/model-context-protocol/cloudflare/servers-for-cloudflare/).

Catalog tests qualify the local OAuth handoff and approval classification with
a mocked external handshake. Real account consent, tool discovery, deployment,
and recovery require separate provider qualification.

## Add and qualify one

Register the implementation through its reviewed module path, declare its package/schema dependencies, and use generic hooks where domain transformation is required. Do not hardcode a domain connector into Core services.

Test authentication failure, scope denial, repeat-sync identity, malformed records, conflicts, revocation, cancellation, and external failures. Qualify the actual provider and credentials; registry presence is not proof that every advertised service works in production.

## External chat channel foundation

Inbound chat channels are separate from `AgentChatConnector`, which dispatches
an Integral user's chat turn **out** to an external agent runtime. The inert
[external chat contract](../../backend/app/agentive/connectors/external_chat.py)
defines text events and replies; it does not install, expose, or enable a
connector.

- The trusted host resolves the workspace and exact channel provision, verifies
  the source and sender, and then normalizes the opaque conversation and event
  IDs. A sender identity is not an Integral principal or a permission grant.
- The event key is the exact workspace, connector kind, provision,
  conversation and event tuple. The host must atomically enforce replay
  idempotency: a matching retry returns the existing outcome or in-progress
  status; reuse with a different verified sender or message is a conflict.
- The host, not the event or model, resolves the current published profile
  and authority. A separate route epoch changes on disable/re-enable or
  reassignment, even if the publication revision does not. Replies reference
  only the admitted event. The adapter resolves delivery from that event's
  conversation. The [turn coordinator](../../backend/app/agentive/connectors/web_chat_turn.py)
  checks the current assignment and reservation around replay and completion.
  An approved host must supply real admission and durable storage; the
  coordinator is not mounted and never invokes a model.

The standalone preview is not a live transport. The isolated hosted prototype
used canned replies and kept HMAC material, visitor sessions, and rate counters
in process memory; it did not prove shared serving. For a hosted integration,
first qualify a trusted host with
server-owned route provisioning, shared visitor-session and rate-limit state,
and durable, atomic admission/outcome records across workers. Public profiles
should have no tools by default; qualify any explicitly granted capabilities
against current profile permissions. Then qualify
the hosted page and cross-site embed against the same current publication and
origin policy, including disablement and reassignment invalidation, abuse
limits, and retry behavior. Other channel
adapters supply their own transport verification and identity normalization;
they do not inherit a browser visitor session. No new catalogue entry,
public URL, or production readiness is implied by these contracts.

### Web Chat: qualify the full page first

The first release is an anonymous, text-only full-page chat. Keep the embed,
other channel adapters, attachments, and voice out of this qualification.
Keep setup and sharing disabled when the host cannot serve the channel. A
verified catalogue entry may explain why it is unavailable. Prove the following
on an approved, isolated host before calling the connector usable:

| Check | Expected result |
| --- | --- |
| Owner lifecycle | An authorized manager explicitly configures a disabled connector, selects an eligible published public profile, then enables it. A member cannot perform management actions. |
| Real turn | A new visitor, without an Integral login, receives the selected public profile's real reply. A second turn uses that visitor's history. The public profile has no tools, scaffolding, or access to workspace records for this first release. |
| Isolation | Visitors using the same page cannot read or append to one another's conversation. The host, not browser input, chooses workspace, provision, profile and publication revision. |
| Retry | Losing the response and retrying the same event on another worker yields the original result or in-progress status, not a second model turn. Reusing an event ID for different input is rejected without redispatch; the browser receives a generic unavailable result, not internal conflict details. |
| Disable or change | Disabling the connector or replacing its publication invalidates prior visitor access. Re-read the effective assignment before accepting turns and before exposing a completed reply. |
| Failure and abuse | Rate and message limits work across workers. An unavailable model or expired session yields a recoverable user-facing state without leaking internal exceptions. |

The [full-page component](../../frontend/src/features/public-web-chat/PublicWebChat.tsx)
requires an injected host transport and uses the same assistant-ui external
store runtime as signed-in Integral chat. The connector supplies plain-text
history and keeps a controlled composer so pending or ambiguous sends retry
the original event ID rather than append a new draft. The full page keeps its
composer separate from the scrollable history and only follows new replies
when the visitor is at the bottom. It clears the old conversation on expiry
and is not mounted in the product router.
The unregistered full-page admission adapter checks an already verified visitor
session against the exact workspace, provision, and conversation, then projects
the current public profile revision and independent route generation. It does
not issue visitor credentials or make a host ready.
The unregistered PostgreSQL receipt adapter uses one scalar-keyed Object per
workspace, connector provision, conversation and event. It seals the request
and receipt with the installation storage key and atomically consumes one
reservation fence. An interrupted reservation stays in progress; it does not
trigger another model turn. Product enablement still needs an explicit
recovery, retention and key-rotation policy for these receipts.

The unmounted request service accepts only the visitor's event ID and text.
It resolves the conversation and sender from the trusted session and submits an
exact scoped claim through the injected store. It does not mint sessions, run a
model, send replies, or provide a durable store. A production host still needs
to implement those dependencies and recover unfinished claims safely.

The unregistered [text inference composition](../../backend/app/agentive/services/web_chat_profile_inference.py)
requires an approved injected plain-text model port, a verified current
visitor, the exact published persona, and same-visitor history bounded to
eight completed exchanges and 8,000 characters. It supplies no native
workspace harness, user identity, tools, skills, or credentials to the model.
Only the durable claim owner calls the model; replies require current
authorization and durable receipt readback. Provider selection, isolated
history storage, rate limits, and production model qualification remain
host integration requirements.

The unmounted [browser transport controller](../../backend/app/agentive/services/web_chat_browser_transport.py)
accepts only resume/new or an exact event ID and text. A host must inject
route-bound verified visitor sessions, a shared request budget and scoped
durable history. The response contains only literal messages and presentation
IDs, or a pending/expired/unavailable state. Send stays pending until the exact
completed exchange is present in history and rechecked against current route
authority. A future HTTP host must still enforce origin and body limits,
session credentials and replay policy, and safe transport headers.

Component tests and injected backend dependencies qualify the code paths they
exercise; they are not evidence that provisioning, a live model, or a public
URL works. Once the full page passes, test the embed against the same host and
profile, including approved and rejected origins and browsers that block
third-party storage.

**Settings visibility:** Owners and admins see Web Chat setup in Settings → Connectors only when the server confirms that the
workspace transport is ready. If the settled managed registry has no Web Chat row, after a successful
workspace-authorized status read Connector Library can identify Web Chat as **Unavailable** and explain
that setup requires a ready host. The informational item has no install,
enablement, or sharing action and does not add a **Connected** entry. Failed or
unauthorized status reads do not advertise Web Chat. An admitted managed
entry replaces the legacy notice and setup. The legacy setup dialog still
separates saving a published public profile from enabling the channel. It does
not generate links.

The optional `npm run build:web-chat` from `frontend/` builds a separate
visitor asset and HTML template in `dist-web-chat`. It is not part of the
standard web app build. An approved host must resolve the route and current
publication before rendering `public-web-chat.html` at `/chat/<opaque-route>`.
Supply only the current `route` and public `title` as HTML-escaped
`data-web-chat-route` and `data-web-chat-title` on the root element. Serve the
hashed assets under the template's same-origin `/web-chat-assets/assets/`
prefix. The empty template does not start a visitor session or infer a route
from the address bar. Do not cache the rendered page or relax the separate API
origin and cookie checks.

### Sharing belongs to the same connector

In the Web Chat connector's settings dialog, manage the selected published
profile, readiness and enablement, and the two sharing options together:

- **Full page:** open or copy the server-issued HTTPS link.
- **Embed:** manage exact approved HTTPS site origins and copy the iframe
  snippet. The snippet is available only when the host can enforce the
  configured origin policy. Do not assume third-party cookies are available.

These are two presentations of one connector, not separate installs or
profile bindings. The visitor interface can be reused; full-page cookies must not be reused for
cross-site embed admission. Each visitor session still belongs to one conversation and is
not silently shared across sites or presentations. The public base origin and
opaque channel link come from the trusted host; never derive a public link
from the admin browser URL or put a visitor session token into a link or
snippet. Suppress both sharing actions if the channel is not active. Hiding a
button is not revocation: disablement or an invalid assignment must stop
existing links and sessions on the server. If origins change, stale embed
sessions must not retain the old permissions.

The detached `compose_web_chat_embed` factory composes a separate built iframe
document, exact parent `frame-ancestors`, a one-use seed, and short-lived
memory-only visitor sessions. It rejects ambient cookie credentials. Use a
dedicated embed origin without staff cookies and configure a bounded embed
budget before shipping. Reopening the embedded document starts an empty
conversation; in-frame session rotation is not enabled. Ordinary fetch
metadata is a navigation hint, not proof of the parent or visitor identity.
Do not wire the manager embed share control before the actual host qualifies
the policy, artifacts, isolated admission, budgets, and supported browsers.
Public embed paths never contain seed or session credentials.

A separately enabled **API access** presentation is a follow-up, not required
for the first full-page release. Reuse the same server-owned assignment,
revocation, conversation isolation, and idempotency. The proposed versioned
surface is session creation, message submission, and session history only.
Do not put a long-lived credential in custom browser code: use origin-scoped,
short-lived visitor sessions for browser clients and scoped, revocable
credentials for authorized server-to-server clients. API clients get their own
rate limits and no ambient workspace or tool access. The hosted page does not
imply that this external API has been enabled or delivered.

### Managed channel Settings API

A trusted host may inject a reviewed `ChannelPackageRegistry` into its app at
bootstrap. The management API does not load packages or mount their transports.
Without injection, the catalogue is empty. The authenticated manager sends the
exact workspace in both the `workspace_id` query and `X-Integral-Scope`.
The endpoints under `/api/agentive/managed-channels` return the same scoped
catalogue projection for reads and successful changes:

- `GET /` lists admitted packages. A known uninstalled package can be listed
  even when Add is not ready, but an unknown installation state cannot offer
  Add. Installed unavailable and disabled channels stay manageable.
- `POST /{slug}/install` requires fresh readiness and creates a disabled
  installation. It does not start serving.
- `PUT /{slug}/enabled` uses the exact installed provision and route epoch;
  enabling requires current readiness. Disabling does not.
- `DELETE /{slug}` uses the same provision and route epoch in the query; an
  outage does not block removal.

The owning adapter must atomically compare installation identity and epoch and
enforce readiness at commit where needed. The management API never returns
sharing URLs, credentials, or adapter exceptions.


The unregistered Web Chat package composes this lifecycle with the existing
tool-free published profile check. It requires a host that can attest the exact
provision and epoch, and a durable store that commits provision and profile
revision changes together. The unmounted profile-route store performs that
change with the existing workspace revision CAS. A legacy profile assignment
or enabled flag is not an installation; explicit installation creates a
disabled provision, starting without an assigned profile. The legacy settings
handlers cannot modify installed state. No bootstrap, transport or visitor
endpoint is installed by this composition.


A package can optionally declare the Core-owned `published-public-profile-v1`
setup. For an installed channel this advertises `configuration_kind`, not a
custom form or route. `GET /{slug}/configuration` uses the workspace and exact
provision/epoch, returning only current profile keys/labels and the profile
revision. `PUT /{slug}/configuration` checks the same provision and epoch plus
the expected profile revision and eligibility. Selection is disabled while
the channel is enabled; saving a profile does not enable or publish it. The
owning store checks these conditions again at commit.

The unmounted PostgreSQL visitor-session adapter stores a digest of a random
credential, expiry, and the exact workspace, provision, epoch, and visitor
conversation. Each read rechecks active transport and the same published
tool-free profile. Explicit **New chat** rotates the credential and visitor
conversation with a single compare-and-set; an old or foreign credential
cannot reopen the conversation. Credentials are handed to a future trusted
host separately from browser reply data. Cookie handling, shared request
budgets, cleanup, and PostgreSQL serving qualification remain host work.

Browser history is a bounded read of the same completed encrypted turn receipts,
not a second append or a new inference. The reply and completion marker are
committed by one fenced compare-and-set. It returns up to eight completed
exchanges and 8,000 text characters for the exact verified visitor,
conversation, workspace, provision, publication and route epoch. Results are
displayed in completion order. An exact completed event can be recovered even
if it is outside the recent tail. Unfinished reservations are not history;
older receipt formats keep exact-event replay but are not backfilled by guess.
This reader does not register a host or serve traffic; production needs a
receipt retention policy and query/index qualification at expected volumes.

The detached full-page composition factory requires an explicit trusted route
resolver, exact readiness check, prepared PostgreSQL adapter, rate policy and
bounded tool-free text model. It combines those with visitor sessions, encrypted
turn receipts and bounded history. It re-resolves the same route key and epoch
throughout request admission; a rotated or revoked route does not receive an
in-flight answer. The factory never mounts the router or creates a provision.
The host still owns HTTPS/bootstrap, storage readiness, model timeouts and
limits, retirement/reset policy, and production qualification.
