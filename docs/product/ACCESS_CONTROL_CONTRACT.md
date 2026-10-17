# Integral access-control contract

Status: normative implementation target for the authorized access-control work.
The [closure ledger](../ops/qualification/access-control-review/closure.json)
records actual qualification. This contract does not claim that every path
already implements it.

## Ownership and governance

A workspace has exactly one canonical owner, represented by its unique
`IS_MEMBER_OF{role: owner}` relationship. Ownership is protected from generic
member edits. Ambiguous ownership does not select an arbitrary winner.
Personal workspace ownership cannot be transferred through collaborative
workspace administration.

The owner governs every canonical workspace-owned resource, including
member-created private Apps, standalone/contained Tracks and Entries.
Governance is a separate source of authority; it does not manufacture
`OWNS` or collaborator relationships and does not change their inheritance.

| Capability | Owner stewardship | Ordinary authority |
|---|---|---|
| Read workspace data and complete resource inventory | All canonical descendants | Resource audience and grants |
| Edit data and curate configuration | All canonical descendants, through canonical validation | Editor/admin tiers and declared actions |
| Inspect and administer access | All canonical descendants | Direct scoped access-administration grants |
| Delete, reclaim or transfer stewardship | Protected, audited lifecycle operation | Eligible canonical resource steward |
| Assign App functions/groups | Workspace-bound declaration and grant administration | Explicit delegation ceiling |
| Execute a business function | Its active declaration, grant and conditions still apply | Same execution contract |
| Export personal credentials or exercise host trust | No authority from workspace ownership | Separate identity/host perimeter |

Privacy flags and inherited exclusions govern ordinary audiences. They cannot
hide workspace-owned resources from the protected workspace owner. A personal
identity, credential or privately addressed session does not become
workspace-owned merely by referring to that workspace. Canonical organization
data cannot acquire such an exception through an App label or creator flag.

Callable App skills and App-scoped workspace skill overlays require current
ordinary App audience authority and active canonical containment. Protected
data governance alone cannot delegate them. App operation/declaration policy
evaluation distinguishes ordinary function authority from protected stewardship
and cannot reuse a protected data decision as a function allow.

Owner authority remains live, workspace-bound and explainable. Ordinary
administrators do not acquire it from the word `admin`; their delegation must
state the capabilities, target scope and ceiling. They cannot alter the owner,
raise their own rights or change protected peers through generic membership
editing.

## Ordinary resource authority

Roles are `owner > admin > editor > commenter > viewer`. Canonical stewardship
is the source of resource ownership; a historical collaborator `owner` literal
does not itself create that ownership relationship or authorize a transfer.

Every allowed path must be satisfied as one complete grant. Among ordinary
direct and inherited roles, the strongest complete role wins. Inherited
`owner/admin` caps at `editor`; ordinary sharing/configuration rights require
a direct grant. Scoped governance can be delegated separately.

`EXCLUDED_FROM` removes inherited paths only. It does not override a direct
grant, and the UI must explain remaining access. Absolute resource blocks and
workspace suspension are distinct lifecycle controls; they defeat identified
ordinary grants and prevent token/invitation re-entry. Protected ownership
cannot be suspended through an ordinary collaborator operation.

Viewer is read-only, including authored Entries. Commenter may comment/react
and edit its own Entry only under the explicit author condition. Moderation
does not permit rewriting another person's comment. App-protected transitions
and separation-of-duty conditions still constrain canonical mutations.

### Collaborator counts and directory visibility

An authenticated caller with current resource-read authority may see the App or
Track's aggregate collaborator count. The count uses the same deduplicated roster
as the manager view; Track counts distinguish excluded inherited rows from
effective rows. Broad workspace/public visibility pools are summarized separately.
Counts are display metadata and do not confer authority.

Directory visibility remains a separate manager/governance capability. A reader
may receive `collaborators: []` with `directory_visible: false` and a nonzero
count. The UI must use the server aggregate and explain that details are restricted;
it must not treat a hidden directory as an empty one. Unauthorized callers receive
neither the count nor identities. Existing roster truncation indicators still apply.

## Canonical facts and admission

Resource scope comes from the unique rooted structural graph. Scalar workspace
and parent IDs are validated caches, not permission anchors. Missing, foreign,
ambiguous or inconsistent parents cannot activate a direct grant. A single-parent
resource rejects extra parents. Explicitly shared models validate their complete
named parent set within one canonical workspace. Raw candidate
cardinality is checked before hydration; malformed candidates must not
disappear and make a set look unique.

A saved View belongs to one Track through `HAS_VIEW`; its model catalog and
parent caches must agree with that named scope. Shared-model catalog membership
cannot combine Track audiences or let one Track's configuration maintenance
change another's Views. Saved View policy targets bind both the current
canonical Workspace and any claimed parent. Model-only templates require
separate model authority and never acquire a Track capability from membership
alone. Legacy ambiguous paths remain withheld for reviewed repair.

OperationalModel targets bind their actual named attachment or lifecycle path
and the immutable execution workspace. Drafts follow `HAS_DRAFT_PROFILE` to
their current published parent; templates follow `DEFINES_TRACK_PROFILE` into
their owning App model. Scalar `draft_of_id`, `app_id`, workspace and attachment
caches cannot invent those paths. Consumed versions remain readable only under
current parent authority and are immutable configuration evidence. A model read
uses an admitted current parent; a configuration mutation requires authority on
every affected named parent. Creation rights never imply unrestricted mutation
of existing models. Workspace-owner stewardship does not become ordinary App
function authority. Platform publication and private library ownership need
their own named authority: membership in the global library catalog or an empty
workspace cache cannot turn private content into a public package.

Private library ownership uses `Workspace —HAS_LIBRARY_MODEL→ OperationalModel`;
trusted platform publication uses
`IntegralApp —PUBLISHES_LIBRARY_MODEL→ OperationalModel`. Catalog membership
organizes both families and grants neither ownership nor publication. Pending
drafts require configuration authority or an explicit current reviewer grant;
published-parent viewers do not automatically become draft reviewers. Workspace
administrator membership and creation flags do not authorize configuration of
existing private Models. Successful private-library creation by a principal
with explicit creation rights designates that principal as curator of exactly
the new Model. Its typed grant records the workspace, recipient membership
edge identity and issuer provenance. Removing creation flags blocks new
creation but preserves that existing curation; removing and recreating the
membership does not revive the old grant. This is an ordinary durable human
grant, not a live issuer delegation chain. Protected owner creation alone
does not mint an ordinary App configuration grant. Private Model creation
always allocates a server-owned identity; creation rights cannot replace an
existing Model through an upsert or supply attachment, lineage, seed or audit
provenance. Legacy unbound grants still
require their separately reviewed migration; this producer contract does not
claim atomic revocation/effect fencing. Protected workspace-owner governance remains available in Core and
does not amplify ordinary App operations. These contracts require producer,
legacy migration and all consumer adoption before runtime closure.

Persisted role, creation, visibility, kind and lifecycle facts use exact typed
vocabularies. Missing fields do not acquire authority through model defaults.
Conflicting legacy/canonical representations fail closed. API normalization
must be explicit before persistence; legacy migration needs an access diff and
an audited disposition.

Lifecycle admission distinguishes retained data, recovery and execution. Known
`active` and `paused` Apps retain ordinary Core data access, subject to the same
current identity, workspace and resource checks. Canonical direct stewards and
protected workspace owners retain generic recovery controls over `installing`,
`awaiting_settings`, `active`, `paused` and `failed` installations. Recovery
controls do not authorize App functions or activate a declaration. Bound App
execution requires the active lifecycle gate; uninstalled or malformed states
cannot enter these generic data/recovery paths. State transitions retain their
own validation and effect fencing obligations.

Both graph and authentication ID forms resolve to the same current human
identity and unique profile. Graph-only service principals require a
server-established service contract. A caller-supplied actor kind, owner ID or
workspace argument cannot establish that trust.

Collaboration requires current workspace admission, resource authority,
function authority where applicable, and any bound App/workspace scope.
Public exposure has its own explicit audience and authorized projection; it
does not confer general workspace membership or facade rights. Anonymous
audiences cannot enforce identity-specific suspension. Public writes require
separate active declarations and validation.

Entry discovery, pages, counts and streams admit each candidate through the
current canonical Entry audience. A direct Entry grant remains discoverable
without a parent App/Track read grant. Excluded or noncanonical rows contribute
neither payload nor totals; a failed authority read is unavailable rather than
an empty success. Cursors carry position only and preserve every configured
sort key, including the trailing null/missing values.

Embedded parents are navigation projections: Track `id`, `title`, `icon` and
`workspace_id`; App `id`, `name`, `icon` and `workspace_id`. Entry access never
exports a parent's description, configuration or other private content. Full
parent endpoints apply their own admission. A temporary navigation payload is
response data, never a persisted grant, permission cache or release fence.

## Effects, grants and release

Identity and workspace authorization changes serialize with protected effects
through shared storage fences. Authority is evaluated after fence admission.
The common order is identity before workspace; writes advance the appropriate
persisted revision in the same commit. A process generation or TTL is not a
distributed revocation guarantee.

Invitation acceptance, share redemption and ownership/membership changes have
versioned atomic terminal outcomes and durable receipts. A retry reports
created/upgraded/unchanged/conflict from persisted state. It cannot overwrite
a newer grant, consume capacity again or repeat a committed effect. Pending
invitations require a live issuer or explicit protected-owner adoption.
Mailbox proof belongs to the current email; a token ceremony does not globally
verify the account.

Voluntary leave, resource-grant removal, link disablement, sourced-grant
revocation and suspension are separate operations. Each reports remaining
effective authority. Accepted durable collaboration grants and dependent
service delegations have explicit lifetimes. Offboarding fences pending work,
approvals, sessions and shared integrations; personal credentials are not
transferred silently.

Reads, cached/replayed outputs, files, event/audit/poll/notification payloads
and stream chunks are authorized at their server release point against their
actual dependencies. Released bytes cannot be recalled. Unknown current
authority is denied/unavailable, never a cached allow or fabricated empty
success. Count/page/aggregate/export results obey the same audience as point
reads, and raw pagination cannot terminate because hydration skipped a row.

## Qualification

The golden scenarios distinguish workspace owner, resource creator/steward,
ordinary admin, App admin, editor/commenter/viewer, guest, outsider, disabled
principal, verified/unverified invitee and both human ID forms across two
workspaces. They cover private/public/workspace/inherit resources, standalone
Tracks, exclusions, overlapping grant sources and active App versions.

The [current access contract](ACCESS_CONTROL_CONTRACT.md) and U01–U76 ledger retain
all required runtime, migration, browser/MCP, independent-worker, artifact and
operator evidence. A new owner capability does not close its use case before
those surfaces and allowed/denied outcomes are qualified.


### Private creation defaults

New Apps and Tracks default to Private in every Workspace. App visibility is
Private, Workspace or Public; App creation and updates reject `inherit`.
Workspace/public viewing requires an explicit authorized choice and does not
grant editing or access-management capabilities. Track inheritance remains
an explicit supported choice. Existing saved visibility and collaborator grants
are preserved. See [resource visibility](RESOURCE_VISIBILITY.md).
