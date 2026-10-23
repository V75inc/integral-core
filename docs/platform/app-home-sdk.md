# Native App Home SDK

App Homes render directly through Integral's React UI and design system. They
have ordinary document flow, shared typography, inputs, cards, charts, focus and
responsive layout. There is no Home iframe, nested document theme or independent
scrolling boundary.

Developers ship `app.home` defaults. Developers, coding agents and the resident
agent edit the same installed-App contract. Installed edits are stored separately
from package defaults and checked against an observed Home revision. The origin
of an App does not prevent a developer from maintaining it later.

## Composition

```yaml
home:
  title: Home
  renderer: native
  content:
    id: overview
    type: stack
    children:
    - {id: heading, type: heading, text: Your work}
    - id: sections
      type: grid
      columns: 2
      children:
      - {id: notes, type: records, title: Recent notes, query_key: notes}
      - id: message
        type: card
        children:
        - id: message-form
          type: form
          title: Send a message
          operation_key: echo
          submit_label: Send
          fields:
          - {name: message, label: Message, required: true}
```

The native palette includes stack/grid, card, heading/text/markdown,
metric/chart/records, form/action and tabs/accordion. Developers build reusable
components with ordinary typed functions returning compositions. The public
`sdk/ui` package exports `defineHome`, `defineComponent`, `HomeElement` and
`HomeInput`; it imports no private Core module. Serialize the result into
`app.home` or submit it through the public API. For example:

```ts
import { defineHome, defineComponent } from '@integral/sdk-ui';
const Welcome = defineComponent<{title: string}>(({title}) => ({
  id: 'welcome', type: 'heading', text: title,
}));
const home = defineHome({title: 'Home', renderer: 'native',
  content: Welcome({title: 'Your work'})});
```

A mixed Home retains `renderer: widgets` and the existing dashboard widgets.
Add a custom block as `{id, type: native_component, grid, content}`. Static
content needs no fake data source. Legacy native widget Homes remain supported.

The first native increment deliberately does not execute arbitrary package
React/JavaScript inside Core. New rendering primitives extend the native
component contract; business logic remains in declared App operations through
the public Python facade. Existing isolated Track/Entry extensions retain their
sandbox and legacy bridge SDK, independently of native Home rendering.

## Data, actions and state

Home forms are for operational work. App-specific defaults, preferences and
configuration MUST use the standard App settings dialog and
`app.settings_schema`; a native Home form is not a substitute settings editor.
Developers and agents follow the [App settings UI contract](app-settings-ui.md).


Data nodes use an owning App's declared query key and parameters. Query reads
remain permission filtered. Metrics use `value_path` (default `total_estimate`);
records/charts use `rows_path` (`items`) and `label_field` (`title`). Charts
optionally use a numeric `value_field`; otherwise each row contributes one.

Forms name fields from a declared operation's input schema, then submit through
the canonical operation dispatcher. Backend input validation, policy, approval
requirements, receipts and retry identity still apply. Mounting, previewing and
refreshing a layout never invoke an operation. Stable component IDs preserve
form state through property/data updates. Update notifications use toasts.

Operations use one stable intent key per submission. Lost/aborted/5xx results
are unconfirmed; they do not prove failure. Reconcile state/receipt before
retrying the same intent. Withheld recorded output remains distinct from a
new available result. The Python SDK also types the existing runtime job facade:
`job_id`, `enqueue_job`, `report_progress`, and `job_status`.

## Read, preview, persist and upgrade

1. `GET /api/apps/{id}/home?include_data=false` returns the effective Home,
   source, `home_revision`, definition revision, native component schema and
   available query/operation bindings. Ordinary workspace scope applies.
2. `POST /api/apps/{id}/home/preview` with `{home, include_data:false}` validates
   without saving. Live data is opt-in; action controls stay disabled.
3. `PUT /api/apps/{id}` with `{home, expected_home_revision}` persists through
   the canonical metadata service. Native custom edits require the observed
   token. Stale/concurrent edits return 409. Legacy dashboard-only callers keep
   their previous compatibility behavior. `home:{}` restores package defaults.
4. The resident reads, previews, stages an update, obtains ordinary approval and
   reads back. The revision is checked again when the staged change executes.
   Change events refresh open Homes and declared queries without reloading.
5. Definition activation and package upgrades serialize with Home authors.
   Compatible upgrades preserve instance content. Removing a query/operation
   used by that content blocks activation before an upgrade changes the App.

A Home is bounded to 128 KiB, 128 native nodes and depth 12. Component IDs are
unique. Mixed layouts retain the 12-widget limit and nonoverlapping grid, with
legacy heights through 20. No new graph record or effect owner is introduced.

See `examples/reference-sdk-home` for a native package and reusable SDK source.
