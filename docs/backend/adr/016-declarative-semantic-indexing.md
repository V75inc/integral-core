# ADR-016: Declarative, permission-aware passage indexing

**Status:** Implemented in PR #162; deployment qualification pending.
**Date:** 20 October 2026

## Context

Record-wide embeddings and manual backfills lose relevant tails, mix encoder
versions and put model latency on writes. Index coverage is separate from model
configuration. Workspace-wide RAG also needs current access and canonical source
checks; a vector-store metadata filter cannot become an authority store.

## Decision

Keep canonical records and durable index intent in the graph transaction.
A background worker reconciles existing sources and delivers retryable,
generation/lease-fenced jobs. Publish versioned passages atomically under a
cross-worker guard after checking the current source. Model and vector work run
outside primary writes. Configuration and its rebuild/audit intent commit
together; audit delivery happens after commit.

Track policies select canonical text/metadata; EntryType declarations only narrow
that selection. Local ONNX embeddings use tokenizer-aligned chunks with preserved
source offsets. Model/revision/dimension/chunk/format settings identify a separate
profile; old vectors never answer queries encoded with a new profile.

PostgreSQL narrows workspace, readable Tracks or direct Entry grants and the
active profile before the candidate cap. It ranks by exact cosine and, for
hybrid mode, lexical reciprocal-rank fusion. Current entry.read, containment,
source hash/offsets and governed App queries are checked before exposing any
content. Source paths remain relative to the active installation.

## Alternatives and trade-offs

- Synchronous embedding couples write availability to model/vector health.
  Durable intent makes delivery eventual and requires honest readiness reporting.
- A single vector per record truncates long content and forces larger rereads.
  Passages retain relevant evidence with an explicit returned-text budget.
- Global approximate-neighbor candidates can be exhausted by unauthorized rows.
  Scoped exact ranking favors predictable permitted recall for the current
  implementation. Profile HNSW indexes are provisioned but this query does not
  use them; large-scope performance needs separate qualification.
- A model/provider selector alone does not prove indexing or source coverage.
  Status probes the actual encoder/store and compares bounded canonical sources,
  receipts and stored hashes. Coverage is point-in-time and can be truncated.

## Consequences and qualification

Old profiles consume storage until reviewed offline retention work. Invalid
sources/jobs retry independently; model/store outages preserve canonical keyword
reads with a cooldown. Atlas and null remain separate adapters; these passage
semantics are PostgreSQL specific. No automatic external embedding service or
attachment/link expansion is introduced.

Index management requires current configuration authority and normal agent
approval; a queued rebuild is never represented as completion. Real encoder,
PostgreSQL publication/rollback, revocation, stale-source and lease tests are
required in addition to default suites. Latency, labelled evidence accuracy and
provider token usage are measured separately.

See the [maintained contract](../semantic-indexing.md),
[invariants I-RET-01–07](../../INVARIANTS.md#retrieval-scratch-and-connectors),
[rollout runbook](../../ops/DEPLOY.md#search-rollout-and-recovery), and
[local qualification evidence](../../ops/qualification/semantic-search-2026-10-20/REPORT.md).
