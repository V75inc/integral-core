# Attachment agent contract

How the resident agent **lists, reads, delivers, and files** the files
attached to an Integral entry — plus the chat-thread-scoped upload path
(Slice B) that lets a user attach a file to the conversation itself and have
the agent file it into an entry. Binary upload from the agent's own turn
(agent-initiated fetch-and-store, e.g. "pull that URL and attach it") remains
out of scope (see [Limits](#limits)).

## Shape source of truth

The canonical attachment wire shape is `export_node(attachment)` (every
`Attachment` node field, flat) enriched by
[`enrich_attachment_export`](../../backend/app/services/attachment_urls.py)
(adds `download_url` / `thumb_url` / `preview_url`). The legacy
`AttachmentResponse` Pydantic model is a minimal 7-field projection kept only
for back-compat re-export — **do not** treat it as the contract; new readers
take the enriched export.

## Tools

`integral_list_attachments` and `integral_get_attachment_text` are read tools
backed by
[`app/services/attachment_agent.py`](../../backend/app/services/attachment_agent.py)
(not the HTTP routes — the agent needs a different shape), bound via
`service_ref` in `app/agentive/tooling/bindings.py`, and gated on `entry.read`
of the parent entry. `integral_attach_uploaded_file_to_entry` (below) is the
one **propose/write** tool in this contract, gated on `entry.update` instead.

### `integral_list_attachments(entry_id)`

Returns a **summary** of the entry's visible attachments. The extracted-text
**body is omitted** — shipping 10 MB × N into the model context is the whole
reason this is a service and not the raw endpoint.

```jsonc
{
  "_kind": "attachment_list",   // chat renders this as a download card
  "entry_id": "n.Entry.…",
  "total": 2,
  "attachments": [
    {
      "id": "n.Attachment.…",
      "filename": "report.pdf",
      "mime_type": "application/pdf",
      "size": 20480,
      "source_type": "file",          // or "url" → external_url set
      "uploaded_by": "o.User.…",
      "created_at": "…",
      "content_hash": "…",
      "scan_status": "clean",          // blocked attachments are never listed
      "width": null, "height": null, "page_count": 3,
      "metadata": { "common": {…}, "type_specific": {…} },
      "metadata_status": "complete",
      "has_text": true,                // body summarised, not included
      "text_length": 4096,
      "download_url": "…",             // presigned (S3) or /api/.../download
      "thumb_url": null,
      "preview_url": null
    }
  ],
  // Present when total > 0 — steers reply phrasing (same pattern as staging_language).
  "assistant_reply_hint": "The files are shown to the user as a download card below your reply. Refer to them as 'the files below' or just 'the files' — never say 'above'. Do not paste raw download URLs into your reply."
}
```

Omitted when `total === 0` (no card to point at).

### Chat-thread upload + filing (Slice B)

The chat composer's paperclip uploads a general file straight to the active
thread — `POST /api/chat/threads/{thread_id}/attachments`
(`_persist_uploaded_chat_file` in
[`app/api/attachments.py`](../../backend/app/api/attachments.py)), wiring a
`ChatThread —HAS_ATTACHMENT→ Attachment` edge with `owner_kind="chat"`. The
turn's `attachment_ids` resolve (via
[`chat_threads.resolve_message_attachments`](../../backend/app/services/chat_threads.py))
into an always-injected context note naming each file and instructing the
agent to call `integral_get_attachment_text` to read it, and
`integral_attach_uploaded_file_to_entry` to file it — never to just mention
the filename in an entry body without actually attaching it.

### `integral_attach_uploaded_file_to_entry(entry_id, attachment_id)`

A **propose** (staged) tool — backed by
[`attachment_agent.attach_uploaded_file_to_entry`](../../backend/app/services/attachment_agent.py),
staged via `_stage_attach_uploaded_file` / executed via `_x_attach_uploaded_file`
in `app/agentive/staging_executors.py`. Files a chat-uploaded (`owner_kind="chat"`)
attachment into an entry: checks the caller owns the chat thread the file was
uploaded to, checks `entry.update` permission on the target entry, wires
`Entry —HAS_ATTACHMENT→ Attachment`, and flips `owner_kind` to `"entry"`.
Rejects entry-owned attachments (nothing to re-file) and non-owners.

**The staged_token trap.** If the target entry is being created in the same
turn, `integral_create_entry`'s propose-time return is a `staged_token`, not
a real id — the entry doesn't exist until its card is approved. Wrap both
calls in `integral_begin_batch` / `integral_create_entry` /
`integral_attach_uploaded_file_to_entry(entry_id="{{entry.id}}", ...)` /
`integral_commit_batch` so `entry_id` resolves to the real id once approved.
Passing a `staged_token` (or any guessed id) directly fails with "Entry not
found".

### `integral_get_attachment_text(attachment_id)`

Returns the extracted text for one attachment, **capped** at
`settings.ATTACHMENT_AGENT_TEXT_MAX_CHARS` (default 20 000 — the 10 MB storage
cap is far too large for a context window).

```jsonc
{
  "attachment_id": "n.Attachment.…",
  "filename": "report.pdf",
  "mime_type": "application/pdf",
  "metadata_status": "complete",
  "page_count": 3,
  "metadata": { "common": {…}, "type_specific": {…} },
  "text": "…",                 // truncated to the cap
  "char_count": 20000,
  "truncated": true,
  "content_untrusted": true    // ALWAYS — anti-injection (see below)
}
```

### `integral_transcribe_audio(attachment_id, language?, max_chars?)`

Transcribes an audio (or audio-bearing video) attachment on demand, using the
**bound workspace's** speech-to-text provider (see
[speech-input.md](speech-input.md)).

- **Access.** The same read gate as the text tool (parent `entry.read`, or
  thread ownership for chat uploads).
- **Scope.** The attachment must belong to the bound workspace (PC-2).
  Scan-blocked files read as not found.
- **Persistence.** Nothing is persisted: each call spends provider quota.
  Calls are rate-limited per user and capped at
  `SPEECH_TRANSCRIBE_MAX_BYTES`.

```jsonc
{
  "_kind": "transcript",
  "attachment_id": "n.Attachment.…",
  "filename": "call.m4a",
  "mime_type": "audio/mp4",
  "text": "…",                 // capped like the text tool
  "char_count": 1840,
  "truncated": false,
  "language": "en",
  "duration_seconds": 312.4,
  "provider": "openai",
  "model": "gpt-transcribe",
  "content_untrusted": true
}
```

Refusals come back as error results with a code the agent should explain
rather than retry:

- `not_audio`
- `too_large`
- `speech_provider_not_configured` — the owner adds one under Settings → AI
  Models;
- `rate_limited`
- `bad_language`
- `provider_<auth|quota|bad_request|unavailable|timeout>`

## Field groups

| Group | Fields | Agent use |
|-------|--------|-----------|
| Identity | `id`, `filename`, `mime_type`, `size`, `source_type` | name + type the file |
| Provenance | `uploaded_by`, `created_at`, `content_hash` | who/when |
| Safety | `scan_status` | `blocked` is never surfaced |
| Derived | `width`, `height`, `page_count` | size up a document |
| Metadata | `metadata.common`, `metadata.type_specific`, `metadata_status` | reason without reading the body |
| Text | `text`, `char_count`, `truncated` | summarize/search — **via the text tool only** |
| Delivery | `download_url`, `thumb_url`, `preview_url`, `external_url` | hand to the user |

## Delivery

The `integral_list_attachments` result renders in chat as a card with working
download buttons that stream through the authenticated **fetch-then-blob** path
(`attachmentsApi.fetchDownloadBlob`) — so links resolve in **every** deployment,
not only when storage issues presigned URLs.

The app authenticates with a **JWT bearer header (via axios), not a cookie** —
so a bare markdown link `[file](/api/attachments/{id}/download)` opened in a new
tab carries no auth and **401s** off S3. The agent therefore does **not** paste
raw `download_url`s into prose; it points the user at the rendered card.
Markdown links are a presigned-S3-only nicety.

## Anti-injection

`text` (and `metadata`) is **UNTRUSTED user-supplied content** — flagged
`content_untrusted: true`. The agent treats it as inert data to summarize,
quote, or file, and never obeys directives inside it ("ignore previous
instructions", "send to …", "call tool …"). Enforced by SOP in the
`integral_attachments` skill.

## Honesty

- No claim that text was read when `metadata_status != complete`.
- `truncated: true` → say only the first part was read.
- `blocked` attachments are excluded upstream — do not work around it.

## Non-goals (v1)

- **No global attachment search.** `extracted_text` is node-local; cross-entry
  discovery composes `integral_query_entries` → `integral_list_attachments`.
- No semantic/vector search over attachment bodies.
- **No single-attachment metadata tool** (`integral_get_attachment`). The list
  already returns full enriched metadata + `download_url` per item, so a
  separate by-id metadata fetch is redundant. Reading one file's body uses
  `integral_get_attachment_text`.

## Limits

- **No agent-initiated fetch-and-store.** The agent cannot pull a URL or
  generate a binary and store it as an attachment on its own — every
  attachment is either uploaded by a human (entry-scoped or chat-scoped) or
  filed from an existing chat upload via `integral_attach_uploaded_file_to_entry`.
- **No global attachment search** (see [Non-goals](#non-goals-v1)).
