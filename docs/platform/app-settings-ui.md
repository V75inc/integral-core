# App settings UI contract

All App-specific settings UI controls **MUST live in the standard App settings
dialog**, opened by the Settings control in the App header. This rule applies
to developers, coding agents and the resident agent, including packaged Apps
and installed customizations. The dialog is the single settings destination
across the App's Home, Tracks, dashboards and custom views.

## What belongs in settings

Put persistent App configuration here: defaults, preferences, workflow options,
numbering rules, integration options and other values that govern how the App
behaves. Declare supported controls in `app.settings_schema`; Core renders them
with the shared `AppSettingsForm` inside `AppSettingsModal`.

Home, dashboard, Track, Entry and extension views **MUST NOT introduce their own
settings forms, settings tabs, configuration drawers or duplicate editors**.
A shortcut may direct the user to the standard dialog through a supported host
navigation mechanism; it must not create another settings surface. Do not
invent an SDK navigation target or route that the running host does not expose.

Everyday work stays on its operational surface. Creating a record, recording a
transaction, filtering the current view, or running a declared business action
is not App settings. A Home form may perform that work; a Home form that changes
an App-wide default is a settings editor and belongs in the settings dialog.
Do not disguise configuration as an Entry, Track or business action to avoid
this requirement. Account and workspace settings retain their own Core-owned
surfaces.

## Developer implementation

Declare an object schema under `app.settings_schema`, using the controls the
current shared renderer supports. Give fields clear titles, descriptions,
defaults and validation. For example, this is an excerpt of an App declaration:

```yaml
app:
  settings_schema:
    type: object
    properties:
      record_prefix:
        type: string
        title: Record prefix
        description: Prefix used for new record numbers.
        default: REC
    additionalProperties: false
```

Core reads the installed App through `GET /api/apps/{app_id}/settings` and
persists through `PATCH /api/apps/{app_id}/settings` with a
`{"settings": {...}}` body. Keep schema validation, current App policy and
canonical persistence authoritative. A visible control or package declaration
does not grant permission. Read configuration from the injected public App
facade, rather than private Core imports or a separate browser-local settings
store.

Core's required installation step may collect initial values using the same
schema. It is part of the canonical install flow, not a second post-install
settings editor. Subsequent editing belongs in the App settings dialog.

If a required control is not supported, propose and implement a reviewed
extension to the shared settings renderer and its public contract. Do not add a
parallel configuration panel or assume arbitrary custom settings slots exist.
This contract adds an authoring/review requirement; it does not claim a new
runtime slot or automatic semantic detection of misplaced settings forms.

## Agent authoring and maintenance

- Classify requested controls as App configuration or operational work before
  proposing a Home, view or App design.
- Use the App settings schema and standard dialog for configuration. Preserve
  existing values and unrelated fields; apply schema changes through the
  supported definition/model lifecycle and any required migration.
- Use only available, authorized tools and ordinary approval paths. Do not
  invent a settings-write tool or claim a proposed change is saved.
- When revising an existing App, move duplicate settings controls into the
  standard dialog and preserve their values. Verify the canonical readback.

## Review and qualification

Check every App surface for duplicate configuration controls. Verify the
Settings entry point from each App view, readable labels, defaults, validation,
save/reopen persistence, narrow screens, keyboard use and permission denial.
Confirm operational forms still work and existing settings survive upgrades.
Use the shared toast convention for update notifications. A successful compile
does not establish UI compliance; record browser evidence separately.

See the [extension contract](extension-contract-v1.md),
[Home SDK](app-home-sdk.md) and
[agent authoring contract](../operational-models/AGENT_CONTRACT.md).
