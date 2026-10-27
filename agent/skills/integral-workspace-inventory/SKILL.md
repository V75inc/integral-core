---
name: integral-workspace-inventory
description: Report the current total number of Entries in this workspace, including installed Apps. Use for how many entries or records are in this workspace. This is a content-free inventory count, not a domain metric or App-specific breakdown.
allowed-tools: integral_count_workspace_entries
---

# Workspace inventory

Call `integral_count_workspace_entries` with no arguments. Report `total_entries`
as the number of Entries the caller can access in the active workspace. The tool
includes installed Apps, rechecks current access, and returns no record contents.
No App list, Track scan, schema discovery, semantic search or grouped domain
query is needed. Do not report a total unless `coverage` is `complete`.

For a business-specific count, filtered records or a breakdown, use the owning
App's declared read skill. For recurring updates, use integral-scheduling to
stage one agent routine; each run should read this live inventory tool again.
