# C6 resident operation repair candidate

**Source revision:** `d4977382b612ca7ee8db052fc4d497b8337efb61` (PR #97)
**Date:** 2026-10-01 UTC
**Disposition:** selected technical journeys pass; C6 remains incomplete.

## Artifact and release evidence

- `make verify`, `make test-postgres`, `make verify-core-only verify-contract`,
  `make verify-external-asset-register`, and
  `make verify-clean-install verify-sdk-artifact` passed on this source.
  Postgres used fresh per-worker databases. PR #97 backend, frontend,
  independent-artifact, and Postgres CI checks passed; its ordinary Docker
  build job was skipped.
- The signed, extracted `asset-register-1.0.0.tar.gz` archive has SHA-256
  `f49cdac85266f02be9f3a2f2e65517cec519ddad1564763934be741f6bff0fcc`.
  The installed App was upgraded from this archive; its persisted resident
  skill requires `integral_invoke_app_operation` and binds the exact installed
  App ID.
- [Registry workflow run 36798509034](https://github.com/V75inc/integral-core/actions/runs/36798509034)
  published the exact-revision API and web images and pulled both digests on
  a separate fresh runner with a fresh Postgres volume. API digest:
  `sha256:bcbdd77442734f91fe32eb9f4cc923c61909e994eb32ef982a5bb42ffe0c34a5`;
  web digest:
  `sha256:e6ff989bbb97fcf3fac74ae72df9567948c0a3db735bafa85543f7a3ad73f9c2`.
  Revision labels and deployment health were checked there.
- The local browser stack used separately built images from the same revision,
  with image IDs `sha256:ab2a192a69729572c60ac301877579e7d670af3b1c2fae3197514679bec05125`
  (API) and `sha256:45ec8f1b1ecbb8bddd19f30ecccb860363c868e5a2632455ce2f92131e6d0065`
  (web). The signed App was mounted read-only, without a Core source mount.
  Local GHCR pull was unavailable with the host's registry credentials; the
  fresh runner supplied the independent digest-pull proof.

## Resident, browser, and transport journey

The signed-in browser used a synthetic account in workspace
`n.Workspace.939d23f26bcd4dcdbfaed02e` and installed App
`n.WorkspaceApp.434da202d8e3424999c69460`. A GPT-4.1 resident turn was
asked to register asset tag `C6-D497-001`, title `C6 d497 Resident Asset`,
category `it_equipment`. It followed the App skill and called
`integral_invoke_app_operation`. The first attempt used `tag` instead of
`asset_tag` and failed tool validation. The second omitted the required
idempotency key and received `bad_request`. It corrected both in the same
turn; the third call succeeded through declared operation `register_asset`.
The durable receipt was
`o.OperationExecutionReceipt.d66302f58f754e74572d60881dbdbf81252b40aa106256415c0effedd2d76201`,
with `status=succeeded`, `replayed=false`, and entry
`n.Entry.475a5791e2b84b27a67fc085`. The successful run ID was
`4e81fe00-79e2-4aad-80e5-870e17cca9ee`. This was a successful corrected
turn, not a first-attempt success.

After browser reload, the Track Feed showed nine entries and the new record
with its asset tag. Track and view **Improve this** controls were icon-only,
with accessible button names and native tooltips. On this deployed stack,
HTTP `available_assets` and MCP `integral_governed_query` returned the same
nine rows, object references, and workspace scope; the resident-created
entry was present. Unauthenticated MCP returned 401. Replaying the resident
operation's idempotency key through both HTTP and MCP returned `replayed=true`,
the same receipt ID, the same output, and the same entry. No second effect
was observed.

## Qualification boundary

This record closes the previously failed resident declared-write journey and
checks its browser readback and selected HTTP/MCP parity on this revision.
The older `bb3b1e0` ledger table is a different candidate and is not silently
transferred here. Full A01–A15 candidate-specific denial, lifecycle,
recovery, restore, and documentation review remain open where the acceptance
ledger says so. Independent architecture and Product Owner decisions have not
been given. C6 is not complete and no production release is asserted.
