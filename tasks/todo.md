# QA fixes

- [x] Q12 Chat cancellation
- [x] Q06 Tag publication/readback
- [x] Q09 Tag validation errors
- [x] Q08 Tag picker focus
- [x] Q01 Chat paste/caret
- [x] Q03 Entry menu clipping
- [x] Q02 Kanban status deletion
- [x] Q04 Saved template deletion
- [x] Q05 Approval presentation
- [x] Q07 Remove Enhance this view
- [x] Q10 Compact tag filters
- [x] Q11 Global tag filtering
- [x] Full frontend/backend suites
- [x] Browser smoke and final review

## Qualification evidence

- Full default backend suite: exit 0, four workers, isolated test stores. PostgreSQL-only cases skip in this lane; focused PostgreSQL runs below cover changed persistence commands.
- Full frontend suite: 337 files and 1,923 tests passed. The existing admission-cap test now uses controlled stream completion instead of a short timing window.
- PostgreSQL model/batch regression run: 58 passed. Final tag-recovery and Kanban authority/record checks: 9 passed. Feed filtering before pagination also passed against PostgreSQL.
- Final focused backend cancellation/fresh-read checks: 19 passed. Approval presentation: 23 passed. Frontend cancellation runtime: 8 passed; admission: 17 passed.
- Production frontend build and TypeScript check passed. ESLint: zero errors, 464 existing warnings.
- Pre-commit formatting, import, lint, typing and attribution checks passed; all 16 staged guards passed. Artifact verification passed, including reproducible wheel builds and import outside the source tree.
- Browser: repeated tag opening, Track-qualified global tag options, portal menu containment/keyboard dismissal, long native draft editing and a compact 25-tag filter row. No browser console warnings/errors on the isolated fixture.
- CI-faithful smoke result is recorded in the PR validation evidence after its final run.
- No version bump, merge, package publication or deployment in this QA patch.

## Validation limits

Browser checks used isolated components with stubbed API data; HTTP/command checks used real isolated JSON/PostgreSQL stores. Provider-backed production conversations, Google Docs clipboard integration and deployed acceptance remain separate checks. No named QA prompt or live business records were used in regression fixtures.
