# Native connector and public research follow-up

Base: `codex/pr-113-staging` at `9f860e2b`, continuing Core PR #119. No release or version change. The local smoke distribution uses read-only source overlays from this review worktree.

## Findings and generic repairs

1. Native provider construction used only the static Core tool catalogue. MCP tools mounted into the workspace registry were absent from native discovery even though their connector was healthy. The native adapter now projects mounted MCP schemas reachable through the caller's personal or shared rows. Canonical keys replace duplicate row aliases. Credentials, handlers and auth state are omitted. Tools still cross the existing snapshot broker, connector resolution, policy and dispatch boundary; disclosure grants no authority.
2. Read classification comes only from the vetted local connector catalog. An unknown remote tool remains execute-classified even if it advertises `readOnlyHint`. This permits an approved public read on a no-save turn while still refusing writes.
3. Portable resident skills outside the embedded action directory were absent from the Core skill summaries used by native skill projection. Discovery now includes both filesystem locations, preferring an embedded declaration on a name collision. The existing `web-research` procedure is projected through native Skills instead of adding a domain router or user-utterance instructions.
4. The MCP dispatch staging condition recognized row-addressed keys only. Canonical write keys now resolve the caller's row and stage the same approval card, with the resolved connector bound in its payload. The regression runs through the dispatch seam and proves neither form invokes the remote before approval.
5. The vetted Serper connector now offers `fetch_web_page` alongside `search_web`. Fetching uses Core's existing initial URL validation, DNS pinning and redirect guard, rejects credential-bearing URLs and unsupported content types, bounds decoded response bytes/text, strips executable markup, and returns final URL, UTC retrieval time, truncation and untrusted-content provenance. It does not execute JavaScript. Errors/empty pages cannot become successful evidence.
6. Search results carry a retrieval timestamp and distinguish snippets from fetched pages. The existing research procedure prefers the connector fetch, requires substantive content, labels failed/snippet-only evidence, and uses tool retrieval dates instead of searching for today's date.

Preserved: backend-authoritative workspace/principal binding, I-CON-04 dual invocation policy, existing staging and receipts, I-SUBSTRATE-01/I-EXT-01 domain separation. No App identifiers, business policy, inferred user approvals or model overrides were added to Core.

## Browser evidence

Disposable local UI `http://localhost:9108`, native Integral AI binding, default `ollama/deepseek-v4.1-flash:cloud`. Existing operator Serper key reused without printing it. Install from Settings -> Connectors used an empty credential field and server configuration. The UI showed the connector healthy and one tool; refresh after the fetch addition showed two tools.

Run `d76f28f6-1748-4913-8424-27a5ccd255dc` established native Serper execution: ten successful search calls with broker receipts and no workspace writes. It exposed a grounding failure: page-access dates were attached to snippets despite no direct page retrieval, and several searches tried to discover today's date. This run is not research acceptance.

Unchanged ordinary research request in a fresh conversation after skill/fetch repairs: `9adc1658-f7e2-4bd6-ac30-70b6036c1f46`, 28.1 seconds, six tool calls. `web-research` loaded, two five-result searches and two page fetches succeeded. Fetches returned 7,828 and 5,316 characters, `truncated=false`, final source URLs, retrieval timestamps and succeeded broker receipts. The answer cited those pages and separated vendor claims from performance/customer evidence. No workspace write or outreach occurred. Screenshot: `/tmp/integral-venture-smoke-20261007/evidence/native-serper-fetched-research.jpg`.

This qualifies one live public research case, not general reliability, legal applicability, all provider errors, or enterprise qualification. UI aggregate usage was 32.5k tokens, cost unavailable. Per-connector credential override/rotation and broad failure/recovery cases remain open.

## Validation

Focused connector projection, portable skill discovery, page-fetch, Serper and MCP approval regressions: 21 passed. Existing native broker suite also passed, including a no-save connector read and refusal of an unknown write. Initial full gate caught the existing exact allowed-tool assertion after adding the fetch tool; its expected contract is updated to include that actual tool, retaining all prior checks. A new full `LITELLM_MODE=PRODUCTION make verify` is required on the final staged changes before commit. Do not cite the earlier, now superseded gate as validation of this full change set.

### Unreadable page and protocol-error comparison

Initial 404 case `6b8a4686-dde6-424b-b6e0-b6d2f990cc6f` correctly left the claim unknown and made no writes, but its fetch receipt incorrectly said succeeded. The MCP client preferred text/structured payloads before checking `isError`. It now checks protocol error first, raises a bounded generic failure without echoing arbitrary remote error bodies, and lets the existing proxy/dispatch/broker failure path record the outcome. Four focused regressions cover text, structured and empty error payloads plus successful structured payload preservation.

Fresh repeat `1a309af6-5bec-4ba6-a4b5-9e9a6d86be1d`, 21.9 seconds / four tools: loaded research skill, one failed fetch with a **failed** broker receipt (`internal_error`), one exact-URL search fallback. The answer left the claim unknown and refused to treat the placeholder snippet as page evidence. No writes/outreach. Screenshot `/tmp/integral-venture-smoke-20261007/evidence/native-serper-fetch-failed-receipt.jpg`. The generic `internal_error` label is less informative than a normalized external-source error; improving that diagnostic remains a follow-up, not a success-receipt workaround.

The in-flight intermediate full gate was deliberately terminated after this additional protocol fix changed the validation scope. Its handle exited 143; it is not a passed gate. Final staged validation uses `/tmp/integral-native-web-research-final-verify.log`. Focused protocol/proxy/resident alignment coverage passed 41 tests. No commit may precede the final full gate passing.


### Final gate result

The final `LITELLM_MODE=PRODUCTION make verify` completed with `verify: all checks passed` on 2026-10-07 (`/tmp/integral-native-web-research-final-verify.log`). This covers the staged substrate guards and hooks, formatting/types, wheel/import check, CI-faithful backend smoke, all 256 frontend test files / 1,473 tests, and the full backend suite. Optional PostgreSQL/infrastructure and unseeded-App skips remain; this is local validation, not GitHub CI or release qualification.


## Follow-up: constrained view identity

After the Business package moved prescribed views to Track scope, browser navigation exposed an empty Test materials view despite two retained records of its declared type. The type has manifest key `test_asset` and display name `Test Material`. Core list filtering and response type projection derived the identity from the display label, disagreeing with the canonical EntryType key already used for writes and EntryType discovery. The generic repair uses stored manifest identity first, retaining a name-derived fallback for legacy nodes. Listing constraints still exclude unrelated types; there is no App-specific alias or widened query. Focused regressions and a new full gate are required before committing this additional change.


Browser comparison after the identity overlay: the Test materials tab now shows the two retained rows with Superseded status. Screenshot `/tmp/integral-venture-smoke-20261007/evidence/venture-materials-table-upgrade.jpg`. Two focused regressions pass for canonical identity, legacy fallback, listing constraints and the compatibility filter. The first test drafts had an incorrect function import and a malformed mock context; both were repaired before successful focused execution. The full gate `/tmp/integral-native-view-identity-verify.log` is pending. No commit or broad acceptance claim yet.


The additional view-identity repair's full `LITELLM_MODE=PRODUCTION make verify` finished with all checks passed on 2026-10-07 (`/tmp/integral-native-view-identity-verify.log`). Two focused regressions also pass; the full gate includes 256 frontend files / 1,473 tests, CI-faithful smoke, wheel/import and full backend suite with standard optional infrastructure skips. The test mock correction occurred during the guard phase before test collection; final focused execution and full-suite execution used the corrected test. Browser material visibility passes on the local overlay. Table column selection across different schemas and deterministic lifecycle-validation error classification remain separate generic gaps.
