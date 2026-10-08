# Shared chat transient version control cleanup

The user reported a stray `2 / 2` row below user messages in the dock. Browser
readback showed a 376-pixel selector in a 392-pixel message region, plus a forced
third grid row. Reload restored the same conversation with no counters.

The host restores a canonical linear transcript. assistant-ui's local repository
branches are not persisted saved-message versions. Remove their selectors from
both user and assistant message presentation rather than exposing transient
branches as durable version navigation. Edit and Regenerate remain available.
This is a domain-neutral presentation change; it changes no authorization or
workspace effects (I-SUBSTRATE-01/I-EXT-01/I-CRUD-01 remain intact).

An isolated frontend build was deployed while the price-binding gate was frozen.
Final image: `integral-venture-web:canonical-chat-versions-local`, digest
`sha256:b876d1debe7e4aa0d977b17bed469c9a393d2da6a424482d2fa5b76e40beda80`.
Build and deployment succeeded. Browser readback on the reported App/conversation
showed zero version controls, one child per user row and seven Regenerate actions;
the final user row shrank from 118.168 to 78.375 pixels. No model call, message edit,
regeneration or record write was made. External evidence screenshots:
`chat-message-versions-before.png` and `chat-canonical-versions-after.png` under
`/Users/eldonmarks/Briefcase/dev/venture-qualification-recovery-evidence/evidence/`.
The same source is now applied to this worktree. Final full verification and hooks
are required before commit. This does not qualify persisted branch history.
