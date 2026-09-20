"""Live transaction proof for graph writes used by app operations.

This is the WP-00 prerequisite: a command transaction must be able to include
both node materialisation and its structural edge, rather than just raw object
documents such as a work receipt.
"""

from __future__ import annotations

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
