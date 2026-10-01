# Core acceptance ledger

**Purpose:** the single release-evidence record for Integral Core.

**Status:** **C6 is not complete.** The latest repair candidate passes a live
resident declared write, browser readback, selected HTTP/MCP parity and replay,
independent registry deployment with file persistence, and a populated
database plus attachment-volume restore. A selected ordinary Core browser/API
journey also passed with no global model provider configured. A later live A04
probe failed: Track creation accepted a foreign workspace header. Broader
A01–A15 gaps and independent human architecture and Product Owner decisions remain open.
**Latest repair candidate:** `da33c68f1bcdbf0e3191203b1f631bae21ffd84a`;
see its [candidate evidence](evidence/2026-10-01-c6-file-volume-and-resident.md).
**Last full ledger table below:** `bb3b1e0b11bc80db697d7187dc9b7e2212789dc1`;
see its [historical registry/browser record](evidence/2026-09-30-c6-merge-qualification.md).
The table is not a qualification of the changed repair candidate.
Draft PR #99 has a separate [A04 repair record](evidence/2026-10-01-a04-create-scope-repair.md)
through source `b06d134bf0dbf64c6429f09fb3281eefcdd4dd05`. Selected
create-scope, revocation, browser aggregate, resident proposal, primary
resource update/delete, sharing/Comment, share-link, and invitation paths pass
against local Core-only API images, including immediate post-accept readback.
Other mutation families, every effect boundary, and a frozen web/API deployment
pair remain unproven; none of these selected results
updates the frozen C6 table.
**Supported topology for qualification:** Core API and web bundle with
Postgres. SQLite and JSON stores support local development and reconciliation;
they do not establish multi-worker command, lease, or recovery guarantees.

This ledger supersedes the claim-oriented tables in
[RELEASE_CANDIDATE.md](RELEASE_CANDIDATE.md). The test-to-criterion mapping
remains in [ACCEPTANCE_TEST_MAP.md](ACCEPTANCE_TEST_MAP.md). A test can be
useful development evidence without qualifying the frozen candidate.

## Latest candidate selected evidence

The current candidate's [exact artifact identities, commands, registry run,
resident trace, and independent restore](evidence/2026-10-01-c6-file-volume-and-resident.md)
qualify selected technical journeys only. The ten-row HTTP/MCP comparison and
idempotent replay followed a live GPT-4.1 declared App write. A separate
Postgres and file-volume restore preserved the earlier nine-row fixture, its
attachment bytes, and its durable work receipt. The provider-free browser/API
journey on the same deployed API is a selected A02 pass. These are not a
complete A01–A15 disposition. The historical table below belongs only to
`bb3b1e0`.

## How to record a candidate

1. Create a commit with the intended source and documentation state.
2. Record its SHA and build hashes below. Never substitute a branch name.
3. Run every mandatory command against that source/artifact combination.
4. Record the command, environment, timestamp, result, and retained artifact
   or trace for every row. `Skipped`, `not run`, and `blocked` remain visible.
5. Run the human/browser journeys against the same deployment topology.
6. Review the completed ledger. A release decision is separate from this
   evidence and requires explicit authorization.

## Candidate identity and environment

| Field | Required value for a qualified candidate | Current record |
| --- | --- | --- |
| Git revision | Full immutable SHA | `bb3b1e0b11bc80db697d7187dc9b7e2212789dc1` |
| Core wheel | Filename + SHA-256 | `integral_core-0.1.1rc11-py3-none-any.whl`, `3d22c41e1af95a9efaa1a27508ec2239c314813e9a21984202265e14cc7b817d` |
| SDK wheel | Filename + SHA-256 | `integral_sdk-0.2.0-py3-none-any.whl`, `10b64c92f58e9068cad9ceb3328d408b610149f1437d1740bb4271d51ab9eab6` |
| Independent App archive | Filename + SHA-256 + signature key identity | `asset-register-1.0.0.tar.gz`, `a0de55cad63d0fcbc099da06c4f7ef0a4917fcfbd746d8b555ed3ae0d8431cdf`; public-key file SHA-256 `969b247b439f95c8b3ae11790febaef10a8fa58b63d50ef56b6bc01c2c803614` |
| Container images | Image digests for API and web | API `sha256:5fc9d0758afb646b01f8db7c3a5e3f10bd38099bed3164ff6e83e51cf56d20aa`; web `sha256:32a3ccd2b708ac194b8681c2b2bbe36903bffed449c65c769aca17eeb24dbf69`; isolated Postgres `sha256:fa3d9bb7ee77f5c1f0bfb009a9df30243c040896825f3033b09a77101bb2ca95` |
| Python, Node, Docker, Postgres | Exact versions | Host Python 3.14.3, Node 23.10.0, Docker 28.2.2, Postgres 16.14; API image Python 3.11 |
| Configuration | Non-secret settings digest; model/provider state | [Sanitized deployment identities](evidence/c6-merge-qualification.json); platform provider keys absent; synthetic encrypted user BYOK GPT-4.1 |
| Fixture / backup identity | Seed or backup digest and dataset version | Synthetic Asset Register workspace, eight assets, Compose `integral-cdfdddd-browser`; fresh Postgres worker databases and populated live-fixture restore drill |

## Mandatory gates

C6 fills this table once, for one frozen SHA. A green run on another revision stays outside the table. Skipped is not a pass. The external live-model exam is not a row here.

| Gate | Command or journey | Owner | Candidate result | Evidence to retain |
| --- | --- | --- | --- | --- |
| Repository gate | `make verify` | Release | **Pass**; backend/frontend, guards, types, CI and artifact lanes | `refusal-verify.log` |
| CI-faithful smoke | `make verify-ci` | Release | **Pass**, included in repository gate | Same log |
| Core-only boundary | `make verify-core-only` | Platform | **Pass** | `refusal-contracts.log` |
| Contract lane | `make verify-contract` | Extension | **Pass**; explicit skips retained | Same log |
| Postgres proof | `make test-postgres` | Persistence | **Pass**; fresh two-worker databases; 12 explicit skips | `refusal-postgres-corrected.log` |
| Built Core | Artifact and clean-install lanes | Release | **Pass**; exact retained wheel import also passes | `merge-independent-artifacts.log` |
| Built SDK | SDK artifact lane | SDK | **Pass** | Same log |
| Independent App | Signed extracted archive lane | Extension | **Pass** | Same log |
| Browser acceptance | Signed-in App custom view, registration, reload, Feed/Inventory | Experience | **Pass for selected App journey**; broader ordinary-use matrix remains explicit | Final evidence record |
| Transport parity | Browser/resident effects; HTTP/MCP queries and logical replay | Execution | **Pass for UI/HTTP/MCP declared register_asset/available_assets**; eight equal rows and one replayed effect. Resident read repair passes; resident declared-write qualification remains open | Final evidence record |
| Registry/deployment | Harbor digest pull and independent GHCR fresh deployment | Release | **Pass**; frozen revision verified | GitHub run 36793131441 and final record |
| Restore drill | Populated live fixture dump into scratch database | Persistence | **Pass**; counts/identity match, scratch removed; file volumes separate | `merge-restore.log` |
| Human review | Architecture and release review | Independent reviewer / Product Owner | **Pending** | Final review packet; no approval inferred |

All rows refer to `bb3b1e0b11bc80db697d7187dc9b7e2212789dc1`, run 2026-09-30. Logs and immutable registry identities are indexed in the [final record](evidence/2026-09-30-c6-merge-qualification.md).

## Finish-line acceptance matrix

| ID | Required outcome | Responsible area | Candidate status | Evidence requirement |
| --- | --- | --- | --- | --- |
| A01 | Core installs cleanly and boots without commercial Apps | Release / platform | Unproven | Clean install, first-login, API, and generic-view trace |
| A02 | Ordinary Core use remains available with the model provider unavailable | Platform / experience | Unproven | Browser and API proof with provider disabled |
| A03 | Module boundary violations fail the build | Platform | Unproven | Guard result and reviewed allowlist |
| A04 | Bad scope and revoked access fail at every effect boundary | Identity / execution | Unproven | UI, HTTP, resident, MCP negative tests |
| A05 | Fields stay stable across rename, nulls, and platform/business collisions | Information | Partial evidence only | Shared form, view, dashboard, and agent-query fixture |
| A06 | Concurrent command plus crash creates one effect and no duplicate receipt | Execution / persistence | Partial evidence only | Postgres concurrency and injected-crash trace |
| A07 | Approved revision executes once; correction, expiry, and cancellation report accurately | Execution / resident | Partial evidence only | Receipt and continuation/recovery tests |
| A08 | Build resumes after restart without duplicate objects | Applications / execution | Partial evidence only | Restart and requirement-ledger proof |
| A09 | Exact query and every rendered view agree above page limits and date boundaries | Query / experience | Verified | [2026-09-21 query and rendered-view parity evidence](evidence/2026-09-21-a09-query-view-parity.md) |
| A10 | Populated schema alteration preserves bindings or fails before unsafe change | Information / applications | Partial evidence only | Migration fixture and rollback/rejection trace |
| A11 | External unknown outcomes reconcile before retry | Execution | Unproven | Provider correlation and retry trace |
| A12 | Independent App has identical enforcement across UI, HTTP, resident, MCP | Extension / execution | UI/HTTP/MCP operation/query qualified; resident read repair passed; resident declared write and broader denial/lifecycle matrix incomplete | [Current receipts and limitation](evidence/2026-09-30-c6-merge-qualification.md) |
| A13 | Upgrade preserves customization; pause/uninstall fence capabilities and work | Applications / extension | Partial evidence only | Populated upgrade, pause, restart, and uninstall drill |
| A14 | Restore reproduces records, edges, attachments, package identity, and work | Persistence / release | Partial evidence only | Restore inspection against the fixture digest |
| A15 | Active documentation is coherent, linked, and executable | Documentation / all owners | Partial evidence only | Link checks and independent trials |
| A16 | External live-model exam meets its own budgets | Intelligence / release | Unproven; does not block the platform | Versioned model configuration and retained evaluation traces kept outside Core |

## Public extension acceptance map

The historic Foundation sprint AC-01 through AC-14 are mapped to concrete
tests in [ACCEPTANCE_TEST_MAP.md](ACCEPTANCE_TEST_MAP.md). They are supporting
evidence for A01, A04, A06, A12, A13, A14, and A15 above. They do not replace
candidate-specific artifact, browser, restart, and recovery qualification.

AC-14, the sprint's publish digest gate, is implemented and recorded in
[the WP-10 closure](evidence/2026-09-22-wp10-sprint-closure.md). That closes
the public-developer sprint package. It does not mark the mandatory gates
above as passed, and it does not publish a release.

## Known limitations carried into the next candidate

- One durable transaction/effect-receipt authority does not yet cover every
  UI, HTTP, resident, MCP, and extension operation.
- Query, form, saved-view, and dashboard semantics are not yet proven to use
  the same field and projection resolver above page limits.
- Schema publication, backfill, record updates, and App lifecycle evolution
  still require their durable-plan and recovery proof.
- The extracted-archive journey and populated database plus attachment-volume
  restore are recorded for the latest repair candidate as selected journeys;
  the complete A14 fixture and recovery matrix remains open.
- The external live-model exam and human acceptance are pending. A model miss does not change Core.

See [CORE_FINISH_STATUS.md](CORE_FINISH_STATUS.md) for the ordered build
program and work-package exits.
