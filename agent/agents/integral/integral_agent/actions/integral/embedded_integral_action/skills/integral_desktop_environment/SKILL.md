---
name: integral_desktop_environment
description: "Reads files and diagnostics from folders explicitly granted through Integral Desktop. Use for requests about files on the user's computer, desktop, local system, or macOS/Windows/Linux environment. Never substitute Integral workspace attachments for local filesystem requests."
spec: jv
allowed-tools:
  - desktop__list_roots
  - desktop__list_directory
  - desktop__read_file
  - desktop__find_path
  - desktop__grep
  - desktop__diagnostics
requires-actions:
  - EmbeddedIntegralAction
extends: action:integral/embedded_integral_action
always-active: true
tags:
  - integral
  - desktop
  - filesystem
  - local-environment
---

# Integral Desktop environment — SOP

Integral Desktop can expose a small, read-only view of the user's local
environment. The tools exist only while an authenticated desktop application
binding is live. They can access only folders the user granted from the native
**Environment** menu.

## When to use

Use this skill when the user refers to:

- files or folders on "my computer", "my system", "my desktop", or "my Mac";
- a local path, project directory, source tree, or operating-system
  environment;
- searching or reading files outside the Integral knowledge graph;
- local runtime diagnostics.

Do **not** use `integral_list_workspace_attachments`,
`integral_list_track_attachments`, or `integral_list_attachments` for those
requests. Those tools describe files stored inside Integral entries, not the
desktop filesystem.

## When not to use

- Files attached to Integral entries, tracks, or workspaces → use
  `integral_attachments`.
- Conversation artifacts generated or uploaded during chat → use
  `integral_artifacts`.
- Browser downloads that are not inside a granted root are not accessible.
- Creating, changing, deleting, or executing local files is outside the
  current read-only desktop surface.

## Procedure

1. Call `desktop__list_roots` first. These are the only local folders Integral
   may inspect.
2. If no roots are returned, tell the user to grant one from the desktop
   application's native **Environment → Grant Folder…** menu. Do not claim
   their computer has no files.
3. Call `desktop__list_directory` with a returned `root_id` to enumerate a
   folder. Use relative paths only.
4. Use `desktop__find_path` to locate names and `desktop__grep` to search text
   beneath a granted root.
5. Use `desktop__read_file` only after resolving the root and relative path.
6. Use `desktop__diagnostics` only for an environment/runtime question.

## Safety and honesty

- The surface is read-only. Never claim to create, edit, move, delete, or run
  local files or commands.
- Never invent an absolute path or `root_id`.
- A result covers granted roots only, never the user's whole computer.
- If a desktop tool is unavailable, say the desktop environment is not
  connected or granted. Do not silently fall back to Integral attachments.

## Grounding

- Treat `desktop__list_roots` as the authority for accessible local roots.
- Treat each directory, search, grep, and file result as scoped to its returned
  `root_id` and relative path.
- Never infer that an unlisted path does not exist elsewhere on the computer.
- Report truncation and tool errors instead of presenting a partial result as
  complete.

## Staging discipline

All current desktop tools are reads and execute immediately. They create no
staged change. If the user requests a local mutation, explain that the desktop
surface is read-only rather than staging or claiming the mutation.

## Example

> **User:** "List my desktop files."

1. Call `desktop__list_roots`.
2. For each relevant returned root, call `desktop__list_directory` with that
   `root_id` and an empty relative path.
3. Present the returned names and make clear they are from granted folders.

If step 1 returns no roots, direct the user to **Environment → Grant Folder…**;
do not call an Integral attachment tool.
