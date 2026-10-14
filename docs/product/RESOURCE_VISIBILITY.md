# Resource visibility

New Apps and Tracks start **Private**. Creating a resource in a collaborative
Workspace does not implicitly grant access to its members. App creation and
editing offer Private, Workspace and Public; `inherit` is not an App value.
Omitting visibility in the API or agent creation service also selects Private.
Catalog installs default to Private and accept an explicitly chosen visibility.

- **Private:** the owner and explicitly authorized collaborators can access it,
  subject to current membership, exclusions and protected owner governance.
- **Workspace:** eligible current Workspace members receive viewing access.
- **Public:** the existing public visibility policy grants viewing access.

Visibility does not grant editing, administration or approval authority.
Changing visibility uses the existing server-authorized resource update path.
Workspace visibility still requires a collaborative Workspace.

Track inheritance remains an explicit choice for matching a parent App;
it is no longer the creation default. Direct App collaborators continue to
have the existing capped access to contained Tracks. Entries keep their parent
Track access contract; this change does not introduce independent per-entry
public sharing.

Existing saved visibility and grants are preserved. There is no mass privacy
migration. This implements private creation defaults, not a claim that all
outstanding access-control review items have been completed.


## Local qualification — 14 October 2026

The focused API/model/visibility/lifecycle/anchor/scratch suites and generated
capability-map check passed. Native PostgreSQL staging creation and metadata
contracts passed separately. The complete frontend suite passed (314 files,
1,759 tests), as did TypeScript, the production UI build, configured format/lint/
type hooks and staged guards. The browser create dialog showed exactly Private,
Workspace and Public with Private selected; the unsaved dialog was cancelled.

The fresh full default backend suite passed with this visibility amendment.
The full PostgreSQL spike and contract selections also passed; the slow load
probe and database restore drill were skipped, so restore remains unqualified.
The CI-style smoke replay is running at the time of this record. Existing saved
access was not migrated.
