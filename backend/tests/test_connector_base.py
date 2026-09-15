"""Plan 05-01 — SyncConnector ABC + decorator-style registry regression suite.

Covers all 13 behaviors locked in 05-01-PLAN.md <behavior> for Task 1.

Imports use the package-level public re-exports from ``app.services.connectors``
(NOT direct submodule imports) — proves the package-level contract.
"""

from __future__ import annotations

import hashlib

import pytest

from app.services.connectors import (
    ConflictPolicy,
    ExternalRecord,
    MaterializedEntry,
    SyncConnector,
    get_sync_connector,
    list_registered_slugs,
    register_sync_connector,
    reset_sync_registry,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    """Test isolation — clear the module-level registry between tests."""
    reset_sync_registry()
    yield
    reset_sync_registry()


# ---------------------------------------------------------------------------
# Behavior 1: ABC raises NotImplementedError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_pull_raises_not_implemented():
    """SyncConnector.sync_pull on the base class is abstract — yields nothing,
    raises NotImplementedError when iterated."""

    class _Dummy:
        id = "c-1"

    base = SyncConnector()
    with pytest.raises(NotImplementedError):
        async for _ in base.sync_pull(connector=_Dummy()):
            break


def test_to_entry_raises_not_implemented():
    """SyncConnector.to_entry on the base class is abstract."""
    base = SyncConnector()
    rec = ExternalRecord(external_id="e-1", payload={"a": 1})
    with pytest.raises(NotImplementedError):
        base.to_entry(rec)


# ---------------------------------------------------------------------------
# Behavior 2: idempotency_key_for default is deterministic + SHA-256 length
# ---------------------------------------------------------------------------


def test_idempotency_key_for_default_is_deterministic_and_sha256():
    """Default: SHA-256 of slug:external_id; same inputs → same hex; length 64."""

    class MyConn(SyncConnector):
        slug = "my_conn"

    conn = MyConn()
    rec = ExternalRecord(external_id="ext-42", payload={})
    k1 = conn.idempotency_key_for(rec)
    k2 = conn.idempotency_key_for(rec)
    assert k1 == k2
    assert len(k1) == 64
    # Verify against direct SHA-256 of "my_conn:ext-42"
    expected = hashlib.sha256(b"my_conn:ext-42").hexdigest()
    assert k1 == expected


# ---------------------------------------------------------------------------
# Behavior 3: namespace isolation (Pitfall 4) — two connectors, same external_id, different keys
# ---------------------------------------------------------------------------


def test_idempotency_key_for_namespace_isolation_pitfall_4():
    """Two connectors with different slugs but the same external_id MUST produce
    different keys — locked decision #6 / RESEARCH Pitfall 4."""

    class ConnA(SyncConnector):
        slug = "github_issues"

    class ConnB(SyncConnector):
        slug = "linear_issues"

    a = ConnA()
    b = ConnB()
    rec = ExternalRecord(external_id="123", payload={})
    assert a.idempotency_key_for(rec) != b.idempotency_key_for(rec)


# ---------------------------------------------------------------------------
# Behavior 4: subclass override of idempotency_key_for works
# ---------------------------------------------------------------------------


def test_idempotency_key_for_subclass_override():
    """Subclass that overrides idempotency_key_for returns the override value."""

    class CustomConn(SyncConnector):
        slug = "custom"

        def idempotency_key_for(self, record):
            return f"override:{record.external_id}"

    c = CustomConn()
    rec = ExternalRecord(external_id="X", payload={})
    assert c.idempotency_key_for(rec) == "override:X"


# ---------------------------------------------------------------------------
# Behavior 5: ConflictPolicy Literal accepts the 3 locked values
# ---------------------------------------------------------------------------


def test_conflict_policy_literal_members():
    """ConflictPolicy is the locked 3-value Literal."""
    from typing import get_args

    members = set(get_args(ConflictPolicy))
    assert members == {"last_write_wins", "manual_resolve", "mirror_only"}


# ---------------------------------------------------------------------------
# Behavior 6: ExternalRecord construction
# ---------------------------------------------------------------------------


def test_external_record_construction():
    """ExternalRecord(external_id, payload) constructs; updated_at defaults None."""
    rec = ExternalRecord(external_id="e-1", payload={"a": 1})
    assert rec.external_id == "e-1"
    assert rec.payload == {"a": 1}
    assert rec.updated_at is None

    # Construct with explicit updated_at
    rec2 = ExternalRecord(
        external_id="e-2", payload={"b": 2}, updated_at="2026-05-16T00:00:00Z"
    )
    assert rec2.updated_at == "2026-05-16T00:00:00Z"


# ---------------------------------------------------------------------------
# Behavior 7: MaterializedEntry shape
# ---------------------------------------------------------------------------


def test_materialized_entry_shape():
    """MaterializedEntry construction with defaults."""
    me = MaterializedEntry(
        title="t",
        body="b",
        entry_type_key="github_issue",
        tags=[],
        custom_fields={},
    )
    assert me.title == "t"
    assert me.body == "b"
    assert me.entry_type_key == "github_issue"
    assert me.tags == []
    assert me.custom_fields == {}
    assert me.external_updated_at is None


# ---------------------------------------------------------------------------
# Behavior 8: register_sync_connector decorator registers + get returns instance
# ---------------------------------------------------------------------------


def test_register_sync_connector_decorator_and_get_returns_instance():
    """Decorator registers a class; get_sync_connector returns an INSTANCE."""

    @register_sync_connector("test_a")
    class X(SyncConnector):
        slug = "test_a"

    inst = get_sync_connector("test_a")
    assert isinstance(inst, X)
    assert isinstance(inst, SyncConnector)


# ---------------------------------------------------------------------------
# Behavior 9: duplicate slug ValueError
# ---------------------------------------------------------------------------


def test_register_sync_connector_duplicate_raises():
    """Single-registration invariant — second @register on same slug raises ValueError."""

    @register_sync_connector("test_b")
    class Y(SyncConnector):
        slug = "test_b"

    with pytest.raises(ValueError, match="already registered"):

        @register_sync_connector("test_b")
        class Z(SyncConnector):
            slug = "test_b"


# ---------------------------------------------------------------------------
# Behavior 10: empty slug ValueError
# ---------------------------------------------------------------------------


def test_register_sync_connector_empty_slug_raises():
    """register_sync_connector('') raises ValueError on empty/whitespace slug."""
    with pytest.raises(ValueError, match="non-empty slug"):

        @register_sync_connector("")
        class Empty(SyncConnector):
            pass

    with pytest.raises(ValueError, match="non-empty slug"):

        @register_sync_connector("   ")
        class Whitespace(SyncConnector):
            pass


# ---------------------------------------------------------------------------
# Behavior 11: get_sync_connector unknown ValueError
# ---------------------------------------------------------------------------


def test_get_sync_connector_unknown_raises():
    """get_sync_connector for an unregistered slug raises ValueError."""
    with pytest.raises(ValueError, match="Unknown sync connector slug"):
        get_sync_connector("bogus")


# ---------------------------------------------------------------------------
# Behavior 12: reset_sync_registry empties
# ---------------------------------------------------------------------------


def test_reset_sync_registry_empties():
    """reset_sync_registry() removes all registrations."""

    @register_sync_connector("ephemeral")
    class E(SyncConnector):
        slug = "ephemeral"

    # Sanity: it's registered
    assert isinstance(get_sync_connector("ephemeral"), SyncConnector)

    reset_sync_registry()

    with pytest.raises(ValueError, match="Unknown sync connector slug"):
        get_sync_connector("ephemeral")


# ---------------------------------------------------------------------------
# Behavior 13: list_registered_slugs returns sorted
# ---------------------------------------------------------------------------


def test_list_registered_slugs_sorted():
    """list_registered_slugs returns slugs in sorted order."""

    @register_sync_connector("z_slug")
    class Z(SyncConnector):
        slug = "z_slug"

    @register_sync_connector("a_slug")
    class A(SyncConnector):
        slug = "a_slug"

    @register_sync_connector("m_slug")
    class M(SyncConnector):
        slug = "m_slug"

    assert list_registered_slugs() == ["a_slug", "m_slug", "z_slug"]


# ---------------------------------------------------------------------------
# Defaults / sanity: slug normalization (lowercase + strip)
# ---------------------------------------------------------------------------


def test_register_slug_is_normalized():
    """Slug registration normalizes whitespace + casefold (matches Phase 1 idiom)."""

    @register_sync_connector("MixedCase_Slug")
    class M(SyncConnector):
        pass

    # Lookup with normalized form succeeds
    assert isinstance(get_sync_connector("mixedcase_slug"), M)
    assert isinstance(get_sync_connector("  MixedCase_Slug  "), M)
