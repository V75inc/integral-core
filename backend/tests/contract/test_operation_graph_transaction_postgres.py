"""Live transaction proof for graph writes used by app operations.

This is the WP-00 prerequisite: a command transaction must be able to include
both node materialisation and its structural edge, rather than just raw object
documents such as a work receipt.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from jvspatial.core import Edge, Node

from app.services.app_operations.transaction_scope import postgres_graph_transaction


class TransactionProbeNode(Node):
    """Small graph type used only to prove the public transaction seam."""

    label: str = ""


class TransactionProbeEdge(Edge):
    """Structural edge used only to prove the public transaction seam."""


class _RollbackProbe(Exception):
    """Private control-flow exception for rollback proof."""


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_transaction_supports_concurrent_graph_reads_and_bounded_pagination(
    postgres_raw_db,
) -> None:
    from app.services.pagination import paginate_entity_find

    suffix = uuid.uuid4().hex
    async with postgres_graph_transaction(postgres_raw_db):
        parent = await TransactionProbeNode.create(label=f"parent-{suffix}")
        children = [
            await TransactionProbeNode.create(label=f"child-{suffix}") for _ in range(4)
        ]
        for child in children:
            await parent.connect(child, edge=TransactionProbeEdge)

        # Permission resolvers fan out these parent walks on the same handle.
        parents = await asyncio.gather(
            *(
                child.nodes(
                    edge=[TransactionProbeEdge],
                    direction="in",
                    node=[TransactionProbeNode],
                )
                for child in children
            )
        )
        assert all([node.id for node in result] == [parent.id] for result in parents)

        found = await asyncio.gather(
            *(
                TransactionProbeNode.find_one({"context.label": f"parent-{suffix}"})
                for _ in range(12)
            )
        )
        assert all(node is not None and node.id == parent.id for node in found)
        assert await TransactionProbeNode.find_one({"id": "missing-probe"}) is None

        first, metadata = await paginate_entity_find(
            TransactionProbeNode,
            {"context.label": f"child-{suffix}"},
            None,
            2,
            sort=[("id", 1)],
            include_total=True,
        )
        assert len(first) == 2
        assert metadata["total"] is None
        assert metadata["has_more"] is True
        assert metadata["next_cursor"]
        second, final = await paginate_entity_find(
            TransactionProbeNode,
            {"context.label": f"child-{suffix}"},
            metadata["next_cursor"],
            2,
            sort=[("id", 1)],
            include_total=True,
        )
        assert {node.id for node in first + second} == {node.id for node in children}
        assert final["has_more"] is False
        assert final["next_cursor"] is None


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_graph_transaction_commits_node_and_edge_rows(postgres_raw_db) -> None:
    suffix = uuid.uuid4().hex
    async with postgres_graph_transaction(postgres_raw_db):
        parent = await TransactionProbeNode.create(label=f"parent-{suffix}")
        child = await TransactionProbeNode.create(label=f"child-{suffix}")
        edge = await parent.connect(child, edge=TransactionProbeEdge)
        parent_id, child_id, edge_id = parent.id, child.id, edge.id

    assert await postgres_raw_db.get("node", parent_id) is not None
    assert await postgres_raw_db.get("node", child_id) is not None
    assert await postgres_raw_db.get("edge", edge_id) is not None


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_graph_transaction_rolls_back_node_and_edge_rows(postgres_raw_db) -> None:
    suffix = uuid.uuid4().hex
    parent_id = child_id = edge_id = ""
    with pytest.raises(_RollbackProbe):
        async with postgres_graph_transaction(postgres_raw_db):
            parent = await TransactionProbeNode.create(label=f"parent-{suffix}")
            child = await TransactionProbeNode.create(label=f"child-{suffix}")
            edge = await parent.connect(child, edge=TransactionProbeEdge)
            parent_id, child_id, edge_id = parent.id, child.id, edge.id
            raise _RollbackProbe()

    assert await postgres_raw_db.get("node", parent_id) is None
    assert await postgres_raw_db.get("node", child_id) is None
    assert await postgres_raw_db.get("edge", edge_id) is None
