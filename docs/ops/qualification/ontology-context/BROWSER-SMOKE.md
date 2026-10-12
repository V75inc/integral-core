# Ontology context local deployment and browser qualification

Date: 2026-10-11. This is an isolated local managed deployment, not a remote
production release. The original checkout and its services remain untouched.

Latest recorded-detail deployment and evidence: [RICH-CONTEXT.md](RICH-CONTEXT.md).

## Earlier topology-only deployment

- Web at this qualification: http://127.0.0.1:61315
- API at this qualification: http://127.0.0.1:61313
  (Initial browser evidence below used ports 63235/63233 before restarts.)
- Core worktree: `/Users/eldonmarks/.codex/worktrees/ontology-context/integral-core`
- Branch: `codex/ontology-context`; main base `9d13b44eb7425b3371f1f2a21f799bdd41dfe02a`.
- JV Spatial dev: `621b77998c1a459dbb1158791e28e57c2fb438fa`, locally installed
  in the dedicated backend venv and pinned in the uv lockfile.
- Runtime home: `/Users/eldonmarks/.codex/worktrees/ontology-context/runtime`.
  Own native managed PostgreSQL, JWT/OAuth/encryption keys and database;
  DEBUG=false, real signup/login, no test authentication bypass.
- React production assets were built with `.ci/bundle_web_assets.sh`.
  No release versions, shared provider keys, original database or original
  ports were changed. No commit, push or published release was made.

From this worktree's `backend/`, use `.venv/bin/python -m app.cli status
--home /Users/eldonmarks/.codex/worktrees/ontology-context/runtime --json`.
Use `up --home <same-home> --no-open --json` or `stop --home <same-home> --json`
to manage only this deployment. After restart, read reported ports: native
management can choose new ports. Private runtime files contain credentials
and are deliberately excluded from tracked evidence.

## Dummy fixture and login

All data is illustrative. Three real authenticated dummy accounts have the
password `OntologySmoke!2026`:

| Account | Role in the test |
| --- | --- |
| ontology.analyst@example.com | Noah Patel; launch workspace guest with App viewer grant |
| ontology.owner@example.com | Maya Chen; owner of the launch workspace |
| ontology.outsider@example.com | Avery Reed; no launch workspace membership |

The fixture has three personal workspaces, one launch App, three tracks,
seven entries and six REFERENCES edges. The launch decision depends on supplier
capacity and reliability validation; supplier capacity links to a backup
supplier. A confidential entry is explicitly denied to the analyst and links
to another sensitive entry. A foreign-workspace entry is readable in the
analyst's own workspace but is never included through a launch-workspace link.

The hidden intermediary test concerns expansion from the launch root: the
separately readable downstream entry can legitimately appear in ordinary
track lists. Home's existing all-readable-workspace aggregation may show the
foreign record independently; ontology captures stay strictly intra-workspace.

## Browser evidence

Real browser flows covered login, Home, workspace selection, Apps, App detail,
track detail and a read-only entry dialog. Browser requests went through the
same-origin server with the ordinary login session. A local diagnostic page
at `/ontology-smoke.html` executes the assertions through visible buttons;
it is a qualification aid, not a new product graph visualization.

[Analyst receipt](browser-analyst.json): all 18 assertions passed. The native
ontology capture returned four readable nodes and three typed edges in 106 ms
on this busy local host. It excluded denied intermediaries, their expansion
paths, foreign-workspace relationships, entry bodies and arbitrary custom
fields. Other assertions exercised anonymous 401, denied/foreign root 403,
caller-supplied workspace 400, budget truncation, viewer write 403 and the
owner-only personal-workspace default. App/Home counts omitted denied entries.

[Outsider receipt](browser-outsider.json): both assertions passed after real
outsider login. The active session was the outsider and capture of the launch
workspace returned HTTP 403. The outsider Home showed only its own empty workspace.

[Product App screenshot](browser-app.jpg), [read-only entry](browser-entry.jpg),
and [browser assertion screenshot](browser-smoke.jpg) preserve visible results.

## Resident-model trial

The browser chat ran against local Ollama `gemma4:e2b`, with no paid or shared
provider key. The first prompt did not discover the tool and incorrectly claimed
it was unavailable. A follow-up explicitly asking for `search_capabilities`
succeeded: the model discovered and invoked `integral_get_graph_context` through
the ordinary read broker under the analyst's launch-workspace scope. Dispatch
recorded success and 232.2 ms tool latency (distinct from overall model latency).

The final answer named exactly the three readable stored links: launch depends
on supplier confirmation, launch requires reliability validation, and supplier
confirmation is mitigated by a backup supplier. It separated stored links from
an inference section and included no denied or foreign record names. The UI
showed `Worked · 2 steps · gemma4:e2b`. [Chat transcript](browser-model.txt) and
[chat screenshot](browser-model.jpg) preserve the trial. This verifies one live
model/tool integration path; reliable unprompted discovery and broader reasoning
quality are not established by this small-model trial.

The subsequent [plain-language qualification](PLAIN-LANGUAGE.md) used the user's
exact ordinary question with no IDs or tool instructions. After iterative skill,
schema-disclosure and retrieval-scope repairs, two fresh `gemma4:latest` runs
passed the decision-answer criteria. One captured ontology; the repeat selected
relationships with identical stored paths. Both excluded denied/foreign records
and stated that stored links do not confirm readiness. Earlier failed trials
are preserved and explicitly rejected. Local response times were about three
minutes, so these runs do not establish production UX performance.

## Privacy corrections found by live testing

1. Home hydrated entries after track access without applying per-entry policy.
   It now uses the canonical strict accessible-entry resolver before previews
   and counts, preserving direct-grant precedence over inherited exclusions.
2. App cards counted denied entries and App track lists lacked per-track checks.
   Counts now use canonical accessible-entry counts; track policy applies
   before any track card is emitted.
3. Personal-workspace discovery could choose a guest's other personal workspace.
   It now queries owner membership plus personal kind with a native limit of one.
   A dedicated guest fixture proves a separate owned default is created.

## Latest source verification

| Check | Result |
| --- | --- |
| Full backend suite after plain-language fixes | 4,995 passed; 286 skipped; zero failures/errors |
| CI-faithful smoke (`make verify-ci`) | 668 passed; 7 skipped; zero failures/errors |
| Native PostgreSQL context/database/search-scope tests | 30 passed; no skips |
| Retrieval/read-dispatch suite including slow HTTP tests | 52 passed; no skips |
| Focused graph/Home/App tests | 50 passed; 6 skipped |
| Staged guards, pre-commit and pinned formatting | Passed |

[Machine-readable test totals](test-results.json) preserve the final suite counts.

The final full backend run took 404.822 seconds; CI smoke took 91.322 seconds.
PostgreSQL tests used disposable
isolated test databases under this deployment's own server. Initial frontend
verification passed 1,583 tests; no frontend source was changed afterward.
The initial wheel hash in RESULTS.md predates the privacy and plain-language
fixes. The final reproducible artifact gate passed, including out-of-source
wheel import and all packaged skill compliance checks. Current wheel SHA-256:
`da4ed4baf8afd49acc3440972ea36f4340cf4d350eb10accceca1c907c8f6ae6`.
This uses the prepared integration interpreter; it is not a clean public
dependency resolution or published release gate.

## Scope of evidence

This proves local deployment and deterministic access/relationship behavior.
Local latency and SQLite probe gains are not production guarantees. Captures
remain bounded best-effort multi-read evidence, not atomic snapshots. The full
PostgreSQL suite, remote deployment, production concurrency/load and a broad
model/provider quality matrix remain unqualified. See [integration results](RESULTS.md)
and [context contract](../../../backend/workspace-graph-context.md).
