# Staging CI cleanup and integration review

Target: `codex/pr-113-staging` → main through PR 116. Main is not merged.
Reviewed inputs: staging `7bf8ac31`, PR 122 `c7fdfe09`, and PR 123 `a8a1bca4`.

## CI causes and repairs

The failing frontend job in Actions run 37811223996 asserted the App Home
refresh-failure status before the query observer delivered its React update.
PR 122 already supplies the bounded asynchronous assertion; retain it.

The PostgreSQL model-receipt tests required storage encryption but depended on a
developer .env. The isolated no-.env run also exposed the same dependency in
three route-receipt variants. A shared, explicitly imported fixture now generates
a temporary key for the model-request, mandate-receipt and model-admission
storage tests. Production encryption remains fail-closed; no application default
or workflow secret is weakened.

CI previously accepted PRs only against main/dev/ab/dev2/prod. Add the staging
branch to that base filter so stacked PRs receive the same required jobs.

## PR 122 decision

Integrate the reviewed candidate with durable native chat disabled by default.
Its accepted input, attachment revision, host-control authority and tenant
checks remain in place. Replay reads committed events through fresh shared-store
transactions. Cancellation releases only matching admission state. Public
transaction operations are serialized on the held connection, and the heartbeat
retry accepts only the same live execution authority.

The full PostgreSQL spike/contract CI lane passes on a dedicated pgvector/pg16
container with no developer .env. An additional 34 native worker/effect-fence
PostgreSQL cases pass. All 1,550 frontend tests and the production frontend
build pass. The complete local gate is required before the integration commit.

The candidate is source integration, not promotion of durable chat, public
mandates, billing or independent-process recovery to production qualification.
Existing browser evidence is retained as historical evidence; no new live
provider or App-journey browser acceptance is claimed by this review.

## PR 123 disposition

Prepare separately against the updated staging branch and leave it open.
Resolve inherited overlap with PR 122, repair ownership/grant cleanup and
service/API layering, preserve current privacy policy and approval boundaries,
then run the full source and PostgreSQL CI gates before updating its branch.
Its final description must include the later ownership/privacy scope rather
than only the original QA list. See the preparation report on PR 123.

The user's original checkout and its uncommitted .env.example change are
preserved. Review/testing use isolated managed worktrees and a disposable test
database.
