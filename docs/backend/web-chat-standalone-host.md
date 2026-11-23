# Standalone Web Chat document delivery

The unmounted document router takes the reviewed standalone Vite release
(`npm run build:web-chat`), one explicit canonical HTTPS serving origin,
an approved host readiness function, and the host assembly's current
`page_context` reader. The trusted hosting layer must supply the true
scheme and Host through its reviewed proxy boundary; this router does not
interpret forwarding headers.

The host must preserve the exact `/chat/<opaque-reference>` and
`/web-chat-assets/assets/<approved-filename>` paths. The document serves only
after current route resolution. The asset paths expose only the two allowlisted,
immutable bytes loaded at construction, not arbitrary files or the Vite HTML.
The document includes a same-origin CSP and integrity metadata. A differently
shaped build (extra chunks or top-level assets) is not an approved deployment
artifact until this contract is intentionally updated and verified.

The same-origin visitor API and authenticated share API are separate router
outputs. Nothing mounts automatically, issues public URLs, changes provisioning,
or opens a listener. HTTPS, transport assurance, and explicit mounting remain
owned by the approved deployment.

For qualification, run `test_managed_web_chat_delivery_postgres.py` with the
standard isolated PostgreSQL test environment and point
`INTEGRAL_TEST_WEB_CHAT_DIST` at the built artifact. The built-document variant
navigates the manager's issued URL over detached ASGI, verifies the standalone
mount and exact asset bytes, then verifies the alternate origin and disabled
route are not served. The API-only variant remains available without assets.
This does not launch a browser engine or prove a production mount.

The same contract file includes a test-only two-origin HTTPS ASGI dispatcher.
It exercises signed-in management on a staff hostname and a signed-out
visitor reply on a separately approved hostname. It checks that neither host
serves the other's routes, that cross-origin visitor requests do not get CORS
grants, and that the visitor cookie is host-only and scoped to one route.
Use both the full-page and embed artifact variables for this fixture. This is
synthetic, socket-free web routing, not a live deployment recipe: it does not
install DNS, TLS, proxy rules or environment configuration, and it does not
test browser cookie enforcement or a live inference provider.

## Disposable loopback TLS proof

From a clean test checkout with Core's test dependencies, the frontend build
dependencies, PostgreSQL utilities and OpenSSL, run:

```sh
PREVIEW_SECONDS=120 bash backend/scripts/run_disposable_web_chat_tls.sh
```

The fixture builds the real standalone assets, creates a brand-new synthetic
PostgreSQL store, runs the authenticated staff setup in ASGI without a staff
listener, and exposes just the reviewed visitor route group on 127.0.0.1.
The printed URL is a disposable route for a tool-free synthetic profile and
deterministic model adapter. The listener and database stop on exit. No
normal startup environment variable, live provider, public listener,
system trust change or production configuration is involved.

With the default synthetic certificate, use the printed certificate and
visitor URL. For example, setting CERT and URL to those values:

```sh
curl --noproxy '*' --cacert "$CERT" \
  --resolve conversation.example.invalid:58173:127.0.0.1 "$URL"
```

This explicitly pins trust in the command and does not weaken verification.
Do not use insecure mode or click past a browser warning. For a browser run,
supply PREVIEW_HOST and absolute PREVIEW_CERT/PREVIEW_KEY paths **only** when
that exact hostname already routes to loopback and its TLS certificate is
already trusted by the browser. Use a certificate approved for the isolated
fixture, not staff or production key material. The runner does not change
name resolution or install trust. An absent approved browser hostname/trust
chain leaves browser enforcement pending.

## Connector registration and profile identity

The Web Chat extension owns the public document, session and turn adapters as
well as its manager configuration. Core registers the validated package once
and returns explicit mount candidates. With no serving configuration, the
connector still installs and opens Configure; it contributes no visitor,
embed, client API or sharing routes. A hosting operator installs the approved
mounts and readiness inputs. A workspace manager does not install a second
connector or grant public traffic from the configuration dialog alone.

A public profile can exist without a Web Chat service identity. A profile
manager can explicitly set up that profile's non-human identity when publishing
an eligible tool-free revision. Existing profiles do not acquire identities or
permissions during reads, upgrades or connector installation. This identity
has no staff login, workspace membership, tools or workspace resource grants.
It is scoped to the exact workspace, profile and publication, and is distinct
from browser visitor and API client credentials. Only readiness, not its
identifier, is returned to the manager.

A valid identity is necessary but not sufficient for Web Chat serving. The
host, connector assignment, publication and transport must all remain current.
If changing the identity of the profile used by an enabled Web Chat, disable
that route first; future serving still requires explicit activation and a fresh
authorized share. Per-app and per-track data grants are a separate feature,
not part of the text-only release.

### Full-page lifecycle reconciliation

An explicitly composed serving host reconciles the exact committed Configure
assignment or enablement. Assigning an already published profile can prepare a
disabled route only after the platform approves that exact state. It does not
publish a profile, enable the connector, or make a visitor page or share
available. Enable still requires pre-enable readiness; its new epoch must
separately have current host approval and serving readiness before it accepts
traffic. Disable and removal retire prior references without issuing new ones.

The post-commit attempt is bounded and does not roll back the saved settings.
A route missing ready approval remains non-serving, even when enabled in
settings. Read-only Configure, share checks, and visitor requests never repair
or issue references. After checking current state and obtaining the required
approval, the hosting operator can explicitly run the private host
`reconcile(provision)` operation for the current state; retrying an old
manager mutation is not recovery. A concurrent assignment, publication,
disable or removal cannot make a stale reference serve.

## Partner-site embed policy and credentials

The detached embed policy pins the exact provision, route epoch, published
profile, embed policy revision, parent HTTPS origin, and distinct embed HTTPS
origin plus the dedicated `/embed/<opaque-reference>` path. It checks a fresh
matching host attestation. The policy alone emits no document, link, share
proof, session, cookie, or token. The separate document adapter enforces the
exact approved parent in `frame-ancestors`.

The full-page Vite client is not reused for embed: it currently uses the
same-origin full-page cookie session. A production embed requires a distinct
credential and storage design that does not rely on third-party cookies, fresh
parent-origin/assignment admission, revocation and browser qualification. The
parent is not an Integral principal; no parent-provided identity is admitted.
Full-page anti-framing remains unchanged.

The detached embed credential store can now issue a short-lived seed to trusted
host code, atomically exchange it once, and verify the resulting short-lived
embed session. Wire values are `iweb_` or `iwes_` followed by 86 base64url
characters, without a delimiter. Persistence contains only hashes of the
credential and exact policy scope and a hashed selector record key. Seeds live
at most 60 seconds; sessions live at most 300 seconds, without refresh. Embedded
visitor/conversation IDs are independent of full-page context. The detached
composition below invokes the store; Core startup does not mount it. A hosting
operator still needs to qualify retention, quota policy and browser behavior.

## Separate embed build

Build `npm run build:web-chat-embed` independently of the full-page build and
the signed-in application. Review the two hashed outputs under
`/web-chat-embed-assets/assets/`. The approved distinct HTTPS host composes
`compose_web_chat_embed` with trusted current per-reference policy, explicit
host readiness, bounded public database/model capacity, and a separate budget
for document opens, seed exchanges, history reads, and sends. Its document
router sends only the current embed's `frame-ancestors` allow-list and one
short-lived seed in the uncacheable iframe HTML. Do not log response bodies or
Authorization. Do not share this origin with staff cookies or loosen the
full-page anti-framing rules. Visitors reopen the embed for a new session;
an expired session cannot restore earlier history.

Run `test_web_chat_embed_composition_postgres.py` on a disposable PostgreSQL
store with `INTEGRAL_TEST_WEB_CHAT_EMBED_DIST` pointing to the reviewed build.
It qualifies artifact delivery, atomic bootstrap, no cookie dependence,
independent sessions, idempotent text turns, and policy revocation mid-reply.
It does not prove browser enforcement, live provisioning, or serving
capacity. Confirm the real HTTPS host's headers in supported browsers,
including an unauthorized parent refusal and an authorized parent with
third-party cookies blocked. This does not authorize enabling a connector,
issuing a production link, or adding data or tools to the public profile.

## Approved manager sharing read

A separate detached manager route
`GET /api/agentive/managed-channels/public_web_chat/embed-share` can read an
already-issued embed link only. It takes the exact full-page scope query plus
`embed_policy_revision` and canonical `parent_origin`. The authenticated
manager and current host policy are rechecked before the no-store
`approved-web-chat-embed-v1` proof returns. It neither issues the reference nor
returns an embed credential, full-page link, or general configuration.
An eventual approved host must explicitly mount the read with normal API
authentication and trusted current embed readiness/share providers.

## Release evidence and gates

The disposable PostgreSQL contracts use a synthetic text-only model. A
successful run is evidence of transport and persistence behavior, not evidence
of installation in the active demo, browser enforcement, or live model
capacity. Use these evidence levels when handing off a change:

| Path | Detached qualification | Still required on an approved host |
| --- | --- | --- |
| Full-page link | `test_managed_web_chat_delivery_postgres.py` with its built document; `test_full_page_web_chat_composition_postgres.py` for real persistence and turns | An authorized manager retrieves the exact share URL after readiness, and a signed-out browser receives a real reply and can continue its own conversation. Check alternate origins and disable/reassignment. |
| Embed | `test_web_chat_embed_composition_postgres.py` with its separate built document | The same reviewed configuration serves only the authorized parent. Verify a real turn with third-party cookies blocked, refusal from a different parent, and no usable old session after a policy change. |
| Server-to-server | `test_web_chat_client_composition_postgres.py` for separate grant/session credentials, same-event retries, and revocation before a reply | An authorized manager can issue and revoke an exact scoped key; an approved non-browser client exchanges it, sends a real turn, and cannot continue after revocation. Display the key only at issuance and never in an embed or link. |

Before any public traffic, review where anonymous traffic enters relative to
signed-in Integral. Bound requests at the edge before route resolution,
enforce request and model deadlines, and reserve separate capacity for staff
in the worker, database connection, and model queues. Measure staff latency
and error rate while the public path is at its configured limits, including
concurrent retries, slow model responses, and a stalled dependency. An API
budget reached after lookup and a bounded model adapter are not by themselves
proof that staff capacity is protected. Do not send browser frames, copied
snippets, or key material to logs. Keep the release text-only and tool-free;
a healthy transport is not proof of public resource authorization.

For grant maintenance, an authorized trusted operator with an exact reviewed
revocation may inspect metadata for a known grant ID even if its former route
does not serve. The inspection reports retirement and assignment currentness,
does not grant admission, and never returns credentials or digests. It is not
an HTTP route. A lost or uncertain issuance response must not disclose or
regenerate the original bearer. A future operator flow should inspect and
revoke the exact reviewed grant, then obtain new review for a fresh grant ID
before reissue. This does not guarantee the absence of active orphan grants
after lost delivery; that requires a separate delivery/activation protocol.

Configure may project `approved_embed_policies` only when an optional
mounted embed-share reader and an explicitly trusted descriptor port can
verify currently issued policy for the exact enabled workspace, provision,
epoch and publication. Each bounded descriptor is only a canonical approved
`parent_origin` and `embed_policy_revision`. This is not a policy editor,
share link, or visitor credential. The selected Check request independently
revalidates the policy and manager.
