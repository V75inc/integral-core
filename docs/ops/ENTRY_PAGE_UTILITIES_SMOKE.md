# Full-page entry utility strip

Local disposable workspace: localhost:9108. Follow-up to the full-page file reachability issue.

## Design

Attachments appear as an email-style strip below the entry utility bar. The record keeps its full reading width. The top-bar Attachments, Comments and Activity buttons select and toggle the utility section; there is no permanent content sidebar. Empty attachments do not reserve space until explicitly opened. Track dialogs retain their existing tabs. File metadata includes an added timestamp to distinguish retained versions with the same name. The previous warm graphite notification-bar color is restored at the user's request.

## Browser evidence

The existing managed PDF record displayed both retained attachments with distinct timestamps. Opening the current file from the strip rendered the one-page PDF. Comments opened its empty state and composer, then collapsed without losing the record. No comment or record was written. Screenshot evidence: `venture-full-page-attachment-strip.jpg` and `venture-full-page-comments-toggle.jpg` in the local smoke evidence folder.

The initial narrow-viewport inspection found the utility buttons crowded out the heading; the follow-up wraps the header into a heading row and a controls row on small screens. After deployment, the phone layout showed the bounded title above the controls, stacked file cards, one utility region and no document horizontal overflow (349px document width within the observed 355px viewport). Screenshot: `venture-full-page-strip-mobile.jpg`. The desktop Activity control also opened the existing audit history and retained the full-width record.

The full Core verification gate passed during this follow-up. The strip implementation passed all 1,499 frontend tests, lint with zero errors (451 existing warnings), source guards and production build. The small-screen header follow-up also receives targeted tests and a production build. Browser file-download completion remains unqualified: a click fetched the attachment successfully, but the browser download event was not observed. These checks do not close broader Venture Journey acceptance.
