# A04 workspace-bound creation scope repair

**Initial source revision:** `7895c3c790be079f7e2d7f5bcf304273f0127c46` (draft PR #99, stacked on PR #97)
**Expanded source revision:** `ad05e14f23a906b658b4ee366dc48905be0b7b03`
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
