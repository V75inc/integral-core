# App, Track and entry type icons

An App can give itself, each of its Tracks and each entry type an icon, so lists and the sidebar are not text only.
Icons come from **one fixed list inside Integral**. A manifest names an icon; it never supplies an image or a URL.

## Use it

In `operational-model.yaml`:

```yaml
app:
  icon: banknote            # the App itself (sidebar, App page)
  tracks:
  - key: pay_runs
    name: Pay Runs
    icon: receipt           # the Track (sidebar, App page)
    entry_types:
    - key: pay_run
      name: Pay Run
      icon: calendar        # entry type (cards, lists)
```

All three are optional. With no icon an App shows its initials and a Track keeps the coloured bar it has today.

## Rules

- **Names only.** The value must be a name in the list below. Case and `_` / `-` are normalised (`Dollar_Sign` is
  `dollar-sign`). Images, data URLs and links are refused.
- **A wrong name fails the manifest.** For Apps and Tracks, compiling the manifest (install, update, draft) is
  refused with the list of valid names, so a typo cannot ship as a blank icon. Entry types keep their older, lenient
  behaviour: an unknown name draws a generic document icon.
- **The package's icon is a default.** It is applied when the App or Track is created, and on update only when the
  App or Track has no icon yet. An icon a person chose (for example an App's own logo in its settings) is never
  overwritten by an update.
- **Theme and colour.** The icon is drawn in the Track's accent colour (or the theme default), in dark and light.
- **The App icon picker** offers a short curated set. A package may name any icon in the list; a person can still
  upload a small PNG, JPEG or WebP logo for an App in its settings (not from a manifest).

## The list

``archive`, `arrow-right`, `award`, `badge`, `banknote`, `bar-chart`, `bar-chart-3`, `bell`, `book`, `book-open`, `bookmark`, `box`, `brain`, `briefcase`, `broadcast`, `building`, `building-2`, `calculator`, `calendar`, `calendar-clock`, `calendar-days`, `calendar-off`, `chart`, `chart-no-axes-combined`, `check-circle`, `check-square`, `checkbox`, `chevron-right`, `circle-dot`, `clipboard`, `clipboard-check`, `clipboard-list`, `clock`, `compass`, `contact`, `credit-card`, `decision`, `document`, `dollar-sign`, `email`, `eye`, `file`, `file-check`, `file-minus`, `file-signature`, `file-spreadsheet`, `file-stack`, `file-text`, `files`, `flag`, `flask-conical`, `folder`, `folder-kanban`, `folder-open`, `gavel`, `git-branch`, `git-pull-request`, `globe`, `graduation-cap`, `handshake`, `hash`, `heart`, `help-circle`, `idea`, `image`, `inbox`, `issue`, `kanban`, `key`, `landmark`, `laptop`, `layers`, `layout`, `layout-grid`, `leaf`, `life-buoy`, `lightbulb`, `line-chart`, `link`, `link-2`, `list`, `list-checks`, `lock`, `mail`, `map-pin`, `message-circle`, `message-square`, `mic`, `milestone`, `package`, `paperclip`, `percent`, `phone`, `pin`, `plus-circle`, `post`, `question`, `quote`, `receipt`, `repeat`, `rocket`, `search`, `settings`, `shield`, `shield-check`, `signpost`, `sliders`, `sparkles`, `square-stack`, `star`, `table`, `tag`, `tags`, `target`, `task`, `ticket`, `trending-up`, `trophy`, `truck`, `update`, `user`, `user-check`, `users`, `wallet`, `zap``

## Adding an icon to the list

The names are Lucide icons. Two lists must stay equal; a test (`backend/tests/test_manifest_icons.py`) fails if
they differ:

1. `frontend/src/lib/manifestLucideIcon.ts`: import the icon and add it to `MANIFEST_ICON_BY_ID`.
2. `backend/app/services/manifest_icons.py`: add the name to `MANIFEST_ICON_KEYS`.

Prefer an existing icon. Do not add brand logos or icons that only one App needs.

## Where the code is

| Piece | File |
| --- | --- |
| Valid names, refusal message | `backend/app/services/manifest_icons.py` |
| Reading `icon` from `app` and `app.tracks[]` / track templates | `backend/app/services/operational_model_compile.py` |
| Copying it onto Tracks (create, update, template tracks) | `backend/app/services/operational_model_merge.py`, `operational_model_graph.py` |
| Copying it onto the App (create, update from library) | `backend/app/services/app_lifecycle.py`, `app_identity.py` |
| Drawing it | `frontend/src/ui/AppIcon.tsx`, `frontend/src/components/ui/TrackDot.tsx`, `frontend/src/components/layout/Sidebar.tsx` |
