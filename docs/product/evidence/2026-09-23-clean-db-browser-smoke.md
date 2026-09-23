# Clean-database browser smoke — 2026-09-23

This is a signed-in trace of the compose images that were already running. It does **not** close the C6 browser row for `9269ad1783bf83acff90fe1accf3ae19ae961d53`. Those images were built before that commit.

| Artifact | Built (UTC) |
| --- | --- |
| API image `1ca282d0fa31` | 2026-09-23T09:09:40Z |
| Web image `1ef4b9d1509c` | 2026-09-23T08:30:12Z |
| Git `9269ad1` | 2026-09-23T10:20:11Z |

Database: fresh `integral_smoke` on the local Postgres container. The developer database `integral` was not dropped (1244 nodes before and after). The API used `integral_smoke` for the smoke and was returned to `integral` afterward. `/health` was 200 on both attachments.

A new account signed up. The personal workspace took the display name. Email verification was shown and skipped. The verify bar stayed up and did not block navigation.

Signed-in results on that empty workspace:

- Home: 1 workspace, 0 tracks, 0 entries today, 0 unread. Empty copy was “No tracks yet” and “Nothing to show yet”.
- Apps, Tracks, Feed, Approvals, Background tasks, and Notifications each showed a zero-state and no phantom records.
- Settings: Integral active, Echo inactive, no connected agents.
- Agent inbox: nothing waiting.
- “What is in this workspace?” called `integral_list_apps`. Result `total: 0`, `apps: []`. The reply said the workspace has no apps.
- “Do I have any tracks?” called `integral_list_tracks`. Result `total: 0`, `tracks: []`. The reply said there are no tracks.
- A full reload of `/agent` restored that thread once. Home after the chat was still all zeros.
- Route changes settled in 13–15ms. The previous page did not remain.

A fresh boot also creates an Administrator workspace and the resident Integral Agent. The signed-in UI showed only the new workspace. `integral_list_apps` returned no apps.
