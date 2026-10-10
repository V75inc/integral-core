# Model discovery and indexing

Catalog discovery uses package name, description, and discovery tags within the caller's authorized visibility. Metadata is not permission. Do not load private package details simply to score them for an unauthorized result.

Update indexes and fingerprints through the canonical library loader/sync path. Search results should distinguish a reusable catalog artifact from an installed App and its attached schema. Resolve ambiguous type hints explicitly rather than choosing an arbitrary tied package.

Retrieval and vector configuration are separate from package discovery. Test scope prefiltering, index updates, deleted records, and backend-specific semantics before claiming equivalent results across stores.
