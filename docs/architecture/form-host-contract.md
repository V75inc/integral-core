# App-owned form behavior

Core renders generic form schema. It must not infer workflow semantics from App names,
field keys, statuses, or business vocabulary. Publishing Apps own authorization,
validation, migration and domain decisions. UI configuration does not replace server checks.

## Explicit workflow presentation

`public_share_extensions` contains optional scalar string keys:

- `form_submit_field`, `form_submit_value`: patch on submit; omit both for no implicit state change.
- `form_lock_field`, `form_lock_values`: comma-separated read-only values; omit both for no inferred locking. Server `locked` remains authoritative.
- `form_review_enabled=true`, `form_review_status_field`, `form_review_accept_value`, `form_review_reject_value`: opt into the host `DocumentReviewHost`. Core's default host is unavailable, never an approval implementation.
- `form_acknowledgments_enabled=true`, `form_acknowledgments_field`: opt into explicit document acknowledgment list.

Incomplete declarations fail closed. Enabled acknowledgments require the response to contain
`acknowledgment_documents`; no list is not equivalent to an empty list. Items carry `id`,
`title`, optional `description`, `required`, `document_url`, `filename`. Hosts resolve URLs
and enforce authenticated document access. Core renders plain text and safe links.

Apps migrate historical installed schemas and stored states before removal of compatibility
logic. App contract review actions and document APIs are not provided by Core.

## First materialization provenance

A fresh package install can record a versioned historical receipt on its
initial Application Definition. This receipt records exact Track and EntryType
IDs from the creation branches, after checking their graph containment and
the initial definition's canonical keys. It excludes generic creation,
template clones, reused or upgraded Apps, and legacy name-based adoption.
Absence is unknown, not evidence that a Track is unmanaged.

The receipt describes the original materialization and grants nothing. It
does not qualify an installed store or supply current public policy. Before
using it for policy, qualify current bindings, define how moves, detachments
and definition revisions invalidate them, and separately reconcile older Apps.
Public routes still use their existing contracts.

## Schema-selected templates

Template selection is an App-registered field plugin, not a Core builtin. Explicit field type or `config.renderer` selects an installed plugin while preserving storage/relation schema. Missing configured renderers fail closed. App-authored
`config.module` and `config.document_type` scope selection. Fields named
`contract_template_id` are not automatically transformed; installed App migrations own that upgrade.
Likewise, a field named `seed_from` has no implicit cross-record copy behavior.

## Per-form completion presentation

`form_schema.confirmation` may contain plain text `title` (up to 160 chars), `message`
(up to 2000 chars), and boolean `allow_another_response`. Omitted values preserve existing
completion UX. `false` hides only the anonymous repeat-response button; it is not deduplication,
rate limiting, approval, or a change to create permissions. Assigned form editing remains separate.

## Authenticated host routes

Hosts register `registerWorkspaceRoute({path, element})`. Core mounts these within its
existing authenticated layout. Hosts own domain routes, redirects and any additional resource
permission checks. Canonical Core member assignment route is `/me/assigned-form` and the
wire prompt is `pending_assigned_form`; historical App aliases belong in App migrations.

## App-owned document UI and row imports

Document editor, template tools/transport, concrete routes, signatures and default context
are owned by the Business document App. Core exposes `registerWorkspaceRoute`,
`registerTrackNavigation`, and `registerFieldType`. Track navigation is applied after an
authorized track loads and accepts only local paths. It grants no new data access.

Bulk import actions consume rows only when `importable === true` and `custom_fields` is an
object. Producers own matching and validation, omit unresolved records or mark them false.
There is no implicit employee identifier gate. Likewise, Core does not allocate App record
identifiers by scanning records: App server hooks own allocation and consistency.
