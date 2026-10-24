# Attachments and agent access

Attachments belong to authorized operational records through structural edges. Upload validation, content identity, deduplication, metadata, extraction, storage, and read authorization are separate responsibilities.

Agents use the declared file capabilities and scoped context. A file ID alone cannot authorize reading bytes from another workspace. Relation/member/file values should appear as named destinations or readable unavailable states; machine IDs remain in execution payloads.

Default limits are 500 MiB per attachment, 1 GiB and ten files per batch, 10 MiB extracted text, and 20000 characters in agent-facing text. Operators can change these settings. Extraction is bounded and format-dependent; it does not prove complete document understanding.

Metadata extraction has a separate default 50 MiB input budget and two
concurrent jobs per process (`ATTACHMENT_METADATA_MAX_BYTES`,
`ATTACHMENT_METADATA_CONCURRENCY`). Larger originals remain stored and
available for authorized download; their automatic metadata is marked skipped.
Overlapping requests for the same attachment join the current extraction,
including manual reprocessing. A later explicit reprocess runs anew.

File parsers, image thumbnails and document/signature rendering run outside the
API event loop; graph persistence stays in its original task. Automatic PDF and
slide extraction samples at most 200 pages/slides; spreadsheets sample at most
20 sheets, 200 rows and 128 columns, and document text at most 10000 paragraphs.
Sampled outputs report partial status. Office archives are checked before
parsing (4096 members, 100 MiB expanded); video inspection terminates after 30
seconds. These limits protect shared hosted resources and do not certify full
content extraction or adversarial parser isolation.

Office previews have one conversion slot per process and share overlapping
requests for the same attachment. Cache checks read metadata rather than the
entire PDF. Conversion uses a private LibreOffice profile and kills/reaps the
process on timeout or cancellation. Inputs and generated PDFs use the separate
`ATTACHMENT_PREVIEW_MAX_BYTES` budget (50 MiB by default); download remains
available when automatic preview is too large.

Chunked uploads are off by default and need storage qualification. Authorized preview/download, content-type handling, URL safety, and persistence must be checked in the browser and API. Deduplication must preserve record access rather than making content globally readable.

The default scanner is `noop`. No malware-screening assurance follows until a real scanner is configured and tested. See [configuration](../ops/CONFIGURATION.md) and [file qualification](../ops/QUALIFICATION.md).
