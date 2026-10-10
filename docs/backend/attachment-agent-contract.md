# Attachments and agent access

Attachments belong to authorized operational records through structural edges. Upload validation, content identity, deduplication, metadata, extraction, storage, and read authorization are separate responsibilities.

Agents use the declared file capabilities and scoped context. A file ID alone cannot authorize reading bytes from another workspace. Relation/member/file values should appear as named destinations or readable unavailable states; machine IDs remain in execution payloads.

Default limits are 500 MiB per attachment, 1 GiB and ten files per batch, 10 MiB extracted text, and 20000 characters in agent-facing text. Operators can change these settings. Extraction is bounded and format-dependent; it does not prove complete document understanding.

Chunked uploads are off by default and need storage qualification. Authorized preview/download, content-type handling, URL safety, and persistence must be checked in the browser and API. Deduplication must preserve record access rather than making content globally readable.

The default scanner is `noop`. No malware-screening assurance follows until a real scanner is configured and tested. See [configuration](../ops/CONFIGURATION.md) and [file qualification](../ops/QUALIFICATION.md).
