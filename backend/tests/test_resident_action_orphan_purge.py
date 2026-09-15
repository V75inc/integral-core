"""Regression test for the dead-resident-action orphan purge.

Renaming the resident action class (``IntegralEmbeddedAction`` ->
``EmbeddedIntegralAction``) strands the previously-persisted action node under
the OLD class id-prefix (jvspatial encodes the class in the node id,
``n.<Class>.<uuid>``). The dead class cannot be resolved by the ORM, so
jvagent's ``register_action`` cannot find that node to reuse/replace and
collides on the ``(agent_id, label)`` unique index — breaking agentive
startup. ``_purge_dead_resident_action_orphans`` clears such orphans (and their
edges) before bootstrap.

The purge must:
  * delete an orphan node under a retired (dead-class) prefix,
  * delete the edges referencing it (no dangling edge),
  * leave the live current-class node untouched.
"""

import pytest

from app.main import (
    _DEAD_RESIDENT_ACTION_ID_PREFIXES,
    _RESIDENT_ACTION_LABEL,
    _purge_dead_resident_action_orphans,
)


def test_dead_prefix_is_the_retired_class_not_the_live_one():
    """The retired prefix list targets the OLD class, never the current one."""
    assert "n.IntegralEmbeddedAction." in _DEAD_RESIDENT_ACTION_ID_PREFIXES
    # The live class must NOT be in the dead set, or the purge would delete the
    # in-use action node.
    assert not any(
        p.startswith("n.EmbeddedIntegralAction.")
        for p in _DEAD_RESIDENT_ACTION_ID_PREFIXES
    )


@pytest.mark.asyncio
async def test_purge_removes_orphan_and_edges_keeps_live_node():
    """Orphan node + its edges are purged; the live current-class node stays."""
    from jvspatial.db import get_database_manager

    db = get_database_manager().get_prime_database()

    orphan_id = "n.IntegralEmbeddedAction.purgetest0001"
    live_id = "n.EmbeddedIntegralAction.purgetest0002"
    orphan_edge_id = "e.Edge.purgetestedge0001"
    agent_id = "n.Agent.purgetestagent"

    # Seed: an orphan (dead class) + its registry edge + a live (current class)
    # node, all carrying the resident-action label.
    await db.save(
        "node",
        {
            "id": orphan_id,
            "context": {
                "agent_id": agent_id,
                "namespace": "integral",
                "label": _RESIDENT_ACTION_LABEL,
            },
        },
    )
    await db.save(
        "node",
        {
            "id": live_id,
            "context": {
                "agent_id": agent_id,
                "namespace": "integral",
                "label": _RESIDENT_ACTION_LABEL,
            },
        },
    )
    await db.save(
        "edge",
        {
            "id": orphan_edge_id,
            "entity": "Edge",
            "source": "n.Actions.purgetestreg",
            "target": orphan_id,
            "name": "",
        },
    )

    # Sanity: everything present before the purge.
    assert await db.get("node", orphan_id) is not None
    assert await db.get("node", live_id) is not None
    assert await db.get("edge", orphan_edge_id) is not None

    await _purge_dead_resident_action_orphans()

    # Orphan node + its edge are gone; the live node is untouched.
    assert await db.get("node", orphan_id) is None, "orphan node should be purged"
    assert (
        await db.get("edge", orphan_edge_id) is None
    ), "orphan's edge should be purged (no dangling edge)"
    assert (
        await db.get("node", live_id) is not None
    ), "live current-class node must NOT be deleted"


@pytest.mark.asyncio
async def test_purge_is_noop_on_clean_db():
    """No orphan present → purge is a harmless no-op (does not raise)."""
    await _purge_dead_resident_action_orphans()
