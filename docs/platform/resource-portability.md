# Portable Apps and Tracks

Manage Apps and Manage Tracks share an **Import / export** dialog. Users can
select resources, then download a versioned `.integral.zip` archive. Import
previews the archive before creating new private copies in the current
workspace. From inside an App, Track imports stay inside that App.
Declarative name collisions receive an `(import N)` suffix. A packaged App already
installed in the destination produces a conflict; import never updates that App.

In the export tab, choose what to include and select the Apps or Tracks to
download. The search field filters the items currently loaded; use **Load more**
to browse additional pages. Selections remain checked across pages. In Manage
Apps, **Import template** is a separate action for adding a library template;
**Import / export Apps** transfers installed Apps and their contents.

**Apps:** up to 100 may be selected together. Packaged Apps (tools, operations,
agents, query handlers, protected state) include their admitted runtime package
under `package/<slug>/` so import can install working copies. Select related
Apps together to preserve cross-App links. **Tracks:** up to 100 may be selected
together; Track archives remain data-only.

## Contents

| Mode | Contents |
| --- | --- |
| Configuration only | App/Track structure, descriptions and visual identity, record types and field definitions, tags and hierarchy, saved Track views, App dashboards and Home composition |
| Configuration and data | The configuration above, plus Entries, bodies, custom field values, tags, typed record relationships, file-field bindings, URL attachments and binary attachment files |

This is content portability, not a deployment or identity backup. Accounts,
sharing grants, invitations, credentials, secret App settings, reactions,
comments, audit history, chat history, generated-document execution receipts and
derived search indexes are not copied. Imported records belong to the importing
principal; source identity strings never grant authority. Search indexing and
attachment metadata extraction run afresh after import.

### Archive versions

| Version | When | Payload |
| --- | --- | --- |
| `1` | Declarative Apps/Tracks without runtime extensions | `integral.json` + optional `files/` attachments |
| `2` | One or more packaged Apps (optionally mixed with declarative Apps) | Version 1 content plus `packages[]` (legacy singular `package` still accepted) and `package/<slug>/` members copied from each source App's admitted `bundle_dir`. Each packaged App carries `package_slug`. |

Version 1 still refuses runtime declarations (`tools`, `operations`, `agents`,
`protected_state`, hooks/plugins, custom skills, queries with `handler_ref`)
rather than silently dropping behavior. Version 2 retains those declarations
only on Apps that bind an exact host-admitted package via `package_slug`.
Ordinary v1 exports retain the original strict wire shape: no package or settings
fields are added. V2 includes only declared non-secret settings; `writeOnly` and
password fields are omitted recursively, as are undeclared settings.

### Writable package root (required for packaged import)

Packaged App import needs a **writable** host package root. Set one of:

- `INTEGRAL_PACKAGE_PATHS=<dir>` — comma-separated parents; each App lands as
  `<dir>/<slug>/` with `operational-model.yaml` (any package slug from the
  archive — not App-specific).
- `integral up --apps <dir>` — same idea for managed local installs.

When the destination already has one visible library package for a slug,
restore binds to that artifact and never rewrites it. When none is admitted,
import materializes `package/<slug>/` onto that writable root and seeds the
library catalog, then installs Apps against that admission.

Without a writable root (common under Docker Compose when the image package
tree is read-only and `INTEGRAL_PACKAGE_PATHS` is unset), import fails with:

`Import requires a writable host package path (configure INTEGRAL_PACKAGE_PATHS or integral up --apps)`

**Docker:** point the env at a path inside the API container that sits on a
writable volume, for example `INTEGRAL_PACKAGE_PATHS=/data/integral-apps` when
the API mounts `/data`. Create the directory once (`mkdir -p` in the container)
and restart `api` so the process loads the env. Host-only folders that are not
mounted into the container do not count.

Archive-driven admission is not gated by `INTEGRAL_CORE_ONLY` (ambient library
discovery on boot still is). Ambiguous admissions or a host package with a
different fingerprint fail closed without overwriting shared host files.
Package fingerprints ignore the same local cache/VCS junk for install, admit,
and export (for example `__pycache__`, `.pytest_cache`, `.DS_Store`), so source
checkouts remain exportable. Signature and trust checks still apply after
materialize. Run immutable signed Python packages with
`PYTHONDONTWRITEBYTECODE=1` so runtime imports do not add bytes to the signed
artifact.

Apps with protected state import through Core portable restore: reserved import
finalization may rewrite protected fields. Ordinary creates and edits outside
that path still fail closed, and a portable archive never grants an App
operation permit. Packaged Apps still use canonical installation, entitlements
and dependency checks. Early single-App v2 archives using singular `package`
remain readable.

Include all related Tracks in the same full-data App selection when records
cross Tracks. Select related Apps together so cross-App bindings and packaged
`requires_apps` dependencies remount in one import.

On **App** full-data export, relation and file field values that point at
**deleted** (unresolvable) targets are dropped from the archive so stale links
do not block the download. If clearing a link would leave a **required**
relation/file empty, that record is **omitted** from the ZIP and reported via
`X-Integral-Skipped-Entry-Count` / `X-Integral-Skipped-Entries` (and the export
UI toast). Live targets outside the selected Apps still fail export with a
message that names the record, field, and the other App/Track to include (or
clear the link in the UI first). On import, rows that still lack a remappable
required relation/file are **skipped** and reported as `skipped_entry_count` /
`skipped_entries` so the rest of the archive can install.

### New host / new folder

1. **Source:** Manage Apps → Export (select related Apps; configuration or full) →
   download `.integral.zip`. Packaged Apps carry `package/<slug>/` for every
   selected packaged App.
2. **Destination:** install/run Core with a **writable package root**
   (`INTEGRAL_PACKAGE_PATHS` or `integral up --apps` — see above; required before
   packaged import). Sign in → Manage Apps → Import → preview → import. Missing
   packages for **any** archive slug are admitted onto that root from the ZIP.
3. **Result:** new private App copies in that workspace; runtime uses the admitted
   package (pre-existing match or newly materialized). Sharing, credentials, and
   history are not copied. You need App-create permission and package trust still
   applies. Copying only an agent `integral-apps/` folder does not move workspace
   records.

## Transport and validation

The shared authenticated endpoints are:

- `POST /api/portable/export`: JSON `{kind: "app" | "track", ids: string[], mode: "configuration" | "full"}`; returns a ZIP with `Cache-Control: no-store`.
- `POST /api/portable/preview`: raw ZIP body; validates framing, schemas, identities,
  resource/reference closure, attachment checksums and (for v2) package
  fingerprint; returns names, counts, optional `packages: [{ slug, version, installs_package }, ...]`
  and singular `package` when exactly one payload is present (legacy UI).
- `POST /api/portable/import`: the same raw ZIP body; optional `?app_id=...` is a
  Track-only destination (not valid for packaged App archives). The server
  validates its workspace and current authority.

All endpoints bind the authenticated workspace through `X-Integral-Scope`.
Export admits every App, Track and Entry through current canonical policy and
containment checks. An excluded child fails the full export; it is never returned
as a seemingly complete partial backup. Configuration-only exports do not read
Entry bodies or attachment files.

Archives contain `integral.json` (`format: integral-portable`, `version: 1` or
`2`) and binary members under `files/` and, for v2, one or more `package/<slug>/`
trees. No member is extracted to an arbitrary path. Both compressed and expanded
content are capped at 50 MiB, with at most 4,096 members and 10,000
configuration/content identities. Duplicate IDs, ZIP members, JSON keys, invalid
paths, links, encrypted members, non-finite numbers, live out-of-selection
record references, missing or mismatched attachment files and package
fingerprint mismatches are rejected. Large selections must be split into
smaller groups. Export names a single App `{AppName}-{mode}.integral.zip` and
multiple Apps `integral-apps-{mode}.integral.zip`.

## Restore and invariants

**Version 1:** restore authorizes creation in the destination workspace, creates
private App and Track roots through the canonical services, then materializes
their configuration. Every new Node is attached through its structural edge in
the same unit of work (I-GRAPH-01). View creation uses the canonical Track/View
catalog helper.

**Version 2 (packaged Apps):** resolve all packages read-only, check destination
conflicts, settings, protected state, dependencies and entitlements, then install
fresh Apps in dependency order. Installed App names and package aliases participate
in ordering. Runtime registration failure aborts restore. Match packaged Tracks
to installed Tracks by stable key, restore tags and presentation overrides,
then Entries/attachments, Home composition and dashboards. Shared template uses
export independent configuration snapshots with unique archive identities.
Missing required settings fail before installation; completing an existing App's
settings does not authorize importing over it.

Skipped import records are named in the response. Counts and storage charges
include only retained records and attachments. Compensation removes generated
thumbnails as well as original files and clears attempt-owned runtime registrations.

Record identities are reserved in a first pass beneath their new Tracks. A
task-local, one-use reservation lets the **normal entry creator** finalize each
record in a second pass. This supports forward and cyclic relationships while
retaining schema and taxonomy validation, current relation-target access checks,
protected-field guards, hooks and ordinary creation events. The public create
API has no caller-selected identity option. Nested hook creates cannot consume
another record's reservation.

PostgreSQL wraps graph restore in a transaction. File writes are compensated on
failure. Single-process development stores serialize the operation and remove
only its newly created owned subtree on failure; they do not claim transaction
isolation. Existing resources and author/grant relationships are never traversed
for cleanup. These boundaries preserve I-CRUD-01, I-ROLE-01, I-SUBSTRATE-01 and
I-EXT-01 without adding domain code to Core.

`backend/tests/test_portable_archive.py` exercises archive framing and runtime
refusal. `backend/tests/test_portable_transfer.py` exercises actual resource and
HTTP round trips. Packaged App coverage lives in
`backend/tests/test_portable_package_transfer.py`. The shared dialog has UI tests
for selection, paging, scope changes, import preview and contextual Track
placement.
