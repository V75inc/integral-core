# C6 file-volume repair and resident qualification

**Source revision:** `da33c68f1bcdbf0e3191203b1f631bae21ffd84a` (PR #97)
**Date:** 2026-10-01 UTC
**Disposition:** selected technical journeys pass; C6 remains incomplete.

## Root cause and repair

An attachment upload against the deployed API returned 500. The API ran as
UID 10001, but its default `/app/.files` path was not writable. The Compose
volume mounted `/app/integral_data`, which did not contain those files. A
failed upload also left a detached Attachment node because the node was
created before file storage initialization. The one disposable orphan from
that attempt was removed after diagnosis.

The runtime now sets `JVSPATIAL_FILES_ROOT_PATH=/data/files`, creates that
directory with UID 10001 ownership, and mounts the persistent Compose volume
at `/data`. Entry, chat, and chunked attachment uploads initialize storage
before creating an Attachment node. The image qualification workflow mounts
a fresh file volume, writes through the exact API image, and reads the file
back from a separate container.

## Candidate identity and gates

| Artifact | Identity |
| --- | --- |
| Core wheel | `integral_core-0.1.1rc11-py3-none-any.whl`, SHA-256 `1b01987bd561c3a5f111c1f9b233924b384f47c131aab60a6e6936db79dd25b2` |
| SDK wheel | `integral_sdk-0.2.0-py3-none-any.whl`, SHA-256 `b32a763405825d2fcf081485930ace54e8cbf663366abefbb8e7d89bb013444f` |
| Signed, extracted Asset Register archive | SHA-256 `f49cdac85266f02be9f3a2f2e65517cec519ddad1564763934be741f6bff0fcc` |
| Local API image | `sha256:9ea9030ba824b73e6d2ab8686428611fafd212986303ed4f31391580840f3810`, labeled with the source revision |
| GHCR API image | `ghcr.io/v75inc/integral-core-qualification-api@sha256:1b013390e32786b5d5969955b14cef4a08b75a304f894cee0eead369794bd031` |
| GHCR web image | `ghcr.io/v75inc/integral-core-qualification-web@sha256:9d48e5024997bea85402e5dbe1f87c2b8407c1caf5136cc97890ac71c9cbf625` |

`make verify` passed on the staged source, including pre-commit guards,
format/lint, types, CI-faithful smoke, the frontend suite, and the full
backend suite. Fresh-worker Postgres `make test-postgres` passed. Core-only,
contract, clean-install, SDK artifact, and external Asset Register artifact
gates passed. Focused attachment tests covered entry, chat, and chunked
storage initialization. PR #97 backend, frontend, independent-artifact, and
Postgres CI checks passed on this revision; the ordinary Docker CI job was
skipped. The separate [registry qualification run 36802954970](https://github.com/V75inc/integral-core/actions/runs/36802954970)
passed publication and clean digest-pull deployment on a fresh runner with
separate Postgres and file volumes. Its file probe survived a separate
container readback.

## Populated backup and independent restore

On the exact local image, authenticated upload of a 60-byte synthetic file
created `n.Attachment.24693fd76a3248fc901d7359`, structurally attached
to entry `n.Entry.475a5791e2b84b27a67fc085`. Authenticated download and
the stored file each matched SHA-256
`2f489fa034d095d573323e0c4c8c5819c25ede11083fcc2066d7f38d5a21f878`.
The API was stopped for a consistent PostgreSQL dump and file-volume tar:

| Backup | SHA-256 |
| --- | --- |
| `da33-populated-with-attachment.dump` | `c61b79aeb105588fb3aa7246e2c8c6b743d17285d00b2ac58988dce1de0e80d1` |
| `da33-files.tar` | `7fa28201214a6a3ae3b28934405d379a309b3aca7a313472e87217e486933b01` |

Those backups were restored into a separate scratch database and file volume.
A new API container from the same image and revision became healthy. Its
authenticated attachment download matched the original byte hash. Its
governed HTTP and MCP Asset Register queries returned the same nine rows,
including the resident-created asset; unauthenticated MCP returned 401.
Replaying the original resident operation through HTTP and MCP returned the
same durable receipt and entry. This is a selected populated restore drill,
not proof of every A14 fixture or recovery mode. Local backup files and
readback logs are retained under `/private/tmp/integral-c6-d497-artifacts/`
and `.qualification-evidence/da33-full-restore-readback.log`.

## Exact-candidate resident, browser, and transport journey

The signed-in browser used synthetic workspace
`n.Workspace.939d23f26bcd4dcdbfaed02e` and installed App
`n.WorkspaceApp.434da202d8e3424999c69460`. GPT-4.1 was asked to register
tag `C6-DA33-001`, title `C6 da33 Resident Asset`, category
`it_equipment`. The first `integral_invoke_app_operation` call lacked an
idempotency key and returned `bad_request` without an effect. It corrected
the call in the same turn and succeeded through the declared `register_asset`
operation in 8.2 seconds. Run ID:
`d8bf83fd-9457-4575-9140-471570cc7a48`; durable receipt:
`o.OperationExecutionReceipt.965c5301e99e32d5ab4113b252347601009b151a17cfcc68c0b3eac4b9d64863`;
new entry: `n.Entry.8c510dad93924e18b28b987e`. The Track Feed visibly
showed ten entries with the new title, tag, and category. Track and view
**Improve this** controls were icon-only with accessible names and tooltips.

After that write, authenticated HTTP and MCP `available_assets` queries
returned the same ten rows, object references, and applied workspace scope;
the new asset was present. Unauthenticated MCP returned 401. HTTP and MCP
replay of idempotency key `c6-da33-001-register` each returned
`replayed=true`, the original receipt, and the same entry without a second
effect. The browser write required one corrected tool call; it was not a
first-attempt success.

## Qualification boundary

### A03 module boundary guard

On 2026-10-01, `.ci/module_boundary_check.sh` was run against this source
tree. Its reviewed allowlist permits `app.contracts`, `app.modules`,
`app.schemas`, and the single legacy service `app.services.policy_engine`.
The guard rejected a temporary `backend/app/modules/` module importing
`app.models` with exit 1 and `forbidden module import app.models`; after the
probe was removed, the same guard passed with exit 0 and
`module-boundary: OK`. The guard is included in `make guards` and the
pre-commit hook. This proves the tested forbidden import fails the local
build guard on the candidate; it does not replace review of every module
boundary or the broader C6 matrix.

### Ordinary Core without a global model provider

On 2026-10-01, the same local da33 API container reported no
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GOOGLE_API_KEY` environment value,
and `INTEGRAL_CORE_ONLY=1`. Its image revision label and digest matched the
candidate identity above. The d497 web image served the unchanged frontend
bytes through `http://127.0.0.1:19100`; its `/api/` reverse proxy reached
the da33 API container.

In that browser deployment, a new synthetic user signed up, skipped optional
email verification, and opened the empty Mission Control and Apps surfaces.
The user created blank App `n.WorkspaceApp.73a0b3af436f4b90a1f1a9ab`,
default-model Track `n.Track.64f3e65e389645cf82bedb23`, and Post
`n.Entry.1f4081da3af8498ebaf59ab3`. The Track Feed showed the new title
and body. After a page navigation/reload, the same entry was still present.
The user signed out and signed in again; the entry remained visible. An
authenticated `GET /api/entries/n.Entry.1f4081da3af8498ebaf59ab3` through
the same-origin proxy, with the active `X-Integral-Scope: ws:…` header,
returned 200 and the stored title and body. This is a passing selected A02
ordinary-use journey without a global provider; it does not exercise an
agent turn or prove every A02/A01 journey on a fresh installation.

The host also had a separate Python server bound to IPv4
`127.0.0.1:4000`, while Docker published the qualification API on port 4000
through a different listener. Direct host IPv4 requests to that port reached
the separate server and rejected this synthetic account. Those 401 responses
are not attributed to the da33 container or counted as an A02 failure. The
browser and successful API readback used port 19100 and its verified proxy.

The local browser used the exact da33 API image and the prior d497 web image;
da33 did not change frontend source. The separate registry runner built both
images from da33 and supplied digest-pull deployment evidence. The
broader A01–A15 denial, lifecycle, concurrency, recovery, and human journey
matrix has not been fully reconciled on this source revision. The older
`bb3b1e0` table is historical. Independent architecture and Product Owner
decisions remain pending. C6 is not complete, and no production release is
asserted.
