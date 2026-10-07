# Native harness follow-up — execution evidence

## Scope

Remediation of the unpublished Core review at `/tmp/integral-core-venture-smoke-fix/docs/backend/qualification/NATIVE_HARNESS_BROWSER_FINDINGS_2026-10-07.md`, against `d542ffe5` on `codex/pr-113-staging`. That checkout remains intact. Plan: [native-harness-follow-up-plan](2026-10-07-native-harness-follow-up-plan.md). Existing database, populated workspace and long conversations were retained. Browser deployment: UI `localhost:9107`, API `localhost:4407`; provider `integral_native`, configured DeepSeek default. No founder-specific policy entered Core.

## Implemented

| Item | Repair | Evidence boundary |
| --- | --- | --- |
| Host continuations | Authored text is bound/reset; absent host utterances use Pydantic AI's `None` prompt. Current scoped decisions supersede proposal-time history. Server-patched terminal chat receipts restore executor IDs after restart. Rollback and partial-failure facts survive. | Browser approvals and rejection; exact scope, missing/foreign receipt, current-state precedence and restart contracts. |
| Typed identity | One destination/type guard for creation and filing. Compare exact normalized title within verified types; shared prose is not identity. Unknown legacy types remain conservative. Explicit separate creation remains staged and discloses retention of existing records. | Focused staging regressions; mixed-type live qualification recorded below separately. |
| Markdown | Maintained public Markdown parser removes formatting syntax only for moderation; words, code and obfuscated characters are retained. Original stored Markdown is unchanged. | Authenticated/public create/update/comment regressions and a live clean bold-heading create. |
| Context | Content-free per-physical-call segment sizes, schema fingerprint/repetition, timing, token/cache/cost evidence. Compact only verified terminal previews; retain IDs/outcomes. Public library context compaction starts at 16,384 tokens, keeps five tool pairs and loaded capability contracts. Refusal guidance directs declared queries or explicit unavailability, while preserving permitted known-ID reads. | Physical-call export, projection-size regression and same-history live comparison. No private dependency patch or lexical intent gate. |
| Recovery/UI | Per-run library cancellation tokens prevent late hooks cancelling the next turn. Preserve terminal assistant-row status. Polling errors retain reviews, coalesce refreshes, honor backoff and reject foreign-thread results. Action/poll continuation deduplication is per review item identity, not repeated wording. | Cancellation/parallel-stream/polling regressions; genuine API outage, expired reviews, stale-revision refusal, Stop and immediate follow-up. |

## Browser results

All requests below used ordinary language; no skill/tool names were supplied except the App's existing user-facing name.

| Scenario | Observed result |
| --- | --- |
| Stock update proposal | “In the Bike Repair Shop, change the brake-pad stock count from ten to nine.” Correct pending update; no write before approval. |
| Notes create/moderation | Asked to save `Weekend checklist` containing `**Next test**: inspect the bicycle tyres.` Correct staged create and later saved with the Markdown intact. |
| True expiry across two Apps | Left both proposals untouched for their actual ten-minute lifetime. Both expired. Stock remained ten; independent Notes view showed zero entries. No expiry write or automatic re-proposal. |
| Outage with pending review | Stopped only the API while runs were idle. The approval review stayed visible with a retry message during 502 failures. Restoring the API cleared the error and retained the pending review. Rate limiting remained enabled. |
| Stale approval | While a ten-to-nine stock proposal waited, edited stock to eleven through the real entry dialog. Approving the stale revision failed visibly. No overwrite; subsequent acknowledgment accurately read eleven/revision three. |
| Fresh stock approval | New eleven-to-nine proposal applied. Assistant and independent entry dialog read nine/revision four. Historical receipt body and attachment were retained. |
| Notes approval | Created the note; canonical receipt and independent entry dialog verified the saved body. The initial acknowledgment honestly exposed its inability to enumerate an App without a declared record query. |
| Rejection | Rejected a proposed note rename. No title change or resubmission. Independent entry readback still showed `Weekend checklist`. |
| Stop/recovery | Stopped “Summarize my notes.” Sent “Did stopping that change anything?” immediately. New turn succeeded; readback showed unchanged title/body/revision one. Earlier stopped row stayed interrupted. |
| Restart and renewed request | After rebuilding/restarting, repeated the rename in the same long conversation. Correct known-ID update proposal, no broad-query retry loop. Approval acknowledged `Tyre checklist`, revision two, unchanged body. Independent dialog confirmed title/body. |
| Confirmation race | Before the latest approval there were two historical “Updates applied” notes; afterward there were three, i.e. exactly one new notice. Old duplicated transcript rows were retained as historical evidence. Hook regression covers action/poll ordering and a new review with identical wording. |
| Dialog dismissal | Closed both independent record dialogs through their Close buttons; returned to the underlying track normally. |
| Third unrelated App | “Set up a small home library…” produced one mixed list with Book/Reminder types; “Yes, build it.” built the saved design without repeated review. Added a Book, then a separate same-title Reminder. Both retained their types; the Book retained author and shelf status. A repeated Book request read both records and clarified second-copy intent without staging a duplicate. A distinct book with the same author/status staged and saved without a false duplicate warning. Independent Library List readback showed all three records, correct author/status on both Books and blank book-only fields on the Reminder. The Reminder preview explicitly disclosed retention of existing records and its clean bold text was saved. |

### Live run references

- Cold stock proposal: `d5a48682-a663-4fb2-9f20-6027a399d408`.
- Cold Notes proposal: `557fe81a-3001-4470-96d1-3042369e4f49`; expiry acknowledgment `83198762-2304-4f82-98da-f81f1a592c63`.
- Stock post-cancellation recovery: `200fc5b5-695b-4b13-ba8c-9b43afd6421c`.
- Stale-update outcome acknowledgment: `40ebefe0-9736-4b8a-b38b-f40da3b1fce3`.
- Fresh stock proposal: `eda4f19b-5284-42a5-9f6e-0aea0e572982`; applied readback `aae996d3-9f9e-4e77-a770-4d4dffc2a986`.
- Initial Notes approval readback: `73224215-b181-4109-b60f-fff1391fbbd6`.
- Costly rename before final fixes: `a24d44c7-5237-4eaf-a2f3-6004b2d68063`.
- Rejection acknowledgment: `74b697f9-7c87-4b51-83ba-f629b43786a1`.
- Stop: `5d608633-5040-4a94-9c11-2c1dff3333a4`; successful next turn `01dcb6bc-c100-4f4a-87bc-f55bec28f36a`.
- Post-restart rename: `92690bb4-d24b-427f-a48f-3e12b4c2884e`.
- Home Library: App `n.WorkspaceApp.796925075eca49de9da2ec2e`, mixed Track `n.Track.59e4b6284e194214ad249b02`; independent browser screenshot `library-mixed-record-readback.png`. Content-free run export: `/tmp/integral-harness-followup-library-metrics.json`.

## Context measurements

Canonical physical-request observations, not an estimate of distinct prompt tokens:

| Existing Notes conversation | Model calls | Input tokens | Output tokens | Tool calls | Peak input | Host duration |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Initial rename | 17 | 501,113 | 9,581 | 19 | 43,929 | 67.0 s |
| Repeated rename after final repairs/restart | 3 | 55,995 | 2,033 | 1 | 27,231 | 13.7 s |

Observed input total fell about 88.8%. This is an indicative same-conversation comparison, not a controlled benchmark: intervening turns added known-record readback and history grew. Physical model count includes library compaction calls, so that cost is not hidden. The final two operational requests each still carried 47,936 tool-schema characters; the second was detected as repeated. Those are **characters**, not tokens. Repeated loaded schema overhead remains measurable work for a later capability-surface optimization; the fix does not make it zero.

DeepSeek via Ollama supplied token evidence but no trustworthy provider-dollar/cache evidence in these requests. LiteLLM's observed zero estimate was retained separately with `cost_source=unavailable`, not promoted to a free provider cost. UI correctly remained “cost unavailable.” The regressions cover reported provider cost/cached-token preservation; this deployment cannot prove bill reconciliation for a vendor that sends no authoritative dollar value.

Content-free exports: `/tmp/integral-harness-followup-live-metrics-final.json`. Browser screenshots: `/tmp/integral-harness-followup-browser/` (`pending-review-outage`, `stale-update-refused`, `stock-independent-readback`, `note-independent-readback`, `rename-restored-history`, `rename-independent-readback`). Temporary artifacts are evidence on this machine, not portable CI artifacts.

## Qualification limits

This is broader Core qualification than the input review, not full reliability certification. No live multi-principal/BYOK vendor matrix, unknown mutation recovery during a process kill, or exhaustive unrelated-App partial-batch failure matrix is claimed. Contracts exercise isolation and failure semantics. UI continuation deduplication is bounded per mounted client; durable exactly-once delivery across independent clients is not claimed. Existing server work admission still fences concurrent native execution.

One Home Library acknowledgment added unnecessary commentary about what the user clicked despite accurately reporting the saved records and decision source. Host outcomes are now correctly sourced; uniformly concise model narration remains unqualified. No lexical output rewriting was introduced to hide this observation. Public moderation paths and unknown legacy type cases have source regressions rather than live public-form smoke coverage.

The installed Notes fixture intentionally exposes no declared record query. Broad enumeration remains unavailable; permitted known-ID lookup works. Do not weaken that App boundary to make a smoke test appear successful. Founder skill revisions/response contracts remain Business responsibilities.

## Gates and delivery

Final `make verify` passed (`/tmp/integral-harness-followup-verify6.log`): staged drift/skill/manifest/CSP guards, pinned formatters, ESLint errors, TypeScript, clean wheel construction, CI reproduction, full frontend suite (256 files / 1,473 tests) and full backend suite. Existing environment-dependent skips and tracked lint warnings remain visible in that log; they are not live infrastructure qualification. The final source was kept stable while the gate ran. `make capability-map` and `git diff --check` also passed.

Both API and web images built and deployed together; `/health` reports a connected database. Browser results above distinguish tests before and after the final receipt/context fixes. Source-level focused regressions separately passed for identity, moderation, physical observations, cancellation, restart receipts and polling/continuation races. Package construction, local suites, browser evidence, CI and release publication remain separate gates.
