# Native harness and installation qualification — 9 October 2026

This review covers `codex/pr-113-staging`, starting at `f4bb962b`, with the native Integral AI resident harness. It investigates browser crypto failures, confirmation handling, installation secrets, canonical entry transactions, and durable chat recovery. The local package is `integral-core==0.1.1rc16`, with public dependencies `jvspatial==0.1.1` and `pydantic-ai-harness==0.36.0`. This is a qualification record, not a package release or a guarantee across all providers and operating systems.

## Findings and repairs

| Finding | Reproduction and repair |
| --- | --- |
| Chat fails before reaching the harness on HTTP | Against the previous packaged UI, `http://127.0.0.1.nip.io:9140` produced `crypto.randomUUID is not a function`. The same input now returns `Ready.` UUID generation uses native `randomUUID` or browser `getRandomValues`, with proper UUID v4 bits. Authentication retries retain the same request identity; missing browser entropy fails before submission. |
| Verbal approval starts a redundant continuation | A chat approval applied and verified the task, but Prompt Sheet reconciliation then launched another model run. The server now identifies durable decisions owned by this exact native conversation and returns `resume_required=false`. The UI waits for the original stream to settle, records the review receipt, and omits the second run. Card decisions, questions, mixed decisions and unavailable tokens retain their normal continuation. |
| Bootstrap can accept invalid credentials or replace OAuth keys | Source bootstrap now validates every active secret assignment before writing, handles dotenv quoting/comments, rejects duplicates, and preserves valid existing files. Invalid existing secrets produce restoration guidance without rotation or secret values in errors. New files and atomic replacements use private permissions. Docker requires the credential encryption key explicitly. |
| Damaged managed installation can silently acquire a new identity | `integral up` now creates secrets only when `installation.json` does not exist. Existing empty, unreadable, unsupported, incomplete or invalid configurations stop with backup restoration guidance. Restart and upgrade preserve installation identity and all original secrets. |
| Native storage setup errors lack an actionable explanation | Native preparation checks encrypted storage before catalog/model work. Missing storage keys and unreadable harness state receive safe, distinct messages, rather than a provider or pending-confirmation diagnosis. The resident harness remains always-on; choosing a working model is still required. |
| Returned entry retains a released PostgreSQL transaction | Canonical create/update commands now return the entity bound to the caller's graph context. Callers can save provenance and wire transformed records after the owned command commits. Nested commands keep the outer transaction. |
| Nested commands can emit event facts before commit | Entry commands recognize Core's serialized PostgreSQL transaction handle. They persist event facts inside the outer transaction and defer delivery until its owner commits. Tests prove that rollback leaves neither an entry nor an event fact, and that a recovery sweep delivers the committed create/update facts once. |
| Full-suite fixtures exhaust PostgreSQL connections | Raw database fixtures now bind and close the same prime database used by Core commands, eliminating an unowned pool per test loop. The connector audit assertion now observes the canonical deferred event emitter; the creation rollback assertion distinguishes PostgreSQL atomic rollback from JSON fallback. |
| Concurrent JSON runs collide on the graph snapshot | Snapshot rebuild and copying now share a file lock. One earlier rerun collided with another while rebuilding that shared cache; its errors are not counted as successful qualification. |

`crypto.randomUUID` has a secure-context requirement, while `getRandomValues` remains available on HTTP: [MDN randomUUID](https://developer.mozilla.org/en-US/docs/Web/API/Crypto/randomUUID), [MDN getRandomValues](https://developer.mozilla.org/en-US/docs/Web/API/Crypto/getRandomValues).

## Browser scenarios

Tests use real authentication and managed PostgreSQL, with `DEBUG=false`. All submitted application data is synthetic. The selected model is `ollama_chat/deepseek-v4.1-flash:cloud`; browser success does not qualify every model/provider combination.

| Scenario | Observed result |
| --- | --- |
| HTTP chat submission | Exact input `Hello. This is a local HTTP crypto qualification. Please reply with Ready.` failed before the repair and returned `Ready.` afterward. |
| Approve a staged task through the card | One task persisted, the card drained, and the assistant read the task back. |
| Reject a staged task | No rejected task persisted; existing records stayed unchanged; the queue drained. |
| Approve through chat | Input `Yes, go ahead and save exactly that task.` persisted one task, returned its status/due date, and recorded one review receipt without another model reply. Reload retained the result and no pending card. |
| Build a fresh task app | Requested a private app with Projects, Tasks and Personal Assignments, tables, a task status board and due-date calendar. The plan explicitly limited notifications to visible in-app tracking. Input `Approved. Build exactly that setup now. Do not add sample records or reminders.` built the app without another confirmation. |
| Read generated app in the UI | App `Harness Qualification Tasks 2026-10-09` contains the three private lists, initially empty, with the planned view configurations. The four-column status board and October due-date calendar render correctly. An HTTP board-column add/remove round trip persisted across reload. Existing task apps remain present. |
| Backup, restore and continue | The CLI backed up the original installation, restored it into a separate home, and started the restored runtime. The original account signed in. The existing conversation continued with a read-only task query: the three backed-up tasks were present with their original due dates; the rejected title was absent. |
| Upgrade existing managed installation | Installed a clean wheel into an independent virtual environment, restarted the original home, and compared a private fingerprint of installation ID and five secrets. It matched exactly; the browser session remained valid. |
| Fresh Docker installation | Built isolated PostgreSQL/API/nginx services from current source with production-style settings, generated keys and a synthetic bootstrap account. Real browser sign-in selected native Integral AI; the first chat returned `Ready.`. API restart retained the authenticated conversation; a second turn returned `Still ready. Nothing was created or changed.` without crypto or confirmation errors. |
| Fresh managed installation | A blank home started the managed database, API and packaged web UI with generated private secrets, real authentication and native Integral AI selected, without a developer `.env`. |

The generated app is `n.WorkspaceApp.a3975d176d8148e6a5bbb885`. Its Tasks list is `n.Track.cd385a76d87547a4ae8475c9`. The corrected verbal-approval task in the previous smoke app is `n.Entry.f9626b0870d94ce2b127277a`, due `2026-10-15`.

## Automated gates

The stable-source runs completed with these results. Earlier runs that failed due to fixture collisions, pool exhaustion, or mixed old/new imports while repairs were being authored are excluded from the final pass counts.

- `make verify`: passed. Full backend: 4,900 passed, 285 skipped; frontend: 277 files and 1,578 tests passed. Guards, pre-commit, pinned formatting/lint, TypeScript, CI reproduction and reproducible artifact checks passed.
- Broad PostgreSQL suite on disposable pgvector/PostgreSQL 16, with two xdist workers: 5,172 passed, 14 skipped. No failures or errors. Its pgvector collection probe skipped the driver module before the worker database existed; a separate integration run passed all 9 driver tests with the slow-test lane enabled.
- Focused PostgreSQL transaction/chat/approval matrix: 223 passed, 1 skipped. This includes serialized outer commit/rollback, deferred event recovery, returned-entity provenance, transforms and durable chat submission.
- Fresh Docker build and database/API/web health checks, real sign-in, native chat and API-restart continuation: passed. The model was explicitly configured to the test host’s Ollama endpoint.
- Additional CI-style smoke run with `TESTING=1`, an empty environment file and xdist: 603 passed, 6 skipped. Focused installation/key/bootstrap/setup-error checks: 45 passed.
- Clean public-dependency installation and packaged UI: passed locally. The tested wheel SHA-256 is `2e44dac395ebde9d649d212446277e024a9677852e0aee43a36aa6700add6488`. All 640 installed application Python files match the current source; the running managed home uses this wheel.

## Invariants and limits

The repairs preserve I-GRAPH-01/02 (rooted graph participants and Object-shaped event facts), I-CRUD-01 (canonical service mutations), I-WORK-01/02/03/04/05 (lease authority, effect identity, atomic units, idempotent admission and fail-closed approval), and I-HARNESS-01 (rooted tenant-bound native sessions). No graph schema or authority boundary changes. The browser's resume flag cannot bless a token, bypass policy, expand tenant scope, or replay a mutation.

The exact error text and launch method reported by the team were requested but had not been supplied at the time of this review. The HTTP failure and redundant continuation above were independently reproduced. Approve/reject and approved app creation completed; an arbitrary production transcript cannot be certified from those scenarios alone.

Skips include unseeded optional App libraries, explicit deferred/alternate-store paths, the opt-in benchmark and Atlas. Documentation validation passed across 88 maintained files; final pinned formatting checks and all commit guards passed.

Atlas integration requires a configured disposable Atlas environment. Cross-platform signed installers, every BYOK provider, actual email/push delivery, and nondeterministic model wording are separate qualification lanes. No Core PR was merged, and no package was published as part of this sweep.
