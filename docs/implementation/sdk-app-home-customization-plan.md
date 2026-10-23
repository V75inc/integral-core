# Native Home SDK implementation plan

## Rendering decision revised after visual review

The iframe-based Home proposal was rejected during product review on October 23,
2026. It produced a visibly separate document: different spacing, embedded-page
styling, sizing and scrolling behavior. Home customizations must instead render
inside Integral's normal document using its native React design system.

This replaces the previous Home iframe proposal. Existing isolated Track/Entry
extensions are separate compatibility surfaces; they do not determine how an App
Home renders.

## Shared authoring contract

- Developers package native `app.home` defaults. Reusable SDK component
  functions return typed composition data; they do not execute package JavaScript
  in the authenticated Core page.
- The resident agent reads the same component schema and the App's declared
  query/operation bindings. It previews, proposes, obtains ordinary approval,
  persists an instance override and verifies readback.
- Developers may later maintain agent-created Apps. Origin does not determine
  edit rights. Stable IDs preserve local form state while content/data refresh.
- Installed overrides remain separate from package defaults. Reset restores the
  default. Observed revision checks protect concurrent authors and stale approval.

## Native surface

Compose stack/grid layouts, cards, headings/text/markdown, metrics/charts/records,
forms/actions, tabs and accordions. These use Integral's typography, surfaces,
inputs, buttons and chart treatment. Mixed Homes combine native component trees
with the existing governed dashboard widgets. Static content needs no query.

Data binds to declared App query keys. Form fields bind to typed App operation
inputs. Canonical authorization, validation, staging policy, receipts and effect
identity remain authoritative. Preview never submits a business action; live
reads are opt-in. The palette can expand through the native contract without
creating a global package-code injection mechanism.

## Implementation and acceptance

| Area | Delivery | Evidence required |
| --- | --- | --- |
| Contract | Shared Pydantic native component schema; compiler/API/tool parity; bounded trees | Package/instance round trips, no frames/HTML/code, invalid bindings rejected |
| Renderer | Native responsive React composition and existing UI primitives | No iframe in Home DOM; keyboard, mobile, theme, charts, form and navigation checks |
| SDK | Independent typed `defineHome` / `defineComponent`; reusable external example | Clean npm artifact install/typecheck; deterministic external App bundle |
| Resident | Read schemas/bindings, preview, staged update and readback | Preview read-only; stale approval refuses newer edit; persisted refresh |
| Coauthor/upgrade | Workspace fence, revision guard, default/override separation | Concurrent edits, stale developer object, preserved compatible override, incompatible upgrade refusal |
| Regression | Existing native Homes and Track/Entry extension compatibility | Full frontend/backend, PostgreSQL focused cases, Core-only, artifact and CI checks |

## Invariants preserved

I-APP-DEF-01: active definitions own bindings; presentation grants no executable
asset authority. I-APP-01 and I-PROFILE-01/02: canonical authoring and existing
publication path. I-EXT-01, I-HOOK-01/02 and I-CRUD-01: public facades and generic
contracts. I-APPROVAL-04/05: ordinary approval and readback. I-WORK-02/08/09:
stable effect identity and unconfirmed/withheld outcomes remain explicit.
I-GRAPH-01/02: existing App composition/definition storage; no new floating node.

## Qualification status

Native visual qualification passed on the isolated preview: no Home iframe in
its DOM; native form/action toast, shared theme, desktop/mobile layout, readable
chart labels and draft preservation through live layout updates. Stale saves
return 409 and persisted changes render without reloading the page.

The public UI SDK passed its 11 tests and a clean npm pack/install/typecheck.
The external native App archive built twice with identical SHA-256
`f6ac93add01dcd6aac7e0e018f88b130aa3834a66e9c668cda3db6d77b4e79bd`.
The Python SDK wheel also built and imported in a clean consumer environment.

PostgreSQL passed 69 focused Home, App lifecycle and definition tests, plus the
additional native binding-removal upgrade case. The catalog installation path
now forwards events through the canonical transaction event sink; this removes
a transaction/logging deadlock reproduced during that qualification.

The full `make verify` gate passed: guards, pinned formatting/lint, types,
reproducible Core wheel/import, CI-faithful smoke (2,202 passed, 26 skipped),
frontend and the default full backend suite (7,373 passed, 987 configured skips).
After the final form refinements, the frontend suite passed again with 2,129
tests and the production UI build passed. The Core-only lane passed nine tests.
Default contract cases are included in the full backend run; the duplicate
standalone contract run was stopped rather than counted as completed evidence.

Native layout changes preserve drafts; changing an action binding starts fresh
form state. Prefilled values are visible, and unchecked checkboxes submit false.
Earlier iframe screenshots/results are not acceptance evidence for this native
replacement. No production deployment, SDK publication or version bump is part
of this change.
