"""TEST-01 gap closure for /api/approvals surface (Plan 07-05).

Plan 07-04 shipped happy + 400-axis coverage in ``test_approvals.py``.
The audit identified missing axis: ``denied`` (401/403). The three pending-
writes endpoints all raise ``MissingAuthenticationError`` when no principal
resolves; ``approve`` + ``reject`` additionally raise
``InsufficientPermissionsError`` when policy denies. This file exercises
the denied path directly via ``pytest.raises`` — the audit recognizes the
exception as the denied axis.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_list_approvals_missing_principal_raises_401() -> None:
    """GET /api/approvals with no principal → MissingAuthenticationError (401)."""
    from app.api.approvals import list_approvals
    from app.api.errors import MissingAuthenticationError

    request = MagicMock()
    request.state.user = None
    with patch("app.api.approvals.resolve_principal_id", return_value=None):
        with pytest.raises(MissingAuthenticationError):
            await list_approvals(request)


@pytest.mark.asyncio
async def test_approve_approval_missing_principal_raises_401() -> None:
    """POST approve with no principal → 401."""
    from app.api.approvals import approve_approval
    from app.api.errors import MissingAuthenticationError

    request = MagicMock()
    request.state.user = None
    with patch("app.api.approvals.resolve_principal_id", return_value=None):
        with pytest.raises(MissingAuthenticationError):
            await approve_approval(request, approval_id="x")


@pytest.mark.asyncio
async def test_reject_approval_missing_principal_raises_401() -> None:
    """POST reject with no principal → 401."""
    from app.api.approvals import reject_approval_endpoint
    from app.api.errors import MissingAuthenticationError

    request = MagicMock()
    request.state.user = None

    async def _json() -> dict:
        return {}

    request.json = _json
    with patch("app.api.approvals.resolve_principal_id", return_value=None):
        with pytest.raises(MissingAuthenticationError):
            await reject_approval_endpoint(request, approval_id="x")
