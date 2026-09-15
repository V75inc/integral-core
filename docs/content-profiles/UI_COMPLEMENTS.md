# UI Complement Recipes

## Decision

Integral's Region System and `create_wizard` are the runtime for reusable
operational UI. A **UI Complement Recipe** is a named, versioned, YAML-first
composition of those existing regions and wizard step kinds. It expands into
an ordinary content-profile manifest before normal compilation.

```text
App profile + ui_complements[]
        -> complement expansion
        -> ordinary views[] / related_views[] / create_wizard
        -> Region System or CreateWizardModal
```

This is additive. It does not replace the Region System, create a second
widget framework, introduce a new permission path, or load browser code at
runtime.

## When to use each extension mechanism

| Need | Use |
|---|---|
| A new arrangement of existing regions or wizard steps | UI Complement Recipe |
| A genuinely new renderer or generic wizard step kind | UI Pack / Region System plugin |
| A profile-local alias/configuration of one view | Manifest composite |

For example, a Pay Run overview is a recipe. A new interactive
reconciliation grid is a code-backed UI Pack. A reusable file-upload wizard
step extends the Region System's wizard-step registry.

## Manifest contract

An app or track declares the recipes it uses, its target entry type, and its
own bindings. Field names, view keys, tool keys, labels, and domain rules stay
visible in the app profile.

```yaml
ui_complements:
  - id: operational-record
    version: ">=1.0"
    track: pay_runs
    entry_type: pay_run
    config:
      summary_view: pay_run_summary
      actions_view: pay_run_actions
      primary_views: [pay_run_payslips, pay_run_filings]

  - id: guided-workflow
    version: ">=1.0"
    track: pay_runs
    entry_type: pay_run
    config:
      steps:
        - kind: period_picker
          periods_tool: compute_upcoming_pay_periods
          fields: [period_start, period_end, pay_date]
        - kind: entry_checklist
          source_track_type: Guyana Payroll Employees
        - kind: summary
      on_create_tool: populate_pay_run_lines_for_employees
```

The first version accepts only explicit values. It intentionally has no
arbitrary template language or remote-code capability.

## Initial recipes

### `operational-record`

Builds a predictable entry surface from existing views:

```text
summary tiles -> readiness/guidance -> action bar -> related records/files -> history
```

It composes `layout_container`, `summary_tiles`, `static_content`,
`action_bar`, and `reverse_relation_list`.

### `guided-workflow`

Packages the existing create flow:

```text
form and/or period picker -> entry checklist -> review summary -> create callback
```

Guyana Payroll is the reference use case. Aruba and BVI retain their own
calculations and filing rules while reusing the same guided-flow shape.

### `data-health` (next)

Defines a shared readiness contract for users and the resident agent:

```text
ready | warning | blocked
check key, explanation, and recommended next action
```

The initial form is declared guidance. If it needs live computed state, it
becomes a small generic UI Pack with app-supplied inputs, never hardcoded
payroll rules.

## Universal operations UI pack

The shared `operations-ui` pack now includes renderer primitives that apps
configure rather than reimplement:

- `operations-ui/settings-hub` groups related setting records and supports
  inline boolean toggles plus deep-link editing.
- `operations-ui/workflow-stepper` maps an entry status to an ordered visual
  progression. A step may accept one or more status values.
- `operations-ui/data-health` evaluates required/equality checks supplied by
  the app and reports what must be fixed before an operation proceeds.
- `operations-ui/report-center` renders configured report cards with
  app-supplied metrics, labels, and descriptions.

These widgets are intentionally domain-neutral. Payroll supplies its own
status names and checks; another app can use the same primitives for a deal,
case, onboarding process, inventory cycle, or approval workflow. Layout,
placement, labels, colors, visibility, and actions remain profile-owned.

## Agent UI extension guide

This is the contract for agents building apps that use UI complements. Use a
reusable complement before writing an app-specific React component.

### Build an app that uses a complement

1. Inspect the available complement types and their config schemas.
2. Declare the required view in the app or track profile using a stable `key`.
3. Pass app-specific field keys, labels, statuses, checks, actions, and child
   view keys through `config`; do not hard-code those values into the widget.
4. Place the view with an ordered `layout_container.regions[]` entry.
5. Set the intended default view and hide legacy duplicate views.
6. Refresh/materialize the profile on existing installations; YAML changes do
   not automatically change an already-installed workspace.

Example:

```yaml
views:
  - key: approval_progress
    name: Approval Progress
    view_type: operations-ui/workflow-stepper
    entry_types: [purchase_request]
    config:
      status_field: status
      steps:
        - {key: draft, label: Draft, statuses: [draft]}
        - {key: review, label: In Review, statuses: [pending_review]}
        - {key: approved, label: Approved, statuses: [approved]}
        - {key: complete, label: Complete, statuses: [paid, closed]}

  - key: request_workspace
    name: Request Workspace
    view_type: layout_container
    entry_types: [purchase_request]
    config:
      regions:
        - {key: summary, kind: view, view: request_summary}
        - {key: progress, kind: view, view: approval_progress}
        - {key: actions, kind: view, view: request_actions}
```

The order in `regions[]` is the user-facing order. Keep the primary record
and summary first, workflow/readiness next, actions after that, and large
related collections below the primary register or table.

### Build a new reusable complement

1. Create a domain-neutral component in `frontend/src/components/views/`.
2. Type its configuration and consume `ViewWidgetProps` only.
3. Add a manifest in `frontend/src/views/manifests/` with a stable
   `operations-ui/<name>` type.
4. Register the same type in the backend plugin registry with a config schema
   and scope (`track`, `entry`, or `both`).
5. Define loading, empty, error, mobile, and permission states.
6. Document placement and mutation/cache behavior before using it in an app.
7. Add compiler and frontend tests, then materialize it in one reference app.

Never put payroll, HR, or country rules in a universal complement. Never add
an app-specific permission path. If a new renderer is not genuinely generic,
keep it in the app profile or create a domain-specific widget instead.

### Agent safety rules

- Reuse an existing `_manifest_view_key`; do not create duplicate views on
  every profile refresh.
- Verify both frontend and backend registration or the renderer will fall
  back to a table/feed or fail as an unsupported view type.
- Treat setting changes as explicit user operations, never as a side effect of
  opening or running a pay run.
- Preserve existing entries and effective-dated records when reconciling UI
  configuration.

## Compiler responsibilities

The compiler recognizes and expands the first two built-in recipes before
ordinary content-profile normalization. It also preserves the declarations in
canonical output for catalog and introspection consumers:

1. Require a recipe ID and a track or entry target.
2. Apply a bounded declaration count and reject duplicate recipe targets.
3. Resolve the target track and entry type by manifest key.
4. Expand `operational-record` into a `layout_container` plus a related view.
5. Expand `guided-workflow` into the existing `create_wizard` contract.
6. Reject unknown targets, generated-key conflicts, and wizard replacement.
7. Run the expanded manifest through the existing compiler unchanged.

The existing Region System still validates region configuration and the wizard
registry still validates step kinds. Recipes therefore cannot bypass view
scope, permissions, app install lifecycle, or tool approval rules.

## Agent contract

Expose applied recipes through substrate introspection so the resident agent
can follow one deterministic shape:

```text
current record -> readiness result -> permitted next action -> output location
```

For a Pay Run, that means identifying missing setup, directing the user to
the correct region, offering only valid actions, and locating generated
filing files afterwards. A recipe does not grant the agent new authority.

## Delivery sequence

1. Add recipe discovery, validation, and compiler expansion.
2. Implement `operational-record` from existing regions.
3. Implement `guided-workflow` over the current wizard configuration.
4. Make Guyana Pay Runs the reference implementation.
5. Apply the recipes to Aruba and BVI.
6. Add live `data-health` after its generic input contract is proven.

## Invariants preserved

- No persistent node, edge, route, policy, or action path is added by a recipe.
- Region placement and scope enforcement remain in the normal compiler.
- Wizard steps remain registered through `content_profile_wizard_steps`.
- Recipes expand to ordinary manifest data; they cannot execute arbitrary code.
- Domain behaviour remains in app profiles and tools, never generic substrate code.

## Related documentation

- [REGION_SYSTEM.md](REGION_SYSTEM.md)
- [UI_PACKS.md](UI_PACKS.md)
- [COMPOSITES.md](COMPOSITES.md)
- [AGENT_CONTRACT.md](AGENT_CONTRACT.md)
