# Portable Apps and Tracks

Manage Apps and Manage Tracks share an **Import / export** dialog. Users can
select resources, then download a versioned `.integral.zip` archive. Import
previews the archive before creating new private copies in the current
workspace. From inside an App, Track imports stay inside that App.
Name collisions receive an `(import N)` suffix; imports never overwrite a resource.

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
sharing grants, invitations, credentials, App settings values, reactions,
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
only on Apps that bind a verified package via `package_slug`. Import admits
every package through the same library seed and trust gates as Install from
library (`verify_bundle_signature` / `assert_library_artifact_trusted`), orders
Apps by hard `requires_apps` within the archive, calls `install_app` for each
packaged App (or blank-creates declarative Apps), then overlays
tracks/records/Home/dashboards with a shared identity map. Package bytes are
written under the primary package root as `<slug>/` so they remain discoverable
after restart. A portable ZIP never `sys.path`-injects code without that
admission path. Early single-App v2 archives that only set singular `package`
still import; the server normalizes them to `packages` and binds `package_slug`.

Include all related Tracks in the same full-data App selection when records
cross Tracks. Select related Apps together so cross-App bindings and packaged
`requires_apps` dependencies remount in one import.

On **App** full-data export, relation and file field values that point at
**deleted** (unresolvable) targets are dropped from the archive so stale links
do not block the download. If clearing a link would leave a **required**
relation/file empty, that whole record is **omitted** from the ZIP (and records
that required it may cascade out). Live targets outside the selected Apps still
fail export with a message that names the record, field, and the other App/Track
to include (or clear the link in the UI first). On import, rows that still lack a
remappable required relation/file are **skipped** and reported as
`skipped_entry_count` / `skipped_entries` so the rest of the archive can install.

### New host / new folder

1. **Source:** Manage Apps → Export (select related Apps; configuration or full) →
   download `.integral.zip`. Packaged Apps carry `package/<slug>/`.
2. **Destination:** install/run Core in the new location (`integral up`), sign in,
   open the workspace → Manage Apps → Import → preview → install/import.
3. **Result:** new private App copies in that workspace; runtime is admitted from
   the ZIP. Sharing, credentials, and history are not copied. You need App-create
   permission and package trust still applies. Copying only an agent `integral-apps/`
   folder does not move workspace records.

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

**Version 2 (packaged Apps):** admit every `packages[]` payload to the library
catalog, topologically order Apps by hard `requires_apps` within the archive,
then for each App either run `install_app` (entitlements, `requires_apps`,
`register_bundle_on_install`, protected state) when `package_slug` is set or
blank-create a declarative App. Match packaged Tracks to installed Tracks by
`relation_type_key` / title, create any extra Tracks from the archive, restore
Entries/attachments with a shared identity map, and apply Home composition and
dashboards. Packages that stop at `awaiting_settings` must finish library
install settings before a data import can complete.

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
