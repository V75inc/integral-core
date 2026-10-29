# Reusable regions

Regions compose focused portions of an operational interface: forms, related collections, summaries, actions, charts, trees, and overlay containers where registered. They share the palette's contract and the surrounding App's authority.

Choose a region from its manifest and configure only supported inputs. A form needs a declared record type and field configuration. A relation region needs a valid authorized relationship. A chart needs a bounded data source with an explicit shape.

Overlay containers such as modal, drawer, and popover regions organize interaction. Their placement does not create another permission model. Loading, empty, denied, error, and unavailable states must remain understandable.

`region-system/processing-status` shows the state of a record that background work is acting on: configured `stages`, a pill for `status_field`, progress from `progress_field` or a Core job named by `job_field`, the reason from `error_field` when the status is in `failed_statuses`, and an optional `retry` tool. It refreshes while the status is in `active_statuses`. Labels come from the App's configuration; the region holds no domain wording.

Creation flows validate required fields and schema through canonical writers. A multi-step wizard does not authorize a partial record merely because the first screen completed. Test cancellation, invalid input, revision changes, successful save, and browser readback.

Use [view manifests](VIEW_PALETTE.md), [component guidance](UI_COMPONENTS_GUIDE.md), and [extension presentation](../platform/extension-contract-v1.md#presentation) rather than copying obsolete domain-specific UI recipes.

## Optional detail levels and related-record creation

A `layout_container` may set `config.detail_modes: true` for Simple/Advanced
controls. Nested containers inherit the enclosing mode. Fields marked
`validation.advanced: true` hide in Simple only while optional and empty;
required/populated values stay visible. Modes never change values, validation or
authority. Containers without the flag retain their existing presentation.

An editable App-bound contribution can opt a single entry relation into a drawer:

```yaml
validation:
  create_related:
    kind: contact
    form_operation: describe_contact_form
    create_operation: create_contact
    prefill: {email: recipient_email}
```

The read operation returns `fields` (normal scalar field specs), `defaults`,
optional `name_field` for search prefilling and optional `dependent_options`:
`{field_key: {field: controlling_field, values: {enum_value: [allowed_values]}}}`.
The create command accepts `{kind, fields, confirm_duplicate}`, returning
`{ok: true, entry_id}` after domain validation/readback, or
`{ok: false, message, candidates: [{id, title}]}` for duplicate review. Declare
exact action authorization so operation discovery supplies `allowed`.

The host reads the saved entry through the ordinary API before selecting it.
`prefill` maps related custom fields to existing empty parent fields and preserves
edits. Cancelling leaves the parent draft intact. Uncertain results retain exact
command identity; withheld results do not verify current state. User/workspace
changes replace the drawer and partition query/intent state. Apps own kind names,
forms, fields, commands and permissions; Core supplies the neutral renderer.

Drawer selection and recovery also belong to the current App, Track, parent
Entry, contribution mode and field/operation binding. Changing that identity
discards late responses before they can patch another draft. Normal edits to
the same draft retain the pending command. A withheld completed result stays
visible and locked after closing/reopening; **Start a new record** explicitly
acknowledges a separate task with a fresh command identity. Uncertain writes
continue to retry their exact original command instead.
