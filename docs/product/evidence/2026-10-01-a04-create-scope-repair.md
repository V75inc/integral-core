# A04 workspace-bound creation scope repair

**Initial source revision:** `7895c3c790be079f7e2d7f5bcf304273f0127c46` (draft PR #99, stacked on PR #97)
**Expanded source revision:** `ad05e14f23a906b658b4ee366dc48905be0b7b03`
**Browser/resident follow-up revision:** `3f8db1293a75ca071f17b092c5c4e456bbdc02ee`
**Primary CRUD effect-scope revision:** `9935b0e459850322118bd22a2a5b2cba7c007268`
**Sharing and Comment effect-scope revision:** `961d26c1d50c4ea089ac04645e6074011478b8f3`
**Share-link scope revision:** `abcab149003bb227607f54759671dcdfd0209886`
**Share-link readback revision:** `93d00f9fe10052ebfbfb407c9240f1be18f6f166`
**Date:** 2026-10-01 UTC
**Disposition:** selected workspace-bound create gaps are repaired and locally
qualified; A04 and C6 remain incomplete.

## Failed predecessor

The da33 C6 API returned 403 when a synthetic nonmember listed a foreign
workspace, but returned 200 when the same principal sent `POST /api/tracks`
with that workspace in `X-Integral-Scope` and no body `workspace_id`.
The owner's track count stayed zero. The create wrapper had ignored the
header and allowed its service to choose a default workspace. The
[da33 candidate record](2026-10-01-c6-file-volume-and-resident.md) retains
the failed A04 outcome.

## Repair and checks

The App and Track create wrappers now validate a supplied workspace header
before an effect, use a valid header when the body omits a workspace, and
reject header/body disagreement. Track creation under an App rejects a
workspace mismatch with that parent. Headerless callers retain their
previous behavior. The request-scope regression test covers foreign
headers, valid header binding, conflicting body workspaces, and a parent
App in another workspace.

`make verify` passed on the source changes: substrate guards, format/lint,
types, CI-faithful smoke, all 1,277 frontend tests, the full backend suite,
and the Core artifact gate. Full `make test-postgres` then passed against
fresh per-worker databases on a local Postgres service. The first Postgres
run exposed a restore-drill test that used the admin input DSN instead of
the fixture's effective worker DSN; that test was corrected, its focused
drill passed, and the full Postgres lane passed on rerun. Commit hooks passed.

The exact local API image built from this source was
`sha256:d10ee37298ddc79e21d58d543ee3f6f2584376285035b10e7f72d9c5ceb7be28`.
It booted in Core-only mode against a new `integral_a04_live` database in
the local Postgres service and served `/health` with 200. At
2026-10-01T03:30:19Z, two new synthetic users with separate organization
workspaces exercised these live results:

| Request | Result |
| --- | --- |
| Owner lists own workspace | 200 |
| Nonmember lists owner's workspace | 403 |
| Nonmember creates Track or App with owner's workspace header | 403 for each |
| Owner's Track count after refused writes | Unchanged |
| Caller creates Track with own valid header and no body workspace | 200; stored workspace matches header |
| Caller sends valid header but conflicting body workspace for App create | 400 |
| Nonmember calls `integral_list_tracks` over MCP with owner's workspace header | HTTP 200 with `isError=true` |

## Expanded content-create candidate

A second live probe on da33 showed `POST /api/tags` accepted a foreign
workspace header and created a Tag under the caller's own Track. The
expanded source uses the same validated-header helper for Tag, saved-view,
Entry, and Comment creation. It compares a valid header with each target's
parent Track or App before the effect. Headerless callers retain their
existing behavior.

Focused request-scope tests passed for those four added surfaces. `make
verify` and full `make test-postgres` passed on the expanded source, including
the frontend's 1,277 tests, full backend suite, guards, format/lint, types,
artifact gate, and fresh per-worker Postgres databases. Commit hooks passed.

The expanded exact local Core-only API image was
`sha256:45217d8c3b16cf47fa3f3d86fb028bf17a3af3d227475afc1176dd711c262026`.
It booted against a new `integral_a04_expanded` database in the local
Postgres service and served `/health` with 200. At
2026-10-01T03:55:21Z, new synthetic users exercised these live results:

| Create surface | Foreign workspace header | Valid but conflicting workspace |
| --- | --- | --- |
| Track | 403 | 400 (body workspace conflicts) |
| App | 403 | 400 (body workspace conflicts) |
| Tag under Track | 403 | 400 (parent workspace conflicts) |
| Tag under App | 403 | 400 (parent workspace conflicts) |
| Saved view under Track | 403 | 400 (parent workspace conflicts) |
| Entry under Track | 403 | 400 (parent workspace conflicts) |
| Comment under Entry | 403 | 400 (parent workspace conflicts) |

Tag, saved-view, and Comment creation under the matching workspace each
returned 200 as positive controls. An initial probe expected a valid header
for a different workspace with no body workspace to be rejected on a new
Track; the API correctly created in that header's workspace. The accepted
comparison above supplied a conflicting body workspace for Track and App.

These local images were not published to a registry. The live probe covers
selected HTTP create paths and the initial source's MCP scope denial, not
browser UI, resident tool calls, revoked membership, or every resource
effect endpoint. A04 is not marked passed. C6 needs a subsequent full
candidate freeze and qualification.

## Workspace and collaborator revocation repair

The first live revocation probe on the expanded image found that a former
workspace member who had created a private Track still received 200 on a
direct Track GET and could create an Entry without a scope header. The shared
role resolver had allowed a surviving resource `OWNS` edge to bypass the
workspace-membership gate. After removing that exception, a second exact-image
probe still returned 200: the production process cache stored roles under the
login principal ID, but the membership handler invalidated only the graph User
ID. Tests run with `TESTING=1` did not expose that cache mismatch.

The final source revision for this repair is
`7f385ab78bf1c90270df47db8ba8287d64dd887f`. The resolver now denies
private resources when workspace membership is removed, including direct
owners. Public visibility remains a read-only grant; a stale direct owner or
collaborator edge cannot restore write authority. A shared invalidation helper
evicts both graph and auth-principal cache keys on membership, collaborator,
and exclusion writes. The regression runs the membership case with the process
cache enabled and checks both cache identities on collaborator removal. Two
older test fixtures used placeholder workspace IDs; they now create the
membership and structural edges required by the access model.

`make verify` passed on the final source, including guards, format/lint,
TypeScript, the CI-faithful smoke run, 1,277 frontend tests, full backend
suite, and Core artifact import. Full `make test-postgres` passed against fresh
per-worker PostgreSQL databases. Commit hooks passed.

The exact local Core-only image built from that commit was
`sha256:d760286c1020c3c3678dec6db2480f779454753f6a4dc94c293230aeeed02cca`.
It booted against a new `integral_a04_revocation_final` PostgreSQL database
and returned 200 from `/health`. Three new synthetic users then exercised
these live outcomes with production-style caching enabled:

| Request after the permission change | Result |
| --- | --- |
| Removed member lists or creates in the former workspace scope | 403 |
| Removed member opens their private Track, scoped or headerless | 403 |
| Removed member creates an Entry there, scoped or headerless | 403 |
| Removed member opens their public Track | 200, read-only |
| Removed member creates an Entry in the public Track | 403 |
| Removed member calls `integral_list_tracks` over MCP in former scope | HTTP 200, `isError=true` |
| Removed collaborator opens the private Track after a successful cached read | 403 |
| Removed collaborator creates an Entry in that Track | 403 |

The same requests returned 200 before the respective revocations where
access was expected. The image remains local and unpublished. This evidence
covers selected HTTP and MCP revocation paths, not browser UI, resident tool
calls, all resource effects, or registry/deployment parity. A04 and C6 remain
incomplete pending those wider gates.

## Browser aggregate and resident proposal follow-up

The `7f385ab` image denied the removed member's bookmarked private Track, but
the same member still saw its title in Mission Control after a fresh browser
login. An authenticated `GET /api/me/mission-control` returned that Track in
`tracks` (HTTP 200). The aggregate list helpers admitted a surviving direct
`OWNS` or `COLLABORATES_ON` edge without checking whether its Workspace was
still in the member pool. The same pattern existed for Apps. Separately,
`integral_create_entry` could inspect the target Track and stage an approval
before its executor checked permissions.

Revision `3f8db1293a75ca071f17b092c5c4e456bbdc02ee` prunes direct
App/Track candidates against live accessible Workspace inventory and resolves
out-of-pool candidates through the shared role gate, retaining public read
access without restoring an old write grant. The entry-create stager checks
the persisted target and `entry.create` policy before reading its title or
minting a card. Bundle-local tools, which bypass central route bindings,
also validate current Workspace membership before invoking their handler.
The hot list path reuses its existing Workspace inventory: focused SQLite and
Postgres query-budget tests passed after an initial per-resource role check
exceeded the list budgets.

`make verify` passed on this revision, including guards, format/lint,
types, CI-faithful smoke, 1,277 frontend tests, full backend suite, and Core
artifact import. Full `make test-postgres` passed against fresh per-worker
PostgreSQL databases. The generated capability map did not change; staged-index
guards and commit hooks passed separately.

The exact local Core-only image is
`sha256:32dfffc653ebc9f76574cf3b391a7a56a3cbd61bd4887e86ce309c42a349b056`,
labeled with the source revision. It replaced the disposable API container
against the same synthetic PostgreSQL data and returned 200 from `/health`.
The removed member then produced these outcomes:

| Surface | Result |
| --- | --- |
| Mission Control API | HTTP 200; former private Track absent (it had been present on the predecessor image) |
| Direct private Track GET | HTTP 403 |
| Resident `integral_create_entry`, revoked Workspace scope | HTTP 200 tool envelope with `insufficient_permissions`; no staged token returned |
| Resident `integral_create_entry`, valid own scope aimed at former private Track | HTTP 200 tool envelope with `insufficient_permissions`; no staged token returned |
| Browser Mission Control | `Total tracks` 0 and “No tracks yet”; the former private Track title absent |

The browser used the PR #98 Mission Control frontend preview against this
exact PR #99 API image. It is useful UI evidence for the aggregate denial, but
it is not a single frozen web/API deployment pair. The image remains local and
unpublished. A04 still needs every required effect boundary and transport
qualified; C6 still needs a reconciled frozen candidate, registry/deployment
evidence for that candidate, and independent human architecture and Product
Owner decisions.

## Explicit scope on existing primary resources

The `3f8db12` image still accepted a valid but mismatched scope on an
existing resource: the owner had both a Personal and an organization
Workspace, sent `PUT /api/tracks/{id}` for an organization Track with the
Personal Workspace header, and received 200. The Track's stored `purpose`
changed. This was a real effect outside the client's selected Workspace,
despite the caller's valid resource permission.

Revision `9935b0e459850322118bd22a2a5b2cba7c007268` validates any
explicit HTTP Workspace header against the persisted effect target for
App, Track, Entry, Tag, and View update/delete. Entry, Tag, and View resolve
the parent Track or App only when a header is supplied; headerless direct
resource calls retain their existing path. An explicitly selected,
accessible but different Workspace is rejected before mutation. A revoked
or unknown Workspace header is also rejected. A direct-handler connector
test fixture was corrected to represent a genuinely headerless request.

Focused request-scope and connector tests passed. `make verify` passed all
guards, formatting/lint, types, CI-faithful smoke, 1,277 frontend tests,
full backend suite, and artifact import. Full `make test-postgres` passed
against fresh per-worker PostgreSQL databases. Staged-index guards and
commit hooks passed.

The exact local Core-only API image built from this source is
`sha256:fa3eed984f9e757ce42d2179fcca380fc2b747ccd8721181127209056afd3407`,
labeled with the full source revision. It replaced only the disposable A04
API container on port 19123, using the existing synthetic PostgreSQL data;
`/health` returned 200. The predecessor's wrong-scope mutation and its
correction produced:

| Live HTTP probe | Result |
| --- | --- |
| Owner updates organization Track with Personal Workspace header | 403; stored `purpose` unchanged |
| Owner deletes organization Track with Personal Workspace header | 403; Track still readable |
| Owner updates same Track with matching organization header | 200; new `purpose` stored |
| Removed member updates Track with former organization header | 403 |
| Removed member updates Track without header | 403 |

The image is local and unpublished. The automated wrong-scope regression
covers update/delete for all five primary resource types, but this evidence
does not cover every mutation route: collaboration/exclusions, invitations,
attachments, operational-model and template changes, secondary Entry/Track
actions, and their resident/MCP counterparts still require systematic
effect-boundary review. A04 remains unproven; this source is not a frozen
C6 web/API pair and does not satisfy registry/deployment or independent
human review gates.

## Secondary sharing and Comment effects

Revision `961d26c1d50c4ea089ac04645e6074011478b8f3` extends explicit
scope binding to collaborator and exclusion mutations for Apps, Tracks, and
Entries; App/Track ownership transfer; and Comment update/delete. A shared
resource-target resolver finds the persisted App/Track Workspace or an Entry's
parent Track only when a header is present. These routes retain their
existing headerless authorization path.

Focused request-scope, access, and sharing tests passed. `make verify` passed
guards, format/lint, types, CI-faithful smoke, 1,277 frontend tests, full
backend suite, and artifact import. Full `make test-postgres` passed against
fresh per-worker databases. Staged-index guards and commit hooks passed.

The exact Core-only image is
`sha256:02ae517112d2d123de1ed793e9ea173e92a3e8ec8f5b540f788ca82142e6466c`,
labeled with that source revision. It replaced only the disposable A04 API
container against synthetic PostgreSQL data and returned 200 from `/health`.
The owner sent a Personal Workspace header for effects targeting an
organization Track or its Comment:

| Live HTTP probe | Result |
| --- | --- |
| Add Track collaborator under wrong header | 403 |
| Add Track exclusion under wrong header | 403 |
| Edit Comment under wrong header | 403 |
| Delete Comment under wrong header | 403 |
| Removed member opens Track after refused grant | 403 |
| Edit same Comment under matching organization header | 200; new text read back |

The image remains local and unpublished. Share-link mint/revoke/redeem,
invitations, attachments, operational-model/template changes, and other
secondary actions still need scope and revocation review across HTTP,
resident, and MCP. A04 and C6 remain incomplete.

## Share-link scope and immediate grant readback

Revision `abcab149003bb227607f54759671dcdfd0209886` validates an
explicit target Workspace on App/Track/Entry share-link mint and link revoke.
Redemption intentionally permits a valid Personal Workspace header while a
token grants access to a different Workspace; it rejects an invalid or
revoked current-scope header before consuming the token. Focused tests,
`make verify`, full fresh-worker `make test-postgres`, staged guards, and
commit hooks passed.

Its exact local Core-only image,
`sha256:f5d7cac2851ef9947c439a0e624de50f164c1f10a10f801d11f3a981278196d0`,
returned the expected 403 on wrong-scope mint/revoke and 200 on a valid
cross-workspace redeem. The recipient's immediate Track GET nevertheless
returned **403**. The receipt therefore did not prove usable access. The
service had cached a pre-redeem denial under the auth principal and did not
evict it after writing guest membership and a collaborator edge. Also, using
effective role to decide whether to create the collaborator edge could skip
the link's direct role when inherited visibility already provided a weaker
read role.

Revision `93d00f9fe10052ebfbfb407c9240f1be18f6f166` invalidates both
graph and auth-principal cache keys after guest membership materialization
and link redemption. It checks direct `COLLABORATES_ON` and `OWNS` edges
before granting the link role, preserving an existing direct grant or owner
edge while materializing the promised role over inherited access. Regressions
cover cached denial with the process cache enabled and an existing inherited
viewer who redeems an editor link. Focused tests, `make verify`, full fresh-
worker `make test-postgres`, staged guards, and commit hooks passed.

The corrected exact Core-only image is
`sha256:afe6e3f1bafee71d692d8ac34c721848d29cafcd8ad394da3dce7bfdf3e1c997`,
labeled with the full source revision and booted against the disposable
PostgreSQL data with `/health` 200. A fresh synthetic recipient produced:

| Live HTTP probe | Result |
| --- | --- |
| Private Track read before redemption | 403 |
| Redeem with unknown current Workspace header | 403 |
| Redeem with valid Personal Workspace header | 200; direct collaborator edge created |
| Immediate headerless Track read after redemption | 200 |
| Immediate organization-scoped Track read after redemption | 200 |
| Owner revokes link with matching organization header | 200 |

This image remains local and unpublished. Invitation, attachment,
operational-model/template, and remaining secondary effects still need
cross-transport qualification. A04 and C6 are not complete.
