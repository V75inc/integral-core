# Field presentation

Apps configure shared entry rendering through their field specifications. Core
does not select these settings by App slug, field key, or business domain.

| Field option | Default | Purpose |
| --- | --- | --- |
| `hide_on_card` | `false` | Omit the field from shared entry-card metadata. Explicit table/view columns are configured separately. |
| `full_width` | `false` | Span the field across the entry metadata grid. |
| `max_lines` | `0` | Request a collapsed detail preview with an expansion control; zero leaves the preview unclamped. |
| `multiline` | `false` | Use a textarea for the inline text editor. Existing multiline text also uses a textarea. |
| `hide_when_empty` | `false` | Omit an empty field from detail metadata. The edit form remains available to fill it in. |

These options control presentation, not authorization. Hidden values remain
stored; access to records and mutations still follows Core's permission and
protected-field checks. Use separate resource boundaries for different audiences.

Optional selects offer **None** to clear a value. Required selects omit that
choice, and read-only selects cannot be changed. JSON edits preserve array order
and primitive types; only declared non-JSON fields receive field-specific
representation normalization when preparing changed-field payloads.
