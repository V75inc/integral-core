# rc27 review and release qualification

## Candidate

Review baseline: `be4ea82c2f04a050fc58c9d4fcc92ede18408e63` (main, rc26).
QA branch before this review: `fb0db8be5fc5683fd69b8a52cf756fad21249a4b`,
PR #160. The review includes its entire main diff and the subsequent local
chat, design, skill disclosure, Home starter and clipboard fixes.

This document records candidate qualification. It does not establish package
publication or deployment. The next unused candidate on TestPyPI was rc27
when checked during this review; rc26 was already published.

## Review

Reviewed cancellation admission/settlement and transcript retention, staged tag
receipts/readback, Kanban option/default/record guards, model deletion authority,
feed filtering before pagination, Enhance retirement, tool disclosure, design
identity/config validation, error presentation, Home starters, tag/filter/menu
controls, title-preview lifecycle, local links and composer behavior.

Preserved contracts: rooted graph writes (I-GRAPH-01/02), current resource and
workspace authority and role boundaries (I-ROLE-01), exact approved intent and
receipts (I-APPROVAL), and independent chat cancellation/admission fences
(I-CHAT-01/PAR-01). Tool disclosure is presentation; it grants no new authority.

Two review findings were corrected:

1. Clipboard conversion flattened nested lists and lost whitespace beside
   formatted text. It now preserves list nesting, ordered starts, inline
   whitespace/styles, code whitespace/fences and safe link destinations.
   Literal Markdown characters are escaped; script/style nodes are ignored.
   The composer stores editable Markdown source; it is not a visual rich-text
   editor and does not promise fonts, colors or arbitrary document layout.
2. The cancellation body used route-local shape validation. It now uses the
   typed API schema, preserving empty-body callers and rejecting malformed,
   unknown, empty and non-string targets before dispatch.

Actual local browser paste showed the heading, bold text and nested ordered/
unordered list source correctly. The draft was cleared without submission.
The existing session renewed in place through Continue working.
The inactivity prompt now asks "Are you still working?", explains "Your work
is still open. Continue when you’re ready.", and keeps the Continue working
button. Password confirmation remains explicit when signing in is required.
The interrupted-actions footer was removed at product review.

## Validation

Completed before the final broad suites:

- All 16 staged source guards and pre-commit checks, including mypy: passed.
- Independent Core/SDK/external-App build and clean install proofs: passed.
- Backend audit: no known vulnerabilities; frontend policy passed with its
  existing reviewed build-tool exceptions and moderate notices retained.
- Cancellation request/API tests: 29 passed.
- Final Node 24 frontend suite: 340 files, 1,951 tests passed (including the
  additional selection-replacement regression). Production build/type checks
  passed; existing bundle-size warnings remain.
- Core-only lane: passed.
- Six isolated cold-worker PostgreSQL race reproductions: passed (one serial,
  five with independent worker processes and fresh disposable databases). The earlier
  adapter deadlock remains historical evidence in the chat-recovery QA report;
  one passing rerun alone is not proof that a concurrency defect is fixed.

Installed-wheel acceptance used a clean Python 3.12 environment, `integral init`,
packaged `integral web` on 19008 and API on 19041, with a new PostgreSQL 14
`integral_rc27_review_smoke2` database on local port 55447. It did not use
production data. Initial smoke setup used the generated distro's default
PostgreSQL settings and an unsupported test email domain; these were corrected
in the disposable deployment configuration before acceptance.

The real provider proposed Release Checklist, then approval built and verified
it in one attempt (three public calls) with a two-sentence reply. Independent
scoped API readback confirmed one Tasks Track, a Task type with the required
Planned/Done status, the default Release Board with matching canonical columns,
and zero entries. The browser opened the same board through the reply's local
link. This wheel used rc26 metadata for pre-bump qualification; it is not a
published rc27 artifact. Final versioned artifact validation is separate.

Full-suite and PostgreSQL outcomes are recorded below when complete. Release
version changes follow successful qualification.

Native PostgreSQL backup/restore also passed for the populated wheel-smoke
workspace: `pg_dump` and transactional `pg_restore` into a second disposable
database preserved content hashes and counts for all 27 nodes, 32 edges and
157 objects. The backup is restricted to mode 0600 outside the repository.
This supplements the normal Docker-dependent restore test, which still skips
because Docker storage is full; it does not establish a Docker/pgvector restore
proof.

The complete `make test-postgres-ci` run finished with exit 0. The normal
opt-in checkpoint restore and two slow-history skips remain, together with
the Docker backup/restore infrastructure skip described above. No test was
weakened or suppressed. Final lint passed with zero errors and the existing
464 warnings.

Final full `make verify` passed all checks. The full backend suite reported
7,004 passed, 961 skipped and 18 warnings in 22:32. The frontend suite passed
340 files and 1,951 tests; production build, type checks, source guards,
artifact qualification and CI-faithful backend smoke checks passed.

Final `make verify-pr` passed backend, frontend and CI-faithful Postgres
checks. The explicit restore test and two slow-history checks remain opt-in;
the Docker backup rehearsal skipped because the local Docker storage is full.
Independent PostgreSQL backup/restore evidence above passed. No tests were
weakened or suppressed.

The candidate version is now `0.1.1rc27`. Final versioned artifact
qualification is run separately after this metadata change.
