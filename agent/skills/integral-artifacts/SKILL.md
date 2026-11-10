---
name: integral-artifacts
description: Maintains session working artifacts and creates downloadable private document drafts in Markdown, DOCX, PDF or PowerPoint from conversation content. Use for writing and revising requested files; use filing and attachment workflows for approved placement in an App or Track.
allowed-tools: integral_upsert_artifact integral_get_artifact integral_list_artifacts integral_prepare_document_attachment integral_attach_uploaded_file_to_entry integral_begin_batch integral_commit_batch
---

# Session artifacts

Working memory for this conversation. Not substrate writes. Not Prompt Sheet
cards. Any harness can call these tools.

## When to use

- Persist or refresh an `app_design_blueprint` (or other outline) the model must
  re-read on a later turn.
- Store a build `checklist` or freeform `note` keyed on the session.
- Prepare a private downloadable document draft when the user asks for a file,
  report, letter, procedure or presentation. The body is Markdown; choose
  `metadata.format` from `markdown`, `docx`, `pdf`, or `pptx`. A draft remains
  in this conversation and is not an Entry or an attached file.
- Recover a prior outline via get/list after an amend or interrupted build.

## When NOT to use

- Substrate creates/updates — those use propose/staging tools, not artifacts.
- Clarifying questions for the user — ask in prose or hand off to a skill that
  owns ask_user.
- Greenfield design gate recording — skill `integral-scaffold` owns the propose
  flow; this skill only holds the durable body.

## Grounding

1. Confirm the conversation session is live (artifacts require a session).
2. Prefer stable keys: `app_design_blueprint` for greenfield outlines.
3. `integral_list_artifacts` before inventing a new key when continuing work.

## Procedure

1. `integral_upsert_artifact` with `key`, `kind`, optional `title`, and `body`
   (markdown). For a new key pass `expected_version=0`; on a revision pass the
   version last read. Replaces prior body and bumps `version`.
2. `integral_get_artifact` by `key` when amending or building from a prior
   outline. If a version conflict occurs, reload before revising; never silently
   overwrite a newer draft.
3. `integral_list_artifacts` (optional `kind`) to discover keys.

Kinds: `app_design_blueprint`, `checklist`, `note`, `document_draft`.

For a requested file, call `integral_upsert_artifact` with kind
`document_draft`, a stable key, a title, the complete Markdown body,
`metadata={"format": "docx"}` (or the requested supported format), and the
correct expected version. The chat displays the download control returned by
Integral. Say it is a private draft; do not invent a link or claim it was filed.
Explain that rendering verifies file structure, not visual quality or factual
accuracy. If PDF creation reports characters it cannot preserve, explain the
limitation and offer Word or Markdown; do not claim that a PDF was created.
For a different format, make or revise a draft deliberately and use its
resulting version. When the user wants the actual file filed in an App or
Track, call `integral_prepare_document_attachment` with the exact draft key and
version. This creates a private conversation-owned file, not a filed Entry.
Use `integral-attachments` to select an authorized destination and stage
`integral_attach_uploaded_file_to_entry` with the returned attachment ID. For
a new Entry, put its creation and the attachment in one approval batch. The
approval includes a download for review. Say the file is filed only after the
approved operation succeeds. If preparation is unavailable, explain the error
and retain the private download rather than claiming the file was attached.
Where the chosen record has a relevant `file`/`files` field, attachment and
field population are distinct: follow the field procedure in
`integral-attachments`, including its separate reviewed update after the
attachment exists. Do not say the table's file field is populated until
readback confirms it.

## Staging discipline

Artifacts never mint staged changes. Do not claim apps/tracks exist because an
artifact was upserted. Substrate writes still go through begin_batch / commit
(or other propose tools) under their own skills.

## Forbidden patterns

- Minting Prompt Sheet cards for artifact content.
- Treating artifact upsert as a substrate write.
- Guessing artifact bodies instead of calling get after an amend turn.

## Example

```text
integral_upsert_artifact(
  key="app_design_blueprint",
  kind="app_design_blueprint",
  title="Bookstore Inventory",
  body="## Books\n- Title, Author, Stock…\n## Sales\n…"
)
# later
integral_get_artifact(key="app_design_blueprint")
```
