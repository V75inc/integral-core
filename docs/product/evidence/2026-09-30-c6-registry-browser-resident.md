# C6 registry, browser and resident qualification — 2026-09-30

## Decision boundary

Frozen executable source: `eee9b514a7778d72bfb3c5f7247b84c0404cc461`. Later documentation commits describe this
source and do not substitute their SHA for its artifact identity. **C6 remains
incomplete:** independent human architecture and Product Owner decisions are
pending; the broader acceptance matrix has not been promoted by this targeted
journey. This record supersedes the continuation packet's pending registry and
browser/resident items. Qualification publication is not production release.

## Root causes and remedies

- The API image lacked the separately distributed SDK. Install it explicitly.
- A macOS PostgreSQL bind mount caused excessive filesystem-event churn and
  Docker VM failure; virtual-disk exhaustion was a separate problem. Disposable
  build cache was reclaimed and qualification PostgreSQL moved to a named volume.
  PostgreSQL test bootstrap now has bounded connect, command and close deadlines.
- Resident App tool aliases were advertised as Core capabilities. Aliases now
  bind one authorized persisted App operation from the turn snapshot; ambiguity,
  undeclared operations and inaccessible Apps do not produce aliases. Caller
  arguments cannot override the bound App or capability.
- App iframe scripts inherited the Core CSP through `srcDoc`. A scoped signed
  frame grant now serves the declared entry through a route-specific hash CSP,
  sandboxed without same-origin, network access, forms or external scripts. Core's
  global script policy remains enforced. Context updates after entry hydration
  are sent through the existing bridge, including when ready precedes data.
- Container build contexts exclude host dependencies, secrets and evidence.

These preserve I-EXT-01 and I-SUBSTRATE-01 (external packages use published
contracts), I-GRAPH-01/02 (no new persistence primitive), and server-bound
principal, workspace, lifecycle and access checks. Self-contained HTML is
qualified; external frame assets/scripts are not supported by this scoped policy.

## Retained artifact identity

Artifacts are retained under `/private/tmp/integral-c6-final-artifacts`.

| Artifact | SHA-256 |
| --- | --- |
| `integral_core-0.1.1rc11-py3-none-any.whl` | `4898813c310697b3efeea7a3bcaf940883c846bf0ef5db37e5216c66a6de2f8a` |
| `integral_sdk-0.2.0-py3-none-any.whl` | `8e07100c02a310afe1de7f7bbb012a2a1548057ab44dd7fdd9d7dddec890509f` |
| `asset-register-1.0.0.tar.gz` | `a0de55cad63d0fcbc099da06c4f7ef0a4917fcfbd746d8b555ed3ae0d8431cdf` |
| Ed25519 public-key file | `969b247b439f95c8b3ae11790febaef10a8fa58b63d50ef56b6bc01c2c803614` |

The exact retained wheels were installed into a fresh venv; public SDK and ASGI
imports succeeded outside the checkout. Independent-artifact qualification also
loads a signed extracted App through the production loader.

## Registry and deployment evidence

Local browser deployment uses private Harbor project
`registry.v75inc.dev/integral-core-qualification`, linux/arm64:

| Image | Registry digest | Config ID |
| --- | --- | --- |
| `api` | `sha256:56450660d155c85f322a5e6ed10d4f7665f3b74bc2032cdcd5c058ed9f5e8e8a` | `sha256:5b6a6ce8d5b1edfe45a940ae49c275d356e11444eed4fcfe9a15395acc1743c2` |
| `web` | `sha256:eba803d706c5fa8a70ef71ea8a3e3b8167843e401713ecd1fa2883ad0de6a65b` | `sha256:1892c062bd1953215fd05993140410e948db98aee46e4d7071ef88c80a3f0c1f` |

Both OCI revision labels match the frozen SHA. After publication, local candidate
images were removed and their config IDs verified absent, then pulled by digest;
config IDs and labels matched. Cached layers may remain in the local engine.

An independent fresh GitHub runner built, published, pulled by digest and deployed
both images with fresh PostgreSQL. All three jobs passed in
[run 36781913902](https://github.com/V75inc/integral-core/actions/runs/36781913902).
The run retains registry metadata, sanitized deployment inspect, readiness,
HTML and logs. These independently built GHCR images have separate identities:

- API: `ghcr.io/v75inc/integral-core-qualification-api@sha256:3b6e153eafdfde9a46986801933e253063ff6afe2d4680486ec672bc6cad31ba`
- Web: `ghcr.io/v75inc/integral-core-qualification-web@sha256:7f2570dd604ac57b606429ef3890c1b9caba68018329d2f70cf2ebfdaa19cbf8`

The browser deployment is Compose project `integral-cdfdddd-browser`, API 14100,
web 19100 and PostgreSQL 15434. PostgreSQL 16.14 uses managed volume
`integral-cdfdddd-browser_c6_qualification_pg`. API Python 3.11; host test Python 3.14.3,
Node 23.10.0, Docker 28.2.2. There are no source or SDK mounts. Only the extracted
App and public verification key are mounted alongside named data volumes.
Platform OpenAI/Ollama credentials are absent. The synthetic user's encrypted BYOK
configuration selects GPT-4.1 for the live resident journey. JWTs, frame grants,
private keys, encryption keys and model credentials are excluded from this packet.
Sanitized deployment record SHA-256:
`bc342592695e3279c4a06fbb774c552f9b2070f519731cda9a18a4803cbf58f9`.
This hashes the deployment record, including capture metadata, not secrets.

## Candidate gates

All commands below completed with exit 0 on the frozen source, 2026-09-30.
Logs are retained in the worktree's ignored `.qualification-evidence/` directory.

| Gate | Result | Retained evidence |
| --- | --- | --- |
| `make verify` | Pass; guards, formatters, types, CI-faithful smoke, artifact gate, backend and frontend suites; 213 frontend files/1274 tests | `c6-eee9b51-verify.log` |
| `make test-postgres` | Pass on fresh xdist worker databases, 12 explicit skips | `c6-eee9b51-postgres.log` |
| `make verify-core-only verify-contract` | Pass | `c6-eee9b51-explicit-contracts.log` |
| `make verify-independent-artifacts` | Pass: Core, SDK, clean install and signed extracted archive | `c6-eee9b51-independent-artifacts.log` |
| Exact retained wheel imports | Pass outside checkout | `c6-retained-wheel-import.log` |
| Populated live fixture backup/restore drill | Pass: 115 nodes, 145 edges, 216 objects, 2 embeddings; counts and identity match; scratch DB removed | `c6-eee9b51-backup.log`, `c6-eee9b51-restore.log` |
| Registry pull/deploy | Harbor digest deployment healthy; independent GHCR fresh-runner deployment pass | registry identity/deployment JSON and GitHub run above |

Skips are not passes. PostgreSQL skips cover the opt-in benchmark, missing Atlas
service, retired unseeded CRM/Projects packages, one backend-specific facet path,
and two pre-existing deferred endpoint E2E tests. The JSON lane separately skips
PostgreSQL-only cases; those run in the PostgreSQL lane. Existing warnings remain
in logs. The restore drill covers database identity/counts; attachment storage
bytes require a separate volume backup.

## Browser and resident journey

Synthetic workspace `n.Workspace.939d23f26bcd4dcdbfaed02e`, App
`n.WorkspaceApp.434da202d8e3424999c69460`, Assets Track
`n.Track.c9bf0d4b1aea435fab7224af`.

1. On the final Harbor digest deployment, open Asset detail before entry hydration.
   The context refresh fills the current asset after the iframe handshake.
2. Register `C6 Registry View Asset` / `C6-REGISTRY-VIEW-001` through its native
   custom-view form. The governed operation succeeds. Persisted entry:
   `n.Entry.81b631f03c4f428c9a17efde`.
3. Ask live GPT-4.1 to register `C6 Registry Resident Asset` /
   `C6-REGISTRY-RESIDENT-001` and verify with declared `available_assets` query.
   Persisted entry: `n.Entry.42cf3dc2ef2048c3a489f702`.
4. Reload: Feed and Inventory show six entries including both final registrations.
   Custom-view availability reports five rows, matching its configured page limit 5.
   The UI page count is not presented as an unpaginated six-row query.
5. Independently call authenticated extension HTTP and public MCP declared query
   with limit 200. Their six rows and object references are exactly equal and their
   applied scope matches. The expanded live resident query result matches those
   six rows exactly and has a successful broker read receipt. Unauthenticated MCP
   returns 401.
6. Replay the resident's exact logical operation key through HTTP and MCP. Both
   return the same successful durable operation receipt with `replayed:true` and
   the same asset output. No seventh asset is created.
7. Final browser console error inspection returns no errors.

Resident run: `35a44f2c-0ab1-4d59-bf5f-5540c1504b3e`.
Operation step: `capability:c39ac7594753e69f`.
Read step: `capability:8bf40c1c49e5f6ed`.
Durable operation receipt:
`o.OperationExecutionReceipt.ab87821ac38cf03025bce30b39d13c1ec32b8bb98070006881bc8f6f7170fb12`.

**Resident limitation:** one prompt produced an initial repeat-guard/fallback
attempt and a corrective attempt, visible as two assistant messages. The first
used unnecessary generic tools; the final attempt used the declared operation
and governed query, replayed the same logical write and returned grounded data.
This is effect/receipt qualification, not a first-attempt efficiency pass or the
separate held-out model exam. No additional model prompts were used to conceal it.

Retained browser screenshot: `c6-eee9b51-browser-inventory.png`.
Sanitized expanded transcript: `c6-eee9b51-resident-browser-transcript.txt`.
Machine comparisons: `c6-eee9b51-parity.json`,
`c6-eee9b51-resident-parity.json`, `c6-eee9b51-operation-replay.json`.

## Pending decisions

- Independent architecture reviewer: **pending**, reviewer/date/decision unset.
- Product Owner acceptance and release decision: **pending**, reviewer/date/decision unset.
- Broader A01–A16 outcomes retain their individual status in the acceptance ledger.
  This selected App journey does not prove every access-revocation, crash,
  projection, lifecycle or unavailable-provider scenario on the deployed SHA.
- External held-out live-model budget exam remains separate and unqualified here.

The review packet is concrete and reproducible; only the named human reviewers
can provide their decisions. No human signoff or production deployment is inferred.
