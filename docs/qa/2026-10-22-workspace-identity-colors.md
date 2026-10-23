# Independent workspace identity colors

## Diagnosis

WorkspacesPage passes each row's saved color to TrackDot. For an empty or absent
color, TrackDot falls back to `--brand-accent`. ScopeContext changes that token
to the active workspace's color, so default-colored rows appear to adopt another
workspace's identity whenever the user switches scope or edits its color. This
is a presentation dependency; the row's persisted workspace color is not changed.

The workspace switcher already uses the stable `--brand-accent-default` theme
token for default identities. The Workspaces page now uses the same default,
while preserving each row's explicit color. Track/App markers elsewhere and the
active workspace theme retain their existing behavior. No backend change or
stored-color migration is needed.

## Regression coverage

The new component regression fails against the previous implementation. It uses
two distinct saved colors, an absent color and a whitespace-only color; changes
the active accent; then refreshes one row's saved color. Only that row changes.

The browser check uses the isolated preview on port 9155. A new, empty synthetic
personal workspace, Color QA Indigo, has an explicit Indigo color. With it active,
the page's active accent is `#4a6cd6`, its marker is RGB(74,108,214), and the original
default-colored QA workspace remains RGB(255,133,82). The saved color was also
changed to Teal through Workspace settings to exercise the edit path.
The Teal marker became RGB(31,156,142), while the default marker remained
RGB(255,133,82). The original active workspace selection was restored afterward.
Screenshot: `.local/design-review/workspace-colors-independent.png`.

Full frontend suite: 347 files / 2,097 tests passed; production build passed.
The final component regression also passed after tightening its asynchronous
cache-refresh assertion.

The reported hosted Business deployment was not changed. This repair targets
its shared Core Workspaces page; it takes effect there after the Core update
is released and adopted by Business.
