# C6 qualification continuation — 2026-09-30

## Decision state

**C6 is not complete.** This continuation records partial candidate-specific
qualification and explicit blockers. It does not replace the earlier
2026-09-28 record, declare a release, or approve publication.

## Candidate and local images

- Source revision: `b79fd057d502a42047d032af201771cbb375f3a4`.
- The candidate tree hash is `770e74a9dbd1ae42c1f4361c0ceace3b9d00924f`;
  it matches the parent qualification checkout's `cdfdddd` tree. The local
  merge commit adds no source-tree delta over that tree.
- API image `integral-core-c6-b79-api:candidate`: image ID
  `sha256:2cb31529dd57318046a56520ff6b281f458b57b4e2ecc294aa948a179517f308`,
  `linux/arm64`, revision label equals the full candidate SHA.
- Web image `integral-core-c6-b79-web:candidate`: image ID
  `sha256:f9b4cd015520dd9805cd4d5709dcb8538a92ded080f2237b65570fc8e855b319`,
  `linux/arm64`, revision label equals the full candidate SHA.
- Both exact-tagged local containers were running and healthy at 2026-09-30
  19:26 UTC. `/health` reported `healthy` and database `connected`.
- This is local image-build and deployment evidence only. The images have no
  registry digest or registry pull evidence. The configured GHCR package was
  not available to the current credential, whose GitHub token lacks
  `write:packages`; registry publication remains blocked.
- The signed Asset Register archive and public verification key were mounted
  read-only into this disposable deployment. The separately built
  `integral_sdk-0.2.0` wheel was unpacked and mounted read-only at `/app/sdk`;
  the API runtime resolved `integral_sdk` from that wheel. The SDK was not
  baked into the Core API image, so this deployment does not prove that the
  image alone can execute arbitrary SDK-dependent Apps.
- The deployed database is the existing disposable C6 browser database. It
  was not recreated for this continuation.

## Initial candidate-specific evidence (before recovery)

| Check | Result | Scope and limitation |
| --- | --- | --- |
| Repository gate (`make verify`) | **Pass** | Ran on the candidate source tree on 2026-09-30 with the existing Python 3.14.3 environment and frontend dependencies. All 16 guards and hooks passed; formatting, backend type checks, built-wheel check, CI-faithful smoke, frontend lint/type checks, frontend suite (213 files / 1,274 tests), and full backend suite passed. The optional backend skips are listed by their declared fixture conditions. ESLint reported 394 warnings and zero errors. |
| Declared-query transport parity tests | **Pass** | Two focused tests on this candidate passed: dashboard preview, extension HTTP, resident dispatch, MCP dispatch, and authenticated mounted Streamable HTTP MCP were compared against the same query fixture, outputs, references, and workspace scope. This is in-process/ASGI test evidence, not a live network proof. |
| Browser App install | **Pass, partial** | On the signed-in disposable C6 browser deployment, the signed Asset Register became available and installed; the UI reported one installed and zero skipped/failed, and the App showed all five declared tracks. |
| Browser App typed mutation/readback | **Blocked** | Generic `New Asset` submission was refused with the intended protected-field policy message directing writes to the App's typed operation. The browser did not complete a typed operation or show a persisted App-created Asset. The Inventory view remained at zero entries. |
| Live authenticated extension HTTP | **Not qualified** | A manually minted synthetic token was rejected with HTTP 401. That token omitted the required JTI claim, so the response is not evidence of an endpoint defect. No live request was made using or exposing the browser's session credential. |
| Runtime SDK import | **Pass, deployment overlay** | Container Python resolved `integral_sdk` from `/app/sdk`, mounted from the separately built SDK wheel. The operation invocation itself was not proven by that import check. |
| Live network MCP transport | **Not run** | Contract-level mounted Streamable HTTP MCP passed; candidate deployment route was not qualified with an accepted session. |
| Fresh Postgres lane | **Blocked / incomplete** | Started `make test-postgres` against the disposable C6 Postgres service on host port 15434 with two xdist workers. Workers connected but produced no test progress for over eight minutes; host `psql` inspection and the API health check also stopped responding. The run was interrupted, so there is no pass result. The attempted API container restart remained pending because Docker stopped responding. No database or volume was deleted. |
| Image registry/deployment | **Partial; runtime recovered state unknown** | Exact candidate API and web images were built and initially deployed locally and healthy before the Postgres lane. The later stall left Docker unresponsive; container state could not be rechecked after the restart request. No registry push, registry digest, or pull-by-digest test was possible with available package-write scope. |

The earlier Postgres and browser results remain tied to their documented
earlier revision. The present source tree matches the `cdfdddd` parent tree,
and this continuation passed `make verify`, but the fresh Postgres lane did not
complete. Do not carry earlier green rows forward as a completed C6 result.

## Root-cause investigation and repair — same-day recovery

The Docker virtualization log records the Linux VM stopping gracefully at
19:43:34 UTC while its host backend remained alive. TCP and CLI connections
were accepted but the VM services could not answer. The logs do not establish
what initiated that shutdown. `docker desktop restart --timeout 45` restored
engine access; Postgres was started and checked before API and web startup.

The Docker filesystem was independently found at 100% usage (183 GB capacity,
178 GB used and no available space). The macOS filesystem had ample space;
checking only the host disk missed this condition. Pruning unused build cache
and dangling, untagged images reclaimed 6.368 GB and 107.5 GB respectively.
The running database, tagged images, and volumes were retained. Docker now
reports 109 GB available (38% usage). This removes a concrete resource
constraint; it does not prove disk exhaustion initiated the VM shutdown.

The final bootstrap recheck reproduced the VM shutdown at 20:28:38 UTC.
This time the initiating error is explicit: at 20:28:11 UTC Docker's `fs`
service failed because a removed PostgreSQL file event under
`/private/tmp/integral-cdfdddd-browser-postgres/base/...` blocked for 60 seconds.
The qualification override had replaced the shipped Compose named volume with
a macOS bind mount. That routed high-churn worker database files through
Docker Desktop's filesystem-event bridge. Freeing disk space alone did not
remedy this failure.

The browser database was logically backed up with `pg_dump -Fc`, restored into
Docker-managed volume `integral-cdfdddd-browser_c6_qualification_pg`, and the
API/web restarted. `docker inspect` confirms a `volume` mount at
`/var/lib/postgresql/data`; no macOS Postgres bind mount remains. The original
host directory is retained. The named-volume stack is healthy and the live
HTTP/MCP parity checks pass again with both original synthetic assets.
This returns the local deployment to the storage topology already used by
the shipped Compose files. The full Postgres lane passed there with the same 13 explicit skips. A
post-suite worker-bootstrap repeat also passed (exit 0), and the API
remained healthy afterward.

Two repository repairs address failures exposed by the investigation:

- PostgreSQL test bootstrap now bounds connection establishment to 10 seconds,
  commands to 30 seconds, connection close to 5 seconds, and the bootstrap to
  55 seconds plus up to five seconds for connection cleanup. Failures identify the host, port, and worker database without
  printing credentials. A fault-injection server accepted TCP but never
  completed the PostgreSQL handshake: setup failed in 10.07 seconds with the
  intended diagnostic rather than leaving workers waiting indefinitely.
- The API Docker image installs the public `integral-sdk` package alongside
  Core and checks its version/import during construction. The replacement
  runtime imports SDK 0.2.0 from `/opt/venv/lib/python3.11/site-packages`, with
  no SDK mount or `PYTHONPATH` override. The signed App archive remains separate.

Recovery image `integral-core-c6-recovery-api:candidate` has local image ID
`sha256:7db6ff2770731687004761f050a71da66c5a294cfa9c6fa35623809c9e0474b5`.
It was built from the repair working tree based on `b79fd05`, not a frozen
release commit. It is diagnosis/repair evidence and has no registry digest.
The web image remains the original `b79fd05` image above.

| Recovery check | Result | Retained evidence and limits |
| --- | --- | --- |
| API and database | **Pass** | Replacement image `/health` reports healthy and connected; public SDK import succeeds from installed package. |
| Typed HTTP operation and replay | **Pass** | `register_asset` created `C6-IMAGE-001`, entry `n.Entry.a2389a321bc1484689381e4d`. Second identical request returned the same execution receipt with `replayed: true`; no duplicate. |
| Browser persisted readback | **Pass** | Reloaded signed-in Feed and Inventory show `C6 Packaged SDK Asset` and the earlier `C6 Qualification Asset`. Screenshot: `.qualification-evidence/c6-recovered-inventory.png`. This is HTTP mutation followed by browser readback, not proof of browser-origin typed mutation. |
| Live HTTP / deployed MCP query parity | **Pass** | Authenticated TCP requests to extension query and mounted Streamable HTTP MCP match two fixture rows, object references, and applied workspace scope. Unauthenticated MCP returns 401. `.qualification-evidence/c6-recovery-live-parity.json`. No model call or provider credential was used. Resident live parity remains open. |
| Post-suite bootstrap repeat | **Pass** | Repeated `tests/test_model_credential_indexes.py` with two xdist workers, recreating their databases after the full suite. Exit 0 and healthy API afterward. `.qualification-evidence/c6-managed-volume-bootstrap-repeat.log`. |
| Fresh PostgreSQL suite | **Pass on repaired storage topology** | `make test-postgres` exited 0 on the named volume, with 13 fixture/optional skips. `.qualification-evidence/c6-postgres-managed-volume.log`. The original pass and subsequent failure remain retained separately; no successful result erases the intervening infrastructure failure. |
| Repository gate on repair tree | **Pass** | `make verify` exited 0: guards/hooks, formatting/type checks, built wheel, CI-faithful smoke, full backend, frontend (213 files / 1,274 tests). The final staged guards/hooks also pass. `.qualification-evidence/c6-recovery-verify.log` and `c6-recovery-staged-guards.log`. |

The earlier `hook_misconfigured` result belonged to the request made before
SDK installation. Reusing its idempotency key replayed that failed result.
A new request key succeeded; no handler-normalization repair was justified by
this failure. JWTs used for local HTTP qualification were short-lived, included
JTI, and were never printed or saved.

## Required reviews — decision fields intentionally blank

### Independent human architecture review

- Reviewer name and role: ______________________________
- Candidate SHA and image IDs reviewed: ______________________________
- Review of Core / SDK / independently packaged App runtime boundary:
  ________________________________________________________________
- Review of remaining browser and live transport evidence gaps:
  ________________________________________________________________
- Findings and required follow-up: ____________________________________
- Decision: **Accept / Reject / Accept with listed conditions**
- Date and reviewer acknowledgement: ______________________________

### Product Owner release review

- Product Owner: ______________________________
- Candidate SHA and evidence record reviewed: ________________________
- Scope and known limitations accepted: ______________________________
- Decision: **Approve technical completion / Return for further work**
- Publication decision (separate): **Not authorized by this record**
- Date and acknowledgement: ________________________________________

These decisions must be made by the named humans. No agent may fill them in
or infer acceptance from the technical evidence.

## Remaining C6 exit work

1. Freeze the repaired candidate SHA and recheck deployment health on its
   rebuilt image. The repository and managed-volume Postgres gates passed; bind all remaining release evidence to one SHA.
2. Provide a least-privilege GHCR publisher credential or authorized CI path;
   publish the candidate images, record immutable registry digests, and pull
   them into a clean deployment.
3. Rebuild the repaired SDK-inclusive image from the frozen candidate SHA and
   qualify it from registry digests rather than only the local repair image.
4. Complete browser-origin typed-operation and live resident journeys. Live
   extension HTTP mutation, browser persisted readback, and HTTP/MCP query
   equality are now proven on the repair deployment; full transport parity
   still requires the remaining journeys on the frozen candidate.
5. Rerun all mandatory C6 gates against one frozen SHA and its registry image
   digests; update this record with commands, timestamps, and retained logs.
6. Obtain and record the independent human architecture review and Product
   Owner decision above. Keep publication as a separate authorization.

## Superseding frozen qualification

The pending registry and selected browser/resident items above are resolved for
`eee9b514a7778d72bfb3c5f7247b84c0404cc461` in the [final qualification record](2026-09-30-c6-registry-browser-resident.md).
That record preserves separate Harbor and GHCR identities, actual read/write
receipts, suite skips and pending human decisions. This historical diagnosis
is not the current artifact identity. C6 remains incomplete.
