# Dev read and chat repair — 2026-10-20

## Observed deployment

Business dev pipeline 15753 deployed commit `4a8955d4bdd90f45c91c694dd726fe3b72d37817`.
The running API reports Core `0.1.1rc28`; its release source is `5d0f60553764c1350362e35be002a922064fc400`.
Chrome smoke used the existing QA workspace. Apps and record views worked, but
Track lists and record detail requests took 8–15 seconds during the session.
A read-only record/index question never returned an answer before cancellation.

Server observations separated the causes:

- PostgreSQL ping: about 1 ms; 1,309 graph Nodes and 2,304 Edges.
- Track inventory: repeated candidate checks of the same parent Apps across 79 Tracks.
- The generic record query was refused with `app_domain`; the installed App's own
  registered read tool was available. Recovery incorrectly recommended only the
  separate declared-query interface.
- One physical model request remained open for 148.09 seconds before user
  cancellation. Other successful requests in that chat took roughly 1–4 seconds.
- A separate authorized index-status check proved encoder/store readiness and
  complete coverage for the test Track: 1 current record, no pending/failed jobs.

## Repair

Candidate discovery shares in-flight parent visibility checks only within one
inventory call. Final raw canonical admission, membership, exclusions, lifecycle
and revocation checks remain fresh. No reusable authorization cache is added.
The insights skill and refusal hint expose the registered-App read-tool route,
with declared queries as the alternative and a concise limitation if neither is
available. They do not bypass App read contracts.

The model adapter enforces the total request deadline after stream headers as
well as a configurable 30-second silence limit. Only pre-output failures use the
existing bounded retry policy. Later failures remain uncertain outcomes; no tool
effect is replayed and no usage is invented.

The chat trail uses one clipped line for observable preparation, discovery,
reading, checking, reviewing and writing. It does not show private reasoning or
tool arguments. Resumed work clears stale retry notices; pending cancellation
retains its status.

## Read comparison on the dev dataset

Candidate source ran in a separate diagnostic Python process, using read-only
queries against the same dev database. The running API source was not changed.
Each comparison returned the same 79 Track IDs.

| Repetition | Baseline ms | Candidate ms | Baseline DB operations | Candidate DB operations |
|---|---:|---:|---:|---:|
| 1 | 2323 | 521 | 4291 | 835 |
| 2 | 1580 | 414 | 4291 | 835 |
| 3 | 2365 | 399 | 4291 | 835 |

This is a service-level diagnostic, not an end-to-end production latency claim.

## Local browser qualification

An isolated PostgreSQL database, API on 19045 and frontend on 19011 loaded the
current Business Projects package with fictional seed records. Existing local
services and Business's primary checkout were preserved.

The original record/index question completed using the App's registered read
tool, returned the expected Platform API hardening record and complete index
coverage. Its returned record link opened the correct modal in the same tab,
without changing the local host or creating a new tab. A second current-state
question completed as well.

Chrome sampled the live trail every 200 ms. Observed transitions included
Preparing your response, Finding relevant information, Reviewing the results,
Reading the information you can access and Writing your answer. Every sampled
label used a single non-wrapping line; the second answer finished after about
15.4 seconds of sampling.

## Automated validation

- Python 3.12 default backend run: 7,039 passed, 970 skipped, one outdated
  assertion about the intentionally changed refusal wording failed. Updated it
  to assert the registered-tool route and declared-query fallback; the complete
  dispatch/broker modules then passed all 60 tests. No runtime source changed
  in response to that wording failure. A fresh full CI run remains required.
- Focused real encoder/pgvector, inventory authority and model transport lane:
  60 passed. The transport module was rerun after relaxing its test-only timing
  margins and passed all 47 tests.
- PostgreSQL canonical-write/index-intent rollback contracts: 2 passed in an
  isolated test database.
- Frontend: 340 files / 1,957 tests passed; final focused activity/runtime
  checks: 23 passed. TypeScript and production build passed, retaining the
  existing bundle-size warning.
- Pinned pre-commit hooks, mypy, formatting, lint, source drift, capability-map
  generation and attribution tooling checks passed.

These fixes
require a new Core artifact and Business pin update before dev rollout. No
production promotion or live source patch was performed.
