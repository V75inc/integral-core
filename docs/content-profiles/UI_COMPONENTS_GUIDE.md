# Integral UI Components and Complement Packs

## Purpose

Integral’s UI complement system lets an app compose a more useful interface
from reusable components without changing the platform’s core screens. An app
declares what it wants to show, where it should appear, and which track or
entry type supplies the data. The component remains generic; the app supplies
the business meaning through configuration.

This gives each app a tailored workflow while preserving one common platform
UI, data model, permissions model, and agent surface.

## The approach

We separated the problem into three layers:

1. **Platform primitives** — tracks, entries, views, relations, permissions,
   actions, and the existing layout system.
2. **Reusable UI complements** — components such as command centers, settings
   hubs, readiness panels, record collections, reports, and workflow steps.
3. **App manifests** — declarative configuration that chooses components,
   ordering, labels, colors, data sources, and actions for a specific app.

The important design decision is that payroll behavior does not live inside a
generic component. A report component does not know Guyana tax law; it knows
how to display configured metrics, tables, trends, and exports. The payroll
manifest supplies the relevant tracks and fields.

## Components currently available

### Command Center

An app landing surface for the most important operational areas. It can place
multiple child views into a deliberate hierarchy, such as:

- current work queue;
- summary metrics;
- trend or chart section;
- exceptions and data health;
- shortcuts to the next action.

The command center is intended to replace a random list of tracks as the
primary app experience.

### Settings Hub

A single place for app-wide configuration. It presents settings as grouped
cards with clear descriptions and toggle controls instead of exposing every
configuration track as a separate navigation destination.

Apps can define:

- boolean toggles;
- linked configuration records;
- setup status;
- descriptions and operator guidance;
- links to the underlying record when editing is required.

For Guyana Payroll, the hub groups HR sync, Pay Calendar, Statutory Rates, and
Company Profile.

### Record Collection

A reusable collection component for an app’s primary operational records. It
supports configurable columns, status badges, sorting, density, empty states,
and actions. This is the component used to make the Pay Runs list feel like a
work queue rather than a generic feed.

### Readiness Panel

A preflight component that shows whether an operation can proceed. Each check
has a label, state, explanation, and optional action. It is reusable for any
workflow that depends on setup, such as payroll, onboarding, month close, or
compliance filing.

The component reports missing prerequisites; it does not silently modify
settings to make a workflow pass.

### Workflow Stepper

A visual sequence for multi-stage operations. Steps are explicitly ordered by
the manifest and can show pending, active, complete, blocked, or skipped
states. It is useful for onboarding, approvals, filing, publishing, and other
processes—not only payroll.

### Report Center

A configurable reporting surface supporting:

- metric cards;
- operational tables;
- trends and simple charts;
- date and source-track context;
- CSV export;
- empty and incomplete-data states.

Reports are derived from their configured source records. They should make the
period, included records, aggregation method, and data limitations visible.

### Data Health

A reusable diagnostic component for incomplete or inconsistent records. It can
surface missing links, missing required fields, stale records, orphaned data,
and mismatched counts without mixing those problems into the main work queue.

## Placement and layout

Components do not hard-code their position. Placement is controlled by the
app’s view configuration and layout containers. A manifest can define:

- the primary component;
- child view order;
- sections or regions;
- layout direction;
- relative prominence;
- compact or expanded density;
- which components are hidden from normal navigation;
- which component owns the main action.

This is why the same component can serve a payroll dashboard, a CRM command
center, or an HR onboarding page while appearing in a different arrangement.

The preferred page hierarchy is:

```text
Page title and context
    ↓
Summary / current state
    ↓
Primary work queue
    ↓
Warnings, exceptions, and readiness
    ↓
Related records and history
    ↓
Secondary feed or activity
```

The layout is intentional: users see what they need to decide or do before
supporting records and history.

## Styling and interaction

Components use the shared Integral UI primitives for typography, surfaces,
spacing, badges, buttons, tables, and responsive behavior. Apps configure
meaning and emphasis rather than inserting arbitrary page-specific markup.

Configuration can control:

- button label and action placement;
- primary versus secondary action treatment;
- color or tone for status and severity;
- compact versus comfortable table density;
- card arrangement;
- visible fields and column order;
- empty-state instructions;
- whether a component is prominent, collapsed, or hidden.

This preserves visual consistency while allowing an app to feel purpose-built.

## How Guyana Payroll uses the system

The Guyana Payroll app uses the complement system to organize the whole app,
not only the payslip area:

Guyana Payroll can operate on its own. The HR module is optional, not a
prerequisite. Without HR, payroll staff maintain the Guyana Payroll Employees
roster and Guyana Compensation Records directly, then create pay runs,
calculate statutory deductions, finalize payslips, and generate filings from
that payroll data. When HR is installed, the optional sync mirrors valid HR
employee records into the payroll roster; it does not make HR the payroll
system of record or prevent standalone payroll operation.

- the command center presents payroll operations and trends;
- the Pay Runs page prioritizes the register and actions;
- the readiness panel explains missing setup before finalization;
- the settings hub keeps app configuration in one place;
- the report center presents payroll summaries, trends, exceptions, and
  exports;
- related payslips and filings are secondary views rather than the first thing
  users see;
- the agent receives the same workflow rules and source-of-truth guidance.

The payroll-specific rules remain in the payroll profile and tools. The UI
components only render the configured workflow.

## How another app should use the packs

An app should:

1. Identify its primary operational object and source track.
2. Choose a landing component, normally a Command Center or Record
   Collection.
3. Put the main work queue before related history and feeds.
4. Add Readiness or Data Health only where there are real prerequisites or
   integrity checks.
5. Use Settings Hub for app-wide behavior instead of exposing configuration
   records as unrelated navigation items.
6. Configure reports with explicit source tracks, fields, aggregations, and
   export behavior.
7. Declare actions next to the component that owns the decision.
8. Keep domain logic in app tools and skills, not in generic UI components.

Example manifest shape:

```yaml
ui_complements:
  - id: operational-record
    version: ">=1.0"
    track: work_items
    config:
      summary_view: work_summary
      primary_view: work_queue
      readiness_view: work_readiness
      actions_view: work_actions
      primary_views:
        - work_history
        - work_related_records
```

The exact keys depend on the registered complement contract, but the principle
is stable: the app declares composition and the component interprets it.

## Agent compatibility

Every operational UI component should have an equivalent agent-facing path.
The agent must be able to discover:

- the app’s authoritative tracks;
- the component’s source data;
- available actions and prerequisites;
- whether an operation is read-only, staged, approved, or finalized;
- the expected post-action summary.

UI actions and agent actions must use the same backend tools and validation.
The UI is a projection of the workflow, not a second implementation of it.

## Extension rules

When adding a new reusable component:

- keep it domain-neutral;
- accept configuration instead of hard-coding track names or field names;
- use shared UI primitives;
- support loading, empty, error, and permission-limited states;
- make ordering and visibility declarative;
- provide a clear data-source contract;
- expose actions through the existing action system;
- add tests for configuration, layout, and empty states;
- document how an app declares and composes it.

When an app needs domain-specific logic, add an app tool, hook, or skill and
feed its result into the generic component. Do not copy a payroll component to
make a CRM component; extend the generic component contract when the behavior
is genuinely reusable.

## Result

The complement-pack approach gives Integral a middle layer between a generic
CRUD interface and a fully custom application. It allows us to improve the
user experience app by app while keeping the platform reusable, configurable,
and understandable to both people and agents.
