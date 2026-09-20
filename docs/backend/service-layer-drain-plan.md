# I-CRUD-01 service-layer drain — plan

**State (2026-08-12):** ratchet at **17/17** after the watch-edge drain
(`services/watchers.py`; tracks.py is off the allowlist). Every remaining
entry covers at least one live `Node.create(`/`.connect(` in `api/` — verified
by removing each entry in turn and running the guard: **zero free removals**.

**The recipe, proven by the watch drain and by every Wave-2/3 extraction
(`entry_type_service`, `tag_service`, `comment_writer`):**

1. Extract the write (+ its structural edge, same unit of work — I-GRAPH-01)
   into `services/<domain>_service.py`. The ChangeEvent emit moves WITH the
   write; the D-05 guard follows one level of delegation into `services/`
   (`test_change_event_no_bypass.py`), so no allowlist entry is needed there.
2. Idempotency/rollback invariants move into the service, not copies in each
   caller (see `watchers.ensure_watch_edge`, `patch_workspace_member`'s
   restore-on-failure).
3. Handler keeps: auth resolve, validation, policy gate, response shape.
4. Remove the file's allowlist line, lower `MAX_ALLOWLIST_ENTRIES` by one,
   run `make verify-ci`. One file per PR unless files share a service.

**Inventory, measured** (`grep -cE '\.(create\(|connect\()'`), sequenced
easiest-first so the ratchet moves every week:

| Order | File | Writes | Extraction target | Notes |
|---|---|---|---|---|
| 1 | `api/views.py` | 1 | `view_create_resolution` (exists) | View.create + CATALOGS wire |
| 2 | `api/entry_relations.py` | 1 | `services/entry_relations.py` (new) | REFERENCES wire w/ field_key |
| 3 | `agentive/middleware/service_auth.py` | 1 | `services/user_lifecycle` | D-02 auto-create User; flag-gated |
| 4 | `agentive/staging_store.py` | 1 | keep, move file under `agentive/services/` | it IS a store; location is the drift |
| 5 | `agentive/api/channels.py` | 2 | `services/channel_identity.py` (new) | ChannelIdentity.create + owner wire |
| 6 | `agentive/api/connectors.py` | 1 | `services/connectors/registry` | HAS_* wire |
| 7 | `api/notifications.py` | 2 | `notification_router` / `link_notification` | I-GRAPH-01 HAS_NOTIFICATION path |
| 8 | `api/users.py` | 3 | `services/user_lifecycle` | create/update pairs |
| 9 | `api/entries_transform.py` | 3 | `services/entry_transform.py` (new) | promote/convert flows |
| 10 | `api/auth.py` | 4 | `services/personal_workspace` + `user_lifecycle` | signup graph bootstrap |
| 11 | `api/comments.py` | 5 | `comment_writer` (exists — extend for replies/mentions) | closes the v1-limitation note too |
| 12 | `api/tracks_public_share.py` | 5 | `track_public_share` (exists) | public comment path |
| 13 | `api/workspaces.py` | 7 | `services/workspace_members.py` (new) | member edge swaps incl. rollback |
| 14 | `api/apps.py` | 8 | `app_lifecycle` / `app_graph` (exist) | install/link flows |
| 15 | `api/attachments.py` | 8 | `services/attachments_service.py` (new) | HAS_ATTACHMENT + upload sessions |
| 16 | `api/entries.py` | 11 | `entry_service` (split: create w/ rollback exists inline) | biggest; last |
| 17 | `api/content_profiles.py` | 13 | `content_profile_*` family (exist) | draft/publish already service-side; author path remains |
| — | `agentive/api/` (dir entry) | 3 total | falls out with #5 + #6 | delete dir entry after both |
| — | `agentive/sample_consumers/` | 2 | samples — decide keep-as-doc or move under services | cheap either way |

**Invariants each PR must state (AGENTS.md substrate rule):** I-GRAPH-01
(structural edge in the same unit of work as every create), I-CRUD-01 (write
lives in services; handler is gate+shape), D-05 (emit travels with the write),
and where applicable I-ROLE-01 (no gate weakening in the move).

**Definition of done:** allowlist empty, `MAX_ALLOWLIST_ENTRIES=0`, guard
becomes a hard rule instead of a ratchet.
