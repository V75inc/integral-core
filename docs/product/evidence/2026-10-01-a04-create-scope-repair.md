# A04 App and Track creation scope repair

**Source revision:** `7895c3c790be079f7e2d7f5bcf304273f0127c46` (draft PR #99, stacked on PR #97)
**Date:** 2026-10-01 UTC
**Disposition:** the observed App/Track create gap is repaired and locally
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

The local image was not published to a registry. This checks selected HTTP
and MCP boundaries for two new users, not browser UI, resident tool calls,
revoked membership, or every resource effect endpoint. A04 is not marked
passed. C6 needs a subsequent full candidate freeze and qualification.
