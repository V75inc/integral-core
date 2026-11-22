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
