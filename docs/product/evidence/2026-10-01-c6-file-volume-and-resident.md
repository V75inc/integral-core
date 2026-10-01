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

The local browser used the exact da33 API image and the prior d497 web image;
da33 did not change frontend source. The separate registry runner built both
images from da33 and supplied digest-pull deployment evidence. The
broader A01–A15 denial, lifecycle, concurrency, recovery, and human journey
matrix has not been fully reconciled on this source revision. The older
`bb3b1e0` table is historical. Independent architecture and Product Owner
decisions remain pending. C6 is not complete, and no production release is
asserted.
