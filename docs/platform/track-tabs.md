# Track tabs and the Settings hub: what an App can ask for

Everything here is **opt-in**. An App that sets none of it looks and behaves exactly as before.

## Order of a track's tabs

Most specific wins:

1. **A person's own order** (they dragged a tab). Stored as `config.tab_order` on each view and never overwritten by an
   App update.
2. **The App's track-level list**, `defaults.tabs.order` on the track: view keys, first to last. Views the list does not
   name follow it in the order the manifest declares them.
3. **The App's per-view default**: each view's place in the manifest (`default_tab_order`, stamped when the view is
   materialized; a view may declare `tab_order` to choose its own number).
4. The order the views arrive in.

```yaml
tracks:
- key: settings
  defaults:
    default_view: payroll_settings_hub
    tabs:
      order: [payroll_settings_hub, rates]   # optional
      show_single_tab: false                 # optional, default true
```

- `order`: list of view keys, no repeats. Unknown option names are refused when the manifest compiles, so a typo is
  not silently ignored.
- `show_single_tab`: `false` hides the tab strip when only one view is visible. Default `true` (a lone tab keeps its
  strip, as every track did before).

People who can administer a track can drag tabs to reorder them. That writes `tab_order` for every tab the first time,
then only for the tabs that move.

Not supported yet: a side placement for a track's views. It is deliberately refused so it cannot be declared before it
exists.

## The Settings hub (`operations-ui/settings-hub`)

Without options the hub keeps its original look: a generic header, a record count next to each section, and the
original empty and add wording. Set `variant: split` on the view's config to turn on:

- sections listed under group headings (`group` on a section; consecutive sections with one group share a heading),
- a set-up / not-set-up mark instead of a count,
- an empty section with a "Set up ..." button, and "Add another" once a record exists,
- no generic header unless the view names itself.

Two section options work with or without `variant`:

- `extension_view_key`: show that App screen (an extension view) in the detail pane instead of a record list. A
  section shows records or a screen, never both.
- `group`: only used when `variant: split`.
- `single: true` (split layout): the section holds one record, so there is no Add once it exists, only Edit.

In the split layout, records always open in the dialog, even for a record type that opts into a full page
(`open_as_page`), so Settings never sends you to another screen. An empty section shows one "Set up ..." button.

App-wide switches belong in the standard App settings dialog (`app.settings_schema`), not in the hub. See
[app-settings-ui.md](app-settings-ui.md).
