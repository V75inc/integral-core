"""TEST-01 gap closure for POST /api/retrieve (Plan 07-05).

Audit identified missing axis: ``denied`` (401/403). The retrieve handler
raises ``MissingAuthenticationError`` when ``resolve_principal_id`` returns
None — this gap test exercises that path directly so the audit detects the
denied axis as covered.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_retrieve_missing_principal_raises_401() -> None:
    """POST /api/retrieve with no resolved principal → MissingAuthenticationError (401)."""
    from app.api.errors import MissingAuthenticationError
    from app.api.retrieve import retrieve as retrieve_handler

    request = MagicMock()

    async def _json() -> dict:
        return {"query": "hello"}

    request.json = _json
    request.state.user = None
    with patch("app.api.retrieve.resolve_principal_id", return_value=None):
        with pytest.raises(MissingAuthenticationError):
            await retrieve_handler(request)
