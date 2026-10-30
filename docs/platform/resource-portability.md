# Portable Apps and Tracks

Manage Apps and Manage Tracks share an **Import / export** dialog. Users can
select one or up to 100 resources, then download a versioned `.integral.zip`
archive. Import previews the archive before creating new private copies in the
current workspace. From inside an App, Track imports stay inside that App.
Name collisions receive an `(import N)` suffix; imports never overwrite a resource.

In the export tab, choose what to include and select the Apps or Tracks to
download. The search field filters the items currently loaded; use **Load more**
to browse additional pages. Selections remain checked across pages. In Manage
Apps, **Import template** is a separate action for adding a library template;
**Import / export Apps** transfers installed Apps and their contents.

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

Executable extensions, guarded operations and their dependent configurations
must retain their installed-package admission and domain migration contracts.
The portable path refuses those configurations rather than silently dropping
their behavior or importing executable code as trusted. Use the original signed
package and its migration tools for those Apps. A portable ZIP cannot override
protected App state or confer package trust.

Include all related Apps or Tracks in the same full-data selection. A reference
outside the selection causes a clear error, rather than retaining a source-workspace
binding or silently dropping it. Cross-App bindings use an App export with all
related Apps selected; their restored bindings pin the new instances even when
name collisions require renaming. Arbitrary text equal to an old record ID remains
text. Typed relationship and file fields receive new destination identities.

## Transport and validation

The shared authenticated endpoints are:

- `POST /api/portable/export`: JSON `{kind: "app" | "track", ids: string[], mode: "configuration" | "full"}`; returns a ZIP with `Cache-Control: no-store`.
- `POST /api/portable/preview`: raw ZIP body; validates framing, schemas, identities,
  resource/reference closure and attachment checksums; returns names and counts.
- `POST /api/portable/import`: the same raw ZIP body; optional `?app_id=...` is a
  Track-only destination. The server validates its workspace and current authority.

All endpoints bind the authenticated workspace through `X-Integral-Scope`.
Export admits every App, Track and Entry through current canonical policy and
containment checks. An excluded child fails the full export; it is never returned
as a seemingly complete partial backup. Configuration-only exports do not read
Entry bodies or attachment files.

Archives contain `integral.json` (`format: integral-portable`, `version: 1`)
and binary members under `files/`. No member is extracted to an arbitrary path.
Both compressed and expanded content are capped at 50 MiB, with at most 4,096
members and 10,000 configuration/content identities. Duplicate IDs, ZIP members,
JSON keys, invalid paths, links, encrypted members, non-finite numbers, dangling
references and missing or mismatched attachment files are rejected. Large
selections must be split into smaller groups.

## Restore and invariants

Restore authorizes creation in the destination workspace, creates private App and
Track roots through the canonical services, then materializes their configuration.
Every new Node is attached through its structural edge in the same unit of work
(I-GRAPH-01). View creation uses the canonical Track/View catalog helper.

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

`backend/tests/test_portable_archive.py` exercises archive framing.
`backend/tests/test_portable_transfer.py` exercises actual resource and HTTP
round trips, cyclic and cross-Track relations, attachment bytes, tags/views,
workspace boundaries, exclusions and failed-restore cleanup. The shared dialog
has UI tests for multi-selection, paging, scope changes, import preview and
contextual Track placement.
