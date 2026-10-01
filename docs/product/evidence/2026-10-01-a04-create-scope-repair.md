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
