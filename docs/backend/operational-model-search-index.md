# Model discovery and indexing

Catalog discovery uses package name, description, and discovery tags within the caller's authorized visibility. Metadata is not permission. Do not load private package details simply to score them for an unauthorized result.

Update indexes and fingerprints through the canonical library loader/sync path. Search results should distinguish a reusable catalog artifact from an installed App and its attached schema. Resolve ambiguous type hints explicitly rather than choosing an arbitrary tied package.

Record indexing and retrieval are separate from package discovery. Use the
[declarative indexing guide](semantic-indexing.md) for Track/EntryType policies,
pgvector passages, agent capabilities, coverage and rebuilding. Catalog tags
never select record fields or grant retrieval access.
