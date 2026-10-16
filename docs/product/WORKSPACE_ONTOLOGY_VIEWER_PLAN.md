# Workspace ontology viewer — preliminary plan

Status: first version implemented on 2026-10-14; local qualification recorded
in [the viewer guide](WORKSPACE_ONTOLOGY_VIEWER.md). This document retains the
design intent and remaining exploration scope.
Baseline: PR #136, `codex/core-rc18-jvspatial-022`, commit
`ac34fcbad3d3de0409776b6321c1e8e57b6cd4ce` (Core 0.1.1rc18,
jvspatial 0.2.2). Work occurs in an isolated worktree, excluding ongoing
uncommitted checkout work. Refresh the PR baseline before implementation.

## Purpose and placement

Give every authenticated user a browsable ontology of one selected workspace,
limited to what that user can currently see. This supersedes the initial
admin-only placement proposal. Put Ontology in workspace navigation using
Integral's workspace switcher and existing session; no admin role is required.
Any admin entry point to this same view obeys identical visibility rules.
A separate platform-wide diagnostic graph is outside this plan.

Working interpretation: reveal visible Apps, Tracks, Operational Models,
Entry Types, fields, and declared relationships. Make schema structure legible
first; assess entry instances as a drill-down rather than loading all content
initially. Settle that distinction in the detailed design. Access to a readable
resource must not automatically expose privileged model configuration or other
consumers of its shared schema.

## Permission and scope contract

Use server-validated `X-Integral-Scope: ws:<id>` and current workspace membership.
Apply canonical per-resource read policy before returning any node. Every edge
must have two visible endpoints in the same workspace and a permitted relationship
projection. Hidden nodes and foreign-workspace links must not serve as traversal
bridges. A direct grant on a child does not expose a private parent; show only
permitted context without fabricating relationships.

Do not expose hidden labels, IDs, properties, counts, degree totals, placeholders,
or pagination metadata that reveal inaccessible resources. Denied expansion roots
need consistent non-enumerating responses. Preserve direct grants, inheritance,
exclusions, guest access, and lifecycle policy using existing resolvers. Workspace
ownership/admin membership does not automatically expose private children.
Browsing creates no additional read or mutation authority.

Recheck current authorization on load, expansion, search, and continuation.
Partition client state by principal and workspace and discard stale responses
after switching. Clear/refetch the view after access changes; do not reuse cached
permission decisions. Allowlist properties and exclude private account/runtime
records, credentials, tokens, sensitive preferences, and App internals outside
declared query contracts. Schema/field metadata needs explicit exposure rules.

## Experience inspired by JV Spatial

Inspect `jvgraph-ui/src/components/AdminGraphViewer.tsx` and
`examples/visualization/progressive_graph_viewer.html` in the local JV Spatial
repository. Adapt useful interactions into Integral's React UI and design system,
using its API client and session. Evaluate renderer/code reuse independently of
upstream admin API authority and its separate login flow.

Proposed controls: pan/zoom/fit, directed named edges, safe selection inspector,
expand/retract, pagination, and type/layout filters. Start with a bounded view;
disclose loaded scope and truncation. Search uses the same authorized projection.
Include loading, empty, partial, permission-loss, and error states. Bundle
renderer dependencies locally.

## Implementation investigation

Review `backend/app/services/graph_context.py`, `permissions.py`, and
`request_scope.py` for reuse. `WorkspaceProjectionAccess` and the graph-context
capture already establish workspace/read-policy boundaries; their current
allowlist does not cover a complete schema ontology. Assess metadata coverage
and behavior for direct-child grants rather than assuming capture is sufficient.

Use Integral-owned workspace ontology endpoints with `@endpoint`, schemas under
`app/schemas/`, read services, and existing JVSpatialAPIException subclasses.
Verify exact installed jvspatial 0.2.2 APIs before selecting traversal reuse.
Raw upstream admin graph endpoints must not be the ordinary user's data source.

Set server-side depth/node/edge limits and bounded pagination. Filter before
exposure; avoid counts/cursors derived from unauthorized candidates. Measure
sparse-permission and high-degree fixtures so bounded output does not conceal
unbounded database work. Follow the object-spatial traversal contract;
procedural deviations require measurements and inline documentation.

Preserve I-CONTEXT-01, I-RET-01/02, I-UI-SCOPE-01, I-ROLE-01/02,
I-GRAPH-01/02, and I-SUBSTRATE-01; enumerate them against the detailed design.
Introduce no domain-specific substrate behavior.

## Acceptance and sequencing

1. Settle schema-first presentation and visible model/field metadata rules.
2. Verify pinned-package capabilities and select renderer and projection design.
3. Implement workspace-bound authorization and bounded ontology projection.
4. Integrate into workspace navigation for all authenticated users.
5. Test owner/admin/member/guest/viewer, direct-child grants, exclusions, private
   parents, hidden bridges, shared schemas, cross-workspace links, revocation,
   search/pagination leakage, and stale responses after switching workspaces.
6. Inspect actual browser behavior with representative graphs, measure database
   work, and run repository verification gates. Tests alone do not establish
   visual acceptance or production performance.

Implementation is in the isolated worktree. No commit, push, PR update, or
deployment has been performed.
