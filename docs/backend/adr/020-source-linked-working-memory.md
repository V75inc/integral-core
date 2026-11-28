# ADR 020 — Source-linked working memory

**Status:** Implemented foundation; bounded native conversation recall.

**Date:** 2026-11-27

## Decision

Integral keeps one resident and one canonical graph. Working memory is a
**disposable retrieval projection**, not a biography, second resident, Markdown
journal, autonomous instruction store, or source of truth. The user's committed
native transcript and current graph resources remain authoritative.

A `WorkingMemoryIndex` jvspatial Object stores one encrypted, primary-key-
addressed projection for the exact `(principal, workspace, published-profile
key)` namespace. The scope digest is not permission. The payload contains up
to 256 source locators: original thread/message identity, text fingerprint,
date, source kind and at most 32 bounded lexical subject terms. Verified App
search results additionally retain the exact App/Entry locator and source
revision from the authorized search service. No passage text, tool arguments,
attachment bytes, new physical index or separate vector store is retained.
The Object is scalar metadata rather than a graph participant (I-GRAPH-02).

Successful native submissions contribute only committed user-authored text;
terminal successful resident turns contribute committed result locators and
bounded actual retrieval locators captured in that attempt. Inserts and
revision-checked replacements merge by source identity, not by invented
semantic fact. Errors, interruption, missing encryption and contention can
leave a projection incomplete without changing the original chat receipt.
Requests with no source or a corrupt/expired projection do not invent recall.

## Recall and authority

- Voice uses `recall_integral_conversation` only for explicit historical
  caller statements about a distinctive subject, behind the existing spoken
  intent gate. Exactly one matching caller quotation can be read directly,
  dated and identified as historical; its canonical graph containment,
  namespace, text fingerprint and current caller access are verified before
  provider handoff and again before first audio. No heavy-model call is
  needed for that direct hit. A miss or competing source delegates the
  original question to the resident without a second pre-action cue.
- The resident's normal broker exposes `integral_recall_prior_conversation`.
  It requires a server-bound native WorkItem and current conversation scope;
  an unbound MCP call has no access. It returns at most three dated caller
  quotations, explicitly as data, not current facts, directives or approval.
  The currently accepted message is excluded. When no match is indexed,
  the resident may recover at most eight scoped native threads and eight
  recent user messages in each using graph adjacency. Only verified matching
  sources are projected; there is no unbounded startup migration or scan.
- Prior assistant text is indexed for provenance, but **never replayed as
  current evidence**. The resident can receive at most three previously
  retrieved resource locators after rechecking the exact source revision,
  canonical transcript, current App/Entry containment and ordinary read
  authority. Installed Apps requiring declared queries and published-profile
  resource reads must use their own current authorized tools; memory does
  not bypass those boundaries. A changed, removed or unreadable source does
  not produce a fact. Present-tense answers require a fresh authorized read.
- Source deletion and archive immediately prevent recall. Normal hard chat
  deletion also prunes its locators when outside the graph transaction;
  failed cleanup leaves only bounded encrypted inaccessible metadata until
  eviction. Workspace membership and profile availability are checked anew.
  Connector and visitor session histories do not join the native staff index.

## Invariants and qualification

Preserves I-GRAPH-01/02 (original rooted source), I-RUNTIME-01 (bounded work,
existing shared physical access path), I-RET-01 (permission at retrieval),
I-HARNESS-01 and I-CONTEXT-02 (no authority or checkpoint mutation), I-CHAT-01
(no raw node IDs in human-facing messages), and new I-CONTEXT-03 below.
No model or embedding call is added to projection or lookup. Retrieval is an
explicit tool, never an automatic cross-conversation prompt dump. Scope
identity and current authorization come from the host, not model arguments.

**Limits:** lexical subject matching, not exhaustive semantic memory. One small
spelling error may match only when at least two other subject words match;
competing sources still require a resident lookup. The index has 256
locators per scope, 1,600-character caller source cap, no complete migration
of older history, no automatic interpretation of pronoun-only corrections,
no prior tool-output or secret replay, and no inferred current decision. A
projection is recoverable from canonical history when encryption is available;
not a second authority. Measurements use isolated PostgreSQL fixtures and
distinguish local lookup latency from provider latency and billed model calls.

Tests: `backend/tests/test_working_memory.py` (PostgreSQL source, scope,
revision, deletion, ambiguity, CAS, physical index inventory and timing;
voice entry point), plus native voice, retrieval, and harness regressions.
