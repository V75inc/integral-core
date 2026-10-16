# Workspace ontology viewer

Every signed-in user can open **Ontology** in workspace navigation at
`/ontology`. The active workspace switcher selects the single graph boundary.
The view contains only currently readable Apps, Tracks, published attached
Operational Models, and materialized Entry Types. A platform-admin role is
not required and does not expand this view's resource authority.

Select a node in the canvas or **Visible nodes** list to inspect it. Entry Types
show a bounded preview of visible field keys, labels, types, and required flags.
**Browse from here** makes that node the root of a fresh neighborhood capture.
**Back** and **Workspace** navigate between roots. Adjust **Steps**, switch the
layout, or use zoom and fit. **Find** searches readable Apps and Tracks as
starting points, including children shared directly beneath a private parent.

## Access boundary

`POST /api/workspace-ontology` accepts only `root_id` and `max_depth` (1–4).
`GET /api/workspace-ontology/roots?search=…` finds starting points. Both resolve
the authenticated principal and validate `X-Integral-Scope: ws:<id>` against
current membership. Nodes pass the canonical current read policy; schema models
also require a current published attachment to a visible resource. Every emitted
edge has two permitted endpoints in the same workspace. Hidden nodes cannot
serve as traversal bridges, and private parents are not exposed by child grants.

The service builds on JV Spatial's read-only graph projection and Integral's
`WorkspaceProjectionAccess`; it never calls the upstream admin graph APIs.
Display fields are allowlisted. Raw manifests, form schemas, field defaults,
options, relation target IDs, private runtime/account records and inaccessible
counts are excluded. Per-capture read decisions are discarded between requests.
No browsing action grants additional read or write authority.

Client state is mounted per principal/workspace, pending responses are aborted,
and captures are replaced rather than accumulated. Failed reads clear the graph.
Focus return and a visible-page 60-second refresh request fresh authorization.
Previously displayed data cannot be made unseen; revocation takes effect on
the next request/refresh rather than through a new push invalidation channel.

## First-version limits

- This is schema browsing. Entry instances, tags, views and declared relation
  target links are not yet drawn. Types present only in a manifest are not
  materialized by a read and therefore do not appear.
- One capture is bounded to 100 nodes, 180 edges, 800 candidates, 100 incident
  edges per node, and a six-second projection deadline inside an eight-second
  service deadline. Output indicates partial captures and the requested radius.
  A complete radius is not a complete workspace inventory.
- Field previews scan up to 100 declarations, display up to 24 visible fields
  per type, and share a 12 KB detail budget above the 48 KB graph budget.
- Root discovery checks up to 50 matching Apps and 50 matching Tracks. It has
  no whole-workspace count or pagination claim; narrow the name search to locate
  a starting point on larger workspaces. Model attachment checks are bounded
  to 50 parents and may omit a heavily shared template when the visible owner
  lies beyond that page. Large/sparse-permission deployments need further
  qualification and permission-filtered root/attachment paging.

## Local qualification

Implementation baseline: PR #136, commit
`ac34fcbad3d3de0409776b6321c1e8e57b6cd4ce`, Core 0.1.1rc18 and PyPI
`jvspatial==0.2.2`. Rendering uses locally bundled Cytoscape 3.30.4, inspired by
JV Spatial's progressive graph viewer. Original checkout changes are untouched.

The nine ontology tests exercise real rooted native-store graphs, ordinary
viewer access, hidden parents with direct child grants, exclusions, revocation,
shared templates and foreign consumers, draft and stale model/type bindings,
legacy directly attached types, HTTP scope/validation,
membership loss, field redaction and output bounds. The adjacent graph-context
suite and all 1,757 frontend tests pass. Production frontend compilation passes.
Browser checks use ordinary synthetic accounts against an isolated PostgreSQL
preview. Confirmed flows include normal-user sign-in, workspace switching,
root browsing, both layouts, graph selection, zoom/fit, hidden-root search and
revocation followed by refresh clearing the old graph. Desktop (1440px), tablet
(768px) and mobile (375px) were inspected; desktop and mobile main-content axe
checks reported no WCAG A/AA violations. These checks are not a complete
accessibility audit or a visual regression baseline.

The initial unrestricted-xdist verification attempt exhausted capture deadlines
in ontology and existing recorded graph-context tests. Final qualification uses
two xdist workers and deterministic native fixtures. `make verify` passes;
the final permission changes also pass the focused ontology/graph-context
suites, and the label refinements pass browser inspection, type checking and
production compilation.
This is local feature evidence, not deployment or production-scale qualification.
