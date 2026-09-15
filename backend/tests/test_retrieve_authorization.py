"""Authorization isolation for semantic retrieval — FAST, default-running guard.

Invariant I-RET-01 / I-RET-02 (permission-filter-at-retrieval / no-index-bypass):
a user may only retrieve entries they are allowed to read. The embedding store
(pgvector, atlas, …) is a side-index with NO permission awareness — it can
return ANY entry, even at top similarity under universe-wide scope. The
authoritative gate is the per-candidate ``policy_evaluate`` filter inside
``_retrieve_semantic``.

A comprehensive end-to-end version (full app + two users) lives in
``test_retrieve.py::test_no_authorization_bypass_via_index``, but that whole
module is ``slow``-marked and pruned from the default / CI suite, so the headline
security gate never runs in CI. These units are deliberately FAST (no app, no
model, no DB — the store / model / policy / Entry are stubbed) and carry NO
``slow`` marker, so a regression in the gate — including one introduced via the
new pgvector store — fails the default suite and CI immediately.
"""

import pytest

from app.api import retrieve as rm
from app.schemas.policy import Subject
from app.schemas.retrieve import RetrieveRequest

FORBIDDEN = "n.Entry.forbidden_must_not_leak"
OK = "n.Entry.caller_accessible"


class _FakeEntry:
    def __init__(self, _id: str) -> None:
        self.id = _id
        self.track_id = "n.Track.t"
        self.status = "active"
        self.title = _id
        self.provenance = None
        self.type_id = ""


class _Decision:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed


def _wire_stubs(monkeypatch, *, candidates):
    """Stub the store/model/Entry/policy so _retrieve_semantic runs offline.

    Policy denies FORBIDDEN, allows everything else.
    """

    async def fake_embed(_q):
        return [0.0, 0.0, 0.0]

    class FakeStore:
        async def search(self, vector, k=50, scope=None):
            return list(candidates)

    async def fake_entry_get(eid):
        return _FakeEntry(eid)

    async def fake_policy(*, subject, action, resource):
        return _Decision(resource.id != FORBIDDEN)

    monkeypatch.setattr(rm, "embed_query_text", fake_embed)
    monkeypatch.setattr(rm, "get_embedding_store", lambda: FakeStore())
    monkeypatch.setattr(rm.Entry, "get", staticmethod(fake_entry_get))
    monkeypatch.setattr(rm, "policy_evaluate", fake_policy)


@pytest.mark.asyncio
async def test_semantic_drops_inaccessible_candidate_even_at_top_rank(monkeypatch):
    """The store returns a forbidden entry FIRST (sim 0.99) under universe-wide
    scope — the adversarial case. The policy filter MUST drop it: only the
    caller's own entry surfaces, the drop is counted, and the forbidden id is
    never exposed (only the aggregate ``dropped`` is observable)."""
    _wire_stubs(monkeypatch, candidates=[(FORBIDDEN, 0.99), (OK, 0.5)])

    subject = Subject(kind="human", id="n.User.caller")
    payload = RetrieveRequest(query="anything", mode="semantic")

    results, dropped, examined = await rm._retrieve_semantic(
        subject, payload, k=150, top_n=20
    )
    returned = {r.entry_id for r in results}

    assert (
        FORBIDDEN not in returned
    ), "AUTHORIZATION BYPASS: forbidden entry leaked from the index"
    assert OK in returned, "the caller's own accessible entry must surface"
    assert dropped == 1, "the dropped forbidden candidate must be counted"
    assert examined == 2


@pytest.mark.asyncio
async def test_semantic_scope_is_not_a_permission_boundary(monkeypatch):
    """A track-scoped query still policy-filters every candidate — the scope
    pre-filter is a perf hint, never a permission boundary (I-RET-02)."""
    _wire_stubs(monkeypatch, candidates=[(FORBIDDEN, 0.91), (OK, 0.4)])

    subject = Subject(kind="human", id="n.User.caller")
    payload = RetrieveRequest(
        query="anything", mode="semantic", scope="track:n.Track.t"
    )

    results, dropped, _examined = await rm._retrieve_semantic(
        subject, payload, k=150, top_n=20
    )
    returned = {r.entry_id for r in results}

    assert FORBIDDEN not in returned, "scope pre-filter must not bypass the policy gate"
    assert OK in returned
    assert dropped == 1
