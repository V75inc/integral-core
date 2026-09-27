"""The agent request stub must satisfy route query-param calls."""

from types import SimpleNamespace

import pytest

from app.agentive.tooling.invoke import stub_request
from app.api.entry_relations import list_entry_relations
from app.api.errors import ResourceNotFoundError


def test_stub_query_params_support_get_and_getlist():
    """Repeated entry_type values are readable. A missing key is an empty list."""
    request = stub_request(
        SimpleNamespace(id="o.User.test"),
        None,
        query={"direction": "out", "entry_type": ["rental", "invoice"]},
    )
    assert request.query_params.get("direction") == "out"
    assert request.query_params.getlist("entry_type") == ["rental", "invoice"]
    assert request.query_params.getlist("missing") == []


@pytest.mark.asyncio
async def test_get_related_in_process_passes_getlist():
    """A missing entry is not found. It is not an AttributeError on getlist."""
    request = stub_request(
        SimpleNamespace(id="o.User.test"),
        None,
        query={"direction": "out", "include_anchors": "true"},
    )
    with pytest.raises(ResourceNotFoundError):
        await list_entry_relations(request, entry_id="n.Entry.missing")
