# Reaction selection and legacy compatibility

Entries and comments share five choices: 👍 (thumbs up), 👎 (thumbs down),
❤️ (love), 😊 (happy), and 😢 (sad). `PUT
/entries/{id}/reactions` and `PUT /comments/{id}/reactions` take `{ "emoji":
"👍" }` and select idempotently. Existing `POST` entry reaction clients retain
toggle semantics, including historical emoji outside these five choices.
New PUT selections reject retired choices (including 😂, 😮, and 😡) without
modifying historical data.
The frontend presents all choices as outline icons. Comments have
matching POST toggle and DELETE `/{kind}/{id}/reactions/{emoji}` routes. DELETE
removes only the authenticated actor's matching reaction, and remains available
to viewers who retain read access. Adding/replacing requires commenter access.

A typed `ReactsTo` edge from the graph User to the Entry or Comment carries
`emoji`, `principal_id` and `updated_at`. The canonical service serializes graph
writes using the existing workspace membership/authorization fence: PostgreSQL
holds the workspace authority lock in the same graph transaction as the edge,
compatibility projection, and resource event outbox; local stores use the existing
process fence. Current resource location, explicit HTTP workspace scope, current
membership and per-entry exclusions are checked inside that fence. No new Node
or detached record is introduced (I-GRAPH-01/02). Policy uses the Entry even for
comment targets, preserving the current role and deny rules (I-ROLE-01/02).

Legacy Entry.reactions is retained for compatibility. Reads merge its historical
values with edge-backed selections. On an actor's next mutation, the service
reconciles only that actor's aliases and edges; untouched actors and unsupported
historical emoji survive. Selecting a reaction intentionally replaces all of
that actor's old emoji selections. Removing a particular emoji removes only that
emoji, leaving other legacy selections until an explicit selection. Duplicate
edges for the actor are repaired during selection. The retained entry map is
updated with each mutation so existing integrations reading that declared field
continue to see the selected value. Comments have no legacy map: their exported
`reactions` comes directly from edges.

There is no startup scan or destructive global migration. Work is bounded to one
target and one actor per write; preserving old multiple selections until that
actor chooses avoids silently discarding historical data. Reads do not migrate
or mutate data. New writes must use the canonical service, rather than edit
Entry.reactions directly. Existing change-event names remain `entry.update` and
`comment.update`; comment events include the parent entry metadata for live
refresh and authorization.

Entry list/feed pages and comment list pages project their complete window in
one typed-edge query via `reaction_summaries` and `export_nodes`. Scalar exports
use the identical projection function. No reaction state cache or node-field
authority is introduced to avoid per-row queries.
