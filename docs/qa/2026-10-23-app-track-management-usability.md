# App and Track management usability review

Audience: people organizing their work without knowledge of the schema substrate.
Reviewed against finding, understanding, creating/installing and managing templates.

## Assessment and changes

| Finding | Impact | Change |
| --- | --- | --- |
| Track App dropdown grew with every App | High: scanning and selection do not scale | Right-sidebar destination chooser, name/purpose search, at most six visible results, bounded scroll, named selection and Change App |
| Track templates lacked App-style details | Medium: users choose without understanding the structure | Reuse the App detail renderer for Track record types, fields, tags and views; preview does not select or create; explicit Use template |
| App Installed tab showed an empty installation basket and omitted custom Apps | Medium: install guidance distracts from management | Include custom Apps with Open; keep uninstall limited to eligible library Apps; contextual count/guidance and preserved selection |
| Mobile App review required an intermediate review screen | Medium: redundant navigation | Review opens confirmation directly; optional names/descriptions and example-data choice remain editable there |
| Example-data control appeared when no examples existed | Minor: a choice with no effect | Show it only for templates with example records |
| Track delete appeared outside its card | Medium: unclear action ownership | View details and trash icon share an action row inside the colored card, with separate native buttons |
| Deletion confirmation was a paragraph | Medium: important consequence buried | Name, saved-configuration removal, existing items/data kept, irreversible action in three short sentences |
| Import labels differed and implied YAML only | Minor: inconsistent vocabulary and hidden file support | Both say Import template; shared uploader explains supported formats |
| Selection summaries repeated descriptions/instructions | Minor: excessive reading | Track summary shows selected name/counts; details hold full descriptions; remove zero-example and empty-automation sections |

## Final interaction pattern

Browse/search templates on the left; inspect without changing a selection; choose
on the card; review the selection on the right; confirm before writing. Both use
the same 19rem companion column and existing design tokens. Create your own remains
a separate prominent action. Import and template deletion remain library actions.

Track creation has one destination, so it keeps a single selected template and
asks for a name/visibility before Create track. App installation supports multiple
selections and optional setup requirements, so it keeps a basket and batch review.
These functional differences do not require identical controls.

Destination changes clear incompatible App template selections. App-list failure
has retry and does not block top-level Tracks. Cancelled destination searches retain
the selected App. Uninstall retains its separate data-aware preflight. Template
deletion retains backend permission and attachment checks.

## Accessibility and verification

Native buttons and inputs, labeled search/location controls, keyboard location
selection, visible selected states and 44px action targets. No nested action buttons.
Browser checks cover Track detail preview/selection, App destination/details,
short delete confirmation and cancellation, desktop layouts, and 390px layouts.
At 390px both dialogs report document width 390px; mobile App review directly
exposes Install and retains customization. No new records or deletions were needed.
Contrast was visually reviewed using existing tokens; no new automated contrast
certification is claimed.

Scale fixture uses 1,000 synthetic Apps with six rendered results; search can find
App 0999. Filtering is client-side over the workspace API response. Very large
remote collections may later require server pagination; this change does not
claim that capability.

Exports: the user chose configuration-only and full-data options for both Apps
and Tracks. That data-portability implementation remains separate from this UI
refinement; these dialogs do not yet claim Entries/attachments/relation export.

## Catalog congruency follow-up

App and Track templates now render the same `TemplateCatalogCard` component:
identity tile, heading, structural summary, bounded description, Add/Selected
control, View details and trash action inside the card. Both use the same
responsive two-column catalog grid, search row, library tab and result-count
presentation. Track App-provided choices use these cards too, under From this
App. The old radio-list picker remains only in the separate legacy creation
form; Manage Tracks no longer uses it. Create your own track stays in the footer,
without a duplicate blank-template card in the library.

Browser readback verified template selection, switching to an App and back,
App-specific source tabs, the retained selection sidebar, and single-column cards
at 390px (document width 390px; card grid one 344px column). No records were
created or deleted. Desktop screenshot:
`.local/design-review/track-catalog-congruent.png`.

## Contextual custom Track creation follow-up

The footer previously hid **Create your own track** whenever an App was selected,
while an empty library still suggested that action. The control is now available
for both top-level and App destinations on desktop and mobile. Choosing Inside an
App without selecting an App disables creation until the destination is chosen.
Launching from an App still locks the destination, including after Back.

Custom creation explicitly clears the template selection and starts with an empty
name and purpose. It does not submit an App template key or library template ID.
A late template response or retry cannot replace that choice. Back retains the
custom draft; closing/reopening starts a new flow. No write happens until the
user confirms Create track.

App contexts also expose the workspace-visible Track library and Import template,
including packaged Apps with prescribed Track templates. Imported choices reveal
their library card and survive the subsequent library refresh. Workspace scope,
Track-only import validation, and server authorization remain authoritative.
App-provided cards now use actual record-type/view counts and descriptions;
App defaults says Standard setup instead of claiming zero types and views.
Import previews use singular/plural record type and view labels.

Verification includes:

- Track dialog regression cases covering custom creation on desktop/mobile,
  App destination locking, late responses, empty/error states, imported selection
  retention, destination search, confirmation, cancellation, and template deletion.
- Real HTTP creation and readback for custom and imported Tracks under an App.
- Real browser creation of **Custom Track Review QA** and YAML import/creation of
  **App Track Import Review QA** inside the isolated **Reference SDK Home** App.
  Both appeared in that App, and the imported Track exposed its Item record type.
- Mobile walkthrough at 390 × 844: fixed creation controls, locked App, custom
  details, and draft retained through Back/Continue. The viewport was restored.

Synthetic records were added only to the isolated preview. No existing records
were deleted. This follow-up is included in PR #170.

Final checks: 351 frontend test files / **2,150 tests passed**, including 25 Track
dialog cases; all seven Track import/creation HTTP tests passed. TypeScript and
production build passed. Touched-file ESLint reported zero errors (seven existing
hook warnings); backend test formatting and flake8 passed. The build retains the
existing large-chunk advisory.
