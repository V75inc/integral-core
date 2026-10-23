# Chat completion destinations

Successful create/update responses carry permission-checked `result_links`.
The same projection serves model output, approved-change cards and their saved
transcript navigation. It does not change mutation authority or receipts.

- App changes link to the App when it contains zero or several Tracks. Exactly
  one actual attached Track links directly to that Track, if readable.
- A single affected Entry links to the Entry on its canonical Track. Several
  Entries link to their containing Tracks, without individual Entry shortcuts.
  Multiple tool calls in one turn and multiple approved writes in a Prompt Sheet
  count together. Bulk acknowledgements resolve their complete successful ID set.
- Dashboard changes open the particular dashboard. Home changes open App Home.
- Failed, skipped, deleted and unexecuted proposals do not produce destinations.

Destinations are built from canonical graph location, current workspace and role
checks, and current resource labels. Model-provided parent IDs and URLs are not
trusted. A bounded two-neighbour read distinguishes one Track from many without
hydrating an entire collection. Prompt Sheet grouping uses the already checked canonical parent metadata and adds
no asynchronous reads inside its queue mutation/save window. No inferred single-Track shortcut is based only
on the subset returned by a tool. Link read failures cannot invalidate committed
writes. The projection preserves I-ACCESS-01, I-CHAT-01, I-APPROVAL-02/05 and the
existing graph/write boundaries; it adds no persisted Node or Edge type.

The final answer retains its prose and gains missing destination links. Verified
App-build completions use the same selection rules. The model receives the
canonical links and completion instructions; output normalization redirects an
App shortcut to its sole Track and removes individual affected Entry shortcuts
when several Entries were changed. Read-only source citations are unaffected.

App destinations accept `?section=home` and
`?section=dashboards&dashboard_id=<opaque-id>`. Explicit destinations override
remembered tab state; normal App navigation still restores its saved preference.
Dashboard selection updates the query so switching and reload agree. These links
navigate only when clicked; they do not replay automatic agent navigation.
