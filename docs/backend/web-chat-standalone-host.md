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

## Partner-site embedding is a separate, non-serving contract

The detached embed policy pins the exact provision, route epoch, published
profile, embed policy revision, parent HTTPS origin, and distinct embed HTTPS
origin plus the dedicated `/embed/<opaque-reference>` path. It checks a fresh
matching host attestation; it emits no document, link, share proof, session,
cookie, or token, and it is not a mounted endpoint. An eventual live embed
page must enforce the exact approved parent in `frame-ancestors`.

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
visitor/conversation IDs are independent of full-page context. No route invokes
these methods yet. HTTP delivery still needs a vetted origin-bound bootstrap,
no-storage/no-cookie transport, issuance and request quotas, retention, and
browser qualification. Until then, no embed share or admission is exposed.
