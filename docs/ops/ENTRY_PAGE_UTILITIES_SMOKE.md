# Full-page entry utilities

Local disposable workspace: localhost:9108. Follow-up to the full-page file reachability issue.

## Design

Attachments appear as an email-style strip below the entry utility bar. The record keeps its full reading width. The top-bar Attachments, Comments and Activity buttons select and toggle the utility section; there is no permanent content sidebar. Empty attachments do not reserve space until explicitly opened. Track dialogs retain their existing tabs. File metadata includes an added timestamp to distinguish retained versions with the same name. The previous warm graphite notification-bar color is restored at the user's request.

## Browser evidence

The existing managed PDF record displayed both retained attachments with distinct timestamps. Opening the current file from the strip rendered the one-page PDF. Comments opened its empty state and composer, then collapsed without losing the record. No comment or record was written. Screenshot evidence: `venture-full-page-attachment-strip.jpg` and `venture-full-page-comments-toggle.jpg` in the local smoke evidence folder.

The initial narrow-viewport inspection found the utility buttons crowded out the heading; the follow-up wraps the header into a heading row and a controls row on small screens. After deployment, the phone layout showed the bounded title above the controls, stacked file cards, one utility region and no document horizontal overflow (349px document width within the observed 355px viewport). Screenshot: `venture-full-page-strip-mobile.jpg`. The desktop Activity control also opened the existing audit history and retained the full-width record.

The full Core verification gate passed during this follow-up. The strip implementation passed all 1,499 frontend tests, lint with zero errors (451 existing warnings), source guards and production build. The small-screen header follow-up also receives targeted tests and a production build. Browser file-download completion remains unqualified: a click fetched the attachment successfully, but the browser download event was not observed. These checks do not close broader Venture Journey acceptance.

## Toggleable full-height panel follow-up

The user subsequently requested a scalable sidebar rather than the horizontal strip. The revised page starts with the panel closed. Attachments, Comments and Activity open the same right panel from the utility bar; its heading and close control remain fixed while content scrolls independently. Desktop reserves space only while open; smaller screens use the shared Modal primitive in a right-aligned, full-height drawer, retaining its focus and dismissal contract. File rows are vertical again. Closing returns focus to the triggering utility button. Escape closes the page panel without navigating away from the record.

Saved-entry dialogs now expose an **Open full page** control separate from **Expand dialog**. It uses the existing canonical `entryPagePath` route for the same saved record and is hidden while the entry form is being edited. Core remains domain-neutral: these controls and the optional Modal drawer placement are shared record UI, with no Venture or Documents routing exception. This preserves I-SUBSTRATE-01, I-EXT-01 and existing authorization/record-write contracts.

Final production build and all 1,502 frontend tests pass; lint has zero errors and 450 tracked warnings. The full Core gate completed successfully before the final dock-clearance and edit-toggle refinement; the final frontend changes receive separate frontend tests, lint and production build. Browser checks confirm panel closed after reload, current PDF preview, switching to Comments/Activity, close restoring trigger focus, and Escape retaining the entry URL. The desktop panel spans from the page header at approximately 118px to the viewport bottom at 885px. The phone drawer's heading/close control are reachable below the notice, and close retains the same entry. The standard saved-entry dialog's new button opens `Synthetic staleness source — revision A` at the same saved entry ID with its unchanged body.

Screenshots: `venture-full-height-attachments-panel.jpg`, `entry-full-height-mobile-drawer.jpg`, `entry-dialog-open-full-page-control.jpg`. The original immediate-reload click initially did not open the panel; the reset effect now avoids resetting the initial record, while later record changes still reset. Settled desktop and mobile checks pass. Broad early-load/hydration reliability remains a distinct regression to repeat. The strip screenshots above are historical evidence of the prior design, not the current presentation.

## Chat coexistence and edit cancellation

Browser inspection reproduced the fixed details panel being covered by the 420px assistant dock. Page chrome now observes its actual border-box width; below 1000px available page width it uses the drawer instead of shrinking the reading column. Both the fixed panel and shared drawer respect the existing assistant dock clearance variable. The drawer retains the shared assistant-aware focus contract. Measurement does not depend on content width after panel padding, avoiding a resize feedback loop. Browser checks with both open show distinct, reachable attachment and chat surfaces; closing attachments restores trigger focus without closing chat, and chat closes independently. Evidence: `entry-details-with-chat.jpg`.

The header Edit entry control now becomes Cancel edit in the same position, with an X icon and explicit tooltip, while the form retains its lower Cancel action. Neither cancellation saves a record. Final suite evidence: 1503 frontend tests passed, then 27 focused tests passed after adding the edit-toggle assertion. The full Core gate passed earlier in this follow-up. Original 22 Venture records and all three pre-existing Documents records remain unchanged by independent API comparison; one synthetic source fixture was created for the separately unfinished staleness case. Documents same-version library refresh succeeded under work item `1c101835f448277676451bcf0b5b74676e7eb66bd1e83cc56191114d5700a4f0` from Business revision `f9b51ef`.

Final browser edit checks show both header Cancel edit and lower Cancel returning to the saved record with Edit entry restored. Screenshot: `entry-header-cancel-edit.jpg`. Final lint has zero errors (450 warnings), and the corrected production Docker build passes.
