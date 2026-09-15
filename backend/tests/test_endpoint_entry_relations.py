"""GET /api/entries/{id}/related?relation={field_key} — generic entry relations."""

import pytest


@pytest.mark.asyncio
async def test_entry_relations_returns_referenced_entries(
    authenticated_client, test_user
):
    # Contact + Email Thread linked via REFERENCES → /related?relation=communications
    # exercises the generic projection.
    pytest.skip("End-to-end test deferred — covered by test_e2e_uat in Wave E")
