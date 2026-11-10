"""The 913 KB definition check: every authority check per call, full verify per window."""

from __future__ import annotations

import copy

import pytest

from app.exceptions import AuthorizationUnavailableError
from app.services import access_delegation as ad

pytestmark = pytest.mark.asyncio


class _Db:
    """Counts what each call reads; serves one app and one active definition."""

    def __init__(self, definition_row):
        self.definition_row = definition_row
        self.full_reads = 0
        self.projected_reads = 0
        self.active_ids = [definition_row["id"]]
        self.edge_rows = [object()]

    async def find_projected(self, collection, query, *, fields, limit=None):
        self.projected_reads += 1
        ctx = self.definition_row["context"]
        return [
            {
                "id": self.definition_row["id"],
                "entity": self.definition_row["entity"],
                **{
                    f"context.{k}": ctx[k]
                    for k in (
                        "status",
                        "app_id",
                        "workspace_id",
                        "revision",
                        "manifest_fingerprint",
                    )
                },
            }
        ]

    async def find_ids(self, collection, query, *, limit=None, **_):
        return list(self.active_ids)


def _row(manifest):
    from app.models.nodes import ApplicationDefinition
    from app.services.application_definitions import definition_fingerprint

    return {
        "id": "n.ApplicationDefinition.d1",
        "entity": ApplicationDefinition._entity_name(),
        "context": {
            "status": "active",
            "app_id": "a1",
            "workspace_id": "w1",
            "revision": 3,
            "manifest_fingerprint": definition_fingerprint(manifest),
            "canonical_manifest": manifest,
        },
    }


@pytest.fixture
def wired(monkeypatch):
    manifest = {"app": {"operations": [{"key": "x"}] * 50}}
    row = _row(manifest)
    db = _Db(row)
    app_row = {
        "id": "a1",
        "context": {
            "workspace_id": "w1",
            "active_definition_id": row["id"],
            "active_definition_revision": 3,
        },
    }

    async def record(model, identity):
        if identity == "a1":
            return app_row
        db.full_reads += 1
        return copy.deepcopy(db.definition_row)

    async def edges(source, target, edge):
        return db.edge_rows

    async def locate(*a, **k):
        return object()

    async def locate_many(kind, ids, **k):
        return {i: object() for i in ids}

    class Ctx:
        database = db

    monkeypatch.setattr(ad, "_record", record)
    monkeypatch.setattr(ad, "_edges", edges)
    monkeypatch.setattr(ad, "locate_resource", locate)
    monkeypatch.setattr(ad, "locate_resources", locate_many)
    monkeypatch.setattr(ad, "get_default_context", lambda: Ctx)
    monkeypatch.delenv("INTEGRAL_DEFINITION_VERIFY_TTL_SECONDS", raising=False)
    ad.invalidate_verified_definitions()
    yield db
    ad.invalidate_verified_definitions()


async def test_unchanged_definition_reads_the_manifest_once_per_window(wired):
    for _ in range(5):
        _, definition = await ad._current_definition_uncached("a1")
        assert definition["context"]["canonical_manifest"]["app"]
    assert wired.full_reads == 1
    assert wired.projected_reads == 5  # the cheap authority read still runs every call


async def test_ttl_zero_verifies_every_call(wired, monkeypatch):
    monkeypatch.setenv("INTEGRAL_DEFINITION_VERIFY_TTL_SECONDS", "0")
    for _ in range(3):
        await ad._current_definition_uncached("a1")
    assert wired.full_reads == 3


async def test_a_tampered_manifest_is_caught_once_the_window_ends(wired, monkeypatch):
    await ad._current_definition_uncached("a1")
    wired.definition_row["context"]["canonical_manifest"]["app"]["operations"].append(
        {"key": "evil"}
    )
    ad.invalidate_verified_definitions()
    with pytest.raises(AuthorizationUnavailableError):
        await ad._current_definition_uncached("a1")


async def test_a_tampered_manifest_is_caught_when_the_window_is_zero(
    wired, monkeypatch
):
    monkeypatch.setenv("INTEGRAL_DEFINITION_VERIFY_TTL_SECONDS", "0")
    wired.definition_row["context"]["canonical_manifest"]["app"]["operations"].append(
        {"key": "evil"}
    )
    with pytest.raises(AuthorizationUnavailableError):
        await ad._current_definition_uncached("a1")


async def test_authority_checks_still_run_inside_the_window(wired):
    await ad._current_definition_uncached("a1")
    wired.definition_row["context"][
        "status"
    ] = "superseded"  # status is read every call
    with pytest.raises(AuthorizationUnavailableError):
        await ad._current_definition_uncached("a1")


async def test_two_active_definitions_are_refused_inside_the_window(wired):
    await ad._current_definition_uncached("a1")
    wired.active_ids = [wired.definition_row["id"], "n.ApplicationDefinition.other"]
    with pytest.raises(AuthorizationUnavailableError):
        await ad._current_definition_uncached("a1")


async def test_a_missing_attachment_is_refused_inside_the_window(wired):
    await ad._current_definition_uncached("a1")
    wired.edge_rows = []
    with pytest.raises(AuthorizationUnavailableError):
        await ad._current_definition_uncached("a1")


async def test_the_definition_changed_signal_drops_the_memo(wired):
    from app.middleware.permissions_cache import note_definition_changed

    await ad._current_definition_uncached("a1")
    note_definition_changed()
    await ad._current_definition_uncached("a1")
    assert wired.full_reads == 2
